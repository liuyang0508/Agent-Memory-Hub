"""Agent Memory Hub Python SDK — lightweight client for non-MCP agents.

Usage:
    from agent_brain.interfaces.sdk import MemoryClient

    client = MemoryClient()  # uses default ~/.agent-memory-hub
    client.write(type="decision", title="Use SSE over WS", summary="...", body="...")
    results = client.search("SSE real-time push")
    client.reaffirm(results[0].id)
"""

from agent_brain.interfaces.sdk.sdk import MemoryClient, SearchResult

__all__ = ["MemoryClient", "SearchResult"]
