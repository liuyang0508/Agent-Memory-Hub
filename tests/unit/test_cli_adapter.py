"""Tests for `memory adapter list/install/uninstall` CLI + auto-discovery (P2-10).

Before the fix:
  - `agent_brain.agent_integrations.discover_adapters` does not exist -> import errors.
  - The `adapter` Typer sub-command does not exist -> CliRunner invoke exits
    with click's usage error code (2), not 0/1.
After the fix both pass.
"""

import json
import os
import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_brain.interfaces.cli import app

runner = CliRunner()

# The adapter modules that ship in agent_brain/agent_integrations/.
ALL_ADAPTERS = {
    "aider", "aone_copilot", "claude_code", "cline", "codex", "continue_dev",
    "cursor", "github_copilot", "qoder", "qoder_work", "wukong",
    "openclaw", "hermes_agent", "openhuman", "opensquilla", "mulerun",
}


@pytest.fixture(autouse=True)
def isolated_brain_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path / "brain"))
    from agent_brain.agent_integrations import qoder as qoder_mod

    monkeypatch.setattr(
        qoder_mod,
        "QODER_MEMORIES_DIR",
        tmp_path / ".qoder" / "memories",
        raising=False,
    )


def test_discover_adapters_populates_registry():
    from agent_brain.agent_integrations import discover_adapters
    from agent_brain.agent_integrations.registry import ADAPTER_REGISTRY

    names = discover_adapters()
    assert set(names) == ALL_ADAPTERS
    assert set(ADAPTER_REGISTRY.keys()) == ALL_ADAPTERS


def test_adapter_list_json_lists_all():
    result = runner.invoke(app, ["adapter", "list", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    by_name = {r["name"]: r for r in data}
    assert set(by_name) == ALL_ADAPTERS
    # Real adapters expose install-ready claims; remaining Qoder variants stay WIP.
    assert by_name["claude_code"]["status"] == "ready"
    assert by_name["qoder"]["status"] == "ready"
    assert by_name["continue_dev"]["status"] == "ready"
    assert by_name["github_copilot"]["status"] == "ready"
    assert by_name["openclaw"]["status"] == "ready"
    assert by_name["hermes_agent"]["status"] == "ready"
    assert by_name["openhuman"]["status"] == "ready"
    assert by_name["opensquilla"]["status"] == "ready"
    assert by_name["qoder_work"]["status"] == "ready"
    assert by_name["aone_copilot"]["status"] == "ready"
    assert by_name["claude_code"]["support_level"] == "install-ready"
    assert by_name["qoder"]["support_level"] == "install-ready"
    assert by_name["continue_dev"]["support_level"] == "install-ready"
    assert by_name["github_copilot"]["support_level"] == "install-ready"
    assert by_name["codex"]["verified"] is False
    assert by_name["codex"]["display_names"] == ["Codex", "Codex CLI"]
    assert by_name["codex"]["aliases"] == ["codex_cli"]
    assert by_name["codex"]["verification_status"] == "not_verified"
    assert "evidence level is install-ready, not verified" in by_name["codex"]["verification_blockers"]
    assert "runtime event not observed" in by_name["codex"]["verification_blockers"]
    assert by_name["qoder_work"]["support_level"] == "install-ready"
    assert by_name["openhuman"]["support_level"] == "install-ready"
    assert by_name["aone_copilot"]["support_level"] == "install-ready"
    assert by_name["qoder_work"]["verified"] is False
    assert by_name["qoder_work"]["verification_status"] == "not_verified"
    assert "evidence level is install-ready, not verified" in by_name["qoder_work"]["verification_blockers"]
    assert "runtime event not observed" in by_name["qoder_work"]["verification_blockers"]
    assert isinstance(by_name["claude_code"]["integration_modes"], list)
    assert isinstance(by_name["qoder"]["limitations"], list)
    assert by_name["codex"]["evidence_paths"] == [
        "tests/unit/test_adapters.py",
        "tests/unit/test_cli_adapter.py",
        "agent_brain/agent_integrations/codex.py",
        "agent_brain/agent_integrations/codex_hooks.py",
        "agent_brain/agent_integrations/codex_diagnostics.py",
    ]
    assert by_name["claude_code"]["evidence_paths"] == [
        "tests/unit/test_adapters.py",
        "tests/unit/test_cli_adapter.py",
        "agent_brain/agent_integrations/claude_code.py",
        "agent_brain/agent_integrations/claude_code_diagnostics.py",
    ]
    assert by_name["cursor"]["evidence_paths"] == [
        "tests/unit/test_adapters.py",
        "tests/unit/test_cli_adapter.py",
        "agent_brain/agent_integrations/cursor.py",
        "agent_brain/agent_integrations/mcp_config_diagnostics.py",
    ]
    assert by_name["cline"]["evidence_paths"] == [
        "tests/unit/test_adapters.py",
        "tests/unit/test_cli_adapter.py",
        "agent_brain/agent_integrations/cline.py",
        "agent_brain/agent_integrations/mcp_config_diagnostics.py",
    ]
    assert by_name["aider"]["evidence_paths"] == [
        "tests/unit/test_adapters.py",
        "tests/unit/test_cli_adapter.py",
        "agent_brain/agent_integrations/aider.py",
        "agent_brain/agent_integrations/aider_diagnostics.py",
    ]
    assert by_name["wukong"]["evidence_paths"] == [
        "tests/unit/test_adapters.py",
        "tests/unit/test_adapter_robustness_p36.py",
        "tests/unit/test_cli_adapter.py",
        "agent_brain/agent_integrations/wukong.py",
        "<rewinddesktop-repo>/tauri-app/src-tauri/src/mcp/config.rs",
    ]
    assert by_name["qoder"]["evidence_paths"] == [
        "tests/unit/test_adapters.py",
        "tests/unit/test_cli_adapter.py",
        "agent_brain/agent_integrations/qoder.py",
        "agent_brain/agent_integrations/qoder_diagnostics.py",
    ]
    assert by_name["continue_dev"]["evidence_paths"] == [
        "tests/unit/test_adapters.py",
        "tests/unit/test_cli_adapter.py",
        "agent_brain/agent_integrations/continue_dev.py",
    ]
    assert by_name["github_copilot"]["evidence_paths"] == [
        "tests/unit/test_adapters.py",
        "tests/unit/test_cli_adapter.py",
        "agent_brain/agent_integrations/github_copilot.py",
    ]
    assert by_name["openclaw"]["evidence_paths"] == [
        "tests/unit/test_adapters.py",
        "https://docs.openclaw.ai/cli/mcp",
    ]
    assert by_name["hermes_agent"]["evidence_paths"] == [
        "tests/unit/test_adapters.py",
        "agent_brain/agent_integrations/hermes/provider.py",
        "https://hermes-agent.nousresearch.com/docs/user-guide/features/tool-calling-mcp",
    ]
    assert by_name["openhuman"]["evidence_paths"] == [
        "tests/unit/test_adapters.py",
        "https://github.com/tinyhumansai/openhuman",
    ]
    assert by_name["opensquilla"]["evidence_paths"] == [
        "tests/unit/test_adapters.py",
        "https://github.com/opensquilla/opensquilla",
    ]
    assert by_name["aone_copilot"]["evidence_paths"] == [
        "tests/unit/test_adapters.py",
        "/Applications/IntelliJ IDEA Ultimate.app",
    ]


def test_codex_cli_alias_resolves_to_codex_adapter():
    from agent_brain.agent_integrations.codex import CodexAdapter
    from agent_brain.agent_integrations.registry import get_adapter, resolve_adapter_name

    canonical, alias_used = resolve_adapter_name("codex_cli")

    assert canonical == "codex"
    assert alias_used == "codex_cli"
    assert isinstance(get_adapter("codex_cli", Path("/tmp/test_brain")), CodexAdapter)


def test_adapter_list_table_runs():
    result = runner.invoke(app, ["adapter", "list"])
    assert result.exit_code == 0, result.output
    assert "codex" in result.output
    assert "wukong" in result.output
    assert "evidence" in result.output
    assert "paths" in result.output


def test_adapter_install_unknown_name_exits_1():
    result = runner.invoke(app, ["adapter", "install", "does_not_exist"])
    assert result.exit_code == 1


def test_adapter_install_codex_writes_block(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import codex as cx_mod

    monkeypatch.setattr(cx_mod, "AGENTS_MD", tmp_path / ".codex" / "AGENTS.md")
    monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", tmp_path / ".codex" / "hooks.json")
    monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", tmp_path / ".codex" / "config.toml")
    result = runner.invoke(app, ["adapter", "install", "codex"])
    assert result.exit_code == 0, result.output
    assert "codex adapter" in result.output
    content = (tmp_path / ".codex" / "AGENTS.md").read_text()
    assert cx_mod.BEGIN in content
    assert cx_mod.END in content
    assert (tmp_path / ".codex" / "hooks.json").exists()
    assert (tmp_path / ".codex" / "config.toml").exists()


def test_adapter_doctor_codex_json_reports_runtime_warning_after_install(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import codex as cx_mod

    monkeypatch.setattr(cx_mod, "AGENTS_MD", tmp_path / ".codex" / "AGENTS.md")
    monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", tmp_path / ".codex" / "hooks.json")
    monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", tmp_path / ".codex" / "config.toml")

    install = runner.invoke(app, ["adapter", "install", "codex"])
    assert install.exit_code == 0, install.output

    result = runner.invoke(app, ["adapter", "doctor", "codex", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert data["adapter"] == "codex"
    assert data["overall_status"] == "warn"
    assert {check["name"] for check in data["checks"]} >= {
        "AGENTS.md discipline block",
        "Codex hooks.json",
        "Codex MCP server",
        "Codex runtime evidence",
    }
    runtime = [check for check in data["checks"] if check["name"] == "Codex runtime evidence"][0]
    assert runtime["status"] == "warn"
    assert "not observed" in runtime["detail"]
    layered = [check for check in data["checks"] if check["name"] == "Codex layered context pack evidence"][0]
    assert layered["status"] == "warn"
    assert "pack metrics not observed" in layered["detail"]


def test_adapter_doctor_codex_reports_layered_context_pack_metrics(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import codex as cx_mod
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort

    brain_dir = Path(os.environ["BRAIN_DIR"])
    monkeypatch.setattr(cx_mod, "AGENTS_MD", tmp_path / ".codex" / "AGENTS.md")
    monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", tmp_path / ".codex" / "hooks.json")
    monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", tmp_path / ".codex" / "config.toml")

    install = runner.invoke(app, ["adapter", "install", "codex"])
    assert install.exit_code == 0, install.output
    record_injection_cohort(
        brain_dir,
        item_ids=["mem-layered-context-pack"],
        adapter="codex",
        session_id="sess-layered-pack",
        cwd="/repo",
        query="layered context pack smoke",
        pack_metrics={
            "packed_tokens": 20,
            "full_tokens": 205,
            "items": [
                {
                    "id": "mem-layered-context-pack",
                    "selected_view": "locator",
                    "packed_tokens": 20,
                    "full_tokens": 205,
                    "compressed": True,
                }
            ],
        },
    )

    result = runner.invoke(app, ["adapter", "doctor", "codex", "--format", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    layered = [check for check in data["checks"] if check["name"] == "Codex layered context pack evidence"][0]
    assert layered["status"] == "ok"
    assert "selected_view=locator" in layered["detail"]
    assert "packed=20/205t" in layered["detail"]


def test_adapter_install_verify_codex_promotes_after_runtime_observed(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import codex as cx_mod
    from agent_brain.agent_integrations.runtime_events import record_runtime_event

    brain_dir = Path(os.environ["BRAIN_DIR"])
    monkeypatch.setattr(cx_mod, "AGENTS_MD", tmp_path / ".codex" / "AGENTS.md")
    monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", tmp_path / ".codex" / "hooks.json")
    monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", tmp_path / ".codex" / "config.toml")
    record_runtime_event(
        brain_dir,
        adapter="codex",
        event_name="UserPromptSubmit",
        session_id="sess-install-verify",
        source="pytest",
    )

    result = runner.invoke(app, ["adapter", "install-verify", "codex", "--format", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["status"] == "passed"
    assert data["verification"]["record"]["status"] == "passed"

    listed = runner.invoke(app, ["adapter", "list", "--format", "json"])
    by_name = {row["name"]: row for row in json.loads(listed.output)}
    assert by_name["codex"]["verified"] is True


def test_adapter_verify_mcp_adapter_promotes_with_active_probe(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import continue_dev as cont_mod

    monkeypatch.setattr(cont_mod, "MCP_CONFIG_PATH", tmp_path / ".continue" / "config.yaml")
    monkeypatch.setattr(
        cont_mod,
        "AWARENESS_PATH",
        tmp_path / ".continue" / "rules" / "agent-memory-hub.md",
    )

    install = runner.invoke(app, ["adapter", "install", "continue_dev"])
    assert install.exit_code == 0, install.output

    result = runner.invoke(app, ["adapter", "verify", "continue_dev", "--format", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["status"] == "passed"
    assert data["mcp_probe"]["status"] == "passed"
    assert any("mcp_tools=" in entry for entry in data["record"]["evidence"])

    listed = runner.invoke(app, ["adapter", "list", "--format", "json"])
    by_name = {row["name"]: row for row in json.loads(listed.output)}
    assert by_name["continue_dev"]["verified"] is True


def test_adapter_verify_cli_context_probe_flag_requires_effective_context(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import codex as cx_mod
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.product import adapter_onboarding as onboarding

    brain_dir = Path(os.environ["BRAIN_DIR"])
    monkeypatch.setattr(cx_mod, "AGENTS_MD", tmp_path / ".codex" / "AGENTS.md")
    monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", tmp_path / ".codex" / "hooks.json")
    monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", tmp_path / ".codex" / "config.toml")
    monkeypatch.setattr(onboarding, "_context_transcript_roots", lambda adapter: [])
    record_runtime_event(
        brain_dir,
        adapter="codex",
        event_name="UserPromptSubmit",
        session_id="codex-cli-context-probe-missing",
        source="pytest",
    )
    install = runner.invoke(app, ["adapter", "install", "codex"])
    assert install.exit_code == 0, install.output

    result = runner.invoke(app, [
        "adapter",
        "verify",
        "codex",
        "--context-probe",
        "--format",
        "json",
    ])

    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["status"] == "failed"
    assert "context effectiveness not observed" in data["blockers"]
    assert data["context_probe"]["status"] == "failed"


def test_adapter_install_verify_uninstall_check_does_not_persist_verified(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import github_copilot as gh_mod

    instructions = tmp_path / ".github" / "copilot-instructions.md"
    monkeypatch.setattr(gh_mod, "INSTRUCTIONS_PATH", instructions)

    result = runner.invoke(
        app,
        [
            "adapter",
            "install-verify",
            "github_copilot",
            "--uninstall-check",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["status"] == "passed"
    assert data["uninstall"]["status"] == "uninstalled"
    assert data["persistent_verification_recorded"] is False
    assert gh_mod.BEGIN not in instructions.read_text()

    listed = runner.invoke(app, ["adapter", "list", "--format", "json"])
    by_name = {row["name"]: row for row in json.loads(listed.output)}
    assert by_name["github_copilot"]["verified"] is False


def test_adapter_doctor_codex_cli_alias_reports_canonical_codex(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import codex as cx_mod

    monkeypatch.setattr(cx_mod, "AGENTS_MD", tmp_path / ".codex" / "AGENTS.md")
    monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", tmp_path / ".codex" / "hooks.json")
    monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", tmp_path / ".codex" / "config.toml")

    install = runner.invoke(app, ["adapter", "install", "codex_cli"])
    assert install.exit_code == 0, install.output

    result = runner.invoke(app, ["adapter", "doctor", "codex_cli", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert data["requested_adapter"] == "codex_cli"
    assert data["alias"] == "codex_cli"
    assert data["adapter"] == "codex"
    assert data["overall_status"] == "warn"


def test_adapter_doctor_codex_json_reports_error_when_missing(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import codex as cx_mod

    monkeypatch.setattr(cx_mod, "AGENTS_MD", tmp_path / ".codex" / "AGENTS.md")
    monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", tmp_path / ".codex" / "hooks.json")
    monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", tmp_path / ".codex" / "config.toml")

    result = runner.invoke(app, ["adapter", "doctor", "codex", "--format", "json"])
    assert result.exit_code == 1
    data = json.loads(result.output)

    assert data["adapter"] == "codex"
    assert data["overall_status"] == "error"
    assert any(check["status"] == "error" for check in data["checks"])


def test_adapter_doctor_claude_code_json_reports_runtime_warning_after_install(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import claude_code as cc_mod

    monkeypatch.setattr(cc_mod, "SETTINGS_PATH", tmp_path / ".claude" / "settings.json")
    monkeypatch.setattr(cc_mod, "AWARENESS_PATH", tmp_path / ".claude" / "CLAUDE.md")

    install = runner.invoke(app, ["adapter", "install", "claude_code"])
    assert install.exit_code == 0, install.output

    result = runner.invoke(app, ["adapter", "doctor", "claude_code", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert data["adapter"] == "claude_code"
    assert data["overall_status"] == "warn"
    assert {check["name"] for check in data["checks"]} >= {
        "Claude Code settings hooks",
        "Claude Code hook scripts",
        "Claude Code runtime evidence",
    }
    runtime = [check for check in data["checks"] if check["name"] == "Claude Code runtime evidence"][0]
    assert runtime["status"] == "warn"
    assert "not observed" in runtime["detail"]


def test_adapter_doctor_cursor_json_reports_ok_after_install(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import cursor as cur_mod

    monkeypatch.setattr(cur_mod, "MCP_CONFIG_PATH", tmp_path / ".cursor" / "mcp.json")
    monkeypatch.setattr(
        cur_mod,
        "AWARENESS_PATH",
        tmp_path / ".cursor" / "rules" / "agent-memory-hub.mdc",
    )

    install = runner.invoke(app, ["adapter", "install", "cursor"])
    assert install.exit_code == 0, install.output

    result = runner.invoke(app, ["adapter", "doctor", "cursor", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert data["adapter"] == "cursor"
    assert data["overall_status"] == "ok"
    assert {check["name"] for check in data["checks"]} == {
        "Cursor awareness channel",
        "Cursor MCP server",
    }


def test_adapter_doctor_cline_json_reports_ok_after_install(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import cline as cl_mod

    monkeypatch.setattr(cl_mod, "MCP_CONFIG_PATH", tmp_path / ".cline" / "mcp_servers.json")
    monkeypatch.setattr(
        cl_mod,
        "AWARENESS_PATH",
        tmp_path / ".cline" / "agent-memory-hub-awareness.md",
    )

    install = runner.invoke(app, ["adapter", "install", "cline"])
    assert install.exit_code == 0, install.output

    result = runner.invoke(app, ["adapter", "doctor", "cline", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert data["adapter"] == "cline"
    assert data["overall_status"] == "ok"
    assert {check["name"] for check in data["checks"]} == {
        "Cline awareness channel",
        "Cline MCP server",
    }


def test_adapter_doctor_aider_json_reports_ok_after_install(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import aider as ai_mod

    monkeypatch.setattr(ai_mod, "AIDER_CONF", tmp_path / ".aider.conf.yml")

    install = runner.invoke(app, ["adapter", "install", "aider"])
    assert install.exit_code == 0, install.output

    result = runner.invoke(app, ["adapter", "doctor", "aider", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert data["adapter"] == "aider"
    assert data["overall_status"] == "ok"
    assert {check["name"] for check in data["checks"]} >= {
        "Aider read directive",
        "Aider brain digest",
    }


def test_adapter_doctor_wukong_json_reports_ok_after_install(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import wukong as wk_mod

    monkeypatch.setattr(wk_mod, "CONTEXT_FILE", tmp_path / ".wukong" / "brain_context.md")
    monkeypatch.setattr(
        wk_mod,
        "MCP_CONFIG_PATH",
        tmp_path / ".real" / ".mcp" / "mcpServerConfig.json",
    )

    install = runner.invoke(app, ["adapter", "install", "wukong"])
    assert install.exit_code == 0, install.output

    result = runner.invoke(app, ["adapter", "doctor", "wukong", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert data["adapter"] == "wukong"
    assert data["overall_status"] == "ok"
    assert {check["name"] for check in data["checks"]} == {
        "Wukong brain context",
        "Wukong MCP server",
    }


def test_adapter_install_qoder_writes_settings_hooks(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import qoder as qoder_mod

    awareness_path = tmp_path / ".qoder" / "AGENTS.md"
    mcp_path = tmp_path / "Library" / "Application Support" / "Qoder" / "SharedClientCache" / "mcp.json"
    extension_mcp_path = (
        tmp_path
        / "Library"
        / "Application Support"
        / "Qoder"
        / "SharedClientCache"
        / "extension"
        / "local"
        / "mcp.json"
    )
    monkeypatch.setattr(qoder_mod, "SETTINGS_PATH", tmp_path / ".qoder" / "settings.json")
    monkeypatch.setattr(qoder_mod, "AWARENESS_PATH", awareness_path)
    monkeypatch.setattr(qoder_mod, "MCP_CONFIG_PATH", mcp_path)
    monkeypatch.setattr(qoder_mod, "MCP_EXTENSION_CONFIG_PATH", extension_mcp_path)
    monkeypatch.setattr(qoder_mod, "QODER_PROJECTS_DIR", tmp_path / ".qoder" / "projects")

    result = runner.invoke(app, ["adapter", "install", "qoder"])

    assert result.exit_code == 0, result.output
    assert "qoder adapter" in result.output
    settings = json.loads((tmp_path / ".qoder" / "settings.json").read_text())
    assert "UserPromptSubmit" in settings["hooks"]
    assert "Stop" in settings["hooks"]
    prompt_command = settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert "inject-context.sh" in prompt_command
    assert "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT=json" in prompt_command
    assert "Agent Memory Hub Awareness Channel" in awareness_path.read_text(encoding="utf-8")
    assert "agent-memory-hub" in json.loads(mcp_path.read_text())["mcpServers"]
    extension = json.loads(extension_mcp_path.read_text())
    assert extension["mcpServers"]["agent-memory-hub"]["disabled"] is False


def test_adapter_doctor_qoder_json_reports_runtime_warning_after_install(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import qoder as qoder_mod

    mcp_path = tmp_path / "Library" / "Application Support" / "Qoder" / "SharedClientCache" / "mcp.json"
    extension_mcp_path = (
        tmp_path
        / "Library"
        / "Application Support"
        / "Qoder"
        / "SharedClientCache"
        / "extension"
        / "local"
        / "mcp.json"
    )
    monkeypatch.setattr(qoder_mod, "SETTINGS_PATH", tmp_path / ".qoder" / "settings.json")
    monkeypatch.setattr(qoder_mod, "AWARENESS_PATH", tmp_path / ".qoder" / "AGENTS.md")
    monkeypatch.setattr(qoder_mod, "MCP_CONFIG_PATH", mcp_path)
    monkeypatch.setattr(qoder_mod, "MCP_EXTENSION_CONFIG_PATH", extension_mcp_path)
    monkeypatch.setattr(qoder_mod, "QODER_PROJECTS_DIR", tmp_path / ".qoder" / "projects")

    install = runner.invoke(app, ["adapter", "install", "qoder"])
    assert install.exit_code == 0, install.output

    result = runner.invoke(app, ["adapter", "doctor", "qoder", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert data["adapter"] == "qoder"
    assert data["overall_status"] == "warn"
    assert {check["name"] for check in data["checks"]} >= {
        "Qoder settings hooks",
        "Qoder prompt hook injection mode",
        "Qoder hook scripts",
        "Qoder awareness channel",
        "Qoder workspace awareness",
        "Qoder MCP user profile",
        "Qoder MCP shared cache",
        "Qoder MCP extension cache",
        "Qoder runtime evidence",
    }
    runtime = [check for check in data["checks"] if check["name"] == "Qoder runtime evidence"][0]
    assert runtime["status"] == "warn"
    assert "not observed" in runtime["detail"]


def test_adapter_uninstall_qoder_removes_hub_hooks(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import qoder as qoder_mod

    settings_path = tmp_path / ".qoder" / "settings.json"
    awareness_path = tmp_path / ".qoder" / "AGENTS.md"
    mcp_path = tmp_path / "Library" / "Application Support" / "Qoder" / "SharedClientCache" / "mcp.json"
    extension_mcp_path = (
        tmp_path
        / "Library"
        / "Application Support"
        / "Qoder"
        / "SharedClientCache"
        / "extension"
        / "local"
        / "mcp.json"
    )
    monkeypatch.setattr(qoder_mod, "SETTINGS_PATH", settings_path)
    monkeypatch.setattr(qoder_mod, "AWARENESS_PATH", awareness_path)
    monkeypatch.setattr(qoder_mod, "MCP_CONFIG_PATH", mcp_path)
    monkeypatch.setattr(qoder_mod, "MCP_EXTENSION_CONFIG_PATH", extension_mcp_path)
    monkeypatch.setattr(qoder_mod, "QODER_PROJECTS_DIR", tmp_path / ".qoder" / "projects")

    install = runner.invoke(app, ["adapter", "install", "qoder"])
    assert install.exit_code == 0, install.output
    uninstall = runner.invoke(app, ["adapter", "uninstall", "qoder"])
    assert uninstall.exit_code == 0, uninstall.output

    settings = json.loads(settings_path.read_text())
    assert settings["hooks"]["UserPromptSubmit"] == []
    assert settings["hooks"]["Stop"] == []
    assert not awareness_path.exists()


def test_adapter_install_qoder_work_writes_independent_settings_hooks(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import qoder_work as qw_mod

    awareness_path = tmp_path / ".qoderwork" / "awareness" / "main" / "AGENTS.md"
    monkeypatch.setattr(qw_mod, "SETTINGS_PATH", tmp_path / ".qoderwork" / "settings.json")
    monkeypatch.setattr(qw_mod, "MCP_CONFIG_PATH", tmp_path / ".qoderwork" / "mcp.json")
    monkeypatch.setattr(qw_mod, "AWARENESS_PATH", awareness_path)

    result = runner.invoke(app, ["adapter", "install", "qoder_work"])

    assert result.exit_code == 0, result.output
    assert "qoder_work adapter" in result.output
    settings = json.loads((tmp_path / ".qoderwork" / "settings.json").read_text())
    assert "UserPromptSubmit" in settings["hooks"]
    assert "Stop" in settings["hooks"]
    assert "AGENT_MEMORY_HUB_ADAPTER=qoder_work" in settings["hooks"]["Stop"][0]["hooks"][0]["command"]
    mcp = json.loads((tmp_path / ".qoderwork" / "mcp.json").read_text())
    assert "agent-memory-hub" in mcp["mcpServers"]
    assert mcp["mcpServers"]["agent-memory-hub"]["enabled"] is True
    assert "Agent Memory Hub Awareness Channel" in awareness_path.read_text(encoding="utf-8")


def test_adapter_doctor_qoder_work_json_reports_runtime_warning_after_install(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import qoder_work as qw_mod

    monkeypatch.setattr(qw_mod, "SETTINGS_PATH", tmp_path / ".qoderwork" / "settings.json")
    monkeypatch.setattr(qw_mod, "MCP_CONFIG_PATH", tmp_path / ".qoderwork" / "mcp.json")
    monkeypatch.setattr(
        qw_mod,
        "AWARENESS_PATH",
        tmp_path / ".qoderwork" / "awareness" / "main" / "AGENTS.md",
    )

    install = runner.invoke(app, ["adapter", "install", "qoder_work"])
    assert install.exit_code == 0, install.output

    result = runner.invoke(app, ["adapter", "doctor", "qoder_work", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert data["adapter"] == "qoder_work"
    assert data["overall_status"] == "warn"
    assert {check["name"] for check in data["checks"]} >= {
        "QoderWork settings hooks",
        "QoderWork hook scripts",
        "QoderWork awareness channel",
        "QoderWork MCP server",
        "QoderWork runtime evidence",
    }


def test_adapter_install_continue_registers_mcp_server(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import continue_dev as cont_mod

    monkeypatch.setattr(cont_mod, "MCP_CONFIG_PATH", tmp_path / ".continue" / "config.yaml")
    monkeypatch.setattr(
        cont_mod,
        "AWARENESS_PATH",
        tmp_path / ".continue" / "rules" / "agent-memory-hub.md",
    )

    result = runner.invoke(app, ["adapter", "install", "continue_dev"])

    assert result.exit_code == 0, result.output
    assert "continue adapter" in result.output
    import yaml
    config = yaml.safe_load((tmp_path / ".continue" / "config.yaml").read_text())
    assert any(server["name"] == "agent-memory-hub" for server in config["mcpServers"])


def test_adapter_doctor_continue_json_reports_ok_after_install(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import continue_dev as cont_mod

    monkeypatch.setattr(cont_mod, "MCP_CONFIG_PATH", tmp_path / ".continue" / "config.yaml")
    monkeypatch.setattr(
        cont_mod,
        "AWARENESS_PATH",
        tmp_path / ".continue" / "rules" / "agent-memory-hub.md",
    )

    install = runner.invoke(app, ["adapter", "install", "continue_dev"])
    assert install.exit_code == 0, install.output

    result = runner.invoke(app, ["adapter", "doctor", "continue_dev", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert data["adapter"] == "continue_dev"
    assert data["overall_status"] == "ok"
    assert {check["name"] for check in data["checks"]} == {
        "Continue awareness channel",
        "Continue MCP server",
    }


def test_adapter_install_openhuman_writes_agentmemory_backend(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import openhuman as oh_mod

    monkeypatch.setattr(oh_mod, "CONFIG_PATH", tmp_path / ".openhuman" / "config.toml")

    result = runner.invoke(app, ["adapter", "install", "openhuman"])

    assert result.exit_code == 0, result.output
    assert "openhuman adapter" in result.output
    parsed = tomllib.loads((tmp_path / ".openhuman" / "config.toml").read_text())
    assert parsed["memory"]["backend"] == "agentmemory"


def test_adapter_doctor_openhuman_json_reports_ok_after_install(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import openhuman as oh_mod

    monkeypatch.setattr(oh_mod, "CONFIG_PATH", tmp_path / ".openhuman" / "config.toml")

    install = runner.invoke(app, ["adapter", "install", "openhuman"])
    assert install.exit_code == 0, install.output

    result = runner.invoke(app, ["adapter", "doctor", "openhuman", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert data["adapter"] == "openhuman"
    assert data["overall_status"] == "ok"
    assert {check["name"] for check in data["checks"]} == {
        "OpenHuman agentmemory backend"
    }


def test_adapter_uninstall_continue_removes_hub_server(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import continue_dev as cont_mod

    config_path = tmp_path / ".continue" / "config.yaml"
    monkeypatch.setattr(cont_mod, "MCP_CONFIG_PATH", config_path)
    monkeypatch.setattr(
        cont_mod,
        "AWARENESS_PATH",
        tmp_path / ".continue" / "rules" / "agent-memory-hub.md",
    )

    install = runner.invoke(app, ["adapter", "install", "continue_dev"])
    assert install.exit_code == 0, install.output
    uninstall = runner.invoke(app, ["adapter", "uninstall", "continue_dev"])
    assert uninstall.exit_code == 0, uninstall.output

    import yaml
    config = yaml.safe_load(config_path.read_text())
    assert not any(server["name"] == "agent-memory-hub" for server in config["mcpServers"])


def test_adapter_install_github_copilot_writes_repository_instructions(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import github_copilot as gh_mod

    monkeypatch.setattr(
        gh_mod,
        "INSTRUCTIONS_PATH",
        tmp_path / ".github" / "copilot-instructions.md",
    )

    result = runner.invoke(app, ["adapter", "install", "github_copilot"])

    assert result.exit_code == 0, result.output
    assert "github copilot adapter" in result.output
    content = (tmp_path / ".github" / "copilot-instructions.md").read_text()
    assert gh_mod.BEGIN in content


def test_adapter_doctor_github_copilot_json_reports_ok_after_install(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import github_copilot as gh_mod

    monkeypatch.setattr(
        gh_mod,
        "INSTRUCTIONS_PATH",
        tmp_path / ".github" / "copilot-instructions.md",
    )

    install = runner.invoke(app, ["adapter", "install", "github_copilot"])
    assert install.exit_code == 0, install.output

    result = runner.invoke(app, ["adapter", "doctor", "github_copilot", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert data["adapter"] == "github_copilot"
    assert data["overall_status"] == "ok"
    assert {check["name"] for check in data["checks"]} == {"GitHub Copilot instructions"}


def test_adapter_uninstall_github_copilot_removes_hub_block(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import github_copilot as gh_mod

    instructions = tmp_path / ".github" / "copilot-instructions.md"
    monkeypatch.setattr(gh_mod, "INSTRUCTIONS_PATH", instructions)

    install = runner.invoke(app, ["adapter", "install", "github_copilot"])
    assert install.exit_code == 0, install.output
    uninstall = runner.invoke(app, ["adapter", "uninstall", "github_copilot"])
    assert uninstall.exit_code == 0, uninstall.output

    assert gh_mod.BEGIN not in instructions.read_text()


def test_adapter_uninstall_wip_has_no_path():
    result = runner.invoke(app, ["adapter", "uninstall", "mulerun"])
    assert result.exit_code == 1


def test_adapter_uninstall_openclaw_tolerates_broken_cli_when_entry_absent(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import openclaw as oc_mod

    monkeypatch.setattr(oc_mod, "AWARENESS_PATH", tmp_path / ".openclaw" / "agent-memory-hub-awareness.md")
    config_path = tmp_path / ".openclaw" / "openclaw.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('{"mcp": {"servers": {}}}', encoding="utf-8")
    monkeypatch.setattr(oc_mod, "OPENCLAW_CONFIG_PATH", config_path)
    monkeypatch.setattr(oc_mod, "_require_openclaw", lambda: None)

    def fail_remove(_args):
        raise RuntimeError("exit -9")

    monkeypatch.setattr(oc_mod, "_run_openclaw", fail_remove)

    result = runner.invoke(app, ["adapter", "uninstall", "openclaw"])

    assert result.exit_code == 0
    assert "no local agent-memory-hub registry entry found" in result.output
    assert "Traceback" not in result.output


def test_adapter_uninstall_openclaw_keeps_failure_when_entry_remains(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import openclaw as oc_mod

    monkeypatch.setattr(oc_mod, "AWARENESS_PATH", tmp_path / ".openclaw" / "agent-memory-hub-awareness.md")
    config_path = tmp_path / ".openclaw" / "openclaw.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('{"mcp": {"servers": {"agent-memory-hub": {}}}}', encoding="utf-8")
    monkeypatch.setattr(oc_mod, "OPENCLAW_CONFIG_PATH", config_path)
    monkeypatch.setattr(oc_mod, "_require_openclaw", lambda: None)

    def fail_remove(_args):
        raise RuntimeError("exit -9")

    monkeypatch.setattr(oc_mod, "_run_openclaw", fail_remove)

    result = runner.invoke(app, ["adapter", "uninstall", "openclaw"])

    assert result.exit_code == 1
    assert "openclaw: uninstall failed" in result.output
    assert "exit -9" in result.output
    assert "Traceback" not in result.output
