#!/usr/bin/env bash
# list-playbook.sh — v1 thin wrapper. Forwards to `memory list-recent`.
# (M1 scope: list-recent stand-in until playbook namespace lands in M2+.)
# Legacy implementation preserved in agent_runtime_kit/tools/_legacy/list-playbook.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/_resolve-python.sh"
memory_cli list-recent "$@"
