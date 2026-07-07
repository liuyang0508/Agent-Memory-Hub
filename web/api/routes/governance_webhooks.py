"""Webhook governance routes for the Web Admin API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from web._base import _audit, _state_store
from web.auth import CurrentUser, get_current_user


router = APIRouter()


class WebhookRequest(BaseModel):
    url: str
    events: list[str] = ["item_created", "item_updated", "item_deleted"]


@router.get("/api/webhooks")
async def list_webhooks(user: CurrentUser = Depends(get_current_user)):
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    return {"webhooks": _state_store().list_webhooks()}


@router.post("/api/webhooks")
async def add_webhook(req: WebhookRequest, user: CurrentUser = Depends(get_current_user)):
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    webhook = {"url": req.url, "events": req.events}
    total = _state_store().add_webhook(req.url, req.events)
    _audit(user.username, "webhook_add", req.url)
    return {"webhook": webhook, "total": total}


@router.delete("/api/webhooks")
async def remove_webhook(url: str = Query(...), user: CurrentUser = Depends(get_current_user)):
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    removed, total = _state_store().remove_webhook(url)
    _audit(user.username, "webhook_remove", url)
    return {"removed": removed, "total": total}


__all__ = ["WebhookRequest", "add_webhook", "list_webhooks", "remove_webhook", "router"]
