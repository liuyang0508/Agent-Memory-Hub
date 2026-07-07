"""Retention decay scoring for retrieval results."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from agent_brain.memory.recall.retrieval_types import RetrievedItem
from agent_brain.contracts.memory_item import DECAY_HALF_LIFE_DAYS

if TYPE_CHECKING:
    from agent_brain.platform.indexing.index import HubIndex


@dataclass(frozen=True)
class DecayBreakdown:
    """Explainable parts of one memory decay coefficient."""

    coefficient: float
    time_retention: float
    access_multiplier: float
    support_multiplier: float
    contradiction_multiplier: float
    gain_multiplier: float


def retention_factor(decay_class: str, days_since_access: float) -> float:
    """Compute retention factor using exponential decay with per-class half-lives."""
    half_life = DECAY_HALF_LIFE_DAYS.get(decay_class, 60)
    if days_since_access <= 0:
        return 1.0
    return math.pow(0.5, days_since_access / half_life)


def decay_breakdown(
    *,
    decay_class: str,
    days_since_reference: float,
    access_count: int = 0,
    support_count: int = 0,
    contradict_count: int = 0,
    gain_score: float = 0.0,
) -> DecayBreakdown:
    """Compute a bounded, multi-axis decay coefficient.

    Time is still the base forgetting curve. Access count, support feedback, and
    positive gain can reinforce old but repeatedly useful memory; contradictions
    and negative gain reduce retention. The final coefficient is bounded so this
    stage cannot overpower lexical/vector relevance.
    """
    time_retention = retention_factor(decay_class, days_since_reference)
    access_multiplier = 1.0 + min(0.35, math.log1p(max(0, access_count)) * 0.08)
    support_multiplier = 1.0 + min(0.18, max(0, support_count) * 0.03)
    gain_multiplier = 1.0 + max(-0.15, min(0.15, gain_score * 0.12))
    contradiction_multiplier = 1.0 - min(0.45, max(0, contradict_count) * 0.08)
    coefficient = (
        time_retention
        * access_multiplier
        * support_multiplier
        * gain_multiplier
        * contradiction_multiplier
    )
    return DecayBreakdown(
        coefficient=min(1.35, max(0.01, coefficient)),
        time_retention=time_retention,
        access_multiplier=access_multiplier,
        support_multiplier=support_multiplier,
        contradiction_multiplier=contradiction_multiplier,
        gain_multiplier=gain_multiplier,
    )


def decay_coefficient(
    *,
    decay_class: str,
    days_since_reference: float,
    access_count: int = 0,
    support_count: int = 0,
    contradict_count: int = 0,
    gain_score: float = 0.0,
) -> float:
    """Return only the effective coefficient for callers that do not need detail."""
    return decay_breakdown(
        decay_class=decay_class,
        days_since_reference=days_since_reference,
        access_count=access_count,
        support_count=support_count,
        contradict_count=contradict_count,
        gain_score=gain_score,
    ).coefficient


class RetrievalDecay:
    """Apply confidence and retention decay to retrieved candidates."""

    def __init__(self, index: HubIndex) -> None:
        self.index = index

    def apply(self, candidates: list[RetrievedItem]) -> list[RetrievedItem]:
        """Return candidates re-scored by confidence and retention factor."""
        if not candidates:
            return candidates
        ids = [c.id for c in candidates]
        if hasattr(self.index, "get_decay_data"):
            decay_data = self.index.get_decay_data(ids)
        else:
            decay_data = {
                item_id: (confidence, decay_cls, last_accessed, None, 0, 0, 0, 0.0)
                for item_id, (confidence, decay_cls, last_accessed)
                in self.index.get_confidence_data(ids).items()
            }
        now = datetime.now(timezone.utc)
        result = []
        for c in candidates:
            data = decay_data.get(c.id)
            if data is None:
                result.append(c)
                continue
            (
                confidence,
                decay_cls,
                last_acc_iso,
                created_at_iso,
                access_count,
                support_count,
                contradict_count,
                gain_score,
            ) = data
            ref_iso = last_acc_iso or created_at_iso
            if ref_iso:
                try:
                    reference_at = datetime.fromisoformat(ref_iso)
                    if reference_at.tzinfo is None:
                        reference_at = reference_at.replace(tzinfo=timezone.utc)
                    days = (now - reference_at).total_seconds() / 86400
                except (ValueError, TypeError):
                    days = 0.0
            else:
                days = 0.0
            coefficient = decay_coefficient(
                decay_class=decay_cls,
                days_since_reference=days,
                access_count=access_count,
                support_count=support_count,
                contradict_count=contradict_count,
                gain_score=gain_score,
            )
            effective = c.score * confidence * coefficient
            result.append(RetrievedItem(
                id=c.id,
                score=effective,
                bm25_rank=c.bm25_rank,
                vector_rank=c.vector_rank,
            ))
        result.sort(key=lambda item: item.score, reverse=True)
        return result


__all__ = [
    "DecayBreakdown",
    "RetrievalDecay",
    "decay_breakdown",
    "decay_coefficient",
    "retention_factor",
]
