"""MCP graph tier tools. Bodies moved verbatim from mcp_server.py (design §6.2)."""
from __future__ import annotations

from typing import Any

from agent_brain.contracts.memory_item import is_valid_memory_item_id
from agent_brain.interfaces.mcp.tools._shared import *  # noqa: F401,F403
from agent_brain.interfaces.mcp.tools._shared import _brain_dir, _components
from agent_brain.memory.governance.lifecycle_ledger import lifecycle_transaction_lock
from agent_brain.memory.governance.supersession import SupersessionService
from agent_brain.memory.store.durable_fs import lifecycle_mutation_capability


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
    if not (
        is_valid_memory_item_id(source_id)
        and is_valid_memory_item_id(target_id)
    ):
        return {
            "source": source_id,
            "target": target_id,
            "relation": relation,
            "linked": False,
            "status": "blocked",
            "reason": "INVALID_ITEM_ID",
            "index_repair_required": False,
        }
    store, idx, _ = _components()
    if relation == "supersedes":
        result = SupersessionService(_brain_dir(), store, idx).apply(
            replacement_id=source_id,
            obsolete_id=target_id,
            apply=True,
        )
        return {
            "source": source_id,
            "target": target_id,
            "relation": relation,
            "linked": result.status in {"applied", "already_applied"},
            "status": result.status,
            "reason": result.reason,
            "index_repair_required": result.index_repair_required,
        }
    idx.add_ref(source_id, target_id, relation)
    try:
        store.link_mem(source_id, target_id)
    except Exception:
        pass
    return {
        "source": source_id,
        "target": target_id,
        "relation": relation,
        "linked": True,
        "status": "linked",
        "reason": "OK",
        "index_repair_required": False,
    }


def unlink_memories(
    source_id: str,
    target_id: str,
) -> dict[str, Any]:
    """Remove a knowledge-graph link between two memory items.

    WHEN TO USE
    -----------
    Only for ordinary `refs` or custom relations that are now wrong.
    A `supersedes` relation must use the governed revert operation; generic
    unlink is intentionally blocked. Neither operation removes either item.
    """
    if not (
        is_valid_memory_item_id(source_id)
        and is_valid_memory_item_id(target_id)
    ):
        return _unlink_result(
            source_id,
            target_id,
            status="blocked",
            reason="INVALID_ITEM_ID",
        )
    try:
        store, idx, _ = _components()
    except Exception:
        return _unlink_result(
            source_id,
            target_id,
            status="blocked",
            reason="GRAPH_CHECK_FAILED",
        )

    if not lifecycle_mutation_capability():
        loaded = _load_unlink_pair(store, source_id, target_id)
        if isinstance(loaded, str):
            return _unlink_result(
                source_id, target_id, status="blocked", reason=loaded
            )
        source, target = loaded
        return _unlink_checked(
            store.unlink_mem,
            idx,
            source,
            target,
            source_id,
            target_id,
        )

    try:
        with (
            lifecycle_transaction_lock(_brain_dir()),
            store.locked_items(sorted({source_id, target_id})) as locked,
        ):
            loaded = _load_unlink_pair(locked, source_id, target_id)
            if isinstance(loaded, str):
                return _unlink_result(
                    source_id, target_id, status="blocked", reason=loaded
                )
            source, target = loaded
            return _unlink_checked(
                locked.unlink_mem,
                idx,
                source,
                target,
                source_id,
                target_id,
            )
    except Exception:
        return _unlink_result(
            source_id,
            target_id,
            status="blocked",
            reason="LOCK_FAILED",
        )


def _load_unlink_pair(
    reader: Any,
    source_id: str,
    target_id: str,
) -> tuple[Any, Any] | str:
    try:
        source, _source_body = reader.get(source_id)
        target, _target_body = reader.get(target_id)
    except FileNotFoundError:
        return "ITEM_MISSING"
    except Exception:
        return "ITEM_INVALID"
    if source.id != source_id or target.id != target_id:
        return "ITEM_INVALID"
    return source, target


def _unlink_checked(
    unlink_markdown: Any,
    idx: Any,
    source: Any,
    target: Any,
    source_id: str,
    target_id: str,
) -> dict[str, Any]:
    try:
        graph_rows = idx.get_refs(source_id)
        relations: set[str] = set()
        for row in graph_rows:
            if not isinstance(row, (tuple, list)) or len(row) != 3:
                raise ValueError("invalid graph row")
            edge_source, edge_target, relation = row
            if edge_source == source_id and edge_target == target_id:
                if not isinstance(relation, str):
                    raise ValueError("invalid graph relation")
                relations.add(relation)
    except Exception:
        return _unlink_result(
            source_id,
            target_id,
            status="blocked",
            reason="GRAPH_CHECK_FAILED",
        )

    if target.superseded_by == source_id or "supersedes" in relations:
        return _unlink_result(
            source_id,
            target_id,
            status="blocked",
            reason="SUPERSESSION_REVERT_REQUIRED",
        )

    markdown_removed = False
    if target_id in source.refs.mems:
        try:
            markdown_removed = bool(unlink_markdown(source_id, target_id))
        except Exception:
            return _unlink_result(
                source_id,
                target_id,
                status="blocked",
                reason="MARKDOWN_UPDATE_FAILED",
            )

    graph_removed = 0
    try:
        for relation in sorted(relations):
            graph_removed += idx.remove_ref(
                source_id, target_id, relation=relation
            )
    except Exception:
        return _unlink_result(
            source_id,
            target_id,
            status="partial",
            reason="INDEX_UPDATE_FAILED",
            index_repair_required=True,
        )

    did_remove = markdown_removed or graph_removed > 0
    return _unlink_result(
        source_id,
        target_id,
        removed=did_remove,
        status="removed" if did_remove else "not_found",
        reason="OK" if did_remove else "NOT_FOUND",
    )


def _unlink_result(
    source_id: str,
    target_id: str,
    *,
    removed: bool = False,
    status: str,
    reason: str,
    index_repair_required: bool = False,
) -> dict[str, Any]:
    return {
        "source": source_id,
        "target": target_id,
        "removed": removed,
        "status": status,
        "reason": reason,
        "index_repair_required": index_repair_required,
    }


def register(mcp) -> None:
    """Register this tier's tools on the FastMCP instance (called by server.register_all)."""
    mcp.tool()(graph_memory)
    mcp.tool()(link_memories)
    mcp.tool()(unlink_memories)


__all__ = ['graph_memory', 'link_memories', 'unlink_memories']
