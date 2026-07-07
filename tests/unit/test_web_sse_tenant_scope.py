"""P2-12: SSE (/api/events) and WebSocket (/ws/events) streams must not leak
cross-tenant item metadata.

Before the fix, _broadcast_event fanned every event out to every subscriber
regardless of tenant, so a tenant-B subscriber received item_created /
item_updated / item_deleted events describing tenant-A items. The fix scopes
subscribers by tenant and filters _broadcast_event per recipient.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def brain_dir(tmp_path: Path):
    (tmp_path / "items").mkdir()
    os.environ["BRAIN_DIR"] = str(tmp_path)
    os.environ["MEMORY_HUB_RATE_LIMIT"] = "0"
    yield tmp_path
    os.environ.pop("BRAIN_DIR", None)
    os.environ.pop("MEMORY_HUB_RATE_LIMIT", None)


@pytest.fixture()
def client(brain_dir: Path):
    from web.app import app

    return TestClient(app)


def test_broadcast_event_scopes_sse_subscribers_by_tenant():
    """Deterministic, loop-free check of the per-recipient filter.

    Fails before the fix: _broadcast_event has no tenant_id kwarg and
    _sse_subscribers holds bare queues, so this raises TypeError / ValueError.
    """
    from web import app as appmod

    saved = list(appmod._sse_subscribers)
    appmod._sse_subscribers.clear()
    try:
        q_a: asyncio.Queue = asyncio.Queue(maxsize=8)
        q_b: asyncio.Queue = asyncio.Queue(maxsize=8)
        q_admin: asyncio.Queue = asyncio.Queue(maxsize=8)
        appmod._sse_subscribers.append((q_a, "tenant-a", False))
        appmod._sse_subscribers.append((q_b, "tenant-b", False))
        appmod._sse_subscribers.append((q_admin, "ops", True))

        # A tenant-A item event.
        appmod._broadcast_event(
            "item_created", {"id": "x", "title": "tenant-a secret"}, tenant_id="tenant-a"
        )
        assert q_a.qsize() == 1            # owner sees it
        assert q_b.qsize() == 0            # cross-tenant: MUST NOT leak
        assert q_admin.qsize() == 1        # admin sees everything

        # An untenanted (globally-visible) item event reaches everyone.
        appmod._broadcast_event("item_created", {"id": "g"}, tenant_id=None)
        assert q_a.qsize() == 2
        assert q_b.qsize() == 1
        assert q_admin.qsize() == 2

        # An admin-only event never reaches non-admins.
        appmod._broadcast_event("tag_renamed", {"old": "a", "new": "b"}, admin_only=True)
        assert q_a.qsize() == 2
        assert q_b.qsize() == 1
        assert q_admin.qsize() == 3
    finally:
        appmod._sse_subscribers.clear()
        appmod._sse_subscribers.extend(saved)


def _bootstrap_two_tenants(client: TestClient):
    admin = client.post("/api/auth/init", json={"username": "admin", "password": "pw"})
    ah = {"Authorization": f"Bearer {admin.json()['token']}"}
    client.post(
        "/api/auth/register",
        json={"username": "alice", "password": "pw", "tenant_id": "tenant-a"},
        headers=ah,
    )
    client.post(
        "/api/auth/register",
        json={"username": "bob", "password": "pw", "tenant_id": "tenant-b"},
        headers=ah,
    )
    alice = client.post("/api/auth/login", json={"username": "alice", "password": "pw"}).json()["token"]
    bob = client.post("/api/auth/login", json={"username": "bob", "password": "pw"}).json()["token"]
    return alice, bob


def test_ws_stream_does_not_leak_cross_tenant_events(client: TestClient):
    """End-to-end: a tenant-B WS subscriber must not receive tenant-A events.

    We emit a tenant-A event first, then a tenant-B event. Bob (tenant-B) must
    receive only his own. Before the fix he would receive alice's first and the
    title assertion below would fail.
    """
    alice, bob = _bootstrap_two_tenants(client)

    with client.websocket_connect(f"/ws/events?token={bob}") as ws:
        assert ws.receive_json()["event"] == "connected"

        # tenant-A creates an item -> item_created scoped to tenant-a (bob must NOT see).
        r_a = client.post(
            "/api/items",
            json={"type": "fact", "title": "tenant-a-secret", "summary": "s"},
            headers={"Authorization": f"Bearer {alice}"},
        )
        assert r_a.status_code == 200

        # tenant-B creates an item -> item_created scoped to tenant-b (bob MUST see).
        r_b = client.post(
            "/api/items",
            json={"type": "fact", "title": "tenant-b-own", "summary": "s"},
            headers={"Authorization": f"Bearer {bob}"},
        )
        assert r_b.status_code == 200

        msg = ws.receive_json()
        assert msg["event"] == "item_created"
        assert msg["data"]["title"] == "tenant-b-own"  # never the tenant-A title
