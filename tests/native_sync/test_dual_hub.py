"""Two independent Hub databases and isolated device profiles; no production IO."""

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_brain.native_sync import cli, hooks, org_tools
from agent_brain.native_sync.client import NativeClient
from agent_brain.native_sync.common import atomic_json
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.install import detach_products, export_cowork_plugin, install_hooks
from agent_brain.native_sync.org_tools import OrganizationTools
from agent_brain.native_sync.profiles import observe_backups, profile_pin, recall_allowed
from agent_brain.native_sync.projection import cleanup, publish
from agent_brain.native_sync.query_worker import bounded_search
from test_client import DirectTransport
from test_hub import enroll
from test_org_mcp import hub as _synthetic_hub


@pytest.fixture
def mcp_hub(tmp_path):
    yield from _synthetic_hub.__wrapped__(tmp_path)


def profile(tmp_path, name, *, disabled=False):
    server = Hub(tmp_path / (name + ".sqlite"))
    identity = enroll(server, name=name)
    state = tmp_path / name
    atomic_json(
        state / "config.json",
        {
            "hub": "https://" + name + ".example.com",
            "identity": identity,
            "recall_enabled": not disabled,
            "targets": [],
        },
    )
    c = NativeClient(state, transport=DirectTransport(server, identity))
    return c, server


def test_independent_uploads_only_primary_recall_and_backup_failure(tmp_path, monkeypatch):
    primary, hub_a = profile(tmp_path, "primary")
    backup, hub_b = profile(tmp_path, "backup", disabled=True)
    root = tmp_path / "native"
    root.mkdir()
    (root / "old.md").write_text("Excluded historical secret")
    for c in (primary, backup):
        c.register(root, "codex-shared", "", "memory")
    (root / "new.md").write_text("shared nova delivery rule")
    assert primary.sync()["pushed"] == 1
    assert backup.sync()["pushed"] == 1
    assert hub_a.search(primary.identity["token"], "nova")
    assert hub_b.search(backup.identity["token"], "nova")
    assert not hub_a.search(primary.identity["token"], "historical")
    assert not hub_b.search(backup.identity["token"], "historical")
    assert primary.search("nova")
    assert backup.search("nova") == backup.cached() == backup.local_search("nova") == []
    assert primary.identity["token"] != backup.identity["token"]
    assert primary.path != backup.path
    monkeypatch.setattr(backup.transport, "me", lambda: (_ for _ in ()).throw(OSError("offline")))
    (root / "next.md").write_text("shared next nova rule")
    with pytest.raises(OSError):
        backup.sync()
    assert backup.pending_count() == 1
    assert primary.sync()["pushed"] == 1
    assert primary.search("nova")
    monkeypatch.setattr(
        primary.transport, "search", lambda *a, **k: (_ for _ in ()).throw(OSError("offline"))
    )
    with pytest.raises(OSError):
        primary.search("nova")  # No fallback connection to backup.
    assert hub_b.search(backup.identity["token"], "next") == []


def test_disabled_hook_cached_query_and_live_client_paths(tmp_path, monkeypatch):
    c, hub_a = profile(tmp_path, "primary")
    root = tmp_path / "native"
    root.mkdir()
    c.config["projection"] = True
    atomic_json(c.root / "config.json", c.config)
    c.register(root, "codex-shared", "", "memory")
    (root / "new.md").write_text("cached nova evidence")
    c.sync()
    assert c.cached()
    c.config["recall_enabled"] = False
    atomic_json(c.root / "config.json", c.config)
    assert c.cached() == c.local_search("nova") == c.search("nova") == []
    monkeypatch.setattr(
        "agent_brain.native_sync.query_worker.subprocess.run",
        lambda *a, **k: pytest.fail("disabled query must not spawn network"),
    )
    assert bounded_search(c.root, "nova", timeout=1)["items"] == []
    monkeypatch.setattr(hooks, "NativeClient", lambda *a: c)
    assert (
        hooks.run(
            c.root,
            "codex-cli",
            {"hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": "nova"},
        )
        == {}
    )
    assert not (c.root / "paused").exists()


def test_mcp_disabled_during_existing_research_gates_search_read_related(mcp_hub):
    hub = mcp_hub
    tools = OrganizationTools(hub.state)
    result = tools.search("organization")
    assert result["ok"]
    research = result["research_id"]
    calls = len(hub.calls)
    hub.config["recall_enabled"] = False
    atomic_json(hub.state / "config.json", hub.config)
    for result in (
        tools.search("more", research_id=research),
        tools.read("asset_a", 2, research_id=research),
        tools.related("asset_a", 2, research_id=research),
    ):
        assert result["code"] == "recall_disabled"
    assert len(hub.calls) == calls


def test_bounded_observations_preserve_history_and_batch_fresh_files(tmp_path):
    primary, _ = profile(tmp_path, "primary")
    backup, hub_b = profile(tmp_path, "backup", disabled=True)
    watcher = tmp_path / ".claude/projects"
    backup.config["discovery_roots"] = [{"product": "claude-code", "root": str(watcher)}]
    atomic_json(backup.root / "config.json", backup.config)
    primary.config["observation_targets"] = [
        {"state": str(backup.root), "pin": profile_pin(backup.config)}
    ]
    memory = watcher / "future/memory"
    memory.mkdir(parents=True)
    (memory / "old.md").write_text("excluded old history")
    observe_backups(primary, "claude-code", {"hook_event_name": "SessionStart"}, "s", "0")
    for name in ("one", "two"):
        path = memory / (name + ".md")
        path.write_text("new witnessed nova " + name)
        observe_backups(
            primary,
            "claude-code",
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Write",
                "prompt": "DO NOT SAVE PROMPT",
                "tool_input": {"file_path": str(path), "content": "DO NOT SAVE BODY"},
                "tool_response": {"type": "create"},
            },
            "s",
            "0",
        )
    queued = list((backup.root / "source-observations").glob("*.json"))
    assert len(queued) == 3
    assert all("DO NOT SAVE" not in p.read_text() for p in queued)
    assert backup.sync()["pushed"] == 2
    assert len(hub_b.search(backup.identity["token"], "nova")) == 2
    assert hub_b.search(backup.identity["token"], "history") == []
    (memory / "old.md").write_text("edited excluded old history")
    backup.sync()
    assert hub_b.search(backup.identity["token"], "history") == []
    assert not list((backup.root / "source-observations").glob("*.json"))
    # A bad peer cannot suppress another explicitly authorized peer.
    primary.config["observation_targets"].insert(0, {"state": str(tmp_path / "missing"), "pin": {}})
    observe_backups(primary, "claude-code", {"hook_event_name": "SessionStart"}, "s", "0")
    assert list((backup.root / "source-observations").glob("*.json"))


def test_setup_sync_only_idempotent_preserves_settings_and_starts_service(tmp_path, monkeypatch):
    c, _ = profile(tmp_path, "backup")
    home = tmp_path / "home"
    memory = home / ".codex/memories"
    memory.mkdir(parents=True)
    settings = home / ".codex/config.toml"
    settings.write_text('model = "user-model"\n[mcp_servers.amh-org]\ncommand="existing"\n')
    before = settings.read_bytes()
    monkeypatch.setattr(cli, "require_research_service", lambda *a: None)
    monkeypatch.setattr(
        cli,
        "discover_products",
        lambda *a: [
            {"product": "codex-cli", "name": "Codex", "detected": True, "roots": [str(memory)]}
        ],
    )
    monkeypatch.setattr(cli, "discover_assets", lambda *a: [])
    monkeypatch.setattr("agent_brain.native_sync.history.items", lambda *a: [])
    monkeypatch.setattr(cli.typer, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(cli, "install_hooks", lambda *a, **k: pytest.fail("no product install"))
    monkeypatch.setattr(
        cli, "export_cowork_plugin", lambda *a, **k: pytest.fail("no plugin export")
    )
    monkeypatch.setattr(cli.platform, "system", lambda: "Darwin")
    starts = []
    monkeypatch.setattr(cli, "start_background_service", lambda *a, **k: starts.append(a))
    monkeypatch.setattr(cli, "initial_sync", lambda c: not c.recall_enabled())
    runner = CliRunner()
    args = [
        "setup",
        "--sync-only",
        "--hub",
        c.config["hub"],
        "--state",
        str(c.root),
        "--home",
        str(home),
    ]
    for _ in range(2):
        result = runner.invoke(cli.app, args)
        assert result.exit_code == 0, result.output
    current = NativeClient(c.root, transport=c.transport)
    assert not current.recall_enabled() and len(current.sources()) == 1
    assert settings.read_bytes() == before and len(starts) == 2
    assert not (home / ".codex/hooks.json").exists()


def test_detach_precise_preserves_user_edits_worker_and_allows_primary(tmp_path):
    home, old, new = tmp_path / "home", tmp_path / "old", tmp_path / "new"
    products = ["codex-cli", "claude-code"]
    install_hooks(home, old, products=products)
    sentinel = old / "worker-sentinel"
    sentinel.write_text("running")
    settings = home / ".claude/settings.json"
    data = json.loads(settings.read_text())
    data["userSetting"] = 42
    data["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "echo keep-user-hook"}]})
    atomic_json(settings, data)
    config = home / ".codex/config.toml"
    config.write_text(config.read_text() + "\n[user_values]\nkeep = 42\n")
    originals = {p: p.read_bytes() for p in (settings, config)}
    preview = detach_products(old)
    assert not preview["conflicts"] and not preview["applied"]
    assert all(p.read_bytes() == content for p, content in originals.items())
    assert detach_products(old, apply=True)["applied"]
    assert sentinel.read_text() == "running"
    install_hooks(home, new, products=products)
    assert json.loads(settings.read_text())["userSetting"] == 42
    assert "echo keep-user-hook" in settings.read_text()
    assert "keep = 42" in config.read_text()
    assert (
        str(new) in config.read_text()
        and str(old) not in config.read_text().split("[mcp_servers.amh-org]")[-1].split("\n[")[0]
    )


def test_detach_owned_edit_conflict_prevents_all_mutation(tmp_path):
    home, state = tmp_path / "home", tmp_path / "state"
    install_hooks(home, state, products=["codex-cli", "claude-code"])
    settings = home / ".claude/settings.json"
    data = json.loads(settings.read_text())
    data["hooks"]["Stop"][0]["hooks"][0]["timeout"] = 999
    atomic_json(settings, data)
    all_paths = [
        home / ".codex/hooks.json",
        home / ".codex/config.toml",
        settings,
        home / ".claude.json",
    ]
    before = [p.read_bytes() for p in all_paths]
    result = detach_products(state, apply=True)
    assert result["conflicts"] and not result["applied"]
    assert before == [p.read_bytes() for p in all_paths]


def test_owner_safe_projection_and_host_cowork_binding(tmp_path):
    c, _ = profile(tmp_path, "primary")
    root = tmp_path / "projection"
    publish(root, "codex-cli", [], owner=str(c.root.resolve()))
    before = (root / "amh-org/manifest.json").read_bytes()
    result = cleanup(root, "codex-cli", owner=str(tmp_path / "backup"), allow_legacy=False)
    assert result["conflicts"] and (root / "amh-org/manifest.json").read_bytes() == before
    legacy = tmp_path / "legacy"
    publish(legacy, "codex-cli", [])
    assert cleanup(legacy, "codex-cli", owner=str(c.root), allow_legacy=False)["conflicts"]
    runtime = tmp_path / "runtime"
    export_cowork_plugin(tmp_path / "plugin", runtime_state=runtime, recall_state=c.root.resolve())
    assert recall_allowed(runtime)
    c.config["recall_enabled"] = False
    atomic_json(c.root / "config.json", c.config)
    assert not recall_allowed(runtime)
    assert OrganizationTools(runtime).search("nova")["code"] == "recall_disabled"
    (c.root / "config.json").unlink()
    assert not recall_allowed(runtime)  # Missing parent fails closed.


def test_installer_accepts_sync_only_forwarding(tmp_path):
    script = Path(__file__).resolve().parents[2] / "install-native.sh"
    result = subprocess.run(
        [
            "sh",
            str(script),
            "--hub",
            "https://primary.example.com",
            "--sync-only",
            "--no-projection",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0 and "Sync only: true" in result.stdout


def test_copy_only_approved_versions_rescreens_and_preserves_independent_identity(tmp_path):
    import importlib.util

    script = Path(__file__).resolve().parents[2] / "scripts/native-copy-approved.py"
    spec = importlib.util.spec_from_file_location("copy_approved", script)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    old, hub_a = profile(tmp_path, "old")
    new, hub_b = profile(tmp_path, "new", disabled=True)
    root = tmp_path / "source"
    root.mkdir()
    (root / "unselected.md").write_text("unselected old confidential")
    sid = old.register(root, "codex-shared", "", "memory")
    (root / "approved.md").write_text("approved nova shared rule")
    old.sync()
    # A previously approved file now has private data: migration must rescreen.
    (root / "changed.md").write_text("previously harmless selected nova")
    old.sync()
    (root / "changed.md").write_text("api_key = 123456789abcdef")
    plan = migration.preview(old.root, new.root)
    assert plan["counts"]["selected"] == 1
    assert plan["counts"]["skipped_unselected"] == 1
    assert (
        plan["counts"].get("skipped_sensitive", 0) + plan["counts"].get("skipped_blocked", 0) == 1
    )
    assert old.identity["token"] not in json.dumps(plan)
    identity_before = new.identity.copy()
    report = migration.apply(plan)
    assert report["selected"] == 1 and report["uploaded"] == 0
    new.sync()
    assert new.identity == identity_before
    assert hub_b.search(new.identity["token"], "approved nova")
    assert hub_b.search(new.identity["token"], "confidential") == []
    assert hub_b.search(new.identity["token"], "api_key") == []
    # Applying the same choices is idempotent and still does not copy cursors.
    assert migration.apply(plan)["selected"] == 1
    (root / "approved.md").write_text("changed since approved preview")
    with pytest.raises(ValueError, match="changed"):
        migration.apply(plan)


def test_cli_recall_and_foreign_hook_guard(tmp_path):
    c, _ = profile(tmp_path, "profile")
    runner = CliRunner()
    disabled = runner.invoke(cli.app, ["recall", "--state", str(c.root), "--disable"])
    assert disabled.exit_code == 0, disabled.output
    assert not json.loads(disabled.output)["recall_enabled"]
    assert not (c.root / "paused").exists()
    enabled = runner.invoke(cli.app, ["recall", "--state", str(c.root), "--enable"])
    assert enabled.exit_code == 0 and json.loads(enabled.output)["recall_enabled"]
    activated = runner.invoke(
        cli.app,
        [
            "activate-products",
            "--state",
            str(c.root),
            "--home",
            str(tmp_path / "activated-home"),
            "--product",
            "codex-cli",
        ],
    )
    assert activated.exit_code == 0, activated.output
    assert (tmp_path / "activated-home/.codex/hooks.json").exists()
    home = tmp_path / "home"
    old = tmp_path / "old"
    install_hooks(home, old, products=["claude-code"])
    # Even if someone deleted the MCP entry, callbacks of a different profile
    # cannot be silently claimed by a new installation.
    (home / ".claude.json").unlink()
    before = (home / ".claude/settings.json").read_bytes()
    with pytest.raises(ValueError, match="detach"):
        install_hooks(home, tmp_path / "new", products=["claude-code"])
    assert (home / ".claude/settings.json").read_bytes() == before


def test_migration_preserves_deleted_and_renamed_history_denials(tmp_path):
    import importlib.util
    import shutil

    script = Path(__file__).resolve().parents[2] / "scripts/native-copy-approved.py"
    spec = importlib.util.spec_from_file_location("copy_denials", script)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    old, old_hub = profile(tmp_path, "old-denials")
    new, new_hub = profile(tmp_path, "new-denials", disabled=True)
    root = tmp_path / "history"
    root.mkdir()
    (root / "deleted.md").write_text("never approved historical oldbody")
    (root / "renamed.md").write_text("never approved original inode")
    (root / "another.md").write_text("never approved another historical")
    old.register(root, "codex-shared", "", "memory")
    # Missing original paths must survive migration, including identity denial
    # for a file renamed and edited without going through the old scanner.
    (root / "deleted.md").unlink()
    (root / "renamed.md").rename(root / "new-name.md")
    (root / "new-name.md").write_text("changed unapproved retained inode")
    plan = migration.preview(old.root, new.root)
    assert plan["version"] == 2 and plan["counts"]["denied_hashes"] >= 3
    assert plan["counts"]["denied_paths"] == 3
    migration.apply(plan)
    (root / "deleted.md").write_text("different unapproved path content")
    (root / "copied-old.md").write_text("never approved historical oldbody")
    (root / "fresh.md").write_text("new allowed novafresh")
    old.sync()
    new.sync()
    for c, hub in ((old, old_hub), (new, new_hub)):
        assert hub.search(c.identity["token"], "unapproved") == []
        assert hub.search(c.identity["token"], "oldbody") == []
        assert len(hub.search(c.identity["token"], "novafresh")) == 1
    # The entire old registered root can be absent during migration as well.
    offline, offline_hub = profile(tmp_path, "offline-target", disabled=True)
    shutil.rmtree(root)
    plan = migration.preview(old.root, offline.root)
    migration.apply(plan)
    root.mkdir()
    (root / "deleted.md").write_text("yet another unapproved recreation")
    offline.sync()
    assert offline_hub.search(offline.identity["token"], "unapproved") == []


def test_cached_and_local_search_recheck_policy_after_materialization(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from agent_brain.native_sync import client as client_module, materialize

    c, _ = profile(tmp_path, "race-cache")
    root = tmp_path / "race-source"
    root.mkdir()
    c.config["projection"] = True
    atomic_json(c.root / "config.json", c.config)
    c.register(root, "codex-shared", "", "memory")
    (root / "fact.md").write_text("race nova body")
    c.sync()
    real_database = client_module.database

    @contextmanager
    def disable_after_read(path):
        with real_database(path) as db:
            yield db
        c.config["recall_enabled"] = False
        atomic_json(c.root / "config.json", c.config)

    monkeypatch.setattr(client_module, "database", disable_after_read)
    assert c.cached() == []
    monkeypatch.setattr(client_module, "database", real_database)
    c.config["recall_enabled"] = True
    atomic_json(c.root / "config.json", c.config)
    real_assembled = materialize.assembled

    def disable_during_assembly(records):
        result = real_assembled(records)
        c.config["recall_enabled"] = False
        atomic_json(c.root / "config.json", c.config)
        return result

    monkeypatch.setattr(materialize, "assembled", disable_during_assembly)
    assert c.local_search("nova") == []


def test_projection_export_and_activation_guard_actual_write_boundaries(tmp_path, monkeypatch):
    from agent_brain.native_sync import materialize, assets

    c, _ = profile(tmp_path, "race-write")
    root = tmp_path / "write-source"
    root.mkdir()
    c.config["projection"] = True
    atomic_json(c.root / "config.json", c.config)
    c.register(root, "codex-shared", "", "memory")
    (root / "fact.md").write_text("race nova body")
    c.sync()
    records = c.cached()
    original = materialize.assembled

    def disable_during_assembly(rows):
        result = original(rows)
        c.config["recall_enabled"] = False
        atomic_json(c.root / "config.json", c.config)
        return result

    monkeypatch.setattr(materialize, "assembled", disable_during_assembly)
    for action, destination in (
        (
            lambda p: publish(
                p, "codex-cli", records, owner=str(c.root), before_write=c.require_recall
            ),
            tmp_path / "publish-race",
        ),
        (
            lambda p: materialize.materialize(
                p, records, kinds={"memory"}, before_write=c.require_recall
            ),
            tmp_path / "export-race",
        ),
    ):
        c.config["recall_enabled"] = True
        atomic_json(c.root / "config.json", c.config)
        with pytest.raises(ValueError, match="recall is disabled"):
            action(destination)
        assert not list(destination.rglob("*.md"))
    rule = {**records[0], "kind": "rule"}
    # CLI confirmation can take arbitrarily long: policy must be reread on apply.
    c.config["recall_enabled"] = False
    atomic_json(c.root / "config.json", c.config)
    destination = tmp_path / "rules.md"
    with pytest.raises(ValueError, match="recall is disabled"):
        assets.activate_rules(destination, [rule], before_write=c.require_recall)
    assert not destination.exists()


def test_permanently_stale_observations_leave_queue_with_denials(tmp_path, monkeypatch):
    from agent_brain.native_sync.profiles import process_source_observations

    primary, _ = profile(tmp_path, "observation-primary")
    backup, hub_b = profile(tmp_path, "observation-backup", disabled=True)
    watcher = tmp_path / ".claude/projects"
    backup.config["discovery_roots"] = [{"product": "claude-code", "root": str(watcher)}]
    atomic_json(backup.root / "config.json", backup.config)
    primary.config["observation_targets"] = [
        {"state": str(backup.root), "pin": profile_pin(backup.config)}
    ]
    root = watcher / "new/memory"
    root.mkdir(parents=True)
    for name in ("changed", "missing", "fresh"):
        path = root / (name + ".md")
        path.write_text("observed " + name)
        observe_backups(
            primary,
            "claude-code",
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Write",
                "tool_input": {"file_path": str(path)},
                "tool_response": {"type": "create"},
            },
            "s",
            "0",
        )
    (root / "changed.md").write_text("changed after observed history")
    (root / "missing.md").unlink()
    backup.sync()
    assert not list((backup.root / "source-observations").glob("*.json"))
    assert len(list((backup.root / "source-observation-review").glob("*.json"))) == 2
    (root / "missing.md").write_text("reappeared without approval")
    backup.sync()
    assert hub_b.search(backup.identity["token"], "history") == []
    assert hub_b.search(backup.identity["token"], "approval") == []
    assert hub_b.search(backup.identity["token"], "fresh")
    # Transient IO does not consume the event: it can retry after the mount recovers.
    path = root / "transient.md"
    path.write_text("transient nova")
    observe_backups(
        primary,
        "claude-code",
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(path)},
            "tool_response": {"type": "create"},
        },
        "s",
        "0",
    )
    real_stat = Path.stat

    def transient_stat(self, *a, **kw):
        if self == path:
            raise PermissionError("temporary filesystem access denial")
        return real_stat(self, *a, **kw)

    monkeypatch.setattr(Path, "stat", transient_stat)
    assert any("retry" in item for item in process_source_observations(backup))
    assert list((backup.root / "source-observations").glob("*.json"))


def test_detach_missing_hook_fields_is_idempotent_and_still_removes_mcp(tmp_path):
    home, state = tmp_path / "detached-home", tmp_path / "detached-state"
    install_hooks(home, state, products=["claude-code", "codex-cli"])
    settings = home / ".claude/settings.json"
    data = json.loads(settings.read_text())
    data.pop("hooks")
    data["userSetting"] = "keep"
    atomic_json(settings, data)
    result = detach_products(state, apply=True)
    assert result["applied"] and result["conflicts"] == []
    assert json.loads(settings.read_text()) == data
    assert "amh-org" not in json.loads((home / ".claude.json").read_text())["mcpServers"]
    assert detach_products(state, apply=True)["conflicts"] == []


def test_migration_offline_root_remains_quarantined_after_unreviewed_restore(tmp_path):
    import importlib.util

    script = Path(__file__).resolve().parents[2] / "scripts/native-copy-approved.py"
    spec = importlib.util.spec_from_file_location("copy_offline_root", script)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    old, _ = profile(tmp_path, "offline-old")
    new, hub_new = profile(tmp_path, "offline-new", disabled=True)
    root = tmp_path / "mounted"
    root.mkdir()
    (root / "excluded.md").write_text("excluded before root went offline")
    old.register(root, "codex-shared", "", "memory")
    detached = tmp_path / "detached"
    root.rename(detached)
    (detached / "never-reviewed.md").write_text("unreviewed offline novahistory")
    plan = migration.preview(old.root, new.root)
    assert plan["sources"][0]["quarantined"] is True
    migration.apply(plan)
    detached.rename(root)
    for _ in range(2):
        assert new.sync()["pushed"] == 0
        assert new.quarantined_sources()
    assert hub_new.search(new.identity["token"], "novahistory") == []


def test_projection_disable_mid_publish_rolls_back_only_unchanged_own_writes(tmp_path):
    c, _ = profile(tmp_path, "partial-projection")
    source = tmp_path / "partial-source"
    source.mkdir()
    c.config["projection"] = True
    atomic_json(c.root / "config.json", c.config)
    c.register(source, "codex-shared", "", "memory")
    (source / "fact.md").write_text("partial nova body")
    c.sync()
    records = c.cached()
    destination = tmp_path / "partial-target"
    destination.mkdir()
    (destination / "MEMORY.md").write_text("user native prose")
    calls = 0

    def disable_on_third_write():
        nonlocal calls
        calls += 1
        if calls == 3:
            c.config["recall_enabled"] = False
            atomic_json(c.root / "config.json", c.config)
        c.require_recall()

    with pytest.raises(ValueError, match="recall is disabled"):
        publish(
            destination,
            "codex-cli",
            records,
            owner=str(c.root),
            before_write=disable_on_third_write,
        )
    assert (destination / "MEMORY.md").read_text() == "user native prose"
    assert not list((destination / "amh-org").glob("*.md"))
    assert not (destination / "amh-org/manifest.json").exists()
    # Updating an existing owned projection rolls back to its previous complete
    # version, whose original manifest can still safely clean up after disable.
    from agent_brain.native_sync.common import digest

    restored = tmp_path / "restored-target"
    publish(restored, "codex-cli", records, owner=str(c.root))
    before = {
        str(p.relative_to(restored)): p.read_bytes() for p in restored.rglob("*") if p.is_file()
    }
    updated_body = "replacement nova body"
    updated = [
        {
            **records[0],
            "body": updated_body,
            "body_hash": digest(updated_body),
            "document_hash": digest(updated_body),
            "revision": records[0]["revision"] + 1,
        }
    ]
    c.config["recall_enabled"] = True
    atomic_json(c.root / "config.json", c.config)
    calls = 0
    with pytest.raises(ValueError, match="recall is disabled"):
        publish(
            restored, "codex-cli", updated, owner=str(c.root), before_write=disable_on_third_write
        )
    assert {
        str(p.relative_to(restored)): p.read_bytes() for p in restored.rglob("*") if p.is_file()
    } == before
    assert cleanup(restored, "codex-cli", owner=str(c.root), allow_legacy=False)["conflicts"] == []
    # Concurrent user edits to a file written earlier in this operation are
    # not permission to delete it during rollback.
    c.config["recall_enabled"] = True
    atomic_json(c.root / "config.json", c.config)
    calls = 0

    def edit_then_disable():
        nonlocal calls
        calls += 1
        if calls == 3:
            next((destination / "amh-org").glob("*.md")).write_text("concurrent user edit")
            c.config["recall_enabled"] = False
            atomic_json(c.root / "config.json", c.config)
        c.require_recall()

    with pytest.raises(ValueError, match="preserved concurrent edits"):
        publish(
            destination, "codex-cli", records, owner=str(c.root), before_write=edit_then_disable
        )
    assert [p.read_text() for p in (destination / "amh-org").glob("*.md")] == [
        "concurrent user edit"
    ]
