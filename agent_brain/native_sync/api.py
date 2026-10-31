"""Dedicated native-memory service; no imports of legacy web/brain state."""

from __future__ import annotations
from typing import Any
import sqlite3

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from .hub import Hub
from .models import (
    AdminInviteInput,
    AdminRevokeInput,
    AdminSecretInput,
    AdminSettingInput,
    AssetInput,
    EnrollInput,
    FeishuRedeemInput,
    FeishuStartInput,
    SearchInput,
    RuntimeInviteInput,
)


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

    @app.get("/native/v1/auth/feishu")
    def feishu_available() -> dict[str, Any]:
        """Advertise whether this deployment offers browser login, without disclosing credentials."""
        from . import feishu

        try:
            config = feishu.load(hub.setting)
        except ValueError:
            return {"available": False}
        return {"available": config is not None}

    def operator(authorization: str | None) -> str:
        return bearer(authorization)

    @app.get("/native/v1/admin")
    def admin_page() -> HTMLResponse:
        """Operator page; it holds no credential and lists no memory content."""
        from . import admin

        return HTMLResponse(admin.html())

    @app.get("/native/v1/admin/overview")
    def admin_overview(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        return hub.admin_overview(operator(authorization))

    @app.post("/native/v1/admin/invite")
    def admin_invite(
        data: AdminInviteInput, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        return hub.admin_invite(operator(authorization), data.name, data.ttl)

    @app.post("/native/v1/admin/revoke")
    def admin_revoke(
        data: AdminRevokeInput, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        hub.admin_revoke(operator(authorization), data.device_id)
        return {"revoked": data.device_id}

    @app.post("/native/v1/admin/settings")
    def admin_settings(
        data: AdminSettingInput, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        hub.admin_setting(operator(authorization), data.key, data.value)
        return {"saved": data.key}

    @app.post("/native/v1/admin/secret")
    def admin_secret(
        data: AdminSecretInput, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        hub.admin_secret_set(operator(authorization), data.app_secret)
        return {"saved": "feishu_app_secret"}

    @app.post("/native/v1/auth/feishu/start")
    def feishu_start(data: FeishuStartInput) -> dict[str, Any]:
        """Return the consent URL for a state bound to the caller's loopback port."""
        from . import feishu

        try:
            config = feishu.load(hub.setting)
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from None
        if config is None:
            raise HTTPException(503, "this Hub does not offer enterprise login")
        state = hub.feishu_begin(data.port)
        return {"authorize_url": feishu.authorize_url(config, state), "redirect_uri": config.redirect_uri}

    @app.get("/native/v1/auth/feishu/callback")
    def feishu_callback(
        code: str = Query(default=""),
        state: str = Query(default=""),
        error: str = Query(default=""),
    ) -> Any:
        """Consume the code, then hand a one-use ticket to the caller's loopback listener."""
        from . import feishu

        if error:
            raise HTTPException(400, "authorization declined")
        if not code or not state:
            raise HTTPException(400, "missing authorization code")
        try:
            config = feishu.load(hub.setting)
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from None
        if config is None:
            raise HTTPException(503, "this Hub does not offer enterprise login")
        try:
            # Public endpoint: refuse unknown states before spending an outbound
            # request, so invented states cannot drive Feishu's rate limit.
            hub.feishu_check(state)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        token = feishu.exchange_code(config, code)
        identity = feishu.user_identity(token)
        if not config.accepts(identity["tenant_key"]):
            raise HTTPException(403, "this Feishu tenant is not served by this Hub")
        port, ticket = hub.feishu_complete(state, identity)
        response = RedirectResponse(
            "http://127.0.0.1:%d/?ticket=%s" % (port, ticket), status_code=303
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'none'"
        return response

    @app.post("/native/v1/auth/feishu/redeem")
    def feishu_redeem(data: FeishuRedeemInput) -> dict[str, Any]:
        """Exchange the loopback ticket for the device credential, once."""
        return hub.feishu_redeem(data.ticket, data.device_name)

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
