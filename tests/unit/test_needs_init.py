"""Restored capability: GET /api/auth/needs-init first-use detection."""
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    (tmp_path / "items").mkdir()
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path))
    monkeypatch.setenv("MEMORY_HUB_RATE_LIMIT", "0")
    import web.app as appmod
    appmod._components_cache.clear()
    return TestClient(appmod.app)


def test_needs_init_true_on_fresh_install(client):
    r = client.get("/api/auth/needs-init")
    assert r.status_code == 200
    assert r.json()["needs_init"] is True


def test_needs_init_false_after_admin_created(client):
    client.post("/api/auth/init", json={"username": "admin", "password": "pw"})
    r = client.get("/api/auth/needs-init")
    assert r.status_code == 200
    assert r.json()["needs_init"] is False
