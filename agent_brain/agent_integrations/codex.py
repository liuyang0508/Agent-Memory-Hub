"""OpenAI Codex CLI adapter.

Codex integration has three layers:

1. ``~/.codex/AGENTS.md`` for static discipline fallback.
2. ``~/.codex/hooks.json`` for memory and low-noise lifecycle hooks.
3. ``~/.codex/config.toml`` for the agent-memory-hub MCP server.

All writes are hub-scoped and idempotent: install adds or updates only
agent-memory-hub entries, and uninstall removes only those entries.
"""

from __future__ import annotations

from pathlib import Path

from . import AdapterBase, AdapterConfig
from .codex_config import (
    BEGIN,
    END,
    atomic_write_text as _atomic_write_text,
    mcp_block as _mcp_block,
    remove_block as _remove_block,  # noqa: F401 - re-exported for adapter split tests
    remove_mcp_block as _remove_mcp_block,
    upsert_block as _upsert_block,  # noqa: F401 - re-exported for adapter split tests
    upsert_mcp_block as _upsert_mcp_block,
)
from .codex_agents import (
    install_agents_block as _install_agents_block,
    render_agents_block as _render_agents_block,
    uninstall_agents_block as _uninstall_agents_block,
)
from .codex_diagnostics import diagnose_codex_config
from .codex_hooks import (
    install_hooks as _install_hooks,
    uninstall_hooks as _uninstall_hooks,
)
from .diagnostics import AdapterDiagnosticReport
from .hook_config import (
    adapter_hook_command as _adapter_hook_command,  # noqa: F401 - re-exported for adapter split tests
    atomic_write_json as _atomic_write_json,  # noqa: F401 - re-exported for adapter split tests
    hook_already_present as _hook_already_present,  # noqa: F401 - re-exported for adapter split tests
    hook_belongs_to as _hook_belongs_to,  # noqa: F401 - re-exported for adapter split tests
    hook_script_present as _hook_script_present,  # noqa: F401 - re-exported for adapter split tests
    read_json_config as _read_json_config,  # noqa: F401 - re-exported for adapter split tests
    update_hook_command as _update_hook_command,  # noqa: F401 - re-exported for adapter split tests
)
from .registry import register_adapter


AGENTS_MD = Path.home() / ".codex" / "AGENTS.md"
CODEX_HOOKS_JSON = Path.home() / ".codex" / "hooks.json"
CODEX_CONFIG_TOML = Path.home() / ".codex" / "config.toml"


class CodexAdapter(AdapterBase):
    """Real-install adapter for OpenAI Codex CLI."""

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
        "SessionStart": ("startup|resume", "inject-discipline.sh"),
        "UserPromptSubmit": ("", "inject-context.sh"),
        "Stop": ("", "session-end-signal.sh"),
        "PreCompact": ("", "lifecycle-event.sh"),
        "PostCompact": ("", "lifecycle-event.sh"),
        "SubagentStart": ("", "lifecycle-event.sh"),
        "SubagentStop": ("", "lifecycle-event.sh"),
    }

    def __init__(self, brain_dir: Path, repo_dir: Path | None = None):
        super().__init__(brain_dir)
        self.repo_dir = repo_dir or Path(__file__).resolve().parents[2]
        runtime_dir = self.repo_dir / "agent_runtime_kit"
        self.discipline_md = runtime_dir / "AGENT_MEMORY_DISCIPLINE.md"
        self.hooks_dir = runtime_dir / "hooks"
        self.mcp_server = runtime_dir / "mcp" / "server.sh"

    def get_config(self) -> AdapterConfig:
        return AdapterConfig(
            agent_name="codex",
            config_dir=Path.home() / ".codex",
            hook_type="file",
            inject_method="rules_file",
            supports_hooks=True,
            supports_mcp=True,
        )

    def install(self) -> str:
        self._validate_inputs()
        changed = [
            self._install_agents_block(),
            self._install_hooks(),
            self._install_mcp_server(),
        ]
        if not any(changed):
            return f"codex adapter: already up-to-date in {AGENTS_MD}"
        return (
            "codex adapter: installed/updated AGENTS.md, hooks, and MCP server "
            f"under {AGENTS_MD.parent}"
        )

    def uninstall(self) -> str:
        changed = [
            self._uninstall_agents_block(),
            self._uninstall_hooks(),
            self._uninstall_mcp_server(),
        ]
        if not any(changed):
            return "codex adapter: no hub-owned Codex config present, nothing to remove"
        return "codex adapter: removed hub-owned AGENTS.md, hooks, and MCP entries"

    def inject_context(self, query: str) -> str:
        # The UserPromptSubmit hook does real prompt-time retrieval. This
        # method exists for adapter-base symmetry and offline preview.
        return (
            f"# Codex brain-pool context hook: {self.hooks_dir / 'inject-context.sh'}\n"
            f"# Static fallback: {AGENTS_MD}\n"
            f"# MCP server: {self.mcp_server}\n"
            f"# Query for reference: {query}\n"
        )

    def get_install_instructions(self) -> str:
        return (
            "## Codex CLI Adapter\n\n"
            "Installs three hub-owned Codex integration layers:\n\n"
            f"- AGENTS.md discipline block: `{AGENTS_MD}`\n"
            f"- hooks.json entries: `{CODEX_HOOKS_JSON}`\n"
            f"- MCP server section: `{CODEX_CONFIG_TOML}`\n\n"
            "The AGENTS.md block is sentinel-bracketed:\n\n"
            f"    {BEGIN}\n"
            "    ... brain-pool discipline content ...\n"
            f"    {END}\n\n"
            "Run programmatically:\n\n"
            "    from agent_brain.agent_integrations.codex import CodexAdapter\n"
            "    CodexAdapter(brain_dir=Path.home() / '.agent-memory-hub').install()\n\n"
            "Idempotent — re-running updates hub-owned entries in place. `.uninstall()`\n"
            "removes only hub-owned entries, leaving user-written config alone."
        )

    def diagnose(self) -> AdapterDiagnosticReport:
        return diagnose_codex_config(
            agents_md=AGENTS_MD,
            hooks_json=CODEX_HOOKS_JSON,
            config_toml=CODEX_CONFIG_TOML,
            brain_dir=self.brain_dir,
            hook_events=self.HOOK_EVENTS,
            hook_scripts=self.HOOK_SCRIPTS,
            hooks_dir=self.hooks_dir,
            mcp_server=self.mcp_server,
        )

    def _validate_inputs(self) -> None:
        if not self.discipline_md.exists():
            raise FileNotFoundError(
                f"discipline doc missing: {self.discipline_md} — is the agent-memory-hub repo intact?"
            )
        for _, script in self.HOOK_SCRIPTS.values():
            path = self.hooks_dir / script
            if not path.exists():
                raise FileNotFoundError(
                    f"hook script missing: {path} — is the agent-memory-hub repo intact?"
                )
        if not self.mcp_server.exists():
            raise FileNotFoundError(
                f"MCP launcher missing: {self.mcp_server} — is the agent-memory-hub repo intact?"
            )

    def _install_agents_block(self) -> bool:
        return _install_agents_block(AGENTS_MD, _render_agents_block(self.discipline_md))

    def _uninstall_agents_block(self) -> bool:
        return _uninstall_agents_block(AGENTS_MD)

    def _install_hooks(self) -> bool:
        return _install_hooks(
            CODEX_HOOKS_JSON,
            self.hooks_dir,
            self.HOOK_SCRIPTS,
            CODEX_CONFIG_TOML,
        )

    def _uninstall_hooks(self) -> bool:
        return _uninstall_hooks(CODEX_HOOKS_JSON, self.hooks_dir, self.HOOK_EVENTS)

    def _install_mcp_server(self) -> bool:
        CODEX_CONFIG_TOML.parent.mkdir(parents=True, exist_ok=True)
        current = CODEX_CONFIG_TOML.read_text(encoding="utf-8") if CODEX_CONFIG_TOML.exists() else ""
        updated = _upsert_mcp_block(current, _mcp_block(self.mcp_server))
        if updated == current:
            return False
        _atomic_write_text(CODEX_CONFIG_TOML, updated)
        return True

    def _uninstall_mcp_server(self) -> bool:
        if not CODEX_CONFIG_TOML.exists():
            return False
        current = CODEX_CONFIG_TOML.read_text(encoding="utf-8")
        updated = _remove_mcp_block(current)
        if updated == current:
            return False
        _atomic_write_text(CODEX_CONFIG_TOML, updated)
        return True

register_adapter(
    "codex",
    CodexAdapter,
    display_names=("Codex", "Codex CLI"),
    aliases=("codex_cli",),
)
