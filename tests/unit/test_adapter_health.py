from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace

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
from agent_brain.agent_integrations.hook_config import adapter_hook_command
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


def _run_isolated_python(tmp_path: Path, code: str) -> subprocess.CompletedProcess[str]:
    repo = Path(__file__).resolve().parents[2]
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "PYTHONPATH": str(repo),
        }
    )
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
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


@pytest.mark.parametrize(
    "payload",
    [
        {"hooks": CURRENT_HOOK},
        {"hooks": {"UserPromptSubmit": {"hooks": [{"type": "command", "command": CURRENT_HOOK}]}}},
        {"hooks": {"UserPromptSubmit": [{"hooks": {"type": "command", "command": CURRENT_HOOK}}]}},
        {"hooks": {"UserPromptSubmit": [{"hooks": CURRENT_HOOK}]}},
        {"hooks": {"UserPromptSubmit": [{"hooks": [CURRENT_HOOK]}]}},
    ],
)
@pytest.mark.parametrize("adapter_name", ["codex", "claude_code"])
def test_valid_but_structurally_damaged_owned_hooks_are_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    adapter_name: str,
    payload: dict[str, object],
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    target = paths["codex_hooks"] if adapter_name == "codex" else paths["claude_settings"]
    _write(target, json.dumps(payload))

    assert has_managed_footprint(adapter_name) is True


@pytest.mark.parametrize("adapter_name", ["codex", "claude_code"])
def test_valid_damaged_hooks_ignore_description_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    adapter_name: str,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    target = paths["codex_hooks"] if adapter_name == "codex" else paths["claude_settings"]
    payload = {
        "hooks": {
            "UserPromptSubmit": {
                "description": CURRENT_HOOK,
                "metadata": {"path": LEGACY_HOOK},
            }
        }
    }
    _write(target, json.dumps(payload))

    assert has_managed_footprint(adapter_name) is False


@pytest.mark.parametrize("adapter_name", ["codex", "claude_code"])
def test_malformed_json_ignores_marker_in_description(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    adapter_name: str,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    target = paths["codex_hooks"] if adapter_name == "codex" else paths["claude_settings"]
    _write(target, f'{{"description": "{CURRENT_HOOK}"')

    assert has_managed_footprint(adapter_name) is False


@pytest.mark.parametrize("field", ["command", "hooks"])
@pytest.mark.parametrize("adapter_name", ["codex", "claude_code"])
def test_malformed_json_accepts_restricted_owned_hook_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    adapter_name: str,
    field: str,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    target = paths["codex_hooks"] if adapter_name == "codex" else paths["claude_settings"]
    _write(target, f'{{"{field}": "{CURRENT_HOOK}"')

    assert has_managed_footprint(adapter_name) is True


@pytest.mark.parametrize("adapter_name", ["codex", "claude_code"])
@pytest.mark.parametrize("legacy", [False, True])
def test_real_adapter_hook_commands_with_spaced_paths_are_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    adapter_name: str,
    legacy: bool,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    target = paths["codex_hooks"] if adapter_name == "codex" else paths["claude_settings"]
    hook_dir = "brain" if legacy else "agent_runtime_kit"
    script = Path(f"/repo with space/{hook_dir}/hooks/inject-context.sh")
    command = adapter_hook_command(adapter_name, script)
    payload = {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "startup|resume" if adapter_name == "codex" else "",
                    "hooks": [
                        {"type": "command", "command": "/user/hooks/foreign.sh"},
                        {"type": "command", "command": command},
                    ],
                }
            ]
        }
    }
    _write(target, json.dumps(payload))

    assert has_managed_footprint(adapter_name) is True


@pytest.mark.parametrize(
    "command",
    [
        r"C:\repo\agent_runtime_kit\hooks\inject-context.sh",
        r"C:\repo\brain\hooks\inject-context.sh",
    ],
)
@pytest.mark.parametrize("adapter_name", ["codex", "claude_code"])
def test_windows_current_and_legacy_hook_commands_are_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    adapter_name: str,
    command: str,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    target = paths["codex_hooks"] if adapter_name == "codex" else paths["claude_settings"]
    _write(target, _hook_payload(command))

    assert has_managed_footprint(adapter_name) is True


def test_codex_mcp_comment_is_not_a_managed_section(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(paths["codex_config"], f"# {MCP_SECTION}\nmodel = 'gpt-5'\n")

    assert has_managed_footprint("codex") is False


def test_codex_valid_mcp_table_with_inline_comment_is_detected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(
        paths["codex_config"],
        f"{MCP_SECTION} # managed by AMH\ncommand = 'memory'\n",
    )

    assert has_managed_footprint("codex") is True


def test_codex_mcp_text_inside_valid_multiline_string_is_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(
        paths["codex_config"],
        f"notes = '''\n{MCP_SECTION}\nnot a managed table\n'''\n",
    )

    assert has_managed_footprint("codex") is False


def test_codex_mcp_key_with_wrong_value_type_is_still_a_footprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(paths["codex_config"], 'mcp_servers."agent-memory-hub" = "broken"\n')

    assert has_managed_footprint("codex") is True


def test_codex_malformed_owned_toml_uses_restricted_header_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(
        paths["codex_config"],
        f"{MCP_SECTION} # managed by AMH\ncommand = \n",
    )

    assert has_managed_footprint("codex") is True


@pytest.mark.parametrize("delimiter", ["'''", '"""'])
def test_codex_malformed_toml_ignores_headers_inside_multiline_strings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    delimiter: str,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    content = f"notes = {delimiter}\n{MCP_SECTION}\n{delimiter}\nbroken =\n"
    _write(paths["codex_config"], content)

    assert has_managed_footprint("codex") is False


@pytest.mark.parametrize(
    "header",
    [
        MCP_SECTION,
        '[mcp_servers."agent-memory-hub"]',
    ],
)
def test_codex_malformed_toml_accepts_semantic_managed_table_headers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    header: str,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(
        paths["codex_config"],
        f"{header} # managed by AMH\ncommand = \n",
    )

    assert has_managed_footprint("codex") is True


def test_codex_malformed_toml_accepts_root_dotted_mcp_assignment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(
        paths["codex_config"],
        'mcp_servers."agent-memory-hub" = "broken"\nbroken =\n',
    )

    assert has_managed_footprint("codex") is True


def test_codex_malformed_toml_rejects_dotted_mcp_assignment_inside_other_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(
        paths["codex_config"],
        ('[metadata]\nmcp_servers."agent-memory-hub" = "not-root"\nbroken =\n'),
    )

    assert has_managed_footprint("codex") is False


@pytest.mark.parametrize("adapter_name", ["codex", "claude_code"])
def test_utf8_bom_valid_json_does_not_fall_back_to_raw_marker_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    adapter_name: str,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    target = paths["codex_hooks"] if adapter_name == "codex" else paths["claude_settings"]
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"command": CURRENT_HOOK}).encode("utf-8")
    target.write_bytes(b"\xef\xbb\xbf" + payload)

    assert has_managed_footprint(adapter_name) is False


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


@pytest.mark.parametrize("server_value", [None, "broken", [], 42])
def test_claude_mcp_key_is_a_footprint_regardless_of_value_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    server_value: object,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(
        paths["claude_settings"],
        json.dumps({"mcpServers": {"agent-memory-hub": server_value}}),
    )

    assert has_managed_footprint("claude_code") is True


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


@pytest.mark.parametrize(
    "content",
    [
        '{"mcpServers": {"agent-memory-hub": ',
        ('{"mcpServers": {"other": {"command": "tool"}, "agent-memory-hub": '),
    ],
)
def test_malformed_owned_mcp_json_is_diagnosed_not_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: str,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(paths["claude_settings"], content)
    brain = tmp_path / "brain"
    (brain / "items").mkdir(parents=True)

    reports = diagnose_configured_core_adapters(brain)

    assert [report.adapter for report in reports] == ["claude_code"]
    assert reports[0].status == "error"
    assert any("malformed" in check.detail for check in reports[0].non_ok_checks)


@pytest.mark.parametrize(
    "content",
    [
        '{"metadata": {"mcpServers": {"agent-memory-hub": null}}, "broken": ',
        '{"first": true} {"mcpServers": {"agent-memory-hub": ',
        ('{"note": "\\"mcpServers\\": {\\"agent-memory-hub\\": null}", "broken": '),
        '{"mcpServers": {"wrapper": {"agent-memory-hub": null}}, "broken": ',
    ],
)
def test_malformed_mcp_fallback_rejects_non_root_or_non_direct_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: str,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(paths["claude_settings"], content)

    assert has_managed_footprint("claude_code") is False


def test_malformed_mcp_fallback_handles_deep_repeated_nested_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    content = '{"metadata": ' + '{"mcpServers": ' * 500 + '{"agent-memory-hub": null'
    _write(paths["claude_settings"], content)

    assert has_managed_footprint("claude_code") is False


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


def test_footprint_parser_exception_becomes_bounded_adapter_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_paths(tmp_path, monkeypatch)
    brain = tmp_path / "brain"
    brain.mkdir()

    from agent_brain.platform import adapter_health

    def fail_toml(*args: object, **kwargs: object) -> bool:
        raise RuntimeError("footprint\x00 failed " + "x" * 2000)

    monkeypatch.setattr(adapter_health, "_toml_mcp_footprint", fail_toml)

    reports = diagnose_configured_core_adapters(brain)

    assert [report.adapter for report in reports] == ["codex"]
    assert reports[0].status == "error"
    detail = reports[0].non_ok_checks[0].detail
    assert "\x00" not in detail
    assert len(detail) == 1200
    assert detail.endswith("…")


@pytest.mark.parametrize(
    "read_error",
    [
        PermissionError("read failure: permission denied"),
        IsADirectoryError("read failure: path is a directory"),
        OSError("read failure: I/O error"),
    ],
)
def test_has_managed_footprint_propagates_non_missing_read_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    read_error: OSError,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    target = paths["codex_hooks"]
    original_read_text = Path.read_text

    def controlled_read_text(path: Path, *args: object, **kwargs: object) -> str:
        if path == target:
            raise read_error
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", controlled_read_text)

    with pytest.raises(type(read_error), match="read failure"):
        has_managed_footprint("codex")


def test_diagnose_converts_config_read_failure_to_bounded_adapter_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    target = paths["codex_hooks"]
    brain = tmp_path / "brain"
    brain.mkdir()
    original_read_text = Path.read_text

    def controlled_read_text(path: Path, *args: object, **kwargs: object) -> str:
        if path == target:
            raise PermissionError("read failure: permission denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", controlled_read_text)

    reports = diagnose_configured_core_adapters(brain)

    assert [report.adapter for report in reports] == ["codex"]
    assert reports[0].status == "error"
    assert "read failure: permission denied" in reports[0].non_ok_checks[0].detail


def test_missing_and_broken_symlink_configs_remain_unconfigured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    paths["codex_hooks"].parent.mkdir(parents=True)
    try:
        paths["codex_hooks"].symlink_to(tmp_path / "missing-hooks.json")
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    brain = tmp_path / "brain"
    brain.mkdir()

    assert has_managed_footprint("codex") is False
    assert diagnose_configured_core_adapters(brain) == ()


def test_discovery_is_skipped_when_no_core_adapter_is_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_paths(tmp_path, monkeypatch)
    brain = tmp_path / "brain"
    brain.mkdir()

    from agent_brain import agent_integrations

    def unexpected_discovery() -> list[str]:
        raise AssertionError("discovery must be skipped")

    monkeypatch.setattr(agent_integrations, "discover_adapters", unexpected_discovery)

    assert diagnose_configured_core_adapters(brain) == ()


def test_discovery_exception_errors_each_configured_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(paths["codex_agents"], CODEX_BEGIN)
    _write(paths["claude_awareness"], AWARENESS_BEGIN)
    brain = tmp_path / "brain"
    brain.mkdir()

    from agent_brain import agent_integrations
    from agent_brain.agent_integrations import registry

    def fail_discovery() -> list[str]:
        raise RuntimeError("adapter discovery failed")

    def unexpected_get_adapter(name: str, brain_dir: Path) -> None:
        raise AssertionError("get_adapter must not run after discovery failure")

    monkeypatch.setattr(agent_integrations, "discover_adapters", fail_discovery)
    monkeypatch.setattr(registry, "get_adapter", unexpected_get_adapter)

    reports = diagnose_configured_core_adapters(brain)

    assert [report.adapter for report in reports] == ["codex", "claude_code"]
    assert [report.status for report in reports] == ["error", "error"]
    assert all("adapter discovery failed" in report.non_ok_checks[0].detail for report in reports)


def test_adapter_exception_does_not_short_circuit_later_configured_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(paths["codex_agents"], CODEX_BEGIN)
    _write(paths["claude_awareness"], AWARENESS_BEGIN)
    brain = tmp_path / "brain"
    brain.mkdir()
    requested: list[str] = []
    claude_report = AdapterDiagnosticReport(
        adapter="claude_code",
        overall_status="warn",
        checks=[AdapterDiagnosticCheck("runtime", "warn", "not observed")],
        brain_dir=brain,
    )

    class BrokenAdapter:
        def diagnose(self) -> AdapterDiagnosticReport:
            raise RuntimeError("codex failed")

    from agent_brain.agent_integrations import registry

    def get_adapter(name: str, brain_dir: Path) -> object:
        requested.append(name)
        return BrokenAdapter() if name == "codex" else _Adapter(claude_report)

    monkeypatch.setattr(registry, "get_adapter", get_adapter)

    reports = diagnose_configured_core_adapters(brain)

    assert requested == ["codex", "claude_code"]
    assert [report.adapter for report in reports] == ["codex", "claude_code"]
    assert [report.status for report in reports] == ["error", "warn"]


@pytest.mark.parametrize(
    ("report", "detail_fragment"),
    [
        (None, "report"),
        (SimpleNamespace(overall_status="invalid", checks=[]), "status"),
        (SimpleNamespace(overall_status="error", checks=None), "checks"),
        (SimpleNamespace(overall_status="error", checks=[object()]), "AdapterDiagnosticCheck"),
        (
            SimpleNamespace(
                overall_status="error",
                checks=[AdapterDiagnosticCheck("bad", "invalid", "broken")],
            ),
            "check status",
        ),
    ],
)
def test_malformed_diagnostic_report_becomes_adapter_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    report: object,
    detail_fragment: str,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(paths["codex_agents"], CODEX_BEGIN)
    brain = tmp_path / "brain"
    brain.mkdir()

    class ReturningAdapter:
        def diagnose(self) -> object:
            return report

    from agent_brain.agent_integrations import registry

    monkeypatch.setattr(registry, "get_adapter", lambda name, brain_dir: ReturningAdapter())

    reports = diagnose_configured_core_adapters(brain)

    assert len(reports) == 1
    assert reports[0].adapter == "codex"
    assert reports[0].status == "error"
    assert detail_fragment.lower() in reports[0].non_ok_checks[0].detail.lower()


def test_diagnostic_report_accepts_any_iterable_of_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(paths["codex_agents"], CODEX_BEGIN)
    brain = tmp_path / "brain"
    brain.mkdir()
    warn = AdapterDiagnosticCheck("runtime", "warn", "not observed")
    report = SimpleNamespace(overall_status="warn", checks=(check for check in [warn]))

    class ReturningAdapter:
        def diagnose(self) -> object:
            return report

    from agent_brain.agent_integrations import registry

    monkeypatch.setattr(registry, "get_adapter", lambda name, brain_dir: ReturningAdapter())

    reports = diagnose_configured_core_adapters(brain)

    assert reports == (CoreAdapterHealth("codex", "warn", (warn,)),)


@pytest.mark.parametrize(
    ("report_status", "check_status", "expected_status"),
    [
        ("ok", "warn", "warn"),
        ("ok", "error", "error"),
        ("warn", "error", "error"),
        ("error", "ok", "error"),
    ],
)
def test_diagnostic_status_uses_more_severe_report_or_check_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    report_status: str,
    check_status: str,
    expected_status: str,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(paths["codex_agents"], CODEX_BEGIN)
    brain = tmp_path / "brain"
    brain.mkdir()
    check = AdapterDiagnosticCheck("check", check_status, "detail")
    report = AdapterDiagnosticReport(
        adapter="codex",
        overall_status=report_status,
        checks=[check],
        brain_dir=brain,
    )

    from agent_brain.agent_integrations import registry

    monkeypatch.setattr(registry, "get_adapter", lambda name, brain_dir: _Adapter(report))

    reports = diagnose_configured_core_adapters(brain)

    assert reports[0].status == expected_status


def test_core_adapter_health_is_immutable() -> None:
    health = CoreAdapterHealth("codex", "ok", ())

    with pytest.raises(FrozenInstanceError):
        health.status = "error"  # type: ignore[misc]


def test_importing_adapter_health_does_not_import_agent_integrations(
    tmp_path: Path,
) -> None:
    result = _run_isolated_python(
        tmp_path,
        """
        import sys

        assert "agent_brain.agent_integrations" not in sys.modules
        import agent_brain.platform.adapter_health
        assert "agent_brain.agent_integrations" not in sys.modules
        """,
    )

    assert result.returncode == 0, result.stderr


def test_unconfigured_diagnosis_does_not_import_agent_integrations(
    tmp_path: Path,
) -> None:
    result = _run_isolated_python(
        tmp_path,
        """
        import os
        from pathlib import Path
        import sys

        from agent_brain.platform.adapter_health import diagnose_configured_core_adapters

        brain = Path(os.environ["HOME"]) / "brain"
        brain.mkdir()
        assert diagnose_configured_core_adapters(brain) == ()
        assert "agent_brain.agent_integrations" not in sys.modules
        """,
    )

    assert result.returncode == 0, result.stderr


def test_configured_first_package_import_failure_becomes_bounded_error(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    agents_md = home / ".codex" / "AGENTS.md"
    _write(agents_md, CODEX_BEGIN)
    result = _run_isolated_python(
        tmp_path,
        """
        import importlib
        import os
        from pathlib import Path

        from agent_brain.platform import adapter_health

        real_import = importlib.import_module
        calls = []

        def failing_import(name, package=None):
            if name == "agent_brain.agent_integrations":
                calls.append(name)
                raise RuntimeError("first package import failed")
            return real_import(name, package)

        importlib.import_module = failing_import
        brain = Path(os.environ["HOME"]) / "brain"
        brain.mkdir()
        reports = adapter_health.diagnose_configured_core_adapters(brain)

        assert calls == ["agent_brain.agent_integrations"]
        assert [report.adapter for report in reports] == ["codex"]
        assert reports[0].status == "error"
        assert "first package import failed" in reports[0].non_ok_checks[0].detail
        assert adapter_health.has_managed_footprint("codex") is True
        assert adapter_health.bounded_diagnostic_text("still usable") == "still usable"
        """,
    )

    assert result.returncode == 0, result.stderr


def test_neutral_diagnostic_types_are_legacy_identity_reexports() -> None:
    from agent_brain.diagnostic_types import (
        AdapterDiagnosticCheck as NeutralCheck,
    )
    from agent_brain.diagnostic_types import CheckStatus as NeutralStatus
    from agent_brain.agent_integrations.diagnostics import (
        AdapterDiagnosticCheck as LegacyCheck,
    )
    from agent_brain.agent_integrations.diagnostics import CheckStatus as LegacyStatus

    assert NeutralCheck is LegacyCheck
    assert NeutralStatus is LegacyStatus
    assert NeutralCheck("name", "warn", "detail", "fix").to_dict() == {
        "name": "name",
        "status": "warn",
        "detail": "detail",
        "fix": "fix",
    }


def test_bounded_diagnostic_text_preserves_lines_tabs_and_limits_length() -> None:
    value = "line one\nline\ttwo\x00\x1b" + "z" * 2000

    result = bounded_diagnostic_text(value)

    assert result.startswith("line one\nline\ttwo  ")
    assert "\x00" not in result
    assert "\x1b" not in result
    assert len(result) == 1200
    assert result.endswith("…")


def test_bounded_diagnostic_text_handles_zero_and_one_character_limits() -> None:
    assert bounded_diagnostic_text("abc", limit=0) == ""
    assert bounded_diagnostic_text("abc", limit=1) == "…"
    assert bounded_diagnostic_text("x", limit=1) == "x"


def test_footprint_and_diagnosis_leave_config_bytes_hashes_and_modes_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(paths["codex_agents"], f"{CODEX_BEGIN}\nmanaged\n{CODEX_END}\n")
    _write(paths["codex_hooks"], _hook_payload(CURRENT_HOOK))
    _write(paths["codex_config"], f"{MCP_SECTION}\ncommand = 'memory'\n")
    _write(
        paths["claude_settings"],
        json.dumps(
            {
                "hooks": json.loads(_hook_payload(CURRENT_HOOK))["hooks"],
                "mcpServers": {"agent-memory-hub": None},
            }
        ),
    )
    _write(
        paths["claude_awareness"],
        f"{AWARENESS_BEGIN}\nmanaged\n{AWARENESS_END}\n",
    )
    for index, path in enumerate(paths.values()):
        path.chmod(0o600 + index)

    def snapshot() -> dict[str, tuple[bytes, str, int]]:
        result: dict[str, tuple[bytes, str, int]] = {}
        for name, path in paths.items():
            content = path.read_bytes()
            result[name] = (
                content,
                hashlib.sha256(content).hexdigest(),
                path.stat().st_mode & 0o7777,
            )
        return result

    before = snapshot()
    brain = tmp_path / "brain"
    (brain / "items").mkdir(parents=True)

    assert has_managed_footprint("codex") is True
    assert has_managed_footprint("claude_code") is True
    assert [report.adapter for report in diagnose_configured_core_adapters(brain)] == [
        "codex",
        "claude_code",
    ]

    assert snapshot() == before


class _Adapter:
    def __init__(self, report: AdapterDiagnosticReport) -> None:
        self._report = report

    def diagnose(self) -> AdapterDiagnosticReport:
        return self._report
