"""Low-sensitive, append-only records for lifecycle transactions."""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Iterator

from agent_brain.contracts.memory_item import is_valid_memory_item_id


_PROCESS_LOCK = threading.RLock()
_log = logging.getLogger(__name__)
_LEDGER_FIELDS = {
    "action",
    "timestamp",
    "status",
    "reason",
    "obsolete_id",
    "replacement_id",
    "snapshot",
    "replacement_ref_preexisted",
}
_GIT_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", None)
_ALLOWED_RECORD_STATES = {
    ("supersede", "applied", "OK"),
    ("revert-supersession", "reverted", "OK"),
    ("supersede", "blocked", "MARKDOWN_UPDATE_FAILED"),
    ("supersede", "blocked", "ROLLBACK_FAILED"),
    ("revert-supersession", "blocked", "MARKDOWN_UPDATE_FAILED"),
    ("revert-supersession", "blocked", "ROLLBACK_FAILED"),
}


@dataclass(frozen=True)
class LifecycleLedgerRecord:
    action: str
    timestamp: str
    status: str
    reason: str
    obsolete_id: str
    replacement_id: str | None
    snapshot: str | None
    replacement_ref_preexisted: bool


class LifecycleLedgerRollbackError(OSError):
    """The ledger append and its byte rollback both failed."""


@contextmanager
def lifecycle_transaction_lock(brain_dir: Path) -> Iterator[None]:
    """Serialize lifecycle read-check-write transactions across processes."""
    runtime_dir = _safe_runtime_dir(brain_dir, create=True)
    assert runtime_dir is not None
    lock_path = runtime_dir / ".lifecycle-transaction.lock"
    with _PROCESS_LOCK, _locked_file(lock_path, runtime_dir):
        yield


def append_lifecycle_record(
    brain_dir: Path, record: LifecycleLedgerRecord
) -> None:
    """Append and fsync one fixed-schema record, truncating on failure."""
    if not _valid_record(record):
        raise TypeError("INVALID_LIFECYCLE_LEDGER_RECORD")
    payload_data = {
        "action": record.action,
        "timestamp": record.timestamp,
        "status": record.status,
        "reason": record.reason,
        "obsolete_id": record.obsolete_id,
        "replacement_id": record.replacement_id,
        "snapshot": record.snapshot,
        "replacement_ref_preexisted": record.replacement_ref_preexisted,
    }
    payload = (
        json.dumps(payload_data, ensure_ascii=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    runtime_dir = _safe_runtime_dir(brain_dir, create=True)
    assert runtime_dir is not None
    ledger_path = runtime_dir / "lifecycle-actions.jsonl"
    lock_path = runtime_dir / ".lifecycle-ledger.lock"

    with _PROCESS_LOCK, _locked_file(lock_path, runtime_dir):
        descriptor, created = _secure_open_regular(
            ledger_path,
            os.O_WRONLY | os.O_APPEND,
            create=True,
        )
        try:
            original_length = os.fstat(descriptor).st_size
            try:
                _write_all(descriptor, payload)
                os.fsync(descriptor)
                if created:
                    _fsync_directory(runtime_dir)
            except BaseException as append_error:
                try:
                    os.ftruncate(descriptor, original_length)
                    os.fsync(descriptor)
                except BaseException as rollback_error:
                    if not isinstance(append_error, Exception):
                        append_error.add_note("LEDGER_ROLLBACK_FAILED")
                        raise append_error
                    raise LifecycleLedgerRollbackError(
                        "LEDGER_ROLLBACK_FAILED"
                    ) from rollback_error
                raise
        finally:
            try:
                os.close(descriptor)
            except BaseException:
                _log.warning("LIFECYCLE_LEDGER_HOUSEKEEPING_FAILED")


def latest_applied_supersession_record(
    brain_dir: Path,
    replacement_id: str,
    obsolete_id: str,
) -> LifecycleLedgerRecord | None:
    """Return the latest matching transaction only when it is a valid apply."""
    try:
        runtime_dir = _safe_runtime_dir(brain_dir, create=False)
    except OSError:
        return None
    if runtime_dir is None:
        return None
    ledger_path = runtime_dir / "lifecycle-actions.jsonl"
    lock_path = runtime_dir / ".lifecycle-ledger.lock"
    try:
        with _PROCESS_LOCK, _locked_file(lock_path, runtime_dir):
            descriptor, _ = _secure_open_regular(
                ledger_path, os.O_RDONLY, create=False
            )
            try:
                with os.fdopen(descriptor, "rb") as handle:
                    descriptor = -1
                    lines = handle.read().decode("utf-8").splitlines()
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
    except (OSError, UnicodeError):
        return None
    for line in reversed(lines):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        if set(data) != _LEDGER_FIELDS or not _valid_record_types(data):
            return None
        try:
            record = LifecycleLedgerRecord(**data)
        except TypeError:
            return None
        if not _valid_record(record):
            return None
        if (
            record.obsolete_id != obsolete_id
            or record.replacement_id != replacement_id
        ):
            continue
        if (
            record.action == "supersede"
            and record.status == "applied"
            and record.reason == "OK"
        ):
            return record
        return None
    return None


@contextmanager
def _locked_file(path: Path, runtime_dir: Path) -> Iterator[BinaryIO]:
    descriptor, created = _secure_open_regular(path, os.O_RDWR, create=True)
    handle = os.fdopen(descriptor, "r+b", buffering=0)
    try:
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        if created:
            _fsync_directory(runtime_dir)
        _lock(handle)
        try:
            yield handle
        finally:
            try:
                _unlock(handle)
            except BaseException:
                _log.warning("LIFECYCLE_LOCK_HOUSEKEEPING_FAILED")
    finally:
        try:
            handle.close()
        except BaseException:
            _log.warning("LIFECYCLE_LOCK_HOUSEKEEPING_FAILED")


def _lock(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(  # type: ignore[attr-defined]
            handle.fileno(), msvcrt.LK_LOCK, 1  # type: ignore[attr-defined]
        )
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(  # type: ignore[attr-defined]
            handle.fileno(), msvcrt.LK_UNLCK, 1  # type: ignore[attr-defined]
        )
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("LEDGER_WRITE_FAILED")
        remaining = remaining[written:]


def _valid_record_types(data: dict[str, object]) -> bool:
    return (
        all(
            isinstance(data[field], str)
            for field in ("action", "timestamp", "status", "reason", "obsolete_id")
        )
        and (data["replacement_id"] is None or isinstance(data["replacement_id"], str))
        and (data["snapshot"] is None or isinstance(data["snapshot"], str))
        and type(data["replacement_ref_preexisted"]) is bool
    )


def _valid_record(record: object) -> bool:
    if type(record) is not LifecycleLedgerRecord:
        return False
    assert isinstance(record, LifecycleLedgerRecord)
    replacement_id = record.replacement_id
    if not isinstance(replacement_id, str):
        return False
    values = (
        record.action,
        record.timestamp,
        record.status,
        record.reason,
        record.obsolete_id,
        replacement_id,
    )
    if not all(type(value) is str for value in values):
        return False
    if any(_has_control(value) for value in values):
        return False
    if (
        len(record.action) > 32
        or len(record.timestamp) > 64
        or len(record.status) > 24
        or len(record.reason) > 40
        or len(record.obsolete_id) > 240
        or len(replacement_id) > 240
    ):
        return False
    if (record.action, record.status, record.reason) not in _ALLOWED_RECORD_STATES:
        return False
    if not (
        is_valid_memory_item_id(record.obsolete_id)
        and is_valid_memory_item_id(replacement_id)
    ):
        return False
    try:
        parsed_timestamp = datetime.fromisoformat(record.timestamp)
    except ValueError:
        return False
    if (
        parsed_timestamp.tzinfo is None
        or parsed_timestamp.utcoffset() != timedelta(0)
    ):
        return False
    if record.snapshot is not None:
        if (
            type(record.snapshot) is not str
            or _has_control(record.snapshot)
            or _GIT_OBJECT_ID.fullmatch(record.snapshot) is None
        ):
            return False
    return type(record.replacement_ref_preexisted) is bool


def _has_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _safe_runtime_dir(brain_dir: Path, *, create: bool) -> Path | None:
    brain_root = Path(brain_dir).resolve()
    runtime_dir = brain_root / "runtime"
    try:
        runtime_stat = runtime_dir.lstat()
    except FileNotFoundError:
        if not create:
            return None
        os.mkdir(runtime_dir, 0o700)
        _fsync_directory(brain_root)
        runtime_stat = runtime_dir.lstat()
    if (
        stat.S_ISLNK(runtime_stat.st_mode)
        or not stat.S_ISDIR(runtime_stat.st_mode)
        or _is_reparse(runtime_stat)
    ):
        raise OSError("UNSAFE_LIFECYCLE_RUNTIME")
    descriptor = _open_directory(runtime_dir)
    try:
        os.fchmod(descriptor, 0o700)
    finally:
        _close_descriptor_housekeeping(descriptor)
    return runtime_dir


def _secure_open_regular(
    path: Path, flags: int, *, create: bool
) -> tuple[int, bool]:
    if _O_NOFOLLOW is None:
        raise OSError("SECURE_OPEN_UNSUPPORTED")
    secure_flags = flags | _O_NOFOLLOW
    created = False
    if create:
        try:
            descriptor = os.open(
                path, secure_flags | os.O_CREAT | os.O_EXCL, 0o600
            )
            created = True
        except FileExistsError:
            descriptor = os.open(path, secure_flags)
    else:
        descriptor = os.open(path, secure_flags)
    try:
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode) or _is_reparse(opened_stat):
            raise OSError("UNSAFE_LIFECYCLE_FILE")
        os.fchmod(descriptor, 0o600)
        return descriptor, created
    except BaseException:
        _close_descriptor_housekeeping(descriptor)
        raise


def _open_directory(path: Path) -> int:
    if os.name == "nt" or _O_NOFOLLOW is None:
        raise OSError("DIRECTORY_FSYNC_UNSUPPORTED")
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | _O_NOFOLLOW,
    )
    opened_stat = os.fstat(descriptor)
    if not stat.S_ISDIR(opened_stat.st_mode) or _is_reparse(opened_stat):
        _close_descriptor_housekeeping(descriptor)
        raise OSError("UNSAFE_LIFECYCLE_DIRECTORY")
    return descriptor


def _fsync_directory(path: Path) -> None:
    descriptor = _open_directory(path)
    try:
        os.fsync(descriptor)
    finally:
        _close_descriptor_housekeeping(descriptor)


def _close_descriptor_housekeeping(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except BaseException:
        _log.warning("LIFECYCLE_LEDGER_HOUSEKEEPING_FAILED")


def _is_reparse(file_stat: os.stat_result) -> bool:
    attributes = getattr(file_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


__all__ = [
    "LifecycleLedgerRecord",
    "append_lifecycle_record",
    "latest_applied_supersession_record",
    "lifecycle_transaction_lock",
]
