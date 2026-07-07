"""Claude Code adapter — installs memory and low-noise lifecycle hooks into
``~/.claude/settings.json`` so Claude Code reads from the brain pool on every
session start and prompt, writes session-end signals back, and records compact
or subagent lifecycle evidence.

Hook scripts live in ``<repo>/agent_runtime_kit/hooks/`` and are referenced by absolute
path. The install is idempotent: re-running detects the existing entries by
the script path prefix and is a no-op. Uninstall removes only the entries
whose command starts with the repo's agent_runtime_kit/hooks/ prefix; entries added by
other tools are left alone.
"""

from __future__ import annotations

from pathlib import Path

from . import AdapterBase, AdapterConfig
from .awareness import (
    diagnose_awareness_block,
    install_awareness_block,
    render_awareness_block,
    uninstall_awareness_block,
)
from .claude_code_diagnostics import (
    diagnose_hook_scripts,
    diagnose_settings_hooks,
)
from .diagnostics import (
    AdapterDiagnosticCheck,
    AdapterDiagnosticReport,
    diagnose_layered_context_pack_evidence,
    diagnose_mcp_json_server,
    diagnose_runtime_evidence,
    overall_status,
)
from .hook_config import (
    adapter_hook_command as _adapter_hook_command,
    atomic_write_json as _atomic_write_settings,
    hook_already_present as _hook_already_present,  # noqa: F401 - re-exported for adapter split tests
    hook_belongs_to as _hook_belongs_to,
    hook_script_present as _hook_script_present,
    read_json_config as _read_settings,
    update_hook_command as _update_hook_command,
)
from .registry import register_adapter


SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
AWARENESS_PATH = Path.home() / ".claude" / "CLAUDE.md"
SERVER_NAME = "agent-memory-hub"


class ClaudeCodeAdapter(AdapterBase):
    """Real-install adapter for Claude Code (claude.ai/code CLI)."""

    HOOK_EVENTS = (
        "SessionStart",
        "UserPromptSubmit",
        "Stop",
        "PreCompact",
        "PostCompact",
        "SubagentStart",
        "SubagentStop",
    )
    HOOK_SCRIPTS = {
        "SessionStart": "inject-discipline.sh",
        "UserPromptSubmit": "inject-context.sh",
        "Stop": "session-end-signal.sh",
        "PreCompact": "lifecycle-event.sh",
        "PostCompact": "lifecycle-event.sh",
        "SubagentStart": "lifecycle-event.sh",
        "SubagentStop": "lifecycle-event.sh",
    }

    def __init__(self, brain_dir: Path, repo_dir: Path | None = None):
        super().__init__(brain_dir)
        # repo_dir is where agent_runtime_kit/hooks/ lives — the agent-memory-hub checkout
        # itself. brain_dir is the data dir (~/.agent-memory-hub by default);
        # these are intentionally separate so users can move data without
        # reinstalling hooks.
        self.repo_dir = repo_dir or Path(__file__).resolve().parents[2]
        self.hooks_dir = self.repo_dir / "agent_runtime_kit" / "hooks"
        self.mcp_server = self.repo_dir / "agent_runtime_kit" / "mcp" / "server.sh"

    def get_config(self) -> AdapterConfig:
        return AdapterConfig(
            agent_name="claude_code",
            config_dir=Path.home() / ".claude",
            hook_type="command",
            inject_method="system_prompt",
            supports_hooks=True,
            supports_mcp=True,
        )

    def install(self) -> str:
        """Append AMH-owned hooks and MCP server config to settings.json. Idempotent."""
        for event, script in self.HOOK_SCRIPTS.items():
            path = self.hooks_dir / script
            if not path.exists():
                raise FileNotFoundError(
                    f"hook script missing: {path} — is the agent-memory-hub repo intact?"
                )
        if not self.mcp_server.exists():
            raise FileNotFoundError(
                f"MCP launcher missing: {self.mcp_server} — is the agent-memory-hub repo intact?"
            )

        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        settings = _read_settings(SETTINGS_PATH)
        settings.setdefault("hooks", {})

        changed_events: list[str] = []
        for event in self.HOOK_EVENTS:
            script = self.hooks_dir / self.HOOK_SCRIPTS[event]
            existing = settings["hooks"].setdefault(event, [])
            command = _adapter_hook_command("claude_code", script)
            if _hook_script_present(existing, str(script)):
                if _update_hook_command(existing, str(script), command):
                    changed_events.append(event)
                continue
            existing.append({
                "matcher": "",
                "hooks": [{"type": "command", "command": command}],
            })
            changed_events.append(event)

        mcp_changed = self._install_mcp_server(settings)
        _atomic_write_settings(SETTINGS_PATH, settings)
        awareness_changed = install_awareness_block(AWARENESS_PATH, self._awareness_block())
        if not changed_events and not mcp_changed and not awareness_changed:
            return f"claude_code adapter: already installed at {SETTINGS_PATH}"
        if not changed_events and not mcp_changed:
            return f"claude_code adapter: installed/updated awareness channel in {AWARENESS_PATH}"
        if not changed_events:
            return f"claude_code adapter: installed/updated MCP server in {SETTINGS_PATH}"
        return (
            f"claude_code adapter: installed {len(changed_events)} hook(s) "
            f"({', '.join(changed_events)}) into {SETTINGS_PATH}"
        )

    def uninstall(self) -> str:
        """Remove hub-owned hooks from settings.json. Other tools' hooks left alone."""
        if not SETTINGS_PATH.exists():
            return f"claude_code adapter: {SETTINGS_PATH} does not exist, nothing to remove"
        settings = _read_settings(SETTINGS_PATH)
        if "hooks" not in settings:
            return "claude_code adapter: no hooks section, nothing to remove"

        removed = 0
        for event in self.HOOK_EVENTS:
            entries = settings["hooks"].get(event, [])
            kept = [e for e in entries if not _hook_belongs_to(e, str(self.hooks_dir))]
            removed += len(entries) - len(kept)
            settings["hooks"][event] = kept
        removed_mcp = self._uninstall_mcp_server(settings)
        removed_awareness = uninstall_awareness_block(AWARENESS_PATH)

        _atomic_write_settings(SETTINGS_PATH, settings)
        if removed_mcp and removed == 0 and not removed_awareness:
            return "claude_code adapter: removed hub-owned MCP entry"
        return (
            f"claude_code adapter: removed {removed} hub-owned hook entr"
            f"{'y' if removed == 1 else 'ies'}"
            f"{' and MCP entry' if removed_mcp else ''}"
            f"{' and awareness channel' if removed_awareness else ''}"
        )

    def inject_context(self, query: str) -> str:
        # The UserPromptSubmit hook (inject-context.sh) does the real work
        # at prompt time using FTS5+vec retrieval against the brain pool.
        # This method exists for adapter-base symmetry and offline preview.
        return (
            f"# Brain-pool context for: {query}\n"
            f"# Hook: {self.hooks_dir / 'inject-context.sh'}\n"
            f"# Data: {self.brain_dir}\n"
        )

    def get_install_instructions(self) -> str:
        return (
            "## Claude Code Adapter\n\n"
            f"Installs 7 hooks into `{SETTINGS_PATH}`:\n"
            f"- SessionStart  → `{self.hooks_dir / 'inject-discipline.sh'}`\n"
            f"- UserPromptSubmit → `{self.hooks_dir / 'inject-context.sh'}`\n"
            f"- Stop → `{self.hooks_dir / 'session-end-signal.sh'}`\n"
            f"- PreCompact/PostCompact/SubagentStart/SubagentStop → `{self.hooks_dir / 'lifecycle-event.sh'}`\n\n"
            "Run programmatically:\n\n"
            "    from agent_brain.agent_integrations.claude_code import ClaudeCodeAdapter\n"
            "    ClaudeCodeAdapter(brain_dir=Path.home() / '.agent-memory-hub').install()\n\n"
            "Idempotent — re-running detects existing entries.\n"
            "To remove: call `.uninstall()`."
        )

    def diagnose(self) -> AdapterDiagnosticReport:
        checks = [
            self._diagnose_settings_hooks(),
            self._diagnose_hook_scripts(),
            self._diagnose_mcp_server(),
            self._diagnose_awareness_channel(),
            diagnose_runtime_evidence(
                brain_dir=self.brain_dir,
                adapter="claude_code",
                check_name="Claude Code runtime evidence",
            ),
            diagnose_layered_context_pack_evidence(
                brain_dir=self.brain_dir,
                adapter="claude_code",
                check_name="Claude Code layered context pack evidence",
            ),
        ]
        return AdapterDiagnosticReport(
            adapter="claude_code",
            overall_status=overall_status(checks),
            checks=checks,
        )

    def _diagnose_settings_hooks(self) -> AdapterDiagnosticCheck:
        return diagnose_settings_hooks(
            settings_path=SETTINGS_PATH,
            hooks_dir=self.hooks_dir,
            hook_events=self.HOOK_EVENTS,
            hook_scripts=self.HOOK_SCRIPTS,
        )

    def _diagnose_hook_scripts(self) -> AdapterDiagnosticCheck:
        return diagnose_hook_scripts(
            hooks_dir=self.hooks_dir,
            hook_scripts=self.HOOK_SCRIPTS,
        )

    def _diagnose_mcp_server(self) -> AdapterDiagnosticCheck:
        return diagnose_mcp_json_server(
            check_name="Claude Code MCP server",
            config_path=SETTINGS_PATH,
            server_name=SERVER_NAME,
            expected_command=str(self.mcp_server),
            expected_args=[],
            expected_env={"BRAIN_DIR": str(self.brain_dir)},
            install_command="memory adapter install claude_code",
        )

    def _diagnose_awareness_channel(self) -> AdapterDiagnosticCheck:
        return diagnose_awareness_block(
            check_name="Claude Code awareness channel",
            path=AWARENESS_PATH,
            brain_dir=self.brain_dir,
            install_command="memory adapter install claude_code",
        )

    def _awareness_block(self) -> str:
        return render_awareness_block(
            agent_name="Claude Code",
            brain_dir=self.brain_dir,
            tool_channel="Claude Code MCP server `agent-memory-hub` plus lifecycle hooks",
            extra_guidance=(
                "SessionStart hook injects the memory discipline dynamically; this CLAUDE.md block is the static fallback.",
                "UserPromptSubmit hook may already inject memory candidates, but proactive MCP search is still expected for non-trivial work.",
            ),
        )

    def _install_mcp_server(self, settings: dict) -> bool:
        servers = settings.setdefault("mcpServers", {})
        expected = {
            "command": str(self.mcp_server),
            "args": [],
            "env": {"BRAIN_DIR": str(self.brain_dir)},
        }
        if servers.get(SERVER_NAME) == expected:
            return False
        servers[SERVER_NAME] = expected
        return True

    def _uninstall_mcp_server(self, settings: dict) -> bool:
        servers = settings.get("mcpServers")
        if not isinstance(servers, dict) or SERVER_NAME not in servers:
            return False
        del servers[SERVER_NAME]
        return True

register_adapter("claude_code", ClaudeCodeAdapter)
