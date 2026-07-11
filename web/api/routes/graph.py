"""Agent Memory Hub Web Admin — graph routes.

Moved verbatim from app.py (decorators rewritten @app.→@router.); request models for
this group travel with their handlers in original order so FastAPI's decoration-time
binding is unchanged. Infra (helpers/state) comes from web._base.
"""
from __future__ import annotations

from fastapi import APIRouter

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel
from web.auth import (
    CurrentUser,
    get_current_user,
)
from web.graph_payload import build_full_graph_payload

from web._base import *  # noqa: F401,F403  (state, helpers, models, lifespan, middleware)

router = APIRouter()


@router.get("/api/graph/{item_id}")
async def item_graph(
    item_id: str,
    depth: int = Query(1, le=3),
    user: CurrentUser = Depends(get_current_user),
):
    store, idx, _, _ = _components()
    _require_visible(store, item_id, user)
    edges = idx.get_refs(item_id)
    neighbors = idx.graph_neighbors(item_id, depth=depth)
    visible_ids = _visible_existing_ids(
        store,
        {
            item_id,
            *neighbors,
            *(edge_id for edge in edges for edge_id in edge[:2]),
        },
        user,
    )
    edges = [
        edge
        for edge in edges
        if edge[0] in visible_ids and edge[1] in visible_ids
    ]
    neighbors = {neighbor for neighbor in neighbors if neighbor in visible_ids}
    return {
        "item_id": item_id,
        "edges": [{"source": e[0], "target": e[1], "label": e[2]} for e in edges],
        "neighbors": list(neighbors),
    }

@router.get("/api/graph")
async def full_graph(
    debug: bool = Query(False),
    user: CurrentUser = Depends(get_current_user),
):
    """Return all nodes and edges for graph visualization.

    Edges come from three sources, deduplicated:
      1. Explicit links added via POST /api/link  (idx.get_refs)
      2. frontmatter refs.mems on each item
      3. [[wiki-link]] tokens in the item body, resolved by id or exact title match
    """
    store, idx, _, _ = _components()
    return build_full_graph_payload(store, idx, user=user, debug=debug)

class LinkRequest(BaseModel):
    source: str
    target: str
    label: str = "related"

@router.post("/api/link")
async def link_graph_ref(req: LinkRequest, user: CurrentUser = Depends(get_current_user)):
    store, idx, _, _ = _components()
    _require_link_items(store, req.source, req.target, user)
    idx.add_ref(req.source, req.target, req.label)
    return {"linked": True, "source": req.source, "target": req.target, "label": req.label}

@router.delete("/api/link")
async def unlink_graph_ref(
    source: str = Query(...),
    target: str = Query(...),
    user: CurrentUser = Depends(get_current_user),
):
    store, idx, _, _ = _components()
    _require_link_items(store, source, target, user)
    removed = idx.remove_ref(source, target)
    store.unlink_mem(source, target)
    return {"unlinked": removed > 0, "source": source, "target": target}

class LinkByIdRequest(BaseModel):
    source_id: str
    target_id: str
    relation: str = "related"

@router.post("/api/links")
async def link_items(req: LinkByIdRequest, user: CurrentUser = Depends(get_current_user)):
    """Create a manual link between two memory items."""
    store, _, _, _ = _components()
    source_item, _target_item = _require_link_items(
        store,
        req.source_id,
        req.target_id,
        user,
    )
    state = _state_store()
    if state.link_exists(req.source_id, req.target_id):
        raise HTTPException(status_code=409, detail="link already exists")
    link = state.add_link(req.source_id, req.target_id, req.relation, user.username)
    _audit(user.username, "link_create", f"{req.source_id} -> {req.target_id}")
    _broadcast_event("link_created", link, tenant_id=source_item.tenant_id)
    return {"link": link}

@router.get("/api/links/{item_id}")
async def get_item_links(item_id: str, user: CurrentUser = Depends(get_current_user)):
    """Get all links for a specific item."""
    store, _, _, _ = _components()
    _require_visible(store, item_id, user)
    links = _state_store().links_for(item_id)
    visible_ids = _visible_existing_ids(
        store,
        {
            item_id,
            *(linked_id for link in links for linked_id in (link["source"], link["target"])),
        },
        user,
    )
    links = [
        link
        for link in links
        if link["source"] in visible_ids and link["target"] in visible_ids
    ]
    return {"links": links, "count": len(links)}

@router.delete("/api/links")
async def unlink_items(
    source_id: str = Query(...),
    target_id: str = Query(...),
    user: CurrentUser = Depends(get_current_user),
):
    """Remove a link between two items."""
    store, _, _, _ = _components()
    _require_link_items(store, source_id, target_id, user)
    removed = _state_store().remove_link(source_id, target_id)
    if removed:
        _audit(user.username, "link_delete", f"{source_id} -> {target_id}")
    return {"removed": removed}


def _require_link_items(store, source_id: str, target_id: str, user: CurrentUser):
    source_item, _ = _require_visible(store, source_id, user)
    target_item, _ = _require_visible(store, target_id, user)
    return source_item, target_item


def _visible_existing_ids(
    store,
    item_ids: set[str],
    user: CurrentUser,
) -> set[str]:
    visible: set[str] = set()
    for item_id in item_ids:
        try:
            _require_visible(store, item_id, user)
        except HTTPException:
            continue
        visible.add(item_id)
    return visible
