"""Feedback value weighting for retrieval results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_brain.memory.recall.retrieval_types import RetrievedItem

if TYPE_CHECKING:
    from agent_brain.platform.indexing.index import HubIndex


@dataclass(frozen=True)
class FeedbackValueWeightConfig:
    support_weight: float = 0.03
    contradict_weight: float = 0.10
    gain_weight: float = 0.50
    min_multiplier: float = 0.25
    max_multiplier: float = 2.0


def feedback_value_multiplier(
    *,
    support_count: int,
    contradict_count: int,
    gain_score: float,
    config: FeedbackValueWeightConfig | None = None,
) -> float:
    cfg = config or FeedbackValueWeightConfig()
    raw = (
        1.0
        + max(0, support_count) * cfg.support_weight
        - max(0, contradict_count) * cfg.contradict_weight
        + gain_score * cfg.gain_weight
    )
    return min(cfg.max_multiplier, max(cfg.min_multiplier, raw))


def apply_feedback_value_weight(
    index: HubIndex,
    candidates: list[RetrievedItem],
    *,
    config: FeedbackValueWeightConfig | None = None,
) -> list[RetrievedItem]:
    """Apply bounded support/gain weighting without overpowering relevance."""
    if not candidates:
        return candidates
    feedback = index.get_feedback_data([candidate.id for candidate in candidates])
    result: list[RetrievedItem] = []
    for candidate in candidates:
        data = feedback.get(candidate.id)
        if data is None:
            result.append(candidate)
            continue
        support_count, contradict_count, gain_score = data
        multiplier = feedback_value_multiplier(
            support_count=support_count,
            contradict_count=contradict_count,
            gain_score=gain_score,
            config=config,
        )
        result.append(
            RetrievedItem(
                id=candidate.id,
                score=candidate.score * multiplier,
                bm25_rank=candidate.bm25_rank,
                vector_rank=candidate.vector_rank,
            )
        )
    result.sort(key=lambda item: item.score, reverse=True)
    return result


__all__ = [
    "FeedbackValueWeightConfig",
    "apply_feedback_value_weight",
    "feedback_value_multiplier",
]
