"""History routes for item snapshots."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from web._base import _state_store
from web.auth import CurrentUser, get_current_user


router = APIRouter()


@router.get("/api/items/{item_id}/history")
async def item_history(item_id: str, user: CurrentUser = Depends(get_current_user)):
    """Get version history for an item."""
    snapshots = _state_store().list_snapshots(item_id)
    return {
        "id": item_id,
        "snapshots": [
            {
                "timestamp": s["timestamp"],
                "title": s["frontmatter"].get("title", ""),
                "confidence": s["frontmatter"].get("confidence", 0),
            }
            for s in snapshots
        ],
        "count": len(snapshots),
    }


@router.get("/api/items/{item_id}/history/{index}")
async def item_snapshot(item_id: str, index: int, user: CurrentUser = Depends(get_current_user)):
    """Get a specific version snapshot."""
    snapshots = _state_store().list_snapshots(item_id)
    if index < 0 or index >= len(snapshots):
        raise HTTPException(status_code=404, detail="snapshot not found")
    return snapshots[index]
