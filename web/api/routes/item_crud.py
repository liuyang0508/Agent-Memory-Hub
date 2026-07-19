"""CRUD and batch routes for item data."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from agent_brain.memory.store.items_store import make_item_id
from web._base import (
    _audit,
    _broadcast_event,
    _components,
    _evict_from_index,
    _require_visible,
    _visible,
    _write_service,
    mutate_item,
)
from web.api.routes.item_listing import list_visible_items
from web.api.routes.item_payloads import (
    CreateItemRequest,
    UpdateItemRequest,
    clone_item_record,
    create_item_record,
    pinned_item_summary,
    update_fields_from_request,
)
from web.auth import CurrentUser, get_current_user


router = APIRouter()


def _blocked_write_response(result) -> HTTPException:
    return HTTPException(status_code=400, detail=asdict(result))


@router.get("/api/items")
async def list_items(
    type: str | None = None,
    project: str | None = None,
    tag: str | None = None,
    q: str | None = None,
    conf_min: float | None = Query(None, ge=0, le=1),
    conf_max: float | None = Query(None, ge=0, le=1),
    since: str | None = Query(None, description="ISO date filter (created after)"),
    until: str | None = Query(None, description="ISO date filter (created before)"),
    sort: str = Query("created_at", pattern="^(created_at|title|confidence|type)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, le=500),
    user: CurrentUser = Depends(get_current_user),
):
    store, _, _, _ = _components()
    return list_visible_items(
        items_with_bodies=store.iter_all(),
        user=user,
        is_visible=_visible,
        item_type=type,
        project=project,
        tag=tag,
        q=q,
        conf_min=conf_min,
        conf_max=conf_max,
        since=since,
        until=until,
        sort=sort,
        order=order,
        offset=offset,
        limit=limit,
    )


@router.get("/api/items/pinned")
async def list_pinned(user: CurrentUser = Depends(get_current_user)):
    """List all pinned items."""
    store, _, _, _ = _components()
    results = []
    for item, _ in store.iter_all():
        if not _visible(item, user):
            continue
        if "pinned" not in item.tags:
            continue
        results.append(pinned_item_summary(item))
    return {"items": results, "count": len(results)}


@router.get("/api/items/{item_id}")
async def get_item(
    item_id: str,
    head: int | None = Query(None, ge=0),
    view: str = Query("detail", pattern="^(locator|overview|detail)$"),
    user: CurrentUser = Depends(get_current_user),
):
    store, _, _, _ = _components()
    try:
        item, body = store.get(item_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="item not found")
    if not _visible(item, user):
        raise HTTPException(status_code=403, detail="access denied")
    result = {"item": item.model_dump(mode="json")}
    if view == "locator":
        result["locator"] = item.context_views.locator
        return result
    if view == "overview":
        result["locator"] = item.context_views.locator
        result["overview"] = item.context_views.overview
        return result
    if head is not None and len(body) > head:
        result["body"] = body[:head]
        result["body_truncated"] = True
        result["full_chars"] = len(body)
    else:
        result["body"] = body
    return result


@router.delete("/api/items/{item_id}")
async def delete_item(item_id: str, user: CurrentUser = Depends(get_current_user)):
    store, _, _, _ = _components()
    item, _ = _require_visible(store, item_id, user)
    md_path = store.items_dir / f"{item_id}.md"
    md_path.unlink()
    _evict_from_index(item_id)
    _audit(user.username, "delete", item_id)
    _broadcast_event("item_deleted", {"id": item_id}, tenant_id=item.tenant_id)
    return {"deleted": item_id}


@router.patch("/api/items/{item_id}")
async def update_item(
    item_id: str,
    req: UpdateItemRequest,
    user: CurrentUser = Depends(get_current_user),
):
    updates = update_fields_from_request(req)
    if not updates:
        raise HTTPException(status_code=400, detail="no fields to update")
    mutate_item(item_id, user, updates, event="item_updated", snapshot=True)
    return {"id": item_id, "updated_fields": list(updates.keys())}


@router.post("/api/items")
async def create_item(
    req: CreateItemRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Create a new memory item from the web admin."""
    now = datetime.now(timezone.utc)
    item_id = make_item_id(req.title, when=now)

    item = create_item_record(req, item_id=item_id, created_at=now, user=user)
    result = _write_service().write(item=item, body=req.body)
    if result.status == "blocked":
        raise _blocked_write_response(result)
    _audit(user.username, "create", item_id)
    _broadcast_event(
        "item_created",
        {"id": item_id, "title": req.title, "type": req.type},
        tenant_id=item.tenant_id,
    )
    return {
        "id": item_id,
        "path": result.path,
        "indexed": result.indexed,
        "warnings": result.warnings,
    }


@router.post("/api/items/{item_id}/clone")
async def clone_item(
    item_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """Clone an existing item with a new ID and timestamp."""
    store, _, _, _ = _components()
    try:
        item, body = store.get(item_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="item not found")
    if not _visible(item, user):
        raise HTTPException(status_code=403, detail="access denied")

    now = datetime.now(timezone.utc)
    clone_id = make_item_id(item.title, when=now, label="clone")
    clone_item_obj = clone_item_record(item, clone_id=clone_id, created_at=now)

    result = _write_service().write(item=clone_item_obj, body=body)
    if result.status == "blocked":
        raise _blocked_write_response(result)
    _audit(user.username, "clone", f"{item_id} -> {clone_id}")
    return {
        "id": clone_id,
        "source_id": item_id,
        "path": result.path,
        "indexed": result.indexed,
        "warnings": result.warnings,
    }
