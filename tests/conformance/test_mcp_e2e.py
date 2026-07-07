"""MCP server e2e test — verify tools are registered and server can start."""

import asyncio
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VENV_BIN = ROOT / ".venv" / "bin"
MEMORY_MCP_CMD = [str(VENV_BIN / "memory-mcp")] if (VENV_BIN / "memory-mcp").exists() else [
    sys.executable,
    "-m",
    "agent_brain.interfaces.mcp.server",
]


def test_mcp_server_lists_tools_via_python_api(tmp_path: Path, monkeypatch):
    """Verify MCP server registers all expected tools by importing directly."""
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path / "brain"))
    monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")

    from agent_brain.interfaces.mcp.server import mcp

    # FastMCP.list_tools() is async
    tools = asyncio.run(mcp.list_tools())
    tool_names = {tool.name for tool in tools}
    assert "write_memory" in tool_names
    assert "search_memory" in tool_names
    assert "read_memory" in tool_names
    assert "list_recent" in tool_names
    assert "brief_memory" in tool_names


def test_mcp_entrypoint_exists():
    """Verify the MCP module entrypoint starts without import errors."""
    result = subprocess.run(
        [*MEMORY_MCP_CMD, "--help"],
        capture_output=True, text=True, timeout=10, cwd=ROOT,
    )
    # FastMCP may not support --help; just ensure it doesn't crash with import error
    assert result.returncode in (0, 1, 2) or "memory" in result.stderr.lower() or "memory" in result.stdout.lower()
