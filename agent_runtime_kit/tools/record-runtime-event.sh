#!/usr/bin/env bash
# record-runtime-event.sh — append adapter hook runtime evidence.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/_resolve-python.sh"

BRAIN_DIR="${BRAIN_DIR:-$HOME/.agent-memory-hub}"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
"$MEMORY_PYTHON" "$REPO_ROOT/agent_brain/agent_integrations/runtime_events.py" record --brain-dir "$BRAIN_DIR" "$@"

# Opt-in only and throttled to once per hour inside the module. Avoid even
# starting Python on the default-off path, and never delay the agent on I/O.
TELEMETRY_ENABLED=false
case "${AMH_TELEMETRY:-}" in
  1|true|TRUE|yes|YES|on|ON) TELEMETRY_ENABLED=true ;;
  0|false|FALSE|no|NO|off|OFF) TELEMETRY_ENABLED=false ;;
  *)
    if [ -f "$BRAIN_DIR/product-telemetry.json" ] \
      && grep -Eq '"enabled"[[:space:]]*:[[:space:]]*true' "$BRAIN_DIR/product-telemetry.json"; then
      TELEMETRY_ENABLED=true
    fi
    ;;
esac
if [ "$TELEMETRY_ENABLED" = true ]; then
  BRAIN_DIR="$BRAIN_DIR" "$MEMORY_PYTHON" \
    -m agent_brain.platform.product_telemetry active \
    --adapter "${AGENT_MEMORY_HUB_ADAPTER:-unknown}" \
    >/dev/null 2>&1 &
fi
