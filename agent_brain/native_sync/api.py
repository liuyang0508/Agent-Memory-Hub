"""Dedicated native-memory service; no imports of legacy web/brain state."""

from __future__ import annotations
from typing import Any
import sqlite3

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from .hub import Hub
from .models import AssetInput, EnrollInput, SearchInput, RuntimeInviteInput


def create_app(hub: Hub) -> FastAPI:
    """Build an independent HTTP facade; the Hub owns identity, tenant scope and transaction invariants."""
    app = FastAPI(title="AMH Native Organization Memory", docs_url=None, redoc_url=None)

    @app.exception_handler(PermissionError)
    async def denied(request: Any, error: Any) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": str(error)})

    @app.exception_handler(ValueError)
    async def conflict(request: Any, error: Any) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @app.exception_handler(sqlite3.Error)
    async def storage_unavailable(request: Any, error: Any) -> JSONResponse:
        # Retry belongs to clients, not an unbounded server-side retry loop.
        return JSONResponse(status_code=503, content={"detail": "storage unavailable"})

    def bearer(value: Any) -> str:
        if not value or not value.startswith("Bearer "):
            raise HTTPException(401, "device credential required")
        return str(value[7:])

    @app.get("/native/v1/health")
    def health() -> dict[str, Any]:
        """Liveness/protocol discovery; dependency failures must not cause restart loops."""
        return {"protocol": 1, "service": "amh-native"}

    @app.get("/native/v1/ready")
    def ready() -> JSONResponse:
        """Dependency readiness for monitoring; disclose no paths, records or SQL errors."""
        try:
            hub.ready()
        except (sqlite3.Error, OSError):
            return JSONResponse(status_code=503, content={"ready": False})
        return JSONResponse(content={"ready": True})

    @app.post("/native/v1/enroll")
    def enroll(data: EnrollInput) -> dict[str, Any]:
        return hub.enroll(data.invite, data.device_name)

    @app.get("/native/v1/me")
    def me(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        return hub.identity(bearer(authorization))

    @app.post("/native/v1/runtime-invites")
    def runtime_invite(
        data: RuntimeInviteInput, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        return hub.runtime_invite(bearer(authorization))

    @app.post("/native/v1/assets")
    def put(data: AssetInput, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        return hub.put(bearer(authorization), data.model_dump())

    @app.get("/native/v1/changes")
    def changes(
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=100),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        return hub.changes(bearer(authorization), cursor, limit)

    @app.post("/native/v1/search")
    def search(
        data: SearchInput, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        return {"items": hub.search(bearer(authorization), **data.model_dump())}

    app.add_middleware(BodyLimit)
    return app


class BodyLimit:
    """Bound memory before JSON parsing, including chunked requests without Content-Length."""

    def __init__(self, app: Any, limit: Any = 5_000_000) -> None:
        self.app, self.limit = app, limit

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("method") not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > self.limit:
                response = JSONResponse({"detail": "request too large"}, status_code=413)
                return await response(scope, receive, send)
            if not message.get("more_body"):
                break
        delivered = False

        async def replay() -> Any:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
