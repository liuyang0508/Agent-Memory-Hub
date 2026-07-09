"""Lock the web Admin API surface BEFORE the app.py → routers refactor.

Captures the exact (method, path) set and the internal symbols the test-suite +
serve path reach for. The split into web/api/routes/* must keep this identical —
this guards against a dropped/duplicated route or a shadowed overlapping path
(e.g. /api/items/pinned vs /api/items/{item_id}) and against losing a re-exported
internal (_sse_subscribers, _broadcast_event, mutate_item, …).
"""

EXPECTED_ROUTES = {
    "DELETE /api/items/{item_id}",
    "DELETE /api/link",
    "DELETE /api/links",
    "DELETE /api/webhooks",
    "GET /api/activity",
    "GET /api/adapters/{name}/doctor",
    "GET /api/adapters/capabilities",
    "GET /api/adapters/onboarding",
    "GET /api/agents/local-history",
    "GET /api/agents/local-history/drafts",
    "GET /api/agents/{agent}/local-history/sources",
    "GET /api/audit",
    "GET /api/audit/outbound",
    "GET /api/auth/me",
    "GET /api/auth/needs-init",
    "GET /api/auth/users",
    "GET /api/backups",
    "GET /api/chain-logs",
    "GET /api/chain-logs/{chain_id}",
    "GET /api/cockpit/summary",
    "GET /api/data-flow",
    "GET /api/decay-status",
    "GET /api/events",
    "GET /api/export",
    "GET /api/export/csv",
    "GET /api/export/markdown",
    "GET /api/graph",
    "GET /api/graph/{item_id}",
    "GET /api/governance/lifecycle-review",
    "GET /api/health",
    "GET /api/health-detail",
    "GET /api/headroom/status",
    "GET /api/headroom/retrieve/{key}",
    "GET /api/hierarchical-memory",
    "GET /api/items",
    "GET /api/items/pinned",
    "GET /api/items/{item_id}",
    "GET /api/items/{item_id}/history",
    "GET /api/items/{item_id}/history/{index}",
    "GET /api/items/{item_id}/related",
    "GET /api/links/{item_id}",
    "GET /api/memory-candidates",
    "GET /api/memory-lineage",
    "GET /api/projects",
    "GET /api/routes",
    "GET /api/search",
    "GET /api/search/fulltext",
    "GET /api/stats",
    "GET /api/tags",
    "GET /api/version",
    "GET /api/webhooks",
    "PATCH /api/items/{item_id}",
    "PATCH /api/agents/local-history/drafts/{draft_id}",
    "POST /api/audit/scan",
    "POST /api/auth/init",
    "POST /api/auth/login",
    "POST /api/auth/register",
    "POST /api/auth/rotate-key",
    "POST /api/adapters/{name}/install",
    "POST /api/adapters/{name}/install-verify",
    "POST /api/adapters/{name}/uninstall",
    "POST /api/adapters/{name}/verify",
    "POST /api/agents/local-history/drafts/{draft_id}/apply",
    "POST /api/agents/local-history/drafts/{draft_id}/skip",
    "POST /api/agents/local-history/scan",
    "POST /api/agents/{agent}/local-history/sync",
    "POST /api/backup",
    "POST /api/backups/{backup_name}/restore",
    "POST /api/compression-gate",
    "POST /api/evolve",
    "POST /api/gc",
    "POST /api/governance/lifecycle-apply",
    "POST /api/import",
    "POST /api/headroom/compress",
    "POST /api/hierarchical-memory/build",
    "POST /api/items",
    "POST /api/items/batch-confirm",
    "POST /api/items/batch-delete",
    "POST /api/items/batch-tag",
    "POST /api/items/batch-update",
    "POST /api/items/merge",
    "POST /api/items/{item_id}/clone",
    "POST /api/items/{item_id}/pin",
    "POST /api/items/{item_id}/touch",
    "POST /api/link",
    "POST /api/links",
    "POST /api/memory-candidates/generate",
    "POST /api/memory-candidates/generate-semantic",
    "POST /api/memory-candidates/{candidate_id}/approve",
    "POST /api/memory-candidates/{candidate_id}/reject",
    "POST /api/memory-profiles/export",
    "POST /api/ml-advisory-gate",
    "POST /api/obsidian/export",
    "POST /api/obsidian/import",
    "POST /api/retrieval-gate",
    "POST /api/reindex",
    "POST /api/tags/delete",
    "POST /api/tags/rename",
    "POST /api/webhooks",
    "PUT /api/items/{item_id}/body",
    "WS /ws/events",
}

# Internals the test-suite or serve path reach for via `web.app`.
INTERNAL_SYMBOLS = [
    "app", "serve", "mutate_item", "_broadcast_event", "rate_limit_middleware",
    "_sse_subscribers", "_ws_subscribers", "_rate_limit_store", "_components_cache",
    "_RATE_LIMIT_WINDOW", "Response",
]


def _routes() -> set[str]:
    import web.app as a

    out: set[str] = set()
    for r in _iter_routes(a.app):
        path = getattr(r, "path", None)
        if not path or not (path.startswith("/api") or path.startswith("/ws")):
            continue
        methods = getattr(r, "methods", None) or ["WS"]
        for m in methods:
            if m in ("HEAD", "OPTIONS"):
                continue
            out.add(f"{m} {path}")
    return out


def _iter_routes(router):
    for route in router.routes:
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            yield from _iter_routes(original_router)
            continue
        yield route


def test_web_route_surface_is_stable(tmp_brain):
    assert _routes() == EXPECTED_ROUTES


def test_web_internal_symbols_present(tmp_brain):
    import web.app as a
    for s in INTERNAL_SYMBOLS:
        assert hasattr(a, s), f"missing web.app.{s}"


def test_web_governance_audit_webhook_activity_routes_are_split_and_mounted():
    from web.api.routes import (
        governance,
        governance_activity,
        governance_audit,
        governance_webhooks,
    )

    activity_paths = {route.path for route in _iter_routes(governance_activity.router)}
    audit_paths = {route.path for route in _iter_routes(governance_audit.router)}
    webhook_paths = {route.path for route in _iter_routes(governance_webhooks.router)}
    mounted_paths = {route.path for route in _iter_routes(governance.router)}

    assert {"/api/activity"}.issubset(activity_paths)
    assert {
        "/api/audit",
        "/api/audit/scan",
        "/api/audit/outbound",
    }.issubset(audit_paths)
    assert {"/api/webhooks"}.issubset(webhook_paths)
    assert activity_paths.issubset(mounted_paths)
    assert audit_paths.issubset(mounted_paths)
    assert webhook_paths.issubset(mounted_paths)


def test_web_live_event_helpers_are_split_and_reexported(tmp_brain):
    from web import _base
    from web.live_events import (
        _event_visible_to,
        _sse_subscribers,
        _ws_subscribers,
    )

    assert _base._event_visible_to is _event_visible_to
    assert _base._sse_subscribers is _sse_subscribers
    assert _base._ws_subscribers is _ws_subscribers
