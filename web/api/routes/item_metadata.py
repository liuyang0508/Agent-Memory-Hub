"""Metadata routes for projects and tags."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from web._base import _audit, _broadcast_event, _components, _visible
from web.auth import CurrentUser, get_current_user


router = APIRouter()


@router.get("/api/projects")
async def list_projects(user: CurrentUser = Depends(get_current_user)):
    """Return all distinct project names and their item counts."""
    store, _, _, _ = _components()
    counts: dict[str, int] = {}
    for item, _ in store.iter_all():
        if not _visible(item, user):
            continue
        p = item.project or "(none)"
        counts[p] = counts.get(p, 0) + 1
    return {
        "projects": [
            {"name": k, "count": v}
            for k, v in sorted(counts.items(), key=lambda x: -x[1])
        ]
    }


@router.get("/api/tags")
async def list_tags(user: CurrentUser = Depends(get_current_user)):
    """Return all distinct tags and their item counts."""
    store, _, _, _ = _components()
    counts: dict[str, int] = {}
    for item, _ in store.iter_all():
        if not _visible(item, user):
            continue
        for tag in item.tags:
            counts[tag] = counts.get(tag, 0) + 1
    return {
        "tags": [
            {"name": k, "count": v}
            for k, v in sorted(counts.items(), key=lambda x: -x[1])
        ]
    }


class RenameTagRequest(BaseModel):
    old_name: str
    new_name: str


@router.post("/api/tags/rename")
async def rename_tag(req: RenameTagRequest, user: CurrentUser = Depends(get_current_user)):
    """Rename a tag across all items."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    store, _, _, _ = _components()
    updated = 0
    for item, _ in store.iter_all():
        if req.old_name in item.tags:
            new_tags = [req.new_name if t == req.old_name else t for t in item.tags]
            store.update_frontmatter(item.id, tags=new_tags)
            updated += 1
    _audit(user.username, "tag_rename", f"{req.old_name} -> {req.new_name} ({updated} items)")
    _broadcast_event(
        "tag_renamed",
        {"old": req.old_name, "new": req.new_name, "count": updated},
        admin_only=True,
    )
    return {"updated": updated, "old_name": req.old_name, "new_name": req.new_name}


class DeleteTagRequest(BaseModel):
    tag_name: str


@router.post("/api/tags/delete")
async def delete_tag(req: DeleteTagRequest, user: CurrentUser = Depends(get_current_user)):
    """Remove a tag from all items."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    store, _, _, _ = _components()
    updated = 0
    for item, _ in store.iter_all():
        if req.tag_name in item.tags:
            new_tags = [t for t in item.tags if t != req.tag_name]
            store.update_frontmatter(item.id, tags=new_tags)
            updated += 1
    _audit(user.username, "tag_delete", f"{req.tag_name} ({updated} items)")
    return {"removed": updated, "tag_name": req.tag_name}
