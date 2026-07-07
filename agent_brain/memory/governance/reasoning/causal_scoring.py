"""Causal likelihood scoring for reasoning chains."""
from __future__ import annotations

from agent_brain.platform.embedding import Embedder
from agent_brain.contracts.memory_item import MemoryItem, MemoryType


class CausalScorer:
    """Score whether one memory item plausibly caused another."""

    def __init__(
        self,
        embedder: Embedder | None = None,
        *,
        min_similarity: float = 0.4,
    ) -> None:
        self.embedder = embedder
        self.min_similarity = min_similarity

    def compute(
        self,
        cause: tuple[MemoryItem, str],
        effect: tuple[MemoryItem, str],
    ) -> tuple[float, list[str]]:
        """Compute a causal likelihood score between two items."""
        cause_item, cause_body = cause
        effect_item, effect_body = effect

        score = 0.0
        reasons: list[str] = []

        days_apart = (effect_item.created_at - cause_item.created_at).total_seconds() / 86400
        if 0 < days_apart <= 1:
            temporal_boost = 0.3
        elif days_apart <= 7:
            temporal_boost = 0.2
        elif days_apart <= 14:
            temporal_boost = 0.1
        else:
            temporal_boost = 0.05
        score += temporal_boost
        reasons.append(f"temporal: {days_apart:.1f}d apart (+{temporal_boost:.2f})")

        if cause_item.project and cause_item.project == effect_item.project:
            score += 0.2
            reasons.append("same project (+0.20)")

        common_tags = set(cause_item.tags) & set(effect_item.tags)
        if common_tags:
            tag_boost = min(0.2, len(common_tags) * 0.07)
            score += tag_boost
            reasons.append(f"tags overlap: {common_tags} (+{tag_boost:.2f})")

        if cause_item.type == MemoryType.decision and effect_item.type in (MemoryType.episode, MemoryType.signal):
            score += 0.15
            reasons.append("decision→episode/signal (+0.15)")

        if self.embedder is not None:
            try:
                emb_cause = self.embedder.embed(f"{cause_item.title} {cause_body[:200]}")
                emb_effect = self.embedder.embed(f"{effect_item.title} {effect_body[:200]}")
                sim = sum(a * b for a, b in zip(emb_cause, emb_effect))
                norm_c = sum(x * x for x in emb_cause) ** 0.5 or 1.0
                norm_e = sum(x * x for x in emb_effect) ** 0.5 or 1.0
                sim /= (norm_c * norm_e)
                if sim >= self.min_similarity:
                    sem_boost = sim * 0.3
                    score += sem_boost
                    reasons.append(f"semantic sim={sim:.2f} (+{sem_boost:.2f})")
            except Exception:
                pass

        return score, reasons


__all__ = ["CausalScorer"]
