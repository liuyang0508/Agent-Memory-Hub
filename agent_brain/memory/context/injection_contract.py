"""Lightweight shared contract for prompt-injection exclusion accounting."""

from __future__ import annotations


HYDRATE_ERROR_REASON = "hydrate_error"
PACK_ERROR_REASON = "pack_error"
GATEWAY_SYNTHETIC_EXCLUSION_REASONS = frozenset({
    HYDRATE_ERROR_REASON,
    PACK_ERROR_REASON,
})
INJECTION_EXCLUSION_REASONS = frozenset({
    "answerability_mismatch",
    "cohort_strong_anchor_undercovered",
    "contested",
    "duplicate_cluster",
    "invalid_candidate_score",
    "l0_evidence_only",
    "low_confidence",
    "max_items_exceeded",
    "missing_source",
    "negative_feedback",
    "pack_budget_exceeded",
    "query_mismatch",
    "query_not_injectable",
    "route_answerability_insufficient",
    "requires_review",
    "scope_mismatch",
    "semantic_answerability_mismatch",
    "sensitivity_not_allowed",
    "stale_current_state",
    "stale_handoff",
    "stale_negative_state",
    "stale_positive_state",
    "stale_signal",
    "superseded",
    "temporal_state_conflict_newer",
    "topic_recency_newer",
    "very_low_confidence",
}) | GATEWAY_SYNTHETIC_EXCLUSION_REASONS


__all__ = [
    "GATEWAY_SYNTHETIC_EXCLUSION_REASONS",
    "HYDRATE_ERROR_REASON",
    "INJECTION_EXCLUSION_REASONS",
    "PACK_ERROR_REASON",
]
