"""Low-sensitive, append-only records for lifecycle transactions."""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO, Iterator


_PROCESS_LOCK = threading.RLock()
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
    runtime_dir = Path(brain_dir) / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    lock_path = runtime_dir / ".lifecycle-transaction.lock"
    with _PROCESS_LOCK, _locked_file(lock_path):
        yield


def append_lifecycle_record(
    brain_dir: Path, record: LifecycleLedgerRecord
) -> None:
    """Append and fsync one fixed-schema record, truncating on failure."""
    runtime_dir = Path(brain_dir) / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = runtime_dir / "lifecycle-actions.jsonl"
    lock_path = runtime_dir / ".lifecycle-ledger.lock"
    payload = (
        json.dumps(asdict(record), ensure_ascii=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")

    with _PROCESS_LOCK, _locked_file(lock_path):
        descriptor = os.open(
            ledger_path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        try:
            os.chmod(ledger_path, 0o600)
            original_length = os.fstat(descriptor).st_size
            try:
                _write_all(descriptor, payload)
                os.fsync(descriptor)
            except BaseException as append_error:
                try:
                    os.ftruncate(descriptor, original_length)
                    os.fsync(descriptor)
                except BaseException as rollback_error:
                    raise LifecycleLedgerRollbackError(
                        "LEDGER_ROLLBACK_FAILED"
                    ) from rollback_error
                raise append_error
        finally:
            os.close(descriptor)


def latest_applied_supersession_record(
    brain_dir: Path,
    replacement_id: str,
    obsolete_id: str,
) -> LifecycleLedgerRecord | None:
    """Return the latest matching transaction only when it is a valid apply."""
    ledger_path = Path(brain_dir) / "runtime" / "lifecycle-actions.jsonl"
    lock_path = ledger_path.parent / ".lifecycle-ledger.lock"
    if not ledger_path.is_file():
        return None
    with _PROCESS_LOCK, _locked_file(lock_path):
        try:
            lines = ledger_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            return None
    for line in reversed(lines):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        if (
            data.get("obsolete_id") != obsolete_id
            or data.get("replacement_id") != replacement_id
        ):
            continue
        if set(data) != _LEDGER_FIELDS or not _valid_record_types(data):
            return None
        try:
            record = LifecycleLedgerRecord(**data)
        except TypeError:
            return None
        if (
            record.action == "supersede"
            and record.status == "applied"
            and record.reason == "OK"
        ):
            return record
        return None
    return None


@contextmanager
def _locked_file(path: Path) -> Iterator[BinaryIO]:
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    handle = os.fdopen(descriptor, "r+b", buffering=0)
    try:
        os.chmod(path, 0o600)
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"\0")
            handle.flush()
        _lock(handle)
        try:
            yield handle
        finally:
            _unlock(handle)
    finally:
        handle.close()


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


__all__ = [
    "LifecycleLedgerRecord",
    "append_lifecycle_record",
    "latest_applied_supersession_record",
    "lifecycle_transaction_lock",
]
