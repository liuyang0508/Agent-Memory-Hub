"""Recoverable, provenance-preserving containment for contradiction cases."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_brain.contracts.memory_item import is_valid_memory_item_id
from agent_brain.memory.governance.contradiction_cases import ContradictionCase
from agent_brain.memory.governance.lifecycle_ledger import lifecycle_transaction_lock
from agent_brain.memory.governance.lifecycle_snapshot import (
    LifecycleSnapshotError,
    LifecycleSnapshotStore,
)
from agent_brain.memory.store.durable_fs import SecureDirectory
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.store.pending import append_dirty_index_marker


_CASE_ID = re.compile(r"contradiction-[0-9a-f]{16}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_TRANSACTION_ID = re.compile(r"[0-9a-f]{32}\Z")
_SNAPSHOT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_RECEIPT_NAME = "contradiction-containment-receipts.jsonl"
_MAX_LEDGER_BYTES = 16 * 1024 * 1024
_MAX_LEDGER_LINE_BYTES = 1024 * 1024
_MAX_LEDGER_RECORDS = 100_000
_CONTAINMENT_TAGS = frozenset({"contested", "needs-review"})


@dataclass(frozen=True)
class ContainmentBaseline:
    item_id: str
    tags: tuple[str, ...]
    confidence: float
    transaction_id: str
    after_sha256: str

    @property
    def binding_sha256(self) -> str:
        payload = json.dumps(
            {
                "item_id": self.item_id,
                "tags": list(self.tags),
                "confidence": self.confidence,
                "transaction_id": self.transaction_id,
                "after_sha256": self.after_sha256,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class ContainmentResult:
    case_id: str
    status: str
    reason: str
    dry_run: bool
    item_ids: tuple[str, ...] = ()
    transaction_id: str | None = None
    snapshot: str | None = None
    index_repair_required: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ContainmentReceiptHealth:
    status: str = "healthy"
    record_count: int = 0
    incomplete_count: int = 0
    completed_count: int = 0
    rolled_back_count: int = 0


@dataclass(frozen=True)
class ContainmentRecoveryResult:
    transaction_id: str
    status: str
    reason: str
    dry_run: bool
    item_ids: tuple[str, ...] = ()
    snapshot: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def contain_contradiction_case(
    *,
    brain_dir: Path,
    store: ItemsStore,
    case: ContradictionCase,
    apply: bool = False,
    index: Any | None = None,
) -> ContainmentResult:
    """Contain every newly exposed item in one exact, recoverable Case transaction."""

    brain_dir = Path(brain_dir)
    if (
        _CASE_ID.fullmatch(case.case_id) is None
        or not 2 <= len(case.item_ids) <= 500
        or tuple(sorted(set(case.item_ids))) != case.item_ids
        or any(not is_valid_memory_item_id(item_id) for item_id in case.item_ids)
        or case.case_id != _case_id_for_items(case.item_ids)
    ):
        return ContainmentResult(
            case.case_id,
            "blocked",
            "INVALID_CONTRADICTION_CASE",
            not apply,
        )
    records, ledger_status = _read_receipt_records(brain_dir)
    if ledger_status != "healthy" or _incomplete_transaction_count(records):
        return ContainmentResult(
            case.case_id,
            "blocked",
            "CONTAINMENT_LEDGER_UNAVAILABLE",
            not apply,
        )
    _baselines, provenance_status = containment_baselines_for_case(
        brain_dir=brain_dir,
        store=store,
        item_ids=case.item_ids,
    )
    if provenance_status != "healthy":
        return ContainmentResult(
            case.case_id,
            "blocked",
            f"CONTAINMENT_PROVENANCE_{provenance_status.upper()}",
            not apply,
        )
    try:
        candidates = _containment_candidates(store, case.item_ids)
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return ContainmentResult(
            case.case_id,
            "blocked",
            "CONTAINMENT_ITEMS_UNAVAILABLE",
            not apply,
        )
    if not candidates:
        return ContainmentResult(
            case.case_id,
            "already_contained",
            "CASE_ALREADY_CONTAINED",
            not apply,
        )
    if not apply:
        return ContainmentResult(
            case.case_id,
            "ready",
            "CASE_CONTAINMENT_READY",
            True,
            candidates,
        )

    transaction_id = secrets.token_hex(16)
    snapshot: str | None = None
    before_bytes: dict[str, bytes] = {}
    prepared_written = False
    index_repair_required = False
    try:
        with (
            lifecycle_transaction_lock(brain_dir),
            store.locked_catalog(),
            store.locked_items(list(case.item_ids)) as locked,
        ):
            records, ledger_status = _read_receipt_records(brain_dir)
            if ledger_status != "healthy" or _incomplete_transaction_count(records):
                return ContainmentResult(
                    case.case_id,
                    "blocked",
                    "CONTAINMENT_LEDGER_UNAVAILABLE",
                    False,
                )
            _baselines, locked_provenance_status = (
                containment_baselines_for_case(
                    brain_dir=brain_dir,
                    store=store,
                    item_ids=case.item_ids,
                )
            )
            if locked_provenance_status != "healthy":
                return ContainmentResult(
                    case.case_id,
                    "blocked",
                    (
                        "CONTAINMENT_PROVENANCE_"
                        f"{locked_provenance_status.upper()}"
                    ),
                    False,
                )
            current_candidates = tuple(
                item_id
                for item_id in case.item_ids
                if not _CONTAINMENT_TAGS.issubset(
                    {tag.lower() for tag in locked.get(item_id)[0].tags}
                )
            )
            if not current_candidates:
                return ContainmentResult(
                    case.case_id,
                    "already_contained",
                    "CASE_ALREADY_CONTAINED",
                    False,
                )
            before_bytes = {
                item_id: locked.read_bytes(item_id)
                for item_id in current_candidates
            }
            before_state = {
                item_id: _item_state(locked.get(item_id)[0])
                for item_id in current_candidates
            }
            snapshot = LifecycleSnapshotStore(
                brain_dir,
                store.items_dir,
            ).snapshot_items(before_bytes)
            prepared = []
            for item_id in current_candidates:
                item, _body = locked.get(item_id)
                prepared.append(
                    locked.prepare_update_frontmatter(
                        item_id,
                        tags=sorted({*item.tags, *_CONTAINMENT_TAGS}),
                        confidence=max(0.3, item.confidence - 0.15),
                    )
                )
            _append_receipt(
                brain_dir,
                transaction_id=transaction_id,
                state="prepared",
                case=case,
                item_ids=current_candidates,
                before_sha256=_sha256_map(before_bytes),
                after_sha256=None,
                before_state=before_state,
                snapshot=snapshot,
                reason="READY",
                index_repair_required=False,
            )
            prepared_written = True
            for mutation in prepared:
                locked.apply_prepared(mutation)
                if locked.read_bytes(mutation.item_id) != mutation.data:
                    raise OSError("CONTAINMENT_MUTATION_MISMATCH")
            after_bytes = {
                item_id: locked.read_bytes(item_id)
                for item_id in current_candidates
            }
            for mutation in prepared:
                item, body = locked.get(mutation.item_id)
                if index is not None:
                    try:
                        index.upsert(item, body, embedding=None)
                        continue
                    except Exception:  # noqa: BLE001 - Markdown is authoritative.
                        index_repair_required = True
                if not append_dirty_index_marker(brain_dir, mutation.item_id):
                    index_repair_required = True
            _append_receipt(
                brain_dir,
                transaction_id=transaction_id,
                state="completed",
                case=case,
                item_ids=current_candidates,
                before_sha256=_sha256_map(before_bytes),
                after_sha256=_sha256_map(after_bytes),
                before_state=before_state,
                snapshot=snapshot,
                reason="OK",
                index_repair_required=index_repair_required,
            )
    except (LifecycleSnapshotError, OSError, TypeError, ValueError):
        rollback_status = (
            _rollback_containment(
                brain_dir=brain_dir,
                store=store,
                transaction_id=transaction_id,
                before_bytes=before_bytes,
                snapshot=snapshot,
            )
            if prepared_written
            else "CONTAINMENT_PREPARE_FAILED"
        )
        return ContainmentResult(
            case.case_id,
            "blocked",
            rollback_status,
            False,
            tuple(sorted(before_bytes)),
            transaction_id,
            snapshot,
            bool(before_bytes),
        )
    return ContainmentResult(
        case.case_id,
        "applied",
        "CASE_CONTAINMENT_APPLIED",
        False,
        tuple(sorted(before_bytes)),
        transaction_id,
        snapshot,
        index_repair_required,
    )


def containment_baselines_for_case(
    *,
    brain_dir: Path,
    store: ItemsStore,
    item_ids: Iterable[str],
) -> tuple[dict[str, ContainmentBaseline], str]:
    """Return exact active baselines, failing closed on missing provenance."""

    canonical = tuple(sorted(set(item_ids)))
    if not canonical:
        return {}, "healthy"
    records, status = _read_receipt_records(Path(brain_dir))
    if status != "healthy" or _incomplete_transaction_count(records):
        return {}, "unavailable"
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        if record["state"] == "completed":
            for item_id in record["item_ids"]:
                latest[item_id] = record
        elif record["state"] == "rolled_back":
            for item_id in record["item_ids"]:
                current = latest.get(item_id)
                if (
                    current is not None
                    and current["transaction_id"] == record["transaction_id"]
                ):
                    latest.pop(item_id, None)
    baselines: dict[str, ContainmentBaseline] = {}
    try:
        with store.locked_catalog(), store.locked_items(list(canonical)) as locked:
            for item_id in canonical:
                item, _body = locked.get(item_id)
                contained = _CONTAINMENT_TAGS.issubset(
                    {tag.lower() for tag in item.tags}
                )
                active_record = latest.get(item_id)
                if active_record is None:
                    if contained:
                        return {}, "missing"
                    continue
                digest = hashlib.sha256(locked.read_bytes(item_id)).hexdigest()
                expected = active_record["after_sha256"][item_id]
                if digest != expected:
                    if contained:
                        return {}, "changed"
                    continue
                state = active_record["before_state"][item_id]
                baselines[item_id] = ContainmentBaseline(
                    item_id=item_id,
                    tags=tuple(state["tags"]),
                    confidence=float(state["confidence"]),
                    transaction_id=active_record["transaction_id"],
                    after_sha256=expected,
                )
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return {}, "unavailable"
    return baselines, "healthy"


def read_containment_receipt_health(brain_dir: Path) -> ContainmentReceiptHealth:
    records, status = _read_receipt_records(Path(brain_dir))
    if status != "healthy":
        return ContainmentReceiptHealth(status=status, record_count=len(records))
    incomplete = _incomplete_transaction_count(records)
    return ContainmentReceiptHealth(
        status="incomplete" if incomplete else "healthy",
        record_count=len(records),
        incomplete_count=incomplete,
        completed_count=sum(record["state"] == "completed" for record in records),
        rolled_back_count=sum(record["state"] == "rolled_back" for record in records),
    )


def recover_containment_transaction(
    *,
    brain_dir: Path,
    store: ItemsStore,
    transaction_id: str,
    apply: bool = False,
) -> ContainmentRecoveryResult:
    """Preview or restore an interrupted containment transaction."""

    if _TRANSACTION_ID.fullmatch(transaction_id) is None:
        return ContainmentRecoveryResult(
            transaction_id,
            "blocked",
            "INVALID_TRANSACTION_ID",
            not apply,
        )
    records, status = _read_receipt_records(Path(brain_dir))
    if status != "healthy":
        return ContainmentRecoveryResult(
            transaction_id,
            "blocked",
            "CONTAINMENT_LEDGER_UNAVAILABLE",
            not apply,
        )
    scoped = [
        record
        for record in records
        if record["transaction_id"] == transaction_id
    ]
    if not scoped:
        return ContainmentRecoveryResult(
            transaction_id,
            "blocked",
            "TRANSACTION_NOT_FOUND",
            not apply,
        )
    prepared = scoped[0]
    if len(scoped) != 1 or prepared["state"] != "prepared":
        return ContainmentRecoveryResult(
            transaction_id,
            "blocked",
            "TRANSACTION_ALREADY_TERMINAL",
            not apply,
            tuple(prepared["item_ids"]),
            prepared["snapshot"],
        )
    if not apply:
        return ContainmentRecoveryResult(
            transaction_id,
            "ready",
            "CONTAINMENT_RECOVERY_READY",
            True,
            tuple(prepared["item_ids"]),
            prepared["snapshot"],
        )
    item_ids = list(prepared["item_ids"])
    try:
        with (
            lifecycle_transaction_lock(Path(brain_dir)),
            store.locked_catalog(),
            store.locked_items(item_ids) as locked,
        ):
            LifecycleSnapshotStore(
                Path(brain_dir),
                store.items_dir,
            ).restore_items(prepared["snapshot"], item_ids)
            restored = {
                item_id: locked.read_bytes(item_id)
                for item_id in item_ids
            }
            if _sha256_map(restored) != prepared["before_sha256"]:
                raise OSError("CONTAINMENT_RECOVERY_MISMATCH")
            for item_id in item_ids:
                append_dirty_index_marker(Path(brain_dir), item_id)
            _append_receipt_from_prepared(
                Path(brain_dir),
                prepared,
                state="rolled_back",
                after_sha256=_sha256_map(restored),
                reason="RECOVERED",
                index_repair_required=True,
            )
    except (LifecycleSnapshotError, OSError, TypeError, ValueError):
        return ContainmentRecoveryResult(
            transaction_id,
            "blocked",
            "CONTAINMENT_RECOVERY_FAILED",
            False,
            tuple(item_ids),
            prepared["snapshot"],
        )
    return ContainmentRecoveryResult(
        transaction_id,
        "recovered",
        "CONTAINMENT_TRANSACTION_ROLLED_BACK",
        False,
        tuple(item_ids),
        prepared["snapshot"],
    )


def _containment_candidates(
    store: ItemsStore,
    item_ids: Iterable[str],
) -> tuple[str, ...]:
    return tuple(
        item_id
        for item_id in item_ids
        if not _CONTAINMENT_TAGS.issubset(
            {tag.lower() for tag in store.get(item_id)[0].tags}
        )
    )


def _item_state(item: Any) -> dict[str, object]:
    return {
        "tags": list(item.tags),
        "confidence": float(item.confidence),
    }


def _rollback_containment(
    *,
    brain_dir: Path,
    store: ItemsStore,
    transaction_id: str,
    before_bytes: Mapping[str, bytes],
    snapshot: str | None,
) -> str:
    if snapshot is None or not before_bytes:
        return "CONTAINMENT_SNAPSHOT_FAILED"
    item_ids = sorted(before_bytes)
    try:
        with (
            lifecycle_transaction_lock(brain_dir),
            store.locked_catalog(),
            store.locked_items(item_ids) as locked,
        ):
            for item_id, data in before_bytes.items():
                locked.restore_raw(item_id, data)
            restored = {
                item_id: locked.read_bytes(item_id)
                for item_id in item_ids
            }
            if restored != dict(before_bytes):
                raise OSError("CONTAINMENT_ROLLBACK_MISMATCH")
            for item_id in item_ids:
                append_dirty_index_marker(brain_dir, item_id)
            records, status = _read_receipt_records(brain_dir)
            if status != "healthy":
                raise OSError("CONTAINMENT_LEDGER_UNAVAILABLE")
            prepared = next(
                record
                for record in records
                if record["transaction_id"] == transaction_id
                and record["state"] == "prepared"
            )
            _append_receipt_from_prepared(
                brain_dir,
                prepared,
                state="rolled_back",
                after_sha256=_sha256_map(restored),
                reason="MUTATION_FAILED",
                index_repair_required=True,
            )
    except (LifecycleSnapshotError, OSError, StopIteration, TypeError, ValueError):
        return "CONTAINMENT_ROLLBACK_FAILED"
    return "CONTAINMENT_MUTATION_ROLLED_BACK"


def _sha256_map(values: Mapping[str, bytes]) -> dict[str, str]:
    return {
        item_id: hashlib.sha256(values[item_id]).hexdigest()
        for item_id in sorted(values)
    }


def _case_id_for_items(item_ids: Iterable[str]) -> str:
    digest = hashlib.sha256("\0".join(item_ids).encode("utf-8")).hexdigest()
    return f"contradiction-{digest[:16]}"


def _append_receipt(
    brain_dir: Path,
    *,
    transaction_id: str,
    state: str,
    case: ContradictionCase,
    item_ids: Iterable[str],
    before_sha256: Mapping[str, str],
    after_sha256: Mapping[str, str] | None,
    before_state: Mapping[str, Mapping[str, object]],
    snapshot: str,
    reason: str,
    index_repair_required: bool,
) -> None:
    record = {
        "schema_version": 1,
        "transaction_id": transaction_id,
        "state": state,
        "case_id": case.case_id,
        "case_item_ids": list(case.item_ids),
        "item_ids": sorted(item_ids),
        "before_sha256": dict(sorted(before_sha256.items())),
        "after_sha256": (
            None if after_sha256 is None else dict(sorted(after_sha256.items()))
        ),
        "before_state": {
            item_id: dict(before_state[item_id])
            for item_id in sorted(before_state)
        },
        "snapshot": snapshot,
        "reason": reason,
        "index_repair_required": index_repair_required,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if not _valid_receipt_record(record):
        raise TypeError("INVALID_CONTAINMENT_RECEIPT")
    _append_receipt_payload(brain_dir, record)


def _append_receipt_from_prepared(
    brain_dir: Path,
    prepared: Mapping[str, Any],
    *,
    state: str,
    after_sha256: Mapping[str, str],
    reason: str,
    index_repair_required: bool,
) -> None:
    record = dict(prepared)
    record.update(
        {
            "state": state,
            "after_sha256": dict(sorted(after_sha256.items())),
            "reason": reason,
            "index_repair_required": index_repair_required,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    if not _valid_receipt_record(record):
        raise TypeError("INVALID_CONTAINMENT_RECEIPT")
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
        raise OSError("CONTAINMENT_RECEIPT_TOO_LARGE")
    with SecureDirectory.open(brain_dir) as brain:
        with brain.child("runtime", create=True) as runtime:
            descriptor, created = runtime.open_or_create_file(
                _RECEIPT_NAME,
                os.O_WRONLY | os.O_APPEND,
            )
            original_length = os.fstat(descriptor).st_size
            try:
                if original_length + len(payload) > _MAX_LEDGER_BYTES:
                    raise OSError("CONTAINMENT_LEDGER_TOO_LARGE")
                os.fchmod(descriptor, 0o600)
                view = memoryview(payload)
                try:
                    while view:
                        written = os.write(descriptor, view)
                        if written <= 0:
                            raise OSError("CONTAINMENT_RECEIPT_WRITE_FAILED")
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
    keys = {
        "schema_version",
        "transaction_id",
        "case_id",
        "case_item_ids",
        "item_ids",
        "before_sha256",
        "before_state",
        "snapshot",
    }
    return all(prepared[key] == terminal[key] for key in keys)


def _valid_receipt_record(record: object) -> bool:
    expected_keys = {
        "schema_version",
        "transaction_id",
        "state",
        "case_id",
        "case_item_ids",
        "item_ids",
        "before_sha256",
        "after_sha256",
        "before_state",
        "snapshot",
        "reason",
        "index_repair_required",
        "timestamp",
    }
    if not isinstance(record, dict) or set(record) != expected_keys:
        return False
    case_item_ids = record["case_item_ids"]
    item_ids = record["item_ids"]
    before = record["before_sha256"]
    after = record["after_sha256"]
    states = record["before_state"]
    if (
        record["schema_version"] != 1
        or _TRANSACTION_ID.fullmatch(str(record["transaction_id"])) is None
        or record["state"] not in {"prepared", "completed", "rolled_back"}
        or _CASE_ID.fullmatch(str(record["case_id"])) is None
        or not isinstance(case_item_ids, list)
        or not 2 <= len(case_item_ids) <= 500
        or case_item_ids != sorted(set(case_item_ids))
        or any(not is_valid_memory_item_id(item_id) for item_id in case_item_ids)
        or record["case_id"] != _case_id_for_items(case_item_ids)
        or not isinstance(item_ids, list)
        or not 1 <= len(item_ids) <= len(case_item_ids)
        or item_ids != sorted(set(item_ids))
        or not set(item_ids).issubset(case_item_ids)
        or not isinstance(before, dict)
        or set(before) != set(item_ids)
        or not isinstance(states, dict)
        or set(states) != set(item_ids)
        or not isinstance(record["snapshot"], str)
        or _SNAPSHOT_ID.fullmatch(record["snapshot"]) is None
        or not isinstance(record["reason"], str)
        or not isinstance(record["index_repair_required"], bool)
        or not isinstance(record["timestamp"], str)
    ):
        return False
    for item_id, digest in before.items():
        if (
            not is_valid_memory_item_id(item_id)
            or not isinstance(digest, str)
            or _DIGEST.fullmatch(digest) is None
        ):
            return False
    for item_id, state in states.items():
        if (
            not is_valid_memory_item_id(item_id)
            or not isinstance(state, dict)
            or set(state) != {"tags", "confidence"}
            or not isinstance(state["tags"], list)
            or len(state["tags"]) > 500
            or any(
                not isinstance(tag, str)
                or len(tag) > 500
                or any(ord(char) < 32 for char in tag)
                for tag in state["tags"]
            )
            or isinstance(state["confidence"], bool)
            or not isinstance(state["confidence"], (int, float))
            or not math.isfinite(float(state["confidence"]))
            or not 0.0 <= float(state["confidence"]) <= 1.0
        ):
            return False
    try:
        timestamp = datetime.fromisoformat(record["timestamp"])
    except ValueError:
        return False
    if timestamp.tzinfo is None:
        return False
    if record["state"] == "prepared":
        return after is None
    return (
        isinstance(after, dict)
        and set(after) == set(item_ids)
        and all(
            isinstance(digest, str) and _DIGEST.fullmatch(digest) is not None
            for digest in after.values()
        )
    )


def _incomplete_transaction_count(
    records: Iterable[Mapping[str, Any]],
) -> int:
    states: dict[str, str] = {}
    for record in records:
        states[record["transaction_id"]] = record["state"]
    return sum(state == "prepared" for state in states.values())


__all__ = [
    "ContainmentBaseline",
    "ContainmentReceiptHealth",
    "ContainmentRecoveryResult",
    "ContainmentResult",
    "contain_contradiction_case",
    "containment_baselines_for_case",
    "read_containment_receipt_health",
    "recover_containment_transaction",
]
