"""P2-11: web-admin runtime state (audit/webhooks/snapshots/links) must persist.

Before the fix these lived in module-level Python globals, so they were lost on
restart and not shared across workers. These tests prove the four kinds of state
survive a simulated restart (dropping every in-memory cache and reopening the
brain dir) and land on disk in <brain>/web_state.db.
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
def seed_items(brain_dir: Path):
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(items_dir=brain_dir / "items")
    items = []
    for i, (typ, title) in enumerate([("fact", "First fact"), ("decision", "A decision")]):
        item = MemoryItem(
            id=f"mem-20260101-00000{i}-seed-{typ}",
            type=MemoryType(typ),
            title=title,
            summary=f"Summary of {title}",
            tags=["seed"],
            created_at=datetime.now(timezone.utc),
        )
        store.write(item, f"Body for {title}")
        items.append(item)
    return items


@pytest.fixture()
def client(brain_dir: Path):
    from web.app import app

    return TestClient(app)


@pytest.fixture()
def admin_token(client: TestClient):
    resp = client.post("/api/auth/init", json={"username": "admin", "password": "test123"})
    assert resp.status_code == 200
    return resp.json()["token"]


def test_web_state_storage_helper_initializes_schema(tmp_path: Path):
    from web.state_storage import open_web_state_connection

    db_path = tmp_path / "state" / "web_state.db"
    connection = open_web_state_connection(db_path)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        connection.close()

    assert {"audit_log", "webhooks", "item_snapshots", "item_links"} <= tables
    assert db_path.exists()


def test_webhook_state_helpers_are_split_and_reexported():
    from web import state_store
    from web.state_webhooks import add_webhook, list_webhooks, remove_webhook

    assert state_store.add_webhook is add_webhook
    assert state_store.list_webhooks is list_webhooks
    assert state_store.remove_webhook is remove_webhook


def test_link_state_helpers_are_split_and_reexported():
    from web import state_store
    from web.state_links import add_link, link_exists, links_for, remove_link

    assert state_store.add_link is add_link
    assert state_store.link_exists is link_exists
    assert state_store.links_for is links_for
    assert state_store.remove_link is remove_link


def _simulate_restart():
    """Drop every in-memory cache so the next request must reload from disk."""
    import web.app as appmod
    import web.state_store as st

    for store in st._state_cache.values():
        store.connection.close()
    st._state_cache.clear()
    appmod._components_cache.clear()


def test_web_state_persists_across_restart(client, admin_token, seed_items, brain_dir):
    headers = {"Authorization": f"Bearer {admin_token}"}

    # webhook
    assert client.post(
        "/api/webhooks", headers=headers, json={"url": "https://example.com/persist"}
    ).status_code == 200
    # audit entry (create action)
    assert client.post(
        "/api/items", headers=headers,
        json={"type": "fact", "title": "audit me", "summary": "s"},
    ).status_code == 200
    # version snapshot (patch keeps the pre-edit snapshot)
    item_id = seed_items[0].id
    assert client.patch(
        f"/api/items/{item_id}", headers=headers, json={"title": "Edited"}
    ).status_code == 200
    # manual link
    assert client.post(
        "/api/links", headers=headers,
        json={"source_id": seed_items[0].id, "target_id": seed_items[1].id},
    ).status_code == 200

    # On-disk db must exist (proves persistence, not just RAM).
    assert (brain_dir / "web_state.db").exists()

    _simulate_restart()

    # webhook survived
    webhooks = client.get("/api/webhooks", headers=headers).json()["webhooks"]
    assert any(w["url"] == "https://example.com/persist" for w in webhooks)

    # audit survived
    entries = client.get("/api/audit", headers=headers).json()["entries"]
    assert any(e["action"] == "create" for e in entries)

    # snapshot survived (and still holds the original pre-edit title)
    hist = client.get(f"/api/items/{item_id}/history", headers=headers).json()
    assert hist["count"] >= 1
    assert any(s["title"] == "First fact" for s in hist["snapshots"])

    # link survived
    links = client.get(f"/api/links/{seed_items[0].id}", headers=headers).json()
    assert links["count"] >= 1


def test_webhook_remove_persists(client, admin_token, brain_dir):
    headers = {"Authorization": f"Bearer {admin_token}"}
    client.post("/api/webhooks", headers=headers, json={"url": "https://example.com/a"})
    client.post("/api/webhooks", headers=headers, json={"url": "https://example.com/b"})
    resp = client.delete("/api/webhooks?url=https://example.com/a", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["removed"] == 1

    _simulate_restart()
    urls = [w["url"] for w in client.get("/api/webhooks", headers=headers).json()["webhooks"]]
    assert "https://example.com/a" not in urls
    assert "https://example.com/b" in urls
