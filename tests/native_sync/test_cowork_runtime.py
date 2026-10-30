"""Runtime pairing must preserve identity without bundling reusable credentials."""

import pytest
import json
import zipfile
import os
import subprocess
import sys
import secrets
from pathlib import Path
from fastapi.testclient import TestClient

from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.api import create_app
from test_hub import enroll
from test_runtime import live as live
from test_client import client
from agent_brain.native_sync.client import NativeClient
from agent_brain.native_sync.cowork_runtime import (
    export_connection,
    enroll_runtime,
    schedule_enrollment,
)
from agent_brain.native_sync.runtime_sources import observe_native_root, queue_observation
from agent_brain.native_sync.install import export_cowork_plugin
from agent_brain.native_sync.storage import database


def test_runtime_invitation_issues_an_independent_device_for_same_member(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    host = enroll(hub)
    ticket = hub.runtime_invite(host["token"])
    runtime = hub.enroll(ticket["invite"], "Cowork runtime")
    assert runtime["member_id"] == host["member_id"] == ticket["member_id"]
    assert runtime["tenant_id"] == host["tenant_id"]
    assert runtime["device_id"] != host["device_id"]
    assert runtime["token"] != host["token"]
    with pytest.raises(ValueError):
        hub.enroll(ticket["invite"], "replay")


def test_revoking_host_invalidates_unclaimed_runtime_ticket(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    host = enroll(hub)
    ticket = hub.runtime_invite(host["token"])
    hub.revoke(host["device_id"])
    with pytest.raises(ValueError):
        hub.enroll(ticket["invite"], "Cowork runtime")
    with pytest.raises(PermissionError):
        hub.runtime_invite(host["token"])


def test_runtime_ticket_endpoint_cannot_select_another_identity(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    host = enroll(hub)
    api = TestClient(create_app(hub))
    headers = {"Authorization": "Bearer " + host["token"]}
    assert api.post("/native/v1/runtime-invites", json={}).status_code == 401
    assert (
        api.post(
            "/native/v1/runtime-invites", headers=headers, json={"member_id": "other"}
        ).status_code
        == 422
    )
    response = api.post("/native/v1/runtime-invites", headers=headers, json={})
    assert response.status_code == 200
    assert response.json()["member_id"] == host["member_id"]


def test_expired_runtime_ticket_cannot_enroll(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    host = enroll(hub)
    ticket = hub.runtime_invite(host["token"])
    with database(hub.path) as db:
        db.execute("UPDATE runtime_invites SET expires=0")
    with pytest.raises(ValueError):
        hub.enroll(ticket["invite"], "late runtime")


def test_pairing_keeps_credentials_outside_plugin_and_is_idempotent(live, tmp_path):
    hub, url = live
    host = NativeClient.join(tmp_path / "host", url, hub.invite("linkcrux", "member"), "host")
    connection = tmp_path / "connection.json"
    policy = export_connection(host.root, connection)
    ticket = json.loads(connection.read_text())
    assert connection.stat().st_mode & 0o777 == 0o600
    plugin = export_cowork_plugin(tmp_path / "plugin", policy=policy)
    with zipfile.ZipFile(str(plugin) + ".zip") as archive:
        for name in archive.namelist():
            content = archive.read(name)
            assert ticket["invite"].encode() not in content
            assert host.identity["token"].encode() not in content
    runtime = enroll_runtime(tmp_path / "runtime", policy, connection)
    assert runtime.identity["member_id"] == host.identity["member_id"]
    assert runtime.identity["device_id"] != host.identity["device_id"]
    assert enroll_runtime(runtime.root, policy, connection).identity == runtime.identity


def test_modified_connection_cannot_repoint_pinned_hub(live, tmp_path):
    hub, url = live
    host = NativeClient.join(tmp_path / "host", url, hub.invite("linkcrux", "member"), "host")
    connection = tmp_path / "connection.json"
    policy = export_connection(host.root, connection)
    ticket = json.loads(connection.read_text())
    ticket["hub"] = "https://other.example"
    connection.write_text(json.dumps(ticket))
    with pytest.raises(PermissionError):
        enroll_runtime(tmp_path / "runtime", policy, connection)
    assert not (tmp_path / "runtime/config.json").exists()


def test_mismatched_enrollment_response_is_never_persisted(live, tmp_path, monkeypatch):
    from agent_brain.native_sync.transport import Transport

    hub, url = live
    invite = hub.invite("linkcrux", "member")
    original = Transport.enroll

    def wrong_identity(self, invite, device_name):
        result = original(self, invite, device_name)
        result["member_id"] = "wrong-member"
        return result

    monkeypatch.setattr(Transport, "enroll", wrong_identity)
    with pytest.raises(PermissionError):
        NativeClient.join(
            tmp_path / "runtime",
            url,
            invite,
            "runtime",
            expected_identity={"member_id": "expected-member"},
        )
    assert not (tmp_path / "runtime/config.json").exists()


def test_cowork_runtime_binds_actual_directory_and_excludes_history(tmp_path, monkeypatch):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "member")
    root = tmp_path / "session-mount/memory"
    root.mkdir(parents=True)
    (root / "history.md").write_text("unselected old history")
    monkeypatch.setenv("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE", str(root))
    monkeypatch.setenv("CLAUDE_PROJECT_UUID", "cowork-project-id")
    observe_native_root(c, {"hook_event_name": "SessionStart"})
    (root / "new.md").write_text("new runtime memory")
    c.sync()
    assert [r["body"] for r in c.cached()] == ["new runtime memory"]
    assert c.cached()[0]["product"] == "cowork"
    assert c.targets() == [{"root": str(root), "product": "cowork"}]


def test_cowork_new_session_mount_keeps_original_store_and_revisions(tmp_path, monkeypatch):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "member")
    first = tmp_path / "first-mount"
    first.mkdir()
    monkeypatch.setenv("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE", str(first))
    monkeypatch.setenv("CLAUDE_PROJECT_UUID", "stable-project")
    observe_native_root(c, {"hook_event_name": "SessionStart"})
    (first / "note.md").write_text("first version")
    c.sync()
    original = c.cached()[0]
    second = tmp_path / "next-mount"
    first.rename(second)
    monkeypatch.setenv("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE", str(second))
    observe_native_root(c, {"hook_event_name": "SessionStart"})
    (second / "note.md").write_text("next version")
    c.sync()
    latest = c.cached()[0]
    assert latest["asset_id"] == original["asset_id"]
    assert latest["source_store"] == original["source_store"]
    assert latest["revision"] == 2
    assert latest["body"] == "next version"
    assert c.targets() == [{"root": str(second), "product": "cowork"}]


def test_unknown_runtime_does_not_guess_a_memory_directory(tmp_path, monkeypatch):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "member")
    monkeypatch.delenv("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE", raising=False)
    observe_native_root(c, {"hook_event_name": "SessionStart"})
    assert c.sources() == []


def test_reused_mount_cannot_upload_a_different_projects_history(tmp_path, monkeypatch):
    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "member")
    root = tmp_path / "reused-mount"
    root.mkdir()
    monkeypatch.setenv("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE", str(root))
    monkeypatch.setenv("CLAUDE_PROJECT_UUID", "project-a")
    observe_native_root(c, {"hook_event_name": "SessionStart"})
    (root / "private-history.md").write_text("pre-existing project-b history")
    monkeypatch.setenv("CLAUDE_PROJECT_UUID", "project-b")
    with pytest.raises(ValueError, match="identity"):
        observe_native_root(c, {"hook_event_name": "SessionStart"})
    c.sync()
    assert c.cached() == []
    assert not (root / "amh-org").exists()


@pytest.mark.parametrize("replace_directory", [False, True])
def test_remount_between_discovery_and_scan_never_uploads_other_history(
    tmp_path, monkeypatch, replace_directory
):
    from agent_brain.native_sync import runtime_sources

    hub = Hub(tmp_path / "hub.sqlite")
    c = client(tmp_path, hub, "member")
    root = tmp_path / "mount"
    root.mkdir()
    monkeypatch.setenv("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE", str(root))
    monkeypatch.setenv("CLAUDE_PROJECT_UUID", "a")
    observe_native_root(c, {"hook_event_name": "SessionStart"})
    process = runtime_sources.process_observations

    def remount_after_discovery(client):
        result = process(client)
        if replace_directory:
            root.rename(tmp_path / "old-mount")
            root.mkdir()
        (root / "private.md").write_text("PROJECT B PRIVATE HISTORY")
        if not replace_directory:
            monkeypatch.setenv("CLAUDE_PROJECT_UUID", "b")
            queue_observation(client.root, {"hook_event_name": "SessionStart"})
        return result

    monkeypatch.setattr(runtime_sources, "process_observations", remount_after_discovery)
    c.sync()
    assert c.cached() == []
    assert c.quarantined_sources()


def test_created_file_observation_survives_pairing_and_async_baseline(live, tmp_path, monkeypatch):
    hub, url = live
    host = NativeClient.join(tmp_path / "host", url, hub.invite("linkcrux", "member"), "host")
    connection = tmp_path / "connection.json"
    policy = export_connection(host.root, connection)
    state = tmp_path / "runtime"
    root = tmp_path / "memory"
    root.mkdir()
    (root / "old.md").write_text("old history stays private")
    new = root / "new.md"
    new.write_text("created while pairing")
    monkeypatch.setenv("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE", str(root))
    monkeypatch.setenv("CLAUDE_PROJECT_UUID", "new-project")
    queue_observation(
        state,
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(new), "content": new.read_text()},
            "tool_response": {"type": "create"},
        },
    )
    assert not (state / "config.json").exists()
    runtime = enroll_runtime(state, policy, connection)
    runtime.sync()
    assert [r["body"] for r in runtime.cached()] == ["created while pairing"]
    assert not list((state / "runtime-observations").glob("*.json"))


def test_session_hook_queues_baseline_even_while_database_is_busy(live, tmp_path):
    hub, url = live
    c = NativeClient.join(tmp_path / "runtime", url, hub.invite("linkcrux", "member"), "runtime")
    root = tmp_path / "many-memories"
    root.mkdir()
    (root / "history.md").write_text("history" * 100_000)
    env = {
        **os.environ,
        "CLAUDE_COWORK_MEMORY_PATH_OVERRIDE": str(root),
        "CLAUDE_PROJECT_UUID": "p",
    }
    with database(c.path) as db:
        db.execute("BEGIN IMMEDIATE")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_brain.native_sync.cowork_runtime",
                "--state",
                str(c.root),
            ],
            input=json.dumps({"hook_event_name": "SessionStart", "session_id": "s"}),
            env=env,
            capture_output=True,
            text=True,
            timeout=2,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}
    assert c.sources() == []
    assert list((c.root / "runtime-observations").glob("*.json"))
    c.sync()
    assert len(c.sources()) == 1
    assert c.cached() == []  # Baseline remains history, not an implicit upload.


def test_hook_never_calls_history_baseline_inline(live, tmp_path):
    hub, url = live
    c = NativeClient.join(tmp_path / "runtime", url, hub.invite("linkcrux", "member"), "runtime")
    root = tmp_path / "memory"
    root.mkdir()
    env = {**os.environ, "CLAUDE_COWORK_MEMORY_PATH_OVERRIDE": str(root)}
    script = """
from agent_brain.native_sync.client import NativeClient
def forbidden(*args, **kwargs):
    raise AssertionError("baseline ran inside Hook")
NativeClient.register = forbidden
from agent_brain.native_sync.cowork_runtime import main
main()
"""
    result = subprocess.run(
        [sys.executable, "-c", script, "--state", str(c.root)],
        input=json.dumps({"hook_event_name": "SessionStart", "session_id": "s"}),
        env=env,
        capture_output=True,
        text=True,
        timeout=2,
    )
    assert result.returncode == 0
    assert not (c.root / "runtime-error.json").exists()
    assert c.sources() == []


def test_unstable_pending_create_is_held_for_visible_history_review(live, tmp_path, monkeypatch):
    hub, url = live
    c = NativeClient.join(tmp_path / "runtime", url, hub.invite("linkcrux", "member"), "runtime")
    root = tmp_path / "memory"
    root.mkdir()
    path = root / "new.md"
    path.write_text("first")
    monkeypatch.setenv("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE", str(root))
    queue_observation(
        c.root,
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(path), "content": "first"},
            "tool_response": {"type": "create"},
        },
    )
    path.write_text("changed before inspection")
    c.sync()
    assert c.cached() == []
    assert c.origin_reviews()[0]["status"] == "needs-history-review"


def test_background_enrollment_coalesces_and_never_exposes_invite_in_argv(tmp_path, monkeypatch):
    from agent_brain.native_sync import cowork_runtime

    connection = tmp_path / "connection.json"
    connection.write_text(json.dumps({"invite": "secret-ticket-value", "protocol": 1}))
    launched = []
    monkeypatch.setattr(
        cowork_runtime.subprocess, "Popen", lambda argv, **kwargs: launched.append(argv)
    )
    for _ in range(5):
        schedule_enrollment(tmp_path / "state", tmp_path / "policy.json", connection)
    assert len(launched) == 1
    assert "secret-ticket-value" not in " ".join(launched[0])


def test_runtime_diagnostics_never_read_or_print_credentials(live, tmp_path):
    hub, url = live
    plugin = export_cowork_plugin(
        tmp_path / "plugin",
        policy={
            "hub": url,
            "member_id": "public-member",
            "tenant_id": "linkcrux",
        },
    )
    root = tmp_path / "memory"
    root.mkdir()
    secret = secrets.token_urlsafe(32)
    result = subprocess.run(
        [
            sys.executable,
            str(plugin / "runtime/entry.py"),
            "--policy",
            str(plugin / "runtime/connection-policy.json"),
            "--diagnose",
        ],
        env={
            **os.environ,
            "CLAUDE_PLUGIN_DATA": str(tmp_path / "data"),
            "CLAUDE_COWORK_MEMORY_PATH_OVERRIDE": str(root),
            "AMH_NATIVE_INVITE": secret,
        },
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["hub_reachable"] and report["native_memory_directory_exists"]
    assert secret not in result.stdout + result.stderr
    assert not (tmp_path / "data").exists()


def test_pairing_preflight_failure_leaves_client_configuration_untouched(
    live, tmp_path, monkeypatch
):
    from agent_brain.native_sync import cli, cowork_runtime
    from typer.testing import CliRunner

    hub, url = live
    c = NativeClient.join(tmp_path / "state", url, hub.invite("linkcrux", "member"), "host")
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    config = home / ".codex/config.toml"
    config.write_text('model = "keep-this-config"\n')
    monkeypatch.setattr(
        cli,
        "discover_products",
        lambda home: [
            {"product": "cowork", "name": "Claude Cowork", "detected": True, "roots": []},
            {"product": "codex-cli", "name": "Codex CLI", "detected": True, "roots": []},
        ],
    )
    monkeypatch.setattr(cli, "discover_assets", lambda *args: [])

    def unavailable(*args):
        raise ValueError("pairing unavailable")

    monkeypatch.setattr(cowork_runtime, "export_connection", unavailable)
    result = CliRunner().invoke(
        cli.app,
        ["setup", "--hub", url, "--state", str(c.root), "--home", str(home), "--no-service"],
        input="y\ny\ny\n",
    )
    assert result.exit_code == 1
    assert config.read_text() == 'model = "keep-this-config"\n'
    assert not (home / ".codex/hooks.json").exists()
