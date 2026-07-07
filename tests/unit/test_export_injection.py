"""P2-6: web exports must not enable CSV formula injection or corrupt YAML.

CSV: a cell starting with =,+,-,@,tab,CR is a spreadsheet formula vector
(CWE-1236) — guard by prefixing a single quote. Markdown ZIP export: build
frontmatter via yaml.safe_dump so titles/summaries with newlines, colons, or
leading [/{ don't corrupt the YAML.
"""
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_brain.contracts.memory_item import MemoryItem, MemoryType


@pytest.fixture()
def brain(tmp_path, monkeypatch):
    (tmp_path / "items").mkdir()
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path))
    monkeypatch.setenv("MEMORY_HUB_RATE_LIMIT", "0")
    import web.app as appmod
    appmod._components_cache.clear()
    return tmp_path


@pytest.fixture()
def client(brain):
    from web.app import app
    return TestClient(app)


@pytest.fixture()
def token(client):
    return client.post("/api/auth/init", json={"username": "admin", "password": "pw"}).json()["token"]


def _seed(brain: Path, title: str):
    from agent_brain.memory.store.items_store import ItemsStore
    store = ItemsStore(items_dir=brain / "items")
    item = MemoryItem(
        id="mem-20260101-000000-inj",
        type=MemoryType.fact,
        title=title,
        summary="line1\nline2: colon",
        created_at=datetime.now(timezone.utc),
        tags=["t1"],
    )
    store.write(item, "body")


def test_csv_export_neutralizes_formula(client, token, brain):
    _seed(brain, "=cmd|'/calc'!A1")
    h = {"Authorization": f"Bearer {token}"}
    r = client.get("/api/export/csv", headers=h)
    assert r.status_code == 200
    # The dangerous title must be quoted, not start a raw formula cell.
    assert "'=cmd" in r.text or "\"'=cmd" in r.text


def test_md_zip_export_yaml_is_valid(client, token, brain):
    import io, zipfile, yaml
    _seed(brain, "title: with colon")
    h = {"Authorization": f"Bearer {token}"}
    r = client.get("/api/export/markdown", headers=h)
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    md = zf.read(zf.namelist()[0]).decode()
    fm = md.split("---\n", 2)[1]
    parsed = yaml.safe_load(fm)  # must not raise
    assert parsed["title"] == "title: with colon"
