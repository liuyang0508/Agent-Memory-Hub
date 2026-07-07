"""Agent Memory Hub Web Admin — backups routes.

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
from web.auth import (
    CurrentUser,
    authenticate,
    create_token,
    create_user,
    get_current_user,
)

from web._base import *  # noqa: F401,F403  (state, helpers, models, lifespan, middleware)

router = APIRouter()


@router.post("/api/backup")
async def create_backup(user: CurrentUser = Depends(get_current_user)):
    """Create a compressed backup of the brain directory. Admin only."""
    import shutil
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    brain = _brain_dir()
    items_dir = brain / "items"
    if not items_dir.exists():
        raise HTTPException(status_code=404, detail="no items directory")
    backup_dir = brain / "backups"
    backup_dir.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_name = f"brain-backup-{timestamp}"
    backup_path = backup_dir / backup_name
    shutil.copytree(items_dir, backup_path)
    item_count = len(list(backup_path.glob("*.md")))
    return {"backup": str(backup_path), "items": item_count, "timestamp": timestamp}

@router.get("/api/backups")
async def list_backups(user: CurrentUser = Depends(get_current_user)):
    """List available backups."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    brain = _brain_dir()
    backup_dir = brain / "backups"
    if not backup_dir.exists():
        return {"backups": []}
    backups = []
    for d in sorted(backup_dir.iterdir(), reverse=True):
        if d.is_dir() and d.name.startswith("brain-backup-"):
            count = len(list(d.glob("*.md")))
            backups.append({"name": d.name, "path": str(d), "items": count})
    return {"backups": backups[:20]}

@router.post("/api/backups/{backup_name}/restore")
async def restore_backup(backup_name: str, user: CurrentUser = Depends(get_current_user)):
    """Restore items from a backup. Creates a pre-restore backup first. Admin only."""
    import shutil
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    brain = _brain_dir()
    backup_path = brain / "backups" / backup_name
    if not backup_path.exists() or not backup_path.is_dir():
        raise HTTPException(status_code=404, detail="backup not found")
    items_dir = brain / "items"
    pre_restore = brain / "backups" / f"pre-restore-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    if items_dir.exists():
        shutil.copytree(items_dir, pre_restore)
        shutil.rmtree(items_dir)
    shutil.copytree(backup_path, items_dir)
    store, idx, _, embedder = _components()
    count = 0
    for item, body in store.iter_all():
        idx.upsert(item, body, embedding=embedder.embed(item.context_views.locator))
        count += 1
    _audit(user.username, "restore", f"from {backup_name}, {count} items")
    _broadcast_event("backup_restored", {"backup": backup_name, "items": count}, admin_only=True)
    return {"restored": count, "backup": backup_name, "pre_restore_backup": pre_restore.name}
