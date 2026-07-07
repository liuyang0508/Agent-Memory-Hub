from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def suggest_related_memories(
    retriever: Any,
    store: Any,
    item_id: str,
    query: str,
    *,
    top_k: int = 4,
    min_score: float = 0.3,
    limit: int = 3,
) -> list[dict[str, Any]]:
    try:
        hits = retriever.search(query, top_k=top_k)
        items_by_id = {item.id: item for item, _ in store.iter_all()}
        related: list[dict[str, Any]] = []
        for hit in hits:
            if hit.id == item_id:
                continue
            if hit.id in items_by_id and hit.score > min_score:
                related.append({
                    "id": hit.id,
                    "title": items_by_id[hit.id].title,
                    "score": round(hit.score, 3),
                })
        return related[:limit]
    except Exception:
        logger.warning(
            "Failed to suggest related Hermes memories for %s",
            item_id,
            exc_info=True,
        )
        return []


__all__ = ["suggest_related_memories"]
