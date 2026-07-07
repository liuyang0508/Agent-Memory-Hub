"""Explainable maturity scoring for MemoryItem governance."""
from __future__ import annotations

from dataclasses import dataclass

from agent_brain.contracts.memory_enums import memory_enum_value
from agent_brain.contracts.memory_item import MemoryItem


@dataclass(frozen=True)
class MaturityScore:
    score: float
    maturity: str
    abstraction: str
    reasons: tuple[str, ...]


STALE_SCOPE_TAGS = {
    "stale",
    "stale-state",
    "outdated",
    "expired",
    "needs-review",
    "requires-review",
}


def score_maturity(item: MemoryItem) -> MaturityScore:
    """Score whether an item should remain raw or mature into reusable memory.

    The score is intentionally bounded and explainable. It is a governance
    recommendation, not a hard mutation; callers decide whether to persist it.
    """
    score = 0.0
    reasons: list[str] = []

    source_completeness = _source_completeness(item)
    score += source_completeness * 0.28
    if _has_direct_source_refs(item):
        reasons.append("direct_source_refs")
    elif item.refs.mems:
        reasons.append("memory_source_refs")
    else:
        reasons.append("no_source_refs")

    confidence_component = max(0.0, min(1.0, item.confidence)) * 0.22
    score += confidence_component

    support_component = min(0.18, max(0, item.support_count) * 0.03)
    score += support_component
    if item.support_count > 0 or item.gain_score > 0:
        reasons.append("positive_feedback")

    reuse_component = min(0.12, max(0, item.retention.access_count) * 0.02)
    score += reuse_component
    if item.retention.access_count > 0:
        reasons.append("reuse_signal")

    graph_component = min(0.12, len(item.refs.mems) * 0.04)
    score += graph_component
    if item.refs.mems:
        reasons.append("graph_citations")

    validation_component = _validation_evidence_score(item)
    score += validation_component
    if validation_component:
        reasons.append("validation_evidence")

    if item.context_views.overview:
        score += 0.08
        reasons.append("overview_present")

    if item.gain_score > 0:
        score += min(0.10, item.gain_score * 0.10)

    contradiction_penalty = min(0.35, max(0, item.contradict_count) * 0.07)
    if contradiction_penalty:
        score -= contradiction_penalty
        reasons.append("contradiction_penalty")

    if _has_stale_scope(item):
        score -= 0.18
        reasons.append("stale_scope_penalty")

    score = max(0.0, min(1.0, score))
    maturity, abstraction = _classify(score, item)
    return MaturityScore(
        score=score,
        maturity=maturity,
        abstraction=abstraction,
        reasons=tuple(dict.fromkeys(reasons)),
    )


def _classify(score: float, item: MemoryItem) -> tuple[str, str]:
    item_type = memory_enum_value(item.type)
    abstraction = memory_enum_value(item.abstraction)
    if score >= 0.80 and (item_type == "skill" or abstraction == "L2"):
        return "skill", "L2"
    if score >= 0.65:
        return "consolidated", "L1"
    return "raw", "L0"


def _source_completeness(item: MemoryItem) -> float:
    if _has_direct_source_refs(item):
        return 1.0
    if item.refs.mems:
        return 0.55
    return 0.0


def _has_direct_source_refs(item: MemoryItem) -> bool:
    refs = item.refs
    return bool(
        refs.files
        or refs.urls
        or refs.commits
        or refs.resources
        or refs.extractions
    )


def _validation_evidence_score(item: MemoryItem) -> float:
    paths = [path.lower() for path in item.refs.files]
    if any(_is_validation_path(path) for path in paths):
        return 0.07
    if item.refs.commits:
        return 0.04
    return 0.0


def _is_validation_path(path: str) -> bool:
    return (
        "/tests/" in f"/{path}"
        or path.startswith("tests/")
        or "test_" in path
        or "_test." in path
        or "docs/evaluation/" in path
        or "benchmark" in path
        or "eval" in path
    )


def _has_stale_scope(item: MemoryItem) -> bool:
    tags = {tag.lower() for tag in item.tags}
    if tags & STALE_SCOPE_TAGS:
        return True
    return bool(getattr(item, "superseded_by", None))


__all__ = ["MaturityScore", "score_maturity"]
