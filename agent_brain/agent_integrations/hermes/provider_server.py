"""Hermes provider MCP registration and standalone server helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any


def register_tools(mcp_instance: Any, tools: Iterable[Callable[..., Any]]) -> None:
    """Register a sequence of Hermes tool callables on a FastMCP instance."""
    for tool in tools:
        mcp_instance.tool()(tool)


def run_provider(version: str, register: Callable[[Any], None]) -> None:
    """Run the standalone Hermes provider MCP server."""
    from mcp.server.fastmcp import FastMCP

    hermes_mcp = FastMCP(f"agent-memory-hub-hermes-{version}")
    register(hermes_mcp)
    hermes_mcp.run()


__all__ = ["register_tools", "run_provider"]
