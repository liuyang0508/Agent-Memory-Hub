"""Simple token-based auth for the admin API.

Users are stored in a YAML file at $BRAIN_DIR/users.yaml:
    - username: admin
      password_hash: "$2b$12$..."   # bcrypt hash
      tenant_id: default
      role: admin

Tokens are JWTs signed with a per-instance secret stored at $BRAIN_DIR/.web_secret.
"""

from __future__ import annotations

import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, Request as _FastAPIRequest, Response, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from web.auth_storage import (
    load_users,
    save_users,
    secret_key,
)

try:
    import jwt
    from jwt import InvalidTokenError as JWTError
except ImportError:
    jwt = None  # type: ignore[assignment]
    JWTError = Exception  # type: ignore[assignment,misc]

try:
    import bcrypt as _bcrypt
except ImportError:
    _bcrypt = None  # type: ignore[assignment]


def _hash_password(password: str) -> str:
    if _bcrypt is None:
        raise RuntimeError("bcrypt not installed: pip install bcrypt")
    return _bcrypt.hashpw(password.encode("utf-8"), _bcrypt.gensalt()).decode("ascii")


def _verify_password(password: str, hashed: str) -> bool:
    if _bcrypt is None:
        return False
    return _bcrypt.checkpw(password.encode("utf-8"), hashed.encode("ascii"))

ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 72
SESSION_COOKIE = "amh_session"
REALTIME_TICKET_EXPIRE_SECONDS = 60

_bearer = HTTPBearer(auto_error=False)
_consumed_realtime_tickets: dict[str, int] = {}
_realtime_ticket_lock = threading.Lock()


def _brain_dir() -> Path:
    return Path(os.environ.get("BRAIN_DIR", os.path.expanduser("~/.agent-memory-hub")))


def _secret_key() -> str:
    return secret_key(_brain_dir())


def _load_users() -> list[dict[str, Any]]:
    return load_users(_brain_dir())


def _save_users(users: list[dict[str, Any]]) -> None:
    save_users(_brain_dir(), users, replace=os.replace)


def create_user(username: str, password: str, tenant_id: str = "default", role: str = "user") -> dict[str, str]:
    users = _load_users()
    if any(u["username"] == username for u in users):
        raise ValueError(f"user already exists: {username}")
    api_key = f"mhk_{secrets.token_urlsafe(24)}"
    users.append({
        "username": username,
        "password_hash": _hash_password(password),
        "tenant_id": tenant_id,
        "role": role,
        "api_key": api_key,
    })
    _save_users(users)
    return {"username": username, "tenant_id": tenant_id, "role": role, "api_key": api_key}


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    for u in _load_users():
        if u["username"] == username and _verify_password(password, u["password_hash"]):
            return u
    return None


def create_token(user: dict[str, Any]) -> str:
    if jwt is None:
        raise RuntimeError("PyJWT not installed: pip install PyJWT")
    payload = {
        "sub": user["username"],
        "tenant_id": user.get("tenant_id", "default"),
        "role": user.get("role", "user"),
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, _secret_key(), algorithm=ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    if jwt is None:
        raise RuntimeError("PyJWT not installed")
    return jwt.decode(token, _secret_key(), algorithms=[ALGORITHM])


def set_session_cookie(response: Response, token: str, *, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=TOKEN_EXPIRE_HOURS * 3600,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


class CurrentUser:
    def __init__(self, username: str, tenant_id: str, role: str):
        self.username = username
        self.tenant_id = tenant_id
        self.role = role

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def create_realtime_ticket(user: CurrentUser) -> str:
    if jwt is None:
        raise RuntimeError("PyJWT not installed: pip install PyJWT")
    payload = {
        "sub": user.username,
        "tenant_id": user.tenant_id,
        "role": user.role,
        "purpose": "realtime",
        "jti": secrets.token_urlsafe(18),
        "exp": datetime.now(timezone.utc)
        + timedelta(seconds=REALTIME_TICKET_EXPIRE_SECONDS),
    }
    return jwt.encode(payload, _secret_key(), algorithm=ALGORITHM)


def _consume_ticket_jti(jti: str, expires_at: int) -> None:
    now = int(datetime.now(timezone.utc).timestamp())
    with _realtime_ticket_lock:
        expired = [
            key
            for key, expiry in _consumed_realtime_tickets.items()
            if expiry <= now
        ]
        for key in expired:
            del _consumed_realtime_tickets[key]
        if jti in _consumed_realtime_tickets:
            raise JWTError("realtime ticket already used")
        _consumed_realtime_tickets[jti] = expires_at


def consume_realtime_ticket(ticket: str) -> dict[str, Any]:
    payload = decode_token(ticket)
    jti = payload.get("jti")
    expires_at = payload.get("exp")
    if (
        payload.get("purpose") != "realtime"
        or not isinstance(jti, str)
        or not isinstance(expires_at, int)
    ):
        raise JWTError("invalid realtime ticket")
    _consume_ticket_jti(jti, expires_at)
    return payload


def require_admin(user: CurrentUser) -> None:
    """Reject authenticated users that lack global admin authority."""

    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin only")


def _find_user_by_api_key(key: str) -> dict[str, Any] | None:
    for u in _load_users():
        if u.get("api_key") == key:
            return u
    return None


async def get_current_user(
    request: _FastAPIRequest,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> CurrentUser:
    api_key = request.headers.get("x-api-key", "")
    if api_key:
        user = _find_user_by_api_key(api_key)
        if user:
            return CurrentUser(
                username=user["username"],
                tenant_id=user.get("tenant_id", "default"),
                role=user.get("role", "user"),
            )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")
    if creds is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing token")
    try:
        payload = decode_token(creds.credentials)
        if payload.get("purpose") is not None:
            raise JWTError("purpose-restricted token")
        return CurrentUser(
            username=payload["sub"],
            tenant_id=payload.get("tenant_id", "default"),
            role=payload.get("role", "user"),
        )
    except (JWTError, KeyError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
