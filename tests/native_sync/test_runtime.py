import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.client import NativeClient
from agent_brain.native_sync.install import install_hooks, uninstall_hooks, discover
from agent_brain.native_sync.transport import Transport, validate_url
from test_hub import enroll, asset


@pytest.fixture
def live(tmp_path):
    import socket

    hub = Hub(tmp_path / "hub.sqlite")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(hub), log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.01)
    assert server.started
    yield hub, f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)
    sock.close()


def hook(state, event, **kwargs):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_brain.native_sync.hooks",
            "--state",
            str(state),
            "--product",
            "claude-code",
        ],
        input=json.dumps({"hook_event_name": event, "session_id": "session1", **kwargs}),
        text=True,
        capture_output=True,
        timeout=4,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout or "{}")


def test_real_http_hook_cross_member_and_no_old_brain_side_effects(live, tmp_path, monkeypatch):
    hub, url = live
    author = enroll(hub)
    hub.put(author["token"], asset())
    c = NativeClient.join(tmp_path / "client", url, hub.invite("linkcrux", "李四"), "Mac")
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path / "old-brain"))
    output = hook(c.root, "UserPromptSubmit", prompt="订单回调验签失败怎么办")
    body = output["hookSpecificOutput"]["additionalContext"]
    assert "张三" in body and "订单回调" in body
    assert author["member_id"] in body
    assert not (tmp_path / "old-brain").exists()
    assert hook(c.root, "Stop") == {}
    assert (c.root / "wake").exists()


def test_coverage_requires_transcript_evidence_and_compaction_resets(live, tmp_path):
    hub, url = live
    author = enroll(hub)
    hub.put(author["token"], asset())
    c = NativeClient.join(tmp_path / "client", url, hub.invite("linkcrux", "李四"), "Mac")
    first = hook(c.root, "UserPromptSubmit", prompt="订单回调验签")
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(json.dumps(first, ensure_ascii=False))
    second = hook(
        c.root, "UserPromptSubmit", prompt="订单回调验签", transcript_path=str(transcript)
    )
    assert not second.get("hookSpecificOutput", {}).get("additionalContext")
    hook(c.root, "PostCompact")
    # Old pre-compaction transcript must not reconfirm an expired delivery.
    again = hook(c.root, "UserPromptSubmit", prompt="订单回调验签", transcript_path=str(transcript))
    assert again["hookSpecificOutput"]["additionalContext"]


def test_revoked_device_does_not_fallback_to_cached_data(live, tmp_path):
    hub, url = live
    a = enroll(hub)
    hub.put(a["token"], asset())
    c = NativeClient.join(tmp_path / "client", url, hub.invite("linkcrux", "李四"), "Mac")
    c.sync()
    hub.revoke(c.identity["device_id"])
    assert hook(c.root, "UserPromptSubmit", prompt="订单回调") == {}


def test_hook_install_idempotent_preserves_unrelated_and_uninstall_restores(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    settings = home / ".claude/settings.json"
    original = {
        "model": "mine",
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo keep"}]}]},
    }
    settings.write_text(json.dumps(original))
    state = tmp_path / "state"
    state.mkdir()
    install_hooks(home, state, products=["claude-code"])
    install_hooks(home, state, products=["claude-code"])
    data = json.loads(settings.read_text())
    assert data["model"] == "mine"
    assert len(data["hooks"]["Stop"]) == 2
    uninstall_hooks(home, state)
    assert json.loads(settings.read_text()) == original


def test_legacy_handlers_are_disabled_in_native_mode_and_restored(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    p = home / ".claude/settings.json"
    legacy = {
        "hooks": {
            "UserPromptSubmit": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "/x/agent_runtime_kit/hooks/inject-context.sh",
                        }
                    ]
                }
            ]
        }
    }
    p.write_text(json.dumps(legacy))
    state = tmp_path / "state"
    state.mkdir()
    install_hooks(home, state, products=["claude-code"])
    assert "inject-context.sh" not in p.read_text()
    uninstall_hooks(home, state)
    assert json.loads(p.read_text()) == legacy


def test_transport_rejects_plaintext_nonlocal_and_url_credentials():
    for url in [
        "http://example.com",
        "https://" + "a:b" + "@example.com",
        "https://example.com/#token",
        "http://127.0.0.1.evil.com",
    ]:
        with pytest.raises(ValueError):
            validate_url(url)
    assert validate_url("https://memory.example.com/") == "https://memory.example.com"


def test_invalid_hook_input_fails_open_without_log_body(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_brain.native_sync.hooks",
            "--state",
            str(tmp_path),
            "--product",
            "cowork",
        ],
        input="not json secret-canary",
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "{}"
    assert "secret-canary" not in result.stdout + result.stderr


def test_portable_cowork_hook_executes_outside_repository(live, tmp_path):
    from agent_brain.native_sync.install import export_cowork_plugin

    hub, url = live
    a = enroll(hub)
    hub.put(a["token"], asset())
    c = NativeClient.join(tmp_path / "cowork-state", url, hub.invite("linkcrux", "李四"), "VM")
    plugin = export_cowork_plugin(tmp_path / "plugin")
    result = subprocess.run(
        [
            sys.executable,
            str(plugin / "runtime/entry.py"),
            "--state",
            str(c.root),
            "--product",
            "cowork",
        ],
        input=json.dumps(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "vm-test",
                "prompt": "订单回调验签",
            }
        ),
        text=True,
        capture_output=True,
        cwd=tmp_path,
        timeout=4,
    )
    assert result.returncode == 0, result.stderr
    assert "张三" in json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]


def test_codex_install_disables_only_legacy_mcp_and_restores(tmp_path):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    cfg = home / ".codex/config.toml"
    original = 'model = "mine"\n[mcp_servers.agent-memory-hub]\ncommand = "/x/legacy"\n[mcp_servers.keep]\ncommand = "keep"\n'
    cfg.write_text(original)
    state = tmp_path / "state"
    state.mkdir()
    install_hooks(home, state, products=["codex-cli"])
    assert "[mcp_servers.agent-memory-hub]" not in cfg.read_text()
    assert "[mcp_servers.keep]" in cfg.read_text()
    uninstall_hooks(home, state)
    assert cfg.read_text() == original


def test_installed_handler_imports_worktree_from_an_unrelated_cwd(live, tmp_path):
    hub, url = live
    a = enroll(hub)
    hub.put(a["token"], asset())
    c = NativeClient.join(tmp_path / "state", url, hub.invite("linkcrux", "李四"), "Mac")
    home = tmp_path / "home"
    install_hooks(home, c.root, products=["claude-code"])
    command = json.loads((home / ".claude/settings.json").read_text())["hooks"]["UserPromptSubmit"][
        0
    ]["hooks"][0]["command"]
    result = subprocess.run(
        command,
        shell=True,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        input=json.dumps(
            {"session_id": "test", "hook_event_name": "UserPromptSubmit", "prompt": "订单回调验签"}
        ),
        timeout=4,
    )
    assert result.returncode == 0, result.stderr
    assert "张三" in json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]


def test_codex_trust_uses_canonical_paths_and_only_owned_handlers(tmp_path):
    import tomllib
    from agent_brain.agent_integrations.codex_hook_trust import (
        codex_command_hook_hash,
        codex_hook_state_key,
    )

    home = tmp_path / "home"
    state = tmp_path / "state"
    state.mkdir()
    install_hooks(home, state, products=["codex-cli"])
    path = (home / ".codex/hooks.json").resolve()
    hooks = json.loads(path.read_text())["hooks"]
    cfg = tomllib.loads((home / ".codex/config.toml").read_text())
    entry = hooks["UserPromptSubmit"][0]["hooks"][0]
    key = codex_hook_state_key(path, "UserPromptSubmit", 0, 0)
    assert cfg["hooks"]["state"][key]["trusted_hash"] == codex_command_hook_hash(
        "UserPromptSubmit", matcher=None, hook=entry
    )


def test_hook_details_are_real_local_files_with_producer(live, tmp_path):
    hub, url = live
    a = enroll(hub)
    hub.put(a["token"], asset(body="DETAIL_CANARY " + ("long evidence " * 1000)))
    c = NativeClient.join(tmp_path / "state", url, hub.invite("linkcrux", "李四"), "Mac")
    result = hook(c.root, "UserPromptSubmit", prompt="DETAIL_CANARY")
    context = result["hookSpecificOutput"]["additionalContext"]
    metadata = json.loads(next(line for line in context.splitlines() if line.startswith("{")))
    p = Path(metadata["detail_file"])
    assert p.is_file() and "张三" in p.read_text()
    assert "long evidence " * 1000 in p.read_text()


def test_install_enables_native_reading_and_preserves_generation_preference(tmp_path):
    import tomllib

    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".claude").mkdir()
    codex = home / ".codex/config.toml"
    original = 'model="existing"\n[features]\nmemories=false\nother=true\n[memories]\ngenerate_memories=false\nuse_memories=false\n'
    codex.write_text(original)
    cc = home / ".claude/settings.json"
    cc.write_text(json.dumps({"autoMemoryEnabled": False, "model": "existing"}))
    state = tmp_path / "state"
    install_hooks(home, state, products=["codex-cli", "claude-code"])
    parsed = tomllib.loads(codex.read_text())
    assert parsed["features"]["memories"] is True
    assert parsed["memories"]["use_memories"] is True
    assert parsed["memories"]["generate_memories"] is False
    assert parsed["features"]["other"] is True
    assert json.loads(cc.read_text())["autoMemoryEnabled"] is True
    uninstall_hooks(home, state)
    assert codex.read_text() == original
    assert json.loads(cc.read_text())["autoMemoryEnabled"] is False


def test_native_reading_handles_inline_toml_without_losing_comments(tmp_path):
    import tomllib

    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    p = home / ".codex/config.toml"
    p.write_text(
        "# Keep this note\nfeatures = { memories = false, other = true }\nmemories = { use_memories = false, generate_memories = false }\n"
    )
    install_hooks(home, tmp_path / "state", products=["codex-cli"])
    data = tomllib.loads(p.read_text())
    assert data["features"] == {"memories": True, "other": True}
    assert data["memories"] == {"use_memories": True, "generate_memories": False}
    assert "# Keep this note" in p.read_text()


def test_invalid_second_client_config_does_not_partially_change_first(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".codex").mkdir()
    p = home / ".claude/settings.json"
    p.write_text('{"model":"keep"}')
    (home / ".codex/hooks.json").write_text("not json")
    with pytest.raises(ValueError):
        install_hooks(home, tmp_path / "state", products=["claude-code", "codex-cli"])
    assert p.read_text() == '{"model":"keep"}'


def test_new_claude_project_memory_is_discovered_after_install(live, tmp_path):
    from agent_brain.native_sync.common import atomic_json

    hub, url = live
    c = NativeClient.join(tmp_path / "state", url, hub.invite("linkcrux", "张三"), "Mac")
    projects = tmp_path / "home/.claude/projects"
    projects.mkdir(parents=True)
    c.config["discovery_roots"] = [{"root": str(projects), "product": "claude-code"}]
    atomic_json(c.root / "config.json", c.config)
    hook(c.root, "SessionStart", source="startup")
    memory = projects / "new-repo/memory"
    memory.mkdir(parents=True)
    p = memory / "new.md"
    p.write_text("NEW_PROJECT_CANARY")
    hook(
        c.root,
        "PostToolUse",
        tool_name="Write",
        tool_input={"file_path": str(p), "content": p.read_text()},
        tool_response={"type": "create"},
    )
    c.sync()
    assert hub.search(c.identity["token"], "NEW_PROJECT_CANARY")
    assert (memory / "amh-org/index.md").exists()


def test_discovered_existing_memory_remains_history_until_selected(live, tmp_path):
    from agent_brain.native_sync.common import atomic_json

    hub, url = live
    c = NativeClient.join(tmp_path / "state", url, hub.invite("linkcrux", "张三"), "Mac")
    projects = tmp_path / "home/.claude/projects"
    memory = projects / "existing/memory"
    memory.mkdir(parents=True)
    p = memory / "old.md"
    p.write_text("EXCLUDED_PROJECT_HISTORY")
    c.config["discovery_roots"] = [{"root": str(projects), "product": "claude-code"}]
    atomic_json(c.root / "config.json", c.config)
    hook(c.root, "SessionStart", source="startup")
    assert c.sources()
    p.write_text("EXCLUDED_PROJECT_HISTORY changed")
    hook(
        c.root,
        "PostToolUse",
        tool_name="Write",
        tool_input={"file_path": str(p), "content": p.read_text()},
        tool_response={"type": "update"},
    )
    c.sync()
    assert not hub.search(c.identity["token"], "EXCLUDED_PROJECT_HISTORY")


def test_discovery_does_not_promote_updates_in_unregistered_roots(live, tmp_path):
    from agent_brain.native_sync.common import atomic_json

    hub, url = live
    c = NativeClient.join(tmp_path / "state", url, hub.invite("linkcrux", "张三"), "Mac")
    projects = tmp_path / "projects"
    root = projects / "old/memory"
    root.mkdir(parents=True)
    p = root / "old.md"
    p.write_text("PRIVATE_HISTORY_UPDATED")
    c.config["discovery_roots"] = [{"root": str(projects), "product": "claude-code"}]
    atomic_json(c.root / "config.json", c.config)
    hook(
        c.root,
        "PostToolUse",
        tool_name="Write",
        tool_input={"file_path": str(p), "content": p.read_text()},
        tool_response={"type": "update"},
    )
    c.sync()
    assert not c.cached()


def test_fresh_write_is_rechecked_after_history_refresh(live, tmp_path):
    from agent_brain.native_sync.common import atomic_json

    hub, url = live
    c = NativeClient.join(tmp_path / "state", url, hub.invite("linkcrux", "张三"), "Mac")
    old = tmp_path / "old"
    old.mkdir()
    p = old / "history.md"
    p.write_text("PRIVATE_VERSION_ONE")
    c.register(old, "claude-code", "p", "memory")
    p.write_text("PRIVATE_VERSION_TWO")
    projects = tmp_path / "projects"
    new = projects / "new/memory"
    new.mkdir(parents=True)
    copy = new / "copy.md"
    copy.write_text(p.read_text())
    c.config["discovery_roots"] = [{"root": str(projects), "product": "claude-code"}]
    atomic_json(c.root / "config.json", c.config)
    hook(
        c.root,
        "PostToolUse",
        tool_name="Write",
        tool_input={"file_path": str(copy), "content": copy.read_text()},
        tool_response={"type": "create"},
    )
    c.sync()
    assert not c.cached()


def test_new_memory_capture_accepts_native_added_modified_metadata(live, tmp_path):
    from agent_brain.native_sync.common import atomic_json

    hub, url = live
    c = NativeClient.join(tmp_path / "state", url, hub.invite("linkcrux", "张三"), "Mac")
    projects = tmp_path / "projects"
    root = projects / "new/memory"
    root.mkdir(parents=True)
    authored = "---\nname: fresh\n---\nSTAMP_CANARY"
    actual = "---\nname: fresh\nmodified: 2026-10-30T00:00:00Z\n---\nSTAMP_CANARY"
    p = root / "fresh.md"
    p.write_text(actual)
    c.config["discovery_roots"] = [{"root": str(projects), "product": "claude-code"}]
    atomic_json(c.root / "config.json", c.config)
    hook(
        c.root,
        "PostToolUse",
        tool_name="Write",
        tool_input={"file_path": str(p), "content": authored},
        tool_response={"type": "create"},
    )
    c.sync()
    assert hub.search(c.identity["token"], "STAMP_CANARY")[0]["body"] == actual


def test_native_mode_preserves_non_memory_safety_hooks_even_under_same_kit(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    p = home / ".claude/settings.json"
    command = "/x/agent_runtime_kit/hooks/security-check.sh"
    p.write_text(
        json.dumps(
            {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": command}]}]}}
        )
    )
    install_hooks(home, tmp_path / "state", products=["claude-code"])
    assert command in p.read_text()
