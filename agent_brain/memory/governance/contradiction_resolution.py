"""Digest-bound, recoverable resolution transactions for contradiction cases."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from agent_brain.contracts.memory_item import MemoryItem, is_valid_memory_item_id
from agent_brain.memory.governance.contradiction_cases import ContradictionCase
from agent_brain.memory.governance.contradiction_containment import (
    ContainmentBaseline,
    containment_baselines_for_case,
)
from agent_brain.memory.governance.drift import DriftDetector
from agent_brain.memory.governance.lifecycle_ledger import lifecycle_transaction_lock
from agent_brain.memory.governance.lifecycle_snapshot import (
    LifecycleSnapshotError,
    LifecycleSnapshotStore,
)
from agent_brain.memory.governance.supersession import SupersessionService
from agent_brain.memory.store.durable_fs import (
    SecureDirectory,
    lifecycle_mutation_capability,
)
from agent_brain.memory.store.items_store import ItemsStore, PreparedItemMutation
from agent_brain.memory.store.pending import append_dirty_index_marker


ContradictionResolutionAction = Literal[
    "select_authority",
    "merge",
    "coexist",
    "dismiss",
    "defer",
]
_CASE_ID = re.compile(r"contradiction-[0-9a-f]{16}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_TRANSACTION_ID = re.compile(r"[0-9a-f]{32}\Z")
_SNAPSHOT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_RECEIPT_NAME = "contradiction-case-receipts.jsonl"
_MAX_LEDGER_BYTES = 16 * 1024 * 1024
_MAX_LEDGER_LINE_BYTES = 1024 * 1024
_MAX_LEDGER_RECORDS = 100_000


@dataclass(frozen=True)
class ContradictionCaseView:
    case_id: str
    item_ids: tuple[str, ...]
    pair_count: int
    confidence: float
    evidence: tuple[str, ...]
    status: str
    resolution_action: str | None = None
    target_item_id: str | None = None
    resolved_at: str | None = None
    deferred_until: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ContradictionCaseInventory:
    status: str
    open_count: int
    resolved_count: int
    deferred_count: int
    cases: tuple[ContradictionCaseView, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "open_count": self.open_count,
            "resolved_count": self.resolved_count,
            "deferred_count": self.deferred_count,
            "cases": [case.to_dict() for case in self.cases],
        }


@dataclass(frozen=True)
class ContradictionResolutionResult:
    case_id: str
    action: ContradictionResolutionAction
    status: str
    reason: str
    dry_run: bool
    expected_intent_sha256: str
    item_sha256: dict[str, str]
    target_item_id: str | None = None
    defer_days: int | None = None
    deferred_until: str | None = None
    transaction_id: str | None = None
    snapshot: str | None = None
    index_repair_required: bool = False
    containment_restored_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ContradictionReceiptHealth:
    status: str = "healthy"
    record_count: int = 0
    incomplete_count: int = 0
    completed_count: int = 0
    rolled_back_count: int = 0


@dataclass(frozen=True)
class ContradictionRecoveryResult:
    transaction_id: str
    status: str
    reason: str
    dry_run: bool
    item_ids: tuple[str, ...] = ()
    snapshot: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_contradiction_case_inventory(
    *,
    brain_dir: Path,
    store: ItemsStore,
    cases: Iterable[ContradictionCase] | None = None,
    now: datetime | None = None,
) -> ContradictionCaseInventory:
    """Classify current cases as open, resolved, or temporarily deferred."""

    current_cases = tuple(
        cases
        if cases is not None
        else _current_cases(store)
    )
    records, ledger_status = _read_receipt_records(Path(brain_dir))
    if ledger_status == "healthy" and _incomplete_transaction_count(records):
        ledger_status = "incomplete"
    if ledger_status != "healthy":
        unavailable_views = tuple(
            _open_case_view(case) for case in current_cases
        )
        return ContradictionCaseInventory(
            ledger_status,
            len(unavailable_views),
            0,
            0,
            unavailable_views,
        )
    resolutions = _latest_completed_resolutions(records)
    current_time = now or datetime.now(timezone.utc)
    views: list[ContradictionCaseView] = []
    for case in current_cases:
        record = resolutions.get(case.case_id)
        if record is None or not _resolution_matches(store, record):
            views.append(_open_case_view(case))
            continue
        deferred_until = record["deferred_until"]
        if record["action"] == "defer":
            try:
                deadline = datetime.fromisoformat(deferred_until)
            except (TypeError, ValueError):
                views.append(_open_case_view(case))
                continue
            if deadline <= current_time:
                views.append(_open_case_view(case))
                continue
            case_status = "deferred"
        else:
            case_status = "resolved"
        views.append(
            ContradictionCaseView(
                case_id=case.case_id,
                item_ids=case.item_ids,
                pair_count=case.pair_count,
                confidence=case.confidence,
                evidence=case.evidence,
                status=case_status,
                resolution_action=record["action"],
                target_item_id=record["target_item_id"],
                resolved_at=record["timestamp"],
                deferred_until=deferred_until,
            )
        )
    ordered = tuple(sorted(views, key=lambda row: (row.status != "open", row.case_id)))
    return ContradictionCaseInventory(
        "healthy",
        sum(row.status == "open" for row in ordered),
        sum(row.status == "resolved" for row in ordered),
        sum(row.status == "deferred" for row in ordered),
        ordered,
    )


def resolve_contradiction_case(
    *,
    brain_dir: Path,
    store: ItemsStore,
    case_id: str,
    action: ContradictionResolutionAction,
    target_item_id: str | None = None,
    defer_days: int | None = None,
    apply: bool = False,
    expected_intent_sha256: str | None = None,
    index: Any | None = None,
) -> ContradictionResolutionResult:
    """Preview or apply one exact-case resolution transaction."""

    brain_dir = Path(brain_dir)
    case = _find_current_case(store, case_id)
    validation_reason = _validate_resolution_request(
        case,
        action=action,
        target_item_id=target_item_id,
        defer_days=defer_days,
    )
    item_ids = _transaction_item_ids(case, action, target_item_id)
    before = _read_item_digests(store, item_ids) if item_ids else {}
    containment_baselines: dict[str, ContainmentBaseline] = {}
    containment_status = "healthy"
    if case is not None and action != "defer":
        containment_baselines, containment_status = (
            containment_baselines_for_case(
                brain_dir=brain_dir,
                store=store,
                item_ids=case.item_ids,
            )
        )
    containment_binding = {
        item_id: baseline.binding_sha256
        for item_id, baseline in containment_baselines.items()
    }
    intent = _intent_sha256(
        case_id=case_id,
        action=action,
        case_item_ids=case.item_ids if case is not None else (),
        target_item_id=target_item_id,
        defer_days=defer_days,
        item_sha256=before,
        containment_sha256=containment_binding,
    )
    if validation_reason != "OK":
        return ContradictionResolutionResult(
            case_id,
            action,
            "blocked",
            validation_reason,
            not apply,
            intent,
            before,
            target_item_id,
            defer_days,
        )
    assert case is not None
    if containment_status != "healthy":
        return ContradictionResolutionResult(
            case_id=case_id,
            action=action,
            status="blocked",
            reason=f"CONTAINMENT_PROVENANCE_{containment_status.upper()}",
            dry_run=not apply,
            expected_intent_sha256=intent,
            item_sha256=before,
            target_item_id=target_item_id,
            defer_days=defer_days,
        )
    inventory = build_contradiction_case_inventory(
        brain_dir=brain_dir,
        store=store,
        cases=(case,),
    )
    if inventory.status != "healthy":
        return ContradictionResolutionResult(
            case_id=case_id,
            action=action,
            status="blocked",
            reason="CASE_LEDGER_UNAVAILABLE",
            dry_run=not apply,
            expected_intent_sha256=intent,
            item_sha256=before,
            target_item_id=target_item_id,
            defer_days=defer_days,
        )
    if inventory.cases and inventory.cases[0].status != "open":
        return ContradictionResolutionResult(
            case_id,
            action,
            "blocked",
            "CASE_ALREADY_RESOLVED",
            not apply,
            intent,
            before,
            target_item_id,
            defer_days,
            deferred_until=inventory.cases[0].deferred_until,
        )
    pair_reason = _validate_supersession_pairs(
        brain_dir,
        store,
        case,
        action=action,
        target_item_id=target_item_id,
    )
    if pair_reason != "OK":
        return ContradictionResolutionResult(
            case_id,
            action,
            "blocked",
            pair_reason,
            not apply,
            intent,
            before,
            target_item_id,
            defer_days,
        )
    if not apply:
        return ContradictionResolutionResult(
            case_id,
            action,
            "ready",
            "CASE_RESOLUTION_READY",
            True,
            intent,
            before,
            target_item_id,
            defer_days,
            containment_restored_count=len(containment_baselines),
        )
    if expected_intent_sha256 != intent:
        return ContradictionResolutionResult(
            case_id,
            action,
            "blocked",
            "CASE_RESOLUTION_CHANGED",
            False,
            intent,
            before,
            target_item_id,
            defer_days,
        )
    if not lifecycle_mutation_capability():
        return ContradictionResolutionResult(
            case_id,
            action,
            "blocked",
            "PLATFORM_UNSUPPORTED",
            False,
            intent,
            before,
            target_item_id,
            defer_days,
        )

    transaction_id = secrets.token_hex(16)
    snapshot_store = LifecycleSnapshotStore(brain_dir, store.items_dir)
    deferred_until: str | None = None
    snapshot: str | None = None
    index_repair_required = False
    locked_before: dict[str, bytes] = {}
    prepared_receipt_written = False
    try:
        with (
            _locked_case_inventory(
                brain_dir,
                store,
                case,
            ) as locked_inventory,
            store.locked_catalog(),
            store.locked_items(item_ids) as locked,
        ):
            if locked_inventory.status != "healthy":
                return ContradictionResolutionResult(
                    case_id=case_id,
                    action=action,
                    status="blocked",
                    reason="CASE_LEDGER_UNAVAILABLE",
                    dry_run=False,
                    expected_intent_sha256=intent,
                    item_sha256=before,
                    target_item_id=target_item_id,
                    defer_days=defer_days,
                )
            if (
                locked_inventory.cases
                and locked_inventory.cases[0].status != "open"
            ):
                return ContradictionResolutionResult(
                    case_id=case_id,
                    action=action,
                    status="blocked",
                    reason="CASE_ALREADY_RESOLVED",
                    dry_run=False,
                    expected_intent_sha256=intent,
                    item_sha256=before,
                    target_item_id=target_item_id,
                    defer_days=defer_days,
                    deferred_until=locked_inventory.cases[0].deferred_until,
                )
            locked_case = _find_current_case(store, case_id)
            if locked_case is None or locked_case.item_ids != case.item_ids:
                return _changed_result(
                    case_id,
                    action,
                    before,
                    target_item_id,
                    defer_days,
                )
            locked_before = {
                item_id: locked.read_bytes(item_id) for item_id in item_ids
            }
            locked_digests = _sha256_map(locked_before)
            locked_baselines: dict[str, ContainmentBaseline] = {}
            locked_containment_status = "healthy"
            if action != "defer":
                locked_baselines, locked_containment_status = (
                    containment_baselines_for_case(
                        brain_dir=brain_dir,
                        store=store,
                        item_ids=locked_case.item_ids,
                    )
                )
            if locked_containment_status != "healthy":
                return ContradictionResolutionResult(
                    case_id=case_id,
                    action=action,
                    status="blocked",
                    reason=(
                        "CONTAINMENT_PROVENANCE_"
                        f"{locked_containment_status.upper()}"
                    ),
                    dry_run=False,
                    expected_intent_sha256=intent,
                    item_sha256=locked_digests,
                    target_item_id=target_item_id,
                    defer_days=defer_days,
                )
            locked_intent = _intent_sha256(
                case_id=case_id,
                action=action,
                case_item_ids=case.item_ids,
                target_item_id=target_item_id,
                defer_days=defer_days,
                item_sha256=locked_digests,
                containment_sha256={
                    item_id: baseline.binding_sha256
                    for item_id, baseline in locked_baselines.items()
                },
            )
            if locked_intent != expected_intent_sha256:
                return ContradictionResolutionResult(
                    case_id,
                    action,
                    "blocked",
                    "CASE_RESOLUTION_CHANGED",
                    False,
                    locked_intent,
                    locked_digests,
                    target_item_id,
                    defer_days,
                )
            pair_reason = _validate_supersession_pairs(
                brain_dir,
                store,
                locked_case,
                action=action,
                target_item_id=target_item_id,
            )
            if pair_reason != "OK":
                return ContradictionResolutionResult(
                    case_id,
                    action,
                    "blocked",
                    pair_reason,
                    False,
                    locked_intent,
                    locked_digests,
                    target_item_id,
                    defer_days,
                )
            snapshot = snapshot_store.snapshot_items(locked_before)
            if action == "defer":
                assert defer_days is not None
                deferred_until = (
                    datetime.now(timezone.utc) + timedelta(days=defer_days)
                ).isoformat()
            prepared = _prepare_case_mutations(
                locked,
                locked_case,
                action=action,
                target_item_id=target_item_id,
                containment_baselines=locked_baselines,
            )
            _append_case_receipt(
                brain_dir,
                transaction_id=transaction_id,
                state="prepared",
                case=locked_case,
                action=action,
                target_item_id=target_item_id,
                deferred_until=deferred_until,
                before_sha256=locked_digests,
                after_sha256=None,
                intent_sha256=locked_intent,
                snapshot=snapshot,
                reason="READY",
                index_repair_required=False,
            )
            prepared_receipt_written = True
            for mutation in prepared:
                locked.apply_prepared(mutation)
                if locked.read_bytes(mutation.item_id) != mutation.data:
                    raise OSError("PREPARED_MUTATION_MISMATCH")
            after_bytes = {
                item_id: locked.read_bytes(item_id) for item_id in item_ids
            }
            after_digests = _sha256_map(after_bytes)
            if prepared:
                index_repair_required = _sync_case_index(
                    brain_dir,
                    locked,
                    prepared,
                    index=index,
                )
            _append_case_receipt(
                brain_dir,
                transaction_id=transaction_id,
                state="completed",
                case=locked_case,
                action=action,
                target_item_id=target_item_id,
                deferred_until=deferred_until,
                before_sha256=locked_digests,
                after_sha256=after_digests,
                intent_sha256=locked_intent,
                snapshot=snapshot,
                reason="OK",
                index_repair_required=index_repair_required,
            )
    except (LifecycleSnapshotError, OSError, TypeError, ValueError):
        rollback_status = (
            _rollback_failed_case_transaction(
                brain_dir=brain_dir,
                store=store,
                transaction_id=transaction_id,
                case=case,
                action=action,
                target_item_id=target_item_id,
                defer_days=defer_days,
                deferred_until=deferred_until,
                snapshot=snapshot,
                before_bytes=locked_before,
                expected_intent_sha256=expected_intent_sha256 or intent,
            )
            if prepared_receipt_written
            else "CASE_PREPARE_FAILED"
        )
        return ContradictionResolutionResult(
            case_id,
            action,
            "blocked",
            rollback_status,
            False,
            expected_intent_sha256 or intent,
            _sha256_map(locked_before) if locked_before else before,
            target_item_id,
            defer_days,
            deferred_until=deferred_until,
            transaction_id=transaction_id,
            snapshot=snapshot,
            index_repair_required=bool(locked_before),
        )
    return ContradictionResolutionResult(
        case_id,
        action,
        "applied" if action != "defer" else "deferred",
        "CASE_RESOLUTION_APPLIED",
        False,
        intent,
        before,
        target_item_id,
        defer_days,
        deferred_until=deferred_until,
        transaction_id=transaction_id,
        snapshot=snapshot,
        index_repair_required=index_repair_required,
        containment_restored_count=len(locked_baselines),
    )


def recover_contradiction_case_transaction(
    *,
    brain_dir: Path,
    store: ItemsStore,
    transaction_id: str,
    apply: bool = False,
) -> ContradictionRecoveryResult:
    """Preview or restore one prepared transaction that has no terminal receipt."""

    records, status = _read_receipt_records(Path(brain_dir))
    if status != "healthy":
        return ContradictionRecoveryResult(
            transaction_id,
            "blocked",
            "CASE_LEDGER_UNAVAILABLE",
            not apply,
        )
    transaction = [record for record in records if record["transaction_id"] == transaction_id]
    if len(transaction) != 1 or transaction[0]["state"] != "prepared":
        return ContradictionRecoveryResult(
            transaction_id,
            "blocked",
            "TRANSACTION_NOT_INCOMPLETE",
            not apply,
        )
    prepared = transaction[0]
    item_ids = tuple(sorted(prepared["before_sha256"]))
    if not apply:
        return ContradictionRecoveryResult(
            transaction_id,
            "ready",
            "CASE_RECOVERY_READY",
            True,
            item_ids,
            prepared["snapshot"],
        )
    try:
        with (
            lifecycle_transaction_lock(brain_dir),
            store.locked_catalog(),
            store.locked_items(list(item_ids)),
        ):
            current_records, current_status = _read_receipt_records(Path(brain_dir))
            current = [
                record
                for record in current_records
                if record["transaction_id"] == transaction_id
            ]
            if current_status != "healthy" or len(current) != 1:
                return ContradictionRecoveryResult(
                    transaction_id,
                    "blocked",
                    "TRANSACTION_NOT_INCOMPLETE",
                    False,
                )
            LifecycleSnapshotStore(brain_dir, store.items_dir).restore_items(
                prepared["snapshot"],
                item_ids,
            )
            restored = _read_item_digests(store, list(item_ids))
            if restored != prepared["before_sha256"]:
                raise LifecycleSnapshotError("SNAPSHOT_FAILED")
            for item_id in item_ids:
                append_dirty_index_marker(brain_dir, item_id)
            _append_case_receipt_from_record(
                brain_dir,
                prepared,
                state="rolled_back",
                after_sha256=restored,
                reason="RECOVERED",
                index_repair_required=True,
            )
    except (LifecycleSnapshotError, OSError, TypeError, ValueError):
        return ContradictionRecoveryResult(
            transaction_id,
            "blocked",
            "CASE_RECOVERY_FAILED",
            False,
            item_ids,
            prepared["snapshot"],
        )
    return ContradictionRecoveryResult(
        transaction_id,
        "recovered",
        "CASE_RECOVERY_APPLIED",
        False,
        item_ids,
        prepared["snapshot"],
    )


def read_contradiction_receipt_health(brain_dir: Path) -> ContradictionReceiptHealth:
    records, status = _read_receipt_records(Path(brain_dir))
    if status != "healthy":
        return ContradictionReceiptHealth(status, len(records))
    by_transaction: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_transaction.setdefault(record["transaction_id"], []).append(record)
    incomplete = 0
    completed = 0
    rolled_back = 0
    for transaction in by_transaction.values():
        if len(transaction) == 1:
            incomplete += 1
        elif transaction[-1]["state"] == "completed":
            completed += 1
        else:
            rolled_back += 1
    return ContradictionReceiptHealth(
        "incomplete" if incomplete else "healthy",
        len(records),
        incomplete,
        completed,
        rolled_back,
    )


def _incomplete_transaction_count(records: Iterable[dict[str, Any]]) -> int:
    terminal: set[str] = set()
    prepared: set[str] = set()
    for record in records:
        transaction_id = record["transaction_id"]
        if record["state"] == "prepared":
            prepared.add(transaction_id)
        else:
            terminal.add(transaction_id)
    return len(prepared - terminal)


def _current_cases(store: ItemsStore) -> tuple[ContradictionCase, ...]:
    from agent_brain.memory.governance.contradiction_cases import (
        build_contradiction_cases,
    )

    return build_contradiction_cases(DriftDetector(store).detect().findings)


@contextmanager
def _locked_case_inventory(
    brain_dir: Path,
    store: ItemsStore,
    case: ContradictionCase,
) -> Iterator[ContradictionCaseInventory]:
    """Hold the lifecycle lock from the final ledger check through commit."""

    with lifecycle_transaction_lock(brain_dir):
        yield build_contradiction_case_inventory(
            brain_dir=brain_dir,
            store=store,
            cases=(case,),
        )


def _find_current_case(
    store: ItemsStore,
    case_id: str,
) -> ContradictionCase | None:
    if _CASE_ID.fullmatch(case_id) is None:
        return None
    return next(
        (case for case in _current_cases(store) if case.case_id == case_id),
        None,
    )


def _open_case_view(case: ContradictionCase) -> ContradictionCaseView:
    return ContradictionCaseView(
        case.case_id,
        case.item_ids,
        case.pair_count,
        case.confidence,
        case.evidence,
        "open",
    )


def _validate_resolution_request(
    case: ContradictionCase | None,
    *,
    action: str,
    target_item_id: str | None,
    defer_days: int | None,
) -> str:
    if case is None:
        return "CASE_NOT_FOUND"
    if action not in {
        "select_authority",
        "merge",
        "coexist",
        "dismiss",
        "defer",
    }:
        return "INVALID_ACTION"
    transaction_size = len(case.item_ids) + (
        1
        if (
            action == "merge"
            and target_item_id is not None
            and target_item_id not in case.item_ids
        )
        else 0
    )
    if transaction_size > 500:
        return "CASE_TOO_LARGE"
    if action == "select_authority":
        if target_item_id not in case.item_ids:
            return "AUTHORITY_NOT_IN_CASE"
    elif action == "merge":
        if (
            target_item_id is None
            or not is_valid_memory_item_id(target_item_id)
            or target_item_id in case.item_ids
        ):
            return "MERGED_ITEM_MUST_BE_OUTSIDE_CASE"
    elif target_item_id is not None:
        return "TARGET_NOT_ALLOWED"
    if action == "defer":
        if type(defer_days) is not int or not 1 <= defer_days <= 365:
            return "INVALID_DEFER_DAYS"
    elif defer_days is not None:
        return "DEFER_DAYS_NOT_ALLOWED"
    return "OK"


def _transaction_item_ids(
    case: ContradictionCase | None,
    action: str,
    target_item_id: str | None,
) -> list[str]:
    if case is None:
        return []
    item_ids = set(case.item_ids)
    if action == "merge" and target_item_id is not None:
        item_ids.add(target_item_id)
    return sorted(item_ids)


def _validate_supersession_pairs(
    brain_dir: Path,
    store: ItemsStore,
    case: ContradictionCase,
    *,
    action: str,
    target_item_id: str | None,
) -> str:
    if action not in {"select_authority", "merge"}:
        return "OK"
    assert target_item_id is not None
    service = SupersessionService(brain_dir, store)
    for item_id in case.item_ids:
        if item_id == target_item_id:
            continue
        result = service.preview(target_item_id, item_id)
        if result.status != "ready":
            return str(result.reason)
    return "OK"


def _prepare_case_mutations(
    locked: Any,
    case: ContradictionCase,
    *,
    action: str,
    target_item_id: str | None,
    containment_baselines: Mapping[str, ContainmentBaseline],
) -> list[PreparedItemMutation]:
    if action == "defer":
        return []
    if action in {"coexist", "dismiss"}:
        prepared = []
        for item_id in case.item_ids:
            item, _body = locked.get(item_id)
            baseline = containment_baselines.get(item_id)
            tags = set(baseline.tags if baseline is not None else item.tags)
            tags.add(
                "contradiction-coexists"
                if action == "coexist"
                else "contradiction-dismissed"
            )
            updates: dict[str, object] = {"tags": sorted(tags)}
            if baseline is not None:
                updates["confidence"] = baseline.confidence
            prepared.append(
                locked.prepare_update_frontmatter(item_id, **updates)
            )
        return prepared
    assert target_item_id is not None
    target, _body = locked.get(target_item_id)
    target_tag = (
        "contradiction-authority"
        if action == "select_authority"
        else "contradiction-merged"
    )
    target_baseline = containment_baselines.get(target.id)
    target_tags = set(
        target_baseline.tags if target_baseline is not None else target.tags
    )
    target_tags.add(target_tag)
    target_refs = target.refs.model_copy(
        update={
            "mems": list(
                dict.fromkeys(
                    [
                        *target.refs.mems,
                        *(item_id for item_id in case.item_ids if item_id != target.id),
                    ]
                )
            )
        }
    )
    target_updates: dict[str, object] = {
        "tags": sorted(target_tags),
        "refs": target_refs,
    }
    if target_baseline is not None:
        target_updates["confidence"] = target_baseline.confidence
    prepared = [
        locked.prepare_update_frontmatter(target.id, **target_updates)
    ]
    for item_id in case.item_ids:
        if item_id == target.id:
            continue
        item, _body = locked.get(item_id)
        baseline = containment_baselines.get(item_id)
        tags = set(baseline.tags if baseline is not None else item.tags)
        tags.add("contradiction-resolved")
        updates = {
            "tags": sorted(tags),
            "superseded_by": target.id,
        }
        if baseline is not None:
            updates["confidence"] = baseline.confidence
        prepared.append(
            locked.prepare_update_frontmatter(item_id, **updates)
        )
    return sorted(prepared, key=lambda mutation: mutation.item_id)


def _sync_case_index(
    brain_dir: Path,
    locked: Any,
    prepared: list[PreparedItemMutation],
    *,
    index: Any | None,
) -> bool:
    repair_required = index is None
    for mutation in prepared:
        item, body = locked.get(mutation.item_id)
        if index is not None:
            try:
                index.upsert(item, body, embedding=None)
                continue
            except Exception:  # noqa: BLE001 - Markdown remains authoritative.
                repair_required = True
        if not append_dirty_index_marker(brain_dir, mutation.item_id):
            repair_required = True
    return repair_required


def _rollback_failed_case_transaction(
    *,
    brain_dir: Path,
    store: ItemsStore,
    transaction_id: str,
    case: ContradictionCase,
    action: ContradictionResolutionAction,
    target_item_id: str | None,
    defer_days: int | None,
    deferred_until: str | None,
    snapshot: str | None,
    before_bytes: Mapping[str, bytes],
    expected_intent_sha256: str,
) -> str:
    if snapshot is None or not before_bytes:
        return "CASE_SNAPSHOT_FAILED"
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
                item_id: locked.read_bytes(item_id) for item_id in item_ids
            }
            if restored != dict(before_bytes):
                raise OSError("CASE_ROLLBACK_MISMATCH")
            for item_id in item_ids:
                append_dirty_index_marker(brain_dir, item_id)
            _append_case_receipt(
                brain_dir,
                transaction_id=transaction_id,
                state="rolled_back",
                case=case,
                action=action,
                target_item_id=target_item_id,
                deferred_until=deferred_until,
                before_sha256=_sha256_map(before_bytes),
                after_sha256=_sha256_map(restored),
                intent_sha256=expected_intent_sha256,
                snapshot=snapshot,
                reason="MUTATION_FAILED",
                index_repair_required=True,
            )
    except (OSError, TypeError, ValueError):
        return "CASE_ROLLBACK_FAILED"
    return "CASE_MUTATION_ROLLED_BACK"


def _read_item_digests(store: ItemsStore, item_ids: list[str]) -> dict[str, str]:
    if not item_ids:
        return {}
    try:
        with store.locked_catalog(), store.locked_items(item_ids) as locked:
            return {
                item_id: hashlib.sha256(locked.read_bytes(item_id)).hexdigest()
                for item_id in item_ids
            }
    except (FileNotFoundError, OSError, ValueError):
        return {}


def _sha256_map(values: Mapping[str, bytes]) -> dict[str, str]:
    return {
        item_id: hashlib.sha256(values[item_id]).hexdigest()
        for item_id in sorted(values)
    }


def _intent_sha256(
    *,
    case_id: str,
    action: str,
    case_item_ids: Iterable[str],
    target_item_id: str | None,
    defer_days: int | None,
    item_sha256: Mapping[str, str],
    containment_sha256: Mapping[str, str] | None = None,
) -> str:
    payload = json.dumps(
        {
            "case_id": case_id,
            "action": action,
            "case_item_ids": sorted(case_item_ids),
            "target_item_id": target_item_id,
            "defer_days": defer_days,
            "item_sha256": dict(sorted(item_sha256.items())),
            "containment_sha256": dict(
                sorted((containment_sha256 or {}).items())
            ),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _changed_result(
    case_id: str,
    action: ContradictionResolutionAction,
    before: dict[str, str],
    target_item_id: str | None,
    defer_days: int | None,
) -> ContradictionResolutionResult:
    return ContradictionResolutionResult(
        case_id,
        action,
        "blocked",
        "CASE_RESOLUTION_CHANGED",
        False,
        "",
        before,
        target_item_id,
        defer_days,
    )


def _append_case_receipt(
    brain_dir: Path,
    *,
    transaction_id: str,
    state: str,
    case: ContradictionCase,
    action: str,
    target_item_id: str | None,
    deferred_until: str | None,
    before_sha256: Mapping[str, str],
    after_sha256: Mapping[str, str] | None,
    intent_sha256: str,
    snapshot: str,
    reason: str,
    index_repair_required: bool,
) -> None:
    record = {
        "schema_version": 1,
        "transaction_id": transaction_id,
        "state": state,
        "case_id": case.case_id,
        "action": action,
        "case_item_ids": list(case.item_ids),
        "target_item_id": target_item_id,
        "deferred_until": deferred_until,
        "before_sha256": dict(sorted(before_sha256.items())),
        "after_sha256": (
            None if after_sha256 is None else dict(sorted(after_sha256.items()))
        ),
        "intent_sha256": intent_sha256,
        "snapshot": snapshot,
        "reason": reason,
        "index_repair_required": index_repair_required,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if not _valid_receipt_record(record):
        raise TypeError("INVALID_CONTRADICTION_CASE_RECEIPT")
    _append_receipt_payload(brain_dir, record)


def _append_case_receipt_from_record(
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
        raise TypeError("INVALID_CONTRADICTION_CASE_RECEIPT")
    _append_receipt_payload(brain_dir, record)


def _append_receipt_payload(brain_dir: Path, record: Mapping[str, Any]) -> None:
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
        raise OSError("CONTRADICTION_CASE_RECEIPT_TOO_LARGE")
    with SecureDirectory.open(brain_dir) as brain:
        with brain.child("runtime", create=True) as runtime:
            descriptor, created = runtime.open_or_create_file(
                _RECEIPT_NAME,
                os.O_WRONLY | os.O_APPEND,
            )
            original_length = os.fstat(descriptor).st_size
            try:
                if original_length + len(payload) > _MAX_LEDGER_BYTES:
                    raise OSError("CONTRADICTION_CASE_LEDGER_TOO_LARGE")
                os.fchmod(descriptor, 0o600)
                view = memoryview(payload)
                try:
                    while view:
                        written = os.write(descriptor, view)
                        if written <= 0:
                            raise OSError("CONTRADICTION_CASE_RECEIPT_WRITE_FAILED")
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
        else:
            if (
                len(transaction) != 1
                or record["state"] not in {"completed", "rolled_back"}
                or not _same_receipt_binding(transaction[0], record)
            ):
                return records, "corrupt"
        transaction.append(record)
        records.append(record)
    return records, "healthy"


def _latest_completed_resolutions(
    records: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    resolutions: dict[str, dict[str, Any]] = {}
    for record in records:
        if record["state"] == "completed":
            resolutions[record["case_id"]] = record
        elif record["state"] == "rolled_back":
            current = resolutions.get(record["case_id"])
            if (
                current is not None
                and current["transaction_id"] == record["transaction_id"]
            ):
                resolutions.pop(record["case_id"], None)
    return resolutions


def _resolution_matches(store: ItemsStore, record: Mapping[str, Any]) -> bool:
    expected = record["after_sha256"]
    if not isinstance(expected, dict):
        return False
    return _read_item_digests(store, sorted(expected)) == expected


def _same_receipt_binding(
    prepared: Mapping[str, Any],
    terminal: Mapping[str, Any],
) -> bool:
    keys = {
        "schema_version",
        "transaction_id",
        "case_id",
        "action",
        "case_item_ids",
        "target_item_id",
        "deferred_until",
        "before_sha256",
        "intent_sha256",
        "snapshot",
    }
    return all(prepared[key] == terminal[key] for key in keys)


def _valid_receipt_record(record: object) -> bool:
    if not isinstance(record, dict) or set(record) != {
        "schema_version",
        "transaction_id",
        "state",
        "case_id",
        "action",
        "case_item_ids",
        "target_item_id",
        "deferred_until",
        "before_sha256",
        "after_sha256",
        "intent_sha256",
        "snapshot",
        "reason",
        "index_repair_required",
        "timestamp",
    }:
        return False
    item_ids = record["case_item_ids"]
    before = record["before_sha256"]
    after = record["after_sha256"]
    action = record["action"]
    target_item_id = record["target_item_id"]
    if (
        record["schema_version"] != 1
        or _TRANSACTION_ID.fullmatch(str(record["transaction_id"])) is None
        or record["state"] not in {"prepared", "completed", "rolled_back"}
        or _CASE_ID.fullmatch(str(record["case_id"])) is None
        or action
        not in {
            "select_authority",
            "merge",
            "coexist",
            "dismiss",
            "defer",
        }
        or not isinstance(item_ids, list)
        or not 2 <= len(item_ids) <= 500
        or item_ids != sorted(set(item_ids))
        or any(not is_valid_memory_item_id(item_id) for item_id in item_ids)
        or (
            target_item_id is not None
            and not is_valid_memory_item_id(target_item_id)
        )
        or not isinstance(before, dict)
        or not 2 <= len(before) <= 500
        or any(
            not is_valid_memory_item_id(item_id)
            or not isinstance(digest, str)
            or _DIGEST.fullmatch(digest) is None
            for item_id, digest in before.items()
        )
        or not isinstance(record["intent_sha256"], str)
        or _DIGEST.fullmatch(record["intent_sha256"]) is None
        or not isinstance(record["snapshot"], str)
        or _SNAPSHOT_ID.fullmatch(record["snapshot"]) is None
        or not isinstance(record["reason"], str)
        or not isinstance(record["index_repair_required"], bool)
        or not isinstance(record["timestamp"], str)
    ):
        return False
    expected_item_ids = set(item_ids)
    if action == "select_authority":
        if target_item_id not in expected_item_ids:
            return False
    elif action == "merge":
        if target_item_id is None or target_item_id in expected_item_ids:
            return False
        expected_item_ids.add(target_item_id)
    elif target_item_id is not None:
        return False
    if set(before) != expected_item_ids:
        return False
    try:
        timestamp = datetime.fromisoformat(record["timestamp"])
    except ValueError:
        return False
    if timestamp.tzinfo is None:
        return False
    if action == "defer":
        if not isinstance(record["deferred_until"], str):
            return False
        try:
            deferred_until = datetime.fromisoformat(record["deferred_until"])
        except ValueError:
            return False
        if deferred_until.tzinfo is None:
            return False
    elif record["deferred_until"] is not None:
        return False
    if record["state"] == "prepared":
        return after is None
    return (
        isinstance(after, dict)
        and set(after) == set(before)
        and all(
            isinstance(digest, str) and _DIGEST.fullmatch(digest) is not None
            for digest in after.values()
        )
    )


__all__ = [
    "ContradictionCaseInventory",
    "ContradictionCaseView",
    "ContradictionReceiptHealth",
    "ContradictionRecoveryResult",
    "ContradictionResolutionAction",
    "ContradictionResolutionResult",
    "build_contradiction_case_inventory",
    "read_contradiction_receipt_health",
    "recover_contradiction_case_transaction",
    "resolve_contradiction_case",
]
