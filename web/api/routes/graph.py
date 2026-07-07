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
    _, idx, _, _ = _components()
    edges = idx.get_refs(item_id)
    neighbors = idx.graph_neighbors(item_id, depth=depth)
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
    _, idx, _, _ = _components()
    idx.add_ref(req.source, req.target, req.label)
    return {"linked": True, "source": req.source, "target": req.target, "label": req.label}

@router.delete("/api/link")
async def unlink_graph_ref(
    source: str = Query(...),
    target: str = Query(...),
    user: CurrentUser = Depends(get_current_user),
):
    store, idx, _, _ = _components()
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
    link_tenant: str | None = None
    for item_id in (req.source_id, req.target_id):
        try:
            _it, _ = store.get(item_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"item {item_id} not found")
        if item_id == req.source_id:
            link_tenant = _it.tenant_id
    state = _state_store()
    if state.link_exists(req.source_id, req.target_id):
        raise HTTPException(status_code=409, detail="link already exists")
    link = state.add_link(req.source_id, req.target_id, req.relation, user.username)
    _audit(user.username, "link_create", f"{req.source_id} -> {req.target_id}")
    _broadcast_event("link_created", link, tenant_id=link_tenant)
    return {"link": link}

@router.get("/api/links/{item_id}")
async def get_item_links(item_id: str, user: CurrentUser = Depends(get_current_user)):
    """Get all links for a specific item."""
    links = _state_store().links_for(item_id)
    return {"links": links, "count": len(links)}

@router.delete("/api/links")
async def unlink_items(
    source_id: str = Query(...),
    target_id: str = Query(...),
    user: CurrentUser = Depends(get_current_user),
):
    """Remove a link between two items."""
    removed = _state_store().remove_link(source_id, target_id)
    if removed:
        _audit(user.username, "link_delete", f"{source_id} -> {target_id}")
    return {"removed": removed}
