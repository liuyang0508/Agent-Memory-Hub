"""Restored capability: /api/graph derives edges from [[wiki-link]] body tokens
(in addition to explicit links + refs.mems), resolving by id or exact title."""
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from web.auth import CurrentUser


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    (tmp_path / "items").mkdir()
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path))
    monkeypatch.setenv("MEMORY_HUB_RATE_LIMIT", "0")
    monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")
    import web.app as appmod
    appmod._components_cache.clear()
    store = ItemsStore(items_dir=tmp_path / "items")
    target = MemoryItem(id="mem-20260101-000000-target", type=MemoryType.fact,
                        created_at=datetime.now(timezone.utc), title="Vector Index Design", summary="s")
    src = MemoryItem(id="mem-20260101-000001-source", type=MemoryType.decision,
                     created_at=datetime.now(timezone.utc), title="Use sqlite-vec", summary="s")
    store.write(target, "the target note")
    store.write(src, "We follow [[Vector Index Design]] for this decision.")  # wiki-link by title
    c = TestClient(appmod.app)
    c.post("/api/auth/init", json={"username": "admin", "password": "pw"})
    return c


def test_graph_has_wikilink_edge(client):
    tok = client.post("/api/auth/login", json={"username": "admin", "password": "pw"}).json()["token"]
    r = client.get("/api/graph", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    edges = r.json()["edges"]
    assert any(e["source"] == "mem-20260101-000001-source" and e["target"] == "mem-20260101-000000-target"
               for e in edges), f"wiki-link edge not derived; edges={edges}"


def test_full_graph_payload_builder_is_split():
    from web.graph_payload import build_full_graph_payload

    target = MemoryItem(
        id="mem-20260101-000000-target",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Vector Index Design",
        summary="s",
    )
    source = MemoryItem(
        id="mem-20260101-000001-source",
        type=MemoryType.decision,
        created_at=datetime.now(timezone.utc),
        title="Use sqlite-vec",
        summary="s",
    )

    class Store:
        def iter_all(self):
            return iter([
                (target, "the target note"),
                (source, "We follow [[Vector Index Design]] for this decision."),
            ])

    class Index:
        def get_refs(self, _item_id):
            return []

    user = CurrentUser(username="admin", tenant_id=None, role="admin")

    result = build_full_graph_payload(Store(), Index(), user=user, debug=True)

    assert len(result["nodes"]) == 2
    assert {
        "source": "mem-20260101-000001-source",
        "target": "mem-20260101-000000-target",
        "label": "wiki",
    } in result["edges"]
    assert result["_debug"]["wiki_resolved"] == 1
