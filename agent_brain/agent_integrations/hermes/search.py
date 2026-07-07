"""Hermes search result formatting helpers."""

from __future__ import annotations

from typing import Any


def format_search_hits(
    *,
    hits: list[Any],
    items_by_id: dict[str, Any],
    bodies_by_id: dict[str, str],
) -> list[dict[str, Any]]:
    """Format Retriever hits into the Hermes hub_search response shape."""
    return [
        {
            "id": hit.id,
            "title": items_by_id[hit.id].title if hit.id in items_by_id else None,
            "type": str(items_by_id[hit.id].type) if hit.id in items_by_id else None,
            "summary": items_by_id[hit.id].summary if hit.id in items_by_id else None,
            "confidence": items_by_id[hit.id].confidence if hit.id in items_by_id else None,
            "snippet": bodies_by_id.get(hit.id, "")[:200],
            "score": hit.score,
        }
        for hit in hits
    ]


__all__ = ["format_search_hits"]
