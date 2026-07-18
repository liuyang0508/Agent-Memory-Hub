"""Agent Memory Hub Web Admin — events routes.

Moved verbatim from app.py (decorators rewritten @app.→@router.); request models for
this group travel with their handlers in original order so FastAPI's decoration-time
binding is unchanged. Infra (helpers/state) comes from web._base.
"""
from __future__ import annotations

import asyncio
import json as _json
import os
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from web._base import _sse_subscribers, _ws_subscribers
from web.auth import (
    JWTError,
    SESSION_COOKIE,
    consume_realtime_ticket,
    decode_token,
)

router = APIRouter()


def _realtime_payload(
    *,
    cookie_token: str | None,
    ticket: str | None,
) -> dict[str, Any]:
    if ticket:
        return consume_realtime_ticket(ticket)
    if cookie_token:
        payload = decode_token(cookie_token)
        if payload.get("purpose"):
            raise JWTError("session token required")
        return payload
    raise JWTError("missing realtime credential")


def _realtime_origin_allowed(origin: str | None, host: str | None) -> bool:
    if not origin:
        return True
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    if host and parsed.netloc.lower() == host.lower():
        return True
    configured = {
        value.strip().rstrip("/")
        for value in os.environ.get("MEMORY_HUB_CORS_ORIGINS", "").split(",")
        if value.strip()
    }
    return origin.rstrip("/") in configured


@router.get("/api/events")
async def event_stream(
    request: Request,
    ticket: str | None = Query(None),
) -> StreamingResponse:
    """Server-Sent Events stream for real-time updates.

    Same-origin browsers authenticate with the HttpOnly session cookie. Clients
    without cookie support may pass a short-lived, one-use realtime ticket.
    """
    if not _realtime_origin_allowed(
        request.headers.get("origin"),
        request.headers.get("host"),
    ):
        raise HTTPException(status_code=403, detail="realtime origin is not allowed")
    if "token" in request.query_params:
        request.scope["query_string"] = b""
        raise HTTPException(status_code=401, detail="session token query is not supported")
    try:
        payload = _realtime_payload(
            cookie_token=request.cookies.get(SESSION_COOKIE),
            ticket=ticket,
        )
    except (JWTError, KeyError):
        raise HTTPException(status_code=401, detail="invalid realtime credential")
    sub_tenant = payload.get("tenant_id", "default")
    sub_is_admin = payload.get("role") == "admin"
    q: asyncio.Queue[Any] = asyncio.Queue(maxsize=64)
    entry = (q, sub_tenant, sub_is_admin)
    _sse_subscribers.append(entry)

    async def generate() -> AsyncIterator[str]:
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
async def ws_events(ws: WebSocket, ticket: str | None = Query(None)) -> None:
    """WebSocket endpoint for bidirectional real-time events."""
    if not _realtime_origin_allowed(ws.headers.get("origin"), ws.headers.get("host")):
        await ws.close(code=4003, reason="realtime origin is not allowed")
        return
    if "token" in ws.query_params:
        ws.scope["query_string"] = b""
        await ws.close(code=4001, reason="session token query is not supported")
        return
    try:
        payload = _realtime_payload(
            cookie_token=ws.cookies.get(SESSION_COOKIE),
            ticket=ticket,
        )
    except (JWTError, KeyError):
        await ws.close(code=4001, reason="invalid realtime credential")
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
