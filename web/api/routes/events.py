"""Agent Memory Hub Web Admin — events routes.

Moved verbatim from app.py (decorators rewritten @app.→@router.); request models for
this group travel with their handlers in original order so FastAPI's decoration-time
binding is unchanged. Infra (helpers/state) comes from web._base.
"""
from __future__ import annotations

import asyncio
import json as _json

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from web._base import _sse_subscribers, _ws_subscribers
from web.auth import JWTError, decode_token

router = APIRouter()


@router.get("/api/events")
async def event_stream(request: Request, token: str = Query("")):
    """Server-Sent Events stream for real-time updates.

    EventSource can't set Authorization header, so token is passed as query param.
    """
    if not token:
        raise HTTPException(status_code=401, detail="missing token")
    try:
        payload = decode_token(token)
    except (JWTError, KeyError):
        raise HTTPException(status_code=401, detail="invalid token")
    sub_tenant = payload.get("tenant_id", "default")
    sub_is_admin = payload.get("role") == "admin"
    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    entry = (q, sub_tenant, sub_is_admin)
    _sse_subscribers.append(entry)

    async def generate():
        try:
            yield "data: {\"event\":\"connected\"}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield f"data: {_json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            if entry in _sse_subscribers:
                _sse_subscribers.remove(entry)

    return StreamingResponse(
        generate(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@router.websocket("/ws/events")
async def ws_events(ws: WebSocket, token: str = Query("")):
    """WebSocket endpoint for bidirectional real-time events."""
    if not token:
        await ws.close(code=4001, reason="missing token")
        return
    try:
        payload = decode_token(token)
    except (JWTError, KeyError):
        await ws.close(code=4001, reason="invalid token")
        return

    await ws.accept()
    sub_tenant = payload.get("tenant_id", "default")
    sub_is_admin = payload.get("role") == "admin"
    entry = (ws, sub_tenant, sub_is_admin)
    _ws_subscribers.append(entry)
    try:
        await ws.send_text(_json.dumps({"event": "connected", "data": {}}))
        while True:
            data = await ws.receive_text()
            try:
                msg = _json.loads(data)
                if msg.get("type") == "ping":
                    await ws.send_text(_json.dumps({"event": "pong", "data": {}}))
            except _json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        if entry in _ws_subscribers:
            _ws_subscribers.remove(entry)
