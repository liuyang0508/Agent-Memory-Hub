"""Durable write buffer for when the full write machinery is unreachable.

What it does:
    A *pending record* is one JSON line written under
    ``$BRAIN_DIR/pending/`` (default ``~/.agent-memory-hub/pending/``). When the
    Python write path can't run — no interpreter on PATH for the hook shim, a
    locked sqlite, an embedder that won't import — the writer drops the intended
    write here instead of losing it. ``PendingQueue.preview()`` classifies the
    backlog without writes; an explicit ``PendingQueue.apply(record_ids=...)``
    or ``apply(safe_only=True)`` later re-drives selected safe records through
    the one true ``WriteService`` funnel.

How to use it::

    from agent_brain.memory.store.pending import enqueue_write_record, PendingQueue

    enqueue_write_record({"op": "write", "item": {"title": ..., "summary": ...}})
    preview = PendingQueue().preview()
    stats = PendingQueue().apply(record_ids=[preview.records[0].record_id])
    PendingQueue().depth()            # how many records are still buffered

Apply is exactly-once at the item boundary: stable item identity plus the source
payload hash recover a write that completed before queue unlink. Review-required
records remain queued; no-argument ``replay()`` is a compatibility no-op rather
than an implicit bulk mutation.

Depends on: ``WriteService`` (the shared write funnel), ``MemoryItem`` + its
enums (record → item mapping), and ``ItemsStore`` locks. The
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
import threading
import time
import unicodedata
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field as dataclass_field, replace
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import BinaryIO, Literal, TypedDict, cast

import yaml
from pydantic import ValidationError

from agent_brain.contracts.memory_enums import MemoryType
from agent_brain.contracts.memory_item import (
    MemoryItem,
    Refs,
    Source,
    Validity,
    is_valid_memory_item_id,
)
from agent_brain.memory.store.item_markdown import parse_item_markdown
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.governance.pending_receipts import (
    PendingBatchReceipt,
    PendingReceiptOutcome,
    PendingReceiptSelection,
    append_pending_receipt,
    complete_pending_receipt,
    incomplete_pending_receipt,
    prepare_pending_receipt,
)
from agent_brain.memory.governance.pending_lock_gc import (
    PendingLockGcReport,
    collect_pending_record_locks,
    pending_record_lock_name,
)
from agent_brain.memory.governance.audit.report import AuditReport
from agent_brain.memory.store.durable_fs import (
    SecureDirectory,
    lifecycle_mutation_capability,
)
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


def dirty_index_path(brain: Path | None = None) -> Path:
    """Append-only log of item ids whose md landed but whose index row is stale.

    ``WriteService`` appends here when the best-effort index upsert fails so a
    later reindex/``sync-pending`` can repair the derived index.
    """
    root = Path(brain).expanduser().resolve(strict=False) if brain is not None else brain_dir()
    return root / ".index-dirty"


MAX_PENDING_RECORD_BYTES = 1024 * 1024
MAX_PENDING_QUEUE_ENTRIES = 20_000
MAX_DIRTY_INDEX_BYTES = 4 * 1024 * 1024
MAX_DIRTY_INDEX_ENTRIES = 20_000
MAX_ITEM_FRONTMATTER_BYTES = 64 * 1024
MAX_ITEM_METADATA_ENTRIES = 20_000
MAX_ITEM_METADATA_BYTES = 64 * 1024 * 1024
MAX_ITEM_DIRECTORY_DEPTH = 32
STALE_EPHEMERAL_SECONDS = 30 * 24 * 60 * 60
_monotonic = time.monotonic

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
PendingResolutionName = Literal[
    "approve_audit",
    "accept_duplicate",
    "convert_type",
]
PendingResolutionPublicName = PendingResolutionName | Literal["unknown"]
PendingResolutionStatus = Literal["ready", "applied", "blocked", "failed"]
PendingResolutionPublicStatus = PendingResolutionStatus | Literal["unknown"]
PendingResolutionPublicClassification = (
    PendingClassification | Literal["unknown"] | None
)

_PUBLIC_PENDING_REASONS = frozenset(
    {
        "AUDIT_BLOCKED",
        "AUDIT_SCAN_FAILED",
        "CONCURRENT_MODIFICATION",
        "CONFLICTING_PENDING_RESOLUTIONS",
        "DUPLICATE_RECORD_ID_SELECTION",
        "EMPTY_PENDING_RECORD",
        "EVIDENCE_SIDECAR_REPAIR_REQUIRED",
        "EXISTING_ITEM_SCAN_UNAVAILABLE",
        "FUTURE_ENQUEUED_AT",
        "FUTURE_ORIGINAL_CREATED_AT",
        "INVALID_ENQUEUED_AT",
        "INVALID_ITEM_BODY",
        "INVALID_ITEM_PAYLOAD",
        "INVALID_ITEM_SCHEMA",
        "INVALID_ITEM_TITLE",
        "INVALID_ORIGINAL_CREATED_AT",
        "INVALID_RECORD_ENCODING",
        "INVALID_RECORD_ID",
        "ITEM_CREATED_AT_MISMATCH",
        "MALFORMED_JSON",
        "MISSING_ENQUEUED_AT",
        "MISSING_ORIGINAL_CREATED_AT",
        "MISSING_PAYLOAD_SHA256",
        "NAIVE_ENQUEUED_AT",
        "NAIVE_ORIGINAL_CREATED_AT",
        "PAYLOAD_HASH_MISMATCH",
        "PENDING_APPLY_FAILED",
        "PENDING_AUDIT_APPROVAL_REQUIRED",
        "PENDING_AUDIT_FINDINGS_CHANGED",
        "PENDING_AUDIT_SECRET_BLOCKED",
        "PENDING_CONVERSION_INVALID",
        "PENDING_CONVERSION_UNSUPPORTED",
        "PENDING_DIRECTORY_FSYNC_UNAVAILABLE",
        "PENDING_ITEM_SNAPSHOT_UNTRUSTED",
        "PENDING_QUEUE_TRUNCATED",
        "PENDING_READINESS_BUDGET_EXCEEDED",
        "PENDING_RECEIPT_COMPLETION_FAILED",
        "PENDING_RECEIPT_PREPARE_FAILED",
        "PENDING_RECORD_CHANGED",
        "PENDING_RECORD_DUPLICATE",
        "PENDING_RECORD_ID_CONFLICT",
        "PENDING_RECORD_NOT_OBJECT",
        "PENDING_RECORD_NOT_REGULAR",
        "PENDING_RECORD_READ_FAILED",
        "PENDING_RECORD_TOO_LARGE",
        "PENDING_RESOLUTION_CHANGED",
        "PENDING_RESOLUTION_APPLIED",
        "PENDING_RESOLUTION_NOT_APPLICABLE",
        "PENDING_RESOLUTION_READY",
        "PENDING_RESOLUTION_REQUEST_TOO_LARGE",
        "PENDING_SCAN_UNAVAILABLE",
        "PENDING_STABLE_ID_CONFLICT",
        "PENDING_DUPLICATE_TARGET_MISMATCH",
        "PENDING_UNLINK_FAILED",
        "PENDING_WRITE_SERVICE_UNAVAILABLE",
        "PENDING_WRITE_SERVICE_CLOSE_FAILED",
        "PENDING_WRITE_EVIDENCE_INVALID",
        "PLATFORM_UNSUPPORTED",
        "READY",
        "RECORD_ID_NOT_FOUND",
        "SAME_SCOPE_METADATA_DUPLICATE",
        "SAME_SCOPE_PAYLOAD_DUPLICATE",
        "STABLE_ITEM_ALREADY_WRITTEN",
        "STABLE_ITEM_ALREADY_WRITTEN_INDEX_REPAIR_REQUIRED",
        "STABLE_ITEM_PAYLOAD_CONFLICT",
        "STABLE_ITEM_SCOPE_CONFLICT",
        "STALE_EPHEMERAL_MEMORY",
        "SOURCE_LEDGER_REPAIR_REQUIRED",
        "UNSUPPORTED_MEMORY_TYPE",
        "UNSUPPORTED_PENDING_OPERATION",
        "UNSUPPORTED_PENDING_VERSION",
        "WRITTEN",
        "WRITTEN_INDEX_REPAIR_REQUIRED",
    }
)


def _public_pending_reason(reason: object) -> str | None:
    if reason is None:
        return None
    if type(reason) is str and reason in _PUBLIC_PENDING_REASONS:
        return reason
    return "UNKNOWN_PENDING_REASON"

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
_FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
_PENDING_ACCESS_FAILURE_REASONS = frozenset(
    {
        "PENDING_RECORD_CHANGED",
        "PENDING_RECORD_NOT_REGULAR",
        "PENDING_RECORD_READ_FAILED",
        "PENDING_RECORD_TOO_LARGE",
    }
)
_PENDING_RECORD_LOCKS_GUARD = threading.Lock()
_PENDING_RECORD_LOCKS: dict[str, threading.RLock] = {}
_PENDING_QUEUE_LOCKS_GUARD = threading.Lock()
_PENDING_QUEUE_LOCKS: dict[str, threading.RLock] = {}
_INDEX_DIRTY_LOCKS_GUARD = threading.Lock()
_INDEX_DIRTY_LOCKS: dict[str, threading.RLock] = {}
_WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)


def _is_reparse_point(opened: object) -> bool:
    attributes = int(getattr(opened, "st_file_attributes", 0) or 0)
    return bool(attributes & _FILE_ATTRIBUTE_REPARSE_POINT)


def _is_safe_directory(opened: object) -> bool:
    return stat.S_ISDIR(int(getattr(opened, "st_mode"))) and not _is_reparse_point(opened)


def _is_safe_regular_file(opened: object) -> bool:
    return stat.S_ISREG(int(getattr(opened, "st_mode"))) and not _is_reparse_point(opened)


def _same_file_identity(first: object, second: object) -> bool:
    """Compare only reliable filesystem object identifiers; never infer identity."""

    first_pair = (
        int(getattr(first, "st_dev", 0) or 0),
        int(getattr(first, "st_ino", 0) or 0),
    )
    second_pair = (
        int(getattr(second, "st_dev", 0) or 0),
        int(getattr(second, "st_ino", 0) or 0),
    )
    return all(first_pair) and all(second_pair) and first_pair == second_pair


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
        if not _is_safe_directory(opened):
            raise PendingEnqueueError("UNSAFE_PENDING_DIRECTORY")
        if created:
            try:
                os.chmod(current, mode, follow_symlinks=False)
            except (NotImplementedError, OSError):
                if os.name != "nt":
                    raise
            _fsync_fallback_directory(current.parent)


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
        if not _is_safe_regular_file(before):
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
            if not _is_safe_regular_file(opened) or not _same_file_identity(before, opened):
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
    if not _is_safe_directory(directory_before):
        raise PendingEnqueueError("UNSAFE_PENDING_DIRECTORY")
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
        if not _is_safe_regular_file(temp_identity):
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
            if not _is_safe_regular_file(target_identity) or not _same_file_identity(
                temp_identity, target_identity
            ):
                raise PendingEnqueueError("PENDING_RECORD_PUBLISH_IDENTITY_MISMATCH")
        directory_after = os.lstat(directory)
        if not _is_safe_directory(directory_after) or not _same_file_identity(
            directory_before, directory_after
        ):
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
    normalized = (
        unicodedata.normalize("NFKD", title.casefold()).encode("ascii", "ignore").decode("ascii")
    )
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")[:30].rstrip("-")
    if not slug or slug in _WINDOWS_RESERVED_NAMES:
        slug = "pending"
    stable = hashlib.sha256(record_id.encode("utf-8")).hexdigest()[:24]
    return f"mem-{utc_created_at:%Y%m%d-%H%M%S}-{slug}-{stable}"


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
    with _locked_pending_queue(brain):
        return _publish_pending_record(brain, filename, data)


@dataclass(frozen=True)
class PendingResolutionAction:
    """One explicit request to resolve a review-only pending record."""

    action: PendingResolutionName
    record_id: str
    target: str | None = None


@dataclass(frozen=True)
class PendingResolutionResult:
    """Detailed read-only outcome for one pending resolution request."""

    action: PendingResolutionPublicName
    record_id: str
    status: PendingResolutionPublicStatus
    reason: str
    classification: PendingResolutionPublicClassification
    target: str | None = None
    item_id: str | None = None
    index_repair_required: bool = False
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", _public_resolution_action(self.action))
        object.__setattr__(self, "status", _public_resolution_status(self.status))
        object.__setattr__(
            self,
            "classification",
            _public_resolution_classification(self.classification),
        )
        object.__setattr__(
            self,
            "reason",
            _public_pending_reason(self.reason) or "UNKNOWN_PENDING_REASON",
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "action": _public_resolution_action(self.action),
            "record_id": self.record_id,
            "status": _public_resolution_status(self.status),
            "reason": _public_pending_reason(self.reason) or "UNKNOWN_PENDING_REASON",
            "classification": _public_resolution_classification(self.classification),
            "target": self.target,
            "item_id": self.item_id,
            "index_repair_required": self.index_repair_required,
            "warnings": list(self.warnings),
        }


@dataclass
class PendingResolutionStats:
    """Aggregate read-only outcomes for explicit pending resolutions."""

    dry_run: bool
    results: list[PendingResolutionResult] = dataclass_field(default_factory=list)
    receipt: PendingBatchReceipt | None = None
    governance_reason: str | None = None
    lock_gc_report: PendingLockGcReport | None = None

    def __post_init__(self) -> None:
        self.governance_reason = _public_pending_reason(self.governance_reason)

    def to_dict(self) -> dict[str, object]:
        return {
            "dry_run": self.dry_run,
            "results": [result.to_dict() for result in self.results],
            "receipt": self.receipt.to_dict() if self.receipt is not None else None,
            "governance_reason": _public_pending_reason(self.governance_reason),
            "lock_gc": (
                self.lock_gc_report.to_dict() if self.lock_gc_report is not None else None
            ),
        }

    def to_summary_dict(self) -> dict[str, object]:
        """Return aggregate outcomes without record, item, target, or content fields."""

        return {
            "schema_version": 1,
            "dry_run": self.dry_run,
            "action_counts": dict(
                sorted(
                    Counter(
                        _public_resolution_action(result.action)
                        for result in self.results
                    ).items()
                )
            ),
            "status_counts": dict(
                sorted(
                    Counter(
                        _public_resolution_status(result.status)
                        for result in self.results
                    ).items()
                )
            ),
            "classification_counts": dict(
                sorted(
                    Counter(
                        _public_resolution_classification(result.classification)
                        or "unknown"
                        for result in self.results
                    ).items()
                )
            ),
            "reason_counts": dict(
                sorted(
                    Counter(
                        _public_pending_reason(result.reason)
                        or "UNKNOWN_PENDING_REASON"
                        for result in self.results
                    ).items()
                )
            ),
            "receipt": self.receipt.to_dict() if self.receipt is not None else None,
            "governance_reason": _public_pending_reason(self.governance_reason),
            "lock_gc": (
                self.lock_gc_report.to_dict() if self.lock_gc_report is not None else None
            ),
        }


PendingApplyStatus = Literal[
    "written",
    "already_written",
    "review_required",
    "skipped",
    "failed",
]


@dataclass(frozen=True)
class PendingApplyResult:
    """Low-sensitivity outcome for one explicit pending apply decision."""

    record_id: str
    classification: PendingClassification | None
    status: PendingApplyStatus
    reason: str
    item_id: str | None = None
    index_repair_required: bool = False
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "record_id": self.record_id,
            "classification": self.classification,
            "status": self.status,
            "reason": self.reason,
            "item_id": self.item_id,
            "index_repair_required": self.index_repair_required,
            "warnings": list(self.warnings),
        }


@dataclass
class PendingApplyStats:
    """Aggregate and per-record outcomes for an explicit apply request."""

    written: int = 0
    already_written: int = 0
    review_required: int = 0
    skipped: int = 0
    failed: int = 0
    dead: int = 0
    results: list[PendingApplyResult] = dataclass_field(default_factory=list)
    receipt: PendingBatchReceipt | None = None
    governance_reason: str | None = None
    lock_gc_report: PendingLockGcReport | None = None

    def add(self, result: PendingApplyResult) -> None:
        self.results.append(result)
        if result.status == "written":
            self.written += 1
        elif result.status == "already_written":
            self.already_written += 1
        elif result.status == "review_required":
            self.review_required += 1
        elif result.status == "skipped":
            self.skipped += 1
        elif result.status == "failed":
            self.failed += 1

    def to_dict(self) -> dict[str, object]:
        return {
            "written": self.written,
            "already_written": self.already_written,
            "review_required": self.review_required,
            "skipped": self.skipped,
            "failed": self.failed,
            "dead": self.dead,
            "results": [result.to_dict() for result in self.results],
            "receipt": self.receipt.to_dict() if self.receipt is not None else None,
            "governance_reason": self.governance_reason,
            "lock_gc": (
                self.lock_gc_report.to_dict() if self.lock_gc_report is not None else None
            ),
        }

    def to_summary_dict(self) -> dict[str, object]:
        """Return aggregate apply outcomes without record or item identifiers."""

        statuses = Counter(result.status for result in self.results)
        classifications = Counter(
            result.classification or "unknown" for result in self.results
        )
        reasons = Counter(
            _public_pending_reason(result.reason) or "UNKNOWN_PENDING_REASON"
            for result in self.results
        )
        warnings = Counter(
            _public_pending_reason(warning) or "UNKNOWN_PENDING_REASON"
            for result in self.results
            for warning in result.warnings
        )
        return {
            "schema_version": 1,
            "written": self.written,
            "already_written": self.already_written,
            "review_required": self.review_required,
            "skipped": self.skipped,
            "failed": self.failed,
            "dead": self.dead,
            "status_counts": dict(sorted(statuses.items())),
            "classification_counts": dict(sorted(classifications.items())),
            "reason_counts": dict(sorted(reasons.items())),
            "index_repair_required_count": sum(
                result.index_repair_required for result in self.results
            ),
            "warning_counts": dict(sorted(warnings.items())),
            "receipt": self.receipt.to_dict() if self.receipt is not None else None,
            "governance_reason": _public_pending_reason(self.governance_reason),
            "lock_gc": (
                self.lock_gc_report.to_dict() if self.lock_gc_report is not None else None
            ),
        }


# Import compatibility for integrations that named the old aggregate type.
ReplayStats = PendingApplyStats


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
    _record_sha256: str | None = dataclass_field(default=None, repr=False, compare=False)
    _record_identity: tuple[int, int] | None = dataclass_field(
        default=None, repr=False, compare=False
    )

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

    def to_summary_dict(self) -> dict[str, object]:
        """Return a bounded aggregate that cannot expose record content or IDs."""

        classifications = Counter(record.classification for record in self.records)
        reasons = Counter(
            _public_pending_reason(record.reason) or "UNKNOWN_PENDING_REASON"
            for record in self.records
        )
        return {
            "schema_version": 1,
            "total": self.total,
            "returned": self.returned,
            "truncated": self.truncated,
            "scan_unavailable": self.scan_unavailable,
            "reason": _public_pending_reason(self.reason),
            "classification_counts": dict(sorted(classifications.items())),
            "reason_counts": dict(sorted(reasons.items())),
            "groups": {
                "ready": classifications["ready"] + classifications["already_written"],
                "review": (
                    classifications["stale_requires_review"]
                    + classifications["duplicate_candidate"]
                    + classifications["unsupported_type"]
                ),
                "blocker": (
                    classifications["conflict"]
                    + classifications["malformed"]
                    + classifications["audit_blocked"]
                ),
            },
            "oldest_age_seconds": max(
                (
                    record.age_seconds
                    for record in self.records
                    if record.age_seconds is not None
                ),
                default=0,
            ),
        }


@dataclass(frozen=True)
class DirtyIndexMarker:
    """Bounded parse result for the derived-index repair marker."""

    status: Literal["clean", "repair_required", "corrupt", "unavailable"]
    item_ids: frozenset[str] = frozenset()
    entries: tuple[str, ...] = ()


@dataclass(frozen=True)
class _DirtyIndexSnapshot:
    marker: DirtyIndexMarker
    identity: tuple[int, int, int, int, int] | None = None


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
class PendingItemCatalogSnapshot:
    """Bounded item metadata supplied to pending classification."""

    items: Mapping[str, MemoryItem]
    trusted: bool
    reason: str | None = None
    entry_count: int = 0
    metadata_bytes: int = 0


@dataclass(frozen=True)
class _PendingResolutionSelection:
    action: PendingResolutionAction
    preview: PendingRecordPreview
    audit_digest: str | None = None
    target_digest: str | None = None
    recovery: bool = False


@dataclass(frozen=True)
class _PendingResolutionIntent:
    item: MemoryItem
    body: str
    audit_digest: str | None = None
    target_digest: str | None = None


# Private compatibility for tests and integrations that imported the old name.
_ItemMetadataSnapshot = PendingItemCatalogSnapshot


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


def read_dirty_index_marker(brain: Path) -> DirtyIndexMarker:
    """Read the dirty-index marker as a bounded canonical ID set."""

    return _read_dirty_index_marker_unlocked(Path(brain)).marker


def _read_dirty_index_marker_unlocked(brain: Path) -> _DirtyIndexSnapshot:
    """Read and parse one marker while the caller holds its file lock."""

    path = dirty_index_path(brain)
    descriptor: int | None = None
    try:
        opened = os.lstat(path)
        if (
            not stat.S_ISREG(opened.st_mode)
            or stat.S_ISLNK(opened.st_mode)
            or opened.st_size > MAX_DIRTY_INDEX_BYTES
        ):
            return _DirtyIndexSnapshot(DirtyIndexMarker("corrupt"))
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        current = os.fstat(descriptor)
        if not _same_file_identity(opened, current) or opened.st_size != current.st_size:
            return _DirtyIndexSnapshot(DirtyIndexMarker("unavailable"))
        data = _read_bounded_descriptor(descriptor, MAX_DIRTY_INDEX_BYTES)
        after = os.lstat(path)
        if not _same_file_identity(current, after) or current.st_size != after.st_size:
            return _DirtyIndexSnapshot(DirtyIndexMarker("unavailable"))
        identity = _dirty_marker_identity(after)
    except FileNotFoundError:
        return _DirtyIndexSnapshot(DirtyIndexMarker("clean"))
    except (OSError, _PendingReadError):
        return _DirtyIndexSnapshot(DirtyIndexMarker("unavailable"))
    finally:
        if descriptor is not None:
            close_descriptor(descriptor)
    if not data:
        return _DirtyIndexSnapshot(DirtyIndexMarker("clean"), identity)
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeError:
        return _DirtyIndexSnapshot(DirtyIndexMarker("corrupt"), identity)
    if (
        not lines
        or len(lines) > MAX_DIRTY_INDEX_ENTRIES
        or any(not is_valid_memory_item_id(line) for line in lines)
    ):
        return _DirtyIndexSnapshot(DirtyIndexMarker("corrupt"), identity)
    return _DirtyIndexSnapshot(
        DirtyIndexMarker("repair_required", frozenset(lines), tuple(lines)),
        identity,
    )


def append_dirty_index_marker(brain: Path, item_id: str) -> bool:
    """Durably append one canonical ID under the shared marker lock."""

    if not is_valid_memory_item_id(item_id):
        return False
    root = Path(brain).expanduser().resolve(strict=False)
    path = dirty_index_path(root)
    descriptor = -1
    created = False
    try:
        with _locked_index_dirty(root):
            root.mkdir(parents=True, exist_ok=True)
            try:
                descriptor = os.open(
                    path,
                    os.O_WRONLY
                    | os.O_APPEND
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_BINARY", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                created = True
            except FileExistsError:
                descriptor = os.open(
                    path,
                    os.O_WRONLY
                    | os.O_APPEND
                    | getattr(os, "O_BINARY", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                )
            if not _is_safe_regular_file(os.fstat(descriptor)):
                raise OSError("UNSAFE_INDEX_DIRTY_MARKER")
            _write_all_descriptor(descriptor, f"{item_id}\n".encode("utf-8"))
            os.fsync(descriptor)
            if created:
                _fsync_fallback_directory(root)
    except OSError:
        return False
    finally:
        if descriptor >= 0:
            close_descriptor(descriptor)
    return True


def clear_dirty_index_marker(
    brain: Path,
    *,
    repaired_ids: Iterable[str] = (),
    expected_entries: Iterable[str] | None = None,
) -> bool:
    """Remove successfully repaired IDs while retaining corrupt markers."""

    marker = read_dirty_index_marker(brain)
    if marker.status == "clean":
        return True
    if marker.status != "repair_required":
        return False
    repaired = frozenset(str(item_id) for item_id in repaired_ids)
    removal_counts = Counter(
        entry
        for entry in (
            marker.entries if expected_entries is None else tuple(expected_entries)
        )
        if entry in repaired
    )
    root = Path(brain).expanduser().resolve(strict=False)
    path = dirty_index_path(root)
    try:
        with _locked_index_dirty(root):
            current = _read_dirty_index_marker_unlocked(root)
            if current.marker.status == "clean":
                return True
            if current.marker.status != "repair_required" or current.identity is None:
                return False
            remaining: list[str] = []
            for entry in current.marker.entries:
                if removal_counts[entry] > 0:
                    removal_counts[entry] -= 1
                else:
                    remaining.append(entry)
            if not remaining:
                if _dirty_marker_identity(os.lstat(path)) != current.identity:
                    return False
                path.unlink()
                _fsync_fallback_directory(path.parent)
                return True
            temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            payload = "".join(f"{item_id}\n" for item_id in remaining)
            try:
                with temporary.open("x", encoding="utf-8") as handle:
                    os.chmod(temporary, 0o600)
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                if _dirty_marker_identity(os.lstat(path)) != current.identity:
                    return False
                os.replace(temporary, path)
                _fsync_fallback_directory(path.parent)
            finally:
                temporary.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _dirty_marker_identity(opened: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(opened.st_dev),
        int(opened.st_ino),
        int(opened.st_size),
        int(opened.st_mtime_ns),
        int(opened.st_ctime_ns),
    )


def _write_all_descriptor(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("INDEX_DIRTY_WRITE_FAILED")
        remaining = remaining[written:]


class PendingQueue:
    """Durable buffer of pending writes, drained through ``WriteService``."""

    def __init__(self, *, brain: Path | None = None) -> None:
        self._brain = Path(brain).expanduser().resolve(strict=False) if brain is not None else None

    def _brain_dir(self) -> Path:
        return self._brain if self._brain is not None else brain_dir()

    def depth(self) -> int:
        """Number of records still buffered (excludes the dead/ sub-dir)."""
        brain = self._brain_dir()
        snapshot = _pending_record_paths(brain / "pending")
        if snapshot.scan_unavailable:
            raise PendingEnqueueError(snapshot.reason or "PENDING_SCAN_UNAVAILABLE")
        return snapshot.total

    def preview(self, *, limit: int = 20) -> PendingPreview:
        """Summarize queued records without replaying or mutating them."""
        brain = self._brain_dir()
        path_snapshot = _pending_record_paths(brain / "pending")
        return self._preview_from_snapshot(path_snapshot, limit=limit)

    def collect_orphan_locks(
        self,
        *,
        apply: bool = False,
    ) -> PendingLockGcReport:
        """Preview orphan record locks, or collect them under the queue lock."""

        if not apply:
            return self._collect_orphan_locks_unlocked(apply=False)
        with _locked_pending_queue(self._brain_dir()):
            return self._collect_orphan_locks_unlocked(apply=True)

    def _preview_from_snapshot(
        self,
        path_snapshot: _PendingPathSnapshot,
        *,
        limit: int,
        deadline_at: float | None = None,
        item_catalog: PendingItemCatalogSnapshot | None = None,
    ) -> PendingPreview:
        brain = self._brain_dir()
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
        existing = item_catalog
        if existing is None:
            existing = _scan_existing_item_metadata(
                brain / "items",
                deadline_at=deadline_at,
            )
        elif not existing.trusted:
            return PendingPreview(
                total=path_snapshot.total,
                returned=0,
                limit=bounded_limit,
                truncated=path_snapshot.total > 0,
                records=[],
                scan_unavailable=True,
                reason="PENDING_ITEM_SNAPSHOT_UNTRUSTED",
            )
        if existing.reason == "PENDING_READINESS_BUDGET_EXCEEDED":
            return PendingPreview(
                total=path_snapshot.total,
                returned=0,
                limit=bounded_limit,
                truncated=path_snapshot.total > 0,
                records=[],
                scan_unavailable=True,
                reason=existing.reason,
            )
        records: list[PendingRecordPreview] = []
        for path in classification_paths:
            if deadline_at is not None and _monotonic() > deadline_at:
                return PendingPreview(
                    total=path_snapshot.total,
                    returned=0,
                    limit=bounded_limit,
                    truncated=path_snapshot.total > 0,
                    records=[],
                    scan_unavailable=True,
                    reason="PENDING_READINESS_BUDGET_EXCEEDED",
                )
            records.append(
                self._preview_record(
                    path,
                    existing_items=existing.items,
                    metadata_trusted=existing.trusted,
                    deadline_at=deadline_at,
                )
            )
            if records[-1].reason == "PENDING_READINESS_BUDGET_EXCEEDED":
                return PendingPreview(
                    total=path_snapshot.total,
                    returned=0,
                    limit=bounded_limit,
                    truncated=path_snapshot.total > 0,
                    records=[],
                    scan_unavailable=True,
                    reason="PENDING_READINESS_BUDGET_EXCEEDED",
                )
            if deadline_at is not None and _monotonic() > deadline_at:
                return PendingPreview(
                    total=path_snapshot.total,
                    returned=0,
                    limit=bounded_limit,
                    truncated=path_snapshot.total > 0,
                    records=[],
                    scan_unavailable=True,
                    reason="PENDING_READINESS_BUDGET_EXCEEDED",
                )
        records = _reconcile_pending_identity_collisions(records)
        record_access_failed = any(
            record.reason in _PENDING_ACCESS_FAILURE_REASONS for record in records
        )
        scan_unavailable = path_snapshot.scan_unavailable or record_access_failed
        preview_reason = "PENDING_SCAN_UNAVAILABLE" if scan_unavailable else path_snapshot.reason
        if scan_unavailable:
            records = [
                replace(
                    record,
                    classification="audit_blocked",
                    reason="PENDING_SCAN_UNAVAILABLE",
                    malformed=False,
                    error=None,
                )
                for record in records
            ]
        elif path_snapshot.total > scan_cap:
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
            scan_unavailable=scan_unavailable,
            reason=preview_reason,
        )

    def preview_for_readiness(
        self,
        *,
        limit: int = 20,
        max_total_bytes: int = 16 * 1024 * 1024,
        deadline_seconds: float = 1.0,
        item_catalog: PendingItemCatalogSnapshot | None = None,
    ) -> PendingPreview:
        """Preview with explicit byte and wall-clock budgets for readiness."""

        bounded_limit = max(0, limit)
        if max_total_bytes < 0 or deadline_seconds <= 0:
            return PendingPreview(
                total=0,
                returned=0,
                limit=bounded_limit,
                truncated=False,
                records=[],
                scan_unavailable=True,
                reason="PENDING_READINESS_BUDGET_EXCEEDED",
            )
        started = _monotonic()
        deadline_at = started + deadline_seconds
        snapshot = _pending_record_paths(
            self._brain_dir() / "pending",
            deadline_at=deadline_at,
            entry_cap=MAX_PENDING_QUEUE_ENTRIES,
        )
        if snapshot.scan_unavailable:
            return PendingPreview(
                total=snapshot.total,
                returned=0,
                limit=bounded_limit,
                truncated=snapshot.total > 0,
                records=[],
                scan_unavailable=True,
                reason=snapshot.reason,
            )
        total_bytes = 0
        try:
            for path in snapshot.paths:
                _require_readiness_deadline(deadline_at)
                opened = os.lstat(path)
                _require_readiness_deadline(deadline_at)
                if not _is_safe_regular_file(opened):
                    raise OSError("pending record is not regular")
                total_bytes += opened.st_size
                if total_bytes > max_total_bytes:
                    raise OverflowError
        except (OSError, OverflowError, _PendingReadError):
            return PendingPreview(
                total=snapshot.total,
                returned=0,
                limit=bounded_limit,
                truncated=snapshot.total > 0,
                records=[],
                scan_unavailable=True,
                reason="PENDING_READINESS_BUDGET_EXCEEDED",
            )
        preview = self._preview_from_snapshot(
            snapshot,
            limit=bounded_limit,
            deadline_at=deadline_at,
            item_catalog=item_catalog,
        )
        if _monotonic() - started <= deadline_seconds:
            return preview
        return PendingPreview(
            total=preview.total,
            returned=0,
            limit=bounded_limit,
            truncated=preview.total > 0,
            records=[],
            scan_unavailable=True,
            reason="PENDING_READINESS_BUDGET_EXCEEDED",
        )

    def resolve(
        self,
        actions: Iterable[PendingResolutionAction],
        *,
        apply: bool = False,
        gc_orphan_locks: bool = False,
    ) -> PendingResolutionStats:
        """Validate or apply explicit governed pending resolutions."""

        stats = PendingResolutionStats(dry_run=not apply)
        cap = max(0, MAX_PENDING_QUEUE_ENTRIES)
        try:
            requested = list(islice(iter(actions), cap + 1))
        except Exception:
            stats.results.append(_invalid_resolution_result())
            return stats
        if not requested:
            return PendingResolutionStats(dry_run=True)
        if len(requested) > cap:
            stats.governance_reason = "PENDING_RESOLUTION_REQUEST_TOO_LARGE"
            for candidate in requested[:cap]:
                stats.results.append(
                    _resolution_result(
                        candidate,
                        status="blocked",
                        reason="PENDING_RESOLUTION_REQUEST_TOO_LARGE",
                    )
                    if type(candidate) is PendingResolutionAction
                    else _invalid_resolution_result(
                        reason="PENDING_RESOLUTION_REQUEST_TOO_LARGE"
                    )
                )
            return stats

        result_slots: list[PendingResolutionResult | None] = [None] * len(requested)
        structurally_valid: list[tuple[int, PendingResolutionAction]] = []
        seen_valid: set[tuple[str, str, str, str]] = set()
        for request_index, candidate in enumerate(requested):
            if type(candidate) is not PendingResolutionAction:
                result_slots[request_index] = _invalid_resolution_result()
                continue
            if not _valid_resolution_action_structure(candidate):
                result_slots[request_index] = _resolution_result(
                    candidate,
                    status="blocked",
                    reason="PENDING_RESOLUTION_NOT_APPLICABLE",
                )
                continue
            target_kind, target_value = _resolution_target_key(candidate.target)
            key = (
                candidate.action,
                candidate.record_id,
                target_kind,
                target_value,
            )
            if key in seen_valid:
                continue
            seen_valid.add(key)
            structurally_valid.append((request_index, candidate))
        if not structurally_valid:
            stats.results = [result for result in result_slots if result is not None]
            return stats

        by_record: dict[str, set[tuple[str, str, str]]] = {}
        for _request_index, action in structurally_valid:
            target_kind, target_value = _resolution_target_key(action.target)
            by_record.setdefault(action.record_id, set()).add(
                (action.action, target_kind, target_value)
            )
        if any(len(selections) > 1 for selections in by_record.values()):
            stats.governance_reason = "CONFLICTING_PENDING_RESOLUTIONS"
            for request_index, action in structurally_valid:
                result_slots[request_index] = _resolution_result(
                    action,
                    status="blocked",
                    reason="CONFLICTING_PENDING_RESOLUTIONS",
                )
            stats.results = [result for result in result_slots if result is not None]
            return stats

        valid: list[tuple[int, PendingResolutionAction]] = []
        for request_index, candidate in structurally_valid:
            rejection_reason = _resolution_target_rejection_reason(candidate)
            if rejection_reason is not None:
                result_slots[request_index] = _resolution_result(
                    candidate,
                    status="blocked",
                    reason=rejection_reason,
                )
                continue
            valid.append((request_index, candidate))
        if not valid:
            stats.results = [result for result in result_slots if result is not None]
            return stats

        if apply:
            _preview, preflight_ready = self._plan_resolutions(
                valid,
                result_slots,
            )
            if not preflight_ready:
                stats.results = [
                    result for result in result_slots if result is not None
                ]
                return stats
            mutation_valid = [
                (request_index, selection.action)
                for request_index, selection in preflight_ready
            ]
            if not lifecycle_mutation_capability():
                for request_index, action in mutation_valid:
                    result_slots[request_index] = _resolution_result(
                        action,
                        status="failed",
                        reason="PLATFORM_UNSUPPORTED",
                    )
                stats.results = [
                    result for result in result_slots if result is not None
                ]
                return stats
            with _locked_pending_queue(self._brain_dir()):
                store = ItemsStore(self._brain_dir() / "items")
                with store.locked_catalog():
                    return self._apply_resolutions_locked(
                        valid=mutation_valid,
                        result_slots=result_slots,
                        requested_count=len(requested),
                        store=store,
                        gc_orphan_locks=gc_orphan_locks,
                    )

        preview, ready_selections = self._plan_resolutions(valid, result_slots)
        if preview is None:
            stats.results = [result for result in result_slots if result is not None]
            return stats
        duplicate_selections = [
            indexed
            for indexed in ready_selections
            if indexed[1].action.action == "accept_duplicate"
        ]
        if duplicate_selections:
            fresh_catalog = _scan_existing_item_metadata(self._brain_dir() / "items")
            for result_index, selection in duplicate_selections:
                target_id = cast(str, selection.action.target)
                current_target = fresh_catalog.items.get(target_id)
                if (
                    not fresh_catalog.trusted
                    or current_target is None
                    or _domain_digest(
                        "amh.pending.resolution.target.v1",
                        current_target.model_dump(mode="json", exclude_none=False),
                    )
                    != selection.target_digest
                ):
                    result_slots[result_index] = _resolution_result(
                        selection.action,
                        preview=selection.preview,
                        status="blocked",
                        reason="PENDING_RESOLUTION_CHANGED",
                    )
        assert len(ready_selections) <= len(valid)
        stats.results = [result for result in result_slots if result is not None]
        return stats

    def _plan_resolutions(
        self,
        valid: list[tuple[int, PendingResolutionAction]],
        result_slots: list[PendingResolutionResult | None],
        *,
        recovery_store: ItemsStore | None = None,
    ) -> tuple[PendingPreview | None, list[tuple[int, _PendingResolutionSelection]]]:
        snapshot = _pending_record_paths(self._brain_dir() / "pending")
        catalog = _scan_existing_item_metadata(self._brain_dir() / "items")
        preview = self._preview_from_snapshot(
            snapshot,
            limit=MAX_PENDING_QUEUE_ENTRIES + 1,
            item_catalog=catalog,
        )
        scan_reason = preview.reason
        if preview.truncated or preview.total > MAX_PENDING_QUEUE_ENTRIES:
            scan_reason = "PENDING_QUEUE_TRUNCATED"
        if preview.scan_unavailable or scan_reason == "PENDING_QUEUE_TRUNCATED":
            for request_index, action in valid:
                result_slots[request_index] = _resolution_result(
                    action,
                    classification="audit_blocked",
                    status="failed",
                    reason=scan_reason or "PENDING_SCAN_UNAVAILABLE",
                )
            return None, []

        by_id: dict[str, list[PendingRecordPreview]] = {}
        for record in preview.records:
            by_id.setdefault(record.record_id, []).append(record)
        ready: list[tuple[int, _PendingResolutionSelection]] = []
        planner_store = recovery_store
        items_dir = self._brain_dir() / "items"
        for request_index, action in valid:
            matches = by_id.get(action.record_id, [])
            if not matches:
                result_slots[request_index] = _resolution_result(
                    action,
                    status="failed",
                    reason="RECORD_ID_NOT_FOUND",
                )
            elif len(matches) != 1:
                result_slots[request_index] = _resolution_result(
                    action,
                    classification="conflict",
                    status="blocked",
                    reason="PENDING_RECORD_ID_CONFLICT",
                )
            else:
                stable_item_id = matches[0]._stable_item_id
                if (
                    planner_store is None
                    and action.action in {"approve_audit", "convert_type"}
                    and stable_item_id is not None
                    and stable_item_id in catalog.items
                    and items_dir.is_dir()
                ):
                    planner_store = ItemsStore(items_dir)
                selection, result = self._validate_resolution(
                    action,
                    matches[0],
                    catalog.items,
                    recovery_store=planner_store,
                )
                result_slots[request_index] = result
                if selection is not None:
                    ready.append((request_index, selection))
        return preview, ready

    def _apply_resolutions_locked(
        self,
        *,
        valid: list[tuple[int, PendingResolutionAction]],
        result_slots: list[PendingResolutionResult | None],
        requested_count: int,
        store: ItemsStore,
        gc_orphan_locks: bool,
    ) -> PendingResolutionStats:
        """Apply resolutions while queue and catalog locks are held."""

        stats = PendingResolutionStats(dry_run=False)
        preview, ready = self._plan_resolutions(
            valid,
            result_slots,
            recovery_store=store,
        )
        if preview is None or not ready:
            stats.results = [result for result in result_slots if result is not None]
            return stats

        try:
            prepared = prepare_pending_receipt(
                selection_mode="resolution",
                requested_count=requested_count,
                selected=[
                    _resolution_receipt_selection(selection)
                    for _request_index, selection in ready
                ],
                depth_before=preview.total,
            )
            append_pending_receipt(self._brain_dir(), prepared)
        except Exception:
            stats.governance_reason = "PENDING_RECEIPT_PREPARE_FAILED"
            for request_index, selection in ready:
                result_slots[request_index] = _resolution_result(
                    selection.action,
                    preview=selection.preview,
                    status="failed",
                    reason="PENDING_RECEIPT_PREPARE_FAILED",
                )
            stats.results = [result for result in result_slots if result is not None]
            return stats
        stats.receipt = prepared

        service = None
        close_warning: str | None = None
        try:
            for request_index, selection in ready:
                requires_service = selection.action.action in {
                    "approve_audit",
                    "convert_type",
                }
                if requires_service and service is None:
                    from agent_brain.memory.store.write_service import WriteService

                    try:
                        service = WriteService.for_brain(self._brain_dir())
                    except Exception:
                        result_slots[request_index] = _resolution_result(
                            selection.action,
                            preview=selection.preview,
                            status="failed",
                            reason="PENDING_WRITE_SERVICE_UNAVAILABLE",
                        )
                        continue
                result_slots[request_index] = self._apply_resolution(
                    selection,
                    store=store,
                    service=service if requires_service else None,
                )
        finally:
            if service is not None:
                try:
                    service.close()
                except Exception:
                    close_warning = "PENDING_WRITE_SERVICE_CLOSE_FAILED"

        if gc_orphan_locks:
            stats.lock_gc_report = self._collect_record_locks()
        if close_warning is not None:
            stats.governance_reason = close_warning
        stats.results = [result for result in result_slots if result is not None]
        batch_warnings = tuple(
            warning
            for warning in (
                close_warning,
                (
                    stats.lock_gc_report.reason
                    if stats.lock_gc_report is not None
                    else None
                ),
            )
            if warning is not None
        )
        try:
            completed = complete_pending_receipt(
                prepared,
                outcomes=[
                    _resolution_receipt_outcome(
                        cast(PendingResolutionResult, result_slots[index])
                    )
                    for index, _selection in ready
                ],
                depth_after=self.depth(),
                batch_warnings=batch_warnings,
            )
            append_pending_receipt(self._brain_dir(), completed)
            stats.receipt = completed
        except Exception:
            stats.receipt = incomplete_pending_receipt(prepared)
            stats.governance_reason = "PENDING_RECEIPT_COMPLETION_FAILED"
        return stats

    def _apply_resolution(
        self,
        selection: _PendingResolutionSelection,
        *,
        store: ItemsStore,
        service: object | None,
    ) -> PendingResolutionResult:
        action = selection.action
        preview = selection.preview
        path = Path(preview.path)
        expected_hash = preview._record_sha256
        expected_identity = preview._record_identity
        if expected_hash is None or expected_identity is None:
            return _resolution_result(
                action,
                preview=preview,
                status="blocked",
                reason="PENDING_RESOLUTION_CHANGED",
            )
        try:
            with _locked_pending_record(path):
                try:
                    raw, identity = _read_pending_record_snapshot(path)
                except (FileNotFoundError, _PendingReadError):
                    return _resolution_result(
                        action,
                        preview=preview,
                        status="blocked",
                        reason="PENDING_RESOLUTION_CHANGED",
                    )
                if (
                    identity != expected_identity
                    or hashlib.sha256(raw).hexdigest() != expected_hash
                ):
                    return _resolution_result(
                        action,
                        preview=preview,
                        status="blocked",
                        reason="PENDING_RESOLUTION_CHANGED",
                    )

                if selection.recovery:
                    return self._recover_resolution(
                        selection,
                        raw=raw,
                        expected_hash=expected_hash,
                        expected_identity=expected_identity,
                        store=store,
                        service=service,
                    )

                if action.action == "accept_duplicate":
                    result = self._apply_duplicate_resolution(
                        selection,
                        raw=raw,
                        store=store,
                    )
                    if result is not None:
                        return result
                    return _unlink_applied_resolution(
                        selection,
                        expected_hash=expected_hash,
                        expected_identity=expected_identity,
                    )

                if action.action == "approve_audit":
                    fresh, result = _preview_audit_resolution(action, preview, raw)
                    if fresh is None:
                        return result
                    if fresh.audit_digest != selection.audit_digest:
                        return _resolution_result(
                            action,
                            preview=preview,
                            status="blocked",
                            reason="PENDING_AUDIT_FINDINGS_CHANGED",
                        )
                    write_input = _pending_write_input(path, raw, preview)
                    allow_unsafe = True
                else:
                    fresh, result = _preview_conversion_resolution(action, preview, raw)
                    if fresh is None:
                        return result
                    if fresh.target_digest != selection.target_digest:
                        return _resolution_result(
                            action,
                            preview=preview,
                            status="blocked",
                            reason="PENDING_RESOLUTION_CHANGED",
                        )
                    converted = _converted_pending_write(preview, raw)
                    write_input = (
                        (converted[0], converted[1], False)
                        if converted is not None
                        else None
                    )
                    allow_unsafe = False
                if write_input is None or service is None:
                    return _resolution_result(
                        action,
                        preview=preview,
                        status="failed",
                        reason="PENDING_APPLY_FAILED",
                    )
                item, body, _queued_allow_unsafe = write_input
                write = getattr(service, "write")
                written = write(item=item, body=body, allow_unsafe=allow_unsafe)
                if written.status != "written":
                    return _resolution_result(
                        action,
                        preview=preview,
                        status="failed",
                        reason="PENDING_APPLY_FAILED",
                    )
                if "evidence-sidecar" in written.degraded:
                    return _resolution_result(
                        action,
                        preview=preview,
                        status="failed",
                        reason="EVIDENCE_SIDECAR_REPAIR_REQUIRED",
                        item_id=_result_item_id(preview, item.id),
                    )
                if "source-ledger" in written.degraded:
                    return _resolution_result(
                        action,
                        preview=preview,
                        status="failed",
                        reason="SOURCE_LEDGER_REPAIR_REQUIRED",
                        item_id=_result_item_id(preview, item.id),
                    )
                return _unlink_applied_resolution(
                    selection,
                    expected_hash=expected_hash,
                    expected_identity=expected_identity,
                    item_id=item.id,
                    index_repair_required="index" in written.degraded,
                )
        except Exception:
            return _resolution_result(
                action,
                preview=preview,
                status="failed",
                reason="PENDING_APPLY_FAILED",
            )

    @staticmethod
    def _recover_resolution(
        selection: _PendingResolutionSelection,
        *,
        raw: bytes,
        expected_hash: str,
        expected_identity: tuple[int, int],
        store: ItemsStore,
        service: object | None,
    ) -> PendingResolutionResult:
        action = selection.action
        preview = selection.preview
        intent, reason, item_id = _resolution_intent(action, preview, raw)
        if intent is None or item_id is None or service is None:
            return _resolution_result(
                action,
                preview=preview,
                status="blocked",
                reason=reason or "PENDING_RESOLUTION_CHANGED",
            )
        if (
            intent.audit_digest != selection.audit_digest
            or intent.target_digest != selection.target_digest
        ):
            return _resolution_result(
                action,
                preview=preview,
                status="blocked",
                reason="PENDING_RESOLUTION_CHANGED",
            )
        with store.locked_items([item_id]) as locked:
            try:
                existing, existing_body = locked.get(item_id)
            except FileNotFoundError:
                return _resolution_result(
                    action,
                    preview=preview,
                    status="blocked",
                    reason="PENDING_RESOLUTION_CHANGED",
                )
            if not _matches_resolution_existing(
                intent,
                existing,
                existing_body,
                brain=store.items_dir.parent,
            ):
                return _resolution_result(
                    action,
                    preview=preview,
                    status="blocked",
                    reason="PENDING_RESOLUTION_CHANGED",
                )
            reconcile = getattr(service, "reconcile_existing")
            repaired = reconcile(item=existing, body=existing_body)
            if "source-ledger" in repaired.degraded:
                return _resolution_result(
                    action,
                    preview=preview,
                    status="failed",
                    reason="SOURCE_LEDGER_REPAIR_REQUIRED",
                    item_id=_result_item_id(preview, item_id),
                )
            return _unlink_applied_resolution(
                selection,
                expected_hash=expected_hash,
                expected_identity=expected_identity,
                item_id=item_id,
                index_repair_required="index" in repaired.degraded,
            )

    @staticmethod
    def _apply_duplicate_resolution(
        selection: _PendingResolutionSelection,
        *,
        raw: bytes,
        store: ItemsStore,
    ) -> PendingResolutionResult | None:
        action = selection.action
        preview = selection.preview
        target_id = cast(str, action.target)
        rebuilt = _pending_write_input(Path(preview.path), raw, preview)
        if rebuilt is None:
            return _resolution_result(
                action,
                preview=preview,
                status="blocked",
                reason="PENDING_RESOLUTION_CHANGED",
            )
        try:
            target, _body = store.get_nofollow(target_id)
        except (FileNotFoundError, OSError, UnicodeError, ValueError):
            return _resolution_result(
                action,
                preview=preview,
                status="blocked",
                reason="PENDING_DUPLICATE_TARGET_MISMATCH",
            )
        if (
            not _matches_duplicate_target(rebuilt[0], target)
            or _domain_digest(
                "amh.pending.resolution.target.v1",
                target.model_dump(mode="json", exclude_none=False),
            )
            != selection.target_digest
        ):
            return _resolution_result(
                action,
                preview=preview,
                status="blocked",
                reason="PENDING_DUPLICATE_TARGET_MISMATCH",
            )
        return None

    def _collect_orphan_locks_unlocked(
        self,
        *,
        apply: bool,
    ) -> PendingLockGcReport:
        remaining = _pending_record_paths(
            self._brain_dir() / "pending",
            entry_cap=MAX_PENDING_QUEUE_ENTRIES,
        )
        if remaining.scan_unavailable or remaining.total > len(remaining.paths):
            return PendingLockGcReport(
                truncated=True,
                reason="PENDING_LOCK_GC_TRUNCATED",
            )
        try:
            return collect_pending_record_locks(
                self._brain_dir() / "pending",
                live_record_names={path.name for path in remaining.paths},
                apply=apply,
                limit=MAX_PENDING_QUEUE_ENTRIES,
            )
        except Exception:
            return PendingLockGcReport(reason="PENDING_LOCK_GC_UNAVAILABLE")

    def _collect_record_locks(self) -> PendingLockGcReport:
        return self._collect_orphan_locks_unlocked(apply=True)

    def _validate_resolution(
        self,
        action: PendingResolutionAction,
        preview: PendingRecordPreview,
        existing_items: Mapping[str, MemoryItem],
        *,
        recovery_store: ItemsStore | None = None,
    ) -> tuple[_PendingResolutionSelection | None, PendingResolutionResult]:
        expected_hash = preview._record_sha256
        expected_identity = preview._record_identity
        if expected_hash is None or expected_identity is None:
            return _resolution_blocked(action, preview, "PENDING_RESOLUTION_CHANGED")
        try:
            raw, identity = _read_pending_record_snapshot(Path(preview.path))
        except (FileNotFoundError, _PendingReadError):
            return _resolution_blocked(action, preview, "PENDING_RESOLUTION_CHANGED")
        if identity != expected_identity or hashlib.sha256(raw).hexdigest() != expected_hash:
            return _resolution_blocked(action, preview, "PENDING_RESOLUTION_CHANGED")
        if recovery_store is not None:
            recovery = _preview_recovery_resolution(
                action,
                preview,
                raw,
                existing_items=existing_items,
                store=recovery_store,
            )
            if recovery is not None:
                return recovery
        if preview.classification == "conflict":
            return _resolution_blocked(action, preview, preview.reason)

        if action.action == "approve_audit":
            return _preview_audit_resolution(action, preview, raw)
        if action.action == "accept_duplicate":
            return _preview_duplicate_resolution(
                action,
                preview,
                raw,
                existing_items,
            )
        return _preview_conversion_resolution(action, preview, raw)

    def replay(
        self,
        *,
        record_ids: Iterable[str] | None = None,
        safe_only: bool = False,
    ) -> PendingApplyStats:
        """Compatibility wrapper for explicit apply; no arguments never mutate."""

        return self.apply(record_ids=record_ids, safe_only=safe_only)

    def apply(
        self,
        *,
        record_ids: Iterable[str] | None = None,
        safe_only: bool = False,
    ) -> PendingApplyStats:
        """Apply explicitly selected records after a complete trusted preview."""

        requested = list(record_ids or [])
        if not requested and not safe_only:
            return PendingApplyStats()
        if not lifecycle_mutation_capability():
            stats = PendingApplyStats()
            for record_id in requested or ["*"]:
                stats.add(
                    PendingApplyResult(
                        record_id=record_id,
                        classification="audit_blocked",
                        status="failed",
                        reason="PLATFORM_UNSUPPORTED",
                    )
                )
            return stats

        with _locked_pending_queue(self._brain_dir()):
            return self._apply_locked(requested=requested, safe_only=safe_only)

    def _apply_locked(
        self,
        *,
        requested: list[str],
        safe_only: bool,
    ) -> PendingApplyStats:
        """Acquire queue -> catalog before any fresh classification or mutation."""

        store = ItemsStore(self._brain_dir() / "items")
        with store.locked_catalog():
            return self._apply_catalog_locked(requested=requested, safe_only=safe_only)

    def _apply_catalog_locked(
        self,
        *,
        requested: list[str],
        safe_only: bool,
    ) -> PendingApplyStats:
        """Apply from global truth while queue and catalog locks are held."""

        stats = PendingApplyStats()

        preview = self.preview(limit=MAX_PENDING_QUEUE_ENTRIES + 1)
        scan_reason = preview.reason
        if preview.truncated and preview.total > MAX_PENDING_QUEUE_ENTRIES:
            scan_reason = "PENDING_QUEUE_TRUNCATED"
        if preview.scan_unavailable or scan_reason == "PENDING_QUEUE_TRUNCATED":
            identifiers = requested or ["*"]
            seen: set[str] = set()
            for record_id in identifiers:
                if record_id in seen:
                    stats.add(_duplicate_selection_result(record_id))
                    continue
                seen.add(record_id)
                stats.add(
                    PendingApplyResult(
                        record_id=record_id,
                        classification="audit_blocked",
                        status="failed",
                        reason=scan_reason or "PENDING_SCAN_UNAVAILABLE",
                    )
                )
            return stats

        by_id: dict[str, list[PendingRecordPreview]] = {}
        for record in preview.records:
            by_id.setdefault(record.record_id, []).append(record)

        selected: list[PendingRecordPreview | PendingApplyResult] = []
        if requested:
            seen = set()
            for record_id in requested:
                if record_id in seen:
                    selected.append(_duplicate_selection_result(record_id))
                    continue
                seen.add(record_id)
                matches = by_id.get(record_id, [])
                if not matches:
                    selected.append(
                        PendingApplyResult(
                            record_id=record_id,
                            classification=None,
                            status="skipped",
                            reason="RECORD_ID_NOT_FOUND",
                        )
                    )
                    continue
                if len(matches) != 1:
                    selected.append(
                        PendingApplyResult(
                            record_id=record_id,
                            classification="conflict",
                            status="review_required",
                            reason="PENDING_RECORD_ID_CONFLICT",
                        )
                    )
                    continue
                selected.append(matches[0])
        else:
            selected = list(preview.records)

        selection_mode: Literal["explicit", "safe_only"] = (
            "explicit" if requested else "safe_only"
        )
        receipt_selections = [
            _pending_receipt_selection(candidate)
            for candidate in selected
            if isinstance(candidate, PendingRecordPreview)
        ]
        try:
            prepared_receipt = prepare_pending_receipt(
                selection_mode=selection_mode,
                requested_count=len(requested) if requested else len(preview.records),
                selected=receipt_selections,
                depth_before=preview.total,
            )
            append_pending_receipt(self._brain_dir(), prepared_receipt)
        except Exception:
            stats.governance_reason = "PENDING_RECEIPT_PREPARE_FAILED"
            for candidate in selected:
                if isinstance(candidate, PendingRecordPreview):
                    stats.add(
                        _failed_apply_result(
                            candidate,
                            "PENDING_RECEIPT_PREPARE_FAILED",
                        )
                    )
                else:
                    stats.add(
                        PendingApplyResult(
                            record_id=candidate.record_id,
                            classification=candidate.classification,
                            status="failed",
                            reason="PENDING_RECEIPT_PREPARE_FAILED",
                        )
                    )
            return stats
        stats.receipt = prepared_receipt

        service = None
        try:
            for apply_candidate in selected:
                if isinstance(apply_candidate, PendingApplyResult):
                    stats.add(apply_candidate)
                    continue
                if safe_only and apply_candidate.classification not in {
                    "ready",
                    "already_written",
                }:
                    stats.add(_review_required_result(apply_candidate))
                    continue
                if apply_candidate.classification not in {"ready", "already_written"}:
                    stats.add(_review_required_result(apply_candidate))
                    continue
                if service is None:
                    from agent_brain.memory.store.write_service import WriteService

                    try:
                        service = WriteService.for_brain(self._brain_dir())
                    except Exception:
                        stats.add(
                            _failed_apply_result(
                                apply_candidate,
                                "PENDING_WRITE_SERVICE_UNAVAILABLE",
                            )
                        )
                        continue
                stats.add(self._apply_record(apply_candidate, service=service))
        finally:
            if service is not None:
                service.close()
        stats.lock_gc_report = self._collect_record_locks()
        batch_warnings = (
            (stats.lock_gc_report.reason,)
            if stats.lock_gc_report is not None and stats.lock_gc_report.reason is not None
            else ()
        )
        try:
            completed_receipt = complete_pending_receipt(
                prepared_receipt,
                outcomes=[
                    PendingReceiptOutcome(
                        record_id=result.record_id,
                        status=result.status,
                        classification=result.classification,
                        reason=result.reason,
                        index_repair_required=result.index_repair_required,
                        warnings=result.warnings,
                    )
                    for result in stats.results
                ],
                depth_after=self.depth(),
                batch_warnings=batch_warnings,
            )
            append_pending_receipt(self._brain_dir(), completed_receipt)
            stats.receipt = completed_receipt
        except Exception:
            stats.receipt = incomplete_pending_receipt(prepared_receipt)
            stats.governance_reason = "PENDING_RECEIPT_COMPLETION_FAILED"
        return stats

    def _apply_record(self, record: PendingRecordPreview, *, service: object) -> PendingApplyResult:
        path = Path(record.path)
        expected_hash = record._record_sha256
        expected_identity = record._record_identity
        item_id = record._stable_item_id
        if expected_hash is None or expected_identity is None or item_id is None:
            return _review_required_result(record)
        try:
            with _locked_pending_record(path):
                try:
                    raw, current_identity = _read_pending_record_snapshot(path)
                except _PendingReadError:
                    return _failed_apply_result(record, "PENDING_RECORD_READ_FAILED")
                except FileNotFoundError:
                    raw = None
                    current_identity = None
                if raw is not None and current_identity != expected_identity:
                    return _failed_apply_result(record, "CONCURRENT_MODIFICATION")
                if raw is not None and hashlib.sha256(raw).hexdigest() != expected_hash:
                    return _failed_apply_result(record, "PENDING_RECORD_CHANGED")

                write_input = _pending_write_input(path, raw, record) if raw is not None else None
                pending_item = write_input[0] if write_input is not None else None
                if raw is not None and (pending_item is None or write_input is None):
                    return _failed_apply_result(record, "PENDING_RECORD_CHANGED")

                store = ItemsStore(self._brain_dir() / "items")
                with store.locked_items([item_id]) as locked:
                    try:
                        existing, existing_body = locked.get(item_id)
                    except FileNotFoundError:
                        existing = None
                    if existing is not None:
                        if (
                            pending_item is None or _same_scope(existing, pending_item)
                        ) and existing.source.span_hash == record.payload_sha256:
                            reconcile = getattr(service, "reconcile_existing")
                            reconciliation = reconcile(item=existing, body=existing_body)
                            if "source-ledger" in reconciliation.degraded:
                                return _failed_apply_result(
                                    record,
                                    "SOURCE_LEDGER_REPAIR_REQUIRED",
                                    item_id=item_id,
                                )
                            index_repair_required = "index" in reconciliation.degraded
                            unlink_warnings: tuple[str, ...] = ()
                            if raw is not None:
                                try:
                                    unlink_warnings = _unlink_pending_record(
                                        path, expected_hash, expected_identity
                                    )
                                except OSError:
                                    return _failed_apply_result(
                                        record, "PENDING_UNLINK_FAILED", item_id=item_id
                                    )
                            return PendingApplyResult(
                                record_id=record.record_id,
                                classification="already_written",
                                status="already_written",
                                reason=(
                                    "STABLE_ITEM_ALREADY_WRITTEN_INDEX_REPAIR_REQUIRED"
                                    if index_repair_required
                                    else "STABLE_ITEM_ALREADY_WRITTEN"
                                ),
                                item_id=_result_item_id(record, item_id),
                                index_repair_required=index_repair_required,
                                warnings=unlink_warnings,
                            )
                        return PendingApplyResult(
                            record_id=record.record_id,
                            classification="conflict",
                            status="review_required",
                            reason="STABLE_ITEM_PAYLOAD_CONFLICT",
                            item_id=_result_item_id(record, item_id),
                        )
                    if raw is None:
                        return _failed_apply_result(record, "PENDING_RECORD_DISAPPEARED")
                    assert write_input is not None and pending_item is not None
                    item, body, allow_unsafe = write_input
                    write = getattr(service, "write")
                    result = write(item=item, body=body, allow_unsafe=allow_unsafe)
                    if result.status != "written":
                        return PendingApplyResult(
                            record_id=record.record_id,
                            classification="audit_blocked",
                            status="review_required",
                            reason="AUDIT_BLOCKED",
                            item_id=_result_item_id(record, item_id),
                        )
                    if "evidence-sidecar" in result.degraded:
                        return _failed_apply_result(
                            record,
                            "EVIDENCE_SIDECAR_REPAIR_REQUIRED",
                            item_id=item_id,
                        )
                    if "source-ledger" in result.degraded:
                        return _failed_apply_result(
                            record, "SOURCE_LEDGER_REPAIR_REQUIRED", item_id=item_id
                        )
                    try:
                        unlink_warnings = _unlink_pending_record(
                            path, expected_hash, expected_identity
                        )
                    except OSError:
                        return _failed_apply_result(
                            record, "PENDING_UNLINK_FAILED", item_id=item_id
                        )
                    return PendingApplyResult(
                        record_id=record.record_id,
                        classification="ready",
                        status="written",
                        reason=(
                            "WRITTEN_INDEX_REPAIR_REQUIRED"
                            if "index" in result.degraded
                            else "WRITTEN"
                        ),
                        item_id=_result_item_id(record, item_id),
                        index_repair_required="index" in result.degraded,
                        warnings=unlink_warnings,
                    )
        except Exception:
            return _failed_apply_result(record, "PENDING_APPLY_FAILED", item_id=item_id)

    def _preview_record(
        self,
        path: Path,
        *,
        existing_items: Mapping[str, MemoryItem],
        metadata_trusted: bool,
        deadline_at: float | None = None,
    ) -> PendingRecordPreview:
        raw: bytes | None = None
        try:
            raw, identity = _read_pending_record_snapshot(
                path,
                deadline_at=deadline_at,
            )
            line = raw.decode("utf-8").strip().splitlines()[0]
            rec = json.loads(line, parse_constant=_reject_json_constant)
            if not isinstance(rec, dict):
                return _malformed_preview(path, "PENDING_RECORD_NOT_OBJECT", raw=raw)
            item = rec.get("item")
            if not isinstance(item, dict):
                return _malformed_preview(path, "INVALID_ITEM_PAYLOAD", raw=raw)
            preview = _classify_pending_record(
                path=path,
                record=rec,
                item=item,
                existing_items=existing_items,
                metadata_trusted=metadata_trusted,
            )
            return replace(
                preview,
                _record_sha256=hashlib.sha256(raw).hexdigest(),
                _record_identity=identity,
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


_RESOLUTION_ACTIONS = frozenset(
    {"approve_audit", "accept_duplicate", "convert_type"}
)
_RESOLUTION_STATUSES = frozenset({"ready", "applied", "blocked", "failed"})
_RESOLUTION_CLASSIFICATIONS = frozenset(
    {
        "ready",
        "already_written",
        "stale_requires_review",
        "duplicate_candidate",
        "conflict",
        "unsupported_type",
        "malformed",
        "audit_blocked",
    }
)
_DECISION_CONVERSION_REASON = (
    "该内容来自旧版 feedback 中已确认的长期约束，迁移为 decision 后才能进入统一记忆模型。"
)
_DECISION_CONVERSION_COST = (
    "恢复为不受支持的 feedback 会让该约束再次滞留在 pending，无法被正常维护和召回。"
)


def _valid_resolution_action_structure(action: PendingResolutionAction) -> bool:
    return (
        type(action.action) is str
        and action.action in _RESOLUTION_ACTIONS
        and type(action.record_id) is str
        and _RECORD_ID_PATTERN.fullmatch(action.record_id) is not None
    )


def _resolution_target_key(target: object) -> tuple[str, str]:
    if target is None:
        return "none", ""
    if type(target) is str:
        return "string", target
    return "invalid", ""


def _resolution_target_rejection_reason(
    action: PendingResolutionAction,
) -> str | None:
    if action.action == "approve_audit":
        return None if action.target is None else "PENDING_RESOLUTION_NOT_APPLICABLE"
    if action.action == "accept_duplicate":
        if type(action.target) is str and is_valid_memory_item_id(action.target):
            return None
        return "PENDING_DUPLICATE_TARGET_MISMATCH"
    if type(action.target) is str and action.target == "decision":
        return None
    return "PENDING_CONVERSION_UNSUPPORTED"


def _public_resolution_action(value: object) -> PendingResolutionPublicName:
    if type(value) is str and value in _RESOLUTION_ACTIONS:
        return cast(PendingResolutionName, value)
    return "unknown"


def _public_resolution_status(value: object) -> PendingResolutionPublicStatus:
    if type(value) is str and value in _RESOLUTION_STATUSES:
        return cast(PendingResolutionStatus, value)
    return "unknown"


def _public_resolution_classification(
    value: object,
) -> PendingResolutionPublicClassification:
    if value is None:
        return None
    if type(value) is str and value in _RESOLUTION_CLASSIFICATIONS:
        return cast(PendingClassification, value)
    return "unknown"


def _invalid_resolution_result(
    *,
    reason: str = "PENDING_RESOLUTION_NOT_APPLICABLE",
) -> PendingResolutionResult:
    return PendingResolutionResult(
        action="unknown",
        record_id="",
        classification=None,
        status="blocked",
        reason=reason,
    )


def _resolution_result(
    action: PendingResolutionAction,
    *,
    preview: PendingRecordPreview | None = None,
    classification: PendingClassification | None = None,
    status: PendingResolutionStatus,
    reason: str,
    item_id: str | None = None,
) -> PendingResolutionResult:
    return PendingResolutionResult(
        action=_public_resolution_action(action.action),
        record_id=action.record_id if type(action.record_id) is str else "",
        classification=(
            preview.classification if preview is not None else classification
        ),
        status=status,
        reason=reason,
        target=action.target if type(action.target) is str else None,
        item_id=item_id,
    )


def _resolution_ready(
    action: PendingResolutionAction,
    preview: PendingRecordPreview,
    *,
    item_id: str | None = None,
) -> PendingResolutionResult:
    return _resolution_result(
        action,
        preview=preview,
        status="ready",
        reason="PENDING_RESOLUTION_READY",
        item_id=_result_item_id(preview, item_id),
    )


def _resolution_blocked(
    action: PendingResolutionAction,
    preview: PendingRecordPreview,
    reason: str,
) -> tuple[None, PendingResolutionResult]:
    return None, _resolution_result(
        action,
        preview=preview,
        status="blocked",
        reason=reason,
    )


def _audit_report(text: str) -> AuditReport:
    from agent_brain.memory.governance.audit.scanner import audit_memory_text

    return audit_memory_text(text)


def _domain_digest(domain: str, value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(domain.encode("utf-8") + b"\0" + encoded).hexdigest()


def _audit_findings_digest(report: AuditReport) -> str:
    rows = sorted(
        (finding.rule_id, finding.severity, finding.category)
        for finding in report.findings
    )
    return _domain_digest("amh.pending.audit-findings.v1", rows)


def _preview_audit_resolution(
    action: PendingResolutionAction,
    preview: PendingRecordPreview,
    raw: bytes,
) -> tuple[_PendingResolutionSelection | None, PendingResolutionResult]:
    if (
        action.target is not None
        or preview.classification != "audit_blocked"
        or preview.reason != "AUDIT_BLOCKED"
    ):
        return _resolution_blocked(
            action, preview, "PENDING_RESOLUTION_NOT_APPLICABLE"
        )
    intent, reason, item_id = _resolution_intent(action, preview, raw)
    if intent is None:
        return _resolution_blocked(
            action,
            preview,
            reason or "PENDING_RESOLUTION_CHANGED",
        )
    return (
        _PendingResolutionSelection(
            action=action,
            preview=preview,
            audit_digest=intent.audit_digest,
        ),
        _resolution_ready(action, preview, item_id=item_id),
    )


def _preview_duplicate_resolution(
    action: PendingResolutionAction,
    preview: PendingRecordPreview,
    raw: bytes,
    existing_items: Mapping[str, MemoryItem],
) -> tuple[_PendingResolutionSelection | None, PendingResolutionResult]:
    if (
        preview.classification != "duplicate_candidate"
        or preview.reason != "SAME_SCOPE_METADATA_DUPLICATE"
    ):
        return _resolution_blocked(
            action, preview, "PENDING_RESOLUTION_NOT_APPLICABLE"
        )
    if not is_valid_memory_item_id(action.target):
        return _resolution_blocked(
            action, preview, "PENDING_DUPLICATE_TARGET_MISMATCH"
        )
    rebuilt = _pending_write_input(Path(preview.path), raw, preview)
    if rebuilt is None:
        return _resolution_blocked(action, preview, "PENDING_RESOLUTION_CHANGED")
    item, _body, _allow_unsafe = rebuilt
    target = existing_items.get(cast(str, action.target))
    if target is None or not _matches_duplicate_target(item, target):
        return _resolution_blocked(
            action, preview, "PENDING_DUPLICATE_TARGET_MISMATCH"
        )
    target_digest = _domain_digest(
        "amh.pending.resolution.target.v1",
        target.model_dump(mode="json", exclude_none=False),
    )
    return (
        _PendingResolutionSelection(
            action=action,
            preview=preview,
            target_digest=target_digest,
        ),
        _resolution_ready(action, preview, item_id=item.id),
    )


def _preview_recovery_resolution(
    action: PendingResolutionAction,
    preview: PendingRecordPreview,
    raw: bytes,
    *,
    existing_items: Mapping[str, MemoryItem],
    store: ItemsStore,
) -> tuple[_PendingResolutionSelection | None, PendingResolutionResult] | None:
    if (
        preview._stable_item_id is None
        or preview._stable_item_id not in existing_items
    ):
        return None
    intent, reason, item_id = _resolution_intent(action, preview, raw)
    if item_id is None or item_id not in existing_items:
        return None
    if intent is None:
        return _resolution_blocked(
            action,
            preview,
            reason or "PENDING_RESOLUTION_CHANGED",
        )
    try:
        existing, existing_body = store.get_nofollow(item_id)
    except (FileNotFoundError, OSError, UnicodeError, ValueError):
        return _resolution_blocked(action, preview, "PENDING_RESOLUTION_CHANGED")
    if not _matches_resolution_existing(
        intent,
        existing,
        existing_body,
        brain=store.items_dir.parent,
    ):
        return _resolution_blocked(action, preview, "PENDING_RESOLUTION_CHANGED")
    return (
        _PendingResolutionSelection(
            action=action,
            preview=preview,
            audit_digest=intent.audit_digest,
            target_digest=intent.target_digest,
            recovery=True,
        ),
        _resolution_ready(action, preview, item_id=item_id),
    )


def _resolution_intent(
    action: PendingResolutionAction,
    preview: PendingRecordPreview,
    raw: bytes,
) -> tuple[_PendingResolutionIntent | None, str | None, str | None]:
    from agent_brain.memory.store.write_service import (
        _write_evidence_boundary_valid,
    )

    if action.action == "approve_audit":
        rebuilt = _pending_write_input(Path(preview.path), raw, preview)
        if rebuilt is None:
            return None, "PENDING_RESOLUTION_CHANGED", preview._stable_item_id
        item, body, _allow_unsafe = rebuilt
        if str(item.sensitivity) not in {"public", "internal"}:
            return None, "PENDING_AUDIT_APPROVAL_REQUIRED", item.id
        if not _write_evidence_boundary_valid(item, body):
            return None, "PENDING_WRITE_EVIDENCE_INVALID", item.id
        try:
            report = _audit_report("\n".join((item.title, item.summary, body)))
        except Exception:
            return None, "PENDING_AUDIT_FINDINGS_CHANGED", item.id
        if any(finding.category == "secrets" for finding in report.findings):
            return None, "PENDING_AUDIT_SECRET_BLOCKED", item.id
        if report.passed:
            return None, "PENDING_RESOLUTION_CHANGED", item.id
        digest = _audit_findings_digest(report)
        return _PendingResolutionIntent(item, body, audit_digest=digest), None, item.id

    if action.action != "convert_type":
        return (
            None,
            "PENDING_RESOLUTION_NOT_APPLICABLE",
            preview._stable_item_id,
        )
    parsed = _pending_record_payload(Path(preview.path), raw, preview)
    if parsed is None:
        return None, "PENDING_CONVERSION_INVALID", preview._stable_item_id
    if parsed[0].get("type") != "feedback":
        return None, "PENDING_CONVERSION_UNSUPPORTED", preview._stable_item_id
    converted = _converted_pending_write(preview, raw, parsed=parsed)
    if converted is None:
        return None, "PENDING_CONVERSION_INVALID", preview._stable_item_id
    item, body = converted
    if not _write_evidence_boundary_valid(item, body):
        return None, "PENDING_WRITE_EVIDENCE_INVALID", item.id
    try:
        conversion_report = _audit_report(
            "\n".join((item.title, item.summary, body))
        )
    except Exception:
        conversion_report = None
    if conversion_report is None or not conversion_report.passed:
        return None, "PENDING_CONVERSION_INVALID", item.id
    return (
        _PendingResolutionIntent(
            item,
            body,
            target_digest=_domain_digest(
                "amh.pending.resolution.target.v1",
                "decision",
            ),
        ),
        None,
        item.id,
    )


def _matches_resolution_existing(
    intent: _PendingResolutionIntent,
    existing: MemoryItem,
    existing_body: str,
    *,
    brain: Path,
) -> bool:
    if not _same_scope(intent.item, existing):
        return False
    from agent_brain.memory.store.write_service import _matches_existing_write

    return _matches_existing_write(
        intent.item,
        intent.body,
        existing,
        existing_body,
        brain=brain,
        now=_utc_now(),
    )


def _matches_duplicate_target(
    item: MemoryItem,
    target: MemoryItem | None,
) -> bool:
    return target is not None and (
        _same_scope(item, target)
        and str(item.type) == str(target.type)
        and item.title.strip().lower() == target.title.strip().lower()
        and item.summary.strip().lower() == target.summary.strip().lower()
    )


def _preview_conversion_resolution(
    action: PendingResolutionAction,
    preview: PendingRecordPreview,
    raw: bytes,
) -> tuple[_PendingResolutionSelection | None, PendingResolutionResult]:
    if (
        preview.classification != "unsupported_type"
        or preview.reason != "UNSUPPORTED_MEMORY_TYPE"
    ):
        return _resolution_blocked(
            action, preview, "PENDING_RESOLUTION_NOT_APPLICABLE"
        )
    if action.target != "decision":
        return _resolution_blocked(action, preview, "PENDING_CONVERSION_UNSUPPORTED")
    intent, reason, item_id = _resolution_intent(action, preview, raw)
    if intent is None:
        return _resolution_blocked(
            action,
            preview,
            reason or "PENDING_CONVERSION_INVALID",
        )
    return (
        _PendingResolutionSelection(
            action=action,
            preview=preview,
            target_digest=intent.target_digest,
        ),
        _resolution_ready(action, preview, item_id=item_id),
    )


def _converted_pending_write(
    preview: PendingRecordPreview,
    raw: bytes,
    *,
    parsed: tuple[dict[str, object], int, datetime, str] | None = None,
) -> tuple[MemoryItem, str] | None:
    if parsed is None:
        parsed = _pending_record_payload(Path(preview.path), raw, preview)
    if parsed is None:
        return None
    item, version, original_created_at, payload_sha256 = parsed
    body = item.get("body", "")
    if (
        item.get("type") != "feedback"
        or not isinstance(body, str)
        or preview._stable_item_id is None
    ):
        return None
    converted_body = (
        f"**决策**\n\n{body.strip()}\n\n"
        f"**理由**\n\n{_DECISION_CONVERSION_REASON}\n\n"
        f"**改回去的代价**\n\n{_DECISION_CONVERSION_COST}"
    )
    converted = dict(item)
    converted["type"] = "decision"
    converted["body"] = converted_body
    validated, reason = _validate_pending_item(
        item=converted,
        version=version,
        stable_item_id=preview._stable_item_id,
        original_created_at=original_created_at,
        payload_sha256=payload_sha256,
    )
    if validated is None or reason is not None:
        return None
    return validated, converted_body


def _resolution_receipt_selection(
    selection: _PendingResolutionSelection,
) -> PendingReceiptSelection:
    preview = selection.preview
    binding = preview._record_sha256 or preview.payload_sha256
    if binding is None or re.fullmatch(r"[0-9a-f]{64}", binding) is None:
        raise TypeError("INVALID_PENDING_RESOLUTION_BINDING")
    target = selection.action.target
    target_digest = (
        hashlib.sha256(
            b"amh.pending.resolution.target.v1\0" + target.encode("utf-8")
        ).hexdigest()
        if target is not None
        else None
    )
    return PendingReceiptSelection(
        record_id=selection.action.record_id,
        payload_sha256=binding,
        action=selection.action.action,
        target_digest=target_digest,
    )


def _resolution_receipt_outcome(
    result: PendingResolutionResult,
) -> PendingReceiptOutcome:
    return PendingReceiptOutcome(
        record_id=result.record_id,
        status=result.status,
        classification=result.classification,
        reason=result.reason,
        index_repair_required=result.index_repair_required,
        warnings=result.warnings,
    )


def _unlink_applied_resolution(
    selection: _PendingResolutionSelection,
    *,
    expected_hash: str,
    expected_identity: tuple[int, int],
    item_id: str | None = None,
    index_repair_required: bool = False,
) -> PendingResolutionResult:
    try:
        warnings = _unlink_pending_record(
            Path(selection.preview.path),
            expected_hash,
            expected_identity,
        )
    except OSError:
        return _resolution_result(
            selection.action,
            preview=selection.preview,
            status="failed",
            reason="PENDING_UNLINK_FAILED",
            item_id=_result_item_id(selection.preview, item_id),
        )
    return PendingResolutionResult(
        action=selection.action.action,
        record_id=selection.action.record_id,
        classification=selection.preview.classification,
        status="applied",
        reason="PENDING_RESOLUTION_APPLIED",
        target=selection.action.target,
        item_id=_result_item_id(
            selection.preview,
            item_id or selection.preview._stable_item_id,
        ),
        index_repair_required=index_repair_required,
        warnings=warnings,
    )


def _pending_receipt_selection(record: PendingRecordPreview) -> PendingReceiptSelection:
    binding = record._record_sha256 or record.payload_sha256
    if binding is None or re.fullmatch(r"[0-9a-f]{64}", binding) is None:
        binding = hashlib.sha256(
            f"unverified\0{record.record_id}\0{record.reason}".encode("utf-8")
        ).hexdigest()
    return PendingReceiptSelection(
        record_id=record.record_id,
        payload_sha256=binding,
    )


def _duplicate_selection_result(record_id: str) -> PendingApplyResult:
    return PendingApplyResult(
        record_id=record_id,
        classification=None,
        status="skipped",
        reason="DUPLICATE_RECORD_ID_SELECTION",
    )


def _review_required_result(record: PendingRecordPreview) -> PendingApplyResult:
    return PendingApplyResult(
        record_id=record.record_id,
        classification=record.classification,
        status="review_required",
        reason=record.reason,
        item_id=_result_item_id(record, record._stable_item_id),
    )


def _failed_apply_result(
    record: PendingRecordPreview,
    reason: str,
    *,
    item_id: str | None = None,
) -> PendingApplyResult:
    return PendingApplyResult(
        record_id=record.record_id,
        classification=record.classification,
        status="failed",
        reason=reason,
        item_id=_result_item_id(record, item_id or record._stable_item_id),
    )


def _result_item_id(record: PendingRecordPreview, item_id: str | None) -> str | None:
    return item_id if record.sensitivity in {"public", "internal"} else None


def _acquire_queue_file_lock(descriptor: int) -> str:
    if os.name == "nt":
        import msvcrt

        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        getattr(msvcrt, "locking")(descriptor, getattr(msvcrt, "LK_LOCK"), 1)
        return "msvcrt"
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_EX)
    return "fcntl"


def _release_queue_file_lock(descriptor: int, lock_kind: str) -> None:
    if lock_kind == "msvcrt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        getattr(msvcrt, "locking")(descriptor, getattr(msvcrt, "LK_UNLCK"), 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextmanager
def _locked_index_dirty(brain: Path) -> Iterator[None]:
    """Serialize marker read/append/rewrite: process lock, then runtime file lock."""

    root = Path(brain).expanduser().resolve(strict=False)
    key = str(root)
    with _INDEX_DIRTY_LOCKS_GUARD:
        process_lock = _INDEX_DIRTY_LOCKS.setdefault(key, threading.RLock())
    process_lock.acquire()
    descriptor = -1
    lock_kind: str | None = None
    try:
        if secure_dir_fd_io_supported():
            root_descriptor = _open_or_create_secure_directory(root)
            try:
                with SecureDirectory(root_descriptor) as directory:
                    root_descriptor = -1
                    with directory.child("runtime", create=True) as runtime:
                        descriptor, created = runtime.open_or_create_file(
                            "index-dirty.lock", os.O_RDWR
                        )
                        os.fchmod(descriptor, 0o600)
                        if created:
                            runtime.fsync()
                        lock_kind = _acquire_queue_file_lock(descriptor)
                        yield
            finally:
                if root_descriptor >= 0:
                    close_descriptor(root_descriptor)
        else:
            runtime_path = root / "runtime"
            _ensure_fallback_directory(runtime_path)
            lock_path = runtime_path / "index-dirty.lock"
            try:
                descriptor = os.open(
                    lock_path,
                    os.O_RDWR
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_BINARY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
                _fsync_fallback_directory(runtime_path)
            except FileExistsError:
                descriptor = os.open(
                    lock_path,
                    os.O_RDWR
                    | getattr(os, "O_BINARY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                )
            before = os.lstat(lock_path)
            opened = os.fstat(descriptor)
            if (
                not _is_safe_regular_file(before)
                or not _is_safe_regular_file(opened)
                or not _same_file_identity(before, opened)
            ):
                raise OSError("UNSAFE_INDEX_DIRTY_LOCK")
            lock_kind = _acquire_queue_file_lock(descriptor)
            yield
    finally:
        if descriptor >= 0:
            try:
                if lock_kind is not None:
                    _release_queue_file_lock(descriptor, lock_kind)
            finally:
                close_descriptor(descriptor)
        process_lock.release()


@contextmanager
def _locked_pending_queue(brain: Path) -> Iterator[None]:
    """Serialize cooperative enqueue/apply operations around a fresh preview."""

    key = str(brain.resolve(strict=False))
    with _PENDING_QUEUE_LOCKS_GUARD:
        process_lock = _PENDING_QUEUE_LOCKS.setdefault(key, threading.RLock())
    process_lock.acquire()
    descriptor = -1
    lock_kind: str | None = None
    try:
        if secure_dir_fd_io_supported():
            root_descriptor = _open_or_create_secure_directory(brain)
            try:
                with SecureDirectory(root_descriptor) as root:
                    root_descriptor = -1
                    with root.child("runtime", create=True) as runtime:
                        with runtime.child("locks", create=True) as locks:
                            with locks.child("pending", create=True) as pending_locks:
                                descriptor, created = pending_locks.open_or_create_file(
                                    "queue.lock", os.O_RDWR
                                )
                                os.fchmod(descriptor, 0o600)
                                if created:
                                    pending_locks.fsync()
                                lock_kind = _acquire_queue_file_lock(descriptor)
                                yield
            finally:
                if root_descriptor >= 0:
                    os.close(root_descriptor)
        else:
            lock_dir = brain / "runtime" / "locks" / "pending"
            _ensure_fallback_directory(lock_dir)
            lock_path = lock_dir / "queue.lock"
            try:
                descriptor = os.open(
                    lock_path,
                    os.O_RDWR
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_BINARY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
                _fsync_fallback_directory(lock_dir)
            except FileExistsError:
                descriptor = os.open(
                    lock_path,
                    os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
                )
            path_identity = os.lstat(lock_path)
            opened_identity = os.fstat(descriptor)
            if (
                not _is_safe_regular_file(path_identity)
                or not _is_safe_regular_file(opened_identity)
                or not _same_file_identity(path_identity, opened_identity)
            ):
                raise OSError("UNSAFE_PENDING_QUEUE_LOCK")
            lock_kind = _acquire_queue_file_lock(descriptor)
            yield
    finally:
        if descriptor >= 0:
            try:
                if lock_kind is not None:
                    _release_queue_file_lock(descriptor, lock_kind)
            finally:
                os.close(descriptor)
        process_lock.release()


@contextmanager
def _locked_pending_record(path: Path) -> Iterator[None]:
    """Coordinate one pending record across threads and cooperating processes."""

    key = str(path.resolve(strict=False))
    with _PENDING_RECORD_LOCKS_GUARD:
        process_lock = _PENDING_RECORD_LOCKS.setdefault(key, threading.RLock())
    process_lock.acquire()
    descriptor = -1
    try:
        with SecureDirectory.open(path.parent) as directory:
            with directory.child(".amh-record-locks", create=True) as locks:
                lock_name = pending_record_lock_name(path.name)
                descriptor, created = locks.open_or_create_file(lock_name, os.O_RDWR)
                os.fchmod(descriptor, 0o600)
                if created:
                    locks.fsync()
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
    finally:
        if descriptor >= 0:
            try:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        process_lock.release()


def _unlink_pending_record(
    path: Path,
    expected_sha256: str,
    expected_identity: tuple[int, int] | None = None,
) -> tuple[str, ...]:
    """Unlink only the exact no-follow record verified by content and identity."""

    with SecureDirectory.open(path.parent) as directory:
        descriptor, _created = directory.open_file(path.name, os.O_RDONLY | os.O_NONBLOCK)
        try:
            opened = os.fstat(descriptor)
            raw = _read_bounded_descriptor(descriptor, MAX_PENDING_RECORD_BYTES)
            current = directory.stat(path.name)
            if not _same_file_identity(opened, current):
                raise OSError("PENDING_RECORD_CHANGED")
            opened_identity = (int(opened.st_dev), int(opened.st_ino))
            if expected_identity is not None and opened_identity != expected_identity:
                raise OSError("CONCURRENT_MODIFICATION")
            if hashlib.sha256(raw).hexdigest() != expected_sha256:
                raise OSError("PENDING_RECORD_CHANGED")
        finally:
            os.close(descriptor)
        directory.unlink(path.name)
        try:
            directory.fsync()
        except OSError:
            _log.warning("PENDING_DIRECTORY_FSYNC_UNAVAILABLE")
            return ("PENDING_DIRECTORY_FSYNC_UNAVAILABLE",)
    return ()


def _pending_write_input(
    path: Path,
    raw: bytes,
    preview: PendingRecordPreview,
) -> tuple[MemoryItem, str, bool] | None:
    """Rebuild the exact previewed item without trusting mutable wall-clock state."""

    parsed = _pending_record_payload(path, raw, preview)
    if parsed is None or preview._stable_item_id is None:
        return None
    item, version, original_created_at, payload_sha256 = parsed
    validated, validation_reason = _validate_pending_item(
        item=item,
        version=version,
        stable_item_id=preview._stable_item_id,
        original_created_at=original_created_at,
        payload_sha256=payload_sha256,
    )
    if validated is None or validation_reason is not None:
        return None
    body = item.get("body", "")
    if not isinstance(body, str):
        return None
    # The apply boundary always re-runs the audit. A queued allow_unsafe bit
    # cannot silently bypass a governance apply decision.
    return validated, body, False


def _pending_record_payload(
    path: Path,
    raw: bytes,
    preview: PendingRecordPreview,
) -> tuple[dict[str, object], int, datetime, str] | None:
    """Re-validate the exact envelope while retaining unsupported raw item types."""

    try:
        line = raw.decode("utf-8").strip().splitlines()[0]
        record = json.loads(line, parse_constant=_reject_json_constant)
        if not isinstance(record, dict):
            return None
        item = record.get("item")
        if not isinstance(item, dict):
            return None
        version = _record_version(record.get("v"))
        if version is None or record.get("op") != "write":
            return None
        payload_sha256 = _canonical_payload_sha256(item)
        if payload_sha256 != preview.payload_sha256:
            return None
        if version == 2:
            if record.get("record_id") != preview.record_id:
                return None
            declared_hash = record.get("payload_sha256")
            if not isinstance(declared_hash, str) or declared_hash.lower() != payload_sha256:
                return None
        elif _legacy_record_id(path, record) != preview.record_id:
            return None
        if preview.original_created_at is None:
            return None
        original_created_at, reason = _parse_pending_time(
            preview.original_created_at,
            field="ORIGINAL_CREATED_AT",
        )
        if reason or original_created_at is None:
            return None
        raw_original = record.get("original_created_at")
        if raw_original is None:
            raw_original = item.get("created_at")
        if raw_original is None:
            raw_original = _enqueued_at_value(path, record, version=version)
        current_original, current_reason = _parse_pending_time(
            raw_original,
            field="ORIGINAL_CREATED_AT",
        )
        if (
            current_reason
            or current_original is None
            or current_original != original_created_at
        ):
            return None
        return item, version, original_created_at, payload_sha256
    except (IndexError, UnicodeError, ValueError, TypeError, OverflowError):
        return None


def _classify_pending_record(
    *,
    path: Path,
    record: dict[str, object],
    item: dict[str, object],
    existing_items: Mapping[str, MemoryItem],
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


def _require_readiness_deadline(deadline_at: float | None) -> None:
    if deadline_at is not None and _monotonic() > deadline_at:
        raise _PendingReadError("PENDING_READINESS_BUDGET_EXCEEDED")


def _pending_record_paths(
    directory: Path,
    *,
    deadline_at: float | None = None,
    entry_cap: int | None = None,
) -> _PendingPathSnapshot:
    retained: list[tuple[_DescendingName, str, Path]] = []
    total = 0
    scan_failed = False
    budget_exceeded = False
    scanned_entries = 0
    retain_limit = max(0, MAX_PENDING_QUEUE_ENTRIES) + 1

    def consider(entry: os.DirEntry[str], *, fallback: bool) -> None:
        nonlocal scan_failed, total
        try:
            if not entry.name.endswith(".jsonl"):
                return
            if fallback:
                _require_readiness_deadline(deadline_at)
                opened = os.lstat(directory / entry.name)
                _require_readiness_deadline(deadline_at)
                if _is_reparse_point(opened):
                    scan_failed = True
                    return
                if not stat.S_ISREG(opened.st_mode):
                    return
            else:
                _require_readiness_deadline(deadline_at)
                is_symlink = entry.is_symlink()
                _require_readiness_deadline(deadline_at)
                if is_symlink:
                    return
                is_file = entry.is_file(follow_symlinks=False)
                _require_readiness_deadline(deadline_at)
                if not is_file:
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
            _require_readiness_deadline(deadline_at)
            descriptor = open_directory_path_without_symlinks(directory)
            _require_readiness_deadline(deadline_at)
            with os.scandir(descriptor) as entries:
                _require_readiness_deadline(deadline_at)
                while True:
                    _require_readiness_deadline(deadline_at)
                    try:
                        entry = next(entries)
                    except StopIteration:
                        break
                    _require_readiness_deadline(deadline_at)
                    if entry_cap is not None and scanned_entries >= max(0, entry_cap):
                        budget_exceeded = True
                        break
                    scanned_entries += 1
                    consider(entry, fallback=False)
                    _require_readiness_deadline(deadline_at)
        except FileNotFoundError:
            return _PendingPathSnapshot(paths=[], total=0)
        except _PendingReadError:
            budget_exceeded = True
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
            scan_unavailable=scan_failed or budget_exceeded,
            reason=(
                "PENDING_READINESS_BUDGET_EXCEEDED"
                if budget_exceeded
                else "PENDING_SCAN_UNAVAILABLE"
                if scan_failed
                else None
            ),
        )

    try:
        _require_readiness_deadline(deadline_at)
        before = os.lstat(directory)
        _require_readiness_deadline(deadline_at)
        if not _is_safe_directory(before):
            return _PendingPathSnapshot(
                paths=[],
                total=0,
                scan_unavailable=True,
                reason="PENDING_SCAN_UNAVAILABLE",
            )
        with os.scandir(directory) as entries:
            _require_readiness_deadline(deadline_at)
            while True:
                _require_readiness_deadline(deadline_at)
                try:
                    entry = next(entries)
                except StopIteration:
                    break
                _require_readiness_deadline(deadline_at)
                if entry_cap is not None and scanned_entries >= max(0, entry_cap):
                    budget_exceeded = True
                    break
                scanned_entries += 1
                consider(entry, fallback=True)
                _require_readiness_deadline(deadline_at)
        if not budget_exceeded:
            _require_readiness_deadline(deadline_at)
            after = os.lstat(directory)
            _require_readiness_deadline(deadline_at)
            if not _is_safe_directory(after) or not _same_file_identity(before, after):
                scan_failed = True
    except FileNotFoundError:
        return _PendingPathSnapshot(paths=[], total=0)
    except _PendingReadError:
        budget_exceeded = True
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
        scan_unavailable=scan_failed or budget_exceeded,
        reason=(
            "PENDING_READINESS_BUDGET_EXCEEDED"
            if budget_exceeded
            else "PENDING_SCAN_UNAVAILABLE"
            if scan_failed
            else None
        ),
    )


def _read_pending_record_snapshot(
    path: Path,
    *,
    deadline_at: float | None = None,
    limit: int = MAX_PENDING_RECORD_BYTES,
) -> tuple[bytes, tuple[int, int]]:
    """Read a bounded record and bind it to a reliable non-zero file identity."""

    if secure_dir_fd_io_supported():
        directory_descriptor: int | None = None
        descriptor: int | None = None
        try:
            _require_readiness_deadline(deadline_at)
            directory_descriptor = open_directory_path_without_symlinks(path.parent)
            _require_readiness_deadline(deadline_at)
            before = os.stat(
                path.name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if stat.S_ISLNK(before.st_mode):
                raise _PendingReadError("PENDING_RECORD_CHANGED")
            if not _is_safe_regular_file(before):
                raise _PendingReadError("PENDING_RECORD_NOT_REGULAR")
            descriptor = open_regular_file_at(directory_descriptor, path.name)
            _require_readiness_deadline(deadline_at)
            opened = os.fstat(descriptor)
            _require_readiness_deadline(deadline_at)
            identity = (
                int(getattr(opened, "st_dev", 0) or 0),
                int(getattr(opened, "st_ino", 0) or 0),
            )
            if not all(identity):
                raise _PendingReadError("PENDING_RECORD_CHANGED")
            raw = _read_bounded_descriptor(
                descriptor,
                limit,
                deadline_at=deadline_at,
            )
            _require_readiness_deadline(deadline_at)
            after = os.fstat(descriptor)
            _require_readiness_deadline(deadline_at)
            if not _same_file_identity(opened, after):
                raise _PendingReadError("PENDING_RECORD_CHANGED")
            return raw, identity
        except FileNotFoundError:
            raise
        except _PendingReadError:
            raise
        except OSError as exc:
            raise _PendingReadError("PENDING_RECORD_READ_FAILED") from exc
        finally:
            if descriptor is not None:
                close_descriptor(descriptor)
            if directory_descriptor is not None:
                close_descriptor(directory_descriptor)

    try:
        _require_readiness_deadline(deadline_at)
        before = os.lstat(path)
        _require_readiness_deadline(deadline_at)
        if stat.S_ISLNK(before.st_mode):
            raise _PendingReadError("PENDING_RECORD_CHANGED")
        if not _is_safe_regular_file(before):
            raise _PendingReadError("PENDING_RECORD_NOT_REGULAR")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        _require_readiness_deadline(deadline_at)
        descriptor = os.open(path, flags)
        try:
            _require_readiness_deadline(deadline_at)
            opened = os.fstat(descriptor)
            _require_readiness_deadline(deadline_at)
            if not _is_safe_regular_file(opened) or not _same_file_identity(before, opened):
                raise _PendingReadError("PENDING_RECORD_CHANGED")
            identity = (
                int(getattr(opened, "st_dev", 0) or 0),
                int(getattr(opened, "st_ino", 0) or 0),
            )
            if not all(identity):
                raise _PendingReadError("PENDING_RECORD_CHANGED")
            raw = _read_bounded_descriptor(
                descriptor,
                limit,
                deadline_at=deadline_at,
            )
            return raw, identity
        finally:
            close_descriptor(descriptor)
    except FileNotFoundError:
        raise
    except _PendingReadError:
        raise
    except OSError as exc:
        raise _PendingReadError("PENDING_RECORD_READ_FAILED") from exc


def _read_pending_record(
    path: Path,
    *,
    limit: int = MAX_PENDING_RECORD_BYTES,
) -> bytes:
    return _read_pending_record_snapshot(path, limit=limit)[0]


def _read_bounded_descriptor(
    descriptor: int,
    limit: int,
    *,
    deadline_at: float | None = None,
) -> bytes:
    _require_readiness_deadline(deadline_at)
    before = os.fstat(descriptor)
    _require_readiness_deadline(deadline_at)
    if not _is_safe_regular_file(before):
        raise _PendingReadError("PENDING_RECORD_NOT_REGULAR")
    if before.st_size > limit:
        raise _PendingReadError("PENDING_RECORD_TOO_LARGE")
    chunks: list[bytes] = []
    remaining = limit + 1
    while remaining > 0:
        _require_readiness_deadline(deadline_at)
        chunk = os.read(descriptor, min(65536, remaining))
        _require_readiness_deadline(deadline_at)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    _require_readiness_deadline(deadline_at)
    after = os.fstat(descriptor)
    _require_readiness_deadline(deadline_at)
    if len(data) > limit:
        raise _PendingReadError("PENDING_RECORD_TOO_LARGE")
    if (
        not _is_safe_regular_file(after)
        or not _same_file_identity(before, after)
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(data) != after.st_size
    ):
        raise _PendingReadError("PENDING_RECORD_CHANGED")
    return data


def _scan_existing_item_metadata(
    items_dir: Path,
    *,
    deadline_at: float | None = None,
) -> _ItemMetadataSnapshot:
    """Read bounded item frontmatter only; never load Markdown bodies."""

    if not secure_dir_fd_io_supported():
        return _scan_existing_item_metadata_fallback(
            items_dir,
            deadline_at=deadline_at,
        )
    root: int | None = None
    stack: list[tuple[int, Iterator[os.DirEntry[str]], int]] = []
    items: dict[str, MemoryItem] = {}
    entry_count = 0
    bytes_read = 0
    try:
        _require_readiness_deadline(deadline_at)
        root = open_directory_path_without_symlinks(items_dir)
        _require_readiness_deadline(deadline_at)
        root_entries = os.scandir(root)
        try:
            _require_readiness_deadline(deadline_at)
        except BaseException:
            root_entries.close()
            raise
        stack.append((root, root_entries, 0))
        root = None
    except FileNotFoundError:
        if root is not None:
            close_descriptor(root)
        return _ItemMetadataSnapshot(items={}, trusted=True)
    except OSError:
        if root is not None:
            close_descriptor(root)
        return _ItemMetadataSnapshot(items={}, trusted=False)
    except _PendingReadError as exc:
        if root is not None:
            close_descriptor(root)
        return _ItemMetadataSnapshot(items={}, trusted=False, reason=exc.reason)
    try:
        while stack:
            directory, entries, depth = stack[-1]
            try:
                _require_readiness_deadline(deadline_at)
                entry = next(entries)
                _require_readiness_deadline(deadline_at)
            except StopIteration:
                _close_item_scan_frame(stack.pop())
                continue
            except OSError:
                return _ItemMetadataSnapshot(items={}, trusted=False)
            if depth == 0 and entry.name == ".amh-item-locks":
                continue
            entry_count += 1
            if entry_count > MAX_ITEM_METADATA_ENTRIES:
                return _ItemMetadataSnapshot(items={}, trusted=False)
            try:
                _require_readiness_deadline(deadline_at)
                is_symlink = entry.is_symlink()
                _require_readiness_deadline(deadline_at)
                if is_symlink:
                    continue
                is_directory = entry.is_dir(follow_symlinks=False)
                _require_readiness_deadline(deadline_at)
                if is_directory:
                    if depth >= MAX_ITEM_DIRECTORY_DEPTH:
                        return _ItemMetadataSnapshot(items={}, trusted=False)
                    _require_readiness_deadline(deadline_at)
                    child = open_child_directory(directory, entry.name)
                    try:
                        _require_readiness_deadline(deadline_at)
                        child_entries = os.scandir(child)
                        try:
                            _require_readiness_deadline(deadline_at)
                            stack.append((child, child_entries, depth + 1))
                        except BaseException:
                            child_entries.close()
                            raise
                    except BaseException:
                        close_descriptor(child)
                        raise
                    continue
                is_file = entry.is_file(follow_symlinks=False)
                _require_readiness_deadline(deadline_at)
                if not is_file or not entry.name.endswith(".md"):
                    continue
                item, consumed = _read_item_frontmatter(
                    directory,
                    entry.name,
                    deadline_at=deadline_at,
                )
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
        return _ItemMetadataSnapshot(
            items=items,
            trusted=True,
            entry_count=entry_count,
            metadata_bytes=bytes_read,
        )
    except _PendingReadError as exc:
        return _ItemMetadataSnapshot(items={}, trusted=False, reason=exc.reason)
    finally:
        if root is not None:
            close_descriptor(root)
        while stack:
            _close_item_scan_frame(stack.pop())


def _verify_fallback_directory_chain(
    path: Path,
    *,
    deadline_at: float | None = None,
) -> os.stat_result:
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    _require_readiness_deadline(deadline_at)
    opened = os.lstat(current)
    _require_readiness_deadline(deadline_at)
    if not _is_safe_directory(opened):
        raise OSError("trusted path anchor is not a directory")
    for component in absolute.parts[1:]:
        if component in {"", ".", ".."}:
            raise OSError("invalid trusted directory component")
        current /= component
        _require_readiness_deadline(deadline_at)
        opened = os.lstat(current)
        _require_readiness_deadline(deadline_at)
        if not _is_safe_directory(opened):
            raise OSError("trusted path component is not a directory")
    return opened


def _scan_existing_item_metadata_fallback(
    items_dir: Path,
    *,
    deadline_at: float | None = None,
) -> _ItemMetadataSnapshot:
    """Windows-compatible lstat/open/fstat frontmatter-only traversal."""

    items: dict[str, MemoryItem] = {}
    entry_count = 0
    bytes_read = 0
    stack: list[tuple[Path, Iterator[os.DirEntry[str]], int, os.stat_result]] = []
    try:
        root_identity = _verify_fallback_directory_chain(
            items_dir,
            deadline_at=deadline_at,
        )
        _require_readiness_deadline(deadline_at)
        root_entries = os.scandir(items_dir)
        try:
            _require_readiness_deadline(deadline_at)
        except BaseException:
            root_entries.close()
            raise
        stack.append((items_dir, root_entries, 0, root_identity))
    except FileNotFoundError:
        return _ItemMetadataSnapshot(items={}, trusted=True)
    except OSError:
        return _ItemMetadataSnapshot(items={}, trusted=False)
    except _PendingReadError as exc:
        return _ItemMetadataSnapshot(items={}, trusted=False, reason=exc.reason)
    try:
        while stack:
            directory, entries, depth, identity = stack[-1]
            try:
                _require_readiness_deadline(deadline_at)
                entry = next(entries)
                _require_readiness_deadline(deadline_at)
            except StopIteration:
                close = getattr(entries, "close", None)
                if callable(close):
                    close()
                stack.pop()
                try:
                    _require_readiness_deadline(deadline_at)
                    current_identity = os.lstat(directory)
                    _require_readiness_deadline(deadline_at)
                    if not _is_safe_directory(current_identity) or not _same_file_identity(
                        identity, current_identity
                    ):
                        return _ItemMetadataSnapshot(items={}, trusted=False)
                except OSError:
                    return _ItemMetadataSnapshot(items={}, trusted=False)
                continue
            except OSError:
                return _ItemMetadataSnapshot(items={}, trusted=False)
            if depth == 0 and entry.name == ".amh-item-locks":
                continue
            entry_count += 1
            if entry_count > MAX_ITEM_METADATA_ENTRIES:
                return _ItemMetadataSnapshot(items={}, trusted=False)
            path = directory / entry.name
            try:
                # DirEntry identity can be zero on Windows. Use an explicit
                # path lstat as the pre-open identity for every nested entry.
                _require_readiness_deadline(deadline_at)
                opened = os.lstat(path)
                _require_readiness_deadline(deadline_at)
                if _is_reparse_point(opened):
                    return _ItemMetadataSnapshot(items={}, trusted=False)
                if stat.S_ISLNK(opened.st_mode):
                    continue
                if stat.S_ISDIR(opened.st_mode):
                    if depth >= MAX_ITEM_DIRECTORY_DEPTH:
                        return _ItemMetadataSnapshot(items={}, trusted=False)
                    _require_readiness_deadline(deadline_at)
                    child_entries = os.scandir(path)
                    try:
                        _require_readiness_deadline(deadline_at)
                    except BaseException:
                        child_entries.close()
                        raise
                    stack.append((path, child_entries, depth + 1, opened))
                    continue
                if not stat.S_ISREG(opened.st_mode) or not entry.name.endswith(".md"):
                    continue
                item, consumed = _read_item_frontmatter_fallback(
                    path,
                    deadline_at=deadline_at,
                )
                bytes_read += consumed
                if bytes_read > MAX_ITEM_METADATA_BYTES or item is None:
                    return _ItemMetadataSnapshot(items={}, trusted=False)
                if path.stem != item.id or item.id in items:
                    return _ItemMetadataSnapshot(items={}, trusted=False)
                items[item.id] = item
            except OSError:
                return _ItemMetadataSnapshot(items={}, trusted=False)
        return _ItemMetadataSnapshot(
            items=items,
            trusted=True,
            entry_count=entry_count,
            metadata_bytes=bytes_read,
        )
    except _PendingReadError as exc:
        return _ItemMetadataSnapshot(items={}, trusted=False, reason=exc.reason)
    finally:
        while stack:
            _directory, frame_entries, _depth, _identity = stack.pop()
            close = getattr(frame_entries, "close", None)
            if callable(close):
                close()


def _read_item_frontmatter_fallback(
    path: Path,
    *,
    deadline_at: float | None = None,
) -> tuple[MemoryItem | None, int]:
    consumed = 0
    descriptor: int | None = None
    try:
        _require_readiness_deadline(deadline_at)
        before = os.lstat(path)
        _require_readiness_deadline(deadline_at)
        if not _is_safe_regular_file(before):
            return None, consumed
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        _require_readiness_deadline(deadline_at)
        descriptor = os.open(path, flags)
        _require_readiness_deadline(deadline_at)
        opened = os.fstat(descriptor)
        _require_readiness_deadline(deadline_at)
        if not _is_safe_regular_file(opened) or not _same_file_identity(before, opened):
            return None, consumed
        handle = cast(BinaryIO, os.fdopen(descriptor, "rb", buffering=0))
        descriptor = None
        with handle:
            _require_readiness_deadline(deadline_at)
            opening = handle.readline(MAX_ITEM_FRONTMATTER_BYTES + 1)
            _require_readiness_deadline(deadline_at)
            consumed += len(opening)
            normalized_opening = opening.rstrip(b"\r\n")
            if normalized_opening.startswith(b"\xef\xbb\xbf"):
                normalized_opening = normalized_opening[3:]
            if normalized_opening != b"---":
                return None, consumed
            lines: list[bytes] = []
            while consumed <= MAX_ITEM_FRONTMATTER_BYTES:
                _require_readiness_deadline(deadline_at)
                line = handle.readline(MAX_ITEM_FRONTMATTER_BYTES - consumed + 1)
                _require_readiness_deadline(deadline_at)
                consumed += len(line)
                if consumed > MAX_ITEM_FRONTMATTER_BYTES or not line:
                    return None, consumed
                if line.rstrip(b"\r\n") == b"---":
                    frontmatter = b"---\n" + b"".join(lines) + b"---\n"
                    item, _body = parse_item_markdown(frontmatter.decode("utf-8"))
                    _require_readiness_deadline(deadline_at)
                    after = os.fstat(handle.fileno())
                    _require_readiness_deadline(deadline_at)
                    if (
                        opened.st_size != after.st_size
                        or opened.st_mtime_ns != after.st_mtime_ns
                        or not _is_safe_regular_file(after)
                        or not _same_file_identity(opened, after)
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
    except _PendingReadError:
        raise
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
    directory_descriptor: int,
    filename: str,
    *,
    deadline_at: float | None = None,
) -> tuple[MemoryItem | None, int]:
    consumed = 0
    lines: list[bytes] = []
    try:
        with _open_regular_binary(
            directory_descriptor,
            filename,
            deadline_at=deadline_at,
        ) as handle:
            _require_readiness_deadline(deadline_at)
            opening = handle.readline(MAX_ITEM_FRONTMATTER_BYTES + 1)
            _require_readiness_deadline(deadline_at)
            consumed += len(opening)
            normalized_opening = opening.rstrip(b"\r\n")
            if normalized_opening.startswith(b"\xef\xbb\xbf"):
                normalized_opening = normalized_opening[3:]
            if normalized_opening != b"---":
                return None, consumed
            while consumed <= MAX_ITEM_FRONTMATTER_BYTES:
                _require_readiness_deadline(deadline_at)
                line = handle.readline(MAX_ITEM_FRONTMATTER_BYTES - consumed + 1)
                _require_readiness_deadline(deadline_at)
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
    except _PendingReadError:
        raise
    return None, consumed


@contextmanager
def _open_regular_binary(
    directory_descriptor: int,
    filename: str,
    *,
    deadline_at: float | None = None,
) -> Iterator[BinaryIO]:
    descriptor: int | None = None
    try:
        _require_readiness_deadline(deadline_at)
        descriptor = open_regular_file_at(directory_descriptor, filename)
        _require_readiness_deadline(deadline_at)
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
