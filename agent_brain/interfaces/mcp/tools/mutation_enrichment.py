"""Best-effort enrichment helpers for MCP mutation tools."""
from __future__ import annotations

from typing import Any

from agent_brain.memory.recall.retrieval import Retriever
from agent_brain.memory.recall.retrieval_tags import suggest_tags as _suggest_tags


def build_write_enrichment(
    *,
    store: Any,
    index: Any,
    embedder: Any,
    item_id: str,
    title: str,
    summary: str,
    body: str,
    tags: list[str] | None,
) -> dict[str, Any]:
    """Return best-effort related-memory and suggested-tag enrichment."""
    result: dict[str, Any] = {}
    related: list[dict[str, Any]] = []
    try:
        retriever = Retriever(index=index, embedder=embedder, apply_decay=False, record_access=False)
        hits = retriever.search(f"{title} {summary}", top_k=4)
        items_by_id = {item.id: item for item, _ in store.iter_all()}
        for hit in hits:
            if hit.id == item_id:
                continue
            if hit.id in items_by_id and hit.score > 0.3:
                related.append(
                    {
                        "id": hit.id,
                        "title": items_by_id[hit.id].title,
                        "score": round(hit.score, 3),
                    }
                )
    except Exception:
        pass

    suggested: list[str] = []
    try:
        suggestions = _suggest_tags(index, embedder, f"{title} {summary} {body}", max_tags=5)
        suggested = [tag for tag, _ in suggestions if tag not in (tags or [])]
    except Exception:
        pass

    if related:
        result["related"] = related[:3]
    if suggested:
        result["suggested_tags"] = suggested[:5]
    return result


__all__ = ["build_write_enrichment"]
