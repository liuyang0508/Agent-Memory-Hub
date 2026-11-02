"""Upload scope tests use real snapshots, outbox and a local Hub; no network."""

import json
import subprocess
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_brain.native_sync import cli, history, picker, source_policy
from agent_brain.native_sync.client import NativeClient
from agent_brain.native_sync.common import atomic_json
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.storage import database
from test_client import DirectTransport
from test_history_import import make_home


def enrolled(tmp_path, *, legacy=False):
    hub = Hub(tmp_path / "hub.sqlite")
    identity = hub.provision("scope-test", "isolated")
    c = NativeClient.adopt(tmp_path / "profile", "http://127.0.0.1:9", identity, "isolated")
    if legacy:
        del c.config["source_policy"]
        atomic_json(c.root / "config.json", c.config)
    c.transport = DirectTransport(hub, identity)
    return c, hub


def pending(c):
    with database(c.path) as db:
        return [json.loads(r["payload"]) for r in db.execute("SELECT payload FROM outbox")]


def remote(c, hub):
    return hub.changes(c.identity["token"], 0)["items"]


def bind_kinds(c, tmp_path):
    paths = {}
    for kind in source_policy.KINDS:
        root = tmp_path / kind
        root.mkdir()
        c.register(root, "claude-code", "scope", kind)
        path = root / (
            "future.jsonl"
            if kind == "transcript"
            else "future.json"
            if kind == "config"
            else "future.md"
        )
        paths[kind] = path
        path.write_text(
            '{"type":"user","message":{"content":"scope transcript canary"}}'
            if kind == "transcript"
            else '{"model":"scope-config-canary"}'
            if kind == "config"
            else "scope " + kind + " canary"
        )
    return paths


def test_new_enrollment_defaults_to_memory_for_actual_uploads(tmp_path):
    c, hub = enrolled(tmp_path)
    paths = bind_kinds(c, tmp_path)
    assert c.source_scope()["enabled_kinds"] == ["memory"]
    assert c.sync()["pushed"] == 1
    assert {r["kind"] for r in remote(c, hub)} == {"memory"}
    assert all(p.exists() for p in paths.values())


@pytest.mark.parametrize("kind", ["transcript", "rule", "skill", "config"])
def test_explicit_opt_in_uploads_only_chosen_extra_kind(tmp_path, kind):
    c, hub = enrolled(tmp_path)
    bind_kinds(c, tmp_path)
    c.set_source_scope(["memory", kind])
    c.sync()
    assert {r["kind"] for r in remote(c, hub)} == {"memory", kind}


def test_disabled_scope_preserves_queued_upload_and_selected_history_then_resumes(tmp_path):
    c, hub = enrolled(tmp_path)
    home = make_home(tmp_path)
    c.set_source_scope(["memory", "transcript"])
    selected = [r for r in history.items(home, ["claude-code"]) if r["kind"] == "transcript"]
    history.import_paths(c, home, selected)
    c.scan()
    before = pending(c)
    assert len(before) == 1
    c.set_source_scope(["memory"])
    assert c.source_scope()["paused_pending"] == 1
    assert c.sync()["pushed"] == 0
    assert pending(c) == before
    assert remote(c, hub) == []
    rows = history.annotate_selection(c, history.items(home, ["claude-code"]))
    chosen = next(r for r in rows if r["path"] == selected[0]["path"])
    assert chosen["previously_selected"] and not chosen["scope_enabled"]
    assert Path(chosen["path"]).exists()
    c.set_source_scope(["memory", "transcript"])
    assert c.sync()["pushed"] == 1
    assert pending(c) == []
    assert remote(c, hub)[0]["kind"] == "transcript"


def test_pausing_never_deletes_remote_or_native_data_and_stale_worker_reloads_policy(tmp_path):
    c, hub = enrolled(tmp_path)
    paths = bind_kinds(c, tmp_path)
    c.set_source_scope(["memory", "rule"])
    c.sync()
    original_remote = remote(c, hub)
    paths["rule"].write_text("updated rule must stay queued")
    c.scan()
    before = pending(c)
    old_worker = NativeClient(c.root, transport=c.transport)
    c.set_source_scope(["memory"])
    assert old_worker.sync()["pushed"] == 0
    assert pending(c) == before
    assert remote(c, hub) == original_remote
    assert paths["rule"].read_text() == "updated rule must stay queued"
    c.set_source_scope(["memory", "rule"])
    assert old_worker.sync()["pushed"] == 1


def test_legacy_profile_preview_preserves_behavior_and_requires_confirmation_to_change(
    tmp_path, monkeypatch
):
    c, hub = enrolled(tmp_path, legacy=True)
    bind_kinds(c, tmp_path)
    original = (c.root / "config.json").read_bytes()
    monkeypatch.setattr(cli, "load", lambda state: c)
    preview = CliRunner().invoke(cli.app, ["scope", "--state", str(c.root)])
    assert preview.exit_code == 0, preview.output
    assert json.loads(preview.stdout)["mode"] == "legacy"
    assert "新版仅记忆默认值尚未应用" in preview.output
    assert (c.root / "config.json").read_bytes() == original
    denied = CliRunner().invoke(cli.app, ["scope", "--kind", "memory"], input="n\n")
    assert denied.exit_code == 0, denied.output
    assert (c.root / "config.json").read_bytes() == original
    c.sync()
    assert {r["kind"] for r in remote(c, hub)} == set(source_policy.KINDS)
    accepted = CliRunner().invoke(cli.app, ["scope", "--kind", "memory"], input="y\n")
    assert accepted.exit_code == 0, accepted.output
    assert c.source_scope()["enabled_kinds"] == ["memory"]
    assert {r["kind"] for r in remote(c, hub)} == set(source_policy.KINDS)


def test_memory_history_import_does_not_enable_future_chats(tmp_path):
    c, hub = enrolled(tmp_path)
    home = make_home(tmp_path)
    for entry in history.roots(home, "claude-code"):
        c.register(entry[0], "claude-code", "", entry[1])
    rows = history.items(home, ["claude-code"])
    history.import_paths(c, home, [r for r in rows if r["kind"] == "memory"])
    (home / ".claude/projects/-Users-example/later.jsonl").write_text(
        '{"type":"user","message":{"content":"new private chat"}}'
    )
    c.sync()
    assert {r["kind"] for r in remote(c, hub)} == {"memory"}
    with pytest.raises(ValueError, match="confirm upload scope"):
        history.import_paths(c, home, [r for r in rows if r["kind"] == "transcript"])


def test_import_extra_scope_rejected_without_confirmation(tmp_path, monkeypatch):
    c, hub = enrolled(tmp_path)
    home = make_home(tmp_path)
    monkeypatch.setattr(cli, "load", lambda state: c)
    result = CliRunner().invoke(
        cli.app,
        [
            "import",
            "--no-web",
            "--yes",
            "--kind",
            "transcript",
            "--state",
            str(c.root),
            "--home",
            str(home),
        ],
        input="y\nn\n",
    )
    assert result.exit_code == 0, result.output
    assert "后续自动同步" in result.output
    assert c.source_scope()["enabled_kinds"] == ["memory"]
    assert remote(c, hub) == []
    assert c.sources() == []


def test_picker_filter_preserves_hidden_selection_and_skip_keeps_prior_decision(tmp_path):
    # Execute the actual browser script with a small DOM fixture. This catches
    # reset-on-render bugs without depending on any installed browser profile.
    if not shutil.which("node"):
        pytest.skip("node unavailable")
    sample = [
        dict(
            i=i,
            name=f"row{i}",
            title="title",
            product_name="product",
            kind_name="记忆",
            bytes=10,
            mtime=1,
            risk="clean",
            reasons=[],
        )
        for i in range(2)
    ]
    script = (
        picker.PAGE.split("<script>", 1)[1]
        .split("</script>", 1)[0]
        .replace("__ROWS__", json.dumps(sample))
    )
    fixture = """const assert = require('node:assert/strict');
const nodes = {};
global.document = {getElementById(id) { return nodes[id] ||= {value:'',textContent:'',innerHTML:'',append(){},addEventListener(){}}; },createElement(){return {};},body:{innerHTML:''}};
let posted;
global.fetch = async (url, args) => {posted = JSON.parse(args.body); return {ok:true};};
"""
    checks = """
toggleSelection({dataset:{i:'0'},checked:true});
document.getElementById('q').value = 'row1'; render();
assert.equal(SELECTED.has(0), true);
toggleSelection({dataset:{i:'1'},checked:true});
assert.equal(SELECTED.size, 2);
(async () => {await submitSelection(true); assert.deepEqual(posted.picked, [0,1]); await submitSelection(false); assert.deepEqual(posted.picked, []);})().catch(e => {console.error(e);process.exitCode=1});
"""
    result = subprocess.run(
        ["node", "-e", fixture + script + checks], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
