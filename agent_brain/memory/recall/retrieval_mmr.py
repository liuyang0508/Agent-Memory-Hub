from __future__ import annotations

from agent_brain.memory.recall.retrieval_types import RetrievedItem


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


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
    """
    if len(candidates) <= 1 or lambda_ is None:
        return candidates
    if not embeddings:
        return candidates
    score_by_id = {c.id: c.score for c in candidates}
    max_score = max(score_by_id.values()) if score_by_id else 1.0
    if max_score == 0:
        max_score = 1.0

    selected: list[RetrievedItem] = []
    remaining = list(candidates)

    while remaining and len(selected) < top_k:
        best_idx = 0
        best_mmr = -float("inf")

        for i, cand in enumerate(remaining):
            rel = score_by_id[cand.id] / max_score

            max_sim = 0.0
            cand_emb = embeddings.get(cand.id)
            if cand_emb and selected:
                for selected_item in selected:
                    selected_emb = embeddings.get(selected_item.id)
                    if selected_emb:
                        sim = _cosine_sim(cand_emb, selected_emb)
                        if sim > max_sim:
                            max_sim = sim

            mmr_score = lambda_ * rel - (1 - lambda_) * max_sim
            if mmr_score > best_mmr:
                best_mmr = mmr_score
                best_idx = i

        selected.append(remaining.pop(best_idx))

    return selected


__all__ = ["_cosine_sim", "mmr_rerank"]
