"""Low-sensitive, append-only records for lifecycle transactions."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Iterator

from agent_brain.contracts.memory_item import is_valid_memory_item_id
from agent_brain.memory.store.durable_fs import SecureDirectory


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
    with SecureDirectory.open(Path(brain_dir)) as brain:
        with brain.child("runtime", create=True) as runtime:
            with _PROCESS_LOCK, _locked_file(runtime, ".lifecycle-transaction.lock"):
                yield


def append_lifecycle_record(brain_dir: Path, record: LifecycleLedgerRecord) -> None:
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
    durable = False
    try:
        with SecureDirectory.open(Path(brain_dir)) as brain:
            with brain.child("runtime", create=True) as runtime:
                with _PROCESS_LOCK, _locked_file(runtime, ".lifecycle-ledger.lock"):
                    descriptor, created = runtime.open_or_create_file(
                        "lifecycle-actions.jsonl", os.O_WRONLY | os.O_APPEND
                    )
                    try:
                        original_length = os.fstat(descriptor).st_size
                        try:
                            _write_all(descriptor, payload)
                            os.fsync(descriptor)
                            if created:
                                runtime.fsync()
                            durable = True
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
    except BaseException:
        if durable:
            _log.warning("LIFECYCLE_LEDGER_HOUSEKEEPING_FAILED")
            return
        raise


def latest_applied_supersession_record(
    brain_dir: Path,
    replacement_id: str,
    obsolete_id: str,
) -> LifecycleLedgerRecord | None:
    """Return the latest matching transaction only when it is a valid apply."""
    try:
        with SecureDirectory.open(Path(brain_dir)) as brain:
            with brain.child("runtime") as runtime:
                with _PROCESS_LOCK, _locked_file(runtime, ".lifecycle-ledger.lock"):
                    descriptor, _ = runtime.open_file(
                        "lifecycle-actions.jsonl", os.O_RDONLY
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
        if record.obsolete_id != obsolete_id or record.replacement_id != replacement_id:
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
def _locked_file(runtime: SecureDirectory, name: str) -> Iterator[BinaryIO]:
    descriptor, created = runtime.open_or_create_file(name, os.O_RDWR)
    handle = os.fdopen(descriptor, "r+b", buffering=0)
    try:
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        if created:
            runtime.fsync()
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
        getattr(msvcrt, "locking")(handle.fileno(), getattr(msvcrt, "LK_LOCK"), 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        getattr(msvcrt, "locking")(handle.fileno(), getattr(msvcrt, "LK_UNLCK"), 1)
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
    if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() != timedelta(0):
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


__all__ = [
    "LifecycleLedgerRecord",
    "append_lifecycle_record",
    "latest_applied_supersession_record",
    "lifecycle_transaction_lock",
]
