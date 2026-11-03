"""Product registrations use installed stdio code and reversible owned entries."""

import json
import tomllib
import zipfile

import pytest

from agent_brain.native_sync import install


def test_mixed_client_mcp_entries_survive_install_and_exact_uninstall(tmp_path):
    home, state = tmp_path / "home", tmp_path / "state"
    (home / ".codex").mkdir(parents=True)
    (home / ".claude").mkdir()
    codex = home / ".codex/config.toml"
    claude = home / ".claude.json"
    original_codex = '# user comment\nmodel="keep"\n[mcp_servers.other]\ncommand="other-server"\nargs=["--existing"]\n'
    original_claude = {
        "preferences": {"theme": "dark"},
        "projects": {"/work": {"mcpServers": {"local": {"command": "local"}}}},
        "mcpServers": {"other": {"command": "other-server", "args": ["--existing"]}},
    }
    codex.write_text(original_codex)
    claude.write_text(json.dumps(original_claude))
    install.install_hooks(home, state, products=["codex-cli", "claude-code"])
    install.install_hooks(home, state, products=["codex-desktop", "claude-code"])
    cc = json.loads(claude.read_text())
    cx = tomllib.loads(codex.read_text())
    assert cc["mcpServers"]["other"] == original_claude["mcpServers"]["other"]
    assert cc["projects"] == original_claude["projects"]
    assert cx["mcp_servers"]["other"] == {"command": "other-server", "args": ["--existing"]}
    # The two entries differ only in the product label the server reports when asking.
    assert cc["mcpServers"]["amh-org"]["command"] == cx["mcp_servers"]["amh-org"]["command"]
    assert cc["mcpServers"]["amh-org"]["args"][:-1] == cx["mcp_servers"]["amh-org"]["args"][:-1]
    for server, product in [
        (cc["mcpServers"]["amh-org"], "claude-code"),
        (cx["mcp_servers"]["amh-org"], "codex-cli"),
    ]:
        assert set(server) == {"command", "args"}
        assert "agent_brain.native_sync.org_mcp" in " ".join(server["args"])
        assert server["args"][-4:] == ["--state", str(state.resolve()), "--product", product]
    assert "mcpServers" not in json.loads((home / ".claude/settings.json").read_text())
    assert install.uninstall_hooks(home, state) == []
    assert codex.read_text() == original_codex
    assert json.loads(claude.read_text()) == original_claude


def test_uninstall_removes_only_owned_mcp_when_user_edits_other_settings(tmp_path):
    home, state = tmp_path / "home", tmp_path / "state"
    install.install_hooks(home, state, products=["codex-cli", "claude-code"])
    codex, claude = home / ".codex/config.toml", home / ".claude.json"
    codex.write_text(codex.read_text() + '\n[mcp_servers.later]\ncommand="later"\n')
    data = json.loads(claude.read_text())
    data["mcpServers"]["later"] = {"command": "later"}
    data["recentTheme"] = "new"
    claude.write_text(json.dumps(data))
    conflicts = install.uninstall_hooks(home, state)
    assert str(codex) in conflicts and str(claude) in conflicts
    cx, cc = tomllib.loads(codex.read_text()), json.loads(claude.read_text())
    assert "amh-org" not in cx["mcp_servers"] and "amh-org" not in cc["mcpServers"]
    assert cx["mcp_servers"]["later"]["command"] == "later"
    assert cc["mcpServers"]["later"]["command"] == "later"
    assert cc["recentTheme"] == "new"


@pytest.mark.parametrize("client", ["codex-cli", "claude-code"])
def test_reinstall_and_uninstall_protect_user_edits_to_owned_entry(tmp_path, client):
    home, state = tmp_path / "home", tmp_path / "state"
    install.install_hooks(home, state, products=[client])
    if client == "claude-code":
        path = home / ".claude.json"
        data = json.loads(path.read_text())
        data["mcpServers"]["amh-org"]["command"] = "user-custom-command"
        path.write_text(json.dumps(data))
    else:
        import tomlkit

        path = home / ".codex/config.toml"
        data = tomlkit.parse(path.read_text())
        data["mcp_servers"]["amh-org"]["command"] = "user-custom-command"
        path.write_text(tomlkit.dumps(data))
    edited = path.read_bytes()
    with pytest.raises(ValueError, match="edited"):
        install.install_hooks(home, state, products=[client])
    assert path.read_bytes() == edited
    assert str(path) in install.uninstall_hooks(home, state)
    assert path.read_bytes() == edited


def test_invalid_user_mcp_config_aborts_before_mutating_other_client(tmp_path):
    home, state = tmp_path / "home", tmp_path / "state"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude/settings.json").write_text('{"model":"keep"}')
    (home / ".claude.json").write_text('{"mcpServers":[]}')
    with pytest.raises(ValueError, match="MCP configuration"):
        install.install_hooks(home, state, products=["claude-code", "codex-cli"])
    assert (home / ".claude/settings.json").read_text() == '{"model":"keep"}'
    assert not (home / ".codex/config.toml").exists()
    assert not (state / "install-receipt.json").exists()


def test_foreign_same_name_entry_is_not_silently_overwritten(tmp_path):
    home, state = tmp_path / "home", tmp_path / "state"
    home.mkdir()
    path = home / ".claude.json"
    path.write_text('{"mcpServers":{"amh-org":{"command":"some-other-server"}}}')
    before = path.read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        install.install_hooks(home, state, products=["claude-code"])
    assert path.read_bytes() == before
    assert not (home / ".claude/settings.json").exists()


def test_bound_cowork_manifest_declares_installed_stdio_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(install.platform, "system", lambda: "Darwin")
    state = tmp_path / "host-state"
    state.mkdir()
    (state / "config.json").write_text('{"identity":{"token":"DO_NOT_PACKAGE_DEVICE_TOKEN"}}')
    connection = tmp_path / "connection.json"
    connection.write_text('{"ticket":"DO_NOT_PACKAGE_TICKET"}')
    plugin = install.export_cowork_plugin(
        tmp_path / "plugin",
        runtime_state=state,
        connection_file=connection,
        policy={"hub": "https://hub.example", "member_id": "member", "tenant_id": "org"},
    )
    manifest = json.loads((plugin / ".claude-plugin/plugin.json").read_text())
    server = manifest["mcpServers"]["amh-org"]
    assert set(server) == {"command", "args"}
    assert server["args"][-6:] == [
        "--state",
        str(state.resolve()),
        "--policy",
        "${CLAUDE_PLUGIN_ROOT}/runtime/connection-policy.json",
        "--product",
        "cowork",
    ]
    assert "agent_brain.native_sync.org_mcp" in " ".join(server["args"])
    with zipfile.ZipFile(str(plugin) + ".zip") as bundle:
        raw = b"\n".join(bundle.read(name) for name in bundle.namelist())
        assert b"DO_NOT_PACKAGE_DEVICE_TOKEN" not in raw
        assert b"DO_NOT_PACKAGE_TICKET" not in raw
        assert not any(name.endswith("/org_mcp.py") for name in bundle.namelist())
        assert not any("site-packages" in name for name in bundle.namelist())
    assert "not_connected" in (plugin / "commands/doctor.md").read_text()


@pytest.mark.parametrize("platform,binding", [("Darwin", False), ("Linux", True)])
def test_generic_or_nonmac_plugin_does_not_claim_mcp_is_connected(
    tmp_path, monkeypatch, platform, binding
):
    monkeypatch.setattr(install.platform, "system", lambda: platform)
    plugin = install.export_cowork_plugin(
        tmp_path / "plugin", runtime_state=tmp_path / "state" if binding else None
    )
    manifest = json.loads((plugin / ".claude-plugin/plugin.json").read_text())
    assert "mcpServers" not in manifest
    assert "需绑定" in manifest["description"]
    assert "未声明" in (plugin / "commands/doctor.md").read_text()


def test_uninstall_keeps_replaced_symlink_and_malformed_user_config(tmp_path):
    home, state = tmp_path / "home", tmp_path / "state"
    install.install_hooks(home, state, products=["codex-cli", "claude-code"])
    codex, claude = home / ".codex/config.toml", home / ".claude.json"
    target = tmp_path / "user-owned-config.toml"
    target.write_bytes(codex.read_bytes())
    codex.unlink()
    codex.symlink_to(target)
    claude.write_text("[malformed user edit")
    original_target = target.read_bytes()
    conflicts = install.uninstall_hooks(home, state)
    assert str(codex) in conflicts and str(claude) in conflicts
    assert codex.is_symlink()
    assert target.read_bytes() == original_target
    assert claude.read_text() == "[malformed user edit"
