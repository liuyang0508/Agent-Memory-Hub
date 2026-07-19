"""Lifecycle review queue planning and explicit action execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from agent_brain.contracts.memory_item import is_valid_memory_item_id
from agent_brain.memory.governance.auto_governance import (
    AutoGovernanceCycle,
    lifecycle_review_due,
)
from agent_brain.memory.governance.lifecycle_archive import archive_reviewed_item
from agent_brain.memory.governance.lifecycle_ledger import (
    LifecycleLedgerRecord,
    active_lifecycle_deferrals,
    append_lifecycle_record,
    lifecycle_transaction_lock,
)
from agent_brain.memory.governance.maintenance_plan import (
    MaintenancePlan,
    build_maintenance_plan,
)
from agent_brain.memory.governance.supersession import SupersessionService
from agent_brain.memory.store.durable_fs import lifecycle_mutation_capability
from agent_brain.memory.store.items_store import ItemsStore

LifecycleActionName = Literal[
    "supersede", "archive", "keep-active", "defer", "revert-supersession"
]
_ACTION_NAMES = {
    "supersede",
    "archive",
    "keep-active",
    "defer",
    "revert-supersession",
}


@dataclass(frozen=True)
class LifecycleReviewAction:
    action: LifecycleActionName
    item_id: str
    replacement_id: str | None = None
    defer_days: int | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class LifecycleActionResult:
    action: LifecycleActionName
    item_id: str
    status: str
    reason: str
    dry_run: bool
    replacement_id: str | None = None
    defer_days: int | None = None
    deferred_until: str | None = None
    snapshot: str | None = None
    index_repair_attempted: bool = False
    index_repair_required: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_lifecycle_review_plan(
    *,
    brain_dir: Path,
    items_store: ItemsStore,
    limit_per_lane: int = 20,
    now: datetime | None = None,
) -> MaintenancePlan:
    """Build a read-only lifecycle review plan for Web/Admin and CLI callers."""
    current = now or datetime.now(timezone.utc)
    report = AutoGovernanceCycle(
        brain_dir=brain_dir,
        items_store=items_store,
        include_index=False,
        include_evolve=False,
        include_conversations=False,
        now=current,
    ).run(apply=False)
    deferred = active_lifecycle_deferrals(brain_dir, now=current)
    if deferred:
        report = replace(
            report,
            actions=[
                action
                for action in report.actions
                if not (
                    action.action == "review_archive"
                    and str(action.details.get("issue_type", "")).startswith("stale_")
                    and any(item_id in deferred for item_id in action.item_ids)
                )
            ],
        )
    return build_maintenance_plan(
        report,
        limit_per_lane=limit_per_lane,
        category_filter="lifecycle",
    )


def conflicting_lifecycle_action_item(
    actions: list[LifecycleReviewAction],
) -> str | None:
    """Return the first item with non-identical requested actions."""
    seen: dict[str, LifecycleReviewAction] = {}
    for action in actions:
        previous = seen.get(action.item_id)
        if previous is not None and previous != action:
            return action.item_id
        seen[action.item_id] = action
    for action in actions:
        if (
            action.replacement_id is not None
            and action.replacement_id != action.item_id
            and action.replacement_id in seen
        ):
            return action.replacement_id
    return None


def apply_lifecycle_review_actions(
    *,
    brain_dir: Path,
    items_store: ItemsStore,
    actions: list[LifecycleReviewAction],
    apply: bool = False,
    index_repair: bool = True,
) -> dict[str, Any]:
    """Preview or execute explicit lifecycle actions through shared services.

    The function validates the complete action set before executing any item.
    Exact duplicates are idempotently collapsed; different actions for one
    source item are rejected as a batch conflict.
    """
    requested = _deduplicate_actions(actions)
    conflict = conflicting_lifecycle_action_item(requested)
    if conflict is not None:
        return {
            "dry_run": not apply,
            "requested": [action.to_dict() for action in requested],
            "results": [],
            "error": "CONFLICTING_ACTIONS",
            "item_id": conflict,
            "index_repair": index_repair,
        }

    validation_reasons = [_validate_action(action) for action in requested]
    if any(reason is not None for reason in validation_reasons):
        invalid_results = [
            _blocked(
                action,
                reason or "BATCH_VALIDATION_FAILED",
                apply=apply,
            )
            for action, reason in zip(requested, validation_reasons, strict=True)
        ]
        return _build_action_payload(
            requested=requested,
            results=invalid_results,
            queue_by_id={},
            apply=apply,
            index_repair=index_repair,
        )

    queue_by_id: dict[str, Any] = {}
    if any(action.action == "archive" for action in requested):
        plan = build_lifecycle_review_plan(
            brain_dir=brain_dir,
            items_store=items_store,
            limit_per_lane=max(len(list(items_store.iter_all())), len(requested), 1),
        )
        queue_by_id = {row.item_id: row for row in plan.review_queue}

    needs_index = any(
        action.action != "defer" and _validate_action(action) is None
        for action in requested
    )
    idx = (
        _open_index(brain_dir)
        if apply and index_repair and needs_index
        else None
    )
    service = SupersessionService(brain_dir, items_store, index=idx)
    results: list[LifecycleActionResult] = []
    index_close_failed = False
    try:
        for action in requested:
            results.append(
                _execute_action(
                    action,
                    brain_dir=brain_dir,
                    items_store=items_store,
                    service=service,
                    queue_by_id=queue_by_id,
                    apply=apply,
                    index=idx,
                )
            )
    finally:
        if idx is not None:
            try:
                idx.close()
            except Exception:  # noqa: BLE001 - derived index remains repairable.
                index_close_failed = True
    if index_close_failed:
        results = [
            replace(result, index_repair_required=True)
            if result.index_repair_attempted
            else result
            for result in results
        ]

    return _build_action_payload(
        requested=requested,
        results=results,
        queue_by_id=queue_by_id,
        apply=apply,
        index_repair=index_repair,
    )


def _build_action_payload(
    *,
    requested: list[LifecycleReviewAction],
    results: list[LifecycleActionResult],
    queue_by_id: dict[str, Any],
    apply: bool,
    index_repair: bool,
) -> dict[str, Any]:

    candidate_rows = [
        queue_by_id[action.item_id].to_dict()
        for action in requested
        if action.action == "archive" and action.item_id in queue_by_id
    ]
    archived = [
        result.item_id
        for result in results
        if result.action == "archive" and result.status == "applied"
    ]
    skipped = [
        {"id": result.item_id, "reason": "not_in_lifecycle_review_queue"}
        for result in results
        if result.action == "archive"
        and result.reason == "NOT_IN_LIFECYCLE_REVIEW_QUEUE"
    ]
    failed = [
        {"id": result.item_id, "reason": result.reason}
        for result in results
        if not result.dry_run and result.status == "blocked" and not (
            result.action == "archive"
            and result.reason == "NOT_IN_LIFECYCLE_REVIEW_QUEUE"
        )
    ]
    failed.extend(
        {"id": result.item_id, "reason": "INDEX_DELETE_FAILED"}
        for result in results
        if result.action == "archive"
        and result.status == "applied"
        and result.index_repair_attempted
        and result.index_repair_required
    )
    return {
        "dry_run": not apply,
        "requested": [action.to_dict() for action in requested],
        "results": [result.to_dict() for result in results],
        "index_repair": index_repair,
        # Compatibility fields retained for archive-only callers.
        "candidates": candidate_rows,
        "archived": archived,
        "skipped": skipped,
        "failed": failed,
        "boundary": (
            "archive requires current lifecycle review_queue membership; "
            "all mutations require explicit apply"
        ),
    }


def apply_lifecycle_review_items(
    *,
    brain_dir: Path,
    items_store: ItemsStore,
    item_ids: list[str],
    apply: bool = False,
    index_repair: bool = True,
) -> dict[str, Any]:
    """Backward-compatible archive-only lifecycle review wrapper."""
    payload = apply_lifecycle_review_actions(
        brain_dir=brain_dir,
        items_store=items_store,
        actions=[
            LifecycleReviewAction(action="archive", item_id=item_id)
            for item_id in item_ids
        ],
        apply=apply,
        index_repair=index_repair,
    )
    # Historical callers expect a list of IDs rather than action objects.
    payload["requested"] = list(dict.fromkeys(item_ids))
    return payload


def _deduplicate_actions(
    actions: list[LifecycleReviewAction],
) -> list[LifecycleReviewAction]:
    unique: list[LifecycleReviewAction] = []
    seen: set[LifecycleReviewAction] = set()
    for action in actions:
        if action in seen:
            continue
        seen.add(action)
        unique.append(action)
    return unique


def _execute_action(
    action: LifecycleReviewAction,
    *,
    brain_dir: Path,
    items_store: ItemsStore,
    service: SupersessionService,
    queue_by_id: dict[str, Any],
    apply: bool,
    index: Any,
) -> LifecycleActionResult:
    invalid_reason = _validate_action(action)
    if invalid_reason is not None:
        return _blocked(action, invalid_reason, apply=apply)

    if action.action == "supersede":
        assert action.replacement_id is not None
        result = service.apply(
            action.replacement_id,
            action.item_id,
            apply=apply,
        )
        return LifecycleActionResult(
            action=action.action,
            item_id=action.item_id,
            replacement_id=action.replacement_id,
            status=result.status,
            reason=result.reason,
            dry_run=result.dry_run,
            snapshot=result.snapshot,
            index_repair_attempted=(
                index is not None
                and not result.dry_run
                and result.status in {"applied", "already_applied"}
            ),
            index_repair_required=result.index_repair_required,
        )
    if action.action == "revert-supersession":
        assert action.replacement_id is not None
        result = service.revert(
            action.replacement_id,
            action.item_id,
            apply=apply,
        )
        return LifecycleActionResult(
            action=action.action,
            item_id=action.item_id,
            replacement_id=action.replacement_id,
            status=result.status,
            reason=result.reason,
            dry_run=result.dry_run,
            snapshot=result.snapshot,
            index_repair_attempted=(
                index is not None
                and not result.dry_run
                and result.status == "reverted"
            ),
            index_repair_required=result.index_repair_required,
        )
    if action.action == "archive":
        return _archive_action(
            action,
            brain_dir=brain_dir,
            items_store=items_store,
            queue_by_id=queue_by_id,
            apply=apply,
            index=index,
        )
    if action.action == "keep-active":
        return _keep_active_action(
            action,
            brain_dir=brain_dir,
            items_store=items_store,
            apply=apply,
            index=index,
        )
    return _defer_action(
        action,
        brain_dir=brain_dir,
        items_store=items_store,
        apply=apply,
    )


def _validate_action(action: LifecycleReviewAction) -> str | None:
    if action.action not in _ACTION_NAMES:
        return "INVALID_ACTION"
    if not is_valid_memory_item_id(action.item_id):
        return "INVALID_ITEM_ID"
    if action.action in {"supersede", "revert-supersession"}:
        if not is_valid_memory_item_id(action.replacement_id):
            return "INVALID_REPLACEMENT_ID"
    elif action.replacement_id is not None:
        return "UNEXPECTED_REPLACEMENT_ID"
    if action.action == "defer" and (
        type(action.defer_days) is not int
        or not 1 <= action.defer_days <= 365
    ):
        return "INVALID_DEFER_DAYS"
    if action.action != "defer" and action.defer_days is not None:
        return "UNEXPECTED_DEFER_DAYS"
    return None


def _blocked(
    action: LifecycleReviewAction,
    reason: str,
    *,
    apply: bool,
) -> LifecycleActionResult:
    return LifecycleActionResult(
        action=action.action,
        item_id=action.item_id,
        replacement_id=action.replacement_id,
        defer_days=action.defer_days,
        status="blocked",
        reason=reason,
        dry_run=not apply,
    )


def _archive_action(
    action: LifecycleReviewAction,
    *,
    brain_dir: Path,
    items_store: ItemsStore,
    queue_by_id: dict[str, Any],
    apply: bool,
    index: Any,
) -> LifecycleActionResult:
    if action.item_id not in queue_by_id:
        return _blocked(action, "NOT_IN_LIFECYCLE_REVIEW_QUEUE", apply=apply)
    if not apply:
        return LifecycleActionResult(action.action, action.item_id, "ready", "OK", True)
    current = datetime.now(timezone.utc)
    transaction = archive_reviewed_item(
        brain_dir=brain_dir,
        items_store=items_store,
        item_id=action.item_id,
        eligible=lambda item: (
            action.item_id
            not in active_lifecycle_deferrals(brain_dir, now=current)
            and lifecycle_review_due(item, now=current)
        ),
        index=index,
    )
    return LifecycleActionResult(
        action.action,
        action.item_id,
        transaction.status,
        transaction.reason,
        False,
        index_repair_attempted=transaction.index_repair_attempted,
        index_repair_required=transaction.index_repair_required,
    )


def _keep_active_action(
    action: LifecycleReviewAction,
    *,
    brain_dir: Path,
    items_store: ItemsStore,
    apply: bool,
    index: Any,
) -> LifecycleActionResult:
    try:
        item, _ = items_store.get(action.item_id)
    except FileNotFoundError:
        return _blocked(action, "ITEM_MISSING", apply=apply)
    except (OSError, UnicodeError, ValueError):
        return _blocked(action, "ITEM_INVALID", apply=apply)
    if item.superseded_by:
        return _blocked(action, "ITEM_SUPERSEDED", apply=apply)
    if not apply:
        return LifecycleActionResult(action.action, action.item_id, "ready", "OK", True)
    if not lifecycle_mutation_capability():
        return _blocked(action, "PLATFORM_UNSUPPORTED", apply=True)

    index_repair_required = index is None
    index_repair_attempted = index is not None
    try:
        with (
            lifecycle_transaction_lock(brain_dir),
            items_store.locked_items([action.item_id]) as locked,
        ):
            current, body = locked.get(action.item_id)
            if current.superseded_by:
                return _blocked(action, "ITEM_SUPERSEDED", apply=True)
            updated = locked.update_frontmatter(
                action.item_id,
                **{"validity.observed_at": datetime.now(timezone.utc)},
            )
            if index is not None:
                try:
                    index.upsert(updated, body, embedding=None)
                except Exception:  # noqa: BLE001 - markdown is authoritative.
                    index_repair_required = True
    except FileNotFoundError:
        return _blocked(action, "ITEM_MISSING", apply=True)
    except (OSError, UnicodeError, ValueError):
        return _blocked(action, "MARKDOWN_UPDATE_FAILED", apply=True)

    return LifecycleActionResult(
        action.action,
        action.item_id,
        "applied",
        "OK",
        False,
        index_repair_attempted=index_repair_attempted,
        index_repair_required=index_repair_required,
    )


def _defer_action(
    action: LifecycleReviewAction,
    *,
    brain_dir: Path,
    items_store: ItemsStore,
    apply: bool,
) -> LifecycleActionResult:
    assert action.defer_days is not None
    try:
        items_store.get(action.item_id)
    except FileNotFoundError:
        return _blocked(action, "ITEM_MISSING", apply=apply)
    except (OSError, UnicodeError, ValueError):
        return _blocked(action, "ITEM_INVALID", apply=apply)
    deferred_until = (
        datetime.now(timezone.utc) + timedelta(days=action.defer_days)
    ).isoformat()
    if not apply:
        return LifecycleActionResult(
            action.action,
            action.item_id,
            "ready",
            "OK",
            True,
            defer_days=action.defer_days,
            deferred_until=deferred_until,
        )
    if not lifecycle_mutation_capability():
        return _blocked(action, "PLATFORM_UNSUPPORTED", apply=True)
    try:
        with (
            lifecycle_transaction_lock(brain_dir),
            items_store.locked_items([action.item_id]) as locked,
        ):
            locked.get(action.item_id)
            append_lifecycle_record(
                brain_dir,
                LifecycleLedgerRecord(
                    action="defer",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    status="deferred",
                    reason="OK",
                    obsolete_id=action.item_id,
                    replacement_id=None,
                    snapshot=None,
                    replacement_ref_preexisted=False,
                    deferred_until=deferred_until,
                ),
            )
    except (OSError, TypeError, ValueError):
        return _blocked(action, "LEDGER_WRITE_FAILED", apply=True)
    return LifecycleActionResult(
        action.action,
        action.item_id,
        "deferred",
        "OK",
        False,
        defer_days=action.defer_days,
        deferred_until=deferred_until,
    )


def _open_index(brain_dir: Path) -> Any:
    db_path = brain_dir / "index.db"
    if not db_path.exists():
        return None
    from agent_brain.platform.indexing.index import HubIndex

    return HubIndex(db_path=db_path)


__all__ = [
    "LifecycleActionName",
    "LifecycleReviewAction",
    "apply_lifecycle_review_actions",
    "apply_lifecycle_review_items",
    "build_lifecycle_review_plan",
    "conflicting_lifecycle_action_item",
]
