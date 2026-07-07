from __future__ import annotations

from collections import Counter
import json
from typing import Any


def suggest_tags(
    index: Any,
    embedder: Any,
    text: str,
    top_k_neighbors: int = 10,
    max_tags: int = 5,
) -> list[tuple[str, int]]:
    """Suggest tags for new content based on similar existing items."""
    emb = embedder.embed(text)
    hits = index.vector_search(emb, top_k=top_k_neighbors)
    if not hits:
        return []
    ids = [hit.id for hit in hits]
    tag_counter: Counter[str] = Counter()
    for item_id in ids:
        row = index.connection.execute(
            "SELECT tags_json FROM items_meta WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row and row[0]:
            tags = json.loads(row[0])
            tag_counter.update(tags)
    return tag_counter.most_common(max_tags)


__all__ = ["suggest_tags"]
