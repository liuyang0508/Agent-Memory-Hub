"""MCP core tier tools. Bodies moved verbatim from mcp_server.py (design §6.2)."""
from __future__ import annotations

from agent_brain.interfaces.mcp.tools._shared import *  # noqa: F401,F403
from agent_brain.interfaces.mcp.tools.mutation_tools import (
    confirm_memory,
    delete_memory,
    update_memory,
    write_memory,
)
from agent_brain.interfaces.mcp.tools.read_tools import brief_memory, list_recent, read_memory
from agent_brain.interfaces.mcp.tools.search_tools import search_memory, tag_suggest
from agent_brain.interfaces.mcp.tools.status import brain_stats


def register(mcp) -> None:
    """Register this tier's tools on the FastMCP instance (called by server.register_all)."""
    mcp.tool()(write_memory)
    mcp.tool()(tag_suggest)
    mcp.tool()(search_memory)
    mcp.tool()(read_memory)
    mcp.tool()(list_recent)
    mcp.tool()(delete_memory)
    mcp.tool()(update_memory)
    mcp.tool()(confirm_memory)
    mcp.tool()(brain_stats)
    mcp.tool()(brief_memory)


__all__ = [
    'write_memory',
    'tag_suggest',
    'search_memory',
    'read_memory',
    'list_recent',
    'delete_memory',
    'update_memory',
    'confirm_memory',
    'brain_stats',
    'brief_memory',
]
