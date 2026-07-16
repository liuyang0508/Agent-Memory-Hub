"""Before-inject firewall for memory context candidates.

The firewall is deliberately independent from retrieval and brief assembly so
callers can test policy decisions before wiring it into user-facing injection.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Mapping

from agent_brain.memory.context.answerability import (
    AnswerabilityVerifier,
    answerability_verifier_from_env,
    preselect_routed_candidate_ids,
    verify_candidate_answerability,
    verify_routed_candidate_answerability,
)
from agent_brain.memory.context.context_firewall_rules import (
    CONTESTED_TAGS,
    REVIEW_REQUIRED_TAGS,
    SOURCE_REQUIRED_TYPES,
    TOPIC_RECENCY_TYPES,
    age_days,
    aware,
    covered_query_terms,
    covered_strong_terms,
    exclude_with,
    has_source_refs,
    has_strong_negative_feedback,
    is_l0_evidence_only,
    matches_query,
    reject_cohort,
    temporal_conflict_anchors,
    temporal_conflict_winner_key,
    temporal_scope_signature,
    topic_recency_terms,
    topic_recency_winner_key,
)
from agent_brain.memory.context.context_firewall_types import (
    CohortGateResult,
    ContextCandidate,
    ContextFirewallConfig,
    FirewallAction,
    FirewallDecision,
    FirewallResult,
)
from agent_brain.memory.context.context_packing import pack_decisions
from agent_brain.memory.context.injection_query_context import InjectionQueryContext
from agent_brain.memory.context.query_signal import QuerySignal, analyze_injection_query
from agent_brain.memory.governance.temporal_state import TemporalStateGate
from agent_brain.platform.bounded_jsonl import MAX_SAFE_INTEGER


def _is_safe_candidate_score(value: object) -> bool:
    """Return whether a retrieval score is finite and JSON-number safe."""
    if type(value) is int:
        return abs(value) <= MAX_SAFE_INTEGER
    if type(value) is float:
        return value == value and abs(value) <= MAX_SAFE_INTEGER
    return False


class ContextFirewall:
    """Rank and filter candidate memories before they are injected as context."""

    def __init__(
        self,
        config: ContextFirewallConfig | None = None,
        *,
        now: datetime | None = None,
        answerability_verifier: AnswerabilityVerifier | None = None,
    ) -> None:
        self.config = config or ContextFirewallConfig()
        self.now = aware(now or datetime.now(timezone.utc))
        self.answerability_verifier = (
            answerability_verifier
            if answerability_verifier is not None
            else answerability_verifier_from_env()
        )

    def filter(
        self,
        candidates: list[ContextCandidate],
        *,
        query: str | None = None,
        query_signal: QuerySignal | None = None,
        max_items: int | None = None,
        budget_tokens: int | None = None,
        current_scope: Mapping[str, str] | None = None,
        query_context: InjectionQueryContext | None = None,
    ) -> FirewallResult:
        """Evaluate and pack candidates for injection.

        The method first applies per-item gates, then cohort-level conflict
        gates, duplicate limits, optional item count and token budget limits,
        and finally query-cohort coverage checks.
        """
        query_context = _validated_query_context(
            query_context,
            query=query,
            query_signal=query_signal,
        )
        query_text: str | None
        signal: QuerySignal | None
        if query_context is not None:
            query_text = query_context.raw_query
            signal = query_context.query_signal
        else:
            query_text = query.replace("|", " ") if query else None
            signal = query_signal
            if signal is None and query:
                signal = analyze_injection_query(query_text or "")
                if not signal.injectable:
                    signal = None
        routed_candidate_ids = (
            preselect_routed_candidate_ids(candidates, query_context, self.config)
            if query_context is not None
            else None
        )
        evaluated = [
            self._evaluate(
                candidate,
                signal=signal,
                query=query_text,
                current_scope=current_scope,
                query_context=query_context,
                routed_candidate_ids=routed_candidate_ids,
            )
            for candidate in candidates
        ]
        hard_excluded = [d for d in evaluated if d.action == "exclude"]
        packable = [d for d in evaluated if d.action != "exclude"]
        packable.sort(key=lambda d: d.effective_score, reverse=True)

        included: list[FirewallDecision] = []
        extra_excluded: list[FirewallDecision] = []
        temporal_conflicts = self._apply_temporal_conflict_gate(packable)
        packable = temporal_conflicts.included
        extra_excluded.extend(temporal_conflicts.excluded)
        topic_conflicts = self._apply_topic_recency_gate(
            packable,
            signal=signal,
            query_context=query_context,
        )
        packable = topic_conflicts.included
        extra_excluded.extend(topic_conflicts.excluded)
        cluster_counts: dict[str, int] = {}
        used_tokens = 0
        limit = max_items if max_items is not None else len(packable)

        for decision in packable:
            cluster_key = self._cluster_key(decision.candidate)
            count = cluster_counts.get(cluster_key, 0)
            if count >= self.config.max_per_duplicate_cluster:
                extra_excluded.append(exclude_with(decision, "duplicate_cluster"))
                continue

            if len(included) >= limit:
                extra_excluded.append(exclude_with(decision, "max_items_exceeded"))
                continue

            if budget_tokens is not None:
                packed = pack_decisions(
                    [decision],
                    requested="auto",
                    budget_tokens=budget_tokens - used_tokens,
                )
                if not packed.included:
                    extra_excluded.append(exclude_with(decision, "pack_budget_exceeded"))
                    continue
                packed_decision = packed.included[0]
                used_tokens += packed_decision.pack.packed_tokens
                decision = packed_decision.decision

            cluster_counts[cluster_key] = count + 1
            included.append(decision)

        cohort = self._apply_cohort_gate(
            included,
            signal=signal,
            query_context=query_context,
        )
        included = cohort.included
        extra_excluded.extend(cohort.excluded)

        excluded = hard_excluded + extra_excluded
        by_id = {d.candidate.item.id: d for d in included + excluded}
        decisions = [by_id[candidate.item.id] for candidate in candidates]
        return FirewallResult(
            included=included,
            excluded=excluded,
            decisions=decisions,
            cohort_reasons=(*temporal_conflicts.reasons, *cohort.reasons),
        )

    def validate_cohort(
        self,
        included: list[FirewallDecision],
        *,
        query_signal: QuerySignal | None,
        query_context: InjectionQueryContext | None = None,
    ) -> CohortGateResult:
        """Recheck query-level cohort rules without repeating item evaluation."""
        query_context = _validated_query_context(
            query_context,
            query=None,
            query_signal=query_signal,
        )
        signal = query_context.query_signal if query_context is not None else query_signal
        return self._apply_cohort_gate(
            included,
            signal=signal,
            query_context=query_context,
        )

    def _apply_temporal_conflict_gate(
        self,
        included: list[FirewallDecision],
    ) -> CohortGateResult:
        if len(included) < 2:
            return CohortGateResult(included=included, excluded=[], reasons=())

        groups: dict[tuple[str, tuple[str, ...]], list[tuple[FirewallDecision, str]]] = {}
        gate = TemporalStateGate(now=self.now)
        for decision in included:
            item = decision.candidate.item
            signal = gate.evaluate(item, decision.candidate.body)
            if signal.category not in ("negative_state", "positive_state"):
                continue
            for anchor in temporal_conflict_anchors(decision.candidate):
                key = (anchor, temporal_scope_signature(item))
                groups.setdefault(key, []).append((decision, signal.category))

        excluded_ids: set[str] = set()
        for group in groups.values():
            categories = {category for _decision, category in group}
            if not {"negative_state", "positive_state"}.issubset(categories):
                continue
            winner = max(
                (decision for decision, _category in group),
                key=temporal_conflict_winner_key,
            )
            for decision, _category in group:
                if decision.candidate.item.id != winner.candidate.item.id:
                    excluded_ids.add(decision.candidate.item.id)

        if not excluded_ids:
            return CohortGateResult(included=included, excluded=[], reasons=())

        kept: list[FirewallDecision] = []
        excluded: list[FirewallDecision] = []
        for decision in included:
            if decision.candidate.item.id in excluded_ids:
                excluded.append(exclude_with(decision, "temporal_state_conflict_newer"))
            else:
                kept.append(decision)
        return CohortGateResult(
            included=kept,
            excluded=excluded,
            reasons=("temporal_state_conflict_newer",),
        )

    def _apply_topic_recency_gate(
        self,
        included: list[FirewallDecision],
        *,
        signal: QuerySignal | None,
        query_context: InjectionQueryContext | None = None,
    ) -> CohortGateResult:
        eligible = (
            query_context.admission.allowed
            if query_context is not None
            else signal is not None and signal.injectable
        )
        if len(included) < 2 or signal is None or not eligible:
            return CohortGateResult(included=included, excluded=[], reasons=())

        excluded_ids: set[str] = set()
        for left_index, left in enumerate(included):
            for right in included[left_index + 1:]:
                if not self._is_topic_recency_conflict(left, right, signal=signal):
                    continue
                winner = max((left, right), key=topic_recency_winner_key)
                loser = right if winner.candidate.item.id == left.candidate.item.id else left
                if aware(winner.candidate.item.created_at) <= aware(loser.candidate.item.created_at):
                    continue
                excluded_ids.add(loser.candidate.item.id)

        if not excluded_ids:
            return CohortGateResult(included=included, excluded=[], reasons=())

        kept: list[FirewallDecision] = []
        excluded: list[FirewallDecision] = []
        for decision in included:
            if decision.candidate.item.id in excluded_ids:
                excluded.append(exclude_with(decision, "topic_recency_newer"))
            else:
                kept.append(decision)
        return CohortGateResult(
            included=kept,
            excluded=excluded,
            reasons=("topic_recency_newer",),
        )

    def _is_topic_recency_conflict(
        self,
        left: FirewallDecision,
        right: FirewallDecision,
        *,
        signal: QuerySignal,
    ) -> bool:
        left_item = left.candidate.item
        right_item = right.candidate.item
        if str(left_item.type) != str(right_item.type):
            return False
        if str(left_item.type) not in TOPIC_RECENCY_TYPES:
            return False
        if temporal_scope_signature(left_item) != temporal_scope_signature(right_item):
            return False
        if not matches_query(left.candidate, signal) or not matches_query(right.candidate, signal):
            return False

        shared_terms = (
            topic_recency_terms(left.candidate)
            & topic_recency_terms(right.candidate)
        )
        return len(shared_terms) >= self.config.topic_recency_min_shared_terms

    def _apply_cohort_gate(
        self,
        included: list[FirewallDecision],
        *,
        signal: QuerySignal | None,
        query_context: InjectionQueryContext | None = None,
    ) -> CohortGateResult:
        if query_context is not None and not query_context.admission.allowed:
            return reject_cohort(included, "query_not_injectable")

        if signal is None:
            return CohortGateResult(included=included, excluded=[], reasons=())

        if query_context is None and not signal.injectable:
            return reject_cohort(included, "query_not_injectable")

        if not included:
            return CohortGateResult(included=[], excluded=[], reasons=())

        if signal.strong_terms:
            covered = covered_strong_terms(included, signal.strong_terms)
            coverage = len(covered) / len(signal.strong_terms)
            if coverage < self.config.min_strong_term_coverage:
                return reject_cohort(included, "cohort_strong_anchor_undercovered")

        return CohortGateResult(included=included, excluded=[], reasons=())

    def _evaluate(
        self,
        candidate: ContextCandidate,
        *,
        signal: QuerySignal | None = None,
        query: str | None = None,
        current_scope: Mapping[str, str] | None = None,
        query_context: InjectionQueryContext | None = None,
        routed_candidate_ids: frozenset[str] | None = None,
    ) -> FirewallDecision:
        item = candidate.item
        item_type = str(item.type)
        reasons: list[str] = []
        action: FirewallAction = "include"
        if not _is_safe_candidate_score(candidate.score):
            return FirewallDecision(
                candidate,
                "exclude",
                ("invalid_candidate_score",),
                0.0,
                0.0,
            )
        base_score = candidate.score if candidate.score > 0 else item.confidence
        effective_score = base_score

        sensitivity = str(getattr(item.sensitivity, "value", item.sensitivity))
        if sensitivity not in self.config.allowed_sensitivities:
            reasons.append("sensitivity_not_allowed")
            return FirewallDecision(candidate, "exclude", tuple(reasons), base_score, 0.0)

        if item.superseded_by:
            reasons.append("superseded")
            return FirewallDecision(candidate, "exclude", tuple(reasons), base_score, 0.0)

        if REVIEW_REQUIRED_TAGS & {tag.lower() for tag in item.tags}:
            reasons.append("requires_review")
            return FirewallDecision(candidate, "exclude", tuple(reasons), base_score, 0.0)

        if query_context is not None:
            if not query_context.admission.allowed:
                reasons.append("query_not_injectable")
                return FirewallDecision(
                    candidate,
                    "exclude",
                    tuple(reasons),
                    base_score,
                    0.0,
                )
            if (
                routed_candidate_ids is not None
                and item.id not in routed_candidate_ids
            ):
                reasons.append("route_answerability_insufficient")
                return FirewallDecision(
                    candidate,
                    "exclude",
                    tuple(reasons),
                    base_score,
                    0.0,
                )
            answerability = verify_routed_candidate_answerability(
                candidate,
                query_context,
                self.config,
                verifier=self.answerability_verifier,
            )
            if not answerability.answerable:
                reasons.append(answerability.reason)
                if answerability.reason == "query_mismatch":
                    reasons.append("answerability_mismatch")
                return FirewallDecision(candidate, "exclude", tuple(reasons), base_score, 0.0)
        elif signal and signal.injectable:
            answerability = verify_candidate_answerability(
                candidate,
                signal,
                query=query,
                verifier=self.answerability_verifier,
            )
            if not answerability.answerable:
                reasons.append(answerability.reason)
                if answerability.reason == "query_mismatch":
                    reasons.append("answerability_mismatch")
                return FirewallDecision(candidate, "exclude", tuple(reasons), base_score, 0.0)

        if item.confidence < self.config.low_confidence_exclude_threshold:
            reasons.append("very_low_confidence")
            return FirewallDecision(candidate, "exclude", tuple(reasons), base_score, 0.0)

        if has_strong_negative_feedback(item, self.config):
            reasons.append("negative_feedback")
            return FirewallDecision(candidate, "exclude", tuple(reasons), base_score, 0.0)

        if (
            self.config.require_source_for_fact_decision
            and item_type in SOURCE_REQUIRED_TYPES
            and not has_source_refs(item)
        ):
            reasons.append("missing_source")
            return FirewallDecision(candidate, "exclude", tuple(reasons), base_score, 0.0)

        if item_type == "signal" and age_days(item.created_at, self.now) > self.config.stale_signal_days:
            reasons.append("stale_signal")
            return FirewallDecision(candidate, "exclude", tuple(reasons), base_score, 0.0)

        if item_type == "handoff" and age_days(item.created_at, self.now) > self.config.stale_handoff_days:
            reasons.append("stale_handoff")
            return FirewallDecision(candidate, "exclude", tuple(reasons), base_score, 0.0)

        temporal = TemporalStateGate(now=self.now).evaluate(
            item,
            candidate.body,
            current_scope=current_scope,
        )
        if temporal.status == "scope_mismatch":
            reasons.append("scope_mismatch")
            return FirewallDecision(candidate, "exclude", tuple(reasons), base_score, 0.0)
        if temporal.status == "stale":
            reason = f"stale_{temporal.category}"
            reasons.append(reason)
            return FirewallDecision(candidate, "exclude", tuple(reasons), base_score, 0.0)

        if CONTESTED_TAGS & {tag.lower() for tag in item.tags}:
            reasons.append("contested")
            action = "demote"
            effective_score *= self.config.contested_penalty

        if item.confidence < self.config.low_confidence_demote_threshold:
            reasons.append("low_confidence")
            action = "demote"
            effective_score *= self.config.low_confidence_penalty

        if is_l0_evidence_only(item):
            reasons.append("l0_evidence_only")
            action = "demote"
            effective_score *= self.config.l0_evidence_only_penalty

        query_eligible = (
            query_context.admission.allowed
            if query_context is not None
            else signal is not None and signal.injectable
        )
        if signal and query_eligible and signal.terms:
            effective_score += (
                len(covered_query_terms(candidate, signal.terms))
                * self.config.query_term_coverage_bonus
            )

        return FirewallDecision(candidate, action, tuple(reasons), base_score, effective_score)

    def _cluster_key(self, candidate: ContextCandidate) -> str:
        if candidate.cluster_key:
            return candidate.cluster_key
        item = candidate.item
        payload = f"{item.project or ''}\n{item.title.strip().lower()}\n{item.summary.strip().lower()}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validated_query_context(
    query_context: InjectionQueryContext | None,
    *,
    query: str | None,
    query_signal: QuerySignal | None,
) -> InjectionQueryContext | None:
    if query_context is None:
        return None
    if not isinstance(query_context, InjectionQueryContext):
        raise TypeError("query_context must be an InjectionQueryContext")
    if query is not None and query != query_context.raw_query:
        raise ValueError("query conflicts with routed query context")
    if query_signal is not None and query_signal != query_context.query_signal:
        raise ValueError("query_signal conflicts with routed query context")
    return query_context


__all__ = [
    "ContextCandidate",
    "ContextFirewall",
    "ContextFirewallConfig",
    "FirewallAction",
    "FirewallDecision",
    "FirewallResult",
]
