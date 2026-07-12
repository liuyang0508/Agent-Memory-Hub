#!/usr/bin/env bash
#
# benchmarks/quickstart-60s.sh
#
# Measures: clean clone → minimal install (data dir + /remember + CLI) → first search.
# Default target: total < 120 seconds; override with AMH_QUICKSTART_TARGET_SECONDS.
#
# Usage:
#   ./benchmarks/quickstart-60s.sh [--keep]   # --keep = don't delete tmp dir on exit
#

set -uo pipefail
set -m

KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1

BENCH_ROOT=$(mktemp -d -t amh-bench-XXXXXX)
SOURCE_REPO=$(cd "$(dirname "$0")/.." && pwd)
BENCH_HOME="$BENCH_ROOT/home"
BENCH_BRAIN="$BENCH_ROOT/brain"
CLONE_DIR="$BENCH_ROOT/agent-memory-hub"
ACTIVE_PID=""

mkdir -p \
  "$BENCH_HOME" \
  "$BENCH_BRAIN" \
  "$BENCH_ROOT/cache/pip" \
  "$BENCH_ROOT/tmp" \
  "$BENCH_ROOT/xdg-config" \
  "$BENCH_ROOT/xdg-data" \
  "$BENCH_ROOT/xdg-state" \
  "$BENCH_ROOT/pyuserbase" \
  "$BENCH_ROOT/cargo" \
  "$BENCH_ROOT/rustup"

cleanup() {
  if [ "$KEEP" -eq 0 ]; then rm -rf "$BENCH_ROOT"; fi
}

stop_active_phase() {
  if [ -n "$ACTIVE_PID" ]; then
    kill -TERM -- "-$ACTIVE_PID" 2>/dev/null || kill -TERM "$ACTIVE_PID" 2>/dev/null || true
    wait "$ACTIVE_PID" 2>/dev/null || true
    ACTIVE_PID=""
  fi
}

handle_int() {
  stop_active_phase
  exit 130
}

handle_term() {
  stop_active_phase
  exit 143
}

trap cleanup EXIT
trap handle_int INT
trap handle_term TERM

run_isolated() (
  unset PIP_TARGET PIP_PREFIX PIP_ROOT PYTHONHOME VIRTUAL_ENV UV_PROJECT_ENVIRONMENT
  export HOME="$BENCH_HOME"
  export BRAIN_DIR="$BENCH_BRAIN"
  export AGENT_MEMORY_HUB_BIN="$BENCH_HOME/.local/bin"
  export AGENT_MEMORY_HUB_HOME="$CLONE_DIR"
  export TMPDIR="$BENCH_ROOT/tmp"
  export XDG_CONFIG_HOME="$BENCH_ROOT/xdg-config"
  export XDG_CACHE_HOME="$BENCH_ROOT/cache"
  export XDG_DATA_HOME="$BENCH_ROOT/xdg-data"
  export XDG_STATE_HOME="$BENCH_ROOT/xdg-state"
  export PIP_CONFIG_FILE=/dev/null
  export PIP_CACHE_DIR="$BENCH_ROOT/cache/pip"
  export PYTHONUSERBASE="$BENCH_ROOT/pyuserbase"
  export CARGO_HOME="$BENCH_ROOT/cargo"
  export RUSTUP_HOME="$BENCH_ROOT/rustup"
  exec "$@"
)

TARGET_SECONDS="${AMH_QUICKSTART_TARGET_SECONDS:-120}"
echo "=== Quickstart benchmark — target < ${TARGET_SECONDS}s ==="
echo "Tmp: $BENCH_ROOT"
echo "Source: $SOURCE_REPO"
echo ""

START=$(date +%s)

# Phase 1: clone (simulates new user)
PHASE1_START=$(date +%s)
CLONE_LOG="$BENCH_ROOT/clone.log"
run_isolated git clone --depth=1 "$SOURCE_REPO" "$CLONE_DIR" > "$CLONE_LOG" 2>&1 &
ACTIVE_PID=$!
wait "$ACTIVE_PID"
CLONE_STATUS=$?
ACTIVE_PID=""
if [ "$CLONE_STATUS" -ne 0 ]; then
  echo "  ✗ git clone failed:"
  tail -n 80 "$CLONE_LOG"
  exit 1
fi
tail -n 1 "$CLONE_LOG"
PHASE1=$(($(date +%s) - PHASE1_START))
echo "  ✓ clone: ${PHASE1}s"

# Phase 2: minimal install keeps the quickstart target focused on first-use setup.
PHASE2_START=$(date +%s)
INSTALL_LOG="$BENCH_ROOT/install.log"
run_isolated "$CLONE_DIR/install.sh" --minimal > "$INSTALL_LOG" 2>&1 &
ACTIVE_PID=$!
wait "$ACTIVE_PID"
INSTALL_STATUS=$?
ACTIVE_PID=""
if [ "$INSTALL_STATUS" -ne 0 ]; then
  echo "  ✗ install.sh failed:"
  tail -n 80 "$INSTALL_LOG"
  exit 1
fi
PHASE2=$(($(date +%s) - PHASE2_START))
echo "  ✓ install.sh: ${PHASE2}s"

# Phase 3: first query (search-memory.sh against empty brain)
PHASE3_START=$(date +%s)
SEARCH_LOG="$BENCH_ROOT/search.log"
run_isolated "$CLONE_DIR/agent_runtime_kit/tools/search-memory.sh" "anything" > "$SEARCH_LOG" 2>&1 &
ACTIVE_PID=$!
wait "$ACTIVE_PID"
SEARCH_STATUS=$?
ACTIVE_PID=""
if [ "$SEARCH_STATUS" -ne 0 ]; then
  echo "  ✗ first search failed:"
  tail -n 80 "$SEARCH_LOG"
  exit 1
fi
sed -n '1,3p' "$SEARCH_LOG"
PHASE3=$(($(date +%s) - PHASE3_START))
echo "  ✓ first search: ${PHASE3}s"

TOTAL=$(($(date +%s) - START))
echo ""
echo "=== Result: total = ${TOTAL}s (target: <${TARGET_SECONDS}s) ==="
echo "    breakdown: clone ${PHASE1}s + install ${PHASE2}s + search ${PHASE3}s"

if [ "$TOTAL" -lt "$TARGET_SECONDS" ]; then
  echo "✅ PASS"
  exit 0
else
  echo "❌ FAIL — over ${TARGET_SECONDS}s budget"
  exit 1
fi
