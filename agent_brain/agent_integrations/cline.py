"""Cline adapter — registers the MCP server in Cline's MCP config and writes
brain-pool discipline as a custom instruction block.

Cline (VS Code extension) reads MCP server configs from
``~/.cline/mcp_servers.json`` (global). We register our MCP server there
so every Cline workspace gets the brain pool.

Install is idempotent. Uninstall removes only our entries.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import AdapterBase, AdapterConfig
from .awareness import (
    diagnose_awareness_block,
    install_awareness_block,
    render_awareness_block,
    uninstall_awareness_block,
)
from .diagnostics import AdapterDiagnosticReport, diagnose_mcp_json_server, overall_status
from .python_runtime import amh_python_executable
from .registry import register_adapter

MCP_CONFIG_PATH = Path.home() / ".cline" / "mcp_servers.json"
AWARENESS_PATH = Path.home() / ".cline" / "agent-memory-hub-awareness.md"
SERVER_NAME = "agent-memory-hub"


class ClineAdapter(AdapterBase):
    """Real-install adapter for Cline (VS Code extension) via MCP server registration."""

    def __init__(self, brain_dir: Path, repo_dir: Path | None = None):
        super().__init__(brain_dir)
        self.repo_dir = repo_dir or Path(__file__).resolve().parents[2]
        self.discipline_md = (
            self.repo_dir / "agent_runtime_kit" / "AGENT_MEMORY_DISCIPLINE.md"
        )

    def get_config(self) -> AdapterConfig:
        return AdapterConfig(
            agent_name="cline",
            config_dir=Path.home() / ".cline",
            hook_type="mcp",
            inject_method="mcp_tool",
            supports_hooks=False,
            supports_mcp=True,
        )

    def install(self) -> str:
        return " | ".join([self._install_awareness(), self._install_mcp()])

    def uninstall(self) -> str:
        return " | ".join([self._uninstall_awareness(), self._uninstall_mcp()])

    def inject_context(self, query: str) -> str:
        return (
            f"# Cline context injection via MCP server '{SERVER_NAME}'\n"
            f"# Use MCP tool 'search_memory' with query: {query}\n"
            f"# Data: {self.brain_dir}\n"
        )

    def get_install_instructions(self) -> str:
        return (
            "## Cline Adapter\n\n"
            f"Registers the agent-memory-hub MCP server in `{MCP_CONFIG_PATH}` and "
            f"writes an Awareness Channel to `{AWARENESS_PATH}`.\n\n"
            "This gives Cline access to all 7 brain-pool MCP tools:\n"
            "  write_memory, search_memory, read_memory, list_recent,\n"
            "  delete_memory, audit_skill, audit_outbound\n\n"
            "Run programmatically:\n\n"
            "    from agent_brain.agent_integrations.cline import ClineAdapter\n"
            "    ClineAdapter(brain_dir=Path.home() / '.agent-memory-hub').install()\n\n"
            "Idempotent — re-running is a no-op if already present.\n"
            "To remove: call `.uninstall()`."
        )

    def diagnose(self) -> AdapterDiagnosticReport:
        checks = [
            diagnose_awareness_block(
                check_name="Cline awareness channel",
                path=AWARENESS_PATH,
                brain_dir=self.brain_dir,
                install_command="memory adapter install cline",
            ),
            diagnose_mcp_json_server(
                check_name="Cline MCP server",
                config_path=MCP_CONFIG_PATH,
                server_name=SERVER_NAME,
                expected_command=amh_python_executable(self.repo_dir),
                expected_args=["-m", "agent_brain.interfaces.mcp.server"],
                expected_env={"BRAIN_DIR": str(self.brain_dir)},
                install_command="memory adapter install cline",
            ),
        ]
        return AdapterDiagnosticReport(
            adapter="cline",
            overall_status=overall_status(checks),
            checks=checks,
        )

    def _install_awareness(self) -> str:
        changed = install_awareness_block(AWARENESS_PATH, self._awareness_block())
        if not changed:
            return f"cline adapter: awareness channel already installed in {AWARENESS_PATH}"
        return f"cline adapter: installed awareness channel in {AWARENESS_PATH}"

    def _uninstall_awareness(self) -> str:
        if uninstall_awareness_block(AWARENESS_PATH):
            return f"cline adapter: removed awareness channel from {AWARENESS_PATH}"
        return "cline adapter: no awareness channel, nothing to remove"

    def _awareness_block(self) -> str:
        return render_awareness_block(
            agent_name="Cline",
            brain_dir=self.brain_dir,
            tool_channel="Cline MCP tools from `~/.cline/mcp_servers.json`",
            extra_guidance=(
                "This file is the awareness sidecar; it does not prove real-client verified usage by itself.",
                "Use MCP `search_memory`/`brief_memory` before non-trivial plans or resume work.",
            ),
        )

    def _install_mcp(self) -> str:
        MCP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        config = _read_json(MCP_CONFIG_PATH)
        config.setdefault("mcpServers", {})

        desired = {
            "command": amh_python_executable(self.repo_dir),
            "args": ["-m", "agent_brain.interfaces.mcp.server"],
            "env": {"BRAIN_DIR": str(self.brain_dir)},
        }
        if SERVER_NAME in config["mcpServers"]:
            if config["mcpServers"][SERVER_NAME] != desired:
                config["mcpServers"][SERVER_NAME] = desired
                _atomic_write_json(MCP_CONFIG_PATH, config)
                return f"cline adapter: updated MCP server in {MCP_CONFIG_PATH}"
            return f"cline adapter: MCP server already registered in {MCP_CONFIG_PATH}"

        config["mcpServers"][SERVER_NAME] = desired
        _atomic_write_json(MCP_CONFIG_PATH, config)
        return f"cline adapter: registered MCP server in {MCP_CONFIG_PATH}"

    def _uninstall_mcp(self) -> str:
        if not MCP_CONFIG_PATH.exists():
            return f"cline adapter: {MCP_CONFIG_PATH} does not exist, nothing to remove"
        config = _read_json(MCP_CONFIG_PATH)
        servers = config.get("mcpServers", {})
        if SERVER_NAME not in servers:
            return "cline adapter: no hub MCP server entry, nothing to remove"
        del servers[SERVER_NAME]
        _atomic_write_json(MCP_CONFIG_PATH, config)
        return f"cline adapter: removed MCP server from {MCP_CONFIG_PATH}"


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        # utf-8-sig tolerates a leading BOM (json.loads rejects one otherwise).
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"refuse to overwrite malformed {path} — fix it by hand first: {exc}"
        ) from exc


def _atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


register_adapter("cline", ClineAdapter)
