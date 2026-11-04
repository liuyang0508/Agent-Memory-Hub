"""Dedicated native-memory service; no imports of legacy web/brain state."""

from __future__ import annotations
from typing import Any, Literal
from collections.abc import Awaitable, Callable
import sqlite3
import hmac
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from . import insights
from .hub import Hub
from .admin_auth import (
    AdminAuth,
    AdminCredential,
    WebAdminSession,
    FLOW_COOKIE,
    SESSION_COOKIE,
    STATE_TTL,
    SESSION_TTL,
)
from .knowledge import KnowledgeConflict, NativeKnowledge
from .models import (
    AdminRevokeInput,
    AdminSecretInput,
    AdminSettingInput,
    AssetInput,
    EnrollInput,
    FeishuRedeemInput,
    FeishuStartInput,
    GoldInput,
    SearchInput,
    RecallFeedbackInput,
    RecallInput,
    RetrieveInput,
    ReadInput,
    RelatedInput,
    RuntimeInviteInput,
)


def create_app(hub: Hub) -> FastAPI:
    """Build an independent HTTP facade; the Hub owns identity, tenant scope and transaction invariants."""
    app = FastAPI(title="AMH Native Organization Memory", docs_url=None, redoc_url=None)
    knowledge = NativeKnowledge(hub)
    admin_auth = AdminAuth(hub)

    @app.middleware("http")
    async def private_admin_responses(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        if request.url.path.startswith("/native/v1/admin"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(KnowledgeConflict)
    async def knowledge_conflict(request: Any, error: KnowledgeConflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"code": error.code, "detail": str(error)})

    @app.exception_handler(PermissionError)
    async def denied(request: Any, error: Any) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": str(error)})

    @app.exception_handler(ValueError)
    async def conflict(request: Any, error: Any) -> JSONResponse:
        # Never echo an arbitrary exception message: some carry SQL, paths or
        # decoder internals. Messages this service raises on purpose are short
        # and lowercase; anything longer is treated as internal.
        detail = str(error)
        if len(detail) > 200:
            detail = "request rejected"
        return JSONResponse(status_code=409, content={"detail": detail})

    @app.exception_handler(sqlite3.Error)
    async def storage_unavailable(request: Any, error: Any) -> JSONResponse:
        # Retry belongs to clients, not an unbounded server-side retry loop.
        return JSONResponse(status_code=503, content={"detail": "storage unavailable"})

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Any, error: RequestValidationError) -> JSONResponse:
        # Location, message and type only. FastAPI's default handler echoes the
        # offending input, which can be a whole memory body; and when that
        # input holds NaN/Infinity the echo itself fails to render, turning a
        # 422 into a 409 with decoder text that clients misread.
        detail = [
            {
                "loc": [part for part in item.get("loc", ()) if isinstance(part, (str, int))],
                "msg": str(item.get("msg", ""))[:200],
                "type": str(item.get("type", ""))[:64],
            }
            for item in error.errors()[:20]
        ]
        return JSONResponse(status_code=422, content={"detail": detail})

    def bearer(value: Any) -> str:
        if not value or not value.startswith("Bearer "):
            raise HTTPException(401, "device credential required")
        return str(value[7:])

    @app.get("/native/v1/health")
    def health() -> dict[str, Any]:
        """Liveness/protocol discovery; dependency failures must not cause restart loops."""
        capabilities = [
            "org-research-v1",
            "admin-feishu-session-v1",
            "recall-parity-v1",
            "insights-v1",
            "asking-context-v1",
            "recall-usage-feedback-v1",
            "recall-hints-v1",
        ]
        state = dict(getattr(hub, "semantic_state", {}) or {})
        semantic = {k: state[k] for k in ("state", "model", "reason") if k in state}
        if state.get("state") == "ready":
            capabilities.append("semantic-v1")
            gate = hub.gate_state()
            semantic["gate"] = gate
            if gate.startswith("learned-"):
                capabilities.append("relevance-gate-v1")
        from agent_brain._version import __version__

        return {
            "protocol": 1,
            "service": "amh-native",
            # Clients update themselves to this version (autoupdate.py).
            "version": __version__,
            "capabilities": capabilities,
            # Model name, index and gate state only; no counts, paths or tenants.
            "semantic": semantic,
        }

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
        return {
            "available": bool(
                config
                and (
                    config.allowed_tenants
                    or hub.setting("feishu_tenant_key")
                    or hub.prepare_legacy_provider(config.app_id)
                )
            )
        }

    def operator(request: Request, authorization: str | None) -> AdminCredential:
        # An explicit Bearer header never falls back to a browser cookie.
        if authorization is not None:
            return bearer(authorization)
        token = request.cookies.get(SESSION_COOKIE, "")
        session = admin_auth.session(token)
        if request.method not in ("GET", "HEAD"):
            origin = urlsplit(admin_auth.config().public_url)
            expected = origin.scheme + "://" + origin.netloc
            if request.headers.get("origin") != expected or not hmac.compare_digest(
                request.headers.get("x-amh-csrf", "").encode(), session["csrf"].encode()
            ):
                raise HTTPException(403, "same-origin administrator action required")
        return WebAdminSession(token)

    def web_redirect(destination: str) -> RedirectResponse:
        response = RedirectResponse(destination, status_code=303)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.delete_cookie(FLOW_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        return response

    @app.get("/native/v1/admin/login")
    def admin_login(
        request: Request, target: Literal["admin", "dashboard", "insights", "recalls"] = "admin"
    ) -> Any:
        from . import feishu

        try:
            config = admin_auth.config()
            state, browser = admin_auth.begin(target, request.cookies.get(FLOW_COOKIE, ""))
        except ValueError:
            return web_redirect("/native/v1/admin?login=unavailable")
        response = web_redirect(feishu.authorize_url(config, state))
        response.set_cookie(
            FLOW_COOKIE,
            browser,
            max_age=STATE_TTL,
            path="/",
            secure=True,
            httponly=True,
            samesite="lax",
        )
        return response

    @app.get("/native/v1/admin/session")
    def admin_session(request: Request) -> JSONResponse:
        response = JSONResponse(admin_auth.session(request.cookies.get(SESSION_COOKIE, "")))
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/native/v1/admin/logout")
    def admin_logout(request: Request) -> JSONResponse:
        try:
            operator(request, None)
        except PermissionError:
            pass  # An already expired session may still clear its stale cookie.
        admin_auth.logout(request.cookies.get(SESSION_COOKIE, ""))
        response = JSONResponse({"signed_out": True})
        response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/native/v1/admin")
    def admin_page() -> HTMLResponse:
        """Operator page; it holds no credential and lists no memory content."""
        from . import admin

        return HTMLResponse(admin.html())

    @app.get("/native/v1/admin/dashboard")
    def admin_dashboard_page() -> HTMLResponse:
        """Activity dashboard; the page holds no credential and no memory text."""
        from . import dashboard

        return HTMLResponse(dashboard.html())

    @app.get("/native/v1/admin/dashboard/data")
    def admin_dashboard(
        request: Request,
        days: int = Query(default=30, ge=1, le=365),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Aggregate activity for the operator dashboard; counts only, no memory text."""
        return hub.admin_dashboard(operator(request, authorization), days=days)

    @app.get("/native/v1/admin/overview")
    def admin_overview(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        return hub.admin_overview(operator(request, authorization))

    @app.post("/native/v1/admin/revoke")
    def admin_revoke(
        request: Request, data: AdminRevokeInput, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        hub.admin_revoke(operator(request, authorization), data.device_id)
        return {"revoked": data.device_id}

    @app.post("/native/v1/admin/settings")
    def admin_settings(
        request: Request, data: AdminSettingInput, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        hub.admin_setting(operator(request, authorization), data.key, data.value)
        return {"saved": data.key}

    @app.post("/native/v1/admin/secret")
    def admin_secret(
        request: Request, data: AdminSecretInput, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        hub.admin_secret_set(operator(request, authorization), data.app_secret)
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
        hub.prepare_legacy_provider(config.app_id)
        state = hub.feishu_begin(data.port)
        return {
            "authorize_url": feishu.authorize_url(config, state),
            "redirect_uri": config.redirect_uri,
        }

    @app.get("/native/v1/auth/feishu/callback")
    def feishu_callback(
        request: Request,
        code: str = Query(default=""),
        state: str = Query(default=""),
        error: str = Query(default=""),
    ) -> Any:
        """Consume the code, then hand a one-use ticket to the caller's loopback listener."""
        from . import feishu

        if state.startswith("admin."):
            browser = request.cookies.get(FLOW_COOKIE, "")
            try:
                admin_auth.check(state, browser)
            except ValueError:
                return web_redirect("/native/v1/admin?login=expired")
            if error or not code:
                return web_redirect("/native/v1/admin?login=cancelled")
            try:
                web_config = admin_auth.config()
                provider_token = feishu.exchange_code(web_config, code)
                identity = feishu.user_identity(provider_token)
            except (ValueError, OSError):
                return web_redirect("/native/v1/admin?login=unavailable")
            try:
                session, destination = admin_auth.complete(
                    state, browser, identity, request.cookies.get(SESSION_COOKIE, "")
                )
            except PermissionError:
                response = web_redirect("/native/v1/admin?login=denied")
                response.delete_cookie(
                    SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax"
                )
                return response
            except ValueError:
                return web_redirect("/native/v1/admin?login=expired")
            response = web_redirect(destination)
            response.set_cookie(
                SESSION_COOKIE,
                session,
                max_age=SESSION_TTL,
                path="/",
                secure=True,
                httponly=True,
                samesite="lax",
            )
            return response

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
        hub.prepare_legacy_provider(config.app_id)
        if not hub.feishu_accept_tenant(
            identity["tenant_key"],
            config.allowed_tenants,
            app_id=config.app_id,
            verified_identity=identity,
        ):
            raise HTTPException(403, "this Feishu tenant is not served by this Hub")
        port, ticket = hub.feishu_complete(state, identity, app_id=config.app_id)
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
        return hub.enroll(data.ticket, data.device_name)

    @app.get("/native/v1/me")
    def me(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        return hub.identity(bearer(authorization))

    @app.post("/native/v1/runtime-invites")
    def runtime_invite(
        data: RuntimeInviteInput, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        return hub.runtime_invite(bearer(authorization), ttl_seconds=data.ttl_seconds)

    @app.post("/native/v1/assets")
    def put(data: AssetInput, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        return hub.put(bearer(authorization), data.model_dump())

    @app.get("/native/v1/changes")
    def changes(
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=100),
        metadata_only: bool = False,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        return hub.changes(bearer(authorization), cursor, limit, metadata_only=metadata_only)

    @app.post("/native/v1/search")
    def search(
        data: SearchInput, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        return {"items": hub.search(bearer(authorization), **data.model_dump())}

    @app.post("/native/v1/retrieve")
    def retrieve(
        data: RetrieveInput, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        from .retrieval_view import bounded_retrieval

        return bounded_retrieval(hub.retrieve(bearer(authorization), **data.model_dump()))

    @app.post("/native/v1/recall")
    def recall(
        data: RecallInput, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """Hook channel: hybrid ranking with full bodies for a bounded number of hits."""
        from .retrieval_view import _diagnostics

        result = hub.recall(
            bearer(authorization),
            data.query,
            limit=data.limit,
            asking=data.asking.model_dump() if data.asking else None,
        )
        return {
            "items": recall_items(result["items"]),
            "hints": recall_hints(result.get("hints", [])),
            "trace_id": result["trace_id"],
            "retrieval": _diagnostics({"retrieval": result["retrieval"]}),
        }

    @app.post("/native/v1/recall/feedback")
    def recall_feedback(
        data: RecallFeedbackInput, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        return hub.recall_feedback(
            bearer(authorization), data.trace_id, [e.model_dump() for e in data.events]
        )

    @app.post("/native/v1/recall/gold")
    def recall_gold(
        data: GoldInput, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """A member adds gold questions for the organization's recall calibration."""
        return hub.recall_gold(
            bearer(authorization),
            [c.model_dump(exclude_none=True) for c in data.cases],
            replace=data.replace,
        )

    @app.get("/native/v1/recall/autotune")
    def recall_autotune(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        return hub.recall_autotune_status(bearer(authorization))

    @app.post("/native/v1/recall/autotune")
    def recall_autotune_start(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        return hub.recall_autotune_start(bearer(authorization))

    @app.get("/native/v1/recalls")
    def recalls(
        trace_id: str | None = Query(
            default=None, min_length=24, max_length=24, pattern=r"^[a-f0-9]+$"
        ),
        limit: int = Query(default=20, ge=1, le=100),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        return hub.recalls(bearer(authorization), trace_id=trace_id, limit=limit)

    @app.get("/native/v1/admin/recalls")
    def admin_recalls_page() -> HTMLResponse:
        """Recall ledger page; the page holds no credential and no memory text."""
        from . import recalls_page

        return HTMLResponse(recalls_page.html())

    @app.get("/native/v1/admin/recalls/data")
    def admin_recalls(
        request: Request,
        trace_id: str | None = Query(
            default=None, min_length=24, max_length=24, pattern=r"^[a-f0-9]+$"
        ),
        limit: int = Query(default=50, ge=1, le=100),
        member: str | None = Query(default=None, max_length=128, pattern=r"^[\w.:-]+$"),
        channel: Literal["hook", "tool", "cli", "benchmark"] | None = Query(default=None),
        day: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if day is not None:
            try:
                insights.day_bounds(day)
            except ValueError:
                raise HTTPException(422, "day must be a calendar date")
        return hub.admin_recalls(
            operator(request, authorization),
            trace_id=trace_id,
            limit=limit,
            member=member,
            channel=channel,
            day=day,
        )

    @app.get("/native/v1/admin/insights")
    def admin_insights_page() -> HTMLResponse:
        """Insight page; the page holds no credential and no memory text."""
        from . import insights_page

        return HTMLResponse(insights_page.html())

    @app.get("/native/v1/admin/insights/data")
    def admin_insights(
        request: Request,
        days: int = Query(default=30, ge=1, le=365),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Recall usage rolled up by day; counts, identifiers and titles only."""
        return hub.admin_insights(operator(request, authorization), days=days)

    @app.post("/native/v1/read")
    def read(data: ReadInput, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        return knowledge.read(bearer(authorization), **data.model_dump())

    @app.post("/native/v1/related")
    def related(
        data: RelatedInput, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        return knowledge.related(bearer(authorization), **data.model_dump())

    app.add_middleware(BodyLimit)
    # Transcript body/index payloads are repetitive. Negotiate standard gzip so
    # the network does not erase the gains from bounded candidate verification.
    from starlette.middleware.gzip import GZipMiddleware

    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=4)
    return app


# What recall.pack, coverage_key, the hook's provenance allowlist (evidence.FIELDS)
# and lineage.covered_records read. Ranking internals (score, retrieval_ranks,
# duplicate_sources, source_meta, hopfield_match, observed_at, content_hash)
# and the index_text of non-transcripts never leave the Hub on this channel.
RECALL_ITEM_FIELDS = (
    "asset_id",
    "revision",
    "tenant_id",
    "source_device_id",
    "source_store",
    "product",
    "project",
    "kind",
    "path",
    "title",
    "role",
    "body_hash",
    "document_id",
    "document_hash",
    "part_index",
    "part_count",
    "deleted",
    "producer_unknown",
    "producer_member_id",
    "producer_name",
    "uploader_member_id",
    "uploader_name",
    "upstream",
    "verified",
    "observed_at",
)
RECALL_MATCH_FIELDS = (
    "similarity",
    "source_field",
    "char_start",
    "char_end",
    "offset_unit",
    "aggregation",
)
RECALL_TRACE_FIELDS = (
    "trace_id",
    "routes",
    "final_rank",
    "nature",
    "decay_weight",
    "state",
    "relevance",
    "usage",
)
RECALL_BODY_BYTES = 64 * 1024
RECALL_TOTAL_BYTES = 512 * 1024


def recall_hints(hints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Title-only recall tier: names and ids, never bodies."""
    out = []
    for hint in hints[:8]:
        row: dict[str, Any] = {
            key: str(hint.get(key) or "")[:256] for key in ("asset_id", "title", "path", "product", "kind")
        }
        if not row["asset_id"] or type(hint.get("revision")) is not int:
            continue
        row["revision"] = hint["revision"]
        probability = hint.get("probability")
        if isinstance(probability, (int, float)):
            row["probability"] = round(float(probability), 4)
        out.append(row)
    return out


def recall_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project hook-channel records to the fields the hook uses, with bounded text.

    The hook packs at most 4KB into the prompt, while the client refuses any
    response over 8MB; a handful of large memories therefore meant that nothing
    was injected at all. Bodies are cut at a UTF-8 character boundary to 64KB
    each and 512KB in total and flagged ``body_truncated``. body_hash still
    names the full source version, which is what coverage keys identify.
    """
    remaining = RECALL_TOTAL_BYTES
    projected: list[dict[str, Any]] = []
    for item in items:
        out = {key: item[key] for key in RECALL_ITEM_FIELDS if key in item}
        for group, fields in (
            ("semantic_match", RECALL_MATCH_FIELDS),
            ("recall", RECALL_TRACE_FIELDS),
        ):
            block = item.get(group)
            if isinstance(block, dict):
                out[group] = {key: block[key] for key in fields if key in block}
        texts = ["body"] + (["index_text"] if item.get("kind") == "transcript" else [])
        for field in texts:
            text = item.get(field)
            if not isinstance(text, str):
                continue
            encoded = text.encode()
            budget = max(0, min(RECALL_BODY_BYTES, remaining))
            if len(encoded) > budget:
                text = encoded[:budget].decode("utf8", errors="ignore")
                out[field + "_truncated"] = True
            out[field] = text
            remaining -= len(text.encode())
        projected.append(out)
    return projected


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
