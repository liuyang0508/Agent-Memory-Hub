"""Explicit installation repair commands.

These tests pin the release-support behavior that users need after a checkout,
release asset, or hook path drifts: diagnosis stays read-only by default, while
``memory self-update`` and ``memory doctor --fix`` are explicit repair entry points.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from agent_brain.interfaces.cli import app

runner = CliRunner()


def _combined(result) -> str:
    out = result.output or ""
    try:
        out += result.stderr
    except (ValueError, Exception):
        pass
    return out


def test_update_dry_run_reports_installer_without_running(monkeypatch):
    called = False

    def fake_run_installer(*args, **kwargs):  # pragma: no cover - should not run
        nonlocal called
        called = True
        raise AssertionError("dry-run should not execute installer")

    import agent_brain.platform.install_repair as repair

    monkeypatch.setattr(repair, "run_installer", fake_run_installer)

    result = runner.invoke(app, ["self-update", "--dry-run"])

    assert result.exit_code == 0, _combined(result)
    assert called is False
    assert "install.sh" in result.output
    assert "--minimal" in result.output
    assert "dry-run" in result.output.lower()


def test_update_repair_hooks_dry_run_reports_core_adapter_repairs(monkeypatch, tmp_path):
    import agent_brain.platform.install_repair as repair

    monkeypatch.setenv("BRAIN_DIR", str(tmp_path / "brain"))
    monkeypatch.setattr(
        repair,
        "run_installer",
        lambda *args, **kwargs: [repair.RepairAction("installer", "planned", "mock")],
    )
    monkeypatch.setattr(
        repair,
        "repair_memory_cli_shim",
        lambda *args, **kwargs: [repair.RepairAction("memory CLI shim", "planned", "mock")],
    )
    monkeypatch.setattr(
        repair,
        "repair_adapters",
        lambda *args, **kwargs: [
            repair.RepairAction("codex adapter", "planned", "mock"),
            repair.RepairAction("claude_code adapter", "planned", "mock"),
        ],
    )

    result = runner.invoke(app, ["self-update", "--repair-hooks", "--dry-run"])

    assert result.exit_code == 0, _combined(result)
    assert "memory CLI shim" in result.output
    assert "codex adapter" in result.output
    assert "claude_code adapter" in result.output


def test_doctor_fix_rejects_offline_mode():
    result = runner.invoke(app, ["doctor", "--offline", "--fix"])

    assert result.exit_code == 2
    assert "--fix cannot be combined with --offline" in _combined(result)


def test_doctor_fix_repairs_broken_cli_shim_and_core_hooks(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import claude_code as cc_mod
    from agent_brain.agent_integrations import codex as cx_mod

    home = tmp_path / "home"
    brain = tmp_path / "brain"
    shim = home / ".local" / "bin" / "memory"
    stale_target = tmp_path / "deleted" / ".venv" / "bin" / "memory"
    repo = tmp_path / "repo"
    venv_memory = repo / ".venv" / "bin" / "memory"
    venv_memory.parent.mkdir(parents=True)
    venv_memory.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    venv_memory.chmod(0o755)
    (brain / "items").mkdir(parents=True)
    shim.parent.mkdir(parents=True)
    shim.write_text(f'#!/bin/sh\nexec "{stale_target}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    monkeypatch.setattr(cx_mod, "AGENTS_MD", tmp_path / ".codex" / "AGENTS.md")
    monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", tmp_path / ".codex" / "hooks.json")
    monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", tmp_path / ".codex" / "config.toml")
    monkeypatch.setattr(cc_mod, "SETTINGS_PATH", tmp_path / ".claude" / "settings.json")
    monkeypatch.setattr(cc_mod, "AWARENESS_PATH", tmp_path / ".claude" / "CLAUDE.md")

    import agent_brain.platform.install_repair as repair

    monkeypatch.setattr(repair, "repo_root", lambda: repo)

    result = runner.invoke(app, ["doctor", "--fix"])

    assert result.exit_code == 0, _combined(result)
    assert f'exec "{venv_memory}" "$@"' in shim.read_text(encoding="utf-8")
    assert (tmp_path / ".codex" / "hooks.json").exists()
    assert (tmp_path / ".claude" / "settings.json").exists()
    assert "Installation Repair" in result.output
    assert "memory CLI shim" in result.output
    assert "codex adapter" in result.output
    assert "claude_code adapter" in result.output
