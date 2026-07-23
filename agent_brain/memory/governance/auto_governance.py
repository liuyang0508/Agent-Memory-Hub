"""Safe auto-governance orchestration for memory maintenance.

This module coordinates existing governance primitives. It does not make
high-risk edits automatically: archive, delete, consolidate, supersede, and
skill synthesis stay review-required.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.contracts.memory_enums import memory_enum_value
from agent_brain.memory.evidence.conversation_governance import classify_tier
from agent_brain.memory.evidence.conversation_store import ConversationStore
from agent_brain.memory.governance.drift import DriftDetector
from agent_brain.memory.governance.evolve.engine import EvolveEngine
from agent_brain.memory.governance.maturity_scoring import score_maturity
from agent_brain.memory.governance.pipeline import GovernancePipeline
from agent_brain.memory.governance.summary_rewrite import preview_summary_rewrite
from agent_brain.memory.store.items_store import ItemsStore


ActionRisk = Literal["safe_apply", "review_required", "blocked"]
_LIFECYCLE_STALE_DAYS = {
    "signal": 30,
    "handoff": 30,
}


def lifecycle_review_due(item: MemoryItem, *, now: datetime) -> bool:
    """Return whether an active signal/handoff is due for lifecycle review."""
    item_type = str(memory_enum_value(item.type))
    stale_after_days = _LIFECYCLE_STALE_DAYS.get(item_type)
    if stale_after_days is None or item.superseded_by:
        return False
    observed_at = item.validity.observed_at or item.created_at
    return bool(max(0, (now - observed_at).days) > stale_after_days)


@dataclass(frozen=True)
class AutoGovernanceAction:
    """One proposed governance action."""

    action: str
    risk: ActionRisk
    title: str
    reason: str
    item_ids: list[str] = field(default_factory=list)
    details: dict[str, object] = field(default_factory=dict)
    applied: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AutoGovernanceReport:
    """Result of one auto-governance cycle."""

    scanned_items: int
    actions: list[AutoGovernanceAction]
    applied_count: int = 0
    apply: bool = False
    items_by_id: Mapping[str, MemoryItem] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )

    @property
    def safe_apply_count(self) -> int:
        return sum(1 for action in self.actions if action.risk == "safe_apply")

    @property
    def review_required_count(self) -> int:
        return sum(1 for action in self.actions if action.risk == "review_required")

    @property
    def blocked_count(self) -> int:
        return sum(1 for action in self.actions if action.risk == "blocked")

    def to_dict(self) -> dict[str, object]:
        return {
            "scanned_items": self.scanned_items,
            "action_count": len(self.actions),
            "safe_apply_count": self.safe_apply_count,
            "review_required_count": self.review_required_count,
            "blocked_count": self.blocked_count,
            "applied_count": self.applied_count,
            "apply": self.apply,
            "actions": [action.to_dict() for action in self.actions],
        }


class AutoGovernanceCycle:
    """Build and optionally apply a conservative memory-governance plan."""

    def __init__(
        self,
        *,
        brain_dir: Path,
        items_store: ItemsStore,
        index: Any | None = None,
        embedder: Any | None = None,
        conversation_store: ConversationStore | None = None,
        now: datetime | None = None,
        include_index: bool = True,
        include_conversations: bool = True,
        include_evolve: bool = True,
    ) -> None:
        self.brain_dir = Path(brain_dir)
        self.items_store = items_store
        self.index = index
        self.embedder = embedder
        self.conversation_store = conversation_store
        self.now = now or datetime.now(timezone.utc)
        self.include_index = include_index
        self.include_conversations = include_conversations
        self.include_evolve = include_evolve

    def run(self, *, apply: bool = False) -> AutoGovernanceReport:
        items = list(self.items_store.iter_all())
        actions: list[AutoGovernanceAction] = []
        actions.extend(self._maturity_actions(apply=apply))
        actions.extend(self._lifecycle_actions(items))
        actions.extend(self._governance_actions())
        actions.extend(self._drift_actions())
        if self.include_evolve:
            actions.extend(self._evolve_actions())
        if self.include_conversations:
            actions.extend(self._conversation_actions(apply=apply))
        if self.include_index:
            actions.extend(self._index_actions(apply=apply))

        applied_count = sum(1 for action in actions if action.applied)
        return AutoGovernanceReport(
            scanned_items=len(items),
            actions=actions,
            applied_count=applied_count,
            apply=apply,
            items_by_id={item.id: item for item, _body in items},
        )

    def _maturity_actions(self, *, apply: bool) -> list[AutoGovernanceAction]:
        actions: list[AutoGovernanceAction] = []
        for item, _body in self.items_store.iter_all():
            score = score_maturity(item)
            current_maturity = memory_enum_value(item.maturity)
            current_abstraction = memory_enum_value(item.abstraction)
            if (
                current_maturity == score.maturity
                and current_abstraction == score.abstraction
            ):
                continue
            applied = False
            if apply:
                self.items_store.update_frontmatter(
                    item.id,
                    maturity=score.maturity,
                    abstraction=score.abstraction,
                )
                applied = True
            actions.append(AutoGovernanceAction(
                action="update_maturity",
                risk="safe_apply",
                title=f"Update maturity for {item.title}",
                reason="maturity_score_recommendation",
                item_ids=[item.id],
                details={
                    "from": {
                        "maturity": current_maturity,
                        "abstraction": current_abstraction,
                    },
                    "to": {
                        "maturity": score.maturity,
                        "abstraction": score.abstraction,
                    },
                    "score": round(score.score, 4),
                    "reasons": list(score.reasons),
                },
                applied=applied,
            ))
        return actions

    def _lifecycle_actions(
        self,
        items: list[tuple[Any, str]],
    ) -> list[AutoGovernanceAction]:
        actions: list[AutoGovernanceAction] = []
        for item, _body in items:
            item_type = memory_enum_value(item.type)
            stale_after_days = _LIFECYCLE_STALE_DAYS.get(item_type)
            if stale_after_days is None:
                continue
            observed_at = item.validity.observed_at or item.created_at
            age_days = max(0, (self.now - observed_at).days)
            if not lifecycle_review_due(item, now=self.now):
                continue
            actions.append(AutoGovernanceAction(
                action="review_archive",
                risk="review_required",
                title=f"Review stale {item_type}: {item.title}",
                reason=f"stale_{item_type}_older_than_{stale_after_days}_days",
                item_ids=[item.id],
                details={
                    "issue_type": f"stale_{item_type}",
                    "lifecycle_type": item_type,
                    "age_days": age_days,
                    "stale_after_days": stale_after_days,
                    "recommended_action": "archive_or_supersede",
                },
            ))
        return actions

    def _governance_actions(self) -> list[AutoGovernanceAction]:
        report = GovernancePipeline(items_store=self.items_store).run()
        items_by_id = {
            item.id: item for item, _body in self.items_store.iter_all()
        }
        actions: list[AutoGovernanceAction] = []
        for issue in report.issues:
            action_name = "review_archive" if issue.issue_type == "expired" else "review_quality"
            details = {
                "issue_type": issue.issue_type,
                "severity": issue.severity,
                "suggestion": issue.suggestion,
            }
            item = items_by_id.get(issue.item_id)
            if item is not None and "very long summary" in issue.description.lower():
                details["summary_rewrite"] = preview_summary_rewrite(item.summary).to_dict()
            actions.append(AutoGovernanceAction(
                action=action_name,
                risk="review_required",
                title=f"Review {issue.issue_type}: {issue.item_id}",
                reason=issue.description,
                item_ids=[issue.item_id],
                details=details,
            ))
        return actions

    def _drift_actions(self) -> list[AutoGovernanceAction]:
        report = DriftDetector(self.items_store).detect()
        return [
            AutoGovernanceAction(
                action=f"review_{finding.drift_type.value}",
                risk="review_required",
                title=f"Review drift: {finding.drift_type.value}",
                reason=finding.description,
                item_ids=list(finding.item_ids),
                details={
                    "confidence": finding.confidence,
                    "evidence": finding.evidence,
                },
            )
            for finding in report.findings
        ]

    def _evolve_actions(self) -> list[AutoGovernanceAction]:
        report = EvolveEngine(
            items_store=self.items_store,
            dry_run=True,
            index=self.index,
        ).evolve()
        actions: list[AutoGovernanceAction] = []
        for proposal in report.proposals:
            risk: ActionRisk = "review_required"
            if proposal.audit_passed is False:
                risk = "blocked"
            actions.append(AutoGovernanceAction(
                action=f"review_evolve_{proposal.action.value}",
                risk=risk,
                title=proposal.title,
                reason=proposal.rationale,
                item_ids=list(proposal.item_ids),
                details={
                    "confidence": proposal.confidence,
                    "description": proposal.description,
                    "audit_passed": proposal.audit_passed,
                    "preview": proposal.output_preview,
                },
            ))
        return actions

    def _conversation_actions(self, *, apply: bool) -> list[AutoGovernanceAction]:
        conversation_store = self.conversation_store or ConversationStore(self.brain_dir)
        pending = []
        for message in conversation_store.iter_messages():
            recommended = classify_tier(message, now=self.now)
            if str(message.tier) != recommended.value:
                pending.append((message, recommended.value))
        if not pending:
            return []

        applied = False
        details: dict[str, object] = {
            "messages_to_rebalance": len(pending),
            "sample_message_ids": [message.id for message, _tier in pending[:10]],
            "recommended_distribution": _distribution(tier for _message, tier in pending),
        }
        if apply:
            rebalance = conversation_store.rebalance_tiers(now=self.now)
            details["rebalance"] = {
                "scanned": rebalance.scanned,
                "updated": rebalance.updated,
                "distribution": rebalance.distribution,
            }
            applied = rebalance.updated > 0

        return [AutoGovernanceAction(
            action="conversation_rebalance",
            risk="safe_apply",
            title="Rebalance raw conversation evidence tiers",
            reason="conversation_tier_recommendation",
            details=details,
            applied=applied,
        )]

    def _index_actions(self, *, apply: bool) -> list[AutoGovernanceAction]:
        if self.index is None:
            return []
        from agent_brain.interfaces.cli.commands.index_maintenance import (
            inspect_index_drift,
            repair_index_drift,
        )

        drift = inspect_index_drift(self.items_store, self.index)
        if not drift.missing_in_index and not drift.orphan_in_index:
            return []
        details: dict[str, object] = {
            "missing_in_index": sorted(drift.missing_in_index),
            "orphan_in_index": sorted(drift.orphan_in_index),
        }
        applied = False
        if apply:
            if self.embedder is None:
                raise ValueError("embedder is required to apply index repair")
            result = repair_index_drift(self.items_store, self.index, self.embedder, drift)
            details["repair"] = {"indexed": result.indexed, "pruned": result.pruned}
            applied = True
        return [AutoGovernanceAction(
            action="index_repair",
            risk="safe_apply",
            title="Repair derived index drift",
            reason="index_drift_detected",
            details=details,
            applied=applied,
        )]


def _distribution(values) -> dict[str, int]:
    distribution: dict[str, int] = {}
    for value in values:
        distribution[value] = distribution.get(value, 0) + 1
    return distribution


__all__ = [
    "AutoGovernanceAction",
    "AutoGovernanceCycle",
    "AutoGovernanceReport",
]
