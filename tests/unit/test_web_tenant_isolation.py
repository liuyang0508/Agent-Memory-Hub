"""P0-2 (critical): mutating routes must enforce tenant visibility.

A non-admin in tenant B could DELETE/PATCH a guessable tenant-A item id —
a cross-tenant tamper/delete. The 5 mutating routes now call _require_visible.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_brain.contracts.memory_item import MemoryItem, MemoryType


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


TENANT_A_ID = "mem-20260101-000000-tenant-a-secret"


@pytest.fixture()
def tenant_a_item(brain_dir: Path):
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(items_dir=brain_dir / "items")
    item = MemoryItem(
        id=TENANT_A_ID,
        type=MemoryType.fact,
        title="tenant A secret",
        summary="should be invisible to tenant B",
        tenant_id="tenant-a",
        created_at=datetime.now(timezone.utc),
    )
    store.write(item, "secret body")
    return item


@pytest.fixture()
def bob_token(client: TestClient):
    admin = client.post("/api/auth/init", json={"username": "admin", "password": "pw"})
    admin_token = admin.json()["token"]
    client.post(
        "/api/auth/register",
        json={"username": "bob", "password": "pw", "tenant_id": "tenant-b"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    login = client.post("/api/auth/login", json={"username": "bob", "password": "pw"})
    return login.json()["token"]


def test_cross_tenant_delete_forbidden(client, tenant_a_item, bob_token, brain_dir):
    h = {"Authorization": f"Bearer {bob_token}"}
    resp = client.delete(f"/api/items/{TENANT_A_ID}", headers=h)
    assert resp.status_code == 403
    assert (brain_dir / "items" / f"{TENANT_A_ID}.md").exists()


def test_visibility_helpers_are_split(tenant_a_item, brain_dir):
    from agent_brain.memory.store.items_store import ItemsStore
    from web.auth import CurrentUser
    from web.visibility import require_visible, visible

    user = CurrentUser(username="bob", tenant_id="tenant-b", role="user")

    assert visible(tenant_a_item, user) is False
    with pytest.raises(Exception) as exc_info:
        require_visible(ItemsStore(items_dir=brain_dir / "items"), TENANT_A_ID, user)
    assert getattr(exc_info.value, "status_code", None) == 403


def test_cross_tenant_patch_forbidden(client, tenant_a_item, bob_token):
    h = {"Authorization": f"Bearer {bob_token}"}
    resp = client.patch(f"/api/items/{TENANT_A_ID}", json={"title": "hijacked"}, headers=h)
    assert resp.status_code == 403


def test_traversal_item_id_rejected(client, bob_token):
    h = {"Authorization": f"Bearer {bob_token}"}
    resp = client.delete("/api/items/..%2F..%2Fetc", headers=h)
    assert resp.status_code in (400, 404)


def test_admin_can_delete(client, tenant_a_item, brain_dir):
    admin = client.post("/api/auth/init", json={"username": "admin", "password": "pw"})
    h = {"Authorization": f"Bearer {admin.json()['token']}"}
    resp = client.delete(f"/api/items/{TENANT_A_ID}", headers=h)
    assert resp.status_code == 200
    assert not (brain_dir / "items" / f"{TENANT_A_ID}.md").exists()
