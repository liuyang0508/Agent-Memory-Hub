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
import heapq
import json
import logging
import os
import re
import secrets
import stat
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field as dataclass_field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Literal, TypedDict, cast

import yaml
from pydantic import ValidationError

from agent_brain.contracts.memory_enums import MemoryType
from agent_brain.contracts.memory_item import MemoryItem, Refs, Source, Validity
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
    configured = Path(
        os.environ.get("BRAIN_DIR", os.path.expanduser("~/.agent-memory-hub"))
    ).expanduser()
    return configured.resolve(strict=False)


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
_PENDING_ITEM_FIELDS = frozenset(
    {
        "type",
        "created_at",
        "title",
        "summary",
        "body",
        "tags",
        "confidence",
        "sensitivity",
        "refs",
        "project",
        "tenant_id",
        "agent",
        "session",
        "validity",
        "source",
        "allow_unsafe",
    }
)
_LEGACY_BOOKKEEPING_FIELDS = frozenset(
    {
        "attempt",
        "attempts",
        "dead_lettered_at",
        "error",
        "last_error",
        "last_error_code",
        "last_attempt_at",
        "next_attempt_at",
        "next_retry_at",
        "retry",
        "retries",
        "retry_at",
        "retry_count",
        "status",
    }
)


class PendingEnqueueError(OSError):
    """Stable fail-closed error raised before a pending record is published."""


_log = logging.getLogger(__name__)
_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)


def _open_or_create_secure_directory(path: Path) -> int:
    """Open a canonical POSIX directory, durably creating missing components."""

    absolute = Path(os.path.abspath(os.fspath(path)))
    parts = absolute.parts
    if not parts or parts[0] != os.sep:
        raise PendingEnqueueError("INVALID_PENDING_DIRECTORY_PATH")
    descriptor: int | None = os.open(os.sep, _DIRECTORY_OPEN_FLAGS)
    try:
        for component in parts[1:]:
            assert descriptor is not None
            if component in {"", ".", ".."}:
                raise PendingEnqueueError("INVALID_PENDING_DIRECTORY_PATH")
            try:
                child = open_child_directory(descriptor, component)
            except FileNotFoundError:
                created = False
                try:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                    created = True
                except FileExistsError:
                    # A concurrent trusted creator won the race. The no-follow
                    # open below still verifies that the winner is a directory.
                    pass
                if created:
                    # The new name must be durable in its parent before creating
                    # descendants beneath it.
                    os.fsync(descriptor)
                child = open_child_directory(descriptor, component)
                if created:
                    try:
                        os.fchmod(child, 0o700)
                        os.fsync(child)
                    except BaseException:
                        close_descriptor(child)
                        raise
            close_descriptor(descriptor)
            descriptor = child
        assert descriptor is not None
        opened = descriptor
        descriptor = None
        return opened
    finally:
        if descriptor is not None:
            close_descriptor(descriptor)


@contextmanager
def _open_pending_write_directory(brain: Path) -> Iterator[int]:
    root_descriptor: int | None = None
    pending_descriptor: int | None = None
    try:
        root_descriptor = _open_or_create_secure_directory(brain)
        try:
            os.mkdir("pending", 0o700, dir_fd=root_descriptor)
            os.fsync(root_descriptor)
        except FileExistsError:
            pass
        pending_descriptor = open_child_directory(root_descriptor, "pending")
        os.fchmod(pending_descriptor, 0o700)
        os.fsync(pending_descriptor)
        yield pending_descriptor
    finally:
        if pending_descriptor is not None:
            close_descriptor(pending_descriptor)
        if root_descriptor is not None:
            close_descriptor(root_descriptor)


def _publish_pending_record(brain: Path, filename: str, data: bytes) -> Path:
    if secure_dir_fd_io_supported():
        return _publish_pending_record_secure(brain, filename, data)
    return _publish_pending_record_fallback(brain, filename, data)


def _publish_pending_record_secure(brain: Path, filename: str, data: bytes) -> Path:
    temp_name = f".amh-pending-{secrets.token_hex(16)}.tmp"
    temp_descriptor: int | None = None
    temp_created = False
    committed = False
    with _open_pending_write_directory(brain) as directory_descriptor:
        try:
            temp_descriptor = os.open(
                temp_name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=directory_descriptor,
            )
            temp_created = True
            os.fchmod(temp_descriptor, 0o600)
            remaining = memoryview(data)
            while remaining:
                written = os.write(temp_descriptor, remaining)
                if written <= 0:
                    raise OSError("PENDING_RECORD_WRITE_FAILED")
                remaining = remaining[written:]
            os.fsync(temp_descriptor)
            close_descriptor(temp_descriptor)
            temp_descriptor = None
            try:
                os.link(
                    temp_name,
                    filename,
                    src_dir_fd=directory_descriptor,
                    dst_dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                if _read_existing_pending_bytes(directory_descriptor, filename) != data:
                    raise PendingEnqueueError("PENDING_RECORD_FILENAME_CONFLICT") from exc
            os.fsync(directory_descriptor)
            committed = True
        finally:
            if temp_descriptor is not None:
                close_descriptor(temp_descriptor)
            if temp_created:
                try:
                    os.unlink(temp_name, dir_fd=directory_descriptor)
                    os.fsync(directory_descriptor)
                except FileNotFoundError:
                    pass
                except OSError:
                    if not committed:
                        raise
                    # The target is already published and directory-synced. A
                    # housekeeping failure must not turn success into a retry.
                    _log.warning("PENDING_TEMP_CLEANUP_FAILED")
    return brain / "pending" / filename


def _ensure_fallback_directory(path: Path, *, mode: int = 0o700) -> None:
    """Create/check one canonical absolute directory chain without symlinks."""

    absolute = Path(os.path.abspath(os.fspath(path)))
    if not absolute.is_absolute():
        raise PendingEnqueueError("INVALID_PENDING_DIRECTORY_PATH")
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        if component in {"", ".", ".."}:
            raise PendingEnqueueError("INVALID_PENDING_DIRECTORY_PATH")
        current /= component
        created = False
        try:
            opened = os.lstat(current)
        except FileNotFoundError:
            try:
                os.mkdir(current, mode)
                created = True
            except FileExistsError:
                pass
            opened = os.lstat(current)
        if not stat.S_ISDIR(opened.st_mode):
            raise PendingEnqueueError("UNSAFE_PENDING_DIRECTORY")
        if created:
            try:
                os.chmod(current, mode, follow_symlinks=False)
            except (NotImplementedError, OSError):
                if os.name != "nt":
                    raise


def _fsync_fallback_directory(path: Path) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, _DIRECTORY_OPEN_FLAGS)
        os.fsync(descriptor)
    except OSError:
        if os.name != "nt":
            raise
        # Windows does not expose POSIX directory fsync consistently. The
        # file itself is flushed and no-replace publication is still enforced.
        _log.warning("PENDING_DIRECTORY_FSYNC_UNAVAILABLE")
    finally:
        if descriptor is not None:
            close_descriptor(descriptor)


def _fallback_fchmod_private(descriptor: int) -> None:
    try:
        os.fchmod(descriptor, 0o600)
    except (AttributeError, OSError):
        if os.name != "nt":
            raise


def _fallback_link_no_replace(source: Path, target: Path) -> None:
    try:
        os.link(source, target, follow_symlinks=False)
    except (TypeError, NotImplementedError):
        if os.name != "nt":
            raise
        # The source is an exclusively-created regular file, so Windows'
        # hard-link operation remains no-replace without following user input.
        os.link(source, target)


def _read_existing_pending_path_bytes(path: Path) -> bytes:
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode):
            raise PendingEnqueueError("UNSAFE_EXISTING_PENDING_RECORD")
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
            if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(before, opened):
                raise PendingEnqueueError("UNSAFE_EXISTING_PENDING_RECORD")
            if os.name == "posix" and stat.S_IMODE(opened.st_mode) != 0o600:
                raise PendingEnqueueError("UNSAFE_EXISTING_PENDING_RECORD")
            return _read_bounded_descriptor(descriptor, MAX_PENDING_RECORD_BYTES)
        finally:
            close_descriptor(descriptor)
    except PendingEnqueueError:
        raise
    except (OSError, _PendingReadError) as exc:
        raise PendingEnqueueError("UNSAFE_EXISTING_PENDING_RECORD") from exc


def _publish_pending_record_fallback(brain: Path, filename: str, data: bytes) -> Path:
    _ensure_fallback_directory(brain)
    directory = brain / "pending"
    _ensure_fallback_directory(directory)
    try:
        os.chmod(directory, 0o700, follow_symlinks=False)
    except (NotImplementedError, OSError):
        if os.name != "nt":
            raise
    directory_before = os.lstat(directory)
    temp_path = directory / f".amh-pending-{secrets.token_hex(16)}.tmp"
    target_path = directory / filename
    temp_descriptor: int | None = None
    temp_created = False
    committed = False
    try:
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        temp_descriptor = os.open(temp_path, flags, 0o600)
        temp_created = True
        _fallback_fchmod_private(temp_descriptor)
        temp_identity = os.fstat(temp_descriptor)
        if not stat.S_ISREG(temp_identity.st_mode):
            raise PendingEnqueueError("UNSAFE_PENDING_TEMP_RECORD")
        remaining = memoryview(data)
        while remaining:
            written = os.write(temp_descriptor, remaining)
            if written <= 0:
                raise OSError("PENDING_RECORD_WRITE_FAILED")
            remaining = remaining[written:]
        os.fsync(temp_descriptor)
        try:
            _fallback_link_no_replace(temp_path, target_path)
        except FileExistsError as exc:
            if _read_existing_pending_path_bytes(target_path) != data:
                raise PendingEnqueueError("PENDING_RECORD_FILENAME_CONFLICT") from exc
        else:
            target_identity = os.lstat(target_path)
            if not stat.S_ISREG(target_identity.st_mode) or not os.path.samestat(
                temp_identity, target_identity
            ):
                raise PendingEnqueueError("PENDING_RECORD_PUBLISH_IDENTITY_MISMATCH")
        directory_after = os.lstat(directory)
        if not os.path.samestat(directory_before, directory_after):
            raise PendingEnqueueError("PENDING_DIRECTORY_CHANGED")
        _fsync_fallback_directory(directory)
        committed = True
    finally:
        if temp_descriptor is not None:
            close_descriptor(temp_descriptor)
        if temp_created:
            try:
                os.unlink(temp_path)
                _fsync_fallback_directory(directory)
            except FileNotFoundError:
                pass
            except OSError:
                if not committed:
                    raise
                _log.warning("PENDING_TEMP_CLEANUP_FAILED")
    return target_path


def _read_existing_pending_bytes(directory_descriptor: int, filename: str) -> bytes:
    descriptor: int | None = None
    try:
        descriptor = open_regular_file_at(directory_descriptor, filename)
        opened = os.fstat(descriptor)
        if stat.S_IMODE(opened.st_mode) != 0o600:
            raise PendingEnqueueError("UNSAFE_EXISTING_PENDING_RECORD")
        return _read_bounded_descriptor(descriptor, MAX_PENDING_RECORD_BYTES)
    except PendingEnqueueError:
        raise
    except (OSError, _PendingReadError) as exc:
        raise PendingEnqueueError("UNSAFE_EXISTING_PENDING_RECORD") from exc
    finally:
        if descriptor is not None:
            close_descriptor(descriptor)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_payload_sha256(item: dict[str, object]) -> str:
    payload = json.dumps(
        item,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _legacy_record_id(path: Path, record: dict[str, object]) -> str:
    semantic_record = {
        key: value for key, value in record.items() if key not in _LEGACY_BOOKKEEPING_FIELDS
    }
    seed = f"{path.name}\n" + json.dumps(
        semantic_record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "pending-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _pending_item_id(title: str, original_created_at: datetime, record_id: str) -> str:
    """Stable item identity shared by preview classification and replay."""

    utc_created_at = original_created_at.astimezone(timezone.utc)
    slug = re.sub(r"[/\\]+", "-", "-".join(title.lower().split()))[:30].strip("-")
    stable = hashlib.sha256(record_id.encode("utf-8")).hexdigest()[:24]
    return f"mem-{utc_created_at:%Y%m%d-%H%M%S}-{slug or 'pending'}-{stable}"


def enqueue_write_record(record: dict[str, object]) -> Path:
    """Append one write record to the pending queue and return its file path.

    The record is a plain dict shaped ``{"op": "write", "item": {...}}``; the
    ``item`` payload carries the fields needed to rebuild a ``MemoryItem`` at
    replay time (title/summary/body/type/tags/sensitivity/confidence/...). New
    records default to the v2 envelope with stable identity, enqueue/original
    time, and canonical payload hash. An explicit ``v=1`` keeps the legacy
    ``ts``/``attempt`` format unchanged for compatibility.
    """
    brain = brain_dir()
    now = _utc_now()
    ts = now.strftime("%Y%m%dT%H%M%SZ")
    filename = f"{ts}-{uuid.uuid4().hex[:8]}.jsonl"
    if _record_version(record.get("v")) == 1:
        # Explicit v1 is the compatibility lane. Preview derives v2 metadata
        # without rewriting the legacy file.
        record.setdefault("v", 1)
        record.setdefault("ts", now.isoformat())
        record.setdefault("attempt", 0)
    else:
        record.setdefault("v", 2)
        record.setdefault("op", "write")
        record.setdefault("origin", "unknown")
        record.setdefault("record_id", str(uuid.uuid4()))
        record.setdefault("enqueued_at", now.isoformat())
        item = record.get("item")
        item_created_at = item.get("created_at") if isinstance(item, dict) else None
        record.setdefault("original_created_at", item_created_at or record["enqueued_at"])
        if isinstance(item, dict):
            try:
                record.setdefault("payload_sha256", _canonical_payload_sha256(item))
            except (ValueError, OverflowError) as exc:
                raise PendingEnqueueError("NON_FINITE_PENDING_PAYLOAD") from exc
            except TypeError as exc:
                raise PendingEnqueueError("INVALID_PENDING_PAYLOAD") from exc
        record.setdefault("attempt", 0)
    try:
        data = (json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    except (ValueError, OverflowError) as exc:
        raise PendingEnqueueError("NON_FINITE_PENDING_PAYLOAD") from exc
    except TypeError as exc:
        raise PendingEnqueueError("INVALID_PENDING_PAYLOAD") from exc
    if len(data) > MAX_PENDING_RECORD_BYTES:
        raise PendingEnqueueError("PENDING_RECORD_TOO_LARGE")
    return _publish_pending_record(brain, filename, data)


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
    _stable_item_id: str | None = dataclass_field(default=None, repr=False, compare=False)

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
    scan_unavailable: bool = False
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "returned": self.returned,
            "limit": self.limit,
            "truncated": self.truncated,
            "records": [record.to_dict() for record in self.records],
            "scan_unavailable": self.scan_unavailable,
            "reason": self.reason,
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
    _stable_item_id: str | None


@dataclass(frozen=True)
class _ItemMetadataSnapshot:
    items: dict[str, MemoryItem]
    trusted: bool


@dataclass(frozen=True)
class _PendingPathSnapshot:
    paths: list[Path]
    total: int
    scan_unavailable: bool = False
    reason: str | None = None


class _DescendingName(str):
    """Reverse string ordering so heap root is the largest retained filename."""

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, str):
            return NotImplemented
        return str.__gt__(self, other)


class PendingQueue:
    """Durable buffer of pending writes, drained through ``WriteService``."""

    def depth(self) -> int:
        """Number of records still buffered (excludes the dead/ sub-dir)."""
        brain = brain_dir()
        snapshot = _pending_record_paths(brain / "pending")
        if snapshot.scan_unavailable:
            raise PendingEnqueueError(snapshot.reason or "PENDING_SCAN_UNAVAILABLE")
        return snapshot.total

    def preview(self, *, limit: int = 20) -> PendingPreview:
        """Summarize queued records without replaying or mutating them."""
        brain = brain_dir()
        path_snapshot = _pending_record_paths(brain / "pending")
        bounded_limit = max(0, limit)
        scan_cap = max(0, MAX_PENDING_QUEUE_ENTRIES)
        classification_paths = path_snapshot.paths[:scan_cap]
        if bounded_limit == 0 or not classification_paths:
            return PendingPreview(
                total=path_snapshot.total,
                returned=0,
                limit=bounded_limit,
                truncated=path_snapshot.total > 0,
                records=[],
                scan_unavailable=path_snapshot.scan_unavailable,
                reason=path_snapshot.reason,
            )
        existing = _scan_existing_item_metadata(brain / "items")
        records = [
            self._preview_record(
                path,
                existing_items=existing.items,
                metadata_trusted=existing.trusted,
            )
            for path in classification_paths
        ]
        records = _reconcile_pending_identity_collisions(records)
        if path_snapshot.total > scan_cap:
            records = [
                replace(
                    record,
                    classification="audit_blocked",
                    reason="PENDING_QUEUE_TRUNCATED",
                    malformed=False,
                    error=None,
                )
                for record in records
            ]
        displayed = records[:bounded_limit]
        return PendingPreview(
            total=path_snapshot.total,
            returned=len(displayed),
            limit=bounded_limit,
            truncated=path_snapshot.total > len(displayed),
            records=displayed,
            scan_unavailable=path_snapshot.scan_unavailable,
            reason=path_snapshot.reason,
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
                res = svc.write(
                    item=item, body=f.get("body", ""), allow_unsafe=f.get("allow_unsafe", False)
                )
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

    validated_item, validation_reason = _validate_pending_item(
        item=item,
        version=version,
        stable_item_id=_pending_item_id(title, original_created_at, record_id),
        original_created_at=original_created_at,
        payload_sha256=payload_sha256,
    )
    if validated_item is None:
        reason = validation_reason or "INVALID_ITEM_SCHEMA"
        return PendingRecordPreview(
            **common,
            classification="malformed",
            reason=reason,
            malformed=True,
            error=reason,
        )

    if not metadata_trusted:
        return PendingRecordPreview(
            **common,
            classification="audit_blocked",
            reason="EXISTING_ITEM_SCAN_UNAVAILABLE",
        )

    stable_item_id = validated_item.id
    stable_existing = existing_items.get(stable_item_id)
    if stable_existing is not None:
        if not _same_scope(validated_item, stable_existing):
            return PendingRecordPreview(
                **common,
                classification="conflict",
                reason="STABLE_ITEM_SCOPE_CONFLICT",
            )
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
        item=validated_item,
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
    sensitivity, redact = _preview_sensitivity(item)
    title = _optional_str(item.get("title"))
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
        "title": None if redact else title,
        "summary": None if redact else _optional_str(item.get("summary")),
        "type": _optional_str(item.get("type", "fact")),
        "project": None if redact else _optional_str(item.get("project")),
        "agent": None if redact else _optional_str(item.get("agent")),
        "session": None if redact else _optional_str(item.get("session")),
        "sensitivity": sensitivity,
        "allow_unsafe": bool(item.get("allow_unsafe")),
        "_stable_item_id": (
            _pending_item_id(title, original_created_at, record_id) if title else None
        ),
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
    sensitivity, redact = _preview_sensitivity(item)
    return PendingRecordPreview(
        path=str(path),
        record_id=record_id or _malformed_record_id(path, raw),
        enqueued_at=enqueued_at.isoformat() if enqueued_at else None,
        original_created_at=(original_created_at.isoformat() if original_created_at else None),
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
        _stable_item_id=None,
    )


def _preview_sensitivity(item: dict[str, object]) -> tuple[str | None, bool]:
    if "sensitivity" not in item:
        return "internal", False
    value = item.get("sensitivity")
    if not isinstance(value, str):
        return None, True
    if value in {"public", "internal"}:
        return value, False
    if value in {"private", "secret"}:
        return value, True
    return None, True


def _reconcile_pending_identity_collisions(
    records: list[PendingRecordPreview],
) -> list[PendingRecordPreview]:
    conflicts: dict[int, str] = {}
    duplicates: set[int] = set()
    getters: tuple[tuple[str, Callable[[PendingRecordPreview], str | None]], ...] = (
        ("RECORD_ID", lambda row: row.record_id),
        ("STABLE_ID", lambda row: row._stable_item_id),
    )
    for identity_kind, getter in getters:
        groups: dict[str, list[int]] = {}
        for index, record in enumerate(records):
            identity = getter(record)
            if identity:
                groups.setdefault(identity, []).append(index)
        for indexes in groups.values():
            if len(indexes) < 2:
                continue
            hashes = {records[index].payload_sha256 for index in indexes}
            if len(hashes) != 1 or None in hashes:
                reason = f"PENDING_{identity_kind}_CONFLICT"
                for index in indexes:
                    conflicts.setdefault(index, reason)
                continue
            for index in indexes[1:]:
                duplicates.add(index)

    reconciled: list[PendingRecordPreview] = []
    for index, record in enumerate(records):
        if index in conflicts:
            reconciled.append(
                replace(
                    record,
                    classification="conflict",
                    reason=conflicts[index],
                    malformed=False,
                    error=None,
                )
            )
        elif index in duplicates and record.classification == "ready":
            reconciled.append(
                replace(
                    record,
                    classification="duplicate_candidate",
                    reason="PENDING_RECORD_DUPLICATE",
                )
            )
        else:
            reconciled.append(record)
    return reconciled


def _validate_pending_item(
    *,
    item: dict[str, object],
    version: int,
    stable_item_id: str,
    original_created_at: datetime,
    payload_sha256: str,
) -> tuple[MemoryItem | None, str | None]:
    if set(item) - _PENDING_ITEM_FIELDS:
        return None, "INVALID_ITEM_SCHEMA"
    allow_unsafe = item.get("allow_unsafe", False)
    if not isinstance(allow_unsafe, bool):
        return None, "INVALID_ITEM_SCHEMA"
    for field, model in (("refs", Refs), ("validity", Validity), ("source", Source)):
        nested = item.get(field)
        if isinstance(nested, dict) and set(nested) - set(model.model_fields):
            return None, "INVALID_ITEM_SCHEMA"
        if nested is not None:
            try:
                model.model_validate(nested)
            except (ValidationError, TypeError, ValueError, OverflowError):
                return None, "INVALID_ITEM_SCHEMA"
    item_created_at = item.get("created_at")
    if item_created_at is not None:
        parsed_created_at, created_reason = _parse_pending_time(
            item_created_at, field="ITEM_CREATED_AT"
        )
        if created_reason or parsed_created_at is None:
            return None, "INVALID_ITEM_SCHEMA"
        if version == 2 and parsed_created_at != original_created_at:
            return None, "ITEM_CREATED_AT_MISMATCH"
    payload = {
        key: value
        for key, value in item.items()
        if key not in {"body", "allow_unsafe", "created_at", "source"}
    }
    payload.setdefault("type", "fact")
    payload.setdefault("summary", "")
    payload.setdefault("tags", [])
    payload.setdefault("confidence", 0.7)
    payload.setdefault("sensitivity", "internal")
    payload["refs"] = item.get("refs") or {}
    payload["validity"] = item.get("validity") or {}
    payload.update(
        {
            "id": stable_item_id,
            "created_at": original_created_at,
            "source": Source(kind="pending-replay", span_hash=payload_sha256),
        }
    )
    try:
        return MemoryItem.model_validate(payload), None
    except (ValidationError, TypeError, ValueError, OverflowError):
        return None, "INVALID_ITEM_SCHEMA"


def _scope_value(value: object) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _same_scope(first: MemoryItem, second: MemoryItem) -> bool:
    return _scope_value(first.project) == _scope_value(second.project) and _scope_value(
        first.tenant_id
    ) == _scope_value(second.tenant_id)


def _same_scope_duplicate_reason(
    *,
    item: MemoryItem,
    payload_sha256: str,
    existing_items: Iterable[MemoryItem],
) -> str | None:
    project = _scope_value(item.project)
    tenant = _scope_value(item.tenant_id)
    type_value = str(item.type)
    title = item.title.strip().lower()
    summary = item.summary.strip().lower()
    for existing in existing_items:
        if _scope_value(existing.project) != project or _scope_value(existing.tenant_id) != tenant:
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
            return datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
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
    try:
        return parsed.astimezone(timezone.utc), None
    except (OverflowError, ValueError):
        return None, f"INVALID_{field}"


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


def _pending_record_paths(directory: Path) -> _PendingPathSnapshot:
    retained: list[tuple[_DescendingName, str, Path]] = []
    total = 0
    scan_failed = False
    retain_limit = max(0, MAX_PENDING_QUEUE_ENTRIES) + 1

    def consider(entry: os.DirEntry[str], *, fallback: bool) -> None:
        nonlocal scan_failed, total
        try:
            if not entry.name.endswith(".jsonl") or entry.is_symlink():
                return
            if fallback:
                if not stat.S_ISREG(entry.stat(follow_symlinks=False).st_mode):
                    return
            elif not entry.is_file(follow_symlinks=False):
                return
        except OSError:
            scan_failed = True
            return
        total += 1
        candidate = (_DescendingName(entry.name), entry.name, directory / entry.name)
        if len(retained) < retain_limit:
            heapq.heappush(retained, candidate)
        elif entry.name < retained[0][1]:
            heapq.heapreplace(retained, candidate)

    if secure_dir_fd_io_supported():
        descriptor: int | None = None
        try:
            descriptor = open_directory_path_without_symlinks(directory)
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    consider(entry, fallback=False)
        except FileNotFoundError:
            return _PendingPathSnapshot(paths=[], total=0)
        except OSError:
            return _PendingPathSnapshot(
                paths=[],
                total=0,
                scan_unavailable=True,
                reason="PENDING_SCAN_UNAVAILABLE",
            )
        finally:
            if descriptor is not None:
                close_descriptor(descriptor)
        ordered = sorted((row[2] for row in retained), key=lambda path: path.name)
        return _PendingPathSnapshot(
            paths=ordered,
            total=total,
            scan_unavailable=scan_failed,
            reason="PENDING_SCAN_UNAVAILABLE" if scan_failed else None,
        )

    try:
        before = os.lstat(directory)
        if not stat.S_ISDIR(before.st_mode):
            return _PendingPathSnapshot(
                paths=[],
                total=0,
                scan_unavailable=True,
                reason="PENDING_SCAN_UNAVAILABLE",
            )
        with os.scandir(directory) as entries:
            for entry in entries:
                consider(entry, fallback=True)
        after = os.stat(directory, follow_symlinks=False)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            scan_failed = True
    except FileNotFoundError:
        return _PendingPathSnapshot(paths=[], total=0)
    except OSError:
        return _PendingPathSnapshot(
            paths=[],
            total=0,
            scan_unavailable=True,
            reason="PENDING_SCAN_UNAVAILABLE",
        )
    ordered = sorted((row[2] for row in retained), key=lambda path: path.name)
    return _PendingPathSnapshot(
        paths=ordered,
        total=total,
        scan_unavailable=scan_failed,
        reason="PENDING_SCAN_UNAVAILABLE" if scan_failed else None,
    )


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
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ) or len(data) != after.st_size:
        raise _PendingReadError("PENDING_RECORD_CHANGED")
    return data


def _scan_existing_item_metadata(items_dir: Path) -> _ItemMetadataSnapshot:
    """Read bounded item frontmatter only; never load Markdown bodies."""

    if not secure_dir_fd_io_supported():
        return _scan_existing_item_metadata_fallback(items_dir)
    root: int | None = None
    stack: list[tuple[int, Iterator[os.DirEntry[str]], int]] = []
    items: dict[str, MemoryItem] = {}
    entry_count = 0
    bytes_read = 0
    try:
        root = open_directory_path_without_symlinks(items_dir)
        stack.append((root, os.scandir(root), 0))
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
                _close_item_scan_frame(stack.pop())
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
                    try:
                        child_entries = os.scandir(child)
                    except BaseException:
                        close_descriptor(child)
                        raise
                    stack.append((child, child_entries, depth + 1))
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
            _close_item_scan_frame(stack.pop())


def _verify_fallback_directory_chain(path: Path) -> os.stat_result:
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    opened = os.lstat(current)
    if not stat.S_ISDIR(opened.st_mode):
        raise OSError("trusted path anchor is not a directory")
    for component in absolute.parts[1:]:
        if component in {"", ".", ".."}:
            raise OSError("invalid trusted directory component")
        current /= component
        opened = os.lstat(current)
        if not stat.S_ISDIR(opened.st_mode):
            raise OSError("trusted path component is not a directory")
    return opened


def _scan_existing_item_metadata_fallback(items_dir: Path) -> _ItemMetadataSnapshot:
    """Windows-compatible lstat/open/fstat frontmatter-only traversal."""

    items: dict[str, MemoryItem] = {}
    entry_count = 0
    bytes_read = 0
    stack: list[tuple[Path, Iterator[os.DirEntry[str]], int, os.stat_result]] = []
    try:
        root_identity = _verify_fallback_directory_chain(items_dir)
        stack.append((items_dir, os.scandir(items_dir), 0, root_identity))
    except FileNotFoundError:
        return _ItemMetadataSnapshot(items={}, trusted=True)
    except OSError:
        return _ItemMetadataSnapshot(items={}, trusted=False)
    try:
        while stack:
            directory, entries, depth, identity = stack[-1]
            try:
                entry = next(entries)
            except StopIteration:
                close = getattr(entries, "close", None)
                if callable(close):
                    close()
                stack.pop()
                try:
                    if not os.path.samestat(identity, os.lstat(directory)):
                        return _ItemMetadataSnapshot(items={}, trusted=False)
                except OSError:
                    return _ItemMetadataSnapshot(items={}, trusted=False)
                continue
            except OSError:
                return _ItemMetadataSnapshot(items={}, trusted=False)
            entry_count += 1
            if entry_count > MAX_ITEM_METADATA_ENTRIES:
                return _ItemMetadataSnapshot(items={}, trusted=False)
            path = directory / entry.name
            try:
                opened = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(opened.st_mode):
                    continue
                if stat.S_ISDIR(opened.st_mode):
                    if depth >= MAX_ITEM_DIRECTORY_DEPTH:
                        return _ItemMetadataSnapshot(items={}, trusted=False)
                    stack.append((path, os.scandir(path), depth + 1, opened))
                    continue
                if not stat.S_ISREG(opened.st_mode) or not entry.name.endswith(".md"):
                    continue
                item, consumed = _read_item_frontmatter_fallback(path)
                bytes_read += consumed
                if bytes_read > MAX_ITEM_METADATA_BYTES or item is None:
                    return _ItemMetadataSnapshot(items={}, trusted=False)
                if path.stem != item.id or item.id in items:
                    return _ItemMetadataSnapshot(items={}, trusted=False)
                items[item.id] = item
            except OSError:
                return _ItemMetadataSnapshot(items={}, trusted=False)
        return _ItemMetadataSnapshot(items=items, trusted=True)
    finally:
        while stack:
            _directory, entries, _depth, _identity = stack.pop()
            close = getattr(entries, "close", None)
            if callable(close):
                close()


def _read_item_frontmatter_fallback(path: Path) -> tuple[MemoryItem | None, int]:
    consumed = 0
    descriptor: int | None = None
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode):
            return None, consumed
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(before, opened):
            return None, consumed
        handle = cast(BinaryIO, os.fdopen(descriptor, "rb", buffering=0))
        descriptor = None
        with handle:
            opening = handle.readline(MAX_ITEM_FRONTMATTER_BYTES + 1)
            consumed += len(opening)
            normalized_opening = opening.rstrip(b"\r\n")
            if normalized_opening.startswith(b"\xef\xbb\xbf"):
                normalized_opening = normalized_opening[3:]
            if normalized_opening != b"---":
                return None, consumed
            lines: list[bytes] = []
            while consumed <= MAX_ITEM_FRONTMATTER_BYTES:
                line = handle.readline(MAX_ITEM_FRONTMATTER_BYTES - consumed + 1)
                consumed += len(line)
                if consumed > MAX_ITEM_FRONTMATTER_BYTES or not line:
                    return None, consumed
                if line.rstrip(b"\r\n") == b"---":
                    frontmatter = b"---\n" + b"".join(lines) + b"---\n"
                    item, _body = parse_item_markdown(frontmatter.decode("utf-8"))
                    after = os.fstat(handle.fileno())
                    if (
                        opened.st_size != after.st_size
                        or opened.st_mtime_ns != after.st_mtime_ns
                        or not os.path.samestat(opened, after)
                    ):
                        return None, consumed
                    return item, consumed
                lines.append(line)
    except (
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        OverflowError,
        ValidationError,
        yaml.YAMLError,
    ):
        return None, consumed
    finally:
        if descriptor is not None:
            close_descriptor(descriptor)
    return None, consumed


def _close_item_scan_frame(frame: tuple[int, Iterator[os.DirEntry[str]], int]) -> None:
    directory, entries, _depth = frame
    close = getattr(entries, "close", None)
    try:
        if callable(close):
            close()
    finally:
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
    except (
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        OverflowError,
        ValidationError,
        yaml.YAMLError,
    ):
        return None, consumed
    return None, consumed


@contextmanager
def _open_regular_binary(directory_descriptor: int, filename: str) -> Iterator[BinaryIO]:
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
