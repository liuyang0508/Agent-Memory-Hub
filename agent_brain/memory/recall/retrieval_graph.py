"""Graph expansion helpers for retrieval results."""
from __future__ import annotations

from typing import Any

from agent_brain.memory.recall.retrieval_types import RetrievedItem


def expand_via_graph(
    index: Any,
    candidates: list[RetrievedItem],
    *,
    top_k: int,
    graph_depth: int,
    allowed_ids: set[str] | None = None,
    neighbor_score_factor: float = 0.5,
) -> list[RetrievedItem]:
    """Expand search results by pulling in graph neighbors of top hits."""
    existing_ids = {candidate.id for candidate in candidates}
    neighbor_ids: set[str] = set()
    for candidate in candidates[:top_k]:
        neighbors = index.graph_neighbors(candidate.id, depth=graph_depth)
        neighbor_ids.update(neighbors - existing_ids)
    if allowed_ids is not None:
        neighbor_ids &= allowed_ids
    if not neighbor_ids:
        return candidates

    min_score = candidates[-1].score * neighbor_score_factor if candidates else 0.0
    expanded = list(candidates)
    for neighbor_id in neighbor_ids:
        expanded.append(
            RetrievedItem(
                id=neighbor_id,
                score=min_score,
                bm25_rank=None,
                vector_rank=None,
            )
        )
    return expanded


__all__ = ["expand_via_graph"]
