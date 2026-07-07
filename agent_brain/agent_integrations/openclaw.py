"""OpenClaw adapter.

OpenClaw exposes an MCP registry CLI (`openclaw mcp set/remove/doctor`).
This adapter registers the Agent Memory Hub MCP server through that CLI rather
than editing OpenClaw's private config files directly.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from . import AdapterBase, AdapterConfig
from .awareness import (
    diagnose_awareness_block,
    install_awareness_block,
    render_awareness_block,
    uninstall_awareness_block,
)
from .diagnostics import AdapterDiagnosticCheck, AdapterDiagnosticReport, overall_status
from .python_runtime import amh_python_executable
from .registry import register_adapter

SERVER_NAME = "agent-memory-hub"
AWARENESS_PATH = Path.home() / ".openclaw" / "agent-memory-hub-awareness.md"
OPENCLAW_CONFIG_PATH = Path.home() / ".openclaw" / "openclaw.json"
MCP_ARGS = ["-m", "agent_brain.interfaces.mcp.server"]


class OpenClawAdapter(AdapterBase):
    """Install-ready adapter for OpenClaw via its MCP registry CLI."""

    def get_config(self) -> AdapterConfig:
        return AdapterConfig(
            agent_name="openclaw",
            config_dir=Path.home() / ".openclaw",
            hook_type="command",
            inject_method="mcp_tool",
            supports_hooks=False,
            supports_mcp=True,
        )

    def install(self) -> str:
        _require_openclaw()
        awareness_msg = self._install_awareness()
        payload = {
            "command": amh_python_executable(),
            "args": MCP_ARGS,
            "env": {"BRAIN_DIR": str(self.brain_dir)},
            "enabled": True,
        }
        _run_openclaw(["openclaw", "mcp", "set", SERVER_NAME, json.dumps(payload)])
        return f"{awareness_msg} | openclaw adapter: registered MCP server via `openclaw mcp set`"

    def uninstall(self) -> str:
        awareness_msg = self._uninstall_awareness()
        try:
            _require_openclaw()
            _run_openclaw(["openclaw", "mcp", "remove", SERVER_NAME])
        except RuntimeError as exc:
            if not _local_registry_contains_server():
                return (
                    f"{awareness_msg} | openclaw adapter: `openclaw mcp remove` failed "
                    f"({exc}); no local {SERVER_NAME} registry entry found"
                )
            raise
        return f"{awareness_msg} | openclaw adapter: removed MCP server via `openclaw mcp remove`"

    def inject_context(self, query: str) -> str:
        return (
            f"# OpenClaw context injection via MCP server '{SERVER_NAME}'\n"
            f"# Use MCP search_memory with query: {query}\n"
            f"# Data: {self.brain_dir}\n"
        )

    def get_install_instructions(self) -> str:
        return (
            "## OpenClaw Adapter\n\n"
            f"Writes an Awareness Channel to `{AWARENESS_PATH}` and registers "
            "Agent Memory Hub through OpenClaw's MCP registry CLI:\n\n"
            "    memory adapter install openclaw\n\n"
            "Equivalent underlying command:\n\n"
            "    openclaw mcp set agent-memory-hub '<server-json>'\n\n"
            "Uninstall removes only the `agent-memory-hub` registry entry."
        )

    def diagnose(self) -> AdapterDiagnosticReport:
        checks = [
            diagnose_awareness_block(
                check_name="OpenClaw awareness channel",
                path=AWARENESS_PATH,
                brain_dir=self.brain_dir,
                install_command="memory adapter install openclaw",
            ),
            self._diagnose_registry(),
        ]
        return AdapterDiagnosticReport(
            adapter="openclaw",
            overall_status=overall_status(checks),
            checks=checks,
        )

    def _install_awareness(self) -> str:
        changed = install_awareness_block(AWARENESS_PATH, self._awareness_block())
        if not changed:
            return f"openclaw adapter: awareness channel already installed in {AWARENESS_PATH}"
        return f"openclaw adapter: installed awareness channel in {AWARENESS_PATH}"

    def _uninstall_awareness(self) -> str:
        if uninstall_awareness_block(AWARENESS_PATH):
            return f"openclaw adapter: removed awareness channel from {AWARENESS_PATH}"
        return "openclaw adapter: no awareness channel, nothing to remove"

    def _awareness_block(self) -> str:
        return render_awareness_block(
            agent_name="OpenClaw",
            brain_dir=self.brain_dir,
            tool_channel="OpenClaw MCP registry entry `agent-memory-hub`",
            extra_guidance=(
                "OpenClaw's registry CLI installs the tool channel; this sidecar is the awareness layer.",
                "Use the AMH MCP tools for memory search/read/write when OpenClaw exposes them.",
            ),
        )

    def _diagnose_registry(self) -> AdapterDiagnosticCheck:
        if shutil.which("openclaw") is None:
            return AdapterDiagnosticCheck(
                name="OpenClaw MCP registry",
                status="error",
                detail="openclaw CLI not found on PATH",
                fix="install OpenClaw CLI, then run: memory adapter install openclaw",
            )
        try:
            result = _run_openclaw(["openclaw", "mcp", "doctor", SERVER_NAME])
        except RuntimeError as exc:
            return AdapterDiagnosticCheck(
                name="OpenClaw MCP registry",
                status="error",
                detail=str(exc),
                fix="run: memory adapter install openclaw",
            )
        return AdapterDiagnosticCheck(
            name="OpenClaw MCP registry",
            status="ok",
            detail=result.stdout.strip() or f"{SERVER_NAME} passed OpenClaw MCP doctor",
        )


def _require_openclaw() -> None:
    if shutil.which("openclaw") is None:
        raise RuntimeError(
            "openclaw CLI not found on PATH; install OpenClaw CLI before running this adapter"
        )


def _run_openclaw(args: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"`{' '.join(args)}` failed: {detail}")
    return result


def _local_registry_contains_server() -> bool:
    if not OPENCLAW_CONFIG_PATH.exists():
        return False
    try:
        data = json.loads(OPENCLAW_CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return True
    return _contains_server_name(data)


def _contains_server_name(value: object) -> bool:
    if isinstance(value, dict):
        return any(key == SERVER_NAME or _contains_server_name(inner) for key, inner in value.items())
    if isinstance(value, list):
        return any(_contains_server_name(item) for item in value)
    return value == SERVER_NAME


register_adapter("openclaw", OpenClawAdapter)
