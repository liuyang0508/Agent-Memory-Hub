"""Hermes item utility and graph tool implementations."""

from __future__ import annotations

from typing import Any, Callable

from agent_brain.memory.recall.embedding_text import embedding_text_for_item


ComponentsFactory = Callable[[], tuple[Any, Any, Any]]
EmbedderFactory = Callable[[], Any]


def hub_graph_impl(
    components: ComponentsFactory,
    item_id: str,
    depth: int = 1,
) -> dict[str, Any]:
    """Query knowledge-graph connections for a memory item."""
    store, idx, _ = components()
    edges = idx.get_refs(item_id)
    neighbors = idx.graph_neighbors(item_id, depth=min(depth, 3))
    items_by_id = {it.id: it for it, _ in store.iter_all()}
    return {
        "item_id": item_id,
        "edges": [
            {"source": s, "target": t, "relation": r}
            for s, t, r in edges
        ],
        "neighbors": [
            {
                "id": nid,
                "type": str(items_by_id[nid].type) if nid in items_by_id else None,
                "title": items_by_id[nid].title if nid in items_by_id else None,
            }
            for nid in sorted(neighbors)
        ],
    }


def hub_link_impl(
    components: ComponentsFactory,
    source_id: str,
    target_id: str,
    relation: str = "refs",
) -> dict[str, Any]:
    """Create a knowledge-graph link between two memory items."""
    _, idx, _ = components()
    idx.add_ref(source_id, target_id, relation)
    return {"source": source_id, "target": target_id, "relation": relation, "linked": True}


def hub_unlink_impl(
    components: ComponentsFactory,
    source_id: str,
    target_id: str,
) -> dict[str, Any]:
    """Remove a knowledge-graph link between two memory items."""
    store, idx, _ = components()
    removed = idx.remove_ref(source_id, target_id)
    try:
        store.unlink_mem(source_id, target_id)
    except Exception:
        pass
    return {"source": source_id, "target": target_id, "removed": removed > 0}


def hub_read_impl(
    components: ComponentsFactory,
    item_id: str,
) -> dict[str, Any]:
    """Read full content of one memory item."""
    store, _, _ = components()
    for item, body in store.iter_all():
        if item.id == item_id:
            return {"frontmatter": item.model_dump(mode="json"), "body": body}
    return {"error": f"item not found: {item_id}"}


def hub_delete_impl(
    components: ComponentsFactory,
    item_id: str,
) -> dict[str, Any]:
    """Delete a memory item by id."""
    store, idx, _ = components()
    md_path = store.items_dir / f"{item_id}.md"
    if not md_path.exists():
        return {"error": f"item not found: {item_id}"}
    md_path.unlink()
    idx.delete(item_id)
    return {"id": item_id, "deleted": True}


def hub_list_impl(
    components: ComponentsFactory,
    n: int = 10,
    type: str | None = None,
    project: str | None = None,
) -> list[dict[str, Any]]:
    """List recent memory items, optionally filtered by type or project."""
    store, _, _ = components()
    items = list(store.iter_all())
    if type:
        items = [(it, b) for it, b in items if str(it.type) == type]
    if project:
        items = [(it, b) for it, b in items if it.project == project]
    items.sort(key=lambda pair: pair[0].created_at, reverse=True)
    return [
        {
            "id": it.id,
            "type": str(it.type),
            "title": it.title,
            "confidence": it.confidence,
            "created_at": it.created_at.isoformat(),
        }
        for it, _ in items[:n]
    ]


def hub_batch_confirm_impl(
    components: ComponentsFactory,
    item_ids: list[str],
    confidence: float = 0.9,
) -> dict[str, Any]:
    """Confirm multiple memory items at once, setting their confidence."""
    store, idx, _ = components()
    confirmed = 0
    for item_id in item_ids:
        try:
            store.update_frontmatter(item_id, confidence=confidence)
            idx.update_confidence(item_id, confidence)
            confirmed += 1
        except (FileNotFoundError, Exception):
            pass
    return {"total": len(item_ids), "confirmed": confirmed}


def hub_update_impl(
    components: ComponentsFactory,
    embedder_factory: EmbedderFactory,
    item_id: str,
    title: str | None = None,
    summary: str | None = None,
    tags: list[str] | None = None,
    type: str | None = None,
    confidence: float | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Update fields of an existing memory item."""
    store, idx, _ = components()
    updates: dict[str, Any] = {}
    if title is not None:
        updates["title"] = title
    if summary is not None:
        updates["summary"] = summary
    if tags is not None:
        updates["tags"] = tags
    if type is not None:
        updates["type"] = type
    if confidence is not None:
        updates["confidence"] = confidence
    if project is not None:
        updates["project"] = project
    if not updates:
        return {"error": "no fields to update"}
    try:
        updated = store.update_frontmatter(item_id, **updates)
    except FileNotFoundError:
        return {"error": f"item not found: {item_id}"}

    embedder = embedder_factory()
    item_body = ""
    for it, body in store.iter_all():
        if it.id == item_id:
            item_body = body
            break
    idx.upsert(
        updated,
        item_body,
        embedding=embedder.embed(embedding_text_for_item(updated)),
    )
    return {"id": item_id, "updated_fields": list(updates.keys())}
