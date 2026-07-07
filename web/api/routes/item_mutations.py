"""Mutation routes for existing item records."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from web._base import _audit, _components, _save_snapshot, _visible
from web.auth import CurrentUser, get_current_user


router = APIRouter()


@router.post("/api/items/{item_id}/touch")
async def touch_item(item_id: str, user: CurrentUser = Depends(get_current_user)):
    """Update last_accessed and increment access_count for a memory item."""
    store, _, _, _ = _components()
    try:
        item, _ = store.get(item_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="item not found")
    if not _visible(item, user):
        raise HTTPException(status_code=403, detail="access denied")
    now = datetime.now(timezone.utc)
    new_count = item.retention.access_count + 1
    store.update_frontmatter(
        item_id,
        **{
            "retention.last_accessed": now.isoformat(),
            "retention.access_count": new_count,
        },
    )
    return {"id": item_id, "last_accessed": now.isoformat(), "access_count": new_count}


class UpdateBodyRequest(BaseModel):
    body: str


@router.put("/api/items/{item_id}/body")
async def update_body(
    item_id: str,
    req: UpdateBodyRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Update the body content of a memory item."""
    store, idx, _, embedder = _components()
    try:
        item, old_body = store.get(item_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="item not found")
    if not _visible(item, user):
        raise HTTPException(status_code=403, detail="access denied")
    _save_snapshot(item_id, item.model_dump(mode="json"), old_body)
    yaml_text = yaml.safe_dump(
        item.model_dump(mode="json", exclude_none=False),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    md_path = store.items_dir / f"{item_id}.md"
    md_path.write_text(
        f"---\n{yaml_text}---\n\n{req.body.rstrip()}\n",
        encoding="utf-8",
    )
    idx.upsert(
        item,
        req.body,
        embedding=embedder.embed(item.context_views.locator),
    )
    return {"id": item_id, "body_length": len(req.body)}


class BatchUpdateRequest(BaseModel):
    ids: list[str]
    updates: dict[str, Any]


@router.post("/api/items/batch-update")
async def batch_update(
    req: BatchUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Update the same fields on multiple items at once."""
    if not req.ids or not req.updates:
        raise HTTPException(status_code=400, detail="ids and updates required")
    store, idx, _, embedder = _components()
    updated = 0
    allowed = {"title", "summary", "tags", "confidence", "project"}
    kw = {k: v for k, v in req.updates.items() if k in allowed}
    if not kw:
        raise HTTPException(status_code=400, detail="no valid fields in updates")
    for item_id in req.ids:
        try:
            item, _ = store.get(item_id)
        except FileNotFoundError:
            continue
        if not _visible(item, user):
            continue
        item = store.update_frontmatter(item_id, **kw)
        _, body = store.get(item_id)
        embedding = embedder.embed(item.context_views.locator)
        idx.upsert(item, body, embedding=embedding)
        updated += 1
    return {"updated": updated}


@router.post("/api/items/{item_id}/pin")
async def pin_item(item_id: str, user: CurrentUser = Depends(get_current_user)):
    """Add 'pinned' tag to mark an item as important."""
    store, _, _, _ = _components()
    try:
        item, _ = store.get(item_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="item not found")
    if not _visible(item, user):
        raise HTTPException(status_code=403, detail="access denied")
    tags = set(item.tags)
    was_pinned = "pinned" in tags
    if was_pinned:
        tags.discard("pinned")
    else:
        tags.add("pinned")
    store.update_frontmatter(item_id, tags=sorted(tags))
    _audit(user.username, "pin" if not was_pinned else "unpin", item_id)
    return {"id": item_id, "pinned": not was_pinned}
