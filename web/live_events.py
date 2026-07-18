"""Live SSE/WebSocket event fanout helpers for the Web Admin API."""
from __future__ import annotations

import asyncio
import json as _json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Protocol


class WebSocketSender(Protocol):
    async def send_text(self, data: str) -> None: ...


SSESubscriber = tuple[asyncio.Queue[Any], str | None, bool]
WebSocketSubscriber = tuple[WebSocketSender, str | None, bool]

_sse_subscribers: list[SSESubscriber] = []
_ws_subscribers: list[WebSocketSubscriber] = []


def _event_visible_to(
    sub_tenant: str | None,
    sub_is_admin: bool,
    *,
    tenant_id: str | None,
    admin_only: bool,
) -> bool:
    """Mirror item visibility rules for live events."""
    if sub_is_admin:
        return True
    if admin_only:
        return False
    if tenant_id is None:
        return True
    return sub_tenant == tenant_id


async def _ws_send_and_reap(entry: WebSocketSubscriber, text: str) -> None:
    """Send to one websocket, dropping its subscriber entry on failure."""
    ws = entry[0]
    try:
        await ws.send_text(text)
    except Exception:
        if entry in _ws_subscribers:
            _ws_subscribers.remove(entry)


def broadcast_live_event(
    event_type: str,
    data: dict[str, Any],
    *,
    tenant_id: str | None = None,
    admin_only: bool = False,
    fire_webhooks: Callable[[str, dict[str, Any]], None] | None = None,
) -> None:
    """Fan out a live event to tenant-visible SSE/WS subscribers."""
    msg = {"event": event_type, "data": data, "ts": datetime.now(timezone.utc).isoformat()}
    dead: list[SSESubscriber] = []
    for sse_entry in _sse_subscribers:
        q, sub_tenant, sub_is_admin = sse_entry
        if not _event_visible_to(sub_tenant, sub_is_admin, tenant_id=tenant_id, admin_only=admin_only):
            continue
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            dead.append(sse_entry)
    for sse_entry in dead:
        _sse_subscribers.remove(sse_entry)

    msg_str = _json.dumps(msg)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    for ws_entry in list(_ws_subscribers):
        ws, sub_tenant, sub_is_admin = ws_entry
        if not _event_visible_to(sub_tenant, sub_is_admin, tenant_id=tenant_id, admin_only=admin_only):
            continue
        if loop is not None:
            loop.create_task(_ws_send_and_reap(ws_entry, msg_str))

    if fire_webhooks is not None:
        fire_webhooks(event_type, data)


__all__ = [
    "_event_visible_to",
    "_sse_subscribers",
    "_ws_subscribers",
    "_ws_send_and_reap",
    "broadcast_live_event",
]
