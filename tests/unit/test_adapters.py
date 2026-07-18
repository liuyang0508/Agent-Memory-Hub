"""Unit tests for agent adapters.

v1.1 M1 split the adapter set into two tiers:
  - REAL adapters (claude_code, codex, qoder, etc.) — install() writes config
    or AGENTS.md atomically and idempotently. We test these against a tmp
    HOME so the real install logic runs without touching the user's machine.
  - WIP adapters — inherit from WIPAdapter which raises
    NotImplementedError. We assert exactly that contract, so a future
    silent regression to "no-op stub returns hardcoded string" fails fast.

All registered adapters still report a valid AdapterConfig,
because discovery / capability inspection works before install does.
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

from agent_brain.agent_integrations import AdapterBase, AdapterConfig, WIPAdapter
from agent_brain.agent_integrations.python_runtime import amh_python_executable
from agent_brain.agent_integrations.registry import (
    ADAPTER_REGISTRY,
    get_adapter,
    list_adapters,
)

# Importing the adapter modules registers them. Keep noqa: F401 so linters
# don't strip the import.
import agent_brain.agent_integrations.claude_code  # noqa: F401
import agent_brain.agent_integrations.cursor  # noqa: F401
import agent_brain.agent_integrations.aider  # noqa: F401
import agent_brain.agent_integrations.continue_dev  # noqa: F401
import agent_brain.agent_integrations.cline  # noqa: F401
import agent_brain.agent_integrations.codex  # noqa: F401
import agent_brain.agent_integrations.qoder  # noqa: F401
import agent_brain.agent_integrations.qoder_work  # noqa: F401
import agent_brain.agent_integrations.wukong  # noqa: F401
import agent_brain.agent_integrations.aone_copilot  # noqa: F401
import agent_brain.agent_integrations.github_copilot  # noqa: F401
import agent_brain.agent_integrations.openclaw  # noqa: F401
import agent_brain.agent_integrations.hermes_agent  # noqa: F401
import agent_brain.agent_integrations.openhuman  # noqa: F401
import agent_brain.agent_integrations.opensquilla  # noqa: F401
import agent_brain.agent_integrations.mulerun  # noqa: F401


BRAIN_DIR = Path("/tmp/test_brain")
AMH_PYTHON = amh_python_executable()

ALL_ADAPTERS = [
    "claude_code", "cursor", "aider", "continue_dev",
    "cline", "codex", "qoder", "qoder_work",
    "wukong", "aone_copilot", "github_copilot",
    "openclaw", "hermes_agent", "openhuman", "opensquilla", "mulerun",
]
REAL_ADAPTERS = [
    "claude_code", "codex", "cursor", "cline", "aider", "wukong", "qoder",
    "qoder_work", "continue_dev", "github_copilot", "openclaw", "hermes_agent",
    "openhuman", "opensquilla", "aone_copilot",
]
WIP_ADAPTERS = [a for a in ALL_ADAPTERS if a not in REAL_ADAPTERS]


def test_runtime_prompt_hook_uses_structured_routed_protocol():
    hook = (
        Path(__file__).resolve().parents[2]
        / "agent_runtime_kit"
        / "hooks"
        / "inject-context.sh"
    ).read_text(encoding="utf-8")

    assert "--routed-recall" in hook
    assert "--format" in hook
    assert "hook-json" in hook
    assert "AGENT_MEMORY_HUB_RAW_QUERY" not in hook
    assert "KEYWORDS=" not in hook


def test_managed_adapter_runtime_text_contains_no_personal_examples(tmp_path):
    from agent_brain.agent_integrations import qoder as qoder_mod
    from agent_brain.agent_integrations import qoder_work as qw_mod
    from agent_brain.agent_integrations import wukong as wk_mod

    repo_dir = tmp_path / "repo"
    qoder = qoder_mod.QoderAdapter(brain_dir=tmp_path / "brain", repo_dir=repo_dir)
    qoder_work = qw_mod.QoderWorkAdapter(brain_dir=tmp_path / "brain", repo_dir=repo_dir)
    wukong = wk_mod.WukongAdapter(brain_dir=tmp_path / "brain", repo_dir=repo_dir)
    managed_texts = {
        "qoder workspace awareness": qoder._workspace_awareness_block(),
        "qoder native memory bridge": qoder._native_memory_bridge_content(),
        "qoder native priority redirect": qoder._native_priority_redirect_block(),
        "qoder_work workspace awareness": qoder_work._workspace_awareness_block(),
        "qoder_work bootstrap skill": qoder_work._bootstrap_skill_content(),
        "wukong awareness": wukong._awareness_block(),
        "wukong native memory bridge": wukong._native_memory_bridge_content(),
        "wukong bootstrap skill": wukong._bootstrap_skill_content(),
        "wukong bootstrap keywords": wukong._bootstrap_skill_keywords(),
        "wukong fts text": wk_mod._wukong_fts_text("short prompt bridge"),
    }
    for surface, text in managed_texts.items():
        assert "例如：`" not in text, f"{surface} contains a literal prompt example"
        assert "例如“" not in text, f"{surface} contains a literal prompt example"
        assert "such as `" not in text, f"{surface} contains a literal prompt example"


def test_wukong_short_prompt_detector_uses_structure_not_project_name():
    from agent_brain.agent_integrations import wukong as wk_mod

    assert wk_mod._text_has_wukong_short_prompt(
        'gateway_agent_cmd begin label=Some("Alpha") message_len=5 message_preview=Alpha'
    )
    assert wk_mod._text_has_wukong_short_prompt('{"title":"Beta","message_len":4}')
    assert not wk_mod._text_has_wukong_short_prompt("Alpha")


def _yaml_mcp_server(config: dict, name: str) -> dict:
    servers = config["mcpServers"]
    assert isinstance(servers, list)
    return next(server for server in servers if server["name"] == name)


class TestAdapterRegistry:
    """Tests for adapter registry functionality."""

    def test_mcp_diagnostic_server_validator_is_split(self):
        from agent_brain.agent_integrations.mcp_diagnostics import validate_mcp_server

        check = validate_mcp_server(
            check_name="Test MCP",
            config_path=Path("/tmp/test.json"),
            server_name="agent-memory-hub",
            server={"command": "bash", "args": ["server.sh"]},
            expected_command="bash",
            expected_args=["server.sh"],
            expected_env={"BRAIN_DIR": "/tmp/brain"},
            install_command="memory adapter install test",
        )

        assert check.status == "error"
        assert "env" in check.detail
        assert check.fix == "run: memory adapter install test"

    def test_mcp_config_diagnostics_are_split_and_reexported(self):
        from agent_brain.agent_integrations import diagnostics
        from agent_brain.agent_integrations.mcp_config_diagnostics import diagnose_mcp_json_server

        assert diagnostics.diagnose_mcp_json_server is diagnose_mcp_json_server

    def test_codex_hook_commands_are_split_and_reexported(self):
        from agent_brain.agent_integrations import codex_config
        from agent_brain.agent_integrations.codex_hook_commands import (
            command_references_path,
            command_references_prefix,
            hook_already_present,
            hook_belongs_to,
            hook_script_present,
            update_hook_command,
        )
        from agent_brain.agent_integrations.hook_config import (
            command_references_path as shared_command_references_path,
            command_references_prefix as shared_command_references_prefix,
            hook_already_present as shared_hook_already_present,
            hook_belongs_to as shared_hook_belongs_to,
            hook_script_present as shared_hook_script_present,
            update_hook_command as shared_update_hook_command,
        )

        entries = [{"hooks": [{"command": "python /tmp/old-hook.sh"}]}]
        changed = update_hook_command(
            entries,
            script_path="/tmp/old-hook.sh",
            expected_command="python /tmp/new-hook.sh",
            timeout=3,
        )

        assert changed is True
        assert entries[0]["hooks"][0]["command"] == "python /tmp/new-hook.sh"
        assert entries[0]["hooks"][0]["timeout"] == 3
        assert "timeout_ms" not in entries[0]["hooks"][0]
        assert codex_config.update_hook_command is update_hook_command
        assert command_references_path is shared_command_references_path
        assert command_references_prefix is shared_command_references_prefix
        assert hook_already_present is shared_hook_already_present
        assert hook_belongs_to is shared_hook_belongs_to
        assert hook_script_present is shared_hook_script_present
        assert update_hook_command is shared_update_hook_command

    def test_adapter_registry_has_16_adapters(self):
        assert len(ADAPTER_REGISTRY) == 16

    def test_list_adapters(self):
        adapters = list_adapters()
        assert len(adapters) == 16
        assert "qoder_wake" not in adapters
        for adapter in adapters:
            assert isinstance(adapter, str)

    def test_get_adapter_by_name(self):
        for adapter_name in ALL_ADAPTERS:
            adapter = get_adapter(adapter_name, BRAIN_DIR)
            assert adapter is not None
            assert isinstance(adapter, AdapterBase)
            assert adapter.brain_dir == BRAIN_DIR

    def test_get_adapter_invalid_name(self):
        with pytest.raises(ValueError, match="Unknown adapter"):
            get_adapter("invalid_adapter", BRAIN_DIR)


class TestAllAdaptersHaveValidConfig:
    """Every adapter (real or WIP) must report a valid AdapterConfig because
    that's used for discovery / capability inspection before install runs."""

    @pytest.mark.parametrize("adapter_name", ALL_ADAPTERS)
    def test_adapter_config(self, adapter_name):
        adapter = get_adapter(adapter_name, BRAIN_DIR)
        config = adapter.get_config()
        assert isinstance(config, AdapterConfig)
        assert config.agent_name == adapter_name
        assert isinstance(config.config_dir, Path)
        assert config.hook_type in ["file", "command", "mcp", "env"]
        assert config.inject_method in ["system_prompt", "rules_file", "env_var", "mcp_tool"]
        assert isinstance(config.supports_hooks, bool)
        assert isinstance(config.supports_mcp, bool)


class TestWIPAdaptersRaiseNotImplemented:
    """WIP adapters must hard-fail rather than silently no-op so users notice."""

    @pytest.mark.parametrize("adapter_name", WIP_ADAPTERS)
    def test_install_raises(self, adapter_name):
        adapter = get_adapter(adapter_name, BRAIN_DIR)
        assert isinstance(adapter, WIPAdapter)
        with pytest.raises(NotImplementedError, match="install"):
            adapter.install()

    @pytest.mark.parametrize("adapter_name", WIP_ADAPTERS)
    def test_inject_context_raises(self, adapter_name):
        adapter = get_adapter(adapter_name, BRAIN_DIR)
        with pytest.raises(NotImplementedError, match="inject_context"):
            adapter.inject_context("test query")

    @pytest.mark.parametrize("adapter_name", WIP_ADAPTERS)
    def test_install_instructions_returns_markdown(self, adapter_name):
        # The WIP variant of get_install_instructions does NOT raise — it
        # returns a clearly-labeled "WIP" markdown block so users can
        # discover capabilities without provoking an install attempt.
        adapter = get_adapter(adapter_name, BRAIN_DIR)
        result = adapter.get_install_instructions()
        assert isinstance(result, str)
        assert "WIP" in result
        assert "##" in result


class TestClaudeCodeAdapterRealInstall:
    """Exercises claude_code adapter's real install against a tmp HOME."""

    def test_awareness_install_backs_up_existing_file_before_append(self, tmp_path):
        from agent_brain.agent_integrations.awareness import install_awareness_block

        awareness_path = tmp_path / "CLAUDE.md"
        original = "# Personal Claude notes\n\nKeep this.\n"
        block = (
            "<!-- BEGIN agent-memory-hub-awareness -->\n"
            "# Agent Memory Hub Awareness Channel\n"
            "<!-- END agent-memory-hub-awareness -->"
        )
        awareness_path.write_text(original, encoding="utf-8")

        changed = install_awareness_block(awareness_path, block)

        assert changed is True
        backups = list(tmp_path.glob("CLAUDE.md.bak.amh-awareness.*"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == original
        content = awareness_path.read_text(encoding="utf-8")
        assert "# Personal Claude notes" in content
        assert "Agent Memory Hub Awareness Channel" in content

    def test_awareness_install_backs_up_existing_file_before_block_replacement(self, tmp_path):
        from agent_brain.agent_integrations.awareness import install_awareness_block

        awareness_path = tmp_path / "CLAUDE.md"
        old_block = (
            "<!-- BEGIN agent-memory-hub-awareness -->\n"
            "old managed text\n"
            "<!-- END agent-memory-hub-awareness -->"
        )
        new_block = (
            "<!-- BEGIN agent-memory-hub-awareness -->\n"
            "new managed text\n"
            "<!-- END agent-memory-hub-awareness -->"
        )
        original = f"# Personal Claude notes\n\n{old_block}\n\nKeep this.\n"
        awareness_path.write_text(original, encoding="utf-8")

        changed = install_awareness_block(awareness_path, new_block)

        assert changed is True
        backups = list(tmp_path.glob("CLAUDE.md.bak.amh-awareness.*"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == original
        content = awareness_path.read_text(encoding="utf-8")
        assert "# Personal Claude notes" in content
        assert "new managed text" in content
        assert "old managed text" not in content
        assert "Keep this." in content

    def test_awareness_install_does_not_backup_new_file_or_noop_update(self, tmp_path):
        from agent_brain.agent_integrations.awareness import install_awareness_block

        awareness_path = tmp_path / "CLAUDE.md"
        block = (
            "<!-- BEGIN agent-memory-hub-awareness -->\n"
            "# Agent Memory Hub Awareness Channel\n"
            "<!-- END agent-memory-hub-awareness -->"
        )

        assert install_awareness_block(awareness_path, block) is True
        assert list(tmp_path.glob("CLAUDE.md.bak.amh-awareness.*")) == []

        assert install_awareness_block(awareness_path, block) is False
        assert list(tmp_path.glob("CLAUDE.md.bak.amh-awareness.*")) == []

    def test_awareness_install_does_not_backup_managed_only_replacement(self, tmp_path):
        from agent_brain.agent_integrations.awareness import install_awareness_block

        awareness_path = tmp_path / "AGENTS.md"
        old_block = (
            "<!-- BEGIN agent-memory-hub-awareness -->\n"
            "old managed text\n"
            "<!-- END agent-memory-hub-awareness -->\n"
        )
        new_block = (
            "<!-- BEGIN agent-memory-hub-awareness -->\n"
            "new managed text\n"
            "<!-- END agent-memory-hub-awareness -->"
        )
        awareness_path.write_text(old_block, encoding="utf-8")

        assert install_awareness_block(awareness_path, new_block) is True
        assert list(tmp_path.glob("AGENTS.md.bak.amh-awareness.*")) == []
        assert awareness_path.read_text(encoding="utf-8").strip() == new_block

    def test_awareness_install_can_promote_block_to_file_front(self, tmp_path):
        from agent_brain.agent_integrations.awareness import install_awareness_block

        awareness_path = tmp_path / "AGENTS.md"
        old_block = (
            "<!-- BEGIN agent-memory-hub-awareness -->\n"
            "old managed text\n"
            "<!-- END agent-memory-hub-awareness -->"
        )
        new_block = (
            "<!-- BEGIN agent-memory-hub-awareness -->\n"
            "# Agent Memory Hub Awareness Channel\n"
            "<!-- END agent-memory-hub-awareness -->"
        )
        original = "# Existing agent notes\n\nKeep this.\n\n" + old_block + "\n"
        awareness_path.write_text(original, encoding="utf-8")

        changed = install_awareness_block(awareness_path, new_block, placement="prepend")

        assert changed is True
        content = awareness_path.read_text(encoding="utf-8")
        assert content.startswith("<!-- BEGIN agent-memory-hub-awareness -->")
        assert "# Existing agent notes" in content
        assert "Keep this." in content
        assert "old managed text" not in content
        backups = list(tmp_path.glob("AGENTS.md.bak.amh-awareness.*"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == original

    def test_awareness_uninstall_backs_up_existing_file_before_removal(self, tmp_path):
        from agent_brain.agent_integrations.awareness import uninstall_awareness_block

        awareness_path = tmp_path / "CLAUDE.md"
        block = (
            "<!-- BEGIN agent-memory-hub-awareness -->\n"
            "# Agent Memory Hub Awareness Channel\n"
            "<!-- END agent-memory-hub-awareness -->"
        )
        original = f"# Personal Claude notes\n\n{block}\n\nKeep this.\n"
        awareness_path.write_text(original, encoding="utf-8")

        changed = uninstall_awareness_block(awareness_path)

        assert changed is True
        backups = list(tmp_path.glob("CLAUDE.md.bak.amh-awareness.*"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == original
        content = awareness_path.read_text(encoding="utf-8")
        assert "# Personal Claude notes" in content
        assert "agent-memory-hub-awareness" not in content
        assert "Keep this." in content

    def test_awareness_uninstall_deletes_file_when_only_managed_block_remains(self, tmp_path):
        from agent_brain.agent_integrations.awareness import uninstall_awareness_block

        awareness_path = tmp_path / "agent-memory-hub-awareness.md"
        original = (
            "<!-- BEGIN agent-memory-hub-awareness -->\n"
            "# Agent Memory Hub Awareness Channel\n"
            "<!-- END agent-memory-hub-awareness -->\n"
        )
        awareness_path.write_text(original, encoding="utf-8")

        changed = uninstall_awareness_block(awareness_path)

        assert changed is True
        assert not awareness_path.exists()
        backups = list(tmp_path.glob("agent-memory-hub-awareness.md.bak.amh-awareness.*"))
        assert backups == []

    def test_awareness_uninstall_deletes_empty_sidecar_file(self, tmp_path):
        from agent_brain.agent_integrations.awareness import uninstall_awareness_block

        awareness_path = tmp_path / "agent-memory-hub-awareness.md"
        awareness_path.write_text("", encoding="utf-8")

        changed = uninstall_awareness_block(awareness_path)

        assert changed is True
        assert not awareness_path.exists()

    def test_claude_code_installs_static_awareness_channel(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import claude_code as cc_mod

        settings_path = tmp_path / ".claude" / "settings.json"
        awareness_path = tmp_path / ".claude" / "CLAUDE.md"
        awareness_path.parent.mkdir(parents=True, exist_ok=True)
        awareness_path.write_text("# Personal Claude notes\n\nKeep this.\n", encoding="utf-8")
        monkeypatch.setattr(cc_mod, "SETTINGS_PATH", settings_path)
        monkeypatch.setattr(cc_mod, "AWARENESS_PATH", awareness_path)

        adapter = cc_mod.ClaudeCodeAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        content = awareness_path.read_text(encoding="utf-8")
        assert "# Personal Claude notes" in content
        assert "BEGIN agent-memory-hub-awareness" in content
        assert "Awareness Channel" in content
        assert "search_memory" in content
        assert "write_memory" in content
        assert str(tmp_path / ".brain") in content

        report = adapter.diagnose()
        assert any(check.name == "Claude Code awareness channel" for check in report.checks)

        adapter.uninstall()
        content = awareness_path.read_text(encoding="utf-8")
        assert "# Personal Claude notes" in content
        assert "agent-memory-hub-awareness" not in content

    def test_hook_config_helpers_are_split_and_reexported(self):
        from agent_brain.agent_integrations import claude_code as cc_mod
        from agent_brain.agent_integrations.hook_config import (
            adapter_hook_command,
            atomic_write_json,
            hook_belongs_to,
            hook_script_present,
            read_json_config,
            update_hook_command,
        )

        assert cc_mod._read_settings is read_json_config
        assert cc_mod._atomic_write_settings is atomic_write_json
        assert cc_mod._hook_belongs_to is hook_belongs_to
        assert cc_mod._hook_script_present is hook_script_present
        assert cc_mod._update_hook_command is update_hook_command
        assert cc_mod._adapter_hook_command is adapter_hook_command

    def test_claude_code_diagnostic_helpers_are_split(self, tmp_path):
        from agent_brain.agent_integrations.claude_code_diagnostics import diagnose_settings_hooks

        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text('{"hooks": {"Stop": []}}', encoding="utf-8")
        hooks_dir = tmp_path / "agent_runtime_kit" / "hooks"
        hooks_dir.mkdir(parents=True)

        check = diagnose_settings_hooks(
            settings_path=settings_path,
            hooks_dir=hooks_dir,
            hook_events=("Stop",),
            hook_scripts={"Stop": "session-end-signal.sh"},
        )

        assert check.name == "Claude Code settings hooks"
        assert check.status == "error"
        assert "missing hub hook event(s): Stop" in check.detail

    def test_install_creates_settings_with_lifecycle_hooks(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        # claude_code adapter computes SETTINGS_PATH at import time, so we
        # also have to swap the module-level constant for this test.
        from agent_brain.agent_integrations import claude_code as cc_mod
        monkeypatch.setattr(cc_mod, "SETTINGS_PATH", tmp_path / ".claude" / "settings.json")
        monkeypatch.setattr(cc_mod, "AWARENESS_PATH", tmp_path / ".claude" / "CLAUDE.md")

        adapter = cc_mod.ClaudeCodeAdapter(brain_dir=tmp_path / ".brain")
        msg = adapter.install()
        assert "installed 7 hook" in msg

        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert set(settings["hooks"].keys()) >= {
            "SessionStart",
            "UserPromptSubmit",
            "Stop",
            "PreCompact",
            "PostCompact",
            "SubagentStart",
            "SubagentStop",
        }
        for event in ("SessionStart", "UserPromptSubmit", "Stop"):
            assert any(
                "agent_runtime_kit/hooks/" in h.get("command", "")
                for entry in settings["hooks"][event]
                for h in entry.get("hooks", [])
            ), f"no hub-owned hook found under {event}"
            assert any(
                "AGENT_MEMORY_HUB_ADAPTER=claude_code" in h.get("command", "")
                for entry in settings["hooks"][event]
                for h in entry.get("hooks", [])
            ), f"no adapter identity env found under {event}"
        for event in ("PreCompact", "PostCompact", "SubagentStart", "SubagentStop"):
            assert any(
                "agent_runtime_kit/hooks/lifecycle-event.sh" in h.get("command", "")
                for entry in settings["hooks"][event]
                for h in entry.get("hooks", [])
            ), f"no lifecycle hook found under {event}"
            assert any(
                "AGENT_MEMORY_HUB_ADAPTER=claude_code" in h.get("command", "")
                for entry in settings["hooks"][event]
                for h in entry.get("hooks", [])
            ), f"no adapter identity env found under {event}"
        assert "PreToolUse" not in settings["hooks"]
        assert "PostToolUse" not in settings["hooks"]
        assert "PermissionRequest" not in settings["hooks"]

    def test_install_creates_mcp_server_entry(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        from agent_brain.agent_integrations import claude_code as cc_mod
        monkeypatch.setattr(cc_mod, "SETTINGS_PATH", tmp_path / ".claude" / "settings.json")
        monkeypatch.setattr(cc_mod, "AWARENESS_PATH", tmp_path / ".claude" / "CLAUDE.md")

        adapter = cc_mod.ClaudeCodeAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        mcp = settings["mcpServers"]["agent-memory-hub"]
        assert mcp["command"].endswith("agent_runtime_kit/mcp/server.sh")
        assert mcp.get("args", []) == []
        assert mcp["env"] == {"BRAIN_DIR": str(tmp_path / ".brain")}

    def test_install_is_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        from agent_brain.agent_integrations import claude_code as cc_mod
        monkeypatch.setattr(cc_mod, "SETTINGS_PATH", tmp_path / ".claude" / "settings.json")
        monkeypatch.setattr(cc_mod, "AWARENESS_PATH", tmp_path / ".claude" / "CLAUDE.md")

        adapter = cc_mod.ClaudeCodeAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        msg = adapter.install()  # second run
        assert "already installed" in msg

    def test_install_migrates_legacy_brain_hook_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        from agent_brain.agent_integrations import claude_code as cc_mod
        monkeypatch.setattr(cc_mod, "SETTINGS_PATH", tmp_path / ".claude" / "settings.json")
        monkeypatch.setattr(cc_mod, "AWARENESS_PATH", tmp_path / ".claude" / "CLAUDE.md")

        repo = Path(__file__).resolve().parents[2]
        old_script = repo / "brain" / "hooks" / "inject-context.sh"
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps({
            "hooks": {
                "UserPromptSubmit": [
                    {"matcher": "", "hooks": [{"type": "command", "command": str(old_script)}]},
                ],
            },
        }))

        adapter = cc_mod.ClaudeCodeAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        entries = settings["hooks"]["UserPromptSubmit"]
        commands = [
            hook["command"]
            for entry in entries
            for hook in entry.get("hooks", [])
        ]
        assert len(entries) == 1
        assert any("agent_runtime_kit/hooks/inject-context.sh" in command for command in commands)
        assert not any("/brain/hooks/inject-context.sh" in command for command in commands)

    def test_install_prunes_stale_claude_hook_checkouts(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        from agent_brain.agent_integrations import claude_code as cc_mod
        monkeypatch.setattr(cc_mod, "SETTINGS_PATH", tmp_path / ".claude" / "settings.json")
        monkeypatch.setattr(cc_mod, "AWARENESS_PATH", tmp_path / ".claude" / "CLAUDE.md")

        repo = Path(__file__).resolve().parents[2]
        current_script = repo / "agent_runtime_kit" / "hooks" / "inject-context.sh"
        stale_script = (
            "/private/var/folders/example/T/amh-bench-XXXXXX.old/agent-memory-hub/"
            "agent_runtime_kit/hooks/inject-context.sh"
        )
        worktree_script = (
            "/home/example/.config/superpowers/worktrees/agent-memory-hub/old/"
            "agent_runtime_kit/hooks/inject-context.sh"
        )
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps({
            "hooks": {
                "UserPromptSubmit": [
                    {"matcher": "", "hooks": [{"type": "command", "command": stale_script}]},
                    {"matcher": "", "hooks": [{"type": "command", "command": "~/.claude/hooks/guard.sh"}]},
                    {"matcher": "", "hooks": [{"type": "command", "command": str(current_script)}]},
                    {"matcher": "", "hooks": [{"type": "command", "command": worktree_script}]},
                ],
            },
        }))

        adapter = cc_mod.ClaudeCodeAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        commands = [
            hook["command"]
            for entry in settings["hooks"]["UserPromptSubmit"]
            for hook in entry.get("hooks", [])
        ]
        assert sum("agent_runtime_kit/hooks/inject-context.sh" in command for command in commands) == 1
        assert any("~/.claude/hooks/guard.sh" in command for command in commands)
        assert not any("amh-bench-XXXXXX.old" in command for command in commands)
        assert not any("superpowers/worktrees/agent-memory-hub/old" in command for command in commands)

    def test_uninstall_removes_only_hub_hooks(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        from agent_brain.agent_integrations import claude_code as cc_mod
        monkeypatch.setattr(cc_mod, "SETTINGS_PATH", tmp_path / ".claude" / "settings.json")
        monkeypatch.setattr(cc_mod, "AWARENESS_PATH", tmp_path / ".claude" / "CLAUDE.md")

        # Seed settings.json with an unrelated hook the user has set up.
        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps({
            "hooks": {
                "SessionStart": [
                    {"matcher": "", "hooks": [{"type": "command", "command": "/usr/local/bin/their-tool"}]},
                ],
            },
        }))

        adapter = cc_mod.ClaudeCodeAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        adapter.uninstall()

        settings = json.loads(settings_path.read_text())
        remaining = settings["hooks"]["SessionStart"]
        assert len(remaining) == 1
        assert "their-tool" in remaining[0]["hooks"][0]["command"]

    def test_diagnose_reports_runtime_warning_after_install(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        from agent_brain.agent_integrations import claude_code as cc_mod
        monkeypatch.setattr(cc_mod, "SETTINGS_PATH", tmp_path / ".claude" / "settings.json")
        monkeypatch.setattr(cc_mod, "AWARENESS_PATH", tmp_path / ".claude" / "CLAUDE.md")

        adapter = cc_mod.ClaudeCodeAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        report = adapter.diagnose()
        data = report.to_dict()

        assert data["adapter"] == "claude_code"
        assert data["overall_status"] == "warn"
        assert {check["name"] for check in data["checks"]} >= {
            "Claude Code settings hooks",
            "Claude Code hook scripts",
            "Claude Code runtime evidence",
        }
        runtime = [check for check in data["checks"] if check["name"] == "Claude Code runtime evidence"][0]
        assert runtime["status"] == "warn"

    def test_diagnose_reports_missing_settings_without_writing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        from agent_brain.agent_integrations import claude_code as cc_mod
        settings_path = tmp_path / ".claude" / "settings.json"
        monkeypatch.setattr(cc_mod, "SETTINGS_PATH", settings_path)
        monkeypatch.setattr(cc_mod, "AWARENESS_PATH", tmp_path / ".claude" / "CLAUDE.md")

        adapter = cc_mod.ClaudeCodeAdapter(brain_dir=tmp_path / ".brain")

        report = adapter.diagnose()

        assert report.overall_status == "error"
        assert not settings_path.exists()
        assert any(check.name == "Claude Code settings hooks" for check in report.checks)


class TestCodexAdapterRealInstall:
    """Exercises codex adapter's AGENTS.md block install against a tmp HOME."""

    def test_codex_agents_block_helpers_are_split(self, tmp_path):
        from agent_brain.agent_integrations.codex_agents import (
            install_agents_block,
            render_agents_block,
            uninstall_agents_block,
        )
        from agent_brain.agent_integrations.codex_config import BEGIN, END

        discipline = tmp_path / "AGENT_MEMORY_DISCIPLINE.md"
        discipline.write_text("# Discipline\n\nUse memory.\n", encoding="utf-8")
        agents_md = tmp_path / ".codex" / "AGENTS.md"
        agents_md.parent.mkdir()
        agents_md.write_text("# user notes\n", encoding="utf-8")

        block = render_agents_block(discipline)

        assert BEGIN in block and END in block
        assert install_agents_block(agents_md, block) is True
        assert install_agents_block(agents_md, block) is False
        assert "# user notes" in agents_md.read_text(encoding="utf-8")
        assert uninstall_agents_block(agents_md) is True
        assert BEGIN not in agents_md.read_text(encoding="utf-8")

    def test_codex_hooks_helpers_are_split(self, tmp_path):
        from agent_brain.agent_integrations.codex_hooks import install_hooks, uninstall_hooks

        hooks_json = tmp_path / ".codex" / "hooks.json"
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        scripts = {
            "SessionStart": ("startup|resume", "inject-discipline.sh"),
            "UserPromptSubmit": ("", "inject-context.sh"),
            "Stop": ("", "session-end-signal.sh"),
        }
        for _event, (_matcher, script_name) in scripts.items():
            (hooks_dir / script_name).write_text("#!/bin/sh\n", encoding="utf-8")

        assert install_hooks(hooks_json, hooks_dir, scripts) is True
        assert install_hooks(hooks_json, hooks_dir, scripts) is False

        data = json.loads(hooks_json.read_text(encoding="utf-8"))
        assert set(data["hooks"]) == set(scripts)
        assert uninstall_hooks(hooks_json, hooks_dir, scripts.keys()) is True
        assert data["hooks"]["SessionStart"][0]["hooks"][0]["command"]

    def test_codex_hook_helpers_use_shared_hook_config(self):
        from agent_brain.agent_integrations import codex as cx_mod
        from agent_brain.agent_integrations.hook_config import (
            adapter_hook_command,
            atomic_write_json,
            hook_belongs_to,
            hook_script_present,
            read_json_config,
            update_hook_command,
        )

        assert cx_mod._adapter_hook_command is adapter_hook_command
        assert cx_mod._read_json_config is read_json_config
        assert cx_mod._atomic_write_json is atomic_write_json
        assert cx_mod._hook_belongs_to is hook_belongs_to
        assert cx_mod._hook_script_present is hook_script_present
        assert cx_mod._update_hook_command is update_hook_command

    def test_adapter_hook_command_provides_path_for_env_bash_hooks(self, tmp_path):
        from agent_brain.agent_integrations.hook_config import adapter_hook_command

        script = tmp_path / "hook.sh"
        script.write_text("#!/usr/bin/env bash\nprintf 'ok:%s' \"$AGENT_MEMORY_HUB_ADAPTER\"\n", encoding="utf-8")
        script.chmod(0o755)

        result = subprocess.run(
            ["/bin/sh", "-c", adapter_hook_command("codex", script)],
            env={"HOME": str(tmp_path), "PATH": ""},
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout == "ok:codex"

    def test_codex_hook_diagnostics_are_split(self, tmp_path):
        from agent_brain.agent_integrations import codex_diagnostics
        from agent_brain.agent_integrations.codex_hook_diagnostics import diagnose_hooks_json

        hooks_json = tmp_path / ".codex" / "hooks.json"
        hooks_json.parent.mkdir(parents=True)
        hooks_json.write_text('{"hooks": {"Stop": []}}', encoding="utf-8")
        hooks_dir = tmp_path / "agent_runtime_kit" / "hooks"
        hooks_dir.mkdir(parents=True)

        check = diagnose_hooks_json(
            hooks_json=hooks_json,
            hook_events=("Stop",),
            hook_scripts={"Stop": ("", "session-end-signal.sh")},
            hooks_dir=hooks_dir,
        )

        assert check.name == "Codex hooks.json"
        assert check.status == "error"
        assert "missing hub hook event(s): Stop" in check.detail
        assert codex_diagnostics.diagnose_hooks_json is diagnose_hooks_json

    def test_codex_hook_diagnostics_reject_shadowed_hub_hook_entry(self, tmp_path):
        from agent_brain.agent_integrations.codex_hook_diagnostics import diagnose_hooks_json

        hooks_json = tmp_path / ".codex" / "hooks.json"
        hooks_json.parent.mkdir(parents=True)
        hooks_dir = tmp_path / "agent_runtime_kit" / "hooks"
        hooks_dir.mkdir(parents=True)
        script = hooks_dir / "inject-context.sh"
        script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        hooks_json.write_text(
            json.dumps(
                {
                    "hooks": {
                        "UserPromptSubmit": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "~/.codex/hooks/guard-prompt.sh",
                                    }
                                ],
                            },
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": str(script),
                                        "timeout": 10,
                                    }
                                ],
                            },
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        check = diagnose_hooks_json(
            hooks_json=hooks_json,
            hook_events=("UserPromptSubmit",),
            hook_scripts={"UserPromptSubmit": ("", "inject-context.sh")},
            hooks_dir=hooks_dir,
        )

        assert check.status == "error"
        assert "shadowed" in check.detail
        assert "run: memory adapter install codex" in check.fix

    def test_codex_hook_diagnostics_reject_non_first_hub_hook_command(self, tmp_path):
        from agent_brain.agent_integrations.codex_hook_diagnostics import diagnose_hooks_json

        hooks_json = tmp_path / ".codex" / "hooks.json"
        hooks_json.parent.mkdir(parents=True)
        hooks_dir = tmp_path / "agent_runtime_kit" / "hooks"
        hooks_dir.mkdir(parents=True)
        script = hooks_dir / "inject-context.sh"
        script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        hooks_json.write_text(
            json.dumps(
                {
                    "hooks": {
                        "UserPromptSubmit": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "~/.codex/hooks/guard-prompt.sh",
                                    },
                                    {
                                        "type": "command",
                                        "command": str(script),
                                        "timeout": 10,
                                    },
                                ],
                            },
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        check = diagnose_hooks_json(
            hooks_json=hooks_json,
            hook_events=("UserPromptSubmit",),
            hook_scripts={"UserPromptSubmit": ("", "inject-context.sh")},
            hooks_dir=hooks_dir,
        )

        assert check.status == "error"
        assert "not first" in check.detail
        assert "run: memory adapter install codex" in check.fix

    def test_codex_hook_trust_hash_matches_codex_reference(self):
        from agent_brain.agent_integrations.codex_hook_trust import codex_command_hook_hash

        assert codex_command_hook_hash(
            "UserPromptSubmit",
            matcher=None,
            hook={"type": "command", "command": "~/.codex/hooks/guard-prompt.sh"},
        ) == "sha256:d59b811eb0161083bd1f5a77899773e4ea5a08029fd4838b00f3f9453b542328"
        assert codex_command_hook_hash(
            "SessionStart",
            matcher="startup|resume",
            hook={"type": "command", "command": "~/.codex/hooks/audit-session.sh"},
        ) == "sha256:797cfa40023a1b486007113bd8cba5638b620fcf2ddb3a53339b9117a6b2cfc2"

    def test_codex_hook_diagnostics_reject_untrusted_hub_hook_command(self, tmp_path):
        from agent_brain.agent_integrations.codex_hook_diagnostics import diagnose_hooks_json

        hooks_json = tmp_path / ".codex" / "hooks.json"
        config_toml = tmp_path / ".codex" / "config.toml"
        hooks_json.parent.mkdir(parents=True)
        hooks_dir = tmp_path / "agent_runtime_kit" / "hooks"
        hooks_dir.mkdir(parents=True)
        script = hooks_dir / "inject-context.sh"
        script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        hooks_json.write_text(
            json.dumps(
                {
                    "hooks": {
                        "UserPromptSubmit": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": f"AGENT_MEMORY_HUB_ADAPTER=codex {script}",
                                        "timeout": 10,
                                    },
                                ],
                            },
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        config_toml.write_text("[hooks.state]\n", encoding="utf-8")

        check = diagnose_hooks_json(
            hooks_json=hooks_json,
            hook_events=("UserPromptSubmit",),
            hook_scripts={"UserPromptSubmit": ("", "inject-context.sh")},
            hooks_dir=hooks_dir,
            config_toml=config_toml,
        )

        assert check.status == "error"
        assert "not trusted" in check.detail
        assert "run: memory adapter install codex" in check.fix

    def test_codex_doctor_reports_python_timeout_fallback_when_external_timeout_is_missing(self, monkeypatch):
        from agent_brain.agent_integrations import codex_diagnostics

        monkeypatch.setattr(codex_diagnostics.shutil, "which", lambda _name: None)

        check = codex_diagnostics._diagnose_hook_timeout_tooling()

        assert check.name == "Codex hook timeout tooling"
        assert check.status == "ok"
        assert "external timeout command not found" in check.detail
        assert "Python subprocess timeout fallback" in check.detail
        assert "brew install coreutils" in check.fix

    def test_install_creates_bracketed_block(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import codex as cx_mod
        monkeypatch.setattr(cx_mod, "AGENTS_MD", tmp_path / ".codex" / "AGENTS.md")
        monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", tmp_path / ".codex" / "hooks.json")
        monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", tmp_path / ".codex" / "config.toml")

        adapter = cx_mod.CodexAdapter(brain_dir=tmp_path / ".brain")
        msg = adapter.install()
        assert "installed" in msg

        content = (tmp_path / ".codex" / "AGENTS.md").read_text()
        assert cx_mod.BEGIN in content
        assert cx_mod.END in content
        assert "Agent Memory Discipline" in content  # from the source doc

    def test_install_idempotent_updates_in_place(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import codex as cx_mod
        monkeypatch.setattr(cx_mod, "AGENTS_MD", tmp_path / ".codex" / "AGENTS.md")
        monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", tmp_path / ".codex" / "hooks.json")
        monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", tmp_path / ".codex" / "config.toml")

        adapter = cx_mod.CodexAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        first = (tmp_path / ".codex" / "AGENTS.md").read_text()
        msg = adapter.install()
        # Second run is a no-op when content unchanged.
        assert "up-to-date" in msg
        assert (tmp_path / ".codex" / "AGENTS.md").read_text() == first

    def test_install_preserves_user_content(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import codex as cx_mod
        monkeypatch.setattr(cx_mod, "AGENTS_MD", tmp_path / ".codex" / "AGENTS.md")
        monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", tmp_path / ".codex" / "hooks.json")
        monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", tmp_path / ".codex" / "config.toml")

        # User has their own AGENTS.md content already.
        agents_md = tmp_path / ".codex" / "AGENTS.md"
        agents_md.parent.mkdir(parents=True)
        agents_md.write_text("# My own notes\n\nKeep this around.\n")

        adapter = cx_mod.CodexAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        adapter.uninstall()

        content = agents_md.read_text()
        assert "My own notes" in content
        assert cx_mod.BEGIN not in content

    def test_install_creates_codex_hooks(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import codex as cx_mod
        monkeypatch.setattr(cx_mod, "AGENTS_MD", tmp_path / ".codex" / "AGENTS.md")
        monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", tmp_path / ".codex" / "hooks.json")
        monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", tmp_path / ".codex" / "config.toml")

        adapter = cx_mod.CodexAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        hooks = json.loads((tmp_path / ".codex" / "hooks.json").read_text())
        assert "SessionStart" in hooks["hooks"]
        assert "UserPromptSubmit" in hooks["hooks"]
        assert "Stop" in hooks["hooks"]
        assert "PreCompact" in hooks["hooks"]
        assert "PostCompact" in hooks["hooks"]
        assert "SubagentStart" in hooks["hooks"]
        assert "SubagentStop" in hooks["hooks"]
        assert "inject-discipline.sh" in hooks["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        assert "inject-context.sh" in hooks["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        assert "session-end-signal.sh" in hooks["hooks"]["Stop"][0]["hooks"][0]["command"]
        for event in ("PreCompact", "PostCompact", "SubagentStart", "SubagentStop"):
            assert "lifecycle-event.sh" in hooks["hooks"][event][0]["hooks"][0]["command"]
        assert "AGENT_MEMORY_HUB_ADAPTER=codex" in hooks["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        assert "AGENT_MEMORY_HUB_ADAPTER=codex" in hooks["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        assert "AGENT_MEMORY_HUB_ADAPTER=codex" in hooks["hooks"]["Stop"][0]["hooks"][0]["command"]
        assert hooks["hooks"]["SessionStart"][0]["hooks"][0]["timeout"] == 10
        assert hooks["hooks"]["UserPromptSubmit"][0]["hooks"][0]["timeout"] == 10
        assert hooks["hooks"]["Stop"][0]["hooks"][0]["timeout"] == 10
        assert "timeout_ms" not in hooks["hooks"]["UserPromptSubmit"][0]["hooks"][0]
        assert "PreToolUse" not in hooks["hooks"]
        assert "PostToolUse" not in hooks["hooks"]
        assert "PermissionRequest" not in hooks["hooks"]

    def test_install_codex_merges_hub_hooks_into_existing_matching_entries(
        self,
        tmp_path,
        monkeypatch,
    ):
        from agent_brain.agent_integrations import codex as cx_mod
        from agent_brain.agent_integrations.codex_hook_trust import codex_command_hook_hash

        monkeypatch.setattr(cx_mod, "AGENTS_MD", tmp_path / ".codex" / "AGENTS.md")
        monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", tmp_path / ".codex" / "hooks.json")
        monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", tmp_path / ".codex" / "config.toml")
        hooks_json = tmp_path / ".codex" / "hooks.json"
        config_toml = tmp_path / ".codex" / "config.toml"
        hooks_json.parent.mkdir(parents=True)
        hooks_json.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "matcher": "startup|resume",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "~/.codex/hooks/audit-session.sh",
                                    }
                                ],
                            }
                        ],
                        "UserPromptSubmit": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "~/.codex/hooks/guard-prompt.sh",
                                    }
                                ],
                            }
                        ],
                        "Stop": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "~/.codex/hooks/check-stop.sh",
                                    }
                                ],
                            }
                        ],
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        old_prompt_hash = codex_command_hook_hash(
            "UserPromptSubmit",
            matcher=None,
            hook={"type": "command", "command": "~/.codex/hooks/guard-prompt.sh"},
        )
        config_toml.write_text(
            "[hooks.state]\n\n"
            f"[hooks.state.\"{hooks_json}:user_prompt_submit:0:0\"]\n"
            f"trusted_hash = \"{old_prompt_hash}\"\n",
            encoding="utf-8",
        )

        adapter = cx_mod.CodexAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        hooks = json.loads(hooks_json.read_text(encoding="utf-8"))["hooks"]
        assert len(hooks["SessionStart"]) == 1
        assert len(hooks["UserPromptSubmit"]) == 1
        assert len(hooks["Stop"]) == 1
        session_commands = [hook["command"] for hook in hooks["SessionStart"][0]["hooks"]]
        prompt_commands = [hook["command"] for hook in hooks["UserPromptSubmit"][0]["hooks"]]
        stop_commands = [hook["command"] for hook in hooks["Stop"][0]["hooks"]]
        assert any("audit-session.sh" in command for command in session_commands)
        assert any("inject-discipline.sh" in command for command in session_commands)
        assert "inject-discipline.sh" in session_commands[0]
        assert any("guard-prompt.sh" in command for command in prompt_commands)
        assert any("inject-context.sh" in command for command in prompt_commands)
        assert "inject-context.sh" in prompt_commands[0]
        assert any("check-stop.sh" in command for command in stop_commands)
        assert any("session-end-signal.sh" in command for command in stop_commands)
        assert "session-end-signal.sh" in stop_commands[0]
        hub_prompt = next(
            hook for hook in hooks["UserPromptSubmit"][0]["hooks"] if "inject-context.sh" in hook["command"]
        )
        assert hub_prompt["timeout"] == 10
        assert "timeout_ms" not in hub_prompt
        trust = config_toml.read_text(encoding="utf-8")
        hub_prompt_hash = codex_command_hook_hash(
            "UserPromptSubmit",
            matcher=None,
            hook=hub_prompt,
        )
        assert f'[hooks.state."{hooks_json}:user_prompt_submit:0:0"]' in trust
        assert f'trusted_hash = "{hub_prompt_hash}"' in trust
        assert f'[hooks.state."{hooks_json}:user_prompt_submit:0:1"]' in trust
        assert f'trusted_hash = "{old_prompt_hash}"' in trust

    def test_install_codex_hooks_is_idempotent(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import codex as cx_mod
        monkeypatch.setattr(cx_mod, "AGENTS_MD", tmp_path / ".codex" / "AGENTS.md")
        monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", tmp_path / ".codex" / "hooks.json")
        monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", tmp_path / ".codex" / "config.toml")

        adapter = cx_mod.CodexAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        adapter.install()

        hooks = json.loads((tmp_path / ".codex" / "hooks.json").read_text())
        assert len(hooks["hooks"]["SessionStart"]) == 1
        assert len(hooks["hooks"]["UserPromptSubmit"]) == 1
        assert len(hooks["hooks"]["Stop"]) == 1

    def test_install_codex_hooks_migrates_legacy_brain_hook_path(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import codex as cx_mod
        monkeypatch.setattr(cx_mod, "AGENTS_MD", tmp_path / ".codex" / "AGENTS.md")
        monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", tmp_path / ".codex" / "hooks.json")
        monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", tmp_path / ".codex" / "config.toml")

        repo = Path(__file__).resolve().parents[2]
        old_script = repo / "brain" / "hooks" / "inject-context.sh"
        hooks_json = tmp_path / ".codex" / "hooks.json"
        hooks_json.parent.mkdir(parents=True)
        hooks_json.write_text(json.dumps({
            "hooks": {
                "UserPromptSubmit": [
                    {
                        "hooks": [{
                            "type": "command",
                            "command": f"AGENT_MEMORY_HUB_ADAPTER=codex {old_script}",
                            "timeout_ms": 10000,
                        }],
                    },
                ],
            },
        }))

        adapter = cx_mod.CodexAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        hooks = json.loads(hooks_json.read_text(encoding="utf-8"))
        entries = hooks["hooks"]["UserPromptSubmit"]
        commands = [
            hook["command"]
            for entry in entries
            for hook in entry.get("hooks", [])
        ]
        assert len(entries) == 1
        assert any("agent_runtime_kit/hooks/inject-context.sh" in command for command in commands)
        assert not any("/brain/hooks/inject-context.sh" in command for command in commands)

    def test_install_codex_hooks_prunes_stale_checkout_duplicate(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import codex as cx_mod
        monkeypatch.setattr(cx_mod, "AGENTS_MD", tmp_path / ".codex" / "AGENTS.md")
        monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", tmp_path / ".codex" / "hooks.json")
        monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", tmp_path / ".codex" / "config.toml")

        repo = Path(__file__).resolve().parents[2]
        current_script = repo / "agent_runtime_kit" / "hooks" / "inject-context.sh"
        stale_script = (
            "/private/var/folders/example/T/amh-bench-XXXXXX.old/agent-memory-hub/"
            "agent_runtime_kit/hooks/inject-context.sh"
        )
        hooks_json = tmp_path / ".codex" / "hooks.json"
        hooks_json.parent.mkdir(parents=True)
        hooks_json.write_text(json.dumps({
            "hooks": {
                "UserPromptSubmit": [
                    {
                        "hooks": [
                            {"type": "command", "command": stale_script, "timeout": 10},
                            {"type": "command", "command": str(current_script), "timeout": 10},
                            {"type": "command", "command": "~/.codex/hooks/guard-prompt.sh"},
                        ],
                    },
                ],
            },
        }))

        adapter = cx_mod.CodexAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        hooks = json.loads(hooks_json.read_text(encoding="utf-8"))["hooks"]
        commands = [hook["command"] for hook in hooks["UserPromptSubmit"][0]["hooks"]]
        assert "agent_runtime_kit/hooks/inject-context.sh" in commands[0]
        assert sum("agent_runtime_kit/hooks/inject-context.sh" in command for command in commands) == 1
        assert any("~/.codex/hooks/guard-prompt.sh" in command for command in commands)
        assert not any("amh-bench-XXXXXX.old" in command for command in commands)

    def test_install_codex_hooks_repairs_wrong_hub_script_for_event(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import codex as cx_mod
        monkeypatch.setattr(cx_mod, "AGENTS_MD", tmp_path / ".codex" / "AGENTS.md")
        monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", tmp_path / ".codex" / "hooks.json")
        monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", tmp_path / ".codex" / "config.toml")

        hooks_json = tmp_path / ".codex" / "hooks.json"
        hooks_json.parent.mkdir(parents=True)
        wrong_script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
        hooks_json.write_text(json.dumps({
            "hooks": {
                "SessionStart": [
                    {"matcher": "startup|resume", "hooks": [{"type": "command", "command": str(wrong_script)}]},
                ],
            },
        }))

        adapter = cx_mod.CodexAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        hooks = json.loads(hooks_json.read_text())
        commands = [
            hook["command"]
            for entry in hooks["hooks"]["SessionStart"]
            for hook in entry["hooks"]
        ]
        assert any(command.endswith("inject-discipline.sh") for command in commands)

    def test_uninstall_preserves_unrelated_codex_hook(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import codex as cx_mod
        monkeypatch.setattr(cx_mod, "AGENTS_MD", tmp_path / ".codex" / "AGENTS.md")
        monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", tmp_path / ".codex" / "hooks.json")
        monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", tmp_path / ".codex" / "config.toml")

        hooks_json = tmp_path / ".codex" / "hooks.json"
        hooks_json.parent.mkdir(parents=True)
        hooks_json.write_text(json.dumps({
            "hooks": {
                "SessionStart": [
                    {"matcher": "startup|resume", "hooks": [{"type": "command", "command": "/usr/local/bin/their-tool"}]},
                ],
            },
        }))

        adapter = cx_mod.CodexAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        adapter.uninstall()

        hooks = json.loads(hooks_json.read_text())
        remaining = hooks["hooks"]["SessionStart"]
        assert len(remaining) == 1
        assert "their-tool" in remaining[0]["hooks"][0]["command"]


class TestQoderAdapterRealInstall:
    """Exercises qoder adapter's documented hooks config against a tmp HOME."""

    @pytest.fixture(autouse=True)
    def _isolate_native_memories(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import qoder as qoder_mod

        monkeypatch.setattr(
            qoder_mod,
            "QODER_MEMORIES_DIR",
            tmp_path / ".qoder" / "memories",
            raising=False,
        )
        monkeypatch.setattr(
            qoder_mod,
            "QODER_LOCAL_DB_PATH",
            tmp_path / "Library" / "Application Support" / "Qoder" / "SharedClientCache" / "cache" / "db" / "local.db",
            raising=False,
        )

    def test_qoder_diagnostics_are_split_and_reused(self):
        from agent_brain.agent_integrations import qoder as qoder_mod
        from agent_brain.agent_integrations.qoder_diagnostics import diagnose_settings_hooks

        assert "diagnose_settings_hooks" in qoder_mod.QoderAdapter._diagnose_settings_hooks.__code__.co_names
        assert callable(diagnose_settings_hooks)

    def test_qoder_hook_command_uses_shared_hook_config(self):
        from agent_brain.agent_integrations import qoder as qoder_mod
        from agent_brain.agent_integrations.hook_config import adapter_hook_command

        assert qoder_mod._adapter_hook_command is adapter_hook_command

    def test_subprocess_honors_qoder_workspace_path_env_overrides(self, tmp_path):
        projects_dir = tmp_path / "isolated-projects"
        memories_dir = tmp_path / "isolated-memories"
        code = (
            "from agent_brain.agent_integrations import qoder as q\n"
            "print(q.QODER_PROJECTS_DIR)\n"
            "print(q.QODER_MEMORIES_DIR)\n"
        )

        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                "AGENT_MEMORY_HUB_QODER_PROJECTS_DIR": str(projects_dir),
                "AGENT_MEMORY_HUB_QODER_MEMORIES_DIR": str(memories_dir),
            },
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [str(projects_dir), str(memories_dir)]

    def test_workspace_awareness_can_be_disabled_by_env(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import qoder as qoder_mod

        workspace = tmp_path / "real-workspace"
        workspace.mkdir()
        projects_dir = tmp_path / ".qoder" / "projects"
        transcript_dir = projects_dir / "-tmp-real-workspace" / "transcript"
        transcript_dir.mkdir(parents=True)
        transcript_dir.joinpath("session.jsonl").write_text(
            json.dumps({"type": "session_meta", "cwd": str(workspace)}) + "\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("AGENT_MEMORY_HUB_DISABLE_WORKSPACE_AWARENESS", "1")
        monkeypatch.setattr(qoder_mod, "SETTINGS_PATH", tmp_path / ".qoder" / "settings.json")
        monkeypatch.setattr(qoder_mod, "AWARENESS_PATH", tmp_path / ".qoder" / "AGENTS.md")
        monkeypatch.setattr(
            qoder_mod,
            "MCP_CONFIG_PATH",
            tmp_path / "Library" / "Application Support" / "Qoder" / "SharedClientCache" / "mcp.json",
        )
        monkeypatch.setattr(
            qoder_mod,
            "MCP_EXTENSION_CONFIG_PATH",
            tmp_path
            / "Library"
            / "Application Support"
            / "Qoder"
            / "SharedClientCache"
            / "extension"
            / "local"
            / "mcp.json",
        )
        monkeypatch.setattr(qoder_mod, "QODER_PROJECTS_DIR", projects_dir)

        msg = qoder_mod.QoderAdapter(brain_dir=tmp_path / ".brain").install()

        assert "workspace awareness skipped: disabled" in msg
        assert not (workspace / "AGENTS.md").exists()

    def test_install_creates_settings_with_prompt_and_stop_hooks(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import qoder as qoder_mod

        monkeypatch.delenv("AGENT_MEMORY_HUB_DISABLE_WORKSPACE_AWARENESS", raising=False)
        mcp_path = tmp_path / "Library" / "Application Support" / "Qoder" / "SharedClientCache" / "mcp.json"
        memories_dir = tmp_path / ".qoder" / "memories"
        native_profile = memories_dir / "profile-1"
        native_profile.mkdir(parents=True)
        native_user_info = native_profile / "global" / "user_info" / "用户个人信息.md"
        native_user_info.parent.mkdir(parents=True)
        native_user_info.write_text("用户名为Alpha\n", encoding="utf-8")
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
        monkeypatch.setattr(qoder_mod, "QODER_MEMORIES_DIR", memories_dir, raising=False)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        transcript_dir = tmp_path / ".qoder" / "projects" / "-tmp-workspace" / "transcript"
        transcript_dir.mkdir(parents=True)
        transcript_dir.joinpath("session.jsonl").write_text(
            json.dumps({"type": "session_meta", "cwd": str(workspace)}) + "\n",
            encoding="utf-8",
        )

        adapter = qoder_mod.QoderAdapter(brain_dir=tmp_path / ".brain")
        msg = adapter.install()

        assert "installed 2 hook" in msg
        assert "registered MCP server" in msg
        assert "workspace awareness installed" in msg
        settings = json.loads((tmp_path / ".qoder" / "settings.json").read_text())
        assert set(settings["hooks"].keys()) >= {"UserPromptSubmit", "Stop"}
        assert "SessionStart" not in settings["hooks"]
        prompt_command = settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        assert "inject-context.sh" in prompt_command
        assert "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT=json" in prompt_command
        assert "session-end-signal.sh" in settings["hooks"]["Stop"][0]["hooks"][0]["command"]
        assert "AGENT_MEMORY_HUB_ADAPTER=qoder" in prompt_command
        assert "MEMORY_PYTHON=" in prompt_command
        assert "AGENT_MEMORY_HUB_ADAPTER=qoder" in settings["hooks"]["Stop"][0]["hooks"][0]["command"]
        assert "MEMORY_PYTHON=" in settings["hooks"]["Stop"][0]["hooks"][0]["command"]
        awareness = (tmp_path / ".qoder" / "AGENTS.md").read_text(encoding="utf-8")
        assert "Agent Memory Hub Awareness Channel" in awareness
        assert "Agent: Qoder" in awareness
        assert "search_memory" in awareness
        assert "AMH MCP server" in awareness
        native_bridge = (
            native_profile
            / "global"
            / "user_communication"
            / "Agent_Memory_Hub_共享记忆入口.md"
        )
        native_bridge_text = native_bridge.read_text(encoding="utf-8")
        assert "Agent Memory Hub 共享记忆入口" in native_bridge_text
        assert "Qoder 原生 SearchMemory 不能替代 AMH" in native_bridge_text
        assert "python -m agent_brain.interfaces.cli search" in native_bridge_text
        native_user_info_bridge = (
            native_profile
            / "global"
            / "user_info"
            / "Agent_Memory_Hub_共享记忆入口.md"
        )
        native_user_info_text = native_user_info_bridge.read_text(encoding="utf-8")
        assert "用户个人信息" in native_user_info_text
        assert "先检索 AMH" in native_user_info_text
        augmented_user_info = native_user_info.read_text(encoding="utf-8")
        assert "用户名为Alpha" in augmented_user_info
        assert "BEGIN agent-memory-hub-native-redirect" in augmented_user_info
        assert augmented_user_info.lstrip().startswith(qoder_mod.NATIVE_REDIRECT_BEGIN)
        assert augmented_user_info.find(qoder_mod.NATIVE_REDIRECT_END) < augmented_user_info.find("用户名为Alpha")
        assert "原生用户信息只可用于称呼" in augmented_user_info
        assert "必须继续检索 AMH" in augmented_user_info
        workspace_awareness = (workspace / "AGENTS.md").read_text(encoding="utf-8")
        assert "Agent Memory Hub Awareness Channel" in workspace_awareness
        assert "Agent: Qoder / QoderWork" in workspace_awareness
        assert "one-word or short project/name prompts" in workspace_awareness

        mcp = json.loads(mcp_path.read_text())
        server = mcp["mcpServers"]["agent-memory-hub"]
        assert server["command"] == amh_python_executable(adapter.repo_dir)
        assert server["args"] == ["-m", "agent_brain.interfaces.mcp.server"]
        assert server["env"]["BRAIN_DIR"] == str(tmp_path / ".brain")
        assert server["enabled"] is True
        user_mcp_path = tmp_path / "Library" / "Application Support" / "Qoder" / "User" / "mcp.json"
        user_mcp = json.loads(user_mcp_path.read_text())
        user_server = user_mcp["mcpServers"]["agent-memory-hub"]
        assert user_server == server

        extension_mcp = json.loads(extension_mcp_path.read_text())
        extension_server = extension_mcp["mcpServers"]["agent-memory-hub"]
        assert extension_server["command"] == amh_python_executable(adapter.repo_dir)
        assert extension_server["args"] == ["-m", "agent_brain.interfaces.mcp.server"]
        assert extension_server["disabled"] is False
        assert extension_mcp["userConfigMD5"] == hashlib.md5(mcp_path.read_bytes()).hexdigest()

    def test_install_moves_qoder_native_database_redirect_before_user_info(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import qoder as qoder_mod

        memories_dir = tmp_path / ".qoder" / "memories"
        native_profile = memories_dir / "profile-1"
        native_user_info = native_profile / "global" / "user_info" / "用户个人信息.md"
        native_user_info.parent.mkdir(parents=True)
        native_user_info.write_text("用户名为Alpha\n", encoding="utf-8")
        local_db = (
            tmp_path
            / "Library"
            / "Application Support"
            / "Qoder"
            / "SharedClientCache"
            / "cache"
            / "db"
            / "local.db"
        )
        local_db.parent.mkdir(parents=True)
        with sqlite3.connect(local_db) as connection:
            connection.execute(
                """
                create table agent_memory (
                    id text primary key,
                    gmt_modified integer,
                    keywords text,
                    title text,
                    content text not null
                )
                """
            )
            connection.execute(
                """
                insert into agent_memory (id, gmt_modified, keywords, title, content)
                values (?, ?, ?, ?, ?)
                """,
                ("user-info", 1, "用户名,Alpha", "用户个人信息", "用户名为Alpha\n"),
            )

        monkeypatch.setattr(qoder_mod, "SETTINGS_PATH", tmp_path / ".qoder" / "settings.json")
        monkeypatch.setattr(qoder_mod, "AWARENESS_PATH", tmp_path / ".qoder" / "AGENTS.md")
        monkeypatch.setattr(
            qoder_mod,
            "MCP_CONFIG_PATH",
            tmp_path / "Library" / "Application Support" / "Qoder" / "SharedClientCache" / "mcp.json",
        )
        monkeypatch.setattr(
            qoder_mod,
            "MCP_EXTENSION_CONFIG_PATH",
            tmp_path
            / "Library"
            / "Application Support"
            / "Qoder"
            / "SharedClientCache"
            / "extension"
            / "local"
            / "mcp.json",
        )
        monkeypatch.setattr(qoder_mod, "QODER_PROJECTS_DIR", tmp_path / ".qoder" / "projects")
        monkeypatch.setattr(qoder_mod, "QODER_MEMORIES_DIR", memories_dir, raising=False)
        monkeypatch.setattr(qoder_mod, "QODER_LOCAL_DB_PATH", local_db, raising=False)

        adapter = qoder_mod.QoderAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        with sqlite3.connect(local_db) as connection:
            content, keywords, gmt_modified = connection.execute(
                """
                select content, keywords, gmt_modified
                from agent_memory
                where title = ?
                """,
                ("用户个人信息",),
            ).fetchone()
        assert content.lstrip().startswith(qoder_mod.NATIVE_REDIRECT_BEGIN)
        assert content.find(qoder_mod.NATIVE_REDIRECT_END) < content.find("用户名为Alpha")
        assert "agent-memory-hub" in keywords
        assert gmt_modified > 1

    def test_install_cleans_duplicate_qoder_native_bridge_memories(self, tmp_path, monkeypatch):
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
        memories_dir = tmp_path / ".qoder" / "memories"
        native_profile = memories_dir / "profile-1"
        bridge_dir = native_profile / "global" / "user_communication"
        bridge_dir.mkdir(parents=True)
        old_bridge = bridge_dir / "Agent_Memory_Hub共享记忆入口.md"
        duplicate_bridge = bridge_dir / "Agent_Memory_Hub_共享记忆入口_1.md"
        old_bridge.write_text(
            "Agent Memory Hub 共享记忆入口\nQoder 原生 SearchMemory 不能替代 AMH\nBRAIN_DIR=/tmp/pytest-old",
            encoding="utf-8",
        )
        duplicate_bridge.write_text(
            "Agent Memory Hub 共享记忆入口\nQoder 原生 SearchMemory 不能替代 AMH\nBRAIN_DIR=/tmp/pytest-dup",
            encoding="utf-8",
        )
        user_note = bridge_dir / "Agent_Memory_Hub_用户笔记.md"
        user_note.write_text("Qoder 原生 SearchMemory 不能替代 AMH 这句话被用户引用，但不是 AMH 管理文件", encoding="utf-8")

        monkeypatch.setattr(qoder_mod, "SETTINGS_PATH", tmp_path / ".qoder" / "settings.json")
        monkeypatch.setattr(qoder_mod, "AWARENESS_PATH", tmp_path / ".qoder" / "AGENTS.md")
        monkeypatch.setattr(qoder_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(qoder_mod, "MCP_EXTENSION_CONFIG_PATH", extension_mcp_path)
        monkeypatch.setattr(qoder_mod, "QODER_PROJECTS_DIR", tmp_path / ".qoder" / "projects")
        monkeypatch.setattr(qoder_mod, "QODER_MEMORIES_DIR", memories_dir, raising=False)

        adapter = qoder_mod.QoderAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        target = bridge_dir / "Agent_Memory_Hub_共享记忆入口.md"
        assert target.exists()
        assert str(tmp_path / ".brain") in target.read_text(encoding="utf-8")
        assert not old_bridge.exists()
        assert not duplicate_bridge.exists()
        assert user_note.exists()

    def test_install_moves_qoder_prompt_hook_before_existing_guard(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import qoder as qoder_mod

        settings_path = tmp_path / ".qoder" / "settings.json"
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
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps({
            "hooks": {
                "UserPromptSubmit": [
                    {"matcher": "*", "hooks": [{"type": "command", "command": "~/.qoder/hooks/guard-prompt.sh"}]},
                ],
            },
        }), encoding="utf-8")
        monkeypatch.setattr(qoder_mod, "SETTINGS_PATH", settings_path)
        monkeypatch.setattr(qoder_mod, "AWARENESS_PATH", tmp_path / ".qoder" / "AGENTS.md")
        monkeypatch.setattr(qoder_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(qoder_mod, "MCP_EXTENSION_CONFIG_PATH", extension_mcp_path)
        monkeypatch.setattr(qoder_mod, "QODER_PROJECTS_DIR", tmp_path / ".qoder" / "projects")

        adapter = qoder_mod.QoderAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        settings = json.loads(settings_path.read_text())
        commands = [
            entry["hooks"][0]["command"]
            for entry in settings["hooks"]["UserPromptSubmit"]
        ]
        assert "inject-context.sh" in commands[0]
        assert "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT=json" in commands[0]
        assert "guard-prompt.sh" in commands[1]

    def test_install_is_idempotent(self, tmp_path, monkeypatch):
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

        adapter = qoder_mod.QoderAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        first = (tmp_path / ".qoder" / "settings.json").read_text()
        first_awareness = (tmp_path / ".qoder" / "AGENTS.md").read_text(encoding="utf-8")
        first_mcp = mcp_path.read_text()
        first_extension_mcp = extension_mcp_path.read_text()
        msg = adapter.install()

        assert "already installed" in msg
        assert (tmp_path / ".qoder" / "settings.json").read_text() == first
        assert (tmp_path / ".qoder" / "AGENTS.md").read_text(encoding="utf-8") == first_awareness
        assert mcp_path.read_text() == first_mcp
        assert extension_mcp_path.read_text() == first_extension_mcp

    def test_install_replaces_stale_qoder_mcp_paths(self, tmp_path, monkeypatch):
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
        mcp_path.parent.mkdir(parents=True)
        mcp_path.write_text(
            json.dumps({
                "mcpServers": {
                    "agent-memory-hub": {"command": "/old/brain/mcp/server.sh"},
                    "their-server": {"command": "/bin/true"},
                },
            }),
            encoding="utf-8",
        )
        extension_mcp_path.parent.mkdir(parents=True)
        extension_mcp_path.write_text(
            json.dumps({
                "mcpServers": {
                    "agent-memory-hub": {
                        "identifier": "keep-me",
                        "command": "/old/brain/mcp/server.sh",
                        "args": None,
                        "disabled": True,
                        "description": "",
                        "timeout": 0,
                    },
                    "their-server": {"command": "/bin/true"},
                },
                "userConfigMD5": "stale",
            }),
            encoding="utf-8",
        )

        adapter = qoder_mod.QoderAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        mcp = json.loads(mcp_path.read_text())
        server = mcp["mcpServers"]["agent-memory-hub"]
        assert server["command"] == amh_python_executable(adapter.repo_dir)
        assert server["args"] == ["-m", "agent_brain.interfaces.mcp.server"]
        assert "their-server" in mcp["mcpServers"]
        extension_mcp = json.loads(extension_mcp_path.read_text())
        extension_server = extension_mcp["mcpServers"]["agent-memory-hub"]
        assert extension_server["identifier"] == "keep-me"
        assert extension_server["command"] == amh_python_executable(adapter.repo_dir)
        assert extension_server["disabled"] is False
        assert extension_server["description"] == "Agent Memory Hub shared memory MCP server"
        assert extension_server["timeout"] == 60
        assert extension_mcp["userConfigMD5"] == hashlib.md5(mcp_path.read_bytes()).hexdigest()

    def test_uninstall_removes_only_hub_hooks(self, tmp_path, monkeypatch):
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
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(json.dumps({
            "hooks": {
                "UserPromptSubmit": [
                    {"matcher": "", "hooks": [{"type": "command", "command": "/usr/local/bin/their-tool"}]},
                ],
            },
        }))
        awareness_path.write_text("# User Qoder instructions\n", encoding="utf-8")

        adapter = qoder_mod.QoderAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        adapter.uninstall()

        settings = json.loads(settings_path.read_text())
        remaining = settings["hooks"]["UserPromptSubmit"]
        assert len(remaining) == 1
        assert "their-tool" in remaining[0]["hooks"][0]["command"]
        awareness = awareness_path.read_text(encoding="utf-8")
        assert "# User Qoder instructions" in awareness
        assert "Agent Memory Hub Awareness Channel" not in awareness
        user_mcp_path = tmp_path / "Library" / "Application Support" / "Qoder" / "User" / "mcp.json"
        assert "agent-memory-hub" not in json.loads(user_mcp_path.read_text())["mcpServers"]
        assert "agent-memory-hub" not in json.loads(mcp_path.read_text())["mcpServers"]
        assert "agent-memory-hub" not in json.loads(extension_mcp_path.read_text())["mcpServers"]

    def test_diagnose_reports_runtime_warning_after_install(self, tmp_path, monkeypatch):
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

        adapter = qoder_mod.QoderAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        report = adapter.diagnose()
        data = report.to_dict()

        assert data["adapter"] == "qoder"
        assert data["overall_status"] == "warn"
        assert {check["name"] for check in data["checks"]} >= {
            "Qoder settings hooks",
            "Qoder prompt hook injection mode",
            "Qoder hook scripts",
            "Qoder awareness channel",
            "Qoder workspace awareness",
            "Qoder native memory bridge",
            "Qoder MCP user profile",
            "Qoder MCP shared cache",
            "Qoder MCP extension cache",
            "Qoder runtime evidence",
        }
        runtime = [check for check in data["checks"] if check["name"] == "Qoder runtime evidence"][0]
        assert runtime["status"] == "warn"

    def test_diagnose_warns_when_latest_qoder_session_only_used_native_memory(
        self,
        tmp_path,
        monkeypatch,
    ):
        from agent_brain.agent_integrations import qoder as qoder_mod
        from agent_brain.agent_integrations.runtime_events import record_runtime_event

        settings_path = tmp_path / ".qoder" / "settings.json"
        projects_dir = tmp_path / ".qoder" / "projects"
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
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        transcript_dir = projects_dir / "-tmp-workspace" / "transcript"
        transcript_dir.mkdir(parents=True)
        session_id = "qoder-native-only"
        transcript_dir.joinpath(f"{session_id}.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({
                        "type": "session_meta",
                        "sessionId": session_id,
                        "cwd": str(workspace),
                        "timestamp": "2026-01-01T00:00:00Z",
                    }),
                    json.dumps({
                        "type": "progress",
                        "sessionId": session_id,
                        "cwd": str(workspace),
                        "timestamp": "2026-01-01T00:00:01Z",
                        "data": {
                            "hookEvent": "UserPromptSubmit",
                            "command": "AGENT_MEMORY_HUB_ADAPTER=qoder inject-context.sh",
                        },
                    }),
                    json.dumps({
                        "type": "assistant",
                        "sessionId": session_id,
                        "cwd": str(workspace),
                        "timestamp": "2026-01-01T00:00:02Z",
                        "message": {
                            "role": "assistant",
                            "content": [{
                                "type": "tool_use",
                                "name": "SearchMemory",
                                "input": {"query": "用户个人信息"},
                            }],
                        },
                    }),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(qoder_mod, "SETTINGS_PATH", settings_path)
        monkeypatch.setattr(qoder_mod, "AWARENESS_PATH", tmp_path / ".qoder" / "AGENTS.md")
        monkeypatch.setattr(qoder_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(qoder_mod, "MCP_EXTENSION_CONFIG_PATH", extension_mcp_path)
        monkeypatch.setattr(qoder_mod, "QODER_PROJECTS_DIR", projects_dir)

        brain_dir = tmp_path / ".brain"
        adapter = qoder_mod.QoderAdapter(brain_dir=brain_dir)
        adapter.install()
        record_runtime_event(
            brain_dir,
            adapter="qoder",
            event_name="UserPromptSubmit",
            session_id=session_id,
        )

        report = adapter.diagnose()

        assert report.overall_status == "warn"
        effectiveness = next(
            check for check in report.checks if check.name == "Qoder client AMH effectiveness"
        )
        assert effectiveness.status == "warn"
        assert "native SearchMemory" in effectiveness.detail

    def test_diagnose_treats_native_only_session_before_bridge_refresh_as_stale(
        self,
        tmp_path,
        monkeypatch,
    ):
        from agent_brain.agent_integrations import qoder as qoder_mod

        settings_path = tmp_path / ".qoder" / "settings.json"
        projects_dir = tmp_path / ".qoder" / "projects"
        memories_dir = tmp_path / ".qoder" / "memories"
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
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        native_user_info = memories_dir / "profile-1" / "global" / "user_info" / "用户个人信息.md"
        native_user_info.parent.mkdir(parents=True)
        native_user_info.write_text("用户名为Alpha\n", encoding="utf-8")
        transcript_dir = projects_dir / "-tmp-workspace" / "transcript"
        transcript_dir.mkdir(parents=True)
        session_id = "qoder-native-only-before-bridge"
        transcript_path = transcript_dir / f"{session_id}.jsonl"
        transcript_path.write_text(
            "\n".join(
                [
                    json.dumps({"type": "session_meta", "sessionId": session_id, "cwd": str(workspace)}),
                    json.dumps({
                        "type": "progress",
                        "sessionId": session_id,
                        "cwd": str(workspace),
                        "data": {
                            "hookEvent": "UserPromptSubmit",
                            "command": "AGENT_MEMORY_HUB_ADAPTER=qoder inject-context.sh",
                        },
                    }),
                    json.dumps({
                        "type": "assistant",
                        "sessionId": session_id,
                        "cwd": str(workspace),
                        "message": {
                            "role": "assistant",
                            "content": [{
                                "type": "tool_use",
                                "name": "SearchMemory",
                                "input": {"query": "用户个人信息"},
                            }],
                        },
                    }),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        os.utime(transcript_path, (1000, 1000))

        monkeypatch.setattr(qoder_mod, "SETTINGS_PATH", settings_path)
        monkeypatch.setattr(qoder_mod, "AWARENESS_PATH", tmp_path / ".qoder" / "AGENTS.md")
        monkeypatch.setattr(qoder_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(qoder_mod, "MCP_EXTENSION_CONFIG_PATH", extension_mcp_path)
        monkeypatch.setattr(qoder_mod, "QODER_PROJECTS_DIR", projects_dir)
        monkeypatch.setattr(qoder_mod, "QODER_MEMORIES_DIR", memories_dir, raising=False)

        adapter = qoder_mod.QoderAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        report = adapter.diagnose()

        effectiveness = next(
            check for check in report.checks if check.name == "Qoder client AMH effectiveness"
        )
        assert effectiveness.status == "warn"
        assert "predates the current Qoder native AMH bridge" in effectiveness.detail
        assert "latest AMH-hooked Qoder session used native SearchMemory" not in effectiveness.detail

    def test_qoder_transcript_discovery_skips_non_object_json_rows(
        self,
        tmp_path,
        monkeypatch,
    ):
        from agent_brain.agent_integrations import qoder as qoder_mod

        transcript = tmp_path / "session.jsonl"
        transcript.write_text(
            "\n".join(
                [
                    '"scalar"',
                    "42",
                    "null",
                    '["list"]',
                    json.dumps(
                        {
                            "timestamp": "2026-07-19T00:00:00Z",
                            "cwd": str(tmp_path),
                            "data": {
                                "command": (
                                    "AGENT_MEMORY_HUB_ADAPTER=qoder "
                                    "inject-context.sh"
                                )
                            },
                        }
                    ),
                    "{broken",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        adapter = qoder_mod.QoderAdapter(brain_dir=tmp_path / "brain")

        assert adapter._cwd_from_transcript(transcript) == tmp_path
        assert adapter._transcript_observed_time(transcript) is not None
        assert adapter._classify_transcript_effectiveness(transcript) == "unknown"
        monkeypatch.setattr(qoder_mod, "QODER_PROJECTS_DIR", tmp_path)
        effectiveness = adapter._diagnose_client_effectiveness()
        assert effectiveness.status == "warn"
        assert "Traceback" not in effectiveness.detail

    def test_diagnose_orders_qoder_transcripts_by_internal_timestamp_not_file_mtime(
        self,
        tmp_path,
        monkeypatch,
    ):
        from agent_brain.agent_integrations import qoder as qoder_mod

        settings_path = tmp_path / ".qoder" / "settings.json"
        projects_dir = tmp_path / ".qoder" / "projects"
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
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        transcript_dir = projects_dir / "-tmp-workspace" / "transcript"
        transcript_dir.mkdir(parents=True)

        old_amh = transcript_dir / "old-amh.jsonl"
        old_amh.write_text(
            "\n".join([
                json.dumps({
                    "type": "progress",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "cwd": str(workspace),
                    "data": {"command": "AGENT_MEMORY_HUB_ADAPTER=qoder inject-context.sh"},
                }),
                json.dumps({
                    "type": "assistant",
                    "timestamp": "2026-01-01T00:00:01Z",
                    "cwd": str(workspace),
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "tool_use", "name": "search_memory"}],
                    },
                }),
            ])
            + "\n",
            encoding="utf-8",
        )
        new_native_only = transcript_dir / "new-native-only.jsonl"
        new_native_only.write_text(
            "\n".join([
                json.dumps({
                    "type": "progress",
                    "timestamp": "2026-01-02T00:00:00Z",
                    "cwd": str(workspace),
                    "data": {"command": "AGENT_MEMORY_HUB_ADAPTER=qoder inject-context.sh"},
                }),
                json.dumps({
                    "type": "assistant",
                    "timestamp": "2026-01-02T00:00:01Z",
                    "cwd": str(workspace),
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "tool_use", "name": "SearchMemory"}],
                    },
                }),
            ])
            + "\n",
            encoding="utf-8",
        )
        os.utime(old_amh, (3000, 3000))
        os.utime(new_native_only, (1000, 1000))

        monkeypatch.setattr(qoder_mod, "SETTINGS_PATH", settings_path)
        monkeypatch.setattr(qoder_mod, "AWARENESS_PATH", tmp_path / ".qoder" / "AGENTS.md")
        monkeypatch.setattr(qoder_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(qoder_mod, "MCP_EXTENSION_CONFIG_PATH", extension_mcp_path)
        monkeypatch.setattr(qoder_mod, "QODER_PROJECTS_DIR", projects_dir)

        adapter = qoder_mod.QoderAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        report = adapter.diagnose()

        effectiveness = next(
            check for check in report.checks if check.name == "Qoder client AMH effectiveness"
        )
        assert effectiveness.status == "warn"
        assert "native SearchMemory" in effectiveness.detail
        assert "new-native-only.jsonl" in effectiveness.detail

    def test_diagnose_reports_qoder_native_memory_redirect(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import qoder as qoder_mod

        settings_path = tmp_path / ".qoder" / "settings.json"
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
        memories_dir = tmp_path / ".qoder" / "memories"
        native_user_info = memories_dir / "profile-1" / "global" / "user_info" / "用户个人信息.md"
        native_user_info.parent.mkdir(parents=True)
        native_user_info.write_text("用户名为Alpha\n", encoding="utf-8")

        monkeypatch.setattr(qoder_mod, "SETTINGS_PATH", settings_path)
        monkeypatch.setattr(qoder_mod, "AWARENESS_PATH", tmp_path / ".qoder" / "AGENTS.md")
        monkeypatch.setattr(qoder_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(qoder_mod, "MCP_EXTENSION_CONFIG_PATH", extension_mcp_path)
        monkeypatch.setattr(qoder_mod, "QODER_PROJECTS_DIR", tmp_path / ".qoder" / "projects")
        monkeypatch.setattr(qoder_mod, "QODER_MEMORIES_DIR", memories_dir, raising=False)

        adapter = qoder_mod.QoderAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        report = adapter.diagnose()

        check = next(check for check in report.checks if check.name == "Qoder native memory bridge")
        assert check.status == "ok"
        assert "priority redirect" in check.detail

    def test_diagnose_rejects_qoder_native_database_redirect_after_user_info(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import qoder as qoder_mod

        settings_path = tmp_path / ".qoder" / "settings.json"
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
        local_db = (
            tmp_path
            / "Library"
            / "Application Support"
            / "Qoder"
            / "SharedClientCache"
            / "cache"
            / "db"
            / "local.db"
        )
        local_db.parent.mkdir(parents=True)
        with sqlite3.connect(local_db) as connection:
            connection.execute(
                """
                create table agent_memory (
                    id text primary key,
                    gmt_modified integer,
                    keywords text,
                    title text,
                    content text not null
                )
                """
            )
            connection.execute(
                """
                insert into agent_memory (id, gmt_modified, keywords, title, content)
                values (?, ?, ?, ?, ?)
                """,
                ("user-info", 1, "用户名,Alpha", "用户个人信息", "用户名为Alpha\n"),
            )

        memories_dir = tmp_path / ".qoder" / "memories"
        native_user_info = memories_dir / "profile-1" / "global" / "user_info" / "用户个人信息.md"
        native_user_info.parent.mkdir(parents=True)
        native_user_info.write_text("用户名为Alpha\n", encoding="utf-8")

        monkeypatch.setattr(qoder_mod, "SETTINGS_PATH", settings_path)
        monkeypatch.setattr(qoder_mod, "AWARENESS_PATH", tmp_path / ".qoder" / "AGENTS.md")
        monkeypatch.setattr(qoder_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(qoder_mod, "MCP_EXTENSION_CONFIG_PATH", extension_mcp_path)
        monkeypatch.setattr(qoder_mod, "QODER_PROJECTS_DIR", tmp_path / ".qoder" / "projects")
        monkeypatch.setattr(qoder_mod, "QODER_MEMORIES_DIR", memories_dir, raising=False)
        monkeypatch.setattr(qoder_mod, "QODER_LOCAL_DB_PATH", local_db, raising=False)

        adapter = qoder_mod.QoderAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        with sqlite3.connect(local_db) as connection:
            connection.execute(
                """
                update agent_memory
                set content = ?
                where title = ?
                """,
                (
                    "用户名为Alpha\n\n"
                    + adapter._native_priority_redirect_block()
                    + "\n",
                    "用户个人信息",
                ),
            )

        report = adapter.diagnose()

        check = next(check for check in report.checks if check.name == "Qoder native memory bridge")
        assert check.status == "error"
        assert "database priority redirect is not first" in check.detail

    def test_diagnose_reports_missing_settings_without_writing(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import qoder as qoder_mod

        settings_path = tmp_path / ".qoder" / "settings.json"
        monkeypatch.setattr(qoder_mod, "SETTINGS_PATH", settings_path)
        monkeypatch.setattr(qoder_mod, "AWARENESS_PATH", tmp_path / ".qoder" / "AGENTS.md")
        monkeypatch.setattr(
            qoder_mod,
            "MCP_CONFIG_PATH",
            tmp_path / "Library" / "Application Support" / "Qoder" / "SharedClientCache" / "mcp.json",
        )
        monkeypatch.setattr(
            qoder_mod,
            "MCP_EXTENSION_CONFIG_PATH",
            tmp_path
            / "Library"
            / "Application Support"
            / "Qoder"
            / "SharedClientCache"
            / "extension"
            / "local"
            / "mcp.json",
        )
        monkeypatch.setattr(qoder_mod, "QODER_PROJECTS_DIR", tmp_path / ".qoder" / "projects")

        adapter = qoder_mod.QoderAdapter(brain_dir=tmp_path / ".brain")
        report = adapter.diagnose()

        assert report.overall_status == "error"
        assert not settings_path.exists()
        assert any(check.name == "Qoder settings hooks" for check in report.checks)

    def test_install_registers_codex_mcp_server(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import codex as cx_mod
        monkeypatch.setattr(cx_mod, "AGENTS_MD", tmp_path / ".codex" / "AGENTS.md")
        monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", tmp_path / ".codex" / "hooks.json")
        monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", tmp_path / ".codex" / "config.toml")

        adapter = cx_mod.CodexAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        config = (tmp_path / ".codex" / "config.toml").read_text()
        assert "[mcp_servers.agent-memory-hub]" in config
        assert "agent_runtime_kit/mcp/server.sh" in config

    def test_install_updates_existing_codex_mcp_server_section(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import codex as cx_mod
        monkeypatch.setattr(cx_mod, "AGENTS_MD", tmp_path / ".codex" / "AGENTS.md")
        monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", tmp_path / ".codex" / "hooks.json")
        monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", tmp_path / ".codex" / "config.toml")

        config = tmp_path / ".codex" / "config.toml"
        config.parent.mkdir(parents=True)
        config.write_text(
            "[mcp_servers.agent-memory-hub]\n"
            "command = \"/old/server.sh\"\n\n"
            "[mcp_servers.other]\n"
            "command = \"/bin/other\"\n"
        )

        adapter = cx_mod.CodexAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        content = config.read_text()
        assert "/old/server.sh" not in content
        assert "agent_runtime_kit/mcp/server.sh" in content
        assert "[mcp_servers.other]" in content
        assert "command = \"/bin/other\"" in content

    def test_uninstall_preserves_unrelated_codex_config(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import codex as cx_mod
        monkeypatch.setattr(cx_mod, "AGENTS_MD", tmp_path / ".codex" / "AGENTS.md")
        monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", tmp_path / ".codex" / "hooks.json")
        monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", tmp_path / ".codex" / "config.toml")

        config = tmp_path / ".codex" / "config.toml"
        config.parent.mkdir(parents=True)
        config.write_text("[features]\nhooks = true\n\n[mcp_servers.other]\ncommand = \"/bin/other\"\n")

        adapter = cx_mod.CodexAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        adapter.uninstall()

        remaining = config.read_text()
        assert "[features]" in remaining
        assert "[mcp_servers.other]" in remaining
        assert "agent-memory-hub" not in remaining

    def test_install_refuses_malformed_codex_hooks_json(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import codex as cx_mod
        monkeypatch.setattr(cx_mod, "AGENTS_MD", tmp_path / ".codex" / "AGENTS.md")
        monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", tmp_path / ".codex" / "hooks.json")
        monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", tmp_path / ".codex" / "config.toml")

        hooks_json = tmp_path / ".codex" / "hooks.json"
        hooks_json.parent.mkdir(parents=True)
        hooks_json.write_text("{not json")

        adapter = cx_mod.CodexAdapter(brain_dir=tmp_path / ".brain")

        with pytest.raises(RuntimeError, match="malformed"):
            adapter.install()


class TestQoderWorkAdapterRealInstall:
    """Exercises QoderWork's Qoder-compatible hooks config against a tmp HOME."""

    def test_default_settings_path_matches_qoderwork_client_storage(self):
        from agent_brain.agent_integrations import qoder_work as qw_mod

        assert qw_mod.SETTINGS_PATH == Path.home() / ".qoderwork" / "settings.json"
        cfg = qw_mod.QoderWorkAdapter(brain_dir=Path("/tmp/brain")).get_config()
        assert cfg.config_dir == Path.home() / ".qoderwork"

    def test_subprocess_honors_qoderwork_workspace_path_env_overrides(self, tmp_path):
        projects_dir = tmp_path / "isolated-projects"
        skills_dir = tmp_path / "isolated-skills"
        code = (
            "from agent_brain.agent_integrations import qoder_work as q\n"
            "print(q.QODERWORK_PROJECTS_DIR)\n"
            "print(q.QODERWORK_SKILLS_DIR)\n"
        )

        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                "AGENT_MEMORY_HUB_QODERWORK_PROJECTS_DIR": str(projects_dir),
                "AGENT_MEMORY_HUB_QODERWORK_SKILLS_DIR": str(skills_dir),
            },
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [str(projects_dir), str(skills_dir)]

    def test_workspace_awareness_can_be_disabled_by_env(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import qoder_work as qw_mod

        workspace = tmp_path / "real-workspace"
        workspace.mkdir()
        projects_dir = tmp_path / ".qoderwork" / "projects"
        project_dir = projects_dir / "-tmp-real-workspace"
        project_dir.mkdir(parents=True)
        project_dir.joinpath("session.jsonl").write_text(
            json.dumps({"cwd": str(workspace), "type": "user"}) + "\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("AGENT_MEMORY_HUB_DISABLE_WORKSPACE_AWARENESS", "1")
        monkeypatch.setattr(qw_mod, "SETTINGS_PATH", tmp_path / ".qoderwork" / "settings.json")
        monkeypatch.setattr(qw_mod, "MCP_CONFIG_PATH", tmp_path / ".qoderwork" / "mcp.json")
        monkeypatch.setattr(
            qw_mod,
            "AWARENESS_PATH",
            tmp_path / ".qoderwork" / "awareness" / "main" / "AGENTS.md",
        )
        monkeypatch.setattr(qw_mod, "QODERWORK_PROJECTS_DIR", projects_dir, raising=False)
        monkeypatch.setattr(qw_mod, "QODERWORK_SKILLS_DIR", tmp_path / ".qoderwork" / "skills", raising=False)

        msg = qw_mod.QoderWorkAdapter(brain_dir=tmp_path / ".brain").install()

        assert "workspace awareness skipped: disabled" in msg
        assert not (workspace / "AGENTS.md").exists()

    def test_install_writes_settings_hooks(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import qoder_work as qw_mod

        settings_path = tmp_path / ".qoderwork" / "settings.json"
        mcp_path = tmp_path / ".qoderwork" / "mcp.json"
        awareness_path = tmp_path / ".qoderwork" / "awareness" / "main" / "AGENTS.md"
        projects_dir = tmp_path / ".qoderwork" / "projects"
        skills_dir = tmp_path / ".qoderwork" / "skills"
        monkeypatch.setattr(qw_mod, "SETTINGS_PATH", settings_path)
        monkeypatch.setattr(qw_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(qw_mod, "AWARENESS_PATH", awareness_path)
        monkeypatch.setattr(qw_mod, "QODERWORK_PROJECTS_DIR", projects_dir, raising=False)
        monkeypatch.setattr(qw_mod, "QODERWORK_SKILLS_DIR", skills_dir, raising=False)

        adapter = qw_mod.QoderWorkAdapter(brain_dir=tmp_path / ".brain")
        msg = adapter.install()

        assert "qoder_work adapter" in msg
        assert "registered MCP server" in msg
        settings = json.loads(settings_path.read_text())
        assert "UserPromptSubmit" in settings["hooks"]
        assert "Stop" in settings["hooks"]
        prompt_command = settings["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        stop_command = settings["hooks"]["Stop"][0]["hooks"][0]["command"]
        assert "AGENT_MEMORY_HUB_ADAPTER=qoder_work" in prompt_command
        assert "MEMORY_PYTHON=" in prompt_command
        assert "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT=json" in prompt_command
        assert "AGENT_MEMORY_HUB_ADAPTER=qoder_work" in stop_command
        assert "MEMORY_PYTHON=" in stop_command
        awareness = awareness_path.read_text(encoding="utf-8")
        assert "Agent Memory Hub Awareness Channel" in awareness
        assert "Agent: QoderWork" in awareness
        assert "search_memory" in awareness
        assert "QoderWork awareness/main" in awareness
        assert "one-word or short project/name prompt" in awareness
        assert "use `brief_memory` to recover the overall project state" in awareness
        assert "use `search_memory` with the full task description" in awareness
        mcp = json.loads(mcp_path.read_text())
        server = mcp["mcpServers"]["agent-memory-hub"]
        assert server["args"] == ["-m", "agent_brain.interfaces.mcp.server"]
        assert server["env"] == {
            "BRAIN_DIR": str(tmp_path / ".brain"),
            "PYTHONPATH": str(adapter.repo_dir),
        }
        assert server["enabled"] is True
        skill = skills_dir / "agent-memory-hub-shared-memory" / "SKILL.md"
        skill_text = skill.read_text(encoding="utf-8")
        assert "Agent Memory Hub QoderWork Bootstrap Skill" in skill_text
        assert "mcp__agent-memory-hub__brief_memory" in skill_text
        assert "mcp__agent-memory-hub__search_memory" in skill_text
        assert "短 prompt" in skill_text
        assert "mcp__agent-memory-hub__brief_memory" in skill_text

    def test_install_moves_prompt_hook_first_and_discovers_workspace_awareness(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import qoder_work as qw_mod

        monkeypatch.delenv("AGENT_MEMORY_HUB_DISABLE_WORKSPACE_AWARENESS", raising=False)
        settings_path = tmp_path / ".qoderwork" / "settings.json"
        mcp_path = tmp_path / ".qoderwork" / "mcp.json"
        awareness_path = tmp_path / ".qoderwork" / "awareness" / "main" / "AGENTS.md"
        projects_dir = tmp_path / ".qoderwork" / "projects"
        skills_dir = tmp_path / ".qoderwork" / "skills"
        monkeypatch.setattr(qw_mod, "SETTINGS_PATH", settings_path)
        monkeypatch.setattr(qw_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(qw_mod, "AWARENESS_PATH", awareness_path)
        monkeypatch.setattr(qw_mod, "QODERWORK_PROJECTS_DIR", projects_dir, raising=False)
        monkeypatch.setattr(qw_mod, "QODERWORK_SKILLS_DIR", skills_dir, raising=False)
        settings_path.parent.mkdir(parents=True)
        settings_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "UserPromptSubmit": [
                            {
                                "matcher": "",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "/usr/local/bin/team-guard --prompt",
                                    }
                                ],
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        workspace = tmp_path / "real-workspace"
        workspace.mkdir()
        project_dir = projects_dir / "-tmp-real-workspace"
        project_dir.mkdir(parents=True)
        project_dir.joinpath("session.jsonl").write_text(
            json.dumps({"cwd": str(workspace), "type": "user"}) + "\n",
            encoding="utf-8",
        )
        project_dir.joinpath("session-session.json").write_text(
            json.dumps({"working_dir": str(workspace)}),
            encoding="utf-8",
        )

        adapter = qw_mod.QoderWorkAdapter(brain_dir=tmp_path / ".brain")
        msg = adapter.install()

        assert "workspace awareness" in msg
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        prompt_entries = settings["hooks"]["UserPromptSubmit"]
        first_command = prompt_entries[0]["hooks"][0]["command"]
        guard_command = prompt_entries[1]["hooks"][0]["command"]
        assert "inject-context.sh" in first_command
        assert "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT=json" in first_command
        assert "team-guard" in guard_command
        workspace_awareness = (workspace / "AGENTS.md").read_text(encoding="utf-8")
        assert "Agent Memory Hub Awareness Channel" in workspace_awareness
        assert "Agent: Qoder / QoderWork" in workspace_awareness
        assert "one-word or short project/name prompts" in workspace_awareness

    def test_qoder_family_workspace_awareness_is_shared_and_stable(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import qoder as qoder_mod
        from agent_brain.agent_integrations import qoder_work as qw_mod

        monkeypatch.delenv("AGENT_MEMORY_HUB_DISABLE_WORKSPACE_AWARENESS", raising=False)
        workspace = tmp_path / "shared-workspace"
        workspace.mkdir()

        qoder_projects_dir = tmp_path / ".qoder" / "projects"
        qoder_transcript_dir = qoder_projects_dir / "-tmp-shared-workspace" / "transcript"
        qoder_transcript_dir.mkdir(parents=True)
        qoder_transcript_dir.joinpath("session.jsonl").write_text(
            json.dumps({"type": "session_meta", "cwd": str(workspace)}) + "\n",
            encoding="utf-8",
        )

        qoderwork_projects_dir = tmp_path / ".qoderwork" / "projects"
        qoderwork_project_dir = qoderwork_projects_dir / "-tmp-shared-workspace"
        qoderwork_project_dir.mkdir(parents=True)
        qoderwork_project_dir.joinpath("session.jsonl").write_text(
            json.dumps({"cwd": str(workspace), "type": "user"}) + "\n",
            encoding="utf-8",
        )
        qoderwork_project_dir.joinpath("session-session.json").write_text(
            json.dumps({"working_dir": str(workspace)}),
            encoding="utf-8",
        )

        monkeypatch.setattr(qoder_mod, "SETTINGS_PATH", tmp_path / ".qoder" / "settings.json")
        monkeypatch.setattr(qoder_mod, "AWARENESS_PATH", tmp_path / ".qoder" / "AGENTS.md")
        monkeypatch.setattr(
            qoder_mod,
            "MCP_CONFIG_PATH",
            tmp_path / "Library" / "Application Support" / "Qoder" / "SharedClientCache" / "mcp.json",
        )
        monkeypatch.setattr(
            qoder_mod,
            "MCP_EXTENSION_CONFIG_PATH",
            tmp_path
            / "Library"
            / "Application Support"
            / "Qoder"
            / "SharedClientCache"
            / "extension"
            / "local"
            / "mcp.json",
        )
        monkeypatch.setattr(qoder_mod, "QODER_PROJECTS_DIR", qoder_projects_dir)

        monkeypatch.setattr(qw_mod, "SETTINGS_PATH", tmp_path / ".qoderwork" / "settings.json")
        monkeypatch.setattr(qw_mod, "MCP_CONFIG_PATH", tmp_path / ".qoderwork" / "mcp.json")
        monkeypatch.setattr(qw_mod, "AWARENESS_PATH", tmp_path / ".qoderwork" / "awareness" / "main" / "AGENTS.md")
        monkeypatch.setattr(qw_mod, "QODERWORK_PROJECTS_DIR", qoderwork_projects_dir, raising=False)
        monkeypatch.setattr(qw_mod, "QODERWORK_SKILLS_DIR", tmp_path / ".qoderwork" / "skills", raising=False)

        qoder = qoder_mod.QoderAdapter(brain_dir=tmp_path / ".brain")
        qoderwork = qw_mod.QoderWorkAdapter(brain_dir=tmp_path / ".brain")

        qoder.install()
        first = (workspace / "AGENTS.md").read_text(encoding="utf-8")
        qoderwork.install()
        second = (workspace / "AGENTS.md").read_text(encoding="utf-8")
        qoder.install()
        third = (workspace / "AGENTS.md").read_text(encoding="utf-8")

        assert "Agent: Qoder / QoderWork" in first
        assert first == second == third
        assert "Agent: Qoder\n" not in third
        assert "Agent: QoderWork\n" not in third

    def test_uninstall_removes_only_hub_hooks(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import qoder_work as qw_mod

        settings_path = tmp_path / ".qoderwork" / "settings.json"
        mcp_path = tmp_path / ".qoderwork" / "mcp.json"
        awareness_path = tmp_path / ".qoderwork" / "awareness" / "main" / "AGENTS.md"
        projects_dir = tmp_path / ".qoderwork" / "projects"
        skills_dir = tmp_path / ".qoderwork" / "skills"
        monkeypatch.setattr(qw_mod, "SETTINGS_PATH", settings_path)
        monkeypatch.setattr(qw_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(qw_mod, "AWARENESS_PATH", awareness_path)
        monkeypatch.setattr(qw_mod, "QODERWORK_PROJECTS_DIR", projects_dir, raising=False)
        monkeypatch.setattr(qw_mod, "QODERWORK_SKILLS_DIR", skills_dir, raising=False)
        awareness_path.parent.mkdir(parents=True)
        awareness_path.write_text("# User QoderWork playbook\n", encoding="utf-8")
        mcp_path.parent.mkdir(parents=True, exist_ok=True)
        mcp_path.write_text(
            json.dumps({"mcpServers": {"other-tool": {"command": "other"}}}),
            encoding="utf-8",
        )

        adapter = qw_mod.QoderWorkAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        adapter.uninstall()

        settings = json.loads(settings_path.read_text())
        assert settings["hooks"]["UserPromptSubmit"] == []
        assert settings["hooks"]["Stop"] == []
        awareness = awareness_path.read_text(encoding="utf-8")
        assert "# User QoderWork playbook" in awareness
        assert "Agent Memory Hub Awareness Channel" not in awareness
        mcp = json.loads(mcp_path.read_text())
        assert "other-tool" in mcp["mcpServers"]
        assert "agent-memory-hub" not in mcp["mcpServers"]
        assert not (skills_dir / "agent-memory-hub-shared-memory" / "SKILL.md").exists()

    def test_diagnose_reports_runtime_warning_after_install(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import qoder_work as qw_mod

        settings_path = tmp_path / ".qoderwork" / "settings.json"
        mcp_path = tmp_path / ".qoderwork" / "mcp.json"
        awareness_path = tmp_path / ".qoderwork" / "awareness" / "main" / "AGENTS.md"
        projects_dir = tmp_path / ".qoderwork" / "projects"
        skills_dir = tmp_path / ".qoderwork" / "skills"
        monkeypatch.setattr(qw_mod, "SETTINGS_PATH", settings_path)
        monkeypatch.setattr(qw_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(qw_mod, "AWARENESS_PATH", awareness_path)
        monkeypatch.setattr(qw_mod, "QODERWORK_PROJECTS_DIR", projects_dir, raising=False)
        monkeypatch.setattr(qw_mod, "QODERWORK_SKILLS_DIR", skills_dir, raising=False)

        adapter = qw_mod.QoderWorkAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        report = adapter.diagnose()

        assert report.adapter == "qoder_work"
        assert report.overall_status == "warn"
        assert any(check.name == "QoderWork settings hooks" for check in report.checks)
        assert any(check.name == "QoderWork prompt hook injection mode" for check in report.checks)
        assert any(check.name == "QoderWork hook scripts" for check in report.checks)
        assert any(check.name == "QoderWork awareness channel" for check in report.checks)
        assert any(check.name == "QoderWork bootstrap skill" for check in report.checks)
        assert any(check.name == "QoderWork MCP server" for check in report.checks)
        assert any(check.name == "QoderWork runtime evidence" for check in report.checks)


class TestCursorAdapterRealInstall:
    """Exercises cursor adapter's MCP server registration against a tmp HOME."""

    def test_install_registers_mcp_server(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import cursor as cur_mod
        mcp_path = tmp_path / ".cursor" / "mcp.json"
        awareness_path = tmp_path / ".cursor" / "rules" / "agent-memory-hub.mdc"
        monkeypatch.setattr(cur_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(cur_mod, "AWARENESS_PATH", awareness_path)

        adapter = cur_mod.CursorAdapter(brain_dir=tmp_path / ".brain")
        msg = adapter.install()
        assert "registered" in msg

        config = json.loads(mcp_path.read_text())
        assert "agent-memory-hub" in config["mcpServers"]
        server = config["mcpServers"]["agent-memory-hub"]
        assert server["args"] == ["-m", "agent_brain.interfaces.mcp.server"]
        assert "BRAIN_DIR" in server["env"]
        awareness = awareness_path.read_text(encoding="utf-8")
        assert "Awareness Channel" in awareness
        assert "search_memory" in awareness
        assert "MCP tools are available" in awareness

    def test_install_is_idempotent(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import cursor as cur_mod
        mcp_path = tmp_path / ".cursor" / "mcp.json"
        awareness_path = tmp_path / ".cursor" / "rules" / "agent-memory-hub.mdc"
        monkeypatch.setattr(cur_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(cur_mod, "AWARENESS_PATH", awareness_path)

        adapter = cur_mod.CursorAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        first_awareness = awareness_path.read_text(encoding="utf-8")
        msg = adapter.install()
        assert "already registered" in msg
        assert awareness_path.read_text(encoding="utf-8") == first_awareness

    def test_install_preserves_existing_servers(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import cursor as cur_mod
        mcp_path = tmp_path / ".cursor" / "mcp.json"
        awareness_path = tmp_path / ".cursor" / "rules" / "agent-memory-hub.mdc"
        monkeypatch.setattr(cur_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(cur_mod, "AWARENESS_PATH", awareness_path)

        mcp_path.parent.mkdir(parents=True)
        mcp_path.write_text(json.dumps({
            "mcpServers": {"other-tool": {"command": "other", "args": []}}
        }))

        adapter = cur_mod.CursorAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        config = json.loads(mcp_path.read_text())
        assert "other-tool" in config["mcpServers"]
        assert "agent-memory-hub" in config["mcpServers"]

    def test_install_updates_existing_cursor_mcp_server(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import cursor as cur_mod

        mcp_path = tmp_path / ".cursor" / "mcp.json"
        awareness_path = tmp_path / ".cursor" / "rules" / "agent-memory-hub.mdc"
        monkeypatch.setattr(cur_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(cur_mod, "AWARENESS_PATH", awareness_path)
        mcp_path.parent.mkdir(parents=True)
        mcp_path.write_text(json.dumps({
            "mcpServers": {
                "agent-memory-hub": {"command": "/old/python", "args": [], "env": {}},
                "other-tool": {"command": "other", "args": []},
            }
        }))

        adapter = cur_mod.CursorAdapter(brain_dir=tmp_path / ".brain")
        msg = adapter.install()

        assert "updated MCP server" in msg
        config = json.loads(mcp_path.read_text())
        server = config["mcpServers"]["agent-memory-hub"]
        assert server["command"] == AMH_PYTHON
        assert server["args"] == ["-m", "agent_brain.interfaces.mcp.server"]
        assert server["env"] == {"BRAIN_DIR": str(tmp_path / ".brain")}
        assert "other-tool" in config["mcpServers"]

    def test_uninstall_removes_only_hub_server(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import cursor as cur_mod
        mcp_path = tmp_path / ".cursor" / "mcp.json"
        awareness_path = tmp_path / ".cursor" / "rules" / "agent-memory-hub.mdc"
        monkeypatch.setattr(cur_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(cur_mod, "AWARENESS_PATH", awareness_path)

        mcp_path.parent.mkdir(parents=True)
        mcp_path.write_text(json.dumps({
            "mcpServers": {"other-tool": {"command": "other", "args": []}}
        }))
        awareness_path.parent.mkdir(parents=True, exist_ok=True)
        awareness_path.write_text("# User Cursor rules\n", encoding="utf-8")

        adapter = cur_mod.CursorAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        adapter.uninstall()

        config = json.loads(mcp_path.read_text())
        assert "other-tool" in config["mcpServers"]
        assert "agent-memory-hub" not in config["mcpServers"]
        assert "User Cursor rules" in awareness_path.read_text(encoding="utf-8")
        assert "agent-memory-hub-awareness" not in awareness_path.read_text(encoding="utf-8")

    def test_diagnose_reports_ok_after_install(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import cursor as cur_mod
        mcp_path = tmp_path / ".cursor" / "mcp.json"
        awareness_path = tmp_path / ".cursor" / "rules" / "agent-memory-hub.mdc"
        monkeypatch.setattr(cur_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(cur_mod, "AWARENESS_PATH", awareness_path)

        adapter = cur_mod.CursorAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        report = adapter.diagnose()

        assert report.adapter == "cursor"
        assert report.overall_status == "ok"
        assert all(check.status == "ok" for check in report.checks)
        assert any(check.name == "Cursor MCP server" for check in report.checks)
        assert any(check.name == "Cursor awareness channel" for check in report.checks)

    def test_diagnose_reports_missing_config_without_writing(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import cursor as cur_mod
        mcp_path = tmp_path / ".cursor" / "mcp.json"
        awareness_path = tmp_path / ".cursor" / "rules" / "agent-memory-hub.mdc"
        monkeypatch.setattr(cur_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(cur_mod, "AWARENESS_PATH", awareness_path)

        adapter = cur_mod.CursorAdapter(brain_dir=tmp_path / ".brain")

        report = adapter.diagnose()

        assert report.overall_status == "error"
        assert not mcp_path.exists()
        assert not awareness_path.exists()
        assert any(check.name == "Cursor MCP server" for check in report.checks)
        assert any(check.name == "Cursor awareness channel" for check in report.checks)


class TestOpenClawAdapterRealInstall:
    """Exercises OpenClaw's documented MCP registry CLI path."""

    def test_install_registers_mcp_server_with_openclaw_cli(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import openclaw as oc_mod

        calls: list[list[str]] = []

        def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        monkeypatch.setattr(oc_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(oc_mod, "_run_openclaw", fake_run)
        monkeypatch.setattr(
            oc_mod,
            "AWARENESS_PATH",
            tmp_path / ".openclaw" / "agent-memory-hub-awareness.md",
        )

        adapter = oc_mod.OpenClawAdapter(brain_dir=tmp_path / ".brain")
        msg = adapter.install()

        assert "registered" in msg
        assert calls and calls[0][:4] == ["openclaw", "mcp", "set", "agent-memory-hub"]
        payload = json.loads(calls[0][4])
        assert payload == {
            "command": AMH_PYTHON,
            "args": ["-m", "agent_brain.interfaces.mcp.server"],
            "env": {"BRAIN_DIR": str(tmp_path / ".brain")},
            "enabled": True,
        }
        awareness = oc_mod.AWARENESS_PATH.read_text(encoding="utf-8")
        assert "Awareness Channel" in awareness
        assert "search_memory" in awareness

    def test_install_reports_missing_openclaw_cli(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import openclaw as oc_mod

        monkeypatch.setattr(oc_mod.shutil, "which", lambda _name: None)

        adapter = oc_mod.OpenClawAdapter(brain_dir=tmp_path / ".brain")

        with pytest.raises(RuntimeError, match="openclaw CLI not found"):
            adapter.install()

    def test_install_keeps_awareness_fallback_when_mcp_registration_fails(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import openclaw as oc_mod

        awareness_path = tmp_path / ".openclaw" / "agent-memory-hub-awareness.md"
        monkeypatch.setattr(oc_mod, "AWARENESS_PATH", awareness_path)
        monkeypatch.setattr(oc_mod.shutil, "which", lambda name: f"/usr/bin/{name}")

        def fail_set(_args):
            raise RuntimeError("exit -9")

        monkeypatch.setattr(oc_mod, "_run_openclaw", fail_set)

        adapter = oc_mod.OpenClawAdapter(brain_dir=tmp_path / ".brain")

        with pytest.raises(RuntimeError, match="exit -9"):
            adapter.install()

        awareness = awareness_path.read_text(encoding="utf-8")
        assert "Awareness Channel" in awareness
        assert "OpenClaw" in awareness

    def test_uninstall_removes_only_hub_server_via_cli(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import openclaw as oc_mod

        calls: list[list[str]] = []

        def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        monkeypatch.setattr(oc_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(oc_mod, "_run_openclaw", fake_run)
        awareness_path = tmp_path / ".openclaw" / "agent-memory-hub-awareness.md"
        monkeypatch.setattr(oc_mod, "AWARENESS_PATH", awareness_path)

        adapter = oc_mod.OpenClawAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        msg = adapter.uninstall()

        assert "removed" in msg
        assert calls[-1] == ["openclaw", "mcp", "remove", "agent-memory-hub"]
        assert not awareness_path.exists() or "agent-memory-hub-awareness" not in awareness_path.read_text(encoding="utf-8")

    def test_diagnose_reports_cli_doctor_status(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import openclaw as oc_mod

        calls: list[list[str]] = []

        def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

        monkeypatch.setattr(oc_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(oc_mod, "_run_openclaw", fake_run)
        monkeypatch.setattr(
            oc_mod,
            "AWARENESS_PATH",
            tmp_path / ".openclaw" / "agent-memory-hub-awareness.md",
        )

        adapter = oc_mod.OpenClawAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        report = adapter.diagnose()

        assert report.adapter == "openclaw"
        assert report.overall_status == "ok"
        assert ["openclaw", "mcp", "doctor", "agent-memory-hub"] in calls
        assert any(check.name == "OpenClaw MCP registry" for check in report.checks)
        assert any(check.name == "OpenClaw awareness channel" for check in report.checks)


class TestHermesAgentAdapterRealInstall:
    """Exercises Hermes Agent MCP server registration in ~/.hermes/config.yaml."""

    def test_install_registers_mcp_server(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import hermes_agent as hm_mod

        config_path = tmp_path / ".hermes" / "config.yaml"
        awareness_path = tmp_path / ".hermes" / "agent-memory-hub-awareness.md"
        monkeypatch.setattr(hm_mod, "MCP_CONFIG_PATH", config_path)
        monkeypatch.setattr(hm_mod, "AWARENESS_PATH", awareness_path)

        adapter = hm_mod.HermesAgentAdapter(brain_dir=tmp_path / ".brain")
        msg = adapter.install()

        assert "registered" in msg
        config = yaml.safe_load(config_path.read_text())
        server = config["mcp_servers"]["agent_memory_hub"]
        assert server["command"] == AMH_PYTHON
        assert server["args"] == ["-m", "agent_brain.interfaces.mcp.server"]
        assert server["env"]["BRAIN_DIR"] == str(tmp_path / ".brain")
        assert "provider tools" in awareness_path.read_text(encoding="utf-8")

    def test_install_preserves_existing_config_and_servers(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import hermes_agent as hm_mod

        config_path = tmp_path / ".hermes" / "config.yaml"
        awareness_path = tmp_path / ".hermes" / "agent-memory-hub-awareness.md"
        monkeypatch.setattr(hm_mod, "MCP_CONFIG_PATH", config_path)
        monkeypatch.setattr(hm_mod, "AWARENESS_PATH", awareness_path)
        config_path.parent.mkdir(parents=True)
        config_path.write_text(yaml.safe_dump({
            "memory": {"provider": "mem0"},
            "mcp_servers": {"project_fs": {"command": "npx", "args": ["-y", "server"]}},
        }))

        adapter = hm_mod.HermesAgentAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        config = yaml.safe_load(config_path.read_text())
        assert config["memory"]["provider"] == "mem0"
        assert "project_fs" in config["mcp_servers"]
        assert "agent_memory_hub" in config["mcp_servers"]

    def test_uninstall_removes_only_hub_server(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import hermes_agent as hm_mod

        config_path = tmp_path / ".hermes" / "config.yaml"
        awareness_path = tmp_path / ".hermes" / "agent-memory-hub-awareness.md"
        monkeypatch.setattr(hm_mod, "MCP_CONFIG_PATH", config_path)
        monkeypatch.setattr(hm_mod, "AWARENESS_PATH", awareness_path)
        config_path.parent.mkdir(parents=True)
        config_path.write_text(yaml.safe_dump({
            "mcp_servers": {"project_fs": {"command": "npx", "args": []}},
        }))
        awareness_path.parent.mkdir(parents=True, exist_ok=True)
        awareness_path.write_text("# User Hermes notes\n", encoding="utf-8")

        adapter = hm_mod.HermesAgentAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        adapter.uninstall()

        config = yaml.safe_load(config_path.read_text())
        assert "project_fs" in config["mcp_servers"]
        assert "agent_memory_hub" not in config["mcp_servers"]
        assert "User Hermes notes" in awareness_path.read_text(encoding="utf-8")
        assert "agent-memory-hub-awareness" not in awareness_path.read_text(encoding="utf-8")

    def test_diagnose_reports_ok_after_install(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import hermes_agent as hm_mod

        config_path = tmp_path / ".hermes" / "config.yaml"
        awareness_path = tmp_path / ".hermes" / "agent-memory-hub-awareness.md"
        monkeypatch.setattr(hm_mod, "MCP_CONFIG_PATH", config_path)
        monkeypatch.setattr(hm_mod, "AWARENESS_PATH", awareness_path)

        adapter = hm_mod.HermesAgentAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        report = adapter.diagnose()

        assert report.adapter == "hermes_agent"
        assert report.overall_status == "ok"
        assert any(check.name == "Hermes Agent MCP server" for check in report.checks)
        assert any(check.name == "Hermes Agent awareness channel" for check in report.checks)


class TestOpenHumanAdapterRealInstall:
    """Exercises OpenHuman's agentmemory backend config bridge."""

    def test_install_writes_agentmemory_backend_block(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import openhuman as oh_mod

        config_path = tmp_path / ".openhuman" / "config.toml"
        monkeypatch.setattr(oh_mod, "CONFIG_PATH", config_path)

        adapter = oh_mod.OpenHumanAdapter(brain_dir=tmp_path / ".brain")
        msg = adapter.install()

        assert "openhuman adapter" in msg
        parsed = tomllib.loads(config_path.read_text())
        assert parsed["memory"]["backend"] == "agentmemory"
        backend = parsed["memory"]["agentmemory"]
        assert backend["command"] == AMH_PYTHON
        assert backend["args"] == ["-m", "agent_brain.interfaces.mcp.server"]
        assert backend["env"]["BRAIN_DIR"] == str(tmp_path / ".brain")

    def test_install_preserves_existing_toml(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import openhuman as oh_mod

        config_path = tmp_path / ".openhuman" / "config.toml"
        monkeypatch.setattr(oh_mod, "CONFIG_PATH", config_path)
        config_path.parent.mkdir(parents=True)
        config_path.write_text("[ui]\ntheme = \"dark\"\n", encoding="utf-8")

        oh_mod.OpenHumanAdapter(brain_dir=tmp_path / ".brain").install()

        parsed = tomllib.loads(config_path.read_text())
        assert parsed["ui"]["theme"] == "dark"
        assert parsed["memory"]["backend"] == "agentmemory"

    def test_install_refuses_conflicting_memory_backend(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import openhuman as oh_mod

        config_path = tmp_path / ".openhuman" / "config.toml"
        monkeypatch.setattr(oh_mod, "CONFIG_PATH", config_path)
        config_path.parent.mkdir(parents=True)
        config_path.write_text("[memory]\nbackend = \"other\"\n", encoding="utf-8")

        adapter = oh_mod.OpenHumanAdapter(brain_dir=tmp_path / ".brain")
        with pytest.raises(RuntimeError, match="memory.backend"):
            adapter.install()

    def test_uninstall_removes_managed_block_only(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import openhuman as oh_mod

        config_path = tmp_path / ".openhuman" / "config.toml"
        monkeypatch.setattr(oh_mod, "CONFIG_PATH", config_path)
        config_path.parent.mkdir(parents=True)
        config_path.write_text("[ui]\ntheme = \"dark\"\n", encoding="utf-8")

        adapter = oh_mod.OpenHumanAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        adapter.uninstall()

        content = config_path.read_text()
        assert "[ui]" in content
        assert "agentmemory" not in content

    def test_diagnose_reports_ok_after_install(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import openhuman as oh_mod

        config_path = tmp_path / ".openhuman" / "config.toml"
        monkeypatch.setattr(oh_mod, "CONFIG_PATH", config_path)

        adapter = oh_mod.OpenHumanAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        report = adapter.diagnose()

        assert report.adapter == "openhuman"
        assert report.overall_status == "ok"
        assert any(check.name == "OpenHuman agentmemory backend" for check in report.checks)


class TestOpenSquillaAdapterRealInstall:
    """Exercises OpenSquilla's config.toml MCP client registration."""

    def test_install_registers_mcp_server(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import opensquilla as osq_mod

        config_path = tmp_path / ".opensquilla" / "config.toml"
        awareness_path = tmp_path / ".opensquilla" / "agent-memory-hub-awareness.md"
        monkeypatch.setattr(osq_mod, "CONFIG_PATH", config_path)
        monkeypatch.setattr(osq_mod, "AWARENESS_PATH", awareness_path)

        adapter = osq_mod.OpenSquillaAdapter(brain_dir=tmp_path / ".brain")
        msg = adapter.install()

        assert "registered" in msg
        parsed = tomllib.loads(config_path.read_text())
        server = parsed["mcp"]["servers"]["agent-memory-hub"]
        assert parsed["mcp"]["enabled"] is True
        assert server["command"] == AMH_PYTHON
        assert server["args"] == ["-m", "agent_brain.interfaces.mcp.server"]
        assert server["env"]["BRAIN_DIR"] == str(tmp_path / ".brain")
        assert "Awareness Channel" in awareness_path.read_text(encoding="utf-8")

    def test_install_preserves_existing_toml_and_servers(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import opensquilla as osq_mod

        config_path = tmp_path / ".opensquilla" / "config.toml"
        awareness_path = tmp_path / ".opensquilla" / "agent-memory-hub-awareness.md"
        monkeypatch.setattr(osq_mod, "CONFIG_PATH", config_path)
        monkeypatch.setattr(osq_mod, "AWARENESS_PATH", awareness_path)
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            "[llm]\nprovider = \"openrouter\"\n\n"
            "[mcp]\nenabled = true\n\n"
            "[mcp.servers.other]\ncommand = \"other\"\nargs = []\n",
            encoding="utf-8",
        )

        adapter = osq_mod.OpenSquillaAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        parsed = tomllib.loads(config_path.read_text())
        assert parsed["llm"]["provider"] == "openrouter"
        assert "other" in parsed["mcp"]["servers"]
        assert "agent-memory-hub" in parsed["mcp"]["servers"]

    def test_uninstall_removes_only_managed_block(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import opensquilla as osq_mod

        config_path = tmp_path / ".opensquilla" / "config.toml"
        awareness_path = tmp_path / ".opensquilla" / "agent-memory-hub-awareness.md"
        monkeypatch.setattr(osq_mod, "CONFIG_PATH", config_path)
        monkeypatch.setattr(osq_mod, "AWARENESS_PATH", awareness_path)
        config_path.parent.mkdir(parents=True)
        config_path.write_text("[llm]\nprovider = \"openrouter\"\n", encoding="utf-8")
        awareness_path.parent.mkdir(parents=True, exist_ok=True)
        awareness_path.write_text("# User OpenSquilla notes\n", encoding="utf-8")

        adapter = osq_mod.OpenSquillaAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        adapter.uninstall()

        content = config_path.read_text()
        assert "[llm]" in content
        assert "agent-memory-hub" not in content
        assert "User OpenSquilla notes" in awareness_path.read_text(encoding="utf-8")
        assert "agent-memory-hub-awareness" not in awareness_path.read_text(encoding="utf-8")

    def test_diagnose_reports_ok_after_install(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import opensquilla as osq_mod

        config_path = tmp_path / ".opensquilla" / "config.toml"
        awareness_path = tmp_path / ".opensquilla" / "agent-memory-hub-awareness.md"
        monkeypatch.setattr(osq_mod, "CONFIG_PATH", config_path)
        monkeypatch.setattr(osq_mod, "AWARENESS_PATH", awareness_path)

        adapter = osq_mod.OpenSquillaAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        report = adapter.diagnose()

        assert report.adapter == "opensquilla"
        assert report.overall_status == "ok"
        assert any(check.name == "OpenSquilla MCP server" for check in report.checks)
        assert any(check.name == "OpenSquilla awareness channel" for check in report.checks)


class TestClineAdapterRealInstall:
    """Exercises cline adapter's MCP server registration against a tmp HOME."""

    def test_install_registers_mcp_server(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import cline as cl_mod
        mcp_path = tmp_path / ".cline" / "mcp_servers.json"
        awareness_path = tmp_path / ".cline" / "agent-memory-hub-awareness.md"
        monkeypatch.setattr(cl_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(cl_mod, "AWARENESS_PATH", awareness_path)

        adapter = cl_mod.ClineAdapter(brain_dir=tmp_path / ".brain")
        msg = adapter.install()
        assert "registered" in msg

        config = json.loads(mcp_path.read_text())
        assert "agent-memory-hub" in config["mcpServers"]
        server = config["mcpServers"]["agent-memory-hub"]
        assert server["args"] == ["-m", "agent_brain.interfaces.mcp.server"]
        assert "Awareness Channel" in awareness_path.read_text(encoding="utf-8")

    def test_install_is_idempotent(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import cline as cl_mod
        mcp_path = tmp_path / ".cline" / "mcp_servers.json"
        awareness_path = tmp_path / ".cline" / "agent-memory-hub-awareness.md"
        monkeypatch.setattr(cl_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(cl_mod, "AWARENESS_PATH", awareness_path)

        adapter = cl_mod.ClineAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        msg = adapter.install()
        assert "already registered" in msg

    def test_install_updates_existing_cline_mcp_server(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import cline as cl_mod

        mcp_path = tmp_path / ".cline" / "mcp_servers.json"
        awareness_path = tmp_path / ".cline" / "agent-memory-hub-awareness.md"
        monkeypatch.setattr(cl_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(cl_mod, "AWARENESS_PATH", awareness_path)
        mcp_path.parent.mkdir(parents=True)
        mcp_path.write_text(json.dumps({
            "mcpServers": {
                "agent-memory-hub": {"command": "/old/python", "args": [], "env": {}},
                "other-tool": {"command": "other", "args": []},
            }
        }))

        adapter = cl_mod.ClineAdapter(brain_dir=tmp_path / ".brain")
        msg = adapter.install()

        assert "updated MCP server" in msg
        config = json.loads(mcp_path.read_text())
        server = config["mcpServers"]["agent-memory-hub"]
        assert server["command"] == AMH_PYTHON
        assert server["args"] == ["-m", "agent_brain.interfaces.mcp.server"]
        assert server["env"] == {"BRAIN_DIR": str(tmp_path / ".brain")}
        assert "other-tool" in config["mcpServers"]

    def test_uninstall_removes_only_hub_server(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import cline as cl_mod
        mcp_path = tmp_path / ".cline" / "mcp_servers.json"
        awareness_path = tmp_path / ".cline" / "agent-memory-hub-awareness.md"
        monkeypatch.setattr(cl_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(cl_mod, "AWARENESS_PATH", awareness_path)

        mcp_path.parent.mkdir(parents=True)
        mcp_path.write_text(json.dumps({
            "mcpServers": {"other-tool": {"command": "other", "args": []}}
        }))
        awareness_path.parent.mkdir(parents=True, exist_ok=True)
        awareness_path.write_text("# User Cline notes\n", encoding="utf-8")

        adapter = cl_mod.ClineAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        adapter.uninstall()

        config = json.loads(mcp_path.read_text())
        assert "other-tool" in config["mcpServers"]
        assert "agent-memory-hub" not in config["mcpServers"]
        assert "User Cline notes" in awareness_path.read_text(encoding="utf-8")
        assert "agent-memory-hub-awareness" not in awareness_path.read_text(encoding="utf-8")

    def test_diagnose_reports_ok_after_install(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import cline as cl_mod
        mcp_path = tmp_path / ".cline" / "mcp_servers.json"
        awareness_path = tmp_path / ".cline" / "agent-memory-hub-awareness.md"
        monkeypatch.setattr(cl_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(cl_mod, "AWARENESS_PATH", awareness_path)

        adapter = cl_mod.ClineAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        report = adapter.diagnose()

        assert report.adapter == "cline"
        assert report.overall_status == "ok"
        assert all(check.status == "ok" for check in report.checks)
        assert any(check.name == "Cline MCP server" for check in report.checks)
        assert any(check.name == "Cline awareness channel" for check in report.checks)

    def test_diagnose_reports_missing_config_without_writing(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import cline as cl_mod
        mcp_path = tmp_path / ".cline" / "mcp_servers.json"
        monkeypatch.setattr(cl_mod, "MCP_CONFIG_PATH", mcp_path)

        adapter = cl_mod.ClineAdapter(brain_dir=tmp_path / ".brain")

        report = adapter.diagnose()

        assert report.overall_status == "error"
        assert not mcp_path.exists()
        assert any(check.name == "Cline MCP server" for check in report.checks)


class TestContinueAdapterRealInstall:
    """Exercises Continue's documented global config.yaml MCP registration."""

    def test_install_registers_mcp_server(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import continue_dev as cont_mod
        mcp_path = tmp_path / ".continue" / "config.yaml"
        awareness_path = tmp_path / ".continue" / "rules" / "agent-memory-hub.md"
        monkeypatch.setattr(cont_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(cont_mod, "AWARENESS_PATH", awareness_path)

        adapter = cont_mod.ContinueAdapter(brain_dir=tmp_path / ".brain")
        msg = adapter.install()

        assert "registered" in msg
        config = yaml.safe_load(mcp_path.read_text())
        server = _yaml_mcp_server(config, "agent-memory-hub")
        assert server["args"] == ["-m", "agent_brain.interfaces.mcp.server"]
        assert server["env"]["BRAIN_DIR"] == str(tmp_path / ".brain")
        assert "Awareness Channel" in awareness_path.read_text(encoding="utf-8")

    def test_install_is_idempotent(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import continue_dev as cont_mod
        mcp_path = tmp_path / ".continue" / "config.yaml"
        awareness_path = tmp_path / ".continue" / "rules" / "agent-memory-hub.md"
        monkeypatch.setattr(cont_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(cont_mod, "AWARENESS_PATH", awareness_path)

        adapter = cont_mod.ContinueAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        first = mcp_path.read_text()
        msg = adapter.install()

        assert "already registered" in msg
        assert mcp_path.read_text() == first

    def test_install_preserves_existing_config_and_servers(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import continue_dev as cont_mod
        mcp_path = tmp_path / ".continue" / "config.yaml"
        awareness_path = tmp_path / ".continue" / "rules" / "agent-memory-hub.md"
        monkeypatch.setattr(cont_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(cont_mod, "AWARENESS_PATH", awareness_path)

        mcp_path.parent.mkdir(parents=True)
        mcp_path.write_text(yaml.safe_dump({
            "name": "Local Continue",
            "mcpServers": [{"name": "other-tool", "command": "other", "args": []}],
        }))

        adapter = cont_mod.ContinueAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        config = yaml.safe_load(mcp_path.read_text())
        assert config["name"] == "Local Continue"
        names = {server["name"] for server in config["mcpServers"]}
        assert "other-tool" in names
        assert "agent-memory-hub" in names

    def test_uninstall_removes_only_hub_server(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import continue_dev as cont_mod
        mcp_path = tmp_path / ".continue" / "config.yaml"
        awareness_path = tmp_path / ".continue" / "rules" / "agent-memory-hub.md"
        monkeypatch.setattr(cont_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(cont_mod, "AWARENESS_PATH", awareness_path)

        mcp_path.parent.mkdir(parents=True)
        mcp_path.write_text(yaml.safe_dump({
            "mcpServers": [{"name": "other-tool", "command": "other", "args": []}]
        }))
        awareness_path.parent.mkdir(parents=True, exist_ok=True)
        awareness_path.write_text("# User Continue notes\n", encoding="utf-8")

        adapter = cont_mod.ContinueAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        adapter.uninstall()

        config = yaml.safe_load(mcp_path.read_text())
        names = {server["name"] for server in config["mcpServers"]}
        assert "other-tool" in names
        assert "agent-memory-hub" not in names
        assert "User Continue notes" in awareness_path.read_text(encoding="utf-8")
        assert "agent-memory-hub-awareness" not in awareness_path.read_text(encoding="utf-8")

    def test_install_refuses_malformed_yaml(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import continue_dev as cont_mod
        mcp_path = tmp_path / ".continue" / "config.yaml"
        awareness_path = tmp_path / ".continue" / "rules" / "agent-memory-hub.md"
        monkeypatch.setattr(cont_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(cont_mod, "AWARENESS_PATH", awareness_path)

        mcp_path.parent.mkdir(parents=True)
        mcp_path.write_text("mcpServers:\n  - [")

        adapter = cont_mod.ContinueAdapter(brain_dir=tmp_path / ".brain")

        with pytest.raises(RuntimeError, match="malformed"):
            adapter.install()

    def test_diagnose_reports_ok_after_install(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import continue_dev as cont_mod
        mcp_path = tmp_path / ".continue" / "config.yaml"
        awareness_path = tmp_path / ".continue" / "rules" / "agent-memory-hub.md"
        monkeypatch.setattr(cont_mod, "MCP_CONFIG_PATH", mcp_path)
        monkeypatch.setattr(cont_mod, "AWARENESS_PATH", awareness_path)

        adapter = cont_mod.ContinueAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        report = adapter.diagnose()

        assert report.adapter == "continue_dev"
        assert report.overall_status == "ok"
        assert all(check.status == "ok" for check in report.checks)
        assert any(check.name == "Continue MCP server" for check in report.checks)
        assert any(check.name == "Continue awareness channel" for check in report.checks)

    def test_diagnose_reports_missing_config_without_writing(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import continue_dev as cont_mod
        mcp_path = tmp_path / ".continue" / "config.yaml"
        monkeypatch.setattr(cont_mod, "MCP_CONFIG_PATH", mcp_path)

        adapter = cont_mod.ContinueAdapter(brain_dir=tmp_path / ".brain")

        report = adapter.diagnose()

        assert report.overall_status == "error"
        assert not mcp_path.exists()
        assert any(check.name == "Continue MCP server" for check in report.checks)


class TestAiderAdapterRealInstall:
    """Exercises aider adapter's read-file injection against a tmp HOME."""

    def test_aider_config_helpers_are_split_and_reexported(self):
        from agent_brain.agent_integrations import aider as ai_mod
        from agent_brain.agent_integrations.aider_config import _atomic_write_yaml, _read_yaml

        assert ai_mod._read_yaml is _read_yaml
        assert ai_mod._atomic_write_yaml is _atomic_write_yaml

    def test_aider_diagnostics_are_split_and_reexported(self, tmp_path):
        from agent_brain.agent_integrations import aider as ai_mod
        from agent_brain.agent_integrations.aider_diagnostics import diagnose_read_directive

        conf_path = tmp_path / ".aider.conf.yml"
        digest_path = tmp_path / ".brain" / "aider_brain_digest.md"

        check = diagnose_read_directive(conf_path, digest_path)

        assert check.name == "Aider read directive"
        assert check.status == "error"
        assert "missing:" in check.detail
        assert ai_mod.diagnose_read_directive is diagnose_read_directive

    def test_install_creates_read_directive(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import aider as ai_mod
        conf_path = tmp_path / ".aider.conf.yml"
        monkeypatch.setattr(ai_mod, "AIDER_CONF", conf_path)

        adapter = ai_mod.AiderAdapter(brain_dir=tmp_path / ".brain")
        msg = adapter.install()
        assert "installed" in msg

        import yaml
        config = yaml.safe_load(conf_path.read_text())
        assert isinstance(config["read"], list)
        assert any("aider_brain_digest.md" in r for r in config["read"])
        assert (tmp_path / ".brain" / "aider_brain_digest.md").exists()

    def test_install_is_idempotent(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import aider as ai_mod
        conf_path = tmp_path / ".aider.conf.yml"
        monkeypatch.setattr(ai_mod, "AIDER_CONF", conf_path)

        adapter = ai_mod.AiderAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        msg = adapter.install()
        assert "already installed" in msg

    def test_install_preserves_existing_config(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import aider as ai_mod
        conf_path = tmp_path / ".aider.conf.yml"
        monkeypatch.setattr(ai_mod, "AIDER_CONF", conf_path)

        import yaml
        conf_path.parent.mkdir(parents=True, exist_ok=True)
        conf_path.write_text(yaml.dump({"model": "gpt-4", "read": ["/some/other/file"]}))

        adapter = ai_mod.AiderAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        config = yaml.safe_load(conf_path.read_text())
        assert config["model"] == "gpt-4"
        assert "/some/other/file" in config["read"]
        assert len(config["read"]) == 2

    def test_uninstall_removes_only_hub_entry(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import aider as ai_mod
        conf_path = tmp_path / ".aider.conf.yml"
        monkeypatch.setattr(ai_mod, "AIDER_CONF", conf_path)

        import yaml
        conf_path.parent.mkdir(parents=True, exist_ok=True)
        conf_path.write_text(yaml.dump({"read": ["/user/file.md"]}))

        adapter = ai_mod.AiderAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        adapter.uninstall()

        config = yaml.safe_load(conf_path.read_text())
        assert config["read"] == ["/user/file.md"]

    def test_diagnose_reports_ok_after_install(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import aider as ai_mod
        conf_path = tmp_path / ".aider.conf.yml"
        monkeypatch.setattr(ai_mod, "AIDER_CONF", conf_path)

        adapter = ai_mod.AiderAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        report = adapter.diagnose()

        assert report.adapter == "aider"
        assert report.overall_status == "ok"
        assert {check.name for check in report.checks} >= {
            "Aider read directive",
            "Aider brain digest",
        }
        assert all(check.status == "ok" for check in report.checks)

    def test_diagnose_reports_missing_config_without_writing(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import aider as ai_mod
        conf_path = tmp_path / ".aider.conf.yml"
        monkeypatch.setattr(ai_mod, "AIDER_CONF", conf_path)

        adapter = ai_mod.AiderAdapter(brain_dir=tmp_path / ".brain")

        report = adapter.diagnose()

        assert report.overall_status == "error"
        assert not conf_path.exists()
        assert any(check.name == "Aider read directive" for check in report.checks)


class TestGitHubCopilotAdapterRealInstall:
    """Exercises GitHub Copilot repository custom instructions installation."""

    def test_install_creates_repository_instructions(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import github_copilot as gh_mod
        instructions = tmp_path / ".github" / "copilot-instructions.md"
        monkeypatch.setattr(gh_mod, "INSTRUCTIONS_PATH", instructions)

        adapter = gh_mod.GitHubCopilotAdapter(brain_dir=tmp_path / ".brain")
        msg = adapter.install()

        assert "installed" in msg
        content = instructions.read_text()
        assert gh_mod.BEGIN in content
        assert gh_mod.END in content
        assert "Agent Memory Hub" in content
        assert "search-memory.sh" in content

    def test_install_is_idempotent(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import github_copilot as gh_mod
        instructions = tmp_path / ".github" / "copilot-instructions.md"
        monkeypatch.setattr(gh_mod, "INSTRUCTIONS_PATH", instructions)

        adapter = gh_mod.GitHubCopilotAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        first = instructions.read_text()
        msg = adapter.install()

        assert "up-to-date" in msg
        assert instructions.read_text() == first

    def test_install_preserves_user_instructions(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import github_copilot as gh_mod
        instructions = tmp_path / ".github" / "copilot-instructions.md"
        monkeypatch.setattr(gh_mod, "INSTRUCTIONS_PATH", instructions)
        instructions.parent.mkdir(parents=True)
        instructions.write_text("# Existing Copilot notes\n\nKeep this.\n")

        adapter = gh_mod.GitHubCopilotAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        content = instructions.read_text()
        assert "Existing Copilot notes" in content
        assert gh_mod.BEGIN in content

    def test_install_uses_portable_brain_dir_hint_for_default_home(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import github_copilot as gh_mod
        instructions = tmp_path / ".github" / "copilot-instructions.md"
        monkeypatch.setattr(gh_mod, "INSTRUCTIONS_PATH", instructions)
        monkeypatch.setattr(gh_mod.Path, "home", lambda: tmp_path)

        adapter = gh_mod.GitHubCopilotAdapter(brain_dir=tmp_path / ".agent-memory-hub")
        adapter.install()

        content = instructions.read_text()
        assert "Brain directory: `~/.agent-memory-hub`" in content
        assert str(tmp_path / ".agent-memory-hub") not in content

    def test_uninstall_removes_only_hub_block(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import github_copilot as gh_mod
        instructions = tmp_path / ".github" / "copilot-instructions.md"
        monkeypatch.setattr(gh_mod, "INSTRUCTIONS_PATH", instructions)
        instructions.parent.mkdir(parents=True)
        instructions.write_text("# Existing Copilot notes\n\nKeep this.\n")

        adapter = gh_mod.GitHubCopilotAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        adapter.uninstall()

        content = instructions.read_text()
        assert "Existing Copilot notes" in content
        assert gh_mod.BEGIN not in content

    def test_diagnose_reports_ok_after_install(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import github_copilot as gh_mod
        instructions = tmp_path / ".github" / "copilot-instructions.md"
        monkeypatch.setattr(gh_mod, "INSTRUCTIONS_PATH", instructions)

        adapter = gh_mod.GitHubCopilotAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        report = adapter.diagnose()

        assert report.adapter == "github_copilot"
        assert report.overall_status == "ok"
        assert {check.name for check in report.checks} == {"GitHub Copilot instructions"}

    def test_diagnose_reports_missing_instructions_without_writing(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import github_copilot as gh_mod
        instructions = tmp_path / ".github" / "copilot-instructions.md"
        monkeypatch.setattr(gh_mod, "INSTRUCTIONS_PATH", instructions)

        adapter = gh_mod.GitHubCopilotAdapter(brain_dir=tmp_path / ".brain")

        report = adapter.diagnose()

        assert report.overall_status == "error"
        assert not instructions.exists()
        assert any(check.name == "GitHub Copilot instructions" for check in report.checks)


class TestAoneCopilotIdeaPluginAdapter:
    def test_install_writes_intellij_idea_plugin_sidecar(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import aone_copilot as aone_mod

        idea_config = tmp_path / "JetBrains" / "IntelliJIdea2025.3"
        plugin_dir = idea_config / "plugins" / "aone-copilot-idea"
        plugin_dir.mkdir(parents=True)
        monkeypatch.setattr(aone_mod, "JETBRAINS_CONFIG_ROOT", tmp_path / "JetBrains")

        adapter = aone_mod.AoneCopilotAdapter(brain_dir=tmp_path / ".brain")
        msg = adapter.install()

        sidecar = idea_config / "options" / "agent-memory-hub-aone-copilot.md"
        assert "installed awareness sidecar" in msg
        assert sidecar.exists()
        content = sidecar.read_text(encoding="utf-8")
        assert "IntelliJ IDEA Aone Copilot" in content
        assert "search_memory" in content
        assert str(tmp_path / ".brain") in content
        assert "static awareness channel" in content
        assert "not a verified MCP bridge" in content

        report = adapter.diagnose()
        assert report.overall_status == "ok"
        assert {check.name for check in report.checks} == {
            "Aone Copilot IntelliJ plugin",
            "Aone Copilot awareness sidecar",
        }

    def test_uninstall_removes_sidecar_only(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import aone_copilot as aone_mod

        idea_config = tmp_path / "JetBrains" / "IntelliJIdea2025.3"
        plugin_dir = idea_config / "plugins" / "Aone-Idea"
        plugin_dir.mkdir(parents=True)
        user_note = idea_config / "options" / "user-note.md"
        user_note.parent.mkdir(parents=True, exist_ok=True)
        user_note.write_text("keep", encoding="utf-8")
        monkeypatch.setattr(aone_mod, "JETBRAINS_CONFIG_ROOT", tmp_path / "JetBrains")

        adapter = aone_mod.AoneCopilotAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        msg = adapter.uninstall()

        assert "removed awareness sidecar" in msg
        assert not (idea_config / "options" / "agent-memory-hub-aone-copilot.md").exists()
        assert user_note.read_text(encoding="utf-8") == "keep"


class TestWukongAdapterRealInstall:
    """Exercises wukong adapter's brain_context.md injection against tmp HOME."""

    @pytest.fixture(autouse=True)
    def _isolate_real_user_scope(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import wukong as wk_mod

        monkeypatch.setattr(
            wk_mod,
            "REAL_USERS_DIR",
            tmp_path / ".real" / "users",
            raising=False,
        )
        monkeypatch.setattr(
            wk_mod,
            "WUKONG_SERVER_USERS_DIR",
            tmp_path / "dingtalk-rewind-server" / "users",
            raising=False,
        )
        monkeypatch.setattr(
            wk_mod,
            "WUKONG_SERVER_LOGS_DIR",
            tmp_path / "dingtalk-rewind-server" / "logs",
            raising=False,
        )

    def test_install_creates_context_block(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import wukong as wk_mod
        ctx_path = tmp_path / ".wukong" / "brain_context.md"
        mcp_path = tmp_path / ".real" / ".mcp" / "mcpServerConfig.json"
        monkeypatch.setattr(wk_mod, "CONTEXT_FILE", ctx_path)
        monkeypatch.setattr(wk_mod, "MCP_CONFIG_PATH", mcp_path)

        adapter = wk_mod.WukongAdapter(brain_dir=tmp_path / ".brain")
        msg = adapter.install()
        assert "installed" in msg

        content = ctx_path.read_text()
        assert wk_mod.BEGIN in content
        assert wk_mod.END in content
        assert "brain pool" in content.lower()

    def test_install_registers_realbox_mcp_server(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import wukong as wk_mod
        ctx_path = tmp_path / ".wukong" / "brain_context.md"
        mcp_path = tmp_path / ".real" / ".mcp" / "mcpServerConfig.json"
        monkeypatch.setattr(wk_mod, "CONTEXT_FILE", ctx_path)
        monkeypatch.setattr(wk_mod, "MCP_CONFIG_PATH", mcp_path)

        adapter = wk_mod.WukongAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        config = json.loads(mcp_path.read_text())
        server = config["mcpServers"]["agent-memory-hub"]
        assert server["name"] == "Agent Memory Hub"
        assert server["type"] == "stdio"
        assert server["isActive"] is True
        assert server["isRemovable"] is True
        assert server["source"] == "user"
        assert server["command"] == AMH_PYTHON
        assert server["args"] == ["-m", "agent_brain.interfaces.mcp.server"]
        assert server["env"]["BRAIN_DIR"] == str(tmp_path / ".brain")

    def test_install_uses_wukong_cli_for_current_scope_when_available(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import wukong as wk_mod

        ctx_path = tmp_path / ".wukong" / "brain_context.md"
        users_dir = tmp_path / ".real" / "users"
        user_root = users_dir / "user-1"
        (user_root / "workspace").mkdir(parents=True)
        (user_root / ".mcp").mkdir(parents=True)
        skills_dir = user_root / ".skills"
        skills_dir.mkdir(parents=True)
        skills_db = skills_dir / "skills.db"
        import sqlite3

        with sqlite3.connect(skills_db) as conn:
            conn.executescript(
                """
                CREATE TABLE skills (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    source_type TEXT NOT NULL,
                    source_ref TEXT,
                    remote_skill_id TEXT,
                    remote_version INTEGER,
                    remote_source TEXT,
                    sync_status TEXT,
                    directory_identity TEXT,
                    central_path TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    last_sync_at INTEGER,
                    status TEXT NOT NULL DEFAULT 'ok',
                    extension TEXT,
                    search_keywords TEXT
                );
                CREATE VIRTUAL TABLE skills_fts USING fts5(skill_id UNINDEXED, name, description, keywords);
                """
            )
        (user_root / ".mcp" / "mcpServerConfig.json").write_text(
            json.dumps({"mcpServers": {"builtin-agent-model": {"name": "builtin"}}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(wk_mod, "CONTEXT_FILE", ctx_path)
        monkeypatch.setattr(wk_mod, "REAL_USERS_DIR", users_dir, raising=False)
        monkeypatch.setattr(wk_mod, "_find_wukong_cli", lambda: Path("/fake/wukong-cli"))

        calls = []

        def fake_cli_json(namespace, action, payload):
            calls.append((namespace, action, payload))
            if (namespace, action) == ("mcp", "list"):
                return {"servers": []}
            if (namespace, action) == ("mcp", "add"):
                return {"serverId": "scoped-id"}
            if (namespace, action) == ("mcp", "start"):
                return {"ok": True}
            raise AssertionError((namespace, action, payload))

        monkeypatch.setattr(wk_mod, "_cli_json", fake_cli_json)

        adapter = wk_mod.WukongAdapter(brain_dir=tmp_path / ".brain")
        msg = adapter.install()

        assert "scoped Wukong MCP server scoped-id" in msg
        assert ("mcp", "add") in [(ns, action) for ns, action, _ in calls]
        assert ("mcp", "start") in [(ns, action) for ns, action, _ in calls]
        user_config = json.loads((user_root / ".mcp" / "mcpServerConfig.json").read_text())
        assert "agent-memory-hub" in user_config["mcpServers"]
        assert "Agent Memory Hub Awareness Channel" in (
            user_root / "workspace" / "AGENTS.md"
        ).read_text(encoding="utf-8")
        assert "Agent Memory Hub Awareness Channel" in (
            user_root / "workspace" / "MEMORY.md"
        ).read_text(encoding="utf-8")
        skill = skills_dir / "agent-memory-hub-shared-memory" / "SKILL.md"
        assert skill.exists()
        skill_text = skill.read_text(encoding="utf-8")
        assert "agent-memory-hub" in skill_text
        assert "短 prompt" in skill_text
        assert "brief_memory" in skill_text
        with sqlite3.connect(skills_db) as conn:
            row = conn.execute(
                "select name, description, source_type, central_path, enabled from skills where id=?",
                (wk_mod.WUKONG_BOOTSTRAP_SKILL_ID,),
            ).fetchone()
            assert row is not None
            assert row[0] == "agent-memory-hub-shared-memory"
            assert row[2] == "local"
            assert row[3] == str(skill.parent)
            assert row[4] == 1
            fts_row = conn.execute(
                "select name, description, keywords from skills_fts where skill_id=?",
                (wk_mod.WUKONG_BOOTSTRAP_SKILL_ID,),
            ).fetchone()
            assert fts_row is not None
            assert "agent memory hub" in fts_row[1].lower()
            assert "brief_memory" in fts_row[2]

    def test_install_syncs_awareness_into_wukong_project_workspaces(self, tmp_path):
        from agent_brain.agent_integrations import wukong as wk_mod

        users_dir = tmp_path / ".real" / "users"
        user_root = users_dir / "user-1"
        project_root = user_root / "workspace" / "projects" / "default"
        project_root.mkdir(parents=True)
        (project_root / "AGENTS.md").write_text("# Existing Wukong project notes\n", encoding="utf-8")

        adapter = wk_mod.WukongAdapter(brain_dir=tmp_path / ".brain")
        changed, msg = adapter._install_workspace_awareness()

        assert changed is True
        assert "workspace awareness installed" in msg
        assert "Existing Wukong project notes" in (project_root / "AGENTS.md").read_text(encoding="utf-8")
        assert "Agent Memory Hub Awareness Channel" in (project_root / "AGENTS.md").read_text(encoding="utf-8")
        assert "Agent Memory Hub Awareness Channel" in (project_root / "MEMORY.md").read_text(encoding="utf-8")

    def test_install_promotes_wukong_workspace_awareness_to_file_front(self, tmp_path):
        from agent_brain.agent_integrations import wukong as wk_mod

        users_dir = tmp_path / ".real" / "users"
        user_root = users_dir / "user-1"
        project_root = user_root / "workspace" / "projects" / "default"
        project_root.mkdir(parents=True)
        long_existing = "# Existing Wukong project notes\n\n" + ("legacy note\n" * 300)
        (project_root / "AGENTS.md").write_text(long_existing, encoding="utf-8")

        adapter = wk_mod.WukongAdapter(brain_dir=tmp_path / ".brain")
        changed, msg = adapter._install_workspace_awareness()

        assert changed is True
        assert "workspace awareness installed" in msg
        content = (project_root / "AGENTS.md").read_text(encoding="utf-8")
        assert content.startswith("<!-- BEGIN agent-memory-hub-awareness -->")
        assert "Existing Wukong project notes" in content
        assert content.index("Agent Memory Hub Awareness Channel") < content.index("Existing Wukong project notes")

    def test_diagnose_rejects_wukong_workspace_awareness_after_existing_notes(self, tmp_path):
        from agent_brain.agent_integrations import wukong as wk_mod
        from agent_brain.agent_integrations.awareness import install_awareness_block

        users_dir = tmp_path / ".real" / "users"
        user_root = users_dir / "user-1"
        project_root = user_root / "workspace" / "projects" / "default"
        project_root.mkdir(parents=True)
        path = project_root / "AGENTS.md"
        path.write_text("# Existing Wukong project notes\n\n", encoding="utf-8")

        adapter = wk_mod.WukongAdapter(brain_dir=tmp_path / ".brain")
        install_awareness_block(path, adapter._awareness_block())
        install_awareness_block(project_root / "MEMORY.md", adapter._awareness_block(), placement="prepend")

        check = adapter._diagnose_workspace_awareness()

        assert check.status == "error"
        assert "not first" in check.detail

    def test_install_syncs_wukong_native_memory_index_bridge(self, tmp_path):
        from agent_brain.agent_integrations import wukong as wk_mod

        db_path = (
            tmp_path
            / "dingtalk-rewind-server"
            / "users"
            / "user-1"
            / "storage"
            / "memory"
            / "memory.sqlite"
        )
        db_path.parent.mkdir(parents=True)
        with sqlite3.connect(db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE memory_files (
                    path TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    hash TEXT NOT NULL,
                    mtime INTEGER NOT NULL,
                    size INTEGER NOT NULL
                );
                CREATE TABLE memory_chunks (
                    id TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    source TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    hash TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL,
                    embedding TEXT NOT NULL DEFAULT '[]',
                    updated_at INTEGER NOT NULL
                );
                CREATE VIRTUAL TABLE chunks_fts USING fts5(id, text);
                CREATE TABLE memory_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO memory_files VALUES ('AGENTS.md', 'memory', 'old', 1, 3);
                INSERT INTO memory_chunks VALUES (
                    'AGENTS.md:1-3', 'AGENTS.md', 'memory', 1, 3, 'old', 'text-embedding-v4',
                    '## existing wukong memory', '[]', 1
                );
                INSERT INTO chunks_fts VALUES ('AGENTS.md:1-3', 'existing wukong memory');
                """
            )

        adapter = wk_mod.WukongAdapter(brain_dir=tmp_path / ".brain")
        changed, msg = adapter._install_native_memory_bridge()

        assert changed is True
        assert "native memory bridge synced" in msg
        with sqlite3.connect(db_path) as conn:
            bridge = conn.execute(
                "select path, source, text from memory_chunks where id=?",
                (wk_mod.WUKONG_NATIVE_MEMORY_CHUNK_ID,),
            ).fetchone()
            assert bridge is not None
            assert bridge[0] == wk_mod.WUKONG_NATIVE_MEMORY_PATH
            assert bridge[1] == "agent-memory-hub"
            assert "短 prompt" in bridge[2]
            assert "brief_memory" in bridge[2]
            assert conn.execute(
                "select count(*) from memory_chunks where id='AGENTS.md:1-3'"
            ).fetchone()[0] == 1
            hits = conn.execute(
                "select c.id from chunks_fts f join memory_chunks c on c.id=f.id "
                "where chunks_fts match ? order by rank",
                ('"brief_memory"',),
            ).fetchall()
            assert hits[0][0] == wk_mod.WUKONG_NATIVE_MEMORY_CHUNK_ID

    def test_install_syncs_wukong_native_brain_redirect(self, tmp_path):
        from agent_brain.agent_integrations import wukong as wk_mod

        db_path = (
            tmp_path
            / "dingtalk-rewind-server"
            / "users"
            / "user-1"
            / "memory"
            / "brain.db"
        )
        db_path.parent.mkdir(parents=True)
        with sqlite3.connect(db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE memories (
                    id TEXT PRIMARY KEY,
                    key TEXT NOT NULL UNIQUE,
                    content TEXT NOT NULL,
                    category TEXT NOT NULL,
                    source TEXT NOT NULL,
                    role TEXT NOT NULL,
                    conversation_id TEXT,
                    external_msg_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    embedding BLOB
                );
                CREATE VIRTUAL TABLE memories_fts USING fts5(
                    key, content, category, source,
                    content=memories,
                    content_rowid=rowid
                );
                CREATE TRIGGER memories_fts_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts(rowid, key, content, category, source)
                    VALUES (new.rowid, new.key, new.content, new.category, new.source);
                END;
                CREATE TRIGGER memories_fts_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, key, content, category, source)
                    VALUES ('delete', old.rowid, old.key, old.content, old.category, old.source);
                END;
                CREATE TRIGGER memories_fts_au AFTER UPDATE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, key, content, category, source)
                    VALUES ('delete', old.rowid, old.key, old.content, old.category, old.source);
                    INSERT INTO memories_fts(rowid, key, content, category, source)
                    VALUES (new.rowid, new.key, new.content, new.category, new.source);
                END;
                """
            )

        adapter = wk_mod.WukongAdapter(brain_dir=tmp_path / ".brain")
        changed, msg = adapter._install_native_memory_bridge()

        assert changed is True
        assert "native memory bridge synced" in msg
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "select key, category, source, role, content from memories where id=?",
                (wk_mod.WUKONG_NATIVE_BRAIN_MEMORY_ID,),
            ).fetchone()
            assert row is not None
            assert row[0] == "agent-memory-hub shared memory redirect"
            assert row[1] == "system"
            assert row[2] == "agent-memory-hub"
            assert row[3] == "system"
            assert "search_memory" in row[4]
            hits = conn.execute(
                "select m.id from memories_fts f join memories m on m.rowid=f.rowid "
                "where memories_fts match ? order by rank",
                ('"brief_memory"',),
            ).fetchall()
            assert hits[0][0] == wk_mod.WUKONG_NATIVE_BRAIN_MEMORY_ID

    def test_diagnose_rejects_wukong_native_memory_index_without_amh_bridge(self, tmp_path):
        from agent_brain.agent_integrations import wukong as wk_mod

        db_path = (
            tmp_path
            / "dingtalk-rewind-server"
            / "users"
            / "user-1"
            / "storage"
            / "memory"
            / "memory.sqlite"
        )
        db_path.parent.mkdir(parents=True)
        with sqlite3.connect(db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE memory_chunks (
                    id TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    source TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    hash TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL,
                    embedding TEXT NOT NULL DEFAULT '[]',
                    updated_at INTEGER NOT NULL
                );
                CREATE VIRTUAL TABLE chunks_fts USING fts5(id, text);
                """
            )

        adapter = wk_mod.WukongAdapter(brain_dir=tmp_path / ".brain")
        check = adapter._diagnose_native_memory_bridge()

        assert check.status == "error"
        assert "missing AMH native memory bridge" in check.detail

    def test_diagnose_uses_wukong_cli_tools_for_runtime_verification(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import wukong as wk_mod

        ctx_path = tmp_path / ".wukong" / "brain_context.md"
        users_dir = tmp_path / ".real" / "users"
        user_root = users_dir / "user-1"
        (user_root / "workspace").mkdir(parents=True)
        (user_root / ".mcp").mkdir(parents=True)
        (user_root / ".mcp" / "mcpServerConfig.json").write_text(
            json.dumps({"mcpServers": {"builtin-agent-model": {"name": "builtin"}}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(wk_mod, "CONTEXT_FILE", ctx_path)
        monkeypatch.setattr(wk_mod, "REAL_USERS_DIR", users_dir, raising=False)
        monkeypatch.setattr(wk_mod, "_find_wukong_cli", lambda: Path("/fake/wukong-cli"))

        def fake_cli_json(namespace, action, payload):
            if (namespace, action) == ("mcp", "list"):
                return {
                    "servers": [
                        {
                            "id": "scoped-id",
                            "name": "Agent Memory Hub",
                            "type": "stdio",
                            "command": AMH_PYTHON,
                            "args": ["-m", "agent_brain.interfaces.mcp.server"],
                            "env": {"BRAIN_DIR": str(tmp_path / ".brain")},
                            "isActive": True,
                            "status": "connected",
                        }
                    ]
                }
            if (namespace, action) == ("mcp", "tools"):
                return {
                    "tools": [
                        {"name": "search_memory"},
                        {"name": "write_memory"},
                        {"name": "list_recent"},
                    ]
                }
            raise AssertionError((namespace, action, payload))

        monkeypatch.setattr(wk_mod, "_cli_json", fake_cli_json)

        adapter = wk_mod.WukongAdapter(brain_dir=tmp_path / ".brain")
        adapter._install_context()

        report = adapter.diagnose()

        assert report.overall_status == "error"
        scoped_config_check = next(
            check for check in report.checks if check.name == "Wukong user-scoped MCP config"
        )
        assert scoped_config_check.status == "error"
        assert "missing AMH server" in scoped_config_check.detail
        mcp_check = next(check for check in report.checks if check.name == "Wukong MCP server")
        assert mcp_check.status == "ok"
        assert "scoped Wukong MCP server scoped-id connected with AMH tools" in mcp_check.detail

    def test_diagnose_accepts_semantic_user_scoped_mcp_config_without_optional_fields(
        self,
        tmp_path,
        monkeypatch,
    ):
        from agent_brain.agent_integrations import wukong as wk_mod

        ctx_path = tmp_path / ".wukong" / "brain_context.md"
        users_dir = tmp_path / ".real" / "users"
        user_root = users_dir / "user-1"
        workspace = user_root / "workspace"
        workspace.mkdir(parents=True)
        config_path = user_root / ".mcp" / "mcpServerConfig.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            json.dumps({
                "mcpServers": {
                    "agent-memory-hub": {
                        "name": "Agent Memory Hub",
                        "type": "stdio",
                        "command": AMH_PYTHON,
                        "args": ["-m", "agent_brain.interfaces.mcp.server"],
                        "env": {"BRAIN_DIR": str(tmp_path / ".brain")},
                    }
                }
            }),
            encoding="utf-8",
        )
        monkeypatch.setattr(wk_mod, "CONTEXT_FILE", ctx_path)
        monkeypatch.setattr(wk_mod, "REAL_USERS_DIR", users_dir, raising=False)
        monkeypatch.setattr(wk_mod, "_find_wukong_cli", lambda: Path("/fake/wukong-cli"))

        def fake_cli_json(namespace, action, payload):
            if (namespace, action) == ("mcp", "list"):
                return {
                    "servers": [
                        {
                            "id": "scoped-id",
                            "name": "Agent Memory Hub",
                            "type": "stdio",
                            "command": AMH_PYTHON,
                            "args": ["-m", "agent_brain.interfaces.mcp.server"],
                            "env": {"BRAIN_DIR": str(tmp_path / ".brain")},
                            "isActive": True,
                            "status": "connected",
                        }
                    ]
                }
            if (namespace, action) == ("mcp", "tools"):
                return {
                    "tools": [
                        {"name": "search_memory"},
                        {"name": "write_memory"},
                        {"name": "list_recent"},
                    ]
                }
            raise AssertionError((namespace, action, payload))

        monkeypatch.setattr(wk_mod, "_cli_json", fake_cli_json)

        adapter = wk_mod.WukongAdapter(brain_dir=tmp_path / ".brain")
        adapter._install_context()
        adapter._install_workspace_awareness()

        report = adapter.diagnose()

        scoped_config_check = next(
            check for check in report.checks if check.name == "Wukong user-scoped MCP config"
        )
        assert scoped_config_check.status == "ok"

    def test_diagnose_warns_when_latest_wukong_prompt_has_no_amh_usage(self, tmp_path):
        from agent_brain.agent_integrations import wukong as wk_mod

        log_path = wk_mod.WUKONG_SERVER_LOGS_DIR / "app" / "application.2026-06-29.log"
        log_path.parent.mkdir(parents=True)
        session_id = "a05e95e0" + "-d4b8-455f-ba85-" + "68c7fd24e6e2"
        log_path.write_text(
            "\n".join([
                (
                    "[2026-06-29][17:53:45.885][dingtalk_real::gateway::commands][INFO] "
                    f"gateway_agent_cmd begin session_id=Some(\"{session_id}\") label=Some(\"Alpha\") "
                    "message_len=2 message_preview=Alpha"
                ),
                (
                    "[2026-06-29][17:53:45.984][dingtalk_real::agent::agent_client::connection::processes_v2][INFO] "
                    "[ALLSPARK_V2] mcp_runtime injected"
                ),
            ]),
            encoding="utf-8",
        )

        adapter = wk_mod.WukongAdapter(brain_dir=tmp_path / ".brain")
        check = adapter._diagnose_client_effectiveness()

        assert check.name == "Wukong client AMH effectiveness"
        assert check.status == "warn"
        assert "latest Wukong short-prompt session did not show AMH usage" in check.detail
        assert session_id in check.detail

    def test_diagnose_accepts_wukong_amh_tool_call_evidence(self, tmp_path):
        from agent_brain.agent_integrations import wukong as wk_mod

        request_path = (
            wk_mod.WUKONG_SERVER_USERS_DIR
            / "user-1"
            / "storage"
            / "llm_proxy"
            / "requests.jsonl"
        )
        request_path.parent.mkdir(parents=True)
        request_path.write_text(
            json.dumps(
                {
                    "created_at": "2026-06-29T17:54:00+08:00",
                    "type": "tool_call",
                    "server_id": "agent-memory-hub",
                    "tool_name": "search_memory",
                    "arguments": {"query": "Alpha"},
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        adapter = wk_mod.WukongAdapter(brain_dir=tmp_path / ".brain")
        check = adapter._diagnose_client_effectiveness()

        assert check.name == "Wukong client AMH effectiveness"
        assert check.status == "ok"
        assert "recent Wukong evidence shows AMH usage" in check.detail

    def test_install_dedupes_matching_wukong_mcp_servers(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import wukong as wk_mod

        ctx_path = tmp_path / ".wukong" / "brain_context.md"
        monkeypatch.setattr(wk_mod, "CONTEXT_FILE", ctx_path)
        monkeypatch.setattr(wk_mod, "_find_wukong_cli", lambda: Path("/fake/wukong-cli"))

        calls = []

        def fake_cli_json(namespace, action, payload):
            calls.append((namespace, action, payload))
            if (namespace, action) == ("mcp", "list"):
                return {
                    "servers": [
                        {
                            "id": "agent-memory-hub",
                            "name": "Agent Memory Hub",
                            "type": "stdio",
                            "command": AMH_PYTHON,
                            "args": ["-m", "agent_brain.interfaces.mcp.server"],
                            "env": {"BRAIN_DIR": str(tmp_path / ".brain")},
                            "isActive": True,
                            "status": "connected",
                        },
                        {
                            "id": "old-random-id",
                            "name": "Agent Memory Hub",
                            "type": "stdio",
                            "command": AMH_PYTHON,
                            "args": ["-m", "agent_brain.interfaces.mcp.server"],
                            "env": {"BRAIN_DIR": str(tmp_path / ".brain")},
                            "isActive": True,
                            "status": "connected",
                        },
                    ]
                }
            if (namespace, action) == ("mcp", "remove"):
                return {"ok": True}
            raise AssertionError((namespace, action, payload))

        monkeypatch.setattr(wk_mod, "_cli_json", fake_cli_json)

        adapter = wk_mod.WukongAdapter(brain_dir=tmp_path / ".brain")
        changed, msg = adapter._install_mcp_via_cli()

        assert changed is True
        assert "removed duplicate scoped Wukong MCP server(s): old-random-id" in msg
        assert ("mcp", "remove", {"id": "old-random-id"}) in calls

    def test_install_syncs_user_config_before_deduping_wukong_runtime(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import wukong as wk_mod

        ctx_path = tmp_path / ".wukong" / "brain_context.md"
        users_dir = tmp_path / ".real" / "users"
        user_root = users_dir / "user-1"
        (user_root / "workspace").mkdir(parents=True)
        (user_root / ".mcp").mkdir(parents=True)
        config_path = user_root / ".mcp" / "mcpServerConfig.json"
        config_path.write_text(
            json.dumps({"mcpServers": {"builtin-agent-model": {"name": "builtin"}}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(wk_mod, "CONTEXT_FILE", ctx_path)
        monkeypatch.setattr(wk_mod, "REAL_USERS_DIR", users_dir, raising=False)
        monkeypatch.setattr(wk_mod, "_find_wukong_cli", lambda: Path("/fake/wukong-cli"))

        calls = []

        def fake_cli_json(namespace, action, payload):
            calls.append((namespace, action, payload))
            if (namespace, action) == ("mcp", "list"):
                config = json.loads(config_path.read_text(encoding="utf-8"))
                servers = [
                    {
                        "id": "old-random-id",
                        "name": "Agent Memory Hub",
                        "type": "stdio",
                        "command": AMH_PYTHON,
                        "args": ["-m", "agent_brain.interfaces.mcp.server"],
                        "env": {"BRAIN_DIR": str(tmp_path / ".brain")},
                        "isActive": True,
                        "status": "connected",
                    }
                ]
                if "agent-memory-hub" in config["mcpServers"]:
                    servers.insert(0, {
                        "id": "agent-memory-hub",
                        "name": "Agent Memory Hub",
                        "type": "stdio",
                        "command": AMH_PYTHON,
                        "args": ["-m", "agent_brain.interfaces.mcp.server"],
                        "env": {"BRAIN_DIR": str(tmp_path / ".brain")},
                        "isActive": True,
                        "status": "connected",
                    })
                return {"servers": servers}
            if (namespace, action) == ("mcp", "remove"):
                return {"ok": True}
            raise AssertionError((namespace, action, payload))

        monkeypatch.setattr(wk_mod, "_cli_json", fake_cli_json)

        adapter = wk_mod.WukongAdapter(brain_dir=tmp_path / ".brain")
        msg = adapter.install()

        assert "removed duplicate scoped Wukong MCP server(s): old-random-id" in msg
        assert ("mcp", "remove", {"id": "old-random-id"}) in calls

    def test_uninstall_removes_scoped_wukong_cli_entry_when_available(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import wukong as wk_mod

        ctx_path = tmp_path / ".wukong" / "brain_context.md"
        monkeypatch.setattr(wk_mod, "CONTEXT_FILE", ctx_path)
        monkeypatch.setattr(wk_mod, "_find_wukong_cli", lambda: Path("/fake/wukong-cli"))

        calls = []

        def fake_cli_json(namespace, action, payload):
            calls.append((namespace, action, payload))
            if (namespace, action) == ("mcp", "list"):
                return {
                    "servers": [
                        {
                            "id": "scoped-id",
                            "name": "Agent Memory Hub",
                            "type": "stdio",
                            "command": AMH_PYTHON,
                            "args": ["-m", "agent_brain.interfaces.mcp.server"],
                            "env": {"BRAIN_DIR": str(tmp_path / ".brain")},
                            "isActive": True,
                            "status": "connected",
                        }
                    ]
                }
            if (namespace, action) == ("mcp", "remove"):
                return {"ok": True}
            raise AssertionError((namespace, action, payload))

        monkeypatch.setattr(wk_mod, "_cli_json", fake_cli_json)
        adapter = wk_mod.WukongAdapter(brain_dir=tmp_path / ".brain")
        adapter._install_context()

        msg = adapter.uninstall()

        assert "removed scoped Wukong MCP server(s): scoped-id" in msg
        assert ("mcp", "remove", {"id": "scoped-id"}) in calls

    def test_install_is_idempotent(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import wukong as wk_mod
        ctx_path = tmp_path / ".wukong" / "brain_context.md"
        mcp_path = tmp_path / ".real" / ".mcp" / "mcpServerConfig.json"
        monkeypatch.setattr(wk_mod, "CONTEXT_FILE", ctx_path)
        monkeypatch.setattr(wk_mod, "MCP_CONFIG_PATH", mcp_path)

        adapter = wk_mod.WukongAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        first_context = ctx_path.read_text()
        first_mcp = mcp_path.read_text()
        msg = adapter.install()
        assert "already installed" in msg or "up-to-date" in msg
        assert ctx_path.read_text() == first_context
        assert mcp_path.read_text() == first_mcp

    def test_install_preserves_user_content(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import wukong as wk_mod
        ctx_path = tmp_path / ".wukong" / "brain_context.md"
        mcp_path = tmp_path / ".real" / ".mcp" / "mcpServerConfig.json"
        monkeypatch.setattr(wk_mod, "CONTEXT_FILE", ctx_path)
        monkeypatch.setattr(wk_mod, "MCP_CONFIG_PATH", mcp_path)

        ctx_path.parent.mkdir(parents=True, exist_ok=True)
        ctx_path.write_text("# My custom wukong notes\n\nKeep this.\n")

        adapter = wk_mod.WukongAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        content = ctx_path.read_text()
        assert "My custom wukong notes" in content
        assert wk_mod.BEGIN in content

    def test_install_preserves_existing_realbox_mcp_servers(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import wukong as wk_mod
        ctx_path = tmp_path / ".wukong" / "brain_context.md"
        mcp_path = tmp_path / ".real" / ".mcp" / "mcpServerConfig.json"
        monkeypatch.setattr(wk_mod, "CONTEXT_FILE", ctx_path)
        monkeypatch.setattr(wk_mod, "MCP_CONFIG_PATH", mcp_path)

        mcp_path.parent.mkdir(parents=True)
        mcp_path.write_text(json.dumps({
            "mcpServers": {
                "other-tool": {
                    "name": "Other Tool",
                    "type": "stdio",
                    "command": "other",
                    "args": [],
                    "source": "user",
                },
            },
        }))

        adapter = wk_mod.WukongAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        config = json.loads(mcp_path.read_text())
        assert "other-tool" in config["mcpServers"]
        assert "agent-memory-hub" in config["mcpServers"]

    def test_install_repairs_stale_realbox_mcp_server_env(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import wukong as wk_mod
        ctx_path = tmp_path / ".wukong" / "brain_context.md"
        mcp_path = tmp_path / ".real" / ".mcp" / "mcpServerConfig.json"
        monkeypatch.setattr(wk_mod, "CONTEXT_FILE", ctx_path)
        monkeypatch.setattr(wk_mod, "MCP_CONFIG_PATH", mcp_path)

        mcp_path.parent.mkdir(parents=True)
        mcp_path.write_text(json.dumps({
            "mcpServers": {
                "agent-memory-hub": {
                    "name": "Agent Memory Hub",
                    "type": "stdio",
                    "command": "/tmp/old-python",
                    "args": ["-m", "agent_brain.interfaces.mcp.server"],
                    "env": {"BRAIN_DIR": "/tmp/old-brain"},
                    "source": "user",
                },
            },
        }))

        adapter = wk_mod.WukongAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        server = json.loads(mcp_path.read_text())["mcpServers"]["agent-memory-hub"]
        assert server["command"] == AMH_PYTHON
        assert server["env"]["BRAIN_DIR"] == str(tmp_path / ".brain")
        check = adapter._diagnose_mcp()
        assert check.status == "ok"
        assert "env.BRAIN_DIR" not in check.detail

    def test_uninstall_removes_only_hub_block(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import wukong as wk_mod
        ctx_path = tmp_path / ".wukong" / "brain_context.md"
        mcp_path = tmp_path / ".real" / ".mcp" / "mcpServerConfig.json"
        monkeypatch.setattr(wk_mod, "CONTEXT_FILE", ctx_path)
        monkeypatch.setattr(wk_mod, "MCP_CONFIG_PATH", mcp_path)

        ctx_path.parent.mkdir(parents=True, exist_ok=True)
        ctx_path.write_text("# My notes\n\nKeep this.\n")

        adapter = wk_mod.WukongAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        adapter.uninstall()

        content = ctx_path.read_text()
        assert "My notes" in content
        assert wk_mod.BEGIN not in content

    def test_uninstall_removes_only_hub_mcp_server(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import wukong as wk_mod
        ctx_path = tmp_path / ".wukong" / "brain_context.md"
        mcp_path = tmp_path / ".real" / ".mcp" / "mcpServerConfig.json"
        monkeypatch.setattr(wk_mod, "CONTEXT_FILE", ctx_path)
        monkeypatch.setattr(wk_mod, "MCP_CONFIG_PATH", mcp_path)

        mcp_path.parent.mkdir(parents=True)
        mcp_path.write_text(json.dumps({
            "mcpServers": {"other-tool": {"name": "Other", "command": "other", "args": []}},
        }))

        adapter = wk_mod.WukongAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()
        adapter.uninstall()

        config = json.loads(mcp_path.read_text())
        assert "other-tool" in config["mcpServers"]
        assert "agent-memory-hub" not in config["mcpServers"]

    def test_diagnose_reports_ok_after_install(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import wukong as wk_mod
        ctx_path = tmp_path / ".wukong" / "brain_context.md"
        mcp_path = tmp_path / ".real" / ".mcp" / "mcpServerConfig.json"
        monkeypatch.setattr(wk_mod, "CONTEXT_FILE", ctx_path)
        monkeypatch.setattr(wk_mod, "MCP_CONFIG_PATH", mcp_path)

        adapter = wk_mod.WukongAdapter(brain_dir=tmp_path / ".brain")
        adapter.install()

        report = adapter.diagnose()

        assert report.adapter == "wukong"
        assert report.overall_status == "ok"
        assert {check.name for check in report.checks} == {
            "Wukong brain context",
            "Wukong MCP server",
        }
        assert all(check.status == "ok" for check in report.checks)

    def test_diagnose_reports_missing_context_without_writing(self, tmp_path, monkeypatch):
        from agent_brain.agent_integrations import wukong as wk_mod
        ctx_path = tmp_path / ".wukong" / "brain_context.md"
        mcp_path = tmp_path / ".real" / ".mcp" / "mcpServerConfig.json"
        monkeypatch.setattr(wk_mod, "CONTEXT_FILE", ctx_path)
        monkeypatch.setattr(wk_mod, "MCP_CONFIG_PATH", mcp_path)

        adapter = wk_mod.WukongAdapter(brain_dir=tmp_path / ".brain")

        report = adapter.diagnose()

        assert report.overall_status == "error"
        assert not ctx_path.exists()
        assert not mcp_path.exists()
        assert any(check.name == "Wukong brain context" for check in report.checks)
        assert any(check.name == "Wukong MCP server" for check in report.checks)
