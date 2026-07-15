"""Route-aware reciprocal-rank fusion for routed recall."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent_brain.memory.recall.retrieval_types import RetrievedItem
from agent_brain.memory.recall.routed_types import RouteEvidence


def fuse_routes(
    *,
    lexical_terms_hits: Sequence[Any] = (),
    semantic_hits: Sequence[Any] = (),
    lexical_raw_hits: Sequence[Any] = (),
    semantic_similarities: Mapping[str, float] | None = None,
    rrf_k: int = 60,
) -> tuple[list[RetrievedItem], dict[str, RouteEvidence]]:
    """Fuse three independent route rankings without conflating their scores.

    Every route contributes ``1 / (rrf_k + rank)`` once per item. Backend hit
    scores are deliberately ignored because BM25 and vector backends expose
    incomparable score domains.
    """

    similarities = semantic_similarities or {}
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    route_names: dict[str, list[str]] = {}
    semantic_ranks: dict[str, int] = {}
    lexical_terms_ranks: dict[str, int] = {}
    lexical_raw_ranks: dict[str, int] = {}

    route_specs = (
        ("lexical_terms", lexical_terms_hits, lexical_terms_ranks),
        ("semantic_raw", semantic_hits, semantic_ranks),
        ("lexical_raw_fallback", lexical_raw_hits, lexical_raw_ranks),
    )
    for route_name, hits, ranks in route_specs:
        seen_on_route: set[str] = set()
        for rank, hit in enumerate(hits, start=1):
            item_id = str(hit.id)
            if item_id in seen_on_route:
                continue
            seen_on_route.add(item_id)
            first_seen.setdefault(item_id, len(first_seen))
            ranks[item_id] = rank
            route_names.setdefault(item_id, []).append(route_name)
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (rrf_k + rank)

    items = [
        RetrievedItem(
            id=item_id,
            score=score,
            bm25_rank=lexical_terms_ranks.get(item_id, lexical_raw_ranks.get(item_id)),
            vector_rank=semantic_ranks.get(item_id),
        )
        for item_id, score in scores.items()
    ]
    items.sort(key=lambda item: (-item.score, first_seen[item.id]))

    evidence = {
        item.id: RouteEvidence(
            routes=tuple(route_names[item.id]),
            semantic_similarity=similarities.get(item.id),
            semantic_rank=semantic_ranks.get(item.id),
            lexical_terms_rank=lexical_terms_ranks.get(item.id),
            lexical_raw_rank=lexical_raw_ranks.get(item.id),
        )
        for item in items
    }
    return items, evidence


__all__ = ["fuse_routes"]
