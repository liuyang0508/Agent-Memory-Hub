"""Documented retrieval scoring formulas.

This module does not replace the retrieval pipeline; it makes the formulas used
by docs, tests, diagrams, and future explainability code explicit and reusable.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScoreBreakdown:
    """Multiplicative retrieval score with an ordered waterfall trace."""

    final_score: float
    waterfall: tuple[tuple[str, float], ...]


def rrf_base_score(
    *,
    bm25_rank: int | None,
    vector_rank: int | None,
    rrf_k: int = 60,
    bm25_weight: float = 1.0,
    vector_weight: float = 1.0,
) -> float:
    """Return S0 from BM25/vector Reciprocal Rank Fusion.

    Rank is zero-based, matching the existing retrieval_fusion implementation:
    first result contributes weight / (k + 1).
    """
    score = 0.0
    if bm25_rank is not None:
        score += bm25_weight / (rrf_k + bm25_rank + 1)
    if vector_rank is not None:
        score += vector_weight / (rrf_k + vector_rank + 1)
    return score


def final_score_breakdown(
    *,
    base_score: float,
    confidence: float = 1.0,
    retention: float = 1.0,
    feedback_value: float = 1.0,
    status_boost: float = 1.0,
    adapter_runtime_boost: float = 1.0,
    freshness_guard: float = 1.0,
) -> ScoreBreakdown:
    """Return S1 and an ordered score waterfall."""
    running = base_score
    steps = [("S0", running)]
    for name, factor in (
        ("confidence", confidence),
        ("retention", retention),
        ("feedback_value", feedback_value),
        ("status_boost", status_boost),
        ("adapter_runtime_boost", adapter_runtime_boost),
        ("freshness_guard", freshness_guard),
    ):
        running *= factor
        steps.append((name, running))
    return ScoreBreakdown(final_score=running, waterfall=tuple(steps))


def graph_neighbor_score(*, min_score: float, alpha: float = 0.5) -> float:
    """Return the score assigned to a graph-expanded neighbor."""
    return min_score * alpha


def mmr_score(*, relevance: float, max_similarity: float, lambda_: float) -> float:
    """Return Maximal Marginal Relevance score for one candidate."""
    return lambda_ * relevance - (1.0 - lambda_) * max_similarity


__all__ = [
    "ScoreBreakdown",
    "final_score_breakdown",
    "graph_neighbor_score",
    "mmr_score",
    "rrf_base_score",
]
