"""Hermes Agent adapter.

Hermes Agent supports external MCP servers in ``~/.hermes/config.yaml`` under
``mcp_servers``. This adapter registers the standard Agent Memory Hub MCP
server there. The separate Hermes memory-provider tool surface remains in
``agent_brain.agent_integrations.hermes``.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from . import AdapterBase, AdapterConfig
from .awareness import (
    diagnose_awareness_block,
    install_awareness_block,
    render_awareness_block,
    uninstall_awareness_block,
)
from .diagnostics import AdapterDiagnosticCheck, AdapterDiagnosticReport, overall_status
from .mcp_diagnostics import validate_mcp_server
from .python_runtime import amh_python_executable
from .registry import register_adapter

MCP_CONFIG_PATH = Path.home() / ".hermes" / "config.yaml"
AWARENESS_PATH = Path.home() / ".hermes" / "agent-memory-hub-awareness.md"
SERVER_NAME = "agent_memory_hub"
MCP_ARGS = ["-m", "agent_brain.interfaces.mcp.server"]


class HermesAgentAdapter(AdapterBase):
    """Install-ready adapter for Hermes Agent via MCP server config."""

    def get_config(self) -> AdapterConfig:
        return AdapterConfig(
            agent_name="hermes_agent",
            config_dir=Path.home() / ".hermes",
            hook_type="mcp",
            inject_method="mcp_tool",
            supports_hooks=False,
            supports_mcp=True,
        )

    def install(self) -> str:
        awareness_msg = self._install_awareness()
        config = _read_yaml_mapping(MCP_CONFIG_PATH)
        servers = config.setdefault("mcp_servers", {})
        if not isinstance(servers, dict):
            raise RuntimeError(
                f"refuse to overwrite {MCP_CONFIG_PATH}: mcp_servers must be a mapping"
            )

        desired = _server_config(self.brain_dir)
        if servers.get(SERVER_NAME) == desired:
            return (
                f"{awareness_msg} | "
                f"hermes_agent adapter: MCP server already registered in {MCP_CONFIG_PATH}"
            )

        servers[SERVER_NAME] = desired
        _atomic_write_yaml(MCP_CONFIG_PATH, config)
        return f"{awareness_msg} | hermes_agent adapter: registered MCP server in {MCP_CONFIG_PATH}"

    def uninstall(self) -> str:
        awareness_msg = self._uninstall_awareness()
        if not MCP_CONFIG_PATH.exists():
            return (
                f"{awareness_msg} | "
                f"hermes_agent adapter: {MCP_CONFIG_PATH} does not exist, nothing to remove"
            )
        config = _read_yaml_mapping(MCP_CONFIG_PATH)
        servers = config.get("mcp_servers", {})
        if not isinstance(servers, dict) or SERVER_NAME not in servers:
            return f"{awareness_msg} | hermes_agent adapter: no hub MCP server entry, nothing to remove"

        del servers[SERVER_NAME]
        _atomic_write_yaml(MCP_CONFIG_PATH, config)
        return f"{awareness_msg} | hermes_agent adapter: removed MCP server from {MCP_CONFIG_PATH}"

    def inject_context(self, query: str) -> str:
        return (
            f"# Hermes Agent context injection via MCP server '{SERVER_NAME}'\n"
            f"# Use MCP search_memory with query: {query}\n"
            f"# Data: {self.brain_dir}\n"
        )

    def get_install_instructions(self) -> str:
        return (
            "## Hermes Agent Adapter\n\n"
            f"Registers the Agent Memory Hub MCP server in `{MCP_CONFIG_PATH}` "
            "under `mcp_servers.agent_memory_hub` and writes an Awareness "
            f"Channel to `{AWARENESS_PATH}`.\n\n"
            "The Hermes memory-provider tools also exist as a standalone server:\n"
            "`python -m agent_brain.agent_integrations.hermes.provider`."
        )

    def diagnose(self) -> AdapterDiagnosticReport:
        checks = [
            diagnose_awareness_block(
                check_name="Hermes Agent awareness channel",
                path=AWARENESS_PATH,
                brain_dir=self.brain_dir,
                install_command="memory adapter install hermes_agent",
            ),
            _diagnose_hermes_mcp(
                config_path=MCP_CONFIG_PATH,
                brain_dir=self.brain_dir,
            ),
        ]
        return AdapterDiagnosticReport(
            adapter="hermes_agent",
            overall_status=overall_status(checks),
            checks=checks,
            brain_dir=self.brain_dir,
        )

    def _install_awareness(self) -> str:
        changed = install_awareness_block(AWARENESS_PATH, self._awareness_block())
        if not changed:
            return f"hermes_agent adapter: awareness channel already installed in {AWARENESS_PATH}"
        return f"hermes_agent adapter: installed awareness channel in {AWARENESS_PATH}"

    def _uninstall_awareness(self) -> str:
        if uninstall_awareness_block(AWARENESS_PATH):
            return f"hermes_agent adapter: removed awareness channel from {AWARENESS_PATH}"
        return "hermes_agent adapter: no awareness channel, nothing to remove"

    def _awareness_block(self) -> str:
        return render_awareness_block(
            agent_name="Hermes Agent",
            brain_dir=self.brain_dir,
            tool_channel="Hermes MCP server plus AMH Hermes provider tools",
            extra_guidance=(
                "Hermes provider tools can expose AMH search/read/write without pretending every adapter is a hook.",
                "Use provider tools for memory operations when that surface is active; otherwise use the MCP server.",
            ),
        )


def _server_config(brain_dir: Path) -> dict[str, object]:
    return {
        "command": amh_python_executable(),
        "args": MCP_ARGS,
        "env": {"BRAIN_DIR": str(brain_dir)},
    }


def _read_yaml_mapping(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except yaml.YAMLError as exc:
        raise RuntimeError(
            f"refuse to overwrite malformed {path} - fix it by hand first: {exc}"
        ) from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise RuntimeError(f"refuse to overwrite {path}: YAML root must be a mapping")
    return loaded


def _atomic_write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        yaml.safe_dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    tmp.replace(path)


def _diagnose_hermes_mcp(*, config_path: Path, brain_dir: Path) -> AdapterDiagnosticCheck:
    if not config_path.exists():
        return AdapterDiagnosticCheck(
            name="Hermes Agent MCP server",
            status="error",
            detail=f"missing: {config_path}",
            fix="run: memory adapter install hermes_agent",
        )

    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8-sig"))
    except yaml.YAMLError as exc:
        return AdapterDiagnosticCheck(
            name="Hermes Agent MCP server",
            status="error",
            detail=f"malformed YAML in {config_path}: {exc}",
            fix="repair YAML by hand, then run: memory adapter install hermes_agent",
        )

    if not isinstance(loaded, dict):
        return AdapterDiagnosticCheck(
            name="Hermes Agent MCP server",
            status="error",
            detail=f"{config_path} must contain a YAML mapping",
            fix="run: memory adapter install hermes_agent",
        )
    servers = loaded.get("mcp_servers")
    if not isinstance(servers, dict):
        return AdapterDiagnosticCheck(
            name="Hermes Agent MCP server",
            status="error",
            detail="missing top-level mcp_servers mapping",
            fix="run: memory adapter install hermes_agent",
        )
    server = servers.get(SERVER_NAME)
    if not isinstance(server, dict):
        return AdapterDiagnosticCheck(
            name="Hermes Agent MCP server",
            status="error",
            detail=f"missing MCP server: {SERVER_NAME}",
            fix="run: memory adapter install hermes_agent",
        )
    return validate_mcp_server(
        check_name="Hermes Agent MCP server",
        config_path=config_path,
        server_name=SERVER_NAME,
        server=server,
        expected_command=amh_python_executable(),
        expected_args=MCP_ARGS,
        expected_env={"BRAIN_DIR": str(brain_dir)},
        install_command="memory adapter install hermes_agent",
    )


register_adapter("hermes_agent", HermesAgentAdapter)
