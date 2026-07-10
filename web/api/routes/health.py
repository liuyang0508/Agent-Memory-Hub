"""Agent Memory Hub Web Admin — health routes.

Moved verbatim from app.py (decorators rewritten @app.→@router.); request models for
this group travel with their handlers in original order so FastAPI's decoration-time
binding is unchanged. Infra (helpers/state) comes from web._base.
"""
from __future__ import annotations

from fastapi import APIRouter

import asyncio
import io
import json as _json
import os
import time
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from agent_brain._version import __version__
from web.auth import (
    CurrentUser,
    authenticate,
    create_token,
    create_user,
    get_current_user,
    require_admin,
)
from web.health_payloads import build_health_detail_payload

from web._base import *  # noqa: F401,F403  (state, helpers, models, lifespan, middleware)

router = APIRouter()


@router.get("/api/health-detail")
async def health_detail(user: CurrentUser = Depends(get_current_user)):
    """Run governance + drift checks and return a detailed health report."""
    require_admin(user)
    store, _, _, _ = _components()

    try:
        from agent_brain.memory.governance.pipeline import GovernancePipeline
        pipeline = GovernancePipeline(items_store=store)
        gov_report = pipeline.run()
    except Exception:
        gov_report = None

    try:
        from agent_brain.memory.governance.drift import DriftDetector
        detector = DriftDetector(items_store=store)
        drift_report = detector.detect()
    except Exception:
        drift_report = None

    return build_health_detail_payload(
        total_items=sum(1 for _ in store.iter_all()),
        skipped_items=store.last_scan.skipped_count,
        gov_report=gov_report,
        drift_report=drift_report,
    )

@router.get("/api/routes")
async def list_routes(request: Request, user: CurrentUser = Depends(get_current_user)):
    """List all API routes for the interactive docs page."""
    routes = []
    for route in _iter_visible_routes(request.app.routes):
        if not hasattr(route, "path"):
            continue
        if not (route.path.startswith("/api/") or route.path.startswith("/ws/")):
            continue
        doc = ""
        if route.endpoint and route.endpoint.__doc__:
            doc = route.endpoint.__doc__.strip().split("\n")[0]
        params = []
        import inspect
        sig = inspect.signature(route.endpoint)
        for pname, param in sig.parameters.items():
            if pname in ("request", "user", "self"):
                continue
            ptype = "string"
            if param.annotation != inspect.Parameter.empty:
                ann = param.annotation
                if ann is int:
                    ptype = "integer"
                elif ann is float:
                    ptype = "float"
                elif ann is bool:
                    ptype = "boolean"
                elif hasattr(ann, "__name__"):
                    ptype = ann.__name__
            params.append({"name": pname, "type": ptype})
        methods = getattr(route, "methods", None) or {"WEBSOCKET"}
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            routes.append({
                "method": method,
                "path": route.path,
                "description": doc,
                "params": params,
            })
    routes.sort(key=lambda r: (r["path"], r["method"]))
    return {"routes": routes, "total": len(routes)}


def _iter_visible_routes(route_entries):
    for route in route_entries:
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            yield from _iter_visible_routes(original_router.routes)
            continue
        yield route

@router.get("/api/health")
async def health_check():
    """Unauthenticated health check for load balancers and monitoring."""
    brain = _brain_dir()
    items_dir = brain / "items"
    items_exist = items_dir.exists()
    item_count = len(list(items_dir.glob("*.md"))) if items_exist else 0
    return {
        "status": "ok",
        "version": __version__,
        "brain_dir": str(brain),
        "items_count": item_count,
    }

@router.get("/api/version")
async def version_info():
    return {"version": __version__, "name": "agent-memory-hub"}
