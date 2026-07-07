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

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


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


def enqueue_write_record(record: dict) -> Path:
    """Append one write record to the pending queue and return its file path.

    The record is a plain dict shaped ``{"op": "write", "item": {...}}``; the
    ``item`` payload carries the fields needed to rebuild a ``MemoryItem`` at
    replay time (title/summary/body/type/tags/sensitivity/confidence/...). Bookkeeping
    keys (``v``, ``ts``, ``attempt``) are filled in if absent so hand-rolled shell
    records and Python records share one format.
    """
    d = pending_dir()
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = d / f"{ts}-{uuid.uuid4().hex[:8]}.jsonl"
    record.setdefault("v", 1)
    record.setdefault("ts", datetime.now(timezone.utc).isoformat())
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


class PendingQueue:
    """Durable buffer of pending writes, drained through ``WriteService``."""

    def depth(self) -> int:
        """Number of records still buffered (excludes the dead/ sub-dir)."""
        d = pending_dir()
        return len(list(d.glob("*.jsonl"))) if d.exists() else 0

    def preview(self, *, limit: int = 20) -> PendingPreview:
        """Summarize queued records without replaying or mutating them."""
        d = pending_dir()
        paths = sorted(d.glob("*.jsonl")) if d.exists() else []
        bounded_limit = max(0, limit)
        selected = paths[:bounded_limit]
        records = [self._preview_record(path) for path in selected]
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
        from agent_brain.memory.store.items_store import make_item_id
        from agent_brain.memory.store.write_service import WriteService
        from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs, Sensitivity, Source

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
                    validity=f.get("validity") or {},
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

    def _preview_record(self, path: Path) -> PendingRecordPreview:
        try:
            rec = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[0])
            item = rec.get("item") if isinstance(rec.get("item"), dict) else {}
            return PendingRecordPreview(
                path=str(path),
                op=_optional_str(rec.get("op")),
                origin=_optional_str(rec.get("origin")),
                attempt=int(rec.get("attempt") or 0),
                title=_optional_str(item.get("title")),
                summary=_optional_str(item.get("summary")),
                type=_optional_str(item.get("type")),
                project=_optional_str(item.get("project")),
                agent=_optional_str(item.get("agent")),
                session=_optional_str(item.get("session")),
                sensitivity=_optional_str(item.get("sensitivity")),
                allow_unsafe=bool(item.get("allow_unsafe")),
            )
        except Exception as exc:
            return PendingRecordPreview(
                path=str(path),
                op=None,
                origin=None,
                attempt=0,
                title=None,
                summary=None,
                type=None,
                project=None,
                agent=None,
                session=None,
                sensitivity=None,
                allow_unsafe=False,
                malformed=True,
                error=str(exc),
            )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
