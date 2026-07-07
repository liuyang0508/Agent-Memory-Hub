#!/usr/bin/env bash
# agent_runtime_kit/mcp/server.sh — v1 thin wrapper.
# Legacy implementation preserved in agent_runtime_kit/mcp/_legacy/server.sh
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_DIR=$(cd "$SCRIPT_DIR/../.." && pwd)
PYTHON_RESOLVER="$REPO_DIR/agent_runtime_kit/tools/_resolve-python.sh"

if [ -f "$PYTHON_RESOLVER" ]; then
  export AGENT_MEMORY_HUB_PYTHON_IMPORTS="agent_brain.interfaces.mcp.server"
  # shellcheck source=/dev/null
  source "$PYTHON_RESOLVER"
else
  echo "python resolver missing: $PYTHON_RESOLVER" >&2
  exit 127
fi

if [ "${_PYTHON_OK:-1}" -ne 0 ] || [ -z "${MEMORY_PYTHON:-}" ]; then
  echo "python with agent-memory-hub MCP dependencies is required to start the MCP server" >&2
  exit 127
fi

exec "$MEMORY_PYTHON" -m agent_brain.interfaces.mcp.server "$@"
