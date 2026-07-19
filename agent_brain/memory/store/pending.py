"""Durable write buffer for when the full write machinery is unreachable.

What it does:
    A *pending record* is one JSON line written under
    ``$BRAIN_DIR/pending/`` (default ``~/.agent-memory-hub/pending/``). When the
    Python write path can't run — no interpreter on PATH for the hook shim, a
    locked sqlite, an embedder that won't import — the writer drops the intended
    write here instead of losing it. ``PendingQueue.replay()`` later re-drives
    every buffered record through the one true ``WriteService`` funnel and
    deletes it on success, so the markdown pool eventually converges.

How to use it::

    from agent_brain.memory.store.pending import enqueue_write_record, PendingQueue

    enqueue_write_record({"op": "write", "item": {"title": ..., "summary": ...}})
    stats = PendingQueue().replay()   # -> ReplayStats(written, failed, dead)
    PendingQueue().depth()            # how many records are still buffered

Replay is safe to run repeatedly (idempotent at the queue level): a record that
writes successfully is unlinked; one that fails has its ``attempt`` counter
bumped and is parked under ``pending/dead/`` after ``MAX_ATTEMPTS`` so a single
poison record never blocks the rest of the queue forever.

Depends on: ``WriteService`` (the shared write funnel), ``MemoryItem`` + its
enums (record → item mapping), ``make_item_id`` (fresh id at replay time). The
``brain_dir`` / ``pending_dir`` / ``dirty_index_path`` helpers here are the
single source of truth for those locations and are reused by the watermark store
and the offline doctor.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Literal, TypedDict, cast

from agent_brain.contracts.memory_enums import MemoryType
from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.store.item_markdown import parse_item_markdown
from agent_brain.platform.secure_io import (
    close_descriptor,
    open_child_directory,
    open_directory_path_without_symlinks,
    open_regular_file_at,
    secure_dir_fd_io_supported,
)


def brain_dir() -> Path:
    """Resolve the on-disk brain root, honoring ``$BRAIN_DIR``.

    Mirrors ``WriteService._brain_dir`` so a single ``BRAIN_DIR`` controls every
    entry point (write funnel, pending queue, watermark, doctor).
    """
    return Path(os.environ.get("BRAIN_DIR", os.path.expanduser("~/.agent-memory-hub")))


def pending_dir() -> Path:
    """Directory holding buffered ``*.jsonl`` write records."""
    return brain_dir() / "pending"


def dirty_index_path() -> Path:
    """Append-only log of item ids whose md landed but whose index row is stale.

    ``WriteService`` appends here when the best-effort index upsert fails so a
    later reindex/``sync-pending`` can repair the derived index.
    """
    return brain_dir() / ".index-dirty"


# After this many failed replays a record is parked under pending/dead/ so one
# poison record (e.g. content that always trips the audit gate) cannot wedge the
# whole queue. It stays on disk for inspection rather than being deleted.
MAX_ATTEMPTS = 5
MAX_PENDING_RECORD_BYTES = 1024 * 1024
MAX_PENDING_QUEUE_ENTRIES = 20_000
MAX_ITEM_FRONTMATTER_BYTES = 64 * 1024
MAX_ITEM_METADATA_ENTRIES = 20_000
MAX_ITEM_METADATA_BYTES = 64 * 1024 * 1024
MAX_ITEM_DIRECTORY_DEPTH = 32
STALE_EPHEMERAL_SECONDS = 30 * 24 * 60 * 60

PendingClassification = Literal[
    "ready",
    "already_written",
    "stale_requires_review",
    "duplicate_candidate",
    "conflict",
    "unsupported_type",
    "malformed",
    "audit_blocked",
]

_SUPPORTED_MEMORY_TYPES = frozenset(member.value for member in MemoryType)
_RECORD_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_payload_sha256(item: dict[str, object]) -> str:
    payload = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _legacy_record_id(path: Path, record: dict[str, object]) -> str:
    seed = f"{path.name}\n{json.dumps(record, ensure_ascii=False, sort_keys=True)}"
    return "pending-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _pending_item_id(title: str, original_created_at: datetime, record_id: str) -> str:
    """Stable item identity shared by preview classification and replay."""

    slug = re.sub(r"[/\\]+", "-", "-".join(title.lower().split()))[:30].strip("-")
    stable = hashlib.sha256(record_id.encode("utf-8")).hexdigest()[:8]
    return f"mem-{original_created_at:%Y%m%d-%H%M%S}-{slug or 'pending'}-{stable}"


def enqueue_write_record(record: dict[str, object]) -> Path:
    """Append one write record to the pending queue and return its file path.

    The record is a plain dict shaped ``{"op": "write", "item": {...}}``; the
    ``item`` payload carries the fields needed to rebuild a ``MemoryItem`` at
    replay time (title/summary/body/type/tags/sensitivity/confidence/...). New
    records default to the v2 envelope with stable identity, enqueue/original
    time, and canonical payload hash. An explicit ``v=1`` keeps the legacy
    ``ts``/``attempt`` format unchanged for compatibility.
    """
    d = pending_dir()
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = d / f"{ts}-{uuid.uuid4().hex[:8]}.jsonl"
    if _record_version(record.get("v")) == 1:
        # Explicit v1 is the compatibility lane. Preview derives v2 metadata
        # without rewriting the legacy file.
        record.setdefault("v", 1)
        record.setdefault("ts", _utc_now().isoformat())
        record.setdefault("attempt", 0)
    else:
        now = _utc_now().isoformat()
        record.setdefault("v", 2)
        record.setdefault("op", "write")
        record.setdefault("origin", "unknown")
        record.setdefault("record_id", str(uuid.uuid4()))
        record.setdefault("enqueued_at", now)
        item = record.get("item")
        item_created_at = item.get("created_at") if isinstance(item, dict) else None
        record.setdefault("original_created_at", item_created_at or record["enqueued_at"])
        if isinstance(item, dict):
            record.setdefault("payload_sha256", _canonical_payload_sha256(item))
        record.setdefault("attempt", 0)
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


@dataclass
class ReplayStats:
    """Outcome of a replay sweep: records drained, retried, and parked as dead."""

    written: int = 0
    failed: int = 0
    dead: int = 0


@dataclass(frozen=True)
class PendingRecordPreview:
    """One pending record summarized without replaying it."""

    path: str
    record_id: str
    enqueued_at: str | None
    original_created_at: str | None
    age_seconds: int | None
    payload_sha256: str | None
    classification: PendingClassification
    reason: str
    op: str | None
    origin: str | None
    attempt: int
    title: str | None
    summary: str | None
    type: str | None
    project: str | None
    agent: str | None
    session: str | None
    sensitivity: str | None
    allow_unsafe: bool
    malformed: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "record_id": self.record_id,
            "enqueued_at": self.enqueued_at,
            "original_created_at": self.original_created_at,
            "age_seconds": self.age_seconds,
            "payload_sha256": self.payload_sha256,
            "classification": self.classification,
            "reason": self.reason,
            "op": self.op,
            "origin": self.origin,
            "attempt": self.attempt,
            "title": self.title,
            "summary": self.summary,
            "type": self.type,
            "project": self.project,
            "agent": self.agent,
            "session": self.session,
            "sensitivity": self.sensitivity,
            "allow_unsafe": self.allow_unsafe,
            "malformed": self.malformed,
            "error": self.error,
        }


@dataclass(frozen=True)
class PendingPreview:
    """Read-only pending queue preview."""

    total: int
    returned: int
    limit: int
    truncated: bool
    records: list[PendingRecordPreview]

    def to_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "returned": self.returned,
            "limit": self.limit,
            "truncated": self.truncated,
            "records": [record.to_dict() for record in self.records],
        }


class _PendingPreviewCommon(TypedDict):
    path: str
    record_id: str
    enqueued_at: str | None
    original_created_at: str | None
    age_seconds: int | None
    payload_sha256: str | None
    op: str | None
    origin: str | None
    attempt: int
    title: str | None
    summary: str | None
    type: str | None
    project: str | None
    agent: str | None
    session: str | None
    sensitivity: str | None
    allow_unsafe: bool


@dataclass(frozen=True)
class _ItemMetadataSnapshot:
    items: dict[str, MemoryItem]
    trusted: bool


class PendingQueue:
    """Durable buffer of pending writes, drained through ``WriteService``."""

    def depth(self) -> int:
        """Number of records still buffered (excludes the dead/ sub-dir)."""
        return len(_pending_record_paths())

    def preview(self, *, limit: int = 20) -> PendingPreview:
        """Summarize queued records without replaying or mutating them."""
        paths = _pending_record_paths()
        bounded_limit = max(0, limit)
        selected = paths[:bounded_limit]
        existing = _scan_existing_item_metadata(brain_dir() / "items")
        records = [
            self._preview_record(
                path,
                existing_items=existing.items,
                metadata_trusted=existing.trusted,
            )
            for path in selected
        ]
        return PendingPreview(
            total=len(paths),
            returned=len(records),
            limit=bounded_limit,
            truncated=len(paths) > bounded_limit,
            records=records,
        )

    def replay(self) -> ReplayStats:
        """Re-drive every buffered record through the write funnel.

        Records are processed oldest-first (filenames sort by timestamp). A
        record that writes successfully is unlinked; one that fails to build or
        write is bumped/parked via :meth:`_bump_or_kill`. Returns aggregate
        counts. Building the ``WriteService`` once amortizes the index/embedder
        setup across the whole sweep.
        """
        from agent_brain.contracts.memory_enums import MemoryType, Sensitivity
        from agent_brain.contracts.memory_item import MemoryItem, Refs, Source, Validity
        from agent_brain.memory.store.items_store import make_item_id
        from agent_brain.memory.store.write_service import WriteService

        stats = ReplayStats()
        d = pending_dir()
        if not d.exists():
            return stats
        svc = WriteService.for_brain()
        for path in sorted(d.glob("*.jsonl")):
            try:
                rec = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[0])
                f = rec["item"]
                now = datetime.now(timezone.utc).astimezone()
                item = MemoryItem(
                    id=make_item_id(f["title"], when=now),
                    type=MemoryType(f.get("type", "fact")),
                    created_at=now,
                    title=f["title"],
                    summary=f.get("summary", ""),
                    tags=f.get("tags", []),
                    confidence=f.get("confidence", 0.7),
                    sensitivity=Sensitivity(f.get("sensitivity", "internal")),
                    refs=Refs.model_validate(f.get("refs") or {}),
                    project=f.get("project") or None,
                    tenant_id=f.get("tenant_id") or None,
                    agent=f.get("agent") or None,
                    session=f.get("session") or None,
                    validity=Validity.model_validate(f.get("validity") or {}),
                    source=Source(kind="pending-replay"),
                )
                res = svc.write(item=item, body=f.get("body", ""),
                                allow_unsafe=f.get("allow_unsafe", False))
                if res.status == "written":
                    path.unlink()
                    stats.written += 1
                else:
                    # Audit-blocked: not a transient failure, so bump/park it
                    # rather than retrying identically forever.
                    self._bump_or_kill(path, stats)
            except Exception:
                # Malformed record or a genuine write failure (locked store,
                # disk full). Markdown is the source of truth, so we never crash
                # the sweep on one bad record — bump it and move on.
                self._bump_or_kill(path, stats)
        return stats

    def _bump_or_kill(self, path: Path, stats: ReplayStats) -> None:
        """Increment a record's attempt count, parking it under dead/ at the cap.

        A record whose own bytes can no longer be parsed is treated as already
        at the cap so it is parked immediately rather than retried forever.
        """
        try:
            rec = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[0])
        except Exception:
            rec = {"attempt": MAX_ATTEMPTS}
        rec["attempt"] = rec.get("attempt", 0) + 1
        if rec["attempt"] >= MAX_ATTEMPTS:
            dead = pending_dir() / "dead"
            dead.mkdir(parents=True, exist_ok=True)
            path.rename(dead / path.name)
            stats.dead += 1
        else:
            path.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
            stats.failed += 1

    def _preview_record(
        self,
        path: Path,
        *,
        existing_items: dict[str, MemoryItem],
        metadata_trusted: bool,
    ) -> PendingRecordPreview:
        raw: bytes | None = None
        try:
            raw = _read_pending_record(path)
            line = raw.decode("utf-8").strip().splitlines()[0]
            rec = json.loads(line, parse_constant=_reject_json_constant)
            if not isinstance(rec, dict):
                return _malformed_preview(path, "PENDING_RECORD_NOT_OBJECT", raw=raw)
            item = rec.get("item")
            if not isinstance(item, dict):
                return _malformed_preview(path, "INVALID_ITEM_PAYLOAD", raw=raw)
            return _classify_pending_record(
                path=path,
                record=rec,
                item=item,
                existing_items=existing_items,
                metadata_trusted=metadata_trusted,
            )
        except json.JSONDecodeError:
            return _malformed_preview(path, "MALFORMED_JSON", raw=raw)
        except UnicodeError:
            return _malformed_preview(path, "INVALID_RECORD_ENCODING", raw=raw)
        except IndexError:
            return _malformed_preview(path, "EMPTY_PENDING_RECORD", raw=raw)
        except _PendingReadError as exc:
            return _malformed_preview(path, exc.reason, raw=raw)
        except Exception:
            # One unexpected record never breaks the queue. Do not reflect raw
            # exception text because it can contain sensitive payload details.
            return _malformed_preview(path, "PENDING_RECORD_READ_FAILED", raw=raw)


def _classify_pending_record(
    *,
    path: Path,
    record: dict[str, object],
    item: dict[str, object],
    existing_items: dict[str, MemoryItem],
    metadata_trusted: bool,
) -> PendingRecordPreview:
    version = _record_version(record.get("v"))
    if version is None:
        return _malformed_preview(path, "UNSUPPORTED_PENDING_VERSION", record=record)
    if record.get("op") != "write":
        return _malformed_preview(path, "UNSUPPORTED_PENDING_OPERATION", record=record)

    payload_sha256 = _canonical_payload_sha256(item)
    if version == 2:
        record_id_value = record.get("record_id")
        if not isinstance(record_id_value, str) or not _RECORD_ID_PATTERN.fullmatch(
            record_id_value
        ):
            return _malformed_preview(
                path,
                "INVALID_RECORD_ID",
                record=record,
                payload_sha256=payload_sha256,
            )
        record_id = record_id_value
        declared_hash = record.get("payload_sha256")
        if not isinstance(declared_hash, str):
            return _malformed_preview(
                path,
                "MISSING_PAYLOAD_SHA256",
                record=record,
                record_id=record_id,
                payload_sha256=payload_sha256,
            )
    else:
        record_id = _legacy_record_id(path, record)
        declared_hash = payload_sha256

    enqueued_raw = _enqueued_at_value(path, record, version=version)
    enqueued_at, enqueued_reason = _parse_pending_time(enqueued_raw, field="ENQUEUED_AT")
    if enqueued_reason:
        return _malformed_preview(
            path,
            enqueued_reason,
            record=record,
            record_id=record_id,
            payload_sha256=payload_sha256,
        )
    assert enqueued_at is not None

    original_raw = record.get("original_created_at")
    if original_raw is None:
        original_raw = item.get("created_at")
    if original_raw is None:
        original_raw = enqueued_at.isoformat()
    original_created_at, original_reason = _parse_pending_time(
        original_raw, field="ORIGINAL_CREATED_AT"
    )
    if original_reason:
        return _malformed_preview(
            path,
            original_reason,
            record=record,
            record_id=record_id,
            payload_sha256=payload_sha256,
            enqueued_at=enqueued_at,
        )
    assert original_created_at is not None

    now = _utc_now()
    for candidate, field in (
        (enqueued_at, "ENQUEUED_AT"),
        (original_created_at, "ORIGINAL_CREATED_AT"),
    ):
        if candidate > now:
            return _malformed_preview(
                path,
                f"FUTURE_{field}",
                record=record,
                record_id=record_id,
                payload_sha256=payload_sha256,
                enqueued_at=enqueued_at,
                original_created_at=original_created_at,
            )

    age_seconds = int((now - original_created_at).total_seconds())
    common = _preview_common(
        path=path,
        record=record,
        item=item,
        record_id=record_id,
        enqueued_at=enqueued_at,
        original_created_at=original_created_at,
        age_seconds=age_seconds,
        payload_sha256=payload_sha256,
    )

    if version == 2 and declared_hash.lower() != payload_sha256:
        return PendingRecordPreview(
            **common,
            classification="conflict",
            reason="PAYLOAD_HASH_MISMATCH",
        )

    type_value = item.get("type", "fact")
    if not isinstance(type_value, str) or type_value not in _SUPPORTED_MEMORY_TYPES:
        return PendingRecordPreview(
            **common,
            classification="unsupported_type",
            reason="UNSUPPORTED_MEMORY_TYPE",
        )

    title = item.get("title")
    if not isinstance(title, str) or not title.strip():
        return PendingRecordPreview(
            **common,
            classification="malformed",
            reason="INVALID_ITEM_TITLE",
            malformed=True,
            error="INVALID_ITEM_TITLE",
        )
    if not isinstance(item.get("body", ""), str):
        return PendingRecordPreview(
            **common,
            classification="malformed",
            reason="INVALID_ITEM_BODY",
            malformed=True,
            error="INVALID_ITEM_BODY",
        )

    if not metadata_trusted:
        return PendingRecordPreview(
            **common,
            classification="audit_blocked",
            reason="EXISTING_ITEM_SCAN_UNAVAILABLE",
        )

    stable_item_id = _pending_item_id(title, original_created_at, record_id)
    stable_existing = existing_items.get(stable_item_id)
    if stable_existing is not None:
        if stable_existing.source.span_hash == payload_sha256:
            return PendingRecordPreview(
                **common,
                classification="already_written",
                reason="STABLE_ITEM_ALREADY_WRITTEN",
            )
        return PendingRecordPreview(
            **common,
            classification="conflict",
            reason="STABLE_ITEM_PAYLOAD_CONFLICT",
        )

    duplicate_reason = _same_scope_duplicate_reason(
        item=item,
        payload_sha256=payload_sha256,
        existing_items=existing_items.values(),
    )
    if duplicate_reason:
        return PendingRecordPreview(
            **common,
            classification="duplicate_candidate",
            reason=duplicate_reason,
        )

    audit_reason = _audit_reason(item)
    if audit_reason:
        return PendingRecordPreview(
            **common,
            classification="audit_blocked",
            reason=audit_reason,
        )

    if type_value in {"signal", "handoff"} and age_seconds >= STALE_EPHEMERAL_SECONDS:
        return PendingRecordPreview(
            **common,
            classification="stale_requires_review",
            reason="STALE_EPHEMERAL_MEMORY",
        )

    return PendingRecordPreview(**common, classification="ready", reason="READY")


def _preview_common(
    *,
    path: Path,
    record: dict[str, object],
    item: dict[str, object],
    record_id: str,
    enqueued_at: datetime,
    original_created_at: datetime,
    age_seconds: int,
    payload_sha256: str,
) -> _PendingPreviewCommon:
    sensitivity = _optional_str(item.get("sensitivity"))
    redact = sensitivity in {"private", "secret"}
    return {
        "path": str(path),
        "record_id": record_id,
        "enqueued_at": enqueued_at.isoformat(),
        "original_created_at": original_created_at.isoformat(),
        "age_seconds": age_seconds,
        "payload_sha256": payload_sha256,
        "op": _optional_str(record.get("op")),
        "origin": _optional_str(record.get("origin")),
        "attempt": _safe_attempt(record.get("attempt")),
        "title": None if redact else _optional_str(item.get("title")),
        "summary": None if redact else _optional_str(item.get("summary")),
        "type": _optional_str(item.get("type", "fact")),
        "project": None if redact else _optional_str(item.get("project")),
        "agent": None if redact else _optional_str(item.get("agent")),
        "session": None if redact else _optional_str(item.get("session")),
        "sensitivity": sensitivity,
        "allow_unsafe": bool(item.get("allow_unsafe")),
    }


def _malformed_preview(
    path: Path,
    reason: str,
    *,
    raw: bytes | None = None,
    record: dict[str, object] | None = None,
    record_id: str | None = None,
    payload_sha256: str | None = None,
    enqueued_at: datetime | None = None,
    original_created_at: datetime | None = None,
) -> PendingRecordPreview:
    item_value = record.get("item") if isinstance(record, dict) else None
    item = item_value if isinstance(item_value, dict) else {}
    sensitivity = _optional_str(item.get("sensitivity"))
    redact = sensitivity in {"private", "secret"}
    return PendingRecordPreview(
        path=str(path),
        record_id=record_id or _malformed_record_id(path, raw),
        enqueued_at=enqueued_at.isoformat() if enqueued_at else None,
        original_created_at=(
            original_created_at.isoformat() if original_created_at else None
        ),
        age_seconds=None,
        payload_sha256=payload_sha256,
        classification="malformed",
        reason=reason,
        op=_optional_str(record.get("op")) if isinstance(record, dict) else None,
        origin=_optional_str(record.get("origin")) if isinstance(record, dict) else None,
        attempt=_safe_attempt(record.get("attempt")) if isinstance(record, dict) else 0,
        title=None if redact else _optional_str(item.get("title")),
        summary=None if redact else _optional_str(item.get("summary")),
        type=_optional_str(item.get("type")),
        project=None if redact else _optional_str(item.get("project")),
        agent=None if redact else _optional_str(item.get("agent")),
        session=None if redact else _optional_str(item.get("session")),
        sensitivity=sensitivity,
        allow_unsafe=bool(item.get("allow_unsafe")),
        malformed=True,
        error=reason,
    )


def _same_scope_duplicate_reason(
    *,
    item: dict[str, object],
    payload_sha256: str,
    existing_items: Iterable[MemoryItem],
) -> str | None:
    project = _optional_str(item.get("project"))
    tenant = _optional_str(item.get("tenant_id"))
    type_value = _optional_str(item.get("type", "fact"))
    title = (_optional_str(item.get("title")) or "").strip().lower()
    summary = (_optional_str(item.get("summary")) or "").strip().lower()
    for existing in existing_items:
        if existing.project != project or existing.tenant_id != tenant:
            continue
        if existing.source.span_hash == payload_sha256:
            return "SAME_SCOPE_PAYLOAD_DUPLICATE"
        if (
            str(existing.type) == type_value
            and existing.title.strip().lower() == title
            and existing.summary.strip().lower() == summary
        ):
            return "SAME_SCOPE_METADATA_DUPLICATE"
    return None


def _audit_reason(item: dict[str, object]) -> str | None:
    try:
        from agent_brain.memory.governance.audit.scanner import audit_memory_text

        report = audit_memory_text(
            "\n".join(
                (
                    _optional_str(item.get("title")) or "",
                    _optional_str(item.get("summary")) or "",
                    _optional_str(item.get("body")) or "",
                )
            )
        )
    except Exception:
        return "AUDIT_SCAN_FAILED"
    return None if report.passed else "AUDIT_BLOCKED"


def _record_version(value: object) -> int | None:
    if (type(value) is int and value == 1) or value == "1":
        return 1
    if (type(value) is int and value == 2) or value == "2":
        return 2
    return None


def _enqueued_at_value(path: Path, record: dict[str, object], *, version: int) -> object:
    if version == 2:
        return record.get("enqueued_at")
    value = record.get("enqueued_at") or record.get("ts")
    if value is not None:
        return value
    match = re.match(r"^(\d{8}T\d{6}Z)-", path.name)
    if match:
        try:
            return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pass
    return None


def _parse_pending_time(value: object, *, field: str) -> tuple[datetime | None, str | None]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None, f"INVALID_{field}"
    elif value is None:
        return None, f"MISSING_{field}"
    else:
        return None, f"INVALID_{field}"
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None, f"NAIVE_{field}"
    return parsed, None


def _safe_attempt(value: object) -> int:
    if not isinstance(value, (str, bytes, bytearray, int, float, bool)):
        return 0
    try:
        attempt = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, attempt)


def _malformed_record_id(path: Path, raw: bytes | None) -> str:
    digest = hashlib.sha256(path.name.encode("utf-8") + b"\n" + (raw or b"")).hexdigest()
    return "pending-malformed-" + digest[:16]


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


class _PendingReadError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _pending_record_paths() -> list[Path]:
    directory = pending_dir()
    if secure_dir_fd_io_supported():
        descriptor: int | None = None
        try:
            descriptor = open_directory_path_without_symlinks(directory)
            entries = sorted(os.scandir(descriptor), key=lambda entry: entry.name)
            paths: list[Path] = []
            for entry in entries:
                if len(paths) >= MAX_PENDING_QUEUE_ENTRIES:
                    break
                try:
                    if (
                        entry.name.endswith(".jsonl")
                        and not entry.is_symlink()
                        and entry.is_file(follow_symlinks=False)
                    ):
                        paths.append(directory / entry.name)
                except OSError:
                    continue
            return paths
        except OSError:
            return []
        finally:
            if descriptor is not None:
                close_descriptor(descriptor)

    try:
        entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
    except OSError:
        return []
    paths = []
    for entry in entries:
        if len(paths) >= MAX_PENDING_QUEUE_ENTRIES:
            break
        try:
            opened = entry.stat(follow_symlinks=False)
            if entry.name.endswith(".jsonl") and stat.S_ISREG(opened.st_mode):
                paths.append(directory / entry.name)
        except OSError:
            continue
    return paths


def _read_pending_record(path: Path) -> bytes:
    if secure_dir_fd_io_supported():
        directory_descriptor: int | None = None
        descriptor: int | None = None
        try:
            directory_descriptor = open_directory_path_without_symlinks(path.parent)
            descriptor = open_regular_file_at(directory_descriptor, path.name)
            return _read_bounded_descriptor(descriptor, MAX_PENDING_RECORD_BYTES)
        except OSError as exc:
            raise _PendingReadError("PENDING_RECORD_READ_FAILED") from exc
        finally:
            if descriptor is not None:
                close_descriptor(descriptor)
            if directory_descriptor is not None:
                close_descriptor(directory_descriptor)

    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode):
            raise _PendingReadError("PENDING_RECORD_NOT_REGULAR")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise _PendingReadError("PENDING_RECORD_CHANGED")
            return _read_bounded_descriptor(descriptor, MAX_PENDING_RECORD_BYTES)
        finally:
            close_descriptor(descriptor)
    except _PendingReadError:
        raise
    except OSError as exc:
        raise _PendingReadError("PENDING_RECORD_READ_FAILED") from exc


def _read_bounded_descriptor(descriptor: int, limit: int) -> bytes:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise _PendingReadError("PENDING_RECORD_NOT_REGULAR")
    if before.st_size > limit:
        raise _PendingReadError("PENDING_RECORD_TOO_LARGE")
    chunks: list[bytes] = []
    remaining = limit + 1
    while remaining > 0:
        chunk = os.read(descriptor, min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    after = os.fstat(descriptor)
    if len(data) > limit:
        raise _PendingReadError("PENDING_RECORD_TOO_LARGE")
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or len(data) != after.st_size
    ):
        raise _PendingReadError("PENDING_RECORD_CHANGED")
    return data


def _scan_existing_item_metadata(items_dir: Path) -> _ItemMetadataSnapshot:
    """Read bounded item frontmatter only; never load Markdown bodies."""

    if not secure_dir_fd_io_supported():
        return _ItemMetadataSnapshot(items={}, trusted=False)
    root: int | None = None
    stack: list[tuple[int, Iterator[os.DirEntry[str]], int]] = []
    items: dict[str, MemoryItem] = {}
    entry_count = 0
    bytes_read = 0
    try:
        root = open_directory_path_without_symlinks(items_dir)
        stack.append((root, iter(sorted(os.scandir(root), key=lambda row: row.name)), 0))
        root = None
    except FileNotFoundError:
        return _ItemMetadataSnapshot(items={}, trusted=True)
    except OSError:
        return _ItemMetadataSnapshot(items={}, trusted=False)
    try:
        while stack:
            directory, entries, depth = stack[-1]
            try:
                entry = next(entries)
            except StopIteration:
                close_descriptor(directory)
                stack.pop()
                continue
            except OSError:
                return _ItemMetadataSnapshot(items={}, trusted=False)
            entry_count += 1
            if entry_count > MAX_ITEM_METADATA_ENTRIES:
                return _ItemMetadataSnapshot(items={}, trusted=False)
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if depth >= MAX_ITEM_DIRECTORY_DEPTH:
                        return _ItemMetadataSnapshot(items={}, trusted=False)
                    child = open_child_directory(directory, entry.name)
                    stack.append(
                        (child, iter(sorted(os.scandir(child), key=lambda row: row.name)), depth + 1)
                    )
                    continue
                if not entry.is_file(follow_symlinks=False) or not entry.name.endswith(".md"):
                    continue
                item, consumed = _read_item_frontmatter(directory, entry.name)
                bytes_read += consumed
                if bytes_read > MAX_ITEM_METADATA_BYTES:
                    return _ItemMetadataSnapshot(items={}, trusted=False)
                if item is None:
                    return _ItemMetadataSnapshot(items={}, trusted=False)
                if Path(entry.name).stem != item.id:
                    return _ItemMetadataSnapshot(items={}, trusted=False)
                if item.id in items:
                    return _ItemMetadataSnapshot(items={}, trusted=False)
                items[item.id] = item
            except OSError:
                return _ItemMetadataSnapshot(items={}, trusted=False)
        return _ItemMetadataSnapshot(items=items, trusted=True)
    finally:
        if root is not None:
            close_descriptor(root)
        while stack:
            directory, _entries, _depth = stack.pop()
            close_descriptor(directory)


def _read_item_frontmatter(
    directory_descriptor: int, filename: str
) -> tuple[MemoryItem | None, int]:
    consumed = 0
    lines: list[bytes] = []
    try:
        with _open_regular_binary(directory_descriptor, filename) as handle:
            opening = handle.readline(MAX_ITEM_FRONTMATTER_BYTES + 1)
            consumed += len(opening)
            normalized_opening = opening.rstrip(b"\r\n")
            if normalized_opening.startswith(b"\xef\xbb\xbf"):
                normalized_opening = normalized_opening[3:]
            if normalized_opening != b"---":
                return None, consumed
            while consumed <= MAX_ITEM_FRONTMATTER_BYTES:
                line = handle.readline(MAX_ITEM_FRONTMATTER_BYTES - consumed + 1)
                consumed += len(line)
                if consumed > MAX_ITEM_FRONTMATTER_BYTES or not line:
                    return None, consumed
                if line.rstrip(b"\r\n") == b"---":
                    frontmatter = b"---\n" + b"".join(lines) + b"---\n"
                    item, _body = parse_item_markdown(frontmatter.decode("utf-8"))
                    return item, consumed
                lines.append(line)
    except (OSError, UnicodeError, ValueError, TypeError, OverflowError):
        return None, consumed
    return None, consumed


@contextmanager
def _open_regular_binary(
    directory_descriptor: int, filename: str
) -> Iterator[BinaryIO]:
    descriptor: int | None = open_regular_file_at(directory_descriptor, filename)
    try:
        assert descriptor is not None
        handle = cast(BinaryIO, os.fdopen(descriptor, "rb", buffering=0))
        descriptor = None
        with handle:
            yield handle
    finally:
        if descriptor is not None:
            close_descriptor(descriptor)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
