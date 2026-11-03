from __future__ import annotations

from agent_brain.retrieval.types import RetrievedItem


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _unit(vector: list[float]) -> list[float] | None:
    """The vector scaled to unit length, or None for an empty/zero vector."""
    norm = sum(x * x for x in vector) ** 0.5
    if norm == 0:
        return None
    return [x / norm for x in vector]


def mmr_rerank(
    candidates: list[RetrievedItem],
    embeddings: dict[str, list[float]],
    *,
    top_k: int,
    lambda_: float | None,
) -> list[RetrievedItem]:
    """Maximal Marginal Relevance re-ranking for result diversity.

    Balances relevance with diversity: at each step, picks the candidate
    maximizing lambda * relevance - (1-lambda) * max similarity to selected.

    Incremental form: every candidate caches its maximum similarity to the
    selected set, and each pick costs one cosine per remaining candidate
    against pre-normalized vectors (O(n·k·d) instead of O(n²·k·d)). The
    result is the same ordering as the direct definition.
    """
    if len(candidates) <= 1 or lambda_ is None:
        return candidates
    if not embeddings:
        return candidates
    score_by_id = {c.id: c.score for c in candidates}
    max_score = max(score_by_id.values()) if score_by_id else 1.0
    if max_score == 0:
        max_score = 1.0
    units: dict[str, list[float]] = {}
    for cand in candidates:
        unit = _unit(embeddings.get(cand.id) or [])
        if unit is not None:
            units[cand.id] = unit
    # Max cosine to the selected set so far; a negative cosine never lowers it
    # below the 0.0 floor, exactly as the direct form behaves.
    max_sim = {c.id: 0.0 for c in candidates}

    selected: list[RetrievedItem] = []
    remaining = list(candidates)

    while remaining and len(selected) < top_k:
        best_idx = 0
        best_mmr = -float("inf")

        for i, cand in enumerate(remaining):
            rel = score_by_id[cand.id] / max_score
            mmr_score = lambda_ * rel - (1 - lambda_) * max_sim[cand.id]
            if mmr_score > best_mmr:
                best_mmr = mmr_score
                best_idx = i

        chosen = remaining.pop(best_idx)
        selected.append(chosen)
        chosen_unit = units.get(chosen.id)
        if chosen_unit is None:
            continue
        for cand in remaining:
            unit = units.get(cand.id)
            if unit is None:
                continue
            sim = sum(x * y for x, y in zip(unit, chosen_unit))
            if sim > max_sim[cand.id]:
                max_sim[cand.id] = sim

    return selected


__all__ = ["_cosine_sim", "mmr_rerank"]
