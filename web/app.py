"""Agent Memory Hub — Web Admin API.

启动: memory serve [--port 8765]

App assembly + rate-limit/middleware/lifespan + the `/` dashboard live here; the
/api and /ws route handlers live in web.api.routes.* (one APIRouter per
group) and are mounted below via include_router. Infra helpers come from
web._base. The request surface is byte-for-byte unchanged
(tests/conformance/test_web_surface_lock.py).
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from agent_brain._version import __version__

from web._base import *  # noqa: F401,F403  (state, helpers, models, lifespan, middleware)

app = FastAPI(title="Agent Memory Hub Admin", version=__version__, lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_rate_limit_store: dict[str, list[float]] = {}

_RATE_LIMIT_WINDOW = 60

_RATE_LIMIT_DEFAULT = 120

_RATE_LIMIT_MAX_IPS = 10_000

def _rate_limit_value() -> int:
    """Parse MEMORY_HUB_RATE_LIMIT, tolerating non-integer env values.

    A junk value (e.g. "fast") used to raise ValueError inside the middleware,
    turning every single request into a 500. Fall back to the default (P3-8).
    """
    try:
        return int(os.environ.get("MEMORY_HUB_RATE_LIMIT", str(_RATE_LIMIT_DEFAULT)))
    except (TypeError, ValueError):
        return _RATE_LIMIT_DEFAULT

def _prune_rate_limit_store(now: float) -> None:
    """Drop IPs whose entire request window has aged out (bounds memory, P3-8)."""
    stale = [
        ip
        for ip, hits in _rate_limit_store.items()
        if not any(now - t < _RATE_LIMIT_WINDOW for t in hits)
    ]
    for ip in stale:
        del _rate_limit_store[ip]

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    limit = _rate_limit_value()
    if limit <= 0:
        return await call_next(request)
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    if len(_rate_limit_store) > _RATE_LIMIT_MAX_IPS:
        _prune_rate_limit_store(now)
    window = [t for t in _rate_limit_store.get(client_ip, []) if now - t < _RATE_LIMIT_WINDOW]
    if len(window) >= limit:
        _rate_limit_store[client_ip] = window
        return Response(
            content='{"detail":"rate limit exceeded"}',
            status_code=429,
            media_type="application/json",
            headers={"Retry-After": str(_RATE_LIMIT_WINDOW)},
        )
    window.append(now)
    _rate_limit_store[client_ip] = window
    return await call_next(request)

@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
    response.headers["X-Response-Time"] = f"{elapsed_ms}ms"
    return response

if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    template = _TEMPLATES_DIR / "dashboard.html"
    if template.exists():
        return template.read_text(encoding="utf-8")
    return "<h1>Agent Memory Hub Admin</h1><p>Dashboard template not found.</p>"

from web.api.routes import (
    adapters, agent_history, auth, backups, chain_logs, cockpit, data_flow, events, governance, graph, health, items,
    memory_candidates, memory_lineage, product_capabilities,
)
for _r in (
    auth,
    items,
    graph,
    governance,
    health,
    backups,
    chain_logs,
    events,
    adapters,
    agent_history,
    cockpit,
    data_flow,
    memory_candidates,
    memory_lineage,
    product_capabilities,
):
    app.include_router(_r.router)


def serve(host: str = "0.0.0.0", port: int = 8765):
    import uvicorn
    uvicorn.run(app, host=host, port=port)
