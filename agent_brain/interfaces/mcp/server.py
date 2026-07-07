"""FastMCP server assembly. Tool implementations live in mcp/tools/*; each module
exposes register(mcp). This file owns only the instance and registration order."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from agent_brain._version import __version__

mcp = FastMCP(f"agent-memory-hub-{__version__}")


def register_all() -> None:
    # Tool tiers (the actual brain operations).
    from agent_brain.interfaces.mcp.tools import core, governance, evolve, io, graph, conversation
    for mod in (core, governance, evolve, io, graph, conversation):
        mod.register(mcp)

    # Onboarding surfaces — taught to every connecting LLM agent so amh is
    # "open the box and it works" regardless of which MCP client is used.
    # See agent_brain/interfaces/mcp/onboarding.py for the single source of truth.
    from agent_brain.interfaces.mcp import onboarding, prompts, resources
    onboarding.register(mcp)   # get_usage_guide tool  (tool surface)
    prompts.register(mcp)      # agent_workflow_guide etc. (prompt surface)
    resources.register(mcp)    # guide:// URIs        (resource surface)


register_all()


def run() -> None:
    """Entrypoint for `memory-mcp` console script."""
    mcp.run()


from agent_brain.interfaces.mcp.tools._shared import _components_cache, _resolve_item_path  # noqa: E402,F401
from agent_brain.interfaces.mcp.tools.core import (  # noqa: E402,F401
    brain_stats,
    brief_memory,
    confirm_memory,
    delete_memory,
    list_recent,
    read_memory,
    search_memory,
    tag_suggest,
    update_memory,
    write_memory,
)
from agent_brain.interfaces.mcp.tools.evolve import evolve_memory  # noqa: E402,F401
from agent_brain.interfaces.mcp.tools.governance import (  # noqa: E402,F401
    audit_outbound,
    audit_skill,
    batch_archive,
    batch_confirm,
    drift_check,
    govern,
)
from agent_brain.interfaces.mcp.tools.graph import (  # noqa: E402,F401
    graph_memory,
    link_memories,
    unlink_memories,
)
from agent_brain.interfaces.mcp.tools.io import (  # noqa: E402,F401
    export_memory,
    gc_memory,
    import_memory,
    obsidian_export,
    obsidian_import,
)
from agent_brain.interfaces.mcp.tools.conversation import (  # noqa: E402,F401
    list_conversations,
    read_conversation,
)

__all__ = [
    "mcp",
    "register_all",
    "run",
    "_components_cache",
    "_resolve_item_path",
    "write_memory",
    "tag_suggest",
    "search_memory",
    "read_memory",
    "list_recent",
    "delete_memory",
    "update_memory",
    "confirm_memory",
    "brain_stats",
    "brief_memory",
    "audit_skill",
    "audit_outbound",
    "drift_check",
    "govern",
    "batch_confirm",
    "batch_archive",
    "evolve_memory",
    "graph_memory",
    "link_memories",
    "unlink_memories",
    "export_memory",
    "import_memory",
    "obsidian_export",
    "obsidian_import",
    "gc_memory",
    "list_conversations",
    "read_conversation",
]


if __name__ == "__main__":
    run()
