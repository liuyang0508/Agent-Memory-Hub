from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

from agent_brain.agent_integrations import claude_code as cc_mod
from agent_brain.agent_integrations import codex as cx_mod
from agent_brain.agent_integrations.awareness import BEGIN as AWARENESS_BEGIN
from agent_brain.agent_integrations.awareness import END as AWARENESS_END
from agent_brain.agent_integrations.codex_config import BEGIN as CODEX_BEGIN
from agent_brain.agent_integrations.codex_config import END as CODEX_END
from agent_brain.agent_integrations.codex_config import MCP_SECTION
from agent_brain.agent_integrations.diagnostics import (
    AdapterDiagnosticCheck,
    AdapterDiagnosticReport,
)
from agent_brain.platform.adapter_health import (
    CoreAdapterHealth,
    bounded_diagnostic_text,
    diagnose_configured_core_adapters,
    has_managed_footprint,
)


CURRENT_HOOK = "/repo/agent_runtime_kit/hooks/inject-context.sh"
LEGACY_HOOK = "/repo/brain/hooks/inject-context.sh"


def _patch_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    paths = {
        "codex_agents": tmp_path / ".codex" / "AGENTS.md",
        "codex_hooks": tmp_path / ".codex" / "hooks.json",
        "codex_config": tmp_path / ".codex" / "config.toml",
        "claude_settings": tmp_path / ".claude" / "settings.json",
        "claude_awareness": tmp_path / ".claude" / "CLAUDE.md",
    }
    monkeypatch.setattr(cx_mod, "AGENTS_MD", paths["codex_agents"])
    monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", paths["codex_hooks"])
    monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", paths["codex_config"])
    monkeypatch.setattr(cc_mod, "SETTINGS_PATH", paths["claude_settings"])
    monkeypatch.setattr(cc_mod, "AWARENESS_PATH", paths["claude_awareness"])
    return paths


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _hook_payload(command: str) -> str:
    return json.dumps(
        {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": command}]}]}}
    )


@pytest.mark.parametrize(
    ("variant", "content"),
    [
        ("agents_begin", CODEX_BEGIN),
        ("agents_end", CODEX_END),
        ("mcp", f"  {MCP_SECTION}  \ncommand = 'memory'\n"),
        ("hook", _hook_payload(CURRENT_HOOK)),
        ("legacy_hook", _hook_payload(LEGACY_HOOK)),
    ],
)
def test_has_managed_footprint_codex_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
    content: str,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    target = {
        "agents_begin": paths["codex_agents"],
        "agents_end": paths["codex_agents"],
        "mcp": paths["codex_config"],
        "hook": paths["codex_hooks"],
        "legacy_hook": paths["codex_hooks"],
    }[variant]
    _write(target, content)

    assert has_managed_footprint("codex") is True


@pytest.mark.parametrize(
    ("variant", "content"),
    [
        ("awareness_begin", AWARENESS_BEGIN),
        ("awareness_end", AWARENESS_END),
        (
            "mcp",
            json.dumps({"mcpServers": {"agent-memory-hub": {"command": "memory"}}}),
        ),
        ("hook", _hook_payload(CURRENT_HOOK)),
        ("legacy_hook", _hook_payload(LEGACY_HOOK)),
    ],
)
def test_has_managed_footprint_claude_code_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
    content: str,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    target = (
        paths["claude_awareness"] if variant.startswith("awareness") else paths["claude_settings"]
    )
    _write(target, content)

    assert has_managed_footprint("claude_code") is True


def test_valid_hooks_json_ignores_markers_outside_hook_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    payload = json.dumps(
        {
            "description": CURRENT_HOOK,
            "command": LEGACY_HOOK,
            "hooks": {"Stop": [{"description": CURRENT_HOOK, "hooks": []}]},
        }
    )
    _write(paths["codex_hooks"], payload)
    _write(paths["claude_settings"], payload)

    assert has_managed_footprint("codex") is False
    assert has_managed_footprint("claude_code") is False


def test_codex_mcp_comment_is_not_a_managed_section(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(paths["codex_config"], f"# {MCP_SECTION}\nmodel = 'gpt-5'\n")

    assert has_managed_footprint("codex") is False


def test_has_managed_footprint_skips_non_amh_and_unknown_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(paths["codex_agents"], "# user instructions\n")
    _write(paths["codex_hooks"], _hook_payload("/user/hooks/custom.sh"))
    _write(paths["codex_config"], "model = 'gpt-5'\n")
    _write(paths["claude_awareness"], "# user instructions\n")
    _write(
        paths["claude_settings"],
        json.dumps(
            {
                "hooks": {"Stop": []},
                "mcpServers": {"user-server": {"command": "user-tool"}},
            }
        ),
    )

    assert has_managed_footprint("codex") is False
    assert has_managed_footprint("claude_code") is False
    assert has_managed_footprint("other") is False


@pytest.mark.parametrize("adapter_name", ["codex", "claude_code"])
def test_malformed_json_without_amh_marker_is_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    adapter_name: str,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    target = paths["codex_hooks"] if adapter_name == "codex" else paths["claude_settings"]
    _write(target, '{"hooks": {')

    assert has_managed_footprint(adapter_name) is False


@pytest.mark.parametrize("adapter_name", ["codex", "claude_code"])
def test_malformed_owned_hook_json_is_diagnosed_not_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    adapter_name: str,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    target = paths["codex_hooks"] if adapter_name == "codex" else paths["claude_settings"]
    _write(target, f'{{"hooks": "{CURRENT_HOOK}"')
    brain = tmp_path / "brain"
    (brain / "items").mkdir(parents=True)

    reports = diagnose_configured_core_adapters(brain)

    assert [report.adapter for report in reports] == [adapter_name]
    assert reports[0].status == "error"
    assert any("malformed" in check.detail for check in reports[0].non_ok_checks)


def test_malformed_owned_mcp_json_is_diagnosed_not_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(paths["claude_settings"], '{"mcpServers": {"agent-memory-hub": ')
    brain = tmp_path / "brain"
    (brain / "items").mkdir(parents=True)

    reports = diagnose_configured_core_adapters(brain)

    assert [report.adapter for report in reports] == ["claude_code"]
    assert reports[0].status == "error"
    assert any("malformed" in check.detail for check in reports[0].non_ok_checks)


def test_diagnose_only_configured_adapters_and_keeps_non_ok_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(paths["codex_agents"], CODEX_BEGIN)
    brain = tmp_path / "brain"
    brain.mkdir()
    discovered: list[bool] = []
    requested: list[tuple[str, Path]] = []

    ok_check = AdapterDiagnosticCheck("ok", "ok", "healthy")
    warn_check = AdapterDiagnosticCheck("warn", "warn", "runtime evidence missing")
    error_check = AdapterDiagnosticCheck("error", "error", "hook is not trusted")

    class FakeAdapter:
        def diagnose(self) -> AdapterDiagnosticReport:
            return AdapterDiagnosticReport(
                adapter="codex",
                overall_status="error",
                checks=[ok_check, warn_check, error_check],
                brain_dir=brain,
            )

    from agent_brain import agent_integrations
    from agent_brain.agent_integrations import registry

    monkeypatch.setattr(agent_integrations, "discover_adapters", lambda: discovered.append(True))

    def get_adapter(name: str, brain_dir: Path) -> FakeAdapter:
        requested.append((name, brain_dir))
        return FakeAdapter()

    monkeypatch.setattr(registry, "get_adapter", get_adapter)

    reports = diagnose_configured_core_adapters(brain)

    assert discovered == [True]
    assert requested == [("codex", brain)]
    assert reports == (
        CoreAdapterHealth(
            adapter="codex",
            status="error",
            non_ok_checks=(warn_check, error_check),
        ),
    )


def test_diagnose_preserves_ok_status_with_no_non_ok_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(paths["claude_awareness"], AWARENESS_BEGIN)
    brain = tmp_path / "brain"
    brain.mkdir()
    report = AdapterDiagnosticReport(
        adapter="claude_code",
        overall_status="ok",
        checks=[AdapterDiagnosticCheck("settings", "ok", "healthy")],
        brain_dir=brain,
    )

    from agent_brain.agent_integrations import registry

    monkeypatch.setattr(registry, "get_adapter", lambda name, brain_dir: _Adapter(report))

    reports = diagnose_configured_core_adapters(brain)

    assert reports == (CoreAdapterHealth("claude_code", "ok", ()),)


def test_diagnose_checks_all_configured_core_adapters_in_stable_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(paths["codex_agents"], CODEX_BEGIN)
    _write(paths["claude_awareness"], AWARENESS_BEGIN)
    brain = tmp_path / "brain"
    brain.mkdir()

    def get_adapter(name: str, brain_dir: Path) -> _Adapter:
        report = AdapterDiagnosticReport(
            adapter=name,
            overall_status="warn",
            checks=[AdapterDiagnosticCheck("runtime", "warn", "not observed")],
            brain_dir=brain_dir,
        )
        return _Adapter(report)

    from agent_brain.agent_integrations import registry

    monkeypatch.setattr(registry, "get_adapter", get_adapter)

    reports = diagnose_configured_core_adapters(brain)

    assert [report.adapter for report in reports] == ["codex", "claude_code"]


def test_diagnose_exception_becomes_bounded_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(paths["codex_agents"], CODEX_BEGIN)
    brain = tmp_path / "brain"
    brain.mkdir()

    class BrokenAdapter:
        def diagnose(self) -> AdapterDiagnosticReport:
            raise RuntimeError("bad\x00\tline\n" + "x" * 2000)

    from agent_brain.agent_integrations import registry

    monkeypatch.setattr(registry, "get_adapter", lambda name, brain_dir: BrokenAdapter())

    reports = diagnose_configured_core_adapters(brain)

    assert reports[0].status == "error"
    assert len(reports[0].non_ok_checks) == 1
    check = reports[0].non_ok_checks[0]
    assert check.name == "codex adapter doctor"
    assert check.fix == "run: memory adapter install codex"
    assert "\x00" not in check.detail
    assert "\tline\n" in check.detail
    assert len(check.detail) == 1200
    assert check.detail.endswith("…")


def test_core_adapter_health_is_immutable() -> None:
    health = CoreAdapterHealth("codex", "ok", ())

    with pytest.raises(FrozenInstanceError):
        health.status = "error"  # type: ignore[misc]


def test_bounded_diagnostic_text_preserves_lines_tabs_and_limits_length() -> None:
    value = "line one\nline\ttwo\x00\x1b" + "z" * 2000

    result = bounded_diagnostic_text(value)

    assert result.startswith("line one\nline\ttwo  ")
    assert "\x00" not in result
    assert "\x1b" not in result
    assert len(result) == 1200
    assert result.endswith("…")


class _Adapter:
    def __init__(self, report: AdapterDiagnosticReport) -> None:
        self._report = report

    def diagnose(self) -> AdapterDiagnosticReport:
        return self._report
