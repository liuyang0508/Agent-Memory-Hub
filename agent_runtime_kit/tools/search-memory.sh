#!/usr/bin/env bash
# search-memory.sh — v1 thin wrapper. Forwards to `memory search`.
# Legacy implementation preserved in agent_runtime_kit/tools/_legacy/search-memory.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/_resolve-python.sh"
# Hook/session-time search must return quickly on cold or offline machines.
# OFFLINE=1 forbids model downloads but still loads a locally cached model;
# only when no cached model exists does it degrade to HashingEmbedder.
export MEMORY_HUB_EMBEDDING_OFFLINE="${MEMORY_HUB_EMBEDDING_OFFLINE:-1}"
memory_cli search "$@"
