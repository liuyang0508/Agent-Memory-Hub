"""Continue adapter for registering the hub MCP server in Continue config.yaml.

Continue's CLI configuration docs use ``~/.continue/config.yaml`` as the
global config path and support a top-level ``mcpServers`` list. This adapter
only manages the ``agent-memory-hub`` entry and preserves all other Continue
configuration.
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
from .diagnostics import AdapterDiagnosticReport, diagnose_mcp_yaml_server, overall_status
from .python_runtime import amh_python_executable
from .registry import register_adapter

MCP_CONFIG_PATH = Path.home() / ".continue" / "config.yaml"
AWARENESS_PATH = Path.home() / ".continue" / "rules" / "agent-memory-hub.md"
SERVER_NAME = "agent-memory-hub"
MCP_ARGS = ["-m", "agent_brain.interfaces.mcp.server"]


class ContinueAdapter(AdapterBase):
    """Install-ready adapter for Continue via MCP server registration."""

    def get_config(self) -> AdapterConfig:
        return AdapterConfig(
            agent_name="continue_dev",
            config_dir=Path.home() / ".continue",
            hook_type="mcp",
            inject_method="mcp_tool",
            supports_hooks=False,
            supports_mcp=True,
        )

    def install(self) -> str:
        awareness_msg = self._install_awareness()
        config = _read_yaml_mapping(MCP_CONFIG_PATH)
        servers = config.setdefault("mcpServers", [])
        if not isinstance(servers, list):
            raise RuntimeError(
                f"refuse to overwrite {MCP_CONFIG_PATH}: mcpServers must be a list"
            )

        desired = _server_config(self.brain_dir)
        existing_index = _server_index(servers, SERVER_NAME)
        if existing_index is not None and servers[existing_index] == desired:
            return (
                f"{awareness_msg} | "
                f"continue adapter: MCP server already registered in {MCP_CONFIG_PATH}"
            )

        if existing_index is None:
            servers.append(desired)
        else:
            servers[existing_index] = desired
        _atomic_write_yaml(MCP_CONFIG_PATH, config)
        return f"{awareness_msg} | continue adapter: registered MCP server in {MCP_CONFIG_PATH}"

    def uninstall(self) -> str:
        awareness_msg = self._uninstall_awareness()
        if not MCP_CONFIG_PATH.exists():
            return (
                f"{awareness_msg} | "
                f"continue adapter: {MCP_CONFIG_PATH} does not exist, nothing to remove"
            )
        config = _read_yaml_mapping(MCP_CONFIG_PATH)
        servers = config.get("mcpServers", [])
        if not isinstance(servers, list):
            return f"{awareness_msg} | continue adapter: no hub MCP server entry, nothing to remove"

        existing_index = _server_index(servers, SERVER_NAME)
        if existing_index is None:
            return f"{awareness_msg} | continue adapter: no hub MCP server entry, nothing to remove"

        del servers[existing_index]
        _atomic_write_yaml(MCP_CONFIG_PATH, config)
        return f"{awareness_msg} | continue adapter: removed MCP server from {MCP_CONFIG_PATH}"

    def inject_context(self, query: str) -> str:
        return (
            f"# Continue context injection via MCP server '{SERVER_NAME}'\n"
            f"# Use MCP tool 'search_memory' with query: {query}\n"
            f"# Data: {self.brain_dir}\n"
        )

    def get_install_instructions(self) -> str:
        return (
            "## Continue Adapter\n\n"
            f"Registers the agent-memory-hub MCP server in `{MCP_CONFIG_PATH}` and "
            f"writes an Awareness Channel to `{AWARENESS_PATH}`.\n\n"
            "This gives Continue access to the hub MCP tools through its global\n"
            "`mcpServers` configuration. Re-running install is idempotent and\n"
            "uninstall removes only the `agent-memory-hub` server entry."
        )

    def diagnose(self) -> AdapterDiagnosticReport:
        checks = [
            diagnose_awareness_block(
                check_name="Continue awareness channel",
                path=AWARENESS_PATH,
                brain_dir=self.brain_dir,
                install_command="memory adapter install continue_dev",
            ),
            diagnose_mcp_yaml_server(
                check_name="Continue MCP server",
                config_path=MCP_CONFIG_PATH,
                server_name=SERVER_NAME,
                expected_command=amh_python_executable(),
                expected_args=MCP_ARGS,
                expected_env={"BRAIN_DIR": str(self.brain_dir)},
                install_command="memory adapter install continue_dev",
            ),
        ]
        return AdapterDiagnosticReport(
            adapter="continue_dev",
            overall_status=overall_status(checks),
            checks=checks,
        )

    def _install_awareness(self) -> str:
        changed = install_awareness_block(AWARENESS_PATH, self._awareness_block())
        if not changed:
            return f"continue adapter: awareness channel already installed in {AWARENESS_PATH}"
        return f"continue adapter: installed awareness channel in {AWARENESS_PATH}"

    def _uninstall_awareness(self) -> str:
        if uninstall_awareness_block(AWARENESS_PATH):
            return f"continue adapter: removed awareness channel from {AWARENESS_PATH}"
        return "continue adapter: no awareness channel, nothing to remove"

    def _awareness_block(self) -> str:
        return render_awareness_block(
            agent_name="Continue",
            brain_dir=self.brain_dir,
            tool_channel="Continue MCP tools from `~/.continue/config.yaml`",
            extra_guidance=(
                "The rules sidecar is the awareness layer; the YAML mcpServers entry is the tool layer.",
                "Use MCP memory tools proactively on resume, planning, debugging, and handoff work.",
            ),
        )


def _server_config(brain_dir: Path) -> dict[str, object]:
    return {
        "name": SERVER_NAME,
        "command": amh_python_executable(),
        "args": MCP_ARGS,
        "env": {"BRAIN_DIR": str(brain_dir)},
    }


def _server_index(servers: list[object], name: str) -> int | None:
    for index, entry in enumerate(servers):
        if isinstance(entry, dict) and entry.get("name") == name:
            return index
    return None


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


register_adapter("continue_dev", ContinueAdapter)
