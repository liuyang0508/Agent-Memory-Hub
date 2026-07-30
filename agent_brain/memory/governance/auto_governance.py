"""Safe auto-governance orchestration for memory maintenance.

This module coordinates existing governance primitives. Exact duplicates and
explicitly TTL-expired transient items use snapshot-backed reversible actions;
ambiguous merges, deletes, semantic consolidation, and skill synthesis remain
review-required.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.contracts.memory_enums import memory_enum_value
from agent_brain.memory.evidence.conversation_governance import classify_tier
from agent_brain.memory.evidence.conversation_store import ConversationStore
from agent_brain.memory.governance.confidence_review import assess_low_confidence
from agent_brain.memory.governance.contradiction_cases import (
    build_contradiction_cases,
)
from agent_brain.memory.governance.contradiction_containment import (
    contain_contradiction_case,
)
from agent_brain.memory.governance.contradiction_resolution import (
    build_contradiction_case_inventory,
)
from agent_brain.memory.governance.drift import DriftDetector
from agent_brain.memory.governance.evolve.engine import EvolveEngine
from agent_brain.memory.governance.lifecycle_archive import archive_reviewed_item
from agent_brain.memory.governance.maturity_scoring import score_maturity
from agent_brain.memory.governance.pipeline import GovernancePipeline
from agent_brain.memory.governance.signal_state import assess_signal_state
from agent_brain.memory.governance.summary_rewrite import preview_summary_rewrite
from agent_brain.memory.governance.supersession import SupersessionService
from agent_brain.memory.store.items_store import ItemsStore


ActionRisk = Literal["safe_apply", "review_required", "blocked"]
_LIFECYCLE_STALE_DAYS = {
    "signal": 30,
    "handoff": 30,
}


class _MemoryItemSnapshot(Mapping[str, MemoryItem]):
    """Caller-isolated item values backed by private deep-copy baselines."""

    def __init__(self, items: Mapping[str, MemoryItem]) -> None:
        self._items = MappingProxyType(
            {item_id: item.model_copy(deep=True) for item_id, item in items.items()}
        )

    def __getitem__(self, item_id: str) -> MemoryItem:
        return self._items[item_id].model_copy(deep=True)

    def __iter__(self) -> Iterator[str]:
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)


def lifecycle_review_due(item: MemoryItem, *, now: datetime) -> bool:
    """Return whether an active signal/handoff is due for lifecycle review."""
    item_type = str(memory_enum_value(item.type))
    stale_after_days = _LIFECYCLE_STALE_DAYS.get(item_type)
    if stale_after_days is None or item.superseded_by:
        return False
    if item_type == "signal":
        assessment = assess_signal_state(item, now=now)
        if assessment.state in {"resolved", "obsolete", "deferred"}:
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

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "items_by_id",
            _MemoryItemSnapshot(self.items_by_id),
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
        automatic = [
            *self._exact_duplicate_actions(items, apply=apply),
            *self._expired_ttl_actions(items, apply=apply),
        ]
        current_items = list(self.items_store.iter_all()) if apply else items
        automatic.extend(
            self._conflict_containment_actions(current_items, apply=apply)
        )
        actions.extend(automatic)
        automatically_managed = {
            item_id
            for action in automatic
            for item_id in action.item_ids
        }
        actions.extend(
            self._low_confidence_actions(
                items,
                skip_item_ids=automatically_managed,
            )
        )
        actions.extend(
            self._signal_state_actions(
                items,
                skip_item_ids=automatically_managed,
            )
        )
        actions.extend(
            self._lifecycle_actions(items, skip_item_ids=automatically_managed)
        )
        actions.extend(self._governance_actions(skip_item_ids=automatically_managed))
        actions.extend(self._drift_actions(skip_item_ids=automatically_managed))
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

    def _exact_duplicate_actions(
        self,
        items: list[tuple[MemoryItem, str]],
        *,
        apply: bool,
    ) -> list[AutoGovernanceAction]:
        groups: dict[tuple[object, ...], list[MemoryItem]] = {}
        for item, body in items:
            if item.superseded_by:
                continue
            digest = hashlib.sha256(
                (
                    item.title.strip().casefold()
                    + "\n"
                    + item.summary.strip().casefold()
                    + "\n"
                    + body.strip()
                ).encode("utf-8")
            ).hexdigest()
            key = (
                item.tenant_id,
                item.project,
                str(memory_enum_value(item.type)),
                str(memory_enum_value(item.sensitivity)),
                digest,
            )
            groups.setdefault(key, []).append(item)

        service = SupersessionService(
            self.brain_dir,
            self.items_store,
            index=self.index,
        )
        actions: list[AutoGovernanceAction] = []
        for duplicates in groups.values():
            if len(duplicates) < 2:
                continue
            canonical = max(duplicates, key=_canonical_duplicate_key)
            for duplicate in duplicates:
                if duplicate.id == canonical.id:
                    continue
                result = service.apply(
                    canonical.id,
                    duplicate.id,
                    apply=apply,
                )
                accepted = {"ready", "applied", "already_applied"}
                actions.append(AutoGovernanceAction(
                    action="supersede_exact_duplicate",
                    risk="safe_apply" if result.status in accepted else "blocked",
                    title=f"Collapse exact duplicate: {duplicate.title}",
                    reason="same_scope_title_summary_and_body_sha256",
                    item_ids=[duplicate.id, canonical.id],
                    details={
                        "canonical_id": canonical.id,
                        "duplicate_id": duplicate.id,
                        "transaction_status": result.status,
                        "snapshot": result.snapshot,
                        "index_repair_required": result.index_repair_required,
                    },
                    applied=result.status in {"applied", "already_applied"},
                ))
        return actions

    def _expired_ttl_actions(
        self,
        items: list[tuple[MemoryItem, str]],
        *,
        apply: bool,
    ) -> list[AutoGovernanceAction]:
        actions: list[AutoGovernanceAction] = []
        protected = {"blocker", "keep-active", "needs-review", "contested"}
        for item, _body in items:
            item_type = str(memory_enum_value(item.type))
            ttl = item.validity.ttl_hours
            observed = item.validity.observed_at or item.created_at
            if (
                item_type not in {"signal", "handoff"}
                or ttl is None
                or protected.intersection(item.tags)
                or self.now <= observed + timedelta(hours=ttl)
            ):
                continue
            status = "ready"
            reason = "explicit_ttl_expired"
            index_repair_required = False
            if apply:
                def eligible(
                    candidate: MemoryItem,
                    expected: str = item.id,
                ) -> bool:
                    candidate_ttl = candidate.validity.ttl_hours
                    return (
                        candidate.id == expected
                        and candidate_ttl is not None
                        and self.now
                        > (candidate.validity.observed_at or candidate.created_at)
                        + timedelta(hours=candidate_ttl)
                    )

                result = archive_reviewed_item(
                    brain_dir=self.brain_dir,
                    items_store=self.items_store,
                    item_id=item.id,
                    eligible=eligible,
                    index=self.index,
                )
                status = result.status
                reason = result.reason
                index_repair_required = result.index_repair_required
            accepted = {"ready", "applied", "already_applied"}
            actions.append(AutoGovernanceAction(
                action="archive_expired_ttl",
                risk="safe_apply" if status in accepted else "blocked",
                title=f"Archive expired {item_type}: {item.title}",
                reason=reason,
                item_ids=[item.id],
                details={
                    "ttl_hours": ttl,
                    "observed_at": observed.isoformat(),
                    "transaction_status": status,
                    "index_repair_required": index_repair_required,
                },
                applied=status in {"applied", "already_applied"},
            ))
        return actions

    def _conflict_containment_actions(
        self,
        items: list[tuple[MemoryItem, str]],
        *,
        apply: bool,
    ) -> list[AutoGovernanceAction]:
        findings = DriftDetector(self.items_store).detect().findings
        items_by_id = {item.id: (item, body) for item, body in items}
        actions: list[AutoGovernanceAction] = []
        for case in build_contradiction_cases(findings):
            scoped = [items_by_id.get(item_id) for item_id in case.item_ids]
            if any(value is None for value in scoped):
                continue
            loaded = [value for value in scoped if value is not None]
            if any(item.superseded_by for item, _body in loaded):
                continue
            if len({(item.tenant_id, item.project) for item, _body in loaded}) != 1:
                continue
            result = contain_contradiction_case(
                brain_dir=self.brain_dir,
                store=self.items_store,
                case=case,
                apply=apply,
                index=self.index if apply else None,
            )
            if result.status == "already_contained":
                continue
            accepted = {"ready", "applied"}
            actions.append(AutoGovernanceAction(
                action="contain_conflict",
                risk="safe_apply" if result.status in accepted else "blocked",
                title="Contain conflicting memories",
                reason=result.reason,
                item_ids=list(case.item_ids),
                details={
                    "case_id": case.case_id,
                    "confidence": case.confidence,
                    "evidence": list(case.evidence),
                    "transaction_status": result.status,
                    "transaction_id": result.transaction_id,
                    "snapshot": result.snapshot,
                    "index_repair_required": result.index_repair_required,
                    "reversible": (
                        "restore exact pre-containment tags/confidence from receipt"
                    ),
                },
                applied=result.status == "applied",
            ))
        return actions

    def _low_confidence_actions(
        self,
        items: list[tuple[MemoryItem, str]],
        *,
        skip_item_ids: set[str] | None = None,
    ) -> list[AutoGovernanceAction]:
        actions: list[AutoGovernanceAction] = []
        for item, _body in items:
            if item.id in (skip_item_ids or set()):
                continue
            assessment = assess_low_confidence(item)
            if assessment is None or not assessment.actionable:
                continue
            actions.append(
                AutoGovernanceAction(
                    action="review_low_confidence",
                    risk="review_required",
                    title=f"Review low confidence: {item.title}",
                    reason=f"low_confidence_{assessment.disposition}",
                    item_ids=[item.id],
                    details={
                        "issue_type": "low_confidence",
                        "disposition": assessment.disposition,
                        "confidence": item.confidence,
                        "explicit_source_ref_count": (
                            assessment.explicit_source_ref_count
                        ),
                        "provenance_ref_count": assessment.provenance_ref_count,
                        "recommended_action": assessment.recommended_action,
                    },
                )
            )
        return actions

    def _signal_state_actions(
        self,
        items: list[tuple[MemoryItem, str]],
        *,
        skip_item_ids: set[str] | None = None,
    ) -> list[AutoGovernanceAction]:
        actions: list[AutoGovernanceAction] = []
        for item, _body in items:
            if item.id in (skip_item_ids or set()):
                continue
            assessment = assess_signal_state(item, now=self.now)
            if assessment.consistent:
                continue
            actions.append(
                AutoGovernanceAction(
                    action="review_signal_state",
                    risk="review_required",
                    title=f"Review signal state: {item.title}",
                    reason="signal_lifecycle_state_inconsistent",
                    item_ids=[item.id],
                    details={
                        "issue_type": "signal_state_inconsistent",
                        "lifecycle_type": "signal",
                        "derived_state": assessment.state,
                        "issues": list(assessment.issues),
                        "recommended_action": "normalize_or_archive",
                    },
                )
            )
        return actions

    def _lifecycle_actions(
        self,
        items: list[tuple[Any, str]],
        *,
        skip_item_ids: set[str] | None = None,
    ) -> list[AutoGovernanceAction]:
        actions: list[AutoGovernanceAction] = []
        for item, _body in items:
            if item.id in (skip_item_ids or set()):
                continue
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

    def _governance_actions(
        self,
        *,
        skip_item_ids: set[str] | None = None,
    ) -> list[AutoGovernanceAction]:
        report = GovernancePipeline(items_store=self.items_store).run()
        items_by_id = {
            item.id: item for item, _body in self.items_store.iter_all()
        }
        actions: list[AutoGovernanceAction] = []
        for issue in report.issues:
            if (
                issue.item_id in (skip_item_ids or set())
                and issue.issue_type in {"duplicate", "expired"}
            ):
                continue
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

    def _drift_actions(
        self,
        *,
        skip_item_ids: set[str] | None = None,
    ) -> list[AutoGovernanceAction]:
        report = DriftDetector(self.items_store).detect()
        contradiction_cases = build_contradiction_cases(report.findings)
        contradiction_inventory = build_contradiction_case_inventory(
            brain_dir=self.brain_dir,
            store=self.items_store,
            cases=contradiction_cases,
            now=self.now,
        )
        open_case_ids = {
            case.case_id
            for case in contradiction_inventory.cases
            if case.status == "open"
        }
        actions = [
            AutoGovernanceAction(
                action="review_contradiction_case",
                risk="review_required",
                title=f"Review contradiction case: {case.case_id}",
                reason="overlapping_contradictions_grouped_for_review",
                item_ids=list(case.item_ids),
                details={
                    "case_id": case.case_id,
                    "pair_count": case.pair_count,
                    "item_count": len(case.item_ids),
                    "confidence": case.confidence,
                    "evidence": list(case.evidence),
                    "recommended_action": "classify_supersession_or_true_conflict",
                },
            )
            for case in contradiction_cases
            if case.case_id in open_case_ids
            if not set(case.item_ids).issubset(skip_item_ids or set())
        ]
        actions.extend([
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
            if finding.drift_type.value != "contradiction"
        ])
        return actions

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


def _canonical_duplicate_key(item: MemoryItem) -> tuple[object, ...]:
    return (
        item.support_count - item.contradict_count,
        item.gain_score,
        item.confidence,
        item.version,
        item.created_at,
        item.id,
    )


__all__ = [
    "AutoGovernanceAction",
    "AutoGovernanceCycle",
    "AutoGovernanceReport",
]
