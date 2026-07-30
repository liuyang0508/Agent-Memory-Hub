"""Digest-bound, recoverable lifecycle transitions for signal memories."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from agent_brain.contracts.memory_enums import memory_enum_value
from agent_brain.contracts.memory_item import (
    MemoryItem,
    SignalLifecycleState,
    is_valid_memory_item_id,
)
from agent_brain.memory.governance.lifecycle_ledger import lifecycle_transaction_lock
from agent_brain.memory.governance.lifecycle_snapshot import (
    LifecycleSnapshotError,
    LifecycleSnapshotStore,
)
from agent_brain.memory.governance.signal_state import (
    LIFECYCLE_SIGNAL_TAGS,
    assess_signal_state,
)
from agent_brain.memory.store.durable_fs import SecureDirectory
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.store.pending import append_dirty_index_marker


SignalTransitionAction = Literal["resolve", "obsolete", "defer", "reopen"]

_ACTIONS = frozenset({"resolve", "obsolete", "defer", "reopen"})
_RESOLUTION_TYPES = frozenset({"artifact", "decision", "episode", "fact"})
_TRANSACTION_ID = re.compile(r"[0-9a-f]{32}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SNAPSHOT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_RECEIPT_NAME = "signal-state-receipts.jsonl"
_MAX_LEDGER_BYTES = 16 * 1024 * 1024
_MAX_LEDGER_LINE_BYTES = 128 * 1024
_MAX_LEDGER_RECORDS = 100_000


@dataclass(frozen=True)
class SignalTransitionResult:
    action: SignalTransitionAction
    item_id: str
    status: str
    reason: str
    dry_run: bool
    intent_sha256: str | None = None
    before_sha256: str | None = None
    transaction_id: str | None = None
    snapshot: str | None = None
    signal_state: str | None = None
    deferred_until: str | None = None
    resolution_item_id: str | None = None
    index_repair_required: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SignalReceiptHealth:
    status: str = "healthy"
    record_count: int = 0
    incomplete_count: int = 0
    completed_count: int = 0
    rolled_back_count: int = 0


@dataclass(frozen=True)
class SignalRecoveryResult:
    transaction_id: str
    status: str
    reason: str
    dry_run: bool
    item_id: str | None = None
    snapshot: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def transition_signal_state(
    *,
    brain_dir: Path,
    store: ItemsStore,
    item_id: str,
    action: SignalTransitionAction,
    apply: bool = False,
    expected_intent_sha256: str | None = None,
    resolution_item_id: str | None = None,
    defer_days: int | None = None,
    reason: str | None = None,
    index: Any | None = None,
    now: datetime | None = None,
) -> SignalTransitionResult:
    """Preview or apply one exact Signal transition."""

    brain_dir = Path(brain_dir)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    normalized_reason = _normalize_reason(reason)
    invalid = _validate_request(
        item_id=item_id,
        action=action,
        resolution_item_id=resolution_item_id,
        defer_days=defer_days,
        reason=normalized_reason,
    )
    if invalid:
        return _blocked(action, item_id, invalid, not apply)
    records, ledger_status = _read_receipt_records(brain_dir)
    if ledger_status != "healthy" or _incomplete_transaction_count(records):
        return _blocked(
            action,
            item_id,
            "SIGNAL_STATE_LEDGER_UNAVAILABLE",
            not apply,
        )
    try:
        raw, item = _read_exact_item(store, item_id)
        resolution_raw, resolution_item = _read_resolution(
            store, resolution_item_id
        )
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return _blocked(action, item_id, "SIGNAL_ITEMS_UNAVAILABLE", not apply)
    request_error = _validate_transition(
        item=item,
        action=action,
        resolution_item=resolution_item,
        defer_days=defer_days,
        now=current,
    )
    before_sha256 = hashlib.sha256(raw).hexdigest()
    if request_error:
        return _blocked(
            action,
            item_id,
            request_error,
            not apply,
            before_sha256=before_sha256,
        )
    resolution_sha256 = (
        hashlib.sha256(resolution_raw).hexdigest()
        if resolution_raw is not None
        else None
    )
    intent_sha256 = _intent_sha256(
        action=action,
        item_id=item_id,
        before_sha256=before_sha256,
        resolution_item_id=resolution_item_id,
        resolution_sha256=resolution_sha256,
        defer_days=defer_days,
        reason=normalized_reason,
    )
    if not apply:
        return SignalTransitionResult(
            action,
            item_id,
            "ready",
            "SIGNAL_TRANSITION_READY",
            True,
            intent_sha256=intent_sha256,
            before_sha256=before_sha256,
            signal_state=assess_signal_state(item, now=current).state,
            resolution_item_id=resolution_item_id,
        )
    if expected_intent_sha256 != intent_sha256:
        return _blocked(
            action,
            item_id,
            "SIGNAL_TRANSITION_CHANGED",
            False,
            intent_sha256=intent_sha256,
            before_sha256=before_sha256,
        )

    transaction_id = secrets.token_hex(16)
    snapshot: str | None = None
    before_bytes: bytes | None = None
    prepared_written = False
    index_repair_required = False
    deferred_until: datetime | None = None
    lock_ids = [item_id]
    if resolution_item_id is not None:
        lock_ids.append(resolution_item_id)
    try:
        with (
            lifecycle_transaction_lock(brain_dir),
            store.locked_catalog(),
            store.locked_items(lock_ids) as locked,
        ):
            records, ledger_status = _read_receipt_records(brain_dir)
            if ledger_status != "healthy" or _incomplete_transaction_count(records):
                return _blocked(
                    action,
                    item_id,
                    "SIGNAL_STATE_LEDGER_UNAVAILABLE",
                    False,
                )
            locked_raw = locked.read_bytes(item_id)
            locked_item, _body = locked.get(item_id)
            locked_resolution_raw: bytes | None = None
            locked_resolution_item: MemoryItem | None = None
            if resolution_item_id is not None:
                locked_resolution_raw = locked.read_bytes(resolution_item_id)
                locked_resolution_item, _ = locked.get(resolution_item_id)
            locked_before_sha256 = hashlib.sha256(locked_raw).hexdigest()
            locked_resolution_sha256 = (
                hashlib.sha256(locked_resolution_raw).hexdigest()
                if locked_resolution_raw is not None
                else None
            )
            locked_intent = _intent_sha256(
                action=action,
                item_id=item_id,
                before_sha256=locked_before_sha256,
                resolution_item_id=resolution_item_id,
                resolution_sha256=locked_resolution_sha256,
                defer_days=defer_days,
                reason=normalized_reason,
            )
            if locked_intent != expected_intent_sha256:
                return _blocked(
                    action,
                    item_id,
                    "SIGNAL_TRANSITION_CHANGED",
                    False,
                    intent_sha256=locked_intent,
                    before_sha256=locked_before_sha256,
                )
            request_error = _validate_transition(
                item=locked_item,
                action=action,
                resolution_item=locked_resolution_item,
                defer_days=defer_days,
                now=current,
            )
            if request_error:
                return _blocked(
                    action,
                    item_id,
                    request_error,
                    False,
                    intent_sha256=locked_intent,
                    before_sha256=locked_before_sha256,
                )
            before_bytes = locked_raw
            snapshot = LifecycleSnapshotStore(
                brain_dir, store.items_dir
            ).snapshot_items({item_id: locked_raw})
            signal_state, deferred_until = _target_state(
                action=action,
                changed_at=current,
                defer_days=defer_days,
                resolution_item_id=resolution_item_id,
                reason=normalized_reason,
            )
            tags = _target_tags(locked_item.tags, action)
            refs = locked_item.refs.model_dump()
            if (
                action == "resolve"
                and resolution_item_id is not None
                and resolution_item_id not in refs["mems"]
            ):
                refs["mems"] = [*refs["mems"], resolution_item_id]
            prepared = locked.prepare_update_frontmatter(
                item_id,
                signal_state=signal_state.model_dump(exclude_none=True),
                tags=tags,
                refs=refs,
            )
            _append_receipt(
                brain_dir,
                transaction_id=transaction_id,
                state="prepared",
                action=action,
                item_id=item_id,
                before_sha256=locked_before_sha256,
                intent_sha256=locked_intent,
                after_sha256=None,
                snapshot=snapshot,
                resolution_item_id=resolution_item_id,
                resolution_sha256=locked_resolution_sha256,
                defer_days=defer_days,
                operator_reason=normalized_reason,
                changed_at=current,
                deferred_until=deferred_until,
                outcome="READY",
                index_repair_required=False,
            )
            prepared_written = True
            locked.apply_prepared(prepared)
            if locked.read_bytes(item_id) != prepared.data:
                raise OSError("SIGNAL_TRANSITION_MUTATION_MISMATCH")
            after_sha256 = hashlib.sha256(prepared.data).hexdigest()
            if index is not None:
                try:
                    index.upsert(prepared.updated_item, _body, embedding=None)
                except Exception:  # noqa: BLE001 - Markdown is authoritative.
                    index_repair_required = True
            else:
                index_repair_required = True
            if index_repair_required and not append_dirty_index_marker(
                brain_dir, item_id
            ):
                index_repair_required = True
            _append_receipt(
                brain_dir,
                transaction_id=transaction_id,
                state="completed",
                action=action,
                item_id=item_id,
                before_sha256=locked_before_sha256,
                intent_sha256=locked_intent,
                after_sha256=after_sha256,
                snapshot=snapshot,
                resolution_item_id=resolution_item_id,
                resolution_sha256=locked_resolution_sha256,
                defer_days=defer_days,
                operator_reason=normalized_reason,
                changed_at=current,
                deferred_until=deferred_until,
                outcome="OK",
                index_repair_required=index_repair_required,
            )
    except (LifecycleSnapshotError, OSError, TypeError, ValueError):
        rollback_status = (
            _rollback_transition(
                brain_dir=brain_dir,
                store=store,
                transaction_id=transaction_id,
                item_id=item_id,
                before_bytes=before_bytes,
                snapshot=snapshot,
            )
            if prepared_written
            else "SIGNAL_TRANSITION_PREPARE_FAILED"
        )
        return SignalTransitionResult(
            action,
            item_id,
            "blocked",
            rollback_status,
            False,
            intent_sha256=intent_sha256,
            before_sha256=before_sha256,
            transaction_id=transaction_id,
            snapshot=snapshot,
            resolution_item_id=resolution_item_id,
            index_repair_required=before_bytes is not None,
        )
    return SignalTransitionResult(
        action,
        item_id,
        "applied",
        "SIGNAL_TRANSITION_APPLIED",
        False,
        intent_sha256=intent_sha256,
        before_sha256=before_sha256,
        transaction_id=transaction_id,
        snapshot=snapshot,
        signal_state=signal_state.status,
        deferred_until=(
            deferred_until.isoformat() if deferred_until is not None else None
        ),
        resolution_item_id=resolution_item_id,
        index_repair_required=index_repair_required,
    )


def read_signal_receipt_health(brain_dir: Path) -> SignalReceiptHealth:
    records, status = _read_receipt_records(Path(brain_dir))
    if status != "healthy":
        return SignalReceiptHealth(status=status, record_count=len(records))
    incomplete = _incomplete_transaction_count(records)
    return SignalReceiptHealth(
        status="incomplete" if incomplete else "healthy",
        record_count=len(records),
        incomplete_count=incomplete,
        completed_count=sum(row["state"] == "completed" for row in records),
        rolled_back_count=sum(row["state"] == "rolled_back" for row in records),
    )


def recover_signal_transaction(
    *,
    brain_dir: Path,
    store: ItemsStore,
    transaction_id: str,
    apply: bool = False,
) -> SignalRecoveryResult:
    """Preview or restore one interrupted Signal transition."""

    if _TRANSACTION_ID.fullmatch(transaction_id) is None:
        return SignalRecoveryResult(
            transaction_id, "blocked", "INVALID_TRANSACTION_ID", not apply
        )
    records, status = _read_receipt_records(Path(brain_dir))
    if status != "healthy":
        return SignalRecoveryResult(
            transaction_id,
            "blocked",
            "SIGNAL_STATE_LEDGER_UNAVAILABLE",
            not apply,
        )
    scoped = [row for row in records if row["transaction_id"] == transaction_id]
    if not scoped:
        return SignalRecoveryResult(
            transaction_id, "blocked", "TRANSACTION_NOT_FOUND", not apply
        )
    prepared = scoped[0]
    if len(scoped) != 1 or prepared["state"] != "prepared":
        return SignalRecoveryResult(
            transaction_id,
            "blocked",
            "TRANSACTION_ALREADY_TERMINAL",
            not apply,
            prepared["item_id"],
            prepared["snapshot"],
        )
    if not apply:
        return SignalRecoveryResult(
            transaction_id,
            "ready",
            "SIGNAL_RECOVERY_READY",
            True,
            prepared["item_id"],
            prepared["snapshot"],
        )
    item_id = prepared["item_id"]
    try:
        with (
            lifecycle_transaction_lock(Path(brain_dir)),
            store.locked_catalog(),
            store.locked_items([item_id]) as locked,
        ):
            LifecycleSnapshotStore(
                Path(brain_dir), store.items_dir
            ).restore_items(prepared["snapshot"], [item_id])
            restored = locked.read_bytes(item_id)
            if hashlib.sha256(restored).hexdigest() != prepared["before_sha256"]:
                raise OSError("SIGNAL_RECOVERY_MISMATCH")
            append_dirty_index_marker(Path(brain_dir), item_id)
            _append_receipt_from_prepared(
                Path(brain_dir),
                prepared,
                state="rolled_back",
                after_sha256=hashlib.sha256(restored).hexdigest(),
                outcome="RECOVERED",
                index_repair_required=True,
            )
    except (LifecycleSnapshotError, OSError, TypeError, ValueError):
        return SignalRecoveryResult(
            transaction_id,
            "blocked",
            "SIGNAL_RECOVERY_FAILED",
            False,
            item_id,
            prepared["snapshot"],
        )
    return SignalRecoveryResult(
        transaction_id,
        "recovered",
        "SIGNAL_TRANSACTION_ROLLED_BACK",
        False,
        item_id,
        prepared["snapshot"],
    )


def _validate_request(
    *,
    item_id: str,
    action: str,
    resolution_item_id: str | None,
    defer_days: int | None,
    reason: str | None,
) -> str | None:
    if not is_valid_memory_item_id(item_id) or action not in _ACTIONS:
        return "INVALID_SIGNAL_TRANSITION"
    if resolution_item_id is not None and (
        action != "resolve"
        or not is_valid_memory_item_id(resolution_item_id)
        or resolution_item_id == item_id
    ):
        return "INVALID_RESOLUTION_ITEM"
    if action == "defer":
        if (
            isinstance(defer_days, bool)
            or not isinstance(defer_days, int)
            or not 1 <= defer_days <= 365
        ):
            return "INVALID_DEFER_DAYS"
    elif defer_days is not None:
        return "INVALID_DEFER_DAYS"
    if reason is not None and len(reason) > 240:
        return "INVALID_TRANSITION_REASON"
    if reason is not None and any(ord(character) < 32 for character in reason):
        return "INVALID_TRANSITION_REASON"
    return None


def _validate_transition(
    *,
    item: MemoryItem,
    action: str,
    resolution_item: MemoryItem | None,
    defer_days: int | None,
    now: datetime,
) -> str | None:
    del defer_days
    if memory_enum_value(item.type) != "signal" or item.superseded_by:
        return "NOT_ACTIVE_SIGNAL"
    state = assess_signal_state(item, now=now).state
    if action == "reopen" and state == "open":
        return "SIGNAL_ALREADY_OPEN"
    if action != "reopen" and state in {"resolved", "obsolete"}:
        return "SIGNAL_ALREADY_TERMINAL"
    if resolution_item is not None:
        if (
            memory_enum_value(resolution_item.type) not in _RESOLUTION_TYPES
            or resolution_item.superseded_by
            or resolution_item.tenant_id != item.tenant_id
            or resolution_item.project != item.project
        ):
            return "INVALID_RESOLUTION_ITEM"
    return None


def _target_state(
    *,
    action: str,
    changed_at: datetime,
    defer_days: int | None,
    resolution_item_id: str | None,
    reason: str | None,
) -> tuple[SignalLifecycleState, datetime | None]:
    deadline = (
        changed_at + timedelta(days=int(defer_days))
        if action == "defer" and defer_days is not None
        else None
    )
    status = {
        "resolve": "resolved",
        "obsolete": "obsolete",
        "defer": "deferred",
        "reopen": "open",
    }[action]
    return (
        SignalLifecycleState(
            status=status,
            changed_at=changed_at,
            deferred_until=deadline,
            resolution_item_id=(
                resolution_item_id if action == "resolve" else None
            ),
            reason=reason,
        ),
        deadline,
    )


def _target_tags(tags: Iterable[str], action: str) -> list[str]:
    retained = {
        tag
        for tag in tags
        if isinstance(tag, str)
        and tag.strip()
        and tag.strip().casefold() not in LIFECYCLE_SIGNAL_TAGS
    }
    canonical = {
        "resolve": "signal-resolved",
        "obsolete": "signal-obsolete",
        "defer": "signal-deferred",
        "reopen": "signal-open",
    }[action]
    return sorted({*retained, canonical})


def _normalize_reason(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    return normalized or None


def _intent_sha256(
    *,
    action: str,
    item_id: str,
    before_sha256: str,
    resolution_item_id: str | None,
    resolution_sha256: str | None,
    defer_days: int | None,
    reason: str | None,
) -> str:
    payload = json.dumps(
        {
            "action": action,
            "before_sha256": before_sha256,
            "defer_days": defer_days,
            "item_id": item_id,
            "reason": reason,
            "resolution_item_id": resolution_item_id,
            "resolution_sha256": resolution_sha256,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _read_exact_item(store: ItemsStore, item_id: str) -> tuple[bytes, MemoryItem]:
    with store.locked_catalog(), store.locked_items([item_id]) as locked:
        raw = locked.read_bytes(item_id)
        item, _body = locked.get(item_id)
    return raw, item


def _read_resolution(
    store: ItemsStore,
    item_id: str | None,
) -> tuple[bytes | None, MemoryItem | None]:
    if item_id is None:
        return None, None
    raw, item = _read_exact_item(store, item_id)
    return raw, item


def _rollback_transition(
    *,
    brain_dir: Path,
    store: ItemsStore,
    transaction_id: str,
    item_id: str,
    before_bytes: bytes | None,
    snapshot: str | None,
) -> str:
    if before_bytes is None or snapshot is None:
        return "SIGNAL_TRANSITION_SNAPSHOT_FAILED"
    try:
        with (
            lifecycle_transaction_lock(brain_dir),
            store.locked_catalog(),
            store.locked_items([item_id]) as locked,
        ):
            locked.restore_raw(item_id, before_bytes)
            restored = locked.read_bytes(item_id)
            if restored != before_bytes:
                raise OSError("SIGNAL_ROLLBACK_MISMATCH")
            append_dirty_index_marker(brain_dir, item_id)
            records, status = _read_receipt_records(brain_dir)
            if status != "healthy":
                raise OSError("SIGNAL_STATE_LEDGER_UNAVAILABLE")
            prepared = next(
                row
                for row in records
                if row["transaction_id"] == transaction_id
                and row["state"] == "prepared"
            )
            _append_receipt_from_prepared(
                brain_dir,
                prepared,
                state="rolled_back",
                after_sha256=hashlib.sha256(restored).hexdigest(),
                outcome="MUTATION_FAILED",
                index_repair_required=True,
            )
    except (OSError, StopIteration, TypeError, ValueError):
        return "SIGNAL_TRANSITION_ROLLBACK_FAILED"
    return "SIGNAL_TRANSITION_ROLLED_BACK"


def _append_receipt(
    brain_dir: Path,
    *,
    transaction_id: str,
    state: str,
    action: str,
    item_id: str,
    before_sha256: str,
    intent_sha256: str,
    after_sha256: str | None,
    snapshot: str,
    resolution_item_id: str | None,
    resolution_sha256: str | None,
    defer_days: int | None,
    operator_reason: str | None,
    changed_at: datetime,
    deferred_until: datetime | None,
    outcome: str,
    index_repair_required: bool,
) -> None:
    record = {
        "schema_version": 1,
        "transaction_id": transaction_id,
        "state": state,
        "action": action,
        "item_id": item_id,
        "before_sha256": before_sha256,
        "intent_sha256": intent_sha256,
        "after_sha256": after_sha256,
        "snapshot": snapshot,
        "resolution_item_id": resolution_item_id,
        "resolution_sha256": resolution_sha256,
        "defer_days": defer_days,
        "operator_reason": operator_reason,
        "changed_at": changed_at.isoformat(),
        "deferred_until": (
            deferred_until.isoformat() if deferred_until is not None else None
        ),
        "outcome": outcome,
        "index_repair_required": index_repair_required,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if not _valid_receipt_record(record):
        raise TypeError("INVALID_SIGNAL_STATE_RECEIPT")
    _append_receipt_payload(brain_dir, record)


def _append_receipt_from_prepared(
    brain_dir: Path,
    prepared: Mapping[str, Any],
    *,
    state: str,
    after_sha256: str,
    outcome: str,
    index_repair_required: bool,
) -> None:
    record = dict(prepared)
    record.update(
        {
            "state": state,
            "after_sha256": after_sha256,
            "outcome": outcome,
            "index_repair_required": index_repair_required,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    if not _valid_receipt_record(record):
        raise TypeError("INVALID_SIGNAL_STATE_RECEIPT")
    _append_receipt_payload(brain_dir, record)


def _append_receipt_payload(
    brain_dir: Path,
    record: Mapping[str, Any],
) -> None:
    payload = (
        json.dumps(
            record,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode()
    if len(payload) > _MAX_LEDGER_LINE_BYTES:
        raise OSError("SIGNAL_STATE_RECEIPT_TOO_LARGE")
    with SecureDirectory.open(brain_dir) as brain:
        with brain.child("runtime", create=True) as runtime:
            descriptor, created = runtime.open_or_create_file(
                _RECEIPT_NAME, os.O_WRONLY | os.O_APPEND
            )
            original_length = os.fstat(descriptor).st_size
            try:
                if original_length + len(payload) > _MAX_LEDGER_BYTES:
                    raise OSError("SIGNAL_STATE_LEDGER_TOO_LARGE")
                os.fchmod(descriptor, 0o600)
                view = memoryview(payload)
                try:
                    while view:
                        written = os.write(descriptor, view)
                        if written <= 0:
                            raise OSError("SIGNAL_STATE_RECEIPT_WRITE_FAILED")
                        view = view[written:]
                    os.fsync(descriptor)
                except BaseException:
                    os.ftruncate(descriptor, original_length)
                    os.fsync(descriptor)
                    raise
            finally:
                os.close(descriptor)
            if created:
                runtime.fsync()


def _read_receipt_records(
    brain_dir: Path,
) -> tuple[list[dict[str, Any]], str]:
    try:
        with SecureDirectory.open(brain_dir) as brain:
            with brain.child("runtime") as runtime:
                descriptor, _ = runtime.open_file(_RECEIPT_NAME, os.O_RDONLY)
                try:
                    opened = os.fstat(descriptor)
                    if opened.st_size > _MAX_LEDGER_BYTES:
                        return [], "corrupt"
                    with os.fdopen(descriptor, "rb") as handle:
                        descriptor = -1
                        raw_lines = handle.readlines()
                        after = os.fstat(handle.fileno())
                    if (
                        opened.st_dev != after.st_dev
                        or opened.st_ino != after.st_ino
                        or opened.st_size != after.st_size
                        or opened.st_mtime_ns != after.st_mtime_ns
                    ):
                        return [], "unavailable"
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)
    except FileNotFoundError:
        return [], "healthy"
    except OSError:
        return [], "unavailable"
    if len(raw_lines) > _MAX_LEDGER_RECORDS:
        return [], "corrupt"
    records: list[dict[str, Any]] = []
    transactions: dict[str, list[dict[str, Any]]] = {}
    for raw in raw_lines:
        if len(raw) > _MAX_LEDGER_LINE_BYTES:
            return records, "corrupt"
        try:
            record = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return records, "corrupt"
        if not _valid_receipt_record(record):
            return records, "corrupt"
        transaction = transactions.setdefault(record["transaction_id"], [])
        if not transaction:
            if record["state"] != "prepared":
                return records, "corrupt"
        elif (
            len(transaction) != 1
            or record["state"] not in {"completed", "rolled_back"}
            or not _same_receipt_binding(transaction[0], record)
        ):
            return records, "corrupt"
        transaction.append(record)
        records.append(record)
    return records, "healthy"


def _same_receipt_binding(
    prepared: Mapping[str, Any],
    terminal: Mapping[str, Any],
) -> bool:
    mutable = {
        "state",
        "after_sha256",
        "outcome",
        "index_repair_required",
        "timestamp",
    }
    return all(
        prepared[key] == terminal[key]
        for key in set(prepared) - mutable
    )


def _valid_receipt_record(record: object) -> bool:
    expected = {
        "schema_version",
        "transaction_id",
        "state",
        "action",
        "item_id",
        "before_sha256",
        "intent_sha256",
        "after_sha256",
        "snapshot",
        "resolution_item_id",
        "resolution_sha256",
        "defer_days",
        "operator_reason",
        "changed_at",
        "deferred_until",
        "outcome",
        "index_repair_required",
        "timestamp",
    }
    if not isinstance(record, dict) or set(record) != expected:
        return False
    if (
        record["schema_version"] != 1
        or _TRANSACTION_ID.fullmatch(str(record["transaction_id"])) is None
        or record["state"] not in {"prepared", "completed", "rolled_back"}
        or record["action"] not in _ACTIONS
        or not is_valid_memory_item_id(record["item_id"])
        or _DIGEST.fullmatch(str(record["before_sha256"])) is None
        or _DIGEST.fullmatch(str(record["intent_sha256"])) is None
        or _SNAPSHOT_ID.fullmatch(str(record["snapshot"])) is None
        or not isinstance(record["outcome"], str)
        or not isinstance(record["index_repair_required"], bool)
    ):
        return False
    resolution_id = record["resolution_item_id"]
    resolution_sha = record["resolution_sha256"]
    if (resolution_id is None) != (resolution_sha is None):
        return False
    if resolution_id is not None and (
        record["action"] != "resolve"
        or not is_valid_memory_item_id(resolution_id)
        or _DIGEST.fullmatch(str(resolution_sha)) is None
    ):
        return False
    if record["operator_reason"] is not None and (
        not isinstance(record["operator_reason"], str)
        or len(record["operator_reason"]) > 240
        or any(ord(character) < 32 for character in record["operator_reason"])
    ):
        return False
    if record["action"] == "defer":
        if (
            isinstance(record["defer_days"], bool)
            or not isinstance(record["defer_days"], int)
            or not 1 <= record["defer_days"] <= 365
            or record["deferred_until"] is None
        ):
            return False
    elif record["defer_days"] is not None or record["deferred_until"] is not None:
        return False
    try:
        changed = datetime.fromisoformat(record["changed_at"])
        timestamp = datetime.fromisoformat(record["timestamp"])
        deadline = (
            datetime.fromisoformat(record["deferred_until"])
            if record["deferred_until"] is not None
            else None
        )
    except (TypeError, ValueError):
        return False
    if changed.tzinfo is None or timestamp.tzinfo is None:
        return False
    if deadline is not None and (
        deadline.tzinfo is None or deadline <= changed
    ):
        return False
    after = record["after_sha256"]
    if record["state"] == "prepared":
        return after is None
    return isinstance(after, str) and _DIGEST.fullmatch(after) is not None


def _incomplete_transaction_count(
    records: Iterable[Mapping[str, Any]],
) -> int:
    latest: dict[str, str] = {}
    for row in records:
        latest[row["transaction_id"]] = row["state"]
    return sum(state == "prepared" for state in latest.values())


def _blocked(
    action: Any,
    item_id: str,
    reason: str,
    dry_run: bool,
    *,
    intent_sha256: str | None = None,
    before_sha256: str | None = None,
) -> SignalTransitionResult:
    return SignalTransitionResult(
        action,
        item_id,
        "blocked",
        reason,
        dry_run,
        intent_sha256=intent_sha256,
        before_sha256=before_sha256,
    )


__all__ = [
    "SignalReceiptHealth",
    "SignalRecoveryResult",
    "SignalTransitionAction",
    "SignalTransitionResult",
    "read_signal_receipt_health",
    "recover_signal_transaction",
    "transition_signal_state",
]
