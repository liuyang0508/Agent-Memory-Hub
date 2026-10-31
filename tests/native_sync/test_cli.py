import json
from typer.testing import CliRunner
from agent_brain.native_sync.cli import app

runner = CliRunner()


def test_cli_admin_token_and_status_do_not_mix_with_legacy(tmp_path):
    db = tmp_path / "hub.sqlite"
    result = runner.invoke(app, ["admin", "--db", str(db), "--label", "运维"])
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.output)["admin"]) > 20
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


def test_cowork_archive_excludes_existing_private_files(tmp_path):
    import zipfile
    from agent_brain.native_sync.install import export_cowork_plugin

    root = tmp_path / "plugin"
    root.mkdir()
    (root / "config.json").write_text('{"token":"DO_NOT_PACK"}')
    export_cowork_plugin(root)
    archive = root.with_suffix(".zip")
    assert archive.is_file()
    with zipfile.ZipFile(archive) as bundle:
        assert ".claude-plugin/plugin.json" in bundle.namelist()
        assert "config.json" not in bundle.namelist()
        assert not any(b"DO_NOT_PACK" in bundle.read(name) for name in bundle.namelist())


def test_join_failure_does_not_print_invitation_from_traceback_locals(tmp_path):
    import os
    import subprocess
    import sys
    import secrets

    secret = secrets.token_urlsafe(32)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_brain.native_sync",
            "join",
            "--hub",
            "http://127.0.0.1:9",
            "--state",
            str(tmp_path / "state"),
        ],
        env={**os.environ, "AMH_NATIVE_INVITE": secret},
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert secret not in result.stdout + result.stderr
