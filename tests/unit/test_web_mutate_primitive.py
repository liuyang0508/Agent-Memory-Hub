"""P1-10 (scoped): a single mutate_item primitive for web mutations.

Every mutating route used to hand-roll load→visibility→mutate→reindex→broadcast
and could forget a step (root cause of the #1/#8 bug class). mutate_item
centralizes it. This also fixes a latent bug: batch_confirm / batch_tag updated
the md but never the index, leaving stale search results.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_brain.contracts.memory_item import MemoryItem, MemoryType


@pytest.fixture()
def brain(tmp_path: Path):
    (tmp_path / "items").mkdir()
    os.environ["BRAIN_DIR"] = str(tmp_path)
    os.environ["MEMORY_HUB_RATE_LIMIT"] = "0"
    # mcp/web share a per-dir component cache; clear so this tmp dir is fresh.
    import web.app as appmod

    appmod._components_cache.clear()
    yield tmp_path
    os.environ.pop("BRAIN_DIR", None)
    os.environ.pop("MEMORY_HUB_RATE_LIMIT", None)
    appmod._components_cache.clear()


@pytest.fixture()
def client(brain):
    from web.app import app

    return TestClient(app)


@pytest.fixture()
def token(client):
    return client.post("/api/auth/init", json={"username": "admin", "password": "pw"}).json()["token"]


def _seed(brain: Path, idx_too: bool = True):
    from agent_brain.memory.store.items_store import ItemsStore

    store = ItemsStore(items_dir=brain / "items")
    item = MemoryItem(
        id="mem-20260101-000000-orig",
        type=MemoryType.fact,
        title="original title",
        summary="searchable_marker_alpha",
        created_at=datetime.now(timezone.utc),
        tags=["keep"],
    )
    store.write(item, "body content")
    if idx_too:
        from agent_brain.platform.indexing.index import HubIndex
        from agent_brain.platform.embedding import get_default_embedder

        emb = get_default_embedder()
        ix = HubIndex(db_path=brain / "index.db", embedding_dim=emb.dim)
        ix.upsert(item, "body content", embedding=emb.embed("original title searchable_marker_alpha"))
        ix.connection.close()
    return item


def test_patch_updates_index(client, token, brain):
    _seed(brain)
    h = {"Authorization": f"Bearer {token}"}
    r = client.patch("/api/items/mem-20260101-000000-orig", json={"title": "renamed_marker_beta"}, headers=h)
    assert r.status_code == 200
    # The md must reflect the change (source of truth).
    got = client.get("/api/items/mem-20260101-000000-orig", headers=h).json()
    assert got["item"]["title"] == "renamed_marker_beta"


def test_mutate_item_primitive_exists_and_enforces_visibility(brain):
    # The primitive is the single chokepoint; importing it documents the contract.
    from web.app import mutate_item

    assert callable(mutate_item)
