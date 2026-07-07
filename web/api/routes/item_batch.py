"""Batch and merge routes for item data."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from agent_brain.memory.store.items_store import make_item_id
from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Sensitivity
from web._base import (
    _components,
    _evict_from_index,
    _require_visible,
    _visible,
    _write_service,
    mutate_item,
)
from web.auth import CurrentUser, get_current_user


router = APIRouter()


class BatchRequest(BaseModel):
    ids: list[str]


@router.post("/api/items/batch-delete")
async def batch_delete(req: BatchRequest, user: CurrentUser = Depends(get_current_user)):
    store, _, _, _ = _components()
    deleted, missing = 0, 0
    for item_id in req.ids:
        # Silently skip ids the caller may not see (do not leak existence).
        try:
            _require_visible(store, item_id, user)
        except HTTPException:
            missing += 1
            continue
        md_path = store.items_dir / f"{item_id}.md"
        if md_path.exists():
            md_path.unlink()
            _evict_from_index(item_id)
            deleted += 1
        else:
            missing += 1
    return {"deleted": deleted, "missing": missing}


class BatchConfirmRequest(BaseModel):
    ids: list[str]
    confidence: float = 0.9


@router.post("/api/items/batch-confirm")
async def batch_confirm(req: BatchConfirmRequest, user: CurrentUser = Depends(get_current_user)):
    confirmed, errors = 0, 0
    for item_id in req.ids:
        try:
            mutate_item(item_id, user, {"confidence": req.confidence}, event="item_confirmed")
            confirmed += 1
        except (FileNotFoundError, HTTPException):
            errors += 1
    return {"confirmed": confirmed, "errors": errors}


class BatchTagRequest(BaseModel):
    ids: list[str]
    add_tags: list[str] = []
    remove_tags: list[str] = []


@router.post("/api/items/batch-tag")
async def batch_tag(req: BatchTagRequest, user: CurrentUser = Depends(get_current_user)):
    """Add or remove tags from multiple items at once."""
    store, _, _, _ = _components()
    updated, errors = 0, 0
    add_set = set(req.add_tags)
    remove_set = set(req.remove_tags)
    for item_id in req.ids:
        try:
            item, _ = _require_visible(store, item_id, user)
            new_tags = sorted((set(item.tags) | add_set) - remove_set)
            mutate_item(item_id, user, {"tags": new_tags}, event="item_tagged")
            updated += 1
        except (FileNotFoundError, Exception):
            errors += 1
    return {"updated": updated, "errors": errors}


class MergeRequest(BaseModel):
    ids: list[str]
    title: str
    summary: str
    keep_originals: bool = False


@router.post("/api/items/merge")
async def merge_items(req: MergeRequest, user: CurrentUser = Depends(get_current_user)):
    """Merge multiple memory items into a single new item."""
    if len(req.ids) < 2:
        raise HTTPException(status_code=400, detail="need at least 2 items to merge")

    store, _, _, _ = _components()
    bodies = []
    all_tags: set[str] = set()
    types: list[str] = []
    projects: list[str] = []
    max_confidence = 0.0

    for item_id in req.ids:
        try:
            item, body = store.get(item_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"item {item_id} not found")
        if not _visible(item, user):
            raise HTTPException(status_code=403, detail="access denied")
        bodies.append(f"## {item.title}\n\n{body}")
        all_tags.update(item.tags)
        types.append(str(item.type))
        if item.project:
            projects.append(item.project)
        max_confidence = max(max_confidence, item.confidence)

    now = datetime.now(timezone.utc)
    merged_id = make_item_id(req.title, when=now, label="merged")
    merged_body = "\n\n---\n\n".join(bodies)
    most_common_type = Counter(types).most_common(1)[0][0]

    merged_item = MemoryItem(
        id=merged_id,
        type=MemoryType(most_common_type),
        created_at=now,
        agent="web-admin",
        project=projects[0] if projects else None,
        tags=sorted(all_tags | {"merged"}),
        sensitivity=Sensitivity("internal"),
        title=req.title,
        summary=req.summary,
        confidence=max_confidence,
    )
    result = _write_service().write(item=merged_item, body=merged_body)
    if result.status == "blocked":
        raise HTTPException(status_code=400, detail=asdict(result))

    if not req.keep_originals:
        for item_id in req.ids:
            md_path = store.items_dir / f"{item_id}.md"
            if md_path.exists():
                md_path.unlink()
                _evict_from_index(item_id)

    return {
        "merged_id": merged_id,
        "source_count": len(req.ids),
        "originals_kept": req.keep_originals,
        "indexed": result.indexed,
        "warnings": result.warnings,
    }
