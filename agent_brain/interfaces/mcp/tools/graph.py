"""MCP graph tier tools. Bodies moved verbatim from mcp_server.py (design §6.2)."""
from __future__ import annotations

from typing import Any

from agent_brain.interfaces.mcp.tools._shared import *  # noqa: F401,F403
from agent_brain.interfaces.mcp.tools._shared import _components


def graph_memory(item_id: str, depth: int = 1) -> dict[str, Any]:
    """Show knowledge-graph connections for a memory item.

    Returns direct edges and all reachable neighbors within depth hops.
    Edges come from refs.mems links between items.

    WHEN TO USE
    -----------
    After `search_memory` returns a highly relevant item, call this to
    discover related memories the user has linked but that BM25/vector
    search missed (e.g. a `policy` linked to multiple `episode` items).
    Especially useful when answering "give me everything we know about X".

    DO NOT
    ------
    Use `depth>2` for routine queries — graph fan-out explodes quickly.
    Default `depth=1` is the right tradeoff for most reasoning steps.
    """
    store, idx, _ = _components()
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


def link_memories(
    source_id: str,
    target_id: str,
    relation: str = "refs",
) -> dict[str, Any]:
    """Create a knowledge-graph link between two memory items.

    Adds a directed edge source→target with the given relation type.
    Also updates the source item's refs.mems to include target_id.

    WHEN TO USE (mandatory chaining after write_memory)
    --------------------------------------------------
    Call this IMMEDIATELY AFTER `write_memory` whenever the new item:
      * References, refines, or contradicts a memory you just retrieved via
        `search_memory` in the same task.
      * Belongs to a chain (episode → policy → skill, decision → artifact).
      * Replaces an outdated memory (use relation="supersedes").

    CANONICAL `relation` VALUES
    ---------------------------
      * `refs`       - generic reference (default; safe choice)
      * `supersedes` - new item replaces old item
      * `refines`    - new item adds detail/correction to old item
      * `contradicts`- new item disagrees (drift_check will surface)
      * `derives`    - new item is generalized from old item(s)

    Linking is what makes the brain a graph rather than a list. Skipping
    this step is the single largest cause of "memory not found" later.
    """
    store, idx, _ = _components()
    idx.add_ref(source_id, target_id, relation)
    try:
        store.link_mem(source_id, target_id)
    except Exception:
        pass
    return {"source": source_id, "target": target_id, "relation": relation, "linked": True}


def unlink_memories(
    source_id: str,
    target_id: str,
) -> dict[str, Any]:
    """Remove a knowledge-graph link between two memory items.

    WHEN TO USE
    -----------
    Only when a previously-correct link is now wrong (e.g. mis-linked
    `supersedes` between unrelated items). Removing a link does NOT remove
    either item; both remain in the brain.
    """
    store, idx, _ = _components()
    removed = idx.remove_ref(source_id, target_id)
    try:
        store.unlink_mem(source_id, target_id)
    except Exception:
        pass
    return {"source": source_id, "target": target_id, "removed": removed > 0}


def register(mcp) -> None:
    """Register this tier's tools on the FastMCP instance (called by server.register_all)."""
    mcp.tool()(graph_memory)
    mcp.tool()(link_memories)
    mcp.tool()(unlink_memories)


__all__ = ['graph_memory', 'link_memories', 'unlink_memories']
