"""Low-sensitivity, append-only receipts for explicit pending apply batches."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import stat
import threading
from collections import Counter
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO, Iterator, Literal

from agent_brain.memory.store.durable_fs import SecureDirectory


PENDING_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "batch_id",
        "batch_digest",
        "selection_mode",
        "requested_count",
        "selected_count",
        "depth_before",
        "depth_after",
        "status_counts",
        "classification_counts",
        "reason_counts",
        "index_repair_required_count",
        "warning_counts",
        "prepared_at",
        "completed_at",
        "state",
        "result_digest",
    }
)
MAX_PENDING_RECEIPT_LEDGER_BYTES = 16 * 1024 * 1024
MAX_PENDING_RECEIPT_LINE_BYTES = 64 * 1024
MAX_PENDING_RECEIPT_RECORDS = 100_000
_HEX_32 = re.compile(r"[0-9a-f]{32}\Z")
_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
_PROCESS_LOCK = threading.RLock()
_log = logging.getLogger(__name__)

_ALLOWED_STATUSES = frozenset(
    {"written", "already_written", "review_required", "skipped", "failed"}
)
_ALLOWED_CLASSIFICATIONS = frozenset(
    {
        "ready",
        "already_written",
        "stale_requires_review",
        "duplicate_candidate",
        "conflict",
        "unsupported_type",
        "malformed",
        "audit_blocked",
        "unknown",
    }
)
_ALLOWED_REASONS = frozenset(
    {
        "AUDIT_BLOCKED",
        "AUDIT_SCAN_FAILED",
        "CONCURRENT_MODIFICATION",
        "DUPLICATE_RECORD_ID_SELECTION",
        "EXISTING_ITEM_SCAN_UNAVAILABLE",
        "INVALID_ITEM_BODY",
        "INVALID_ITEM_SCHEMA",
        "INVALID_ITEM_TITLE",
        "PAYLOAD_HASH_MISMATCH",
        "PENDING_APPLY_FAILED",
        "PENDING_DIRECTORY_FSYNC_UNAVAILABLE",
        "PENDING_ITEM_SNAPSHOT_UNTRUSTED",
        "PENDING_LOCK_GC_TRUNCATED",
        "PENDING_LOCK_GC_UNAVAILABLE",
        "PENDING_LOCK_GC_UNSAFE_ENTRY",
        "PENDING_QUEUE_TRUNCATED",
        "PENDING_READINESS_BUDGET_EXCEEDED",
        "PENDING_RECORD_CHANGED",
        "PENDING_RECORD_ID_CONFLICT",
        "PENDING_SCAN_UNAVAILABLE",
        "PENDING_UNLINK_FAILED",
        "PENDING_WRITE_SERVICE_UNAVAILABLE",
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
        "UNSUPPORTED_MEMORY_TYPE",
        "UNKNOWN_PENDING_REASON",
        "WRITTEN",
        "WRITTEN_INDEX_REPAIR_REQUIRED",
    }
)


@dataclass(frozen=True)
class PendingReceiptSelection:
    """Ephemeral receipt input; raw identity is never serialized."""

    record_id: str
    payload_sha256: str


@dataclass(frozen=True)
class PendingReceiptOutcome:
    """Ephemeral per-record result used only to build counts and a digest."""

    record_id: str
    status: str
    classification: str | None
    reason: str
    index_repair_required: bool
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class PendingBatchReceipt:
    schema_version: int
    batch_id: str
    batch_digest: str
    selection_mode: Literal["explicit", "safe_only"]
    requested_count: int
    selected_count: int
    depth_before: int
    depth_after: int | None
    status_counts: Mapping[str, int]
    classification_counts: Mapping[str, int]
    reason_counts: Mapping[str, int]
    index_repair_required_count: int
    warning_counts: Mapping[str, int]
    prepared_at: str
    completed_at: str | None
    state: Literal["prepared", "completed", "incomplete"]
    result_digest: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "batch_id": self.batch_id,
            "batch_digest": self.batch_digest,
            "selection_mode": self.selection_mode,
            "requested_count": self.requested_count,
            "selected_count": self.selected_count,
            "depth_before": self.depth_before,
            "depth_after": self.depth_after,
            "status_counts": dict(sorted(self.status_counts.items())),
            "classification_counts": dict(sorted(self.classification_counts.items())),
            "reason_counts": dict(sorted(self.reason_counts.items())),
            "index_repair_required_count": self.index_repair_required_count,
            "warning_counts": dict(sorted(self.warning_counts.items())),
            "prepared_at": self.prepared_at,
            "completed_at": self.completed_at,
            "state": self.state,
            "result_digest": self.result_digest,
        }


@dataclass(frozen=True)
class PendingReceiptLedgerHealth:
    status: Literal["not_present", "healthy", "corrupt", "unavailable"]
    record_count: int = 0
    incomplete_count: int = 0


class PendingReceiptLedgerRollbackError(OSError):
    """The receipt append and byte rollback both failed."""


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_digest(domain: bytes, payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(domain + b"\0" + encoded).hexdigest()


def _public_reason(reason: str) -> str:
    return reason if reason in _ALLOWED_REASONS else "UNKNOWN_PENDING_REASON"


def prepare_pending_receipt(
    *,
    selection_mode: Literal["explicit", "safe_only"],
    requested_count: int,
    selected: Iterable[PendingReceiptSelection],
    depth_before: int,
    batch_id: str | None = None,
    prepared_at: str | None = None,
) -> PendingBatchReceipt:
    selections = tuple(selected)
    canonical_selection: list[tuple[str, str]] = []
    for selection in selections:
        if (
            type(selection) is not PendingReceiptSelection
            or not selection.record_id
            or _HEX_64.fullmatch(selection.payload_sha256) is None
        ):
            raise TypeError("INVALID_PENDING_RECEIPT_SELECTION")
        canonical_selection.append((selection.record_id, selection.payload_sha256))
    receipt = PendingBatchReceipt(
        schema_version=1,
        batch_id=batch_id or secrets.token_hex(16),
        batch_digest=_canonical_digest(
            b"amh.pending.batch.v1",
            sorted(canonical_selection),
        ),
        selection_mode=selection_mode,
        requested_count=requested_count,
        selected_count=len(canonical_selection),
        depth_before=depth_before,
        depth_after=None,
        status_counts={},
        classification_counts={},
        reason_counts={},
        index_repair_required_count=0,
        warning_counts={},
        prepared_at=prepared_at or _utc_timestamp(),
        completed_at=None,
        state="prepared",
        result_digest=None,
    )
    if not _valid_receipt(receipt):
        raise TypeError("INVALID_PENDING_BATCH_RECEIPT")
    return receipt


def complete_pending_receipt(
    prepared: PendingBatchReceipt,
    *,
    outcomes: Iterable[PendingReceiptOutcome],
    depth_after: int,
    completed_at: str | None = None,
    batch_warnings: Iterable[str] = (),
) -> PendingBatchReceipt:
    if not _valid_receipt(prepared) or prepared.state != "prepared":
        raise TypeError("INVALID_PREPARED_PENDING_RECEIPT")
    rows = tuple(outcomes)
    status_counts: Counter[str] = Counter()
    classification_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    normalized_batch_warnings = tuple(
        sorted(_public_reason(warning) for warning in batch_warnings)
    )
    warning_counts: Counter[str] = Counter(normalized_batch_warnings)
    canonical_outcomes: list[tuple[object, ...]] = []
    index_repair_required_count = 0
    for outcome in rows:
        if type(outcome) is not PendingReceiptOutcome or not outcome.record_id:
            raise TypeError("INVALID_PENDING_RECEIPT_OUTCOME")
        if outcome.status not in _ALLOWED_STATUSES:
            raise TypeError("INVALID_PENDING_RECEIPT_OUTCOME")
        classification = outcome.classification or "unknown"
        if classification not in _ALLOWED_CLASSIFICATIONS:
            raise TypeError("INVALID_PENDING_RECEIPT_OUTCOME")
        reason = _public_reason(outcome.reason)
        warnings = tuple(sorted(_public_reason(warning) for warning in outcome.warnings))
        status_counts[outcome.status] += 1
        classification_counts[classification] += 1
        reason_counts[reason] += 1
        warning_counts.update(warnings)
        index_repair_required_count += int(outcome.index_repair_required)
        canonical_outcomes.append(
            (
                outcome.record_id,
                outcome.status,
                classification,
                reason,
                outcome.index_repair_required,
                warnings,
            )
        )
    completed = replace(
        prepared,
        depth_after=depth_after,
        status_counts=dict(sorted(status_counts.items())),
        classification_counts=dict(sorted(classification_counts.items())),
        reason_counts=dict(sorted(reason_counts.items())),
        index_repair_required_count=index_repair_required_count,
        warning_counts=dict(sorted(warning_counts.items())),
        completed_at=completed_at or _utc_timestamp(),
        state="completed",
        result_digest=_canonical_digest(
            b"amh.pending.result.v1",
            {
                "outcomes": sorted(canonical_outcomes),
                "batch_warnings": normalized_batch_warnings,
            },
        ),
    )
    if not _valid_receipt(completed):
        raise TypeError("INVALID_PENDING_BATCH_RECEIPT")
    return completed


def incomplete_pending_receipt(prepared: PendingBatchReceipt) -> PendingBatchReceipt:
    if not _valid_receipt(prepared) or prepared.state != "prepared":
        raise TypeError("INVALID_PREPARED_PENDING_RECEIPT")
    return replace(prepared, state="incomplete")


def append_pending_receipt(brain_dir: Path, receipt: PendingBatchReceipt) -> None:
    """Append and fsync one prepared/completed receipt, rolling back partial bytes."""

    if not _valid_receipt(receipt) or receipt.state not in {"prepared", "completed"}:
        raise TypeError("INVALID_PENDING_BATCH_RECEIPT")
    payload = (
        json.dumps(
            receipt.to_dict(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    if len(payload) > MAX_PENDING_RECEIPT_LINE_BYTES:
        raise OSError("PENDING_RECEIPT_LINE_TOO_LARGE")
    durable = False
    try:
        with SecureDirectory.open(Path(brain_dir)) as brain:
            with brain.child("runtime", create=True) as runtime:
                with _PROCESS_LOCK, _locked_file(runtime, ".pending-receipts.lock"):
                    descriptor, created = runtime.open_or_create_file(
                        "pending-apply-receipts.jsonl",
                        os.O_RDWR | os.O_APPEND,
                    )
                    try:
                        os.fchmod(descriptor, 0o600)
                        original_length = os.fstat(descriptor).st_size
                        if original_length + len(payload) > MAX_PENDING_RECEIPT_LEDGER_BYTES:
                            raise OSError("PENDING_RECEIPT_LEDGER_TOO_LARGE")
                        existing = _read_receipts_from_descriptor(descriptor)
                        candidate_health = _receipt_sequence_health((*existing, receipt))
                        if candidate_health.status != "healthy":
                            raise OSError("PENDING_RECEIPT_LEDGER_CORRUPT")
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
                                    append_error.add_note("PENDING_RECEIPT_ROLLBACK_FAILED")
                                    raise append_error
                                raise PendingReceiptLedgerRollbackError(
                                    "PENDING_RECEIPT_ROLLBACK_FAILED"
                                ) from rollback_error
                            raise
                    finally:
                        try:
                            os.close(descriptor)
                        except BaseException:
                            _log.warning("PENDING_RECEIPT_HOUSEKEEPING_FAILED")
    except BaseException:
        if durable:
            _log.warning("PENDING_RECEIPT_HOUSEKEEPING_FAILED")
            return
        raise


def read_pending_receipt_ledger_health(brain_dir: Path) -> PendingReceiptLedgerHealth:
    """Read ledger health without creating runtime state or exposing batch IDs."""

    receipts: list[PendingBatchReceipt] = []
    try:
        with SecureDirectory.open(Path(brain_dir)) as brain:
            with brain.child("runtime") as runtime:
                descriptor, _ = runtime.open_file(
                    "pending-apply-receipts.jsonl",
                    os.O_RDONLY,
                )
                try:
                    opened = os.fstat(descriptor)
                    if (
                        not stat.S_ISREG(opened.st_mode)
                        or stat.S_IMODE(opened.st_mode) != 0o600
                        or opened.st_size > MAX_PENDING_RECEIPT_LEDGER_BYTES
                    ):
                        return PendingReceiptLedgerHealth("corrupt")
                    with os.fdopen(descriptor, "rb", buffering=0) as handle:
                        descriptor = -1
                        total_bytes = 0
                        while True:
                            raw = handle.readline(MAX_PENDING_RECEIPT_LINE_BYTES + 1)
                            if not raw:
                                break
                            total_bytes += len(raw)
                            if (
                                len(raw) > MAX_PENDING_RECEIPT_LINE_BYTES
                                or total_bytes > MAX_PENDING_RECEIPT_LEDGER_BYTES
                                or len(receipts) >= MAX_PENDING_RECEIPT_RECORDS
                            ):
                                return PendingReceiptLedgerHealth("corrupt")
                            receipt = _parse_receipt(raw)
                            if receipt is None or receipt.state == "incomplete":
                                return PendingReceiptLedgerHealth("corrupt")
                            receipts.append(receipt)
                        after = os.fstat(handle.fileno())
                        if not _same_file_state(opened, after):
                            return PendingReceiptLedgerHealth("unavailable")
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)
    except FileNotFoundError:
        return PendingReceiptLedgerHealth("not_present")
    except (OSError, UnicodeError):
        return PendingReceiptLedgerHealth("unavailable")
    return _receipt_sequence_health(receipts)


def _receipt_sequence_health(
    receipts: Iterable[PendingBatchReceipt],
) -> PendingReceiptLedgerHealth:
    prepared: dict[str, PendingBatchReceipt] = {}
    completed: set[str] = set()
    count = 0
    for receipt in receipts:
        count += 1
        if receipt.state == "prepared":
            if receipt.batch_id in prepared:
                return PendingReceiptLedgerHealth("corrupt")
            prepared[receipt.batch_id] = receipt
            continue
        original = prepared.get(receipt.batch_id)
        if (
            receipt.state != "completed"
            or original is None
            or receipt.batch_id in completed
            or receipt.batch_digest != original.batch_digest
            or receipt.selection_mode != original.selection_mode
            or receipt.requested_count != original.requested_count
            or receipt.selected_count != original.selected_count
            or receipt.depth_before != original.depth_before
            or receipt.prepared_at != original.prepared_at
        ):
            return PendingReceiptLedgerHealth("corrupt")
        completed.add(receipt.batch_id)
    return PendingReceiptLedgerHealth(
        "healthy",
        record_count=count,
        incomplete_count=len(set(prepared) - completed),
    )


def _parse_receipt(raw: bytes) -> PendingBatchReceipt | None:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or set(data) != PENDING_RECEIPT_FIELDS:
        return None
    try:
        receipt = PendingBatchReceipt(**data)
    except TypeError:
        return None
    return receipt if _valid_receipt(receipt) else None


def _read_receipts_from_descriptor(descriptor: int) -> tuple[PendingBatchReceipt, ...]:
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_size > MAX_PENDING_RECEIPT_LEDGER_BYTES
    ):
        raise OSError("PENDING_RECEIPT_LEDGER_CORRUPT")
    duplicate = os.dup(descriptor)
    receipts: list[PendingBatchReceipt] = []
    try:
        os.lseek(duplicate, 0, os.SEEK_SET)
        with os.fdopen(duplicate, "rb", buffering=0) as handle:
            duplicate = -1
            total_bytes = 0
            while True:
                raw = handle.readline(MAX_PENDING_RECEIPT_LINE_BYTES + 1)
                if not raw:
                    break
                total_bytes += len(raw)
                if (
                    len(raw) > MAX_PENDING_RECEIPT_LINE_BYTES
                    or total_bytes > MAX_PENDING_RECEIPT_LEDGER_BYTES
                    or len(receipts) >= MAX_PENDING_RECEIPT_RECORDS
                ):
                    raise OSError("PENDING_RECEIPT_LEDGER_CORRUPT")
                receipt = _parse_receipt(raw)
                if receipt is None or receipt.state == "incomplete":
                    raise OSError("PENDING_RECEIPT_LEDGER_CORRUPT")
                receipts.append(receipt)
    finally:
        if duplicate >= 0:
            os.close(duplicate)
        os.lseek(descriptor, 0, os.SEEK_END)
    if _receipt_sequence_health(receipts).status != "healthy":
        raise OSError("PENDING_RECEIPT_LEDGER_CORRUPT")
    return tuple(receipts)


def _valid_receipt(receipt: object) -> bool:
    if type(receipt) is not PendingBatchReceipt:
        return False
    assert isinstance(receipt, PendingBatchReceipt)
    if (
        receipt.schema_version != 1
        or _HEX_32.fullmatch(receipt.batch_id) is None
        or _HEX_64.fullmatch(receipt.batch_digest) is None
        or receipt.selection_mode not in {"explicit", "safe_only"}
        or any(
            type(value) is not int or value < 0
            for value in (
                receipt.requested_count,
                receipt.selected_count,
                receipt.depth_before,
                receipt.index_repair_required_count,
            )
        )
        or (receipt.depth_after is not None and (type(receipt.depth_after) is not int or receipt.depth_after < 0))
        or not _valid_timestamp(receipt.prepared_at)
        or receipt.state not in {"prepared", "completed", "incomplete"}
    ):
        return False
    if not _valid_counts(receipt.status_counts, _ALLOWED_STATUSES):
        return False
    if not _valid_counts(receipt.classification_counts, _ALLOWED_CLASSIFICATIONS):
        return False
    if not _valid_counts(receipt.reason_counts, _ALLOWED_REASONS):
        return False
    if not _valid_counts(receipt.warning_counts, _ALLOWED_REASONS):
        return False
    if receipt.state in {"prepared", "incomplete"}:
        return (
            receipt.depth_after is None
            and not receipt.status_counts
            and not receipt.classification_counts
            and not receipt.reason_counts
            and receipt.index_repair_required_count == 0
            and not receipt.warning_counts
            and receipt.completed_at is None
            and receipt.result_digest is None
        )
    return (
        receipt.depth_after is not None
        and receipt.completed_at is not None
        and _valid_timestamp(receipt.completed_at)
        and receipt.result_digest is not None
        and _HEX_64.fullmatch(receipt.result_digest) is not None
    )


def _valid_counts(values: object, allowed: frozenset[str]) -> bool:
    return (
        isinstance(values, dict)
        and len(values) <= len(allowed)
        and all(
            type(key) is str
            and key in allowed
            and type(value) is int
            and value >= 0
            for key, value in values.items()
        )
    )


def _valid_timestamp(value: object) -> bool:
    if type(value) is not str or len(value) > 64:
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)


def _same_file_state(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and left.st_ctime_ns == right.st_ctime_ns
    )


@contextmanager
def _locked_file(runtime: SecureDirectory, name: str) -> Iterator[BinaryIO]:
    descriptor, created = runtime.open_or_create_file(name, os.O_RDWR)
    os.fchmod(descriptor, 0o600)
    handle = os.fdopen(descriptor, "r+b", buffering=0)
    try:
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        if created:
            runtime.fsync()
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield handle
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except BaseException:
                _log.warning("PENDING_RECEIPT_LOCK_HOUSEKEEPING_FAILED")
    finally:
        try:
            handle.close()
        except BaseException:
            _log.warning("PENDING_RECEIPT_LOCK_HOUSEKEEPING_FAILED")


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("PENDING_RECEIPT_WRITE_FAILED")
        remaining = remaining[written:]


__all__ = [
    "PENDING_RECEIPT_FIELDS",
    "PendingBatchReceipt",
    "PendingReceiptLedgerHealth",
    "PendingReceiptOutcome",
    "PendingReceiptSelection",
    "append_pending_receipt",
    "complete_pending_receipt",
    "incomplete_pending_receipt",
    "prepare_pending_receipt",
    "read_pending_receipt_ledger_health",
]
