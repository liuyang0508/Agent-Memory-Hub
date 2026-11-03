"""Client-captured source hints: bounded on the wire, optional for old Hubs, hash-neutral when absent."""

import json

import pytest
from fastapi.testclient import TestClient

from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.common import digest
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.models import AssetInput
from agent_brain.native_sync.storage import database
from agent_brain.native_sync.transport import HubHTTPError
from test_client import DirectTransport, client
from test_hub import asset
from test_hybrid_retrieval import SyntheticBackend

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


def hash_of(value, *, keep_meta):
    """``keep_meta=False`` is the v1.4.1 formula: the validated payload minus
    base_revision, with no source_meta field at all. ``keep_meta=True`` is the
    same digest over a payload whose source_meta is passed through as given."""
    item = AssetInput.model_validate(value).model_dump()
    if not keep_meta or not item.get("source_meta"):
        item.pop("source_meta", None)
    return digest(
        json.dumps(
            {k: v for k, v in item.items() if k != "base_revision"},
            sort_keys=True,
            ensure_ascii=False,
        )
    )


def test_content_hash_matches_v141_without_source_meta_and_ignores_mtime(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    who = hub.provision("one", "device")
    plain = hub.put(who["token"], asset("plain"))
    assert plain["content_hash"] == hash_of(asset("plain"), keep_meta=False)
    # mtime rides outside the hash: an mtime-only source_meta hashes like no meta at all.
    stamped = hub.put(who["token"], asset("stamped", source_meta={"mtime": 1700000000}))
    assert stamped["content_hash"] == hash_of(asset("stamped"), keep_meta=False)
    assert stamped["source_meta"] == {"mtime": 1700000000.0}
    # Declared front matter, links and sections still change the meaning, so the hash:
    # it is the digest over the payload with source_meta minus its mtime.
    hints = {"declared": {"type": "feedback"}, "links": ["x"]}
    declared = hub.put(who["token"], asset("declared", source_meta={"mtime": 1700000000, **hints}))
    assert declared["content_hash"] != hash_of(asset("declared"), keep_meta=False)
    assert declared["content_hash"] == hash_of(asset("declared", source_meta=hints), keep_meta=True)
    assert declared["source_meta"] == {"mtime": 1700000000.0, **hints}
    with pytest.raises(ValueError, match="revision conflict"):
        hub.put(who["token"], asset("declared", source_meta={"declared": {"type": "user"}}))


def test_mtime_only_change_replays_in_place_without_revision_or_index_rebuild(tmp_path):
    backend = SyntheticBackend()
    hub = Hub(tmp_path / "hub.sqlite", semantic_backend=backend)
    who = hub.provision("one", "device")
    meta = {"mtime": 1700000000, "declared": {"type": "feedback"}, "sections": ["规则"]}
    first = hub.put(who["token"], asset("m", body="审批需要两人复核", source_meta=meta))
    assert hub.rebuild_semantic(who["token"])["indexed"] == 1
    # A re-scan after reinstall: same file, new stat mtime, base_revision back at 0.
    replay = hub.put(
        who["token"],
        asset("m", body="审批需要两人复核", source_meta={**meta, "mtime": 1700009999}),
    )
    assert replay["revision"] == 1 and replay["content_hash"] == first["content_hash"]
    assert replay["source_meta"] == {**meta, "mtime": 1700009999.0}
    with database(hub.path) as db:
        stored = json.loads(
            db.execute("SELECT payload FROM assets WHERE id='m'").fetchone()["payload"]
        )
        changes = db.execute("SELECT count(*) FROM changes").fetchone()[0]
    assert stored["source_meta"]["mtime"] == 1700009999.0 and stored["revision"] == 1
    assert changes == 1  # No change event: replicas and peers have nothing new to replay.
    result = hub.retrieve(who["token"], "复核")
    assert "semantic_index_incomplete_rebuild_required" not in result["retrieval"]["reasons"]
    assert result["retrieval"]["semantic_index"] == {"current": 1, "eligible": 1}
    assert result["items"][0]["source_meta"]["mtime"] == 1700009999.0
    refreshed = hub.rebuild_semantic(who["token"])
    assert refreshed["reused"] == 1 and refreshed["encoded"] == 0
    # The same hints without an mtime (a store that cannot stat) replay the same
    # content and leave the stored mtime alone rather than erasing it.
    bare = hub.put(
        who["token"],
        asset(
            "m",
            body="审批需要两人复核",
            source_meta={"declared": {"type": "feedback"}, "sections": ["规则"]},
        ),
    )
    assert bare["revision"] == 1 and bare["source_meta"]["mtime"] == 1700009999.0
    # The same mtime replayed again is a plain idempotent replay with no write.
    again = hub.put(
        who["token"],
        asset("m", body="审批需要两人复核", source_meta={**meta, "mtime": 1700009999}),
    )
    assert again == replay
    # Dropping the declared hints is a content change and still needs the base revision.
    with pytest.raises(ValueError, match="revision conflict"):
        hub.put(who["token"], asset("m", body="审批需要两人复核"))
    assert (
        hub.put(who["token"], asset("m", body="审批需要两人复核", base_revision=1))["revision"] == 2
    )
