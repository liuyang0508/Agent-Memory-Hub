from __future__ import annotations

from typing import Any

from agent_brain.memory.recall.query_expansion import _extract_words
from agent_brain.memory.recall.retrieval_types import RetrievedItem

_RUNTIME_EVIDENCE_TERMS = {
    "hook",
    "hooks",
    "runtime",
    "event",
    "events",
    "observed",
    "observation",
    "runtime_observed",
}
_ADAPTER_QUERY_TAGS = {
    "aider": "aider",
    "aone": "aone",
    "cline": "cline",
    "codex": "codex",
    "continue": "continue",
    "cursor": "cursor",
    "github": "github-copilot",
    "qoder": "qoder",
    "wukong": "wukong",
}


def adapter_tags_in_query(query_terms: set[str]) -> set[str]:
    adapter_tags = {
        tag for term, tag in _ADAPTER_QUERY_TAGS.items() if term in query_terms
    }
    if "claude" in query_terms and "code" in query_terms:
        adapter_tags.add("claude-code")
    return adapter_tags


def apply_adapter_runtime_evidence_boost(
    index: Any,
    query: str,
    candidates: list[RetrievedItem],
) -> list[RetrievedItem]:
    query_terms = set(_extract_words(query))
    if not query_terms & _RUNTIME_EVIDENCE_TERMS:
        return candidates
    adapter_tags = adapter_tags_in_query(query_terms)
    if not adapter_tags:
        return candidates

    ids = [candidate.id for candidate in candidates]
    metadata = index.get_search_metadata(ids)
    boosted: list[RetrievedItem] = []
    for candidate in candidates:
        meta = metadata.get(candidate.id, {})
        item_type = str(meta.get("type") or "")
        tags = {str(tag) for tag in meta.get("tags", [])}
        multiplier = 1.0
        if adapter_tags & tags and "runtime-evidence" in tags:
            multiplier += 0.75
            if "hooks" in tags or "real-config" in tags:
                multiplier += 0.25
            if item_type == "fact":
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
    "adapter_tags_in_query",
    "apply_adapter_runtime_evidence_boost",
]
