"""Reciprocal-rank fusion helpers for retrieval."""
from __future__ import annotations

from typing import Any

from agent_brain.memory.recall.retrieval_types import RetrievedItem


def rrf_fusion(
    bm25_hits: list[Any],
    vector_hits: list[Any],
    *,
    rrf_k: int,
    bm25_weight: float,
    vector_weight: float,
) -> list[RetrievedItem]:
    """Fuse BM25 and vector hit lists via Reciprocal Rank Fusion."""
    fused: dict[str, RetrievedItem] = {}
    for rank, hit in enumerate(bm25_hits):
        score = bm25_weight / (rrf_k + rank + 1)
        fused[hit.id] = RetrievedItem(
            id=hit.id,
            score=score,
            bm25_rank=rank + 1,
            vector_rank=None,
        )
    for rank, hit in enumerate(vector_hits):
        score = vector_weight / (rrf_k + rank + 1)
        existing = fused.get(hit.id)
        if existing is None:
            fused[hit.id] = RetrievedItem(
                id=hit.id,
                score=score,
                bm25_rank=None,
                vector_rank=rank + 1,
            )
        else:
            fused[hit.id] = RetrievedItem(
                id=existing.id,
                score=existing.score + score,
                bm25_rank=existing.bm25_rank,
                vector_rank=rank + 1,
            )
    return sorted(fused.values(), key=lambda item: item.score, reverse=True)


__all__ = ["rrf_fusion"]
