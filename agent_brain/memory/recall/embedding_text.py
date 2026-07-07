"""Build text used for vector embeddings.

Vector search should represent the cheap retrieval views, not the full detail
body. The locator remains the strongest signal; overview adds enough navigation
context to avoid overfitting vectors to a terse one-liner.
"""
from __future__ import annotations

from agent_brain.contracts.memory_item import MemoryItem


def embedding_text_for_item(item: MemoryItem) -> str:
    """Return deterministic text used for item vector embeddings."""
    parts: list[str] = []
    locator = (item.context_views.locator or "").strip()
    overview = (item.context_views.overview or "").strip()
    summary = (item.summary or "").strip()
    if locator:
        parts.append(locator)
    elif summary:
        parts.append(summary)
    if overview and overview not in parts:
        parts.append(overview)
    return "\n".join(parts)


__all__ = ["embedding_text_for_item"]
