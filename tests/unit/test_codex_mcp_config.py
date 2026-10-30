"""Removing an MCP server must not leave inferred TOML tables without a transport."""

import tomllib

import pytest

from agent_brain.agent_integrations.codex_config import remove_mcp_block


@pytest.mark.parametrize(
    "owned",
    [
        '[mcp_servers.agent-memory-hub]\ncommand = "legacy"\n'
        '[mcp_servers.agent-memory-hub.env]\nBRAIN_DIR = "/brain"\n'
        '[mcp_servers.agent-memory-hub.tools.write_memory]\napproval_mode = "approve"\n',
        '[mcp_servers."agent-memory-hub"]\ncommand = "legacy"\n'
        '[mcp_servers."agent-memory-hub".tools.read_memory]\napproval_mode = "auto"\n',
        '[mcp_servers.agent-memory-hub.tools.write_memory]\napproval_mode = "approve"\n',
        '[mcp_servers]\nagent-memory-hub = { command = "legacy", env = { BRAIN_DIR = "/brain" } }\n',
    ],
)
def test_remove_owned_server_including_nested_or_inline_tables(owned):
    text = 'model = "keep-model"\n# keep this explanation\n' + owned
    text += '\n[mcp_servers.agent-memory-hub-other]\ncommand = "keep-command"\n'
    changed = remove_mcp_block(text)
    parsed = tomllib.loads(changed)
    assert "agent-memory-hub" not in parsed["mcp_servers"]
    assert parsed["mcp_servers"]["agent-memory-hub-other"]["command"] == "keep-command"
    assert parsed["model"] == "keep-model"
    assert "# keep this explanation" in changed
    assert remove_mcp_block(changed) == changed


def test_absent_owned_server_preserves_exact_text():
    text = '# custom\nmodel="mine"\n[mcp_servers.other]\ncommand="other"\n'
    assert remove_mcp_block(text) == text
