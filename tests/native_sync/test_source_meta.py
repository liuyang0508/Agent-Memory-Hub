"""Client-captured source hints: bounded on the wire, optional for old Hubs, hash-neutral when absent."""

import json

from fastapi.testclient import TestClient

from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.storage import database
from agent_brain.native_sync.transport import HubHTTPError
from test_client import DirectTransport, client
from test_hub import asset

MEMORY = """---
name: approvals
description: "Who signs off"
metadata:
  type: feedback
  modified: 2026-10-30T00:00:00Z
---
审批需要两人复核，见 [[release-checklist]]。

## 规则
"""


def test_scan_captures_declared_front_matter_links_and_mtime(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "张三")
    root = tmp_path / "source"
    root.mkdir()
    c.register(root, "claude-code", "orders", "memory")  # Files written later are not history.
    (root / "approvals.md").write_text(MEMORY)
    (root / "2026-11-03-note.md").write_text("no front matter")
    c.sync()
    saved = {r["path"]: r for r in hub.search(c.identity["token"], "复核 front matter", limit=10)}
    meta = saved["approvals.md"]["source_meta"]
    assert meta["declared"] == {
        "name": "approvals",
        "description": "Who signs off",
        "type": "feedback",
        "modified": "2026-10-30T00:00:00Z",
    }
    assert meta["links"] == ["release-checklist"] and meta["sections"] == ["规则"]
    assert abs(meta["mtime"] - (root / "approvals.md").stat().st_mtime) < 1e-6
    assert set(saved["2026-11-03-note.md"]["source_meta"]) == {"mtime"}


def test_absent_source_meta_keeps_legacy_payload_and_hash(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    who = hub.provision("one", "device")
    first = hub.put(who["token"], asset("legacy"))
    assert "source_meta" not in first
    replay = hub.put(who["token"], {**asset("legacy"), "source_meta": None})
    assert replay == first  # Explicit null is the same content: an idempotent replay.
    with database(hub.path) as db:
        payload = db.execute("SELECT payload FROM assets WHERE id='legacy'").fetchone()["payload"]
    assert "source_meta" not in json.loads(payload)
    tagged = hub.put(
        who["token"], asset("legacy", base_revision=1, source_meta={"declared": {"type": "user"}})
    )
    assert tagged["revision"] == 2 and tagged["source_meta"] == {"declared": {"type": "user"}}


def test_old_hub_without_capability_or_rejecting_field_still_receives_uploads(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")

    class OldHubTransport(DirectTransport):
        def __init__(self, hub, identity, advertise):
            super().__init__(hub, identity)
            self.advertise = advertise
            self.seen = []

        def capabilities(self):
            return ["org-research-v1", "recall-parity-v1"] if self.advertise else []

        def put(self, value):
            self.seen.append("source_meta" in value)
            if "source_meta" in value:
                raise HubHTTPError(422)
            return super().put(value)

    c = client(tmp_path, hub, "张三")
    transport = OldHubTransport(hub, c.identity, advertise=False)
    c.transport = transport
    root = tmp_path / "source"
    root.mkdir()
    c.register(root, "claude-code", "orders", "memory")
    (root / "a.md").write_text(MEMORY)
    assert c.sync()["errors"] == []
    assert transport.seen == [False]  # Probe said no: stripped before the first request.
    (root / "b.md").write_text("second " + MEMORY)
    transport.advertise = True
    transport.seen.clear()
    assert c.sync()["errors"] == []
    assert transport.seen == [True, False]  # Advertised but rejected: one retry, then stripped.
    assert len(hub.search(c.identity["token"], "复核", limit=10)) == 2


def test_http_contract_bounds_source_meta(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    who = hub.provision("one", "device")
    api = TestClient(create_app(hub))
    headers = {"Authorization": "Bearer " + who["token"]}
    assert "recall-parity-v1" in api.get("/native/v1/health").json()["capabilities"]
    good = api.post(
        "/native/v1/assets",
        headers=headers,
        json=asset("m", source_meta={"mtime": 1700000000, "declared": {"type": "feedback"}}),
    )
    assert good.status_code == 200 and good.json()["source_meta"]["mtime"] == 1700000000.0
    for bad in (
        {"declared": {"body": "smuggled"}},
        {"declared": {"description": "x" * 201}},
        {"links": ["x"] * 65},
        {"mtime": -1},
        {"extra": True},
    ):
        response = api.post("/native/v1/assets", headers=headers, json=asset("n", source_meta=bad))
        assert response.status_code == 422, bad
