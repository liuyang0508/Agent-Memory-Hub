import json
from typer.testing import CliRunner
from agent_brain.native_sync.cli import app

runner = CliRunner()


def test_cli_admin_invite_and_status_do_not_mix_with_legacy(tmp_path):
    db = tmp_path / "hub.sqlite"
    result = runner.invoke(
        app, ["invite", "--db", str(db), "--tenant", "linkcrux", "--name", "张三"]
    )
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.output)["invite"]) > 20
    result = runner.invoke(app, ["status", "--state", str(tmp_path / "unjoined")])
    assert result.exit_code != 0
    assert not (tmp_path / "unjoined/state.sqlite").exists()


def test_installer_preview_is_local_and_explicit_products(tmp_path):
    home = tmp_path / "home"
    root = home / ".claude/projects/p/memory"
    root.mkdir(parents=True)
    (root / "MEMORY.md").write_text("Local preview only")
    result = runner.invoke(app, ["discover", "--home", str(home)])
    assert result.exit_code == 0, result.output
    assert "Claude Code" in result.output and str(root) in result.output


def test_export_plugin_contains_no_device_credentials(tmp_path):
    result = runner.invoke(app, ["cowork-plugin", "--output", str(tmp_path / "plugin")])
    assert result.exit_code == 0, result.output
    hooks = json.loads((tmp_path / "plugin/hooks/hooks.json").read_text())
    assert "UserPromptSubmit" in hooks["hooks"]
    assert not (tmp_path / "plugin/config.json").exists()
