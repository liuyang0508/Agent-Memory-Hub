"""Pure rule helpers used by the context firewall policy engine."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from agent_brain.memory.context.context_firewall_types import (
    CohortGateResult,
    ContextCandidate,
    ContextFirewallConfig,
    FirewallDecision,
)
from agent_brain.memory.context.query_signal import QuerySignal
from agent_brain.contracts.memory_item import MemoryItem

SOURCE_REQUIRED_TYPES = {"fact", "decision"}
CONTESTED_TAGS = {"contested", "contradiction", "conflict"}
REVIEW_REQUIRED_TAGS = {
    "needs-review",
    "requires-review",
    "review-rejected",
    "unverified-boundary",
}
TEMPORAL_CONFLICT_ANCHORS = (
    "browser",
    "permission",
    "test",
    "pytest",
    "build",
    "install",
    "release",
    "sync",
    "login",
    "浏览器",
    "权限",
    "测试",
    "验证",
    "构建",
    "安装",
    "发布",
    "同步",
    "登录",
)
TEMPORAL_SCOPE_FIELDS = ("cwd", "repo", "branch", "os", "adapter")
TOPIC_RECENCY_TYPES = {"fact", "decision", "signal", "handoff"}
TOPIC_TERM_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}")
TOPIC_RECENCY_STOPWORDS = {
    "after",
    "before",
    "current",
    "fixed",
    "from",
    "history",
    "latest",
    "memory",
    "new",
    "newer",
    "note",
    "old",
    "older",
    "path",
    "previous",
    "restored",
    "state",
    "status",
    "the",
    "this",
    "unavailable",
    "updated",
    "uses",
    "was",
    "with",
    "之前",
    "历史",
    "当前",
    "旧",
    "新",
}


def has_source_refs(item: MemoryItem) -> bool:
    """Return whether an item has any source reference usable as provenance."""
    refs = item.refs
    return bool(
        refs.files
        or refs.urls
        or refs.mems
        or refs.commits
        or refs.resources
        or refs.extractions
    )


def has_direct_evidence_refs(item: MemoryItem) -> bool:
    """Return whether an item points at primary evidence rather than memories."""
    refs = item.refs
    return bool(
        refs.files
        or refs.urls
        or refs.commits
        or refs.resources
        or refs.extractions
    )


def has_explicit_validity_boundary(item: MemoryItem) -> bool:
    """Return whether validity metadata says where or when the item applies."""
    validity = getattr(item, "validity", None)
    if validity is None:
        return False
    if getattr(validity, "observed_at", None) is not None:
        return True
    if getattr(validity, "ttl_hours", None) is not None:
        return True
    return any(
        getattr(validity, field, None) is not None
        for field in TEMPORAL_SCOPE_FIELDS
    )


def is_l0_evidence_only(item: MemoryItem) -> bool:
    """Return whether an L0 item lacks direct evidence and validity bounds."""
    return (
        str(getattr(item, "abstraction", "")) == "L0"
        and not has_direct_evidence_refs(item)
        and not has_explicit_validity_boundary(item)
    )


def has_strong_negative_feedback(
    item: MemoryItem,
    config: ContextFirewallConfig,
) -> bool:
    """Return whether feedback says this item should not be injected."""
    return (
        item.contradict_count >= config.negative_feedback_exclude_min_contradictions
        and item.gain_score <= config.negative_feedback_exclude_gain_threshold
        and item.contradict_count > item.support_count
    )


def matches_query(candidate: ContextCandidate, signal: QuerySignal) -> bool:
    """Return whether a candidate covers at least one query signal term."""
    haystack = candidate_haystack(candidate)
    if signal.strong_terms:
        return any(_term_matches_haystack(term, haystack) for term in signal.strong_terms)
    return any(_term_matches_haystack(term, haystack) for term in signal.terms)


def covered_strong_terms(
    included: list[FirewallDecision],
    strong_terms: tuple[str, ...],
) -> set[str]:
    """Return the strong query terms represented by an included cohort."""
    covered: set[str] = set()
    haystacks = [candidate_haystack(decision.candidate) for decision in included]
    for term in strong_terms:
        if any(_term_matches_haystack(term, haystack) for haystack in haystacks):
            covered.add(term)
    return covered


def covered_query_terms(
    candidate: ContextCandidate,
    terms: tuple[str, ...],
) -> set[str]:
    """Return the query terms represented by a single candidate."""
    haystack = candidate_haystack(candidate)
    return {
        term
        for term in terms
        if _term_matches_haystack(term, haystack)
    }


def candidate_haystack(candidate: ContextCandidate) -> str:
    """Return normalized searchable text for query-policy checks."""
    item = candidate.item
    return " ".join([
        item.title,
        item.summary,
        " ".join(item.tags),
        candidate.body,
    ]).lower()


def _term_matches_haystack(term: str, haystack: str) -> bool:
    term_lower = term.lower()
    if term_lower in haystack:
        return True
    compact_term = _compact_lookup_text(term_lower)
    if len(compact_term) < 4:
        return False
    return compact_term in _compact_lookup_text(haystack)


def _compact_lookup_text(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def temporal_conflict_anchors(candidate: ContextCandidate) -> tuple[str, ...]:
    """Return state-like terms that can anchor temporal conflict groups."""
    haystack = candidate_haystack(candidate)
    return tuple(anchor for anchor in TEMPORAL_CONFLICT_ANCHORS if anchor in haystack)


def temporal_scope_signature(item: MemoryItem) -> tuple[str, ...]:
    """Return the scope key used to compare state memories."""
    validity = getattr(item, "validity", None)
    return tuple([
        item.project or "",
        *(
            str(getattr(validity, field, "") or "")
            for field in TEMPORAL_SCOPE_FIELDS
        ),
    ])


def temporal_conflict_winner_key(decision: FirewallDecision) -> tuple[datetime, float]:
    """Sort key that prefers newer and then higher-scoring state facts."""
    return (
        aware(decision.candidate.item.created_at),
        decision.effective_score,
    )


def topic_recency_winner_key(decision: FirewallDecision) -> tuple[datetime, float]:
    """Sort key that prefers newer and then higher-scoring topic memories."""
    return (
        aware(decision.candidate.item.created_at),
        decision.effective_score,
    )


def topic_recency_terms(candidate: ContextCandidate) -> set[str]:
    """Return content terms used to detect same-topic stale candidates."""
    item = candidate.item
    text = " ".join([
        item.title,
        item.summary,
        " ".join(item.tags),
    ]).lower()
    return {
        term.strip("._-")
        for term in TOPIC_TERM_RE.findall(text)
        if term.strip("._-") and term.strip("._-") not in TOPIC_RECENCY_STOPWORDS
    }


def age_days(created_at: datetime, now: datetime) -> int:
    """Return non-negative integer age in days after normalizing time zones."""
    return max(0, (aware(now) - aware(created_at)).days)


def aware(value: datetime) -> datetime:
    """Return an aware datetime, treating naive values as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def exclude_with(decision: FirewallDecision, reason: str) -> FirewallDecision:
    """Return an excluded copy of an existing decision with one extra reason."""
    return FirewallDecision(
        candidate=decision.candidate,
        action="exclude",
        reasons=tuple([*decision.reasons, reason]),
        score=decision.score,
        effective_score=0.0,
    )


def reject_cohort(
    included: list[FirewallDecision],
    reason: str,
) -> CohortGateResult:
    """Reject a cohort while preserving per-candidate diagnostic reasons."""
    return CohortGateResult(
        included=[],
        excluded=[exclude_with(decision, reason) for decision in included],
        reasons=(reason,),
    )


__all__ = [
    "CONTESTED_TAGS",
    "REVIEW_REQUIRED_TAGS",
    "SOURCE_REQUIRED_TYPES",
    "TOPIC_RECENCY_TYPES",
    "age_days",
    "aware",
    "candidate_haystack",
    "covered_query_terms",
    "covered_strong_terms",
    "exclude_with",
    "has_source_refs",
    "has_strong_negative_feedback",
    "is_l0_evidence_only",
    "matches_query",
    "reject_cohort",
    "temporal_conflict_anchors",
    "temporal_conflict_winner_key",
    "temporal_scope_signature",
    "topic_recency_terms",
    "topic_recency_winner_key",
]
