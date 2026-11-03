"""Upgrade paths for profiles and Hub records written by earlier releases."""

import json
import sqlite3

import pytest

from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.projection import cleanup, publish
from agent_brain.native_sync.storage import database
from test_client import client


def _hub_product(hub, asset_id):
    with sqlite3.connect(hub.path) as db:
        row = db.execute("SELECT payload FROM assets WHERE id=?", (asset_id,)).fetchone()
    return json.loads(row[0])["product"]


def test_codex_sources_keep_the_label_their_assets_were_created_with(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "codex-user")
    root = tmp_path / "codex-memories"
    root.mkdir()
    # An earlier client registered the shared Codex store under the old label.
    c.register(root, "codex-shared", "", "memory")
    (root / "raw_memories.md").write_text("first native fact")
    c.sync()
    with database(c.path) as db:
        asset = db.execute("SELECT asset FROM source_parts").fetchone()["asset"]
        # The upgraded client shows the store as "codex" locally.
        db.execute("UPDATE sources SET product='codex'")
    assert _hub_product(hub, asset) == "codex-shared"
    (root / "raw_memories.md").write_text("second native fact, edited after the upgrade")
    result = c.sync()
    assert not [e for e in result.get("errors", []) if "upload" in e]
    with database(c.path) as db:
        assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0
    assert _hub_product(hub, asset) == "codex-shared"
    hits = c.search("second native fact")
    assert hits and hits[0]["product"] == "codex-shared"


def test_a_new_codex_source_uploads_the_label_every_hub_accepts(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "new-codex-user")
    root = tmp_path / "codex-memories"
    root.mkdir()
    c.register(root, "codex", "", "memory")
    (root / "raw_memories.md").write_text("fresh codex memory")
    c.sync()
    with database(c.path) as db:
        asset = db.execute("SELECT asset FROM source_parts").fetchone()["asset"]
    assert _hub_product(hub, asset) == "codex-shared"


def test_refused_records_rotate_behind_the_rest_of_a_bounded_queue(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "rotating")
    root = tmp_path / "memories"
    root.mkdir()
    c.register(root, "claude-code", "", "memory")
    (root / "a.md").write_text("first memory refused by the hub")
    (root / "b.md").write_text("second memory that should still upload")
    put = c.transport.put
    attempts = []

    def refuse_first(item):
        attempts.append(item["path"])
        if item["path"] == "a.md":
            raise ValueError("source identity is immutable")
        return put(item)

    c.transport.put = refuse_first
    c.sync(max_uploads=1)
    c.sync(max_uploads=1)
    assert attempts[:2] == ["a.md", "b.md"]
    with database(c.path) as db:
        left = [json.loads(r["payload"])["path"] for r in db.execute("SELECT * FROM outbox")]
    assert left == ["a.md"]


def test_a_spent_pass_budget_still_attempts_one_upload(tmp_path, monkeypatch):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "slow-link")
    root = tmp_path / "memories"
    root.mkdir()
    c.register(root, "claude-code", "", "memory")
    (root / "a.md").write_text("memory behind a slow handshake")
    from agent_brain.native_sync import client as client_module

    clock = [0.0]
    me = c.transport.me

    def slow_me():
        clock[0] += 120.0  # The handshake alone spends the whole pass budget.
        return me()

    c.transport.me = slow_me
    monkeypatch.setattr(client_module.time, "monotonic", lambda: clock[0])
    c.sync(max_uploads=8, pass_budget_seconds=45)
    with database(c.path) as db:
        assert db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0


def test_an_ownerless_manifest_from_an_earlier_release_is_adopted_then_cleaned(tmp_path):
    root = tmp_path / "projection"
    publish(root, "codex-cli", [])
    manifest = root / "amh-org/manifest.json"
    # Releases before 1.4.6 wrote no owner key at all.
    legacy = json.loads(manifest.read_text())
    legacy.pop("owner", None)
    manifest.write_text(json.dumps(legacy))
    owner = str((tmp_path / "profile").resolve())
    result = publish(root, "codex-cli", [], owner=owner)
    assert result["conflicts"] == []
    assert json.loads(manifest.read_text())["owner"] == owner
    other = publish(root, "codex-cli", [], owner=str(tmp_path / "another-profile"))
    assert other["conflicts"] == [str(manifest)]
    assert cleanup(root, "codex-cli", owner=owner, allow_legacy=True)["conflicts"] == []
    assert not manifest.exists()


@pytest.mark.skipif(not hasattr(__import__("os"), "geteuid"), reason="POSIX permissions")
def test_cowork_runtime_state_is_created_owner_only(tmp_path):
    from agent_brain.native_sync.common import atomic_json
    from agent_brain.native_sync.install import export_cowork_plugin

    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "cowork-parent")
    atomic_json(
        c.root / "config.json", {**c.config, "identity": c.identity, "hub": "https://h.example"}
    )
    runtime = tmp_path / "runtime"
    export_cowork_plugin(tmp_path / "plugin", runtime_state=runtime, recall_state=c.root.resolve())
    assert runtime.stat().st_mode & 0o777 == 0o700
