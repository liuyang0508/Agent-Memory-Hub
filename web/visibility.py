"""Web tenant visibility and item-id safety helpers."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from web.auth import CurrentUser


def visible(item: Any, user: CurrentUser) -> bool:
    """Admin sees everything; other users only see their tenant's items."""
    if user.is_admin:
        return True
    return item.tenant_id is None or item.tenant_id == user.tenant_id


def safe_item_id(item_id: str) -> str:
    """Reject path-traversal item ids before building a filesystem path."""
    if not item_id or "/" in item_id or "\\" in item_id or ".." in item_id:
        raise HTTPException(status_code=400, detail="invalid item_id")
    return item_id


def require_visible(store: Any, item_id: str, user: CurrentUser):
    """Load an item, enforcing tenant visibility on mutating routes."""
    safe_item_id(item_id)
    try:
        item, body = store.get(item_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="item not found")
    if not visible(item, user):
        raise HTTPException(status_code=403, detail="forbidden")
    return item, body


__all__ = ["require_visible", "safe_item_id", "visible"]
