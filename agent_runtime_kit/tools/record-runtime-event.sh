#!/usr/bin/env bash
# record-runtime-event.sh — append adapter hook runtime evidence.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/_resolve-python.sh"

BRAIN_DIR="${BRAIN_DIR:-$HOME/.agent-memory-hub}"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
"$MEMORY_PYTHON" "$REPO_ROOT/agent_brain/agent_integrations/runtime_events.py" record --brain-dir "$BRAIN_DIR" "$@"
