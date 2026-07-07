"""Hopfield-style associative expansion for retrieval candidates."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

from agent_brain.memory.recall.hopfield_memory import ContinuousHopfieldMemory
from agent_brain.memory.recall.retrieval_types import RetrievedItem

if TYPE_CHECKING:
    from agent_brain.platform.indexing.index import HubIndex


def expand_via_hopfield(
    index: HubIndex,
    candidates: list[RetrievedItem],
    *,
    top_k: int,
    hopfield_top: int = 20,
    beta: float = 8.0,
    allowed_ids: set[str] | None = None,
) -> list[RetrievedItem]:
    """Add vector neighbors around the candidate attractor.

    BM25/vector fusion finds first-hop candidates. This stage treats their
    embeddings as an associative memory, computes an attractor weighted by the
    current candidate scores, then queries the vector index around that attractor
    for memories that were not in the first-hop pool.
    """
    if not candidates:
        return candidates
    embeddings = index.get_embeddings([candidate.id for candidate in candidates])
    seed_candidates = [candidate for candidate in candidates if candidate.id in embeddings]
    if not seed_candidates:
        return candidates

    seed_weights = _score_weights(seed_candidates)
    query = _weighted_query([embeddings[candidate.id] for candidate in seed_candidates], seed_weights)
    memory = ContinuousHopfieldMemory(
        {candidate.id: embeddings[candidate.id] for candidate in seed_candidates},
        beta=beta,
    )
    attractor = memory.recall(query, top_k=len(seed_candidates)).attractor
    if not attractor:
        return candidates

    existing = {candidate.id: candidate for candidate in candidates}
    max_score = max(candidate.score for candidate in candidates)
    expanded = list(candidates)
    target_size = len(candidates) + max(0, top_k)
    for hit in index.vector_search(attractor, top_k=max(hopfield_top, top_k)):
        if allowed_ids is not None and hit.id not in allowed_ids:
            continue
        if hit.id in existing:
            continue
        # sqlite-vec returns lower distance as a better hit; HubIndex exposes it
        # as a negative score. Convert distance to a bounded similarity factor.
        distance = max(0.0, -hit.score)
        similarity = 1.0 / (1.0 + distance)
        expanded.append(
            RetrievedItem(
                id=hit.id,
                score=max_score * 0.85 * similarity,
                bm25_rank=None,
                vector_rank=None,
            )
        )
        existing[hit.id] = expanded[-1]
        if len(expanded) >= target_size:
            break

    expanded.sort(key=lambda item: item.score, reverse=True)
    return expanded


def _score_weights(candidates: list[RetrievedItem]) -> list[float]:
    scores = [candidate.score for candidate in candidates]
    offset = max(scores)
    exp_values = [math.exp(score - offset) for score in scores]
    total = sum(exp_values)
    if total <= 0:
        return [1.0 / len(candidates) for _ in candidates]
    return [value / total for value in exp_values]


def _weighted_query(vectors: list[list[float]], weights: list[float]) -> list[float]:
    dim = len(vectors[0])
    query = [0.0] * dim
    for vector, weight in zip(vectors, weights, strict=True):
        for index, value in enumerate(vector):
            query[index] += value * weight
    return query


__all__ = ["expand_via_hopfield"]
