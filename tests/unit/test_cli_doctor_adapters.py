"""CLI doctor aggregation tests for configured core adapters."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from typer.testing import CliRunner

from agent_brain.diagnostic_types import AdapterDiagnosticCheck
from agent_brain.interfaces.cli import app
from agent_brain.platform import adapter_health, install_repair
from agent_brain.platform.adapter_health import CoreAdapterHealth


runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[2]


def _doctor(tmp_path: Path, monkeypatch, *args: str):
    home = tmp_path / "home"
    brain = tmp_path / "brain"
    (brain / "items").mkdir(parents=True)
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    monkeypatch.setenv("AGENT_MEMORY_HUB_BIN", str(home / ".local" / "bin"))
    monkeypatch.setattr(
        "agent_brain.platform.doctor.probe_memory_cli_shim",
        lambda: {
            "present": False,
            "path": home / ".local" / "bin" / "memory",
            "target": None,
            "target_exists": False,
        },
    )
    return runner.invoke(app, ["doctor", *args])


def _report(
    adapter: str,
    status: str,
    *checks: AdapterDiagnosticCheck,
) -> CoreAdapterHealth:
    return CoreAdapterHealth(adapter, status, tuple(checks))  # type: ignore[arg-type]


def test_doctor_skips_unconfigured_core_adapters(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter_health, "diagnose_configured_core_adapters", lambda _brain: ())

    result = _doctor(tmp_path, monkeypatch)

    assert result.exit_code == 0, result.output
    assert "codex adapter" not in result.output
    assert "claude_code adapter" not in result.output
    assert "Claude Code hooks" not in result.output
    assert "MCP server" not in result.output


def test_doctor_warn_is_visible_but_exits_zero(tmp_path, monkeypatch):
    warning = AdapterDiagnosticCheck("runtime evidence", "warn", "no recent event", "send prompt")
    monkeypatch.setattr(
        adapter_health,
        "diagnose_configured_core_adapters",
        lambda _brain: (_report("codex", "warn", warning),),
    )

    result = _doctor(tmp_path, monkeypatch)

    assert result.exit_code == 0, result.output
    assert "codex adapter" in result.output
    assert "WARN" in result.output
    assert "runtime evidence" in result.output
    assert "no recent event" in result.output
    assert "send prompt" in result.output


def test_doctor_shows_all_adapter_errors_and_exits_one(tmp_path, monkeypatch):
    codex = AdapterDiagnosticCheck("trust", "error", "hook is not trusted", "reinstall codex")
    claude = AdapterDiagnosticCheck(
        "awareness", "error", "managed block missing", "reinstall claude"
    )
    monkeypatch.setattr(
        adapter_health,
        "diagnose_configured_core_adapters",
        lambda _brain: (
            _report("codex", "error", codex),
            _report("claude_code", "error", claude),
        ),
    )

    result = _doctor(tmp_path, monkeypatch)

    assert result.exit_code == 1, result.output
    for expected in (
        "codex",
        "claude_code",
        "hook is not trusted",
        "managed block missing",
        "reinstall codex",
        "reinstall claude",
    ):
        assert expected in result.output


def test_doctor_fix_repairs_before_rediagnosis_and_error_still_exits_one(tmp_path, monkeypatch):
    order: list[str] = []

    def repair_installation(*, brain_dir):
        order.append(f"repair:{brain_dir}")
        return [install_repair.RepairAction("codex adapter", "fixed", "rewritten")]

    def diagnose(brain_dir):
        order.append(f"diagnose:{brain_dir}")
        return (
            _report(
                "codex",
                "error",
                AdapterDiagnosticCheck("trust", "error", "still not trusted", "reinstall"),
            ),
        )

    monkeypatch.setattr(install_repair, "repair_installation", repair_installation)
    monkeypatch.setattr(adapter_health, "diagnose_configured_core_adapters", diagnose)

    result = _doctor(tmp_path, monkeypatch, "--fix")

    assert result.exit_code == 1, result.output
    assert [entry.split(":", 1)[0] for entry in order] == ["repair", "diagnose"]
    assert "still not trusted" in result.output


def test_doctor_repair_failure_exits_one_even_when_adapters_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(
        install_repair,
        "repair_installation",
        lambda **_kwargs: [install_repair.RepairAction("shim", "error", "write failed")],
    )
    monkeypatch.setattr(adapter_health, "diagnose_configured_core_adapters", lambda _brain: ())

    result = _doctor(tmp_path, monkeypatch, "--fix")

    assert result.exit_code == 1, result.output
    assert "write failed" in result.output


def test_doctor_bounds_and_renders_external_diagnostics_literally(tmp_path, monkeypatch):
    dangerous = "[bold red]literal[/bold red]\x00" + ("x" * 1400)
    check = AdapterDiagnosticCheck(dangerous, "error", dangerous, dangerous)
    monkeypatch.setattr(
        install_repair,
        "repair_installation",
        lambda **_kwargs: [install_repair.RepairAction(dangerous, "fixed", dangerous)],
    )
    monkeypatch.setattr(
        adapter_health,
        "diagnose_configured_core_adapters",
        lambda _brain: (_report("codex", "error", check),),
    )

    result = _doctor(tmp_path, monkeypatch, "--fix")

    assert result.exit_code == 1, result.output
    assert "[bold red]literal[/bold red]" in result.output
    assert "\x00" not in result.output
    assert "…" in result.output
    assert "x" * 1250 not in result.output


def test_doctor_shows_exception_check_fix(tmp_path, monkeypatch):
    check = AdapterDiagnosticCheck(
        "codex adapter doctor", "error", "diagnose exploded", "run: memory adapter install codex"
    )
    monkeypatch.setattr(
        adapter_health,
        "diagnose_configured_core_adapters",
        lambda _brain: (_report("codex", "error", check),),
    )

    result = _doctor(tmp_path, monkeypatch)

    assert result.exit_code == 1, result.output
    assert "diagnose exploded" in result.output
    assert "run:memoryadapterinstallcodex" in "".join(result.output.split())


def _cli(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agent_brain.interfaces.cli", *args],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )


def test_general_doctor_matches_codex_trust_failure_in_fresh_process(tmp_path):
    home = tmp_path / "home"
    brain = tmp_path / "brain"
    (brain / "items").mkdir(parents=True)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "BRAIN_DIR": str(brain),
            "AGENT_MEMORY_HUB_BIN": str(tmp_path / "bin"),
            "PYTHONPATH": str(REPO_ROOT),
            "MEMORY_HUB_TEST_EMBEDDING": "1",
        }
    )

    installed = _cli(env, "adapter", "install", "codex")
    assert installed.returncode == 0, installed.stdout

    hooks_path = home / ".codex" / "hooks.json"
    hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
    changed = False
    for entries in hooks["hooks"].values():
        for entry in entries:
            for hook in entry.get("hooks", []):
                command = str(hook.get("command", ""))
                if "inject-context.sh" in command:
                    hook["command"] = "AGENT_MEMORY_HUB_HOOK_TRACE_EMPTY=1 " + command
                    changed = True
    assert changed is True
    hooks_path.write_text(json.dumps(hooks, indent=2) + "\n", encoding="utf-8")

    adapter_bad = _cli(env, "adapter", "doctor", "codex", "--format", "json")
    general_bad = _cli(env, "doctor")
    assert adapter_bad.returncode == 1, adapter_bad.stdout
    assert general_bad.returncode == 1, general_bad.stdout
    assert "not trusted" in adapter_bad.stdout
    assert "not trusted" in general_bad.stdout
    assert "run: memory adapter install codex" in general_bad.stdout

    repaired = _cli(env, "adapter", "install", "codex")
    adapter_good = _cli(env, "adapter", "doctor", "codex", "--format", "json")
    general_good = _cli(env, "doctor")
    assert repaired.returncode == 0, repaired.stdout
    assert adapter_good.returncode == 0, adapter_good.stdout
    assert general_good.returncode == 0, general_good.stdout
