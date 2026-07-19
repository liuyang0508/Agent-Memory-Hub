"""Read-only maintenance planning over auto-governance actions."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Collection, Mapping

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.governance.auto_governance import (
    ActionRisk,
    AutoGovernanceAction,
    AutoGovernanceReport,
)
from agent_brain.memory.governance.lifecycle_candidates import (
    rank_supersession_candidates,
)


_LANE_ORDER: tuple[ActionRisk, ...] = ("safe_apply", "review_required", "blocked")

_LANE_TITLES = {
    "safe_apply": "Safe auto-apply candidates",
    "review_required": "Review-required maintenance",
    "blocked": "Blocked actions",
}

_LANE_DESCRIPTIONS = {
    "safe_apply": (
        "Deterministic metadata or derived-state updates. "
        "Use apply only after reviewing this dry-run plan."
    ),
    "review_required": (
        "Actions that can change, archive, consolidate, or supersede knowledge. "
        "A human should inspect the affected items first."
    ),
    "blocked": (
        "Actions whose audit gate or safety precondition failed. "
        "Resolve the blocker before applying anything related."
    ),
}


@dataclass(frozen=True)
class MaintenancePlanAction:
    """A compact action row suitable for CLI/API previews."""

    action: str
    category: str
    title: str
    reason: str
    item_count: int
    item_ids: list[str] = field(default_factory=list)
    command: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    applied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MaintenanceReviewQueueItem:
    """Read-only review queue row for external tools and Web Admin."""

    item_id: str
    action: str
    category: str
    title: str
    read_command: str
    recommended_next: str
    can_auto_apply: bool
    boundary: str
    candidates: list[dict[str, object]] = field(default_factory=list)
    reviewed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MaintenancePlanLane:
    """One execution lane in the maintenance plan."""

    risk: ActionRisk
    title: str
    description: str
    count: int
    returned: int
    truncated: bool
    next_command: str
    actions: list[MaintenancePlanAction] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk": self.risk,
            "title": self.title,
            "description": self.description,
            "count": self.count,
            "returned": self.returned,
            "truncated": self.truncated,
            "next_command": self.next_command,
            "actions": [action.to_dict() for action in self.actions],
        }


@dataclass(frozen=True)
class MaintenancePlan:
    """A read-only, ordered maintenance plan."""

    scanned_items: int
    raw_action_count: int
    action_count: int
    suppressed_action_count: int
    filtered_out_count: int
    safe_apply_count: int
    review_required_count: int
    blocked_count: int
    action_counts: dict[str, int]
    category_counts: dict[str, int]
    filters: dict[str, str | None]
    lanes: list[MaintenancePlanLane]
    next_commands: list[str]
    review_queue: list[MaintenanceReviewQueueItem] = field(default_factory=list)
    dry_run: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "scanned_items": self.scanned_items,
            "raw_action_count": self.raw_action_count,
            "action_count": self.action_count,
            "suppressed_action_count": self.suppressed_action_count,
            "filtered_out_count": self.filtered_out_count,
            "safe_apply_count": self.safe_apply_count,
            "review_required_count": self.review_required_count,
            "blocked_count": self.blocked_count,
            "action_counts": self.action_counts,
            "category_counts": self.category_counts,
            "filters": self.filters,
            "next_commands": self.next_commands,
            "review_queue": [row.to_dict() for row in self.review_queue],
            "lanes": [lane.to_dict() for lane in self.lanes],
        }


def build_maintenance_plan(
    report: AutoGovernanceReport,
    *,
    limit_per_lane: int = 20,
    action_filter: str | None = None,
    category_filter: str | None = None,
    items_by_id: Mapping[str, MemoryItem] | None = None,
    supersedes_edges: Collection[tuple[str, str]] | None = None,
) -> MaintenancePlan:
    """Convert raw auto-governance actions into a user-facing plan."""
    deduped_actions = _suppress_duplicate_actions(report.actions)
    visible_actions = [
        action for action in deduped_actions
        if _matches_filters(
            action,
            action_filter=action_filter,
            category_filter=category_filter,
        )
    ]
    actions_by_risk: dict[ActionRisk, list[AutoGovernanceAction]] = {
        risk: [] for risk in _LANE_ORDER
    }
    for action in visible_actions:
        actions_by_risk[action.risk].append(action)

    lanes = [
        _build_lane(risk, actions_by_risk[risk], limit_per_lane=limit_per_lane)
        for risk in _LANE_ORDER
    ]
    normalized_items = dict(items_by_id or {})
    normalized_edges = set(supersedes_edges or ())
    normalized_edges.update(
        (item.superseded_by, item.id)
        for item in normalized_items.values()
        if item.superseded_by is not None
    )
    return MaintenancePlan(
        scanned_items=report.scanned_items,
        raw_action_count=len(report.actions),
        action_count=len(visible_actions),
        suppressed_action_count=len(report.actions) - len(deduped_actions),
        filtered_out_count=len(deduped_actions) - len(visible_actions),
        safe_apply_count=sum(1 for action in visible_actions if action.risk == "safe_apply"),
        review_required_count=sum(
            1 for action in visible_actions if action.risk == "review_required"
        ),
        blocked_count=sum(1 for action in visible_actions if action.risk == "blocked"),
        action_counts=_action_counts(visible_actions),
        category_counts=_category_counts(visible_actions),
        filters={
            "action": action_filter,
            "category": category_filter,
        },
        lanes=lanes,
        next_commands=_unique_commands(visible_actions),
        review_queue=_build_review_queue(
            lanes,
            items_by_id=normalized_items,
            supersedes_edges=normalized_edges,
        ),
    )


def _build_lane(
    risk: ActionRisk,
    actions: list[AutoGovernanceAction],
    *,
    limit_per_lane: int,
) -> MaintenancePlanLane:
    visible = actions[:max(0, limit_per_lane)]
    compact_actions = [_compact_action(action) for action in visible]
    return MaintenancePlanLane(
        risk=risk,
        title=_LANE_TITLES[risk],
        description=_LANE_DESCRIPTIONS[risk],
        count=len(actions),
        returned=len(compact_actions),
        truncated=len(actions) > len(compact_actions),
        next_command=_lane_command(risk, actions),
        actions=compact_actions,
    )


def _compact_action(action: AutoGovernanceAction) -> MaintenancePlanAction:
    return MaintenancePlanAction(
        action=action.action,
        category=_category_for_action(action),
        title=action.title,
        reason=action.reason,
        item_count=len(action.item_ids),
        item_ids=list(action.item_ids[:10]),
        command=_command_for_action(action),
        details=_compact_details(action.details),
        applied=action.applied,
    )


def _compact_details(details: dict[str, object]) -> dict[str, Any]:
    keep_keys = {
        "issue_type",
        "severity",
        "suggestion",
        "confidence",
        "audit_passed",
        "description",
        "score",
        "reasons",
        "from",
        "to",
        "missing_in_index",
        "orphan_in_index",
        "messages_to_rebalance",
        "recommended_distribution",
        "summary_rewrite",
        "lifecycle_type",
        "age_days",
        "stale_after_days",
        "recommended_action",
    }
    return {key: value for key, value in details.items() if key in keep_keys}


def _lane_command(risk: ActionRisk, actions: list[AutoGovernanceAction]) -> str:
    if risk == "safe_apply":
        return "memory govern auto --apply" if actions else ""
    for action in actions:
        command = _command_for_action(action)
        if command:
            return command
    return ""


def _unique_commands(actions: list[AutoGovernanceAction]) -> list[str]:
    commands: list[str] = []
    for action in actions:
        command = _command_for_action(action)
        if command and command not in commands:
            commands.append(command)
    return commands


def _build_review_queue(
    lanes: list[MaintenancePlanLane],
    *,
    items_by_id: Mapping[str, MemoryItem],
    supersedes_edges: set[tuple[str, str]],
) -> list[MaintenanceReviewQueueItem]:
    rows: list[MaintenanceReviewQueueItem] = []
    seen: set[str] = set()
    for lane in lanes:
        if lane.risk != "review_required":
            continue
        for action in lane.actions:
            if action.category != "lifecycle":
                continue
            for item_id in action.item_ids:
                if item_id in seen:
                    continue
                seen.add(item_id)
                obsolete = items_by_id.get(item_id)
                candidates = (
                    rank_supersession_candidates(
                        obsolete=obsolete,
                        items=items_by_id.values(),
                        supersedes_edges=supersedes_edges,
                    )
                    if obsolete is not None
                    else []
                )
                rows.append(
                    MaintenanceReviewQueueItem(
                        item_id=item_id,
                        action=action.action,
                        category=action.category,
                        title=action.title,
                        read_command=f"memory read {item_id} --head 2000 --view detail",
                        recommended_next=(
                            "select_supersession_or_keep_active"
                            if candidates
                            else "archive_after_review"
                        ),
                        can_auto_apply=False,
                        boundary="确认是否已有更新 item 可以 supersede，不能确认再 archive",
                        candidates=[candidate.to_dict() for candidate in candidates],
                    )
                )
    return rows


def _action_counts(actions: list[AutoGovernanceAction]) -> dict[str, int]:
    counter = Counter(action.action for action in actions)
    return dict(sorted(counter.items(), key=lambda entry: (-entry[1], entry[0])))


def _category_counts(actions: list[AutoGovernanceAction]) -> dict[str, int]:
    counter = Counter(_category_for_action(action) for action in actions)
    return dict(sorted(counter.items(), key=lambda entry: (-entry[1], entry[0])))


def _matches_filters(
    action: AutoGovernanceAction,
    *,
    action_filter: str | None,
    category_filter: str | None,
) -> bool:
    if action_filter and action.action != action_filter:
        return False
    if category_filter and _category_for_action(action) != category_filter:
        return False
    return True


def _category_for_action(action: AutoGovernanceAction) -> str:
    if action.action == "review_quality":
        issue_type = str(action.details.get("issue_type", "")).lower()
        reason = action.reason.lower()
        if issue_type == "duplicate" or "near-duplicate" in reason:
            return "near_duplicate"
        if "very long summary" in reason:
            return "summary_too_long"
        if "has no tags" in reason:
            return "missing_tags"
        return "quality"
    if action.action == "review_archive":
        issue_type = str(action.details.get("issue_type", "")).lower()
        if issue_type in {"stale_signal", "stale_handoff"}:
            return "lifecycle"
        return "expired"
    if action.action == "review_contradiction":
        return "contradiction"
    if action.action == "review_drift_cluster":
        return "drift_cluster"
    if action.action == "update_maturity":
        return "maturity_update"
    if action.action == "conversation_rebalance":
        return "conversation_rebalance"
    if action.action == "index_repair":
        return "index_repair"
    if action.action.startswith("review_evolve_"):
        return action.action.removeprefix("review_")
    if action.action.startswith("review_"):
        return action.action.removeprefix("review_")
    return action.action


def _suppress_duplicate_actions(
    actions: list[AutoGovernanceAction],
) -> list[AutoGovernanceAction]:
    lifecycle_archive_ids = {
        action.item_ids[0]
        for action in actions
        if (
            action.action == "review_archive"
            and len(action.item_ids) == 1
            and _category_for_action(action) == "lifecycle"
        )
    }
    direct_archive_ids = {
        action.item_ids[0]
        for action in actions
        if action.action == "review_archive" and len(action.item_ids) == 1
    }
    visible: list[AutoGovernanceAction] = []
    for action in actions:
        if (
            action.action == "review_archive"
            and len(action.item_ids) == 1
            and action.item_ids[0] in lifecycle_archive_ids
            and _category_for_action(action) == "expired"
        ):
            continue
        if (
            action.action == "review_evolve_archive"
            and len(action.item_ids) == 1
            and action.item_ids[0] in direct_archive_ids
        ):
            continue
        visible.append(action)
    return visible


def _command_for_action(action: AutoGovernanceAction | str) -> str:
    action_name = action.action if isinstance(action, AutoGovernanceAction) else action
    if (
        isinstance(action, AutoGovernanceAction)
        and action_name == "review_archive"
        and _category_for_action(action) == "lifecycle"
    ):
        return "memory govern plan --category lifecycle --format markdown"
    if action_name in {"update_maturity", "conversation_rebalance", "index_repair"}:
        return "memory govern auto --apply"
    if action_name in {"review_archive", "review_quality"}:
        return "memory govern run --format json"
    if action_name.startswith("review_evolve_"):
        return "memory evolve --format json"
    if action_name.startswith("review_"):
        return "memory anti-drift --format json"
    return ""


__all__ = [
    "MaintenancePlan",
    "MaintenancePlanAction",
    "MaintenancePlanLane",
    "MaintenanceReviewQueueItem",
    "build_maintenance_plan",
]
