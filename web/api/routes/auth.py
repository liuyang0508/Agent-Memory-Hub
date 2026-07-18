"""Agent Memory Hub Web Admin — auth routes.

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
    create_realtime_ticket,
    create_token,
    create_user,
    get_current_user,
    set_session_cookie,
)

from web._base import *  # noqa: F401,F403  (state, helpers, models, lifespan, middleware)

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str

class RegisterRequest(BaseModel):
    username: str
    password: str
    tenant_id: str = "default"

@router.post("/api/auth/login")
async def login(req: LoginRequest, request: Request, response: Response):
    user = authenticate(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="invalid credentials")
    token = create_token(user)
    set_session_cookie(response, token, secure=request.url.scheme == "https")
    return {"token": token, "username": user["username"], "tenant_id": user.get("tenant_id")}


@router.post("/api/auth/realtime-ticket")
async def realtime_ticket(user: CurrentUser = Depends(get_current_user)):
    return {
        "ticket": create_realtime_ticket(user),
        "expires_in": 60,
    }

@router.post("/api/auth/register")
async def register(req: RegisterRequest, user: CurrentUser = Depends(get_current_user)):
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    try:
        info = create_user(req.username, req.password, req.tenant_id)
        return info
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

@router.get("/api/auth/users")
async def list_users(user: CurrentUser = Depends(get_current_user)):
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    from web.auth import _load_users
    users = _load_users()
    return {"users": [
        {"username": u["username"], "tenant_id": u.get("tenant_id", "default"), "role": u.get("role", "user")}
        for u in users
    ]}

@router.post("/api/auth/rotate-key")
async def rotate_api_key(user: CurrentUser = Depends(get_current_user)):
    """Rotate the current user's API key. Returns the new key."""
    import secrets as _secrets
    from web.auth import _load_users, _save_users
    users = _load_users()
    new_key = ""
    for u in users:
        if u["username"] == user.username:
            new_key = f"mhk_{_secrets.token_urlsafe(24)}"
            u["api_key"] = new_key
            break
    if not new_key:
        raise HTTPException(status_code=404, detail="user not found")
    _save_users(users)
    _audit(user.username, "rotate_api_key", user.username)
    return {"api_key": new_key, "username": user.username}

@router.get("/api/auth/me")
async def get_me(user: CurrentUser = Depends(get_current_user)):
    """Get current user info."""
    return {"username": user.username, "tenant_id": user.tenant_id, "role": user.role, "is_admin": user.is_admin}

@router.post("/api/auth/init")
async def init_admin(req: LoginRequest, request: Request, response: Response):
    """Create initial admin user. Only works when no users exist."""
    from web.auth import _load_users
    if _load_users():
        raise HTTPException(status_code=409, detail="admin already exists; use /api/auth/login")
    info = create_user(req.username, req.password, tenant_id="default", role="admin")
    user = authenticate(req.username, req.password)
    token = create_token(user)
    set_session_cookie(response, token, secure=request.url.scheme == "https")
    return {"token": token, **info}

@router.get("/api/auth/needs-init")
async def needs_init():
    """Whether the system needs first-time admin initialization (no users yet).

    Lets the login page auto-detect a fresh install and steer the user to
    "init admin" instead of guessing (restored from the v1.3 line).
    """
    from web.auth import _load_users
    return {"needs_init": not _load_users()}
