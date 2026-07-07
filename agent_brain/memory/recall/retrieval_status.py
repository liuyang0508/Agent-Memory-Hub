from __future__ import annotations

from typing import Any

from agent_brain.memory.recall.query_expansion import _extract_words
from agent_brain.memory.recall.retrieval_types import RetrievedItem

_STATUS_RISK_TRIGGER_TERMS = {
    "stale",
    "outdated",
    "reorient",
    "handoff",
}
_STATUS_CONTEXT_TERMS = {
    "memory",
    "project",
    "status",
    "trust",
    "current",
}
_HANDOFF_TAGS = {
    "handoff",
    "status",
    "next-step",
    "current",
}


def is_status_risk_query(query: str) -> bool:
    query_terms = set(_extract_words(query))
    return bool(
        query_terms & _STATUS_RISK_TRIGGER_TERMS
        and query_terms & _STATUS_CONTEXT_TERMS
    )


def supplement_status_handoff_candidates(
    index: Any,
    query: str,
    candidates: list[RetrievedItem],
    allowed_ids: set[str] | None = None,
) -> list[RetrievedItem]:
    if not is_status_risk_query(query):
        return candidates

    handoff_ids = index.filter_ids(type="signal", tags=["handoff", "status"])
    if not handoff_ids:
        return candidates
    if allowed_ids is not None:
        handoff_ids &= allowed_ids
    if not handoff_ids:
        return candidates

    top_score = candidates[0].score if candidates else 1.0
    existing_ids: set[str] = set()
    supplemented: list[RetrievedItem] = []
    for candidate in candidates:
        existing_ids.add(candidate.id)
        if candidate.id in handoff_ids and candidate.score < top_score:
            supplemented.append(RetrievedItem(
                id=candidate.id,
                score=top_score,
                bm25_rank=candidate.bm25_rank,
                vector_rank=candidate.vector_rank,
            ))
            continue
        supplemented.append(candidate)

    supplement_ids = sorted(handoff_ids - existing_ids)
    for item_id in supplement_ids:
        supplemented.append(RetrievedItem(
            id=item_id,
            score=top_score,
            bm25_rank=None,
            vector_rank=None,
        ))
    return supplemented


def apply_status_handoff_boost(
    index: Any,
    query: str,
    candidates: list[RetrievedItem],
) -> list[RetrievedItem]:
    if not is_status_risk_query(query):
        return candidates

    ids = [candidate.id for candidate in candidates]
    metadata = index.get_search_metadata(ids)
    boosted: list[RetrievedItem] = []
    for candidate in candidates:
        meta = metadata.get(candidate.id, {})
        item_type = str(meta.get("type") or "")
        tags = {str(tag) for tag in meta.get("tags", [])}
        multiplier = 1.0
        if item_type == "signal":
            multiplier += 0.25
        if tags & _HANDOFF_TAGS:
            multiplier += 0.15
        if multiplier == 1.0:
            boosted.append(candidate)
            continue
        boosted.append(
            RetrievedItem(
                id=candidate.id,
                score=candidate.score * multiplier,
                bm25_rank=candidate.bm25_rank,
                vector_rank=candidate.vector_rank,
            )
        )
    return sorted(boosted, key=lambda item: item.score, reverse=True)


__all__ = [
    "apply_status_handoff_boost",
    "is_status_risk_query",
    "supplement_status_handoff_candidates",
]
