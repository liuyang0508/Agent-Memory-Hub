"""Entry points must not re-implement persistence — they funnel through WriteService.

Task 3 routes both the MCP ``write_memory`` tool and the CLI ``write`` command
through the single ``WriteService`` write path established in Task 2. These tests
pin that invariant structurally: each entry point's source must reference the
funnel and must NOT contain a direct ``store.write(`` persistence call. This keeps
the "md append is the only 'written' verdict" + best-effort-index contract in one
place instead of being duplicated (and drifting) across entry points.
"""
import inspect

import agent_brain.interfaces.cli as cli_mod
import agent_brain.interfaces.mcp.server as mcp_mod


def test_mcp_write_memory_delegates_to_write_service():
    src = inspect.getsource(mcp_mod.write_memory)
    # The MCP tool must not re-implement persistence; it calls the funnel.
    assert "WriteService" in src or "write_service" in src
    assert "store.write(" not in src      # no direct persistence in the tool


def test_cli_write_delegates_to_write_service():
    src = inspect.getsource(cli_mod.write)
    assert "WriteService" in src or "write_service" in src
    assert "store.write(" not in src      # no direct persistence in the command
