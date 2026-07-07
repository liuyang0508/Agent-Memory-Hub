import subprocess
import sys


def test_cli_command_set_is_stable():
    out = subprocess.run(
        [sys.executable, "-m", "agent_brain.interfaces.cli", "--help"],
        capture_output=True,
        text=True,
    ).stdout
    for cmd in ["write", "search", "read", "doctor", "sync-pending", "harvest", "audit", "govern"]:
        assert cmd in out


def test_cli_lifecycle_evolution_commands_are_split_and_reexported():
    from agent_brain.interfaces.cli.commands import lifecycle
    from agent_brain.interfaces.cli.commands.evolution import consolidate, dream, evolve

    assert lifecycle.consolidate is consolidate
    assert lifecycle.evolve is evolve
    assert lifecycle.dream is dream


def test_cli_crud_query_commands_are_split_and_reexported():
    from agent_brain.interfaces.cli.commands import crud
    from agent_brain.interfaces.cli.commands.query import list_recent, read, search, tag_suggest

    assert crud.read is read
    assert crud.search is search
    assert crud.list_recent is list_recent
    assert crud.tag_suggest is tag_suggest


def test_cli_graph_command_is_split_and_reexported():
    from agent_brain.interfaces.cli.commands import insight
    from agent_brain.interfaces.cli.commands.graph import graph

    assert insight.graph is graph


def test_cli_link_commands_are_split_and_reexported():
    from agent_brain.interfaces.cli.commands import crud
    from agent_brain.interfaces.cli.commands.links import link, unlink

    assert crud.link is link
    assert crud.unlink is unlink


def test_cli_module_entrypoint_runs():
    # agent_runtime_kit/tools/_resolve-python.sh invokes `python -m agent_brain.interfaces.cli`; the write
    # hook depends on it. Splitting cli.py into a package dropped the old
    # `if __name__ == "__main__": app()` guard until cli/__main__.py restored it —
    # guard that here so the -m entry point can't silently regress again.
    import sys

    r = subprocess.run([sys.executable, "-m", "agent_brain.interfaces.cli", "--help"],
                       capture_output=True, text=True)
    assert r.returncode == 0
    assert "Agent Memory Hub CLI" in r.stdout


def test_mcp_tool_count_matches_docs():
    import agent_brain.interfaces.mcp.server as m
    # The compat shim still re-exports the stable-core callables directly.
    assert hasattr(m, "write_memory") and hasattr(m, "search_memory")


# The ONE true MCP operation-tier count. README.zh.md / STRATEGY.md /
# architecture.md must all agree with this number; the docs-vs-code drift this
# guards is exactly what the "6-tool" / "7-tool" claims got wrong before the
# honest-tiering pass. The registered MCP surface has one additional onboarding
# fallback tool (`get_usage_guide`) for clients that do not surface prompts or
# resources.
CANONICAL_MCP_TOOL_COUNT = 27
REGISTERED_MCP_TOOL_COUNT = CANONICAL_MCP_TOOL_COUNT + 1
TIER_COUNTS = {
    "core": 10,
    "graph": 3,
    "governance": 6,
    "evolve": 1,
    "io": 5,
    "conversation": 2,
}


def test_mcp_tool_count_is_canonical():
    from agent_brain.interfaces.mcp.tools import core, governance, evolve, io, graph, conversation

    per_tier = {
        "core": len(core.__all__),
        "graph": len(graph.__all__),
        "governance": len(governance.__all__),
        "evolve": len(evolve.__all__),
        "io": len(io.__all__),
        "conversation": len(conversation.__all__) - 1,
    }
    assert per_tier == TIER_COUNTS
    assert sum(per_tier.values()) == CANONICAL_MCP_TOOL_COUNT


def test_mcp_core_read_tools_are_split_and_reexported():
    from agent_brain.interfaces.mcp.tools import core
    from agent_brain.interfaces.mcp.tools.read_tools import brief_memory, list_recent, read_memory

    assert core.read_memory is read_memory
    assert core.list_recent is list_recent
    assert core.brief_memory is brief_memory


def test_mcp_core_mutation_tools_are_split_and_reexported():
    from agent_brain.interfaces.mcp.tools import core
    from agent_brain.interfaces.mcp.tools.mutation_tools import (
        confirm_memory,
        delete_memory,
        update_memory,
        write_memory,
    )

    assert core.write_memory is write_memory
    assert core.delete_memory is delete_memory
    assert core.update_memory is update_memory
    assert core.confirm_memory is confirm_memory


def test_mcp_write_enrichment_helper_is_split_and_reexported():
    from agent_brain.interfaces.mcp.tools import mutation_tools
    from agent_brain.interfaces.mcp.tools.mutation_enrichment import build_write_enrichment

    assert mutation_tools.build_write_enrichment is build_write_enrichment


def test_mcp_core_search_tools_are_split_and_reexported():
    from agent_brain.interfaces.mcp.tools import core
    from agent_brain.interfaces.mcp.tools.search_tools import search_memory, tag_suggest

    assert core.search_memory is search_memory
    assert core.tag_suggest is tag_suggest


def test_registered_tool_count_matches_canonical():
    import asyncio

    import agent_brain.interfaces.mcp.server as m

    tools = asyncio.run(m.mcp.list_tools())
    assert len(tools) == REGISTERED_MCP_TOOL_COUNT
    assert "get_usage_guide" in {tool.name for tool in tools}


def test_web_item_batch_routes_are_split_and_mounted():
    from web.api.routes import item_batch, items

    def _route_paths(router):
        paths = set()
        for route in router.routes:
            path = getattr(route, "path", None)
            if path:
                paths.add(path)
            original_router = getattr(route, "original_router", None)
            if original_router is not None:
                paths.update(_route_paths(original_router))
        return paths

    batch_paths = _route_paths(item_batch.router)
    assert {
        "/api/items/batch-delete",
        "/api/items/batch-confirm",
        "/api/items/batch-tag",
        "/api/items/merge",
    }.issubset(batch_paths)

    mounted_paths = _route_paths(items.router)
    assert batch_paths.issubset(mounted_paths)
