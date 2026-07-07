"""Cross-encoder reranking strategy for retrieval."""
from __future__ import annotations

import logging
import math
import os
from collections.abc import Callable
from typing import TYPE_CHECKING

from agent_brain.memory.recall.retrieval_types import RetrievedItem

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from agent_brain.platform.indexing.index import HubIndex

_RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_cross_encoder = None


def _get_cross_encoder():
    global _cross_encoder
    if _cross_encoder is None:
        from sentence_transformers import CrossEncoder
        logger.info("Loading cross-encoder model: %s", _RERANK_MODEL)
        _cross_encoder = CrossEncoder(_RERANK_MODEL)
    return _cross_encoder


def rerank_enabled() -> bool:
    return os.environ.get("RERANK_ENABLED", "").lower() in ("1", "true", "yes")


def _sigmoid(x: float) -> float:
    """Map a raw cross-encoder logit into (0, 1)."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


class CrossEncoderReranker:
    """Re-rank retrieval candidates with a sentence-transformers CrossEncoder."""

    def __init__(
        self,
        index: HubIndex,
        cross_encoder_factory: Callable[[], object] = _get_cross_encoder,
    ) -> None:
        self.index = index
        self.cross_encoder_factory = cross_encoder_factory

    def rerank(self, query: str, candidates: list[RetrievedItem]) -> list[RetrievedItem]:
        if not candidates:
            return candidates
        ids = [c.id for c in candidates]
        texts = self.index.get_texts(ids)
        pairs = []
        valid_indices = []
        for i, c in enumerate(candidates):
            doc_text = texts.get(c.id)
            if doc_text:
                pairs.append((query, doc_text))
                valid_indices.append(i)
        if not pairs:
            return candidates
        ce = self.cross_encoder_factory()
        scores = ce.predict(pairs)
        scored = []
        for idx, ce_score in zip(valid_indices, scores):
            c = candidates[idx]
            scored.append(RetrievedItem(
                id=c.id,
                score=_sigmoid(float(ce_score)),
                bm25_rank=c.bm25_rank,
                vector_rank=c.vector_rank,
            ))
        scored.sort(key=lambda item: item.score, reverse=True)
        reranked_ids = {s.id for s in scored}
        for c in candidates:
            if c.id not in reranked_ids:
                scored.append(c)
        return scored


__all__ = ["CrossEncoderReranker", "_get_cross_encoder", "_sigmoid", "rerank_enabled"]
