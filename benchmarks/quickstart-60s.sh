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

BENCH_ROOT=""
ACTIVE_PID=""
ACTIVE_PGID=""

cleanup() {
  if [ "$KEEP" -eq 0 ] && [ -n "$BENCH_ROOT" ]; then rm -rf "$BENCH_ROOT"; fi
}

process_group_exists() {
  kill -0 -- "-$1" 2>/dev/null
}

process_group_for_pid() {
  ps -o pgid= -p "$1" 2>/dev/null | tr -d ' '
}

stop_active_phase() {
  local phase_pid phase_pgid use_group current_pgid grace_step
  phase_pid="$ACTIVE_PID"
  phase_pgid="$ACTIVE_PGID"
  ACTIVE_PID="" ACTIVE_PGID=""

  if [ -z "$phase_pid" ]; then
    return
  fi

  use_group=0
  current_pgid=$(process_group_for_pid "$phase_pid")
  if [ -n "$current_pgid" ] && process_group_exists "$current_pgid"; then
    phase_pgid="$current_pgid"
    use_group=1
    kill -TERM -- "-$phase_pgid" 2>/dev/null || true
  elif [ -n "$phase_pgid" ] && process_group_exists "$phase_pgid"; then
    use_group=1
    kill -TERM -- "-$phase_pgid" 2>/dev/null || true
  fi
  if [ "$use_group" -eq 0 ]; then
    kill -TERM "$phase_pid" 2>/dev/null || true
  fi

  grace_step=0
  while [ "$grace_step" -lt 10 ]; do
    if [ "$use_group" -eq 1 ]; then
      process_group_exists "$phase_pgid" || break
    else
      kill -0 "$phase_pid" 2>/dev/null || break
    fi
    sleep 0.1
    grace_step=$((grace_step + 1))
  done

  if [ "$use_group" -eq 1 ] && process_group_exists "$phase_pgid"; then
    kill -KILL -- "-$phase_pgid" 2>/dev/null || true
  elif kill -0 "$phase_pid" 2>/dev/null; then
    kill -KILL "$phase_pid" 2>/dev/null || true
  fi
  wait "$phase_pid" 2>/dev/null || true
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

if ! BENCH_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/amh-bench-XXXXXX"); then
  echo "failed to create benchmark root" >&2
  exit 1
fi
if [ -z "$BENCH_ROOT" ] || [ ! -d "$BENCH_ROOT" ]; then
  echo "failed to create benchmark root" >&2
  exit 1
fi
SOURCE_REPO=$(cd "$(dirname "$0")/.." && pwd)
BENCH_HOME="$BENCH_ROOT/home"
BENCH_BRAIN="$BENCH_ROOT/brain"
CLONE_DIR="$BENCH_ROOT/agent-memory-hub"

if ! mkdir -p \
  "$BENCH_HOME" \
  "$BENCH_BRAIN" \
  "$BENCH_ROOT/cache/pip" \
  "$BENCH_ROOT/cache/uv" \
  "$BENCH_ROOT/tmp" \
  "$BENCH_ROOT/xdg-config" \
  "$BENCH_ROOT/xdg-data" \
  "$BENCH_ROOT/xdg-state" \
  "$BENCH_ROOT/pyuserbase" \
  "$BENCH_ROOT/pycache" \
  "$BENCH_ROOT/pip-src" \
  "$BENCH_ROOT/cargo" \
  "$BENCH_ROOT/cargo-target" \
  "$BENCH_ROOT/rustup" \
  "$BENCH_ROOT/xdg-runtime"; then
  echo "failed to initialize benchmark root" >&2
  exit 1
fi
if ! chmod 700 "$BENCH_ROOT/xdg-runtime"; then
  echo "failed to initialize benchmark root" >&2
  exit 1
fi

run_isolated() (
  unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY
  unset GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_INDEX_FILE
  unset GIT_TRACE GIT_TRACE2 GIT_TRACE2_EVENT GIT_TRACE2_PERF
  unset GIT_TRACE_PERFORMANCE GIT_TRACE_PACKET GIT_TRACE_PACK_ACCESS
  unset GIT_TRACE_SETUP GIT_TRACE_CURL GIT_TRACE_SHALLOW GIT_TRACE_FSMONITOR
  unset GIT_TRACE_REFS GIT_TRACE_PACKFILE
  unset PIP_LOG PIP_REPORT PIP_BUILD_TRACKER PIP_TARGET PIP_PREFIX PIP_ROOT
  unset PIP_DOWNLOAD_CACHE PYTHONHOME VIRTUAL_ENV UV_PROJECT_ENVIRONMENT
  unset BASH_ENV ENV CDPATH
  export HOME="$BENCH_HOME"
  export BRAIN_DIR="$BENCH_BRAIN"
  export AGENT_MEMORY_HUB_BIN="$BENCH_HOME/.local/bin"
  export AGENT_MEMORY_HUB_HOME="$CLONE_DIR"
  export TMPDIR="$BENCH_ROOT/tmp"
  export TMP="$BENCH_ROOT/tmp"
  export TEMP="$BENCH_ROOT/tmp"
  export TEMPDIR="$BENCH_ROOT/tmp"
  export XDG_CONFIG_HOME="$BENCH_ROOT/xdg-config"
  export XDG_CACHE_HOME="$BENCH_ROOT/cache"
  export XDG_DATA_HOME="$BENCH_ROOT/xdg-data"
  export XDG_STATE_HOME="$BENCH_ROOT/xdg-state"
  export XDG_RUNTIME_DIR="$BENCH_ROOT/xdg-runtime"
  export PIP_CONFIG_FILE=/dev/null
  export PIP_CACHE_DIR="$BENCH_ROOT/cache/pip"
  export PIP_SRC="$BENCH_ROOT/pip-src"
  export PYTHONUSERBASE="$BENCH_ROOT/pyuserbase"
  export PYTHONPYCACHEPREFIX="$BENCH_ROOT/pycache"
  export CARGO_HOME="$BENCH_ROOT/cargo"
  export CARGO_TARGET_DIR="$BENCH_ROOT/cargo-target"
  export RUSTUP_HOME="$BENCH_ROOT/rustup"
  export UV_CACHE_DIR="$BENCH_ROOT/cache/uv"
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
ACTIVE_PID=$! ACTIVE_PGID=""
ACTIVE_PGID=$(process_group_for_pid "$ACTIVE_PID")
[ -n "$ACTIVE_PGID" ] || ACTIVE_PGID="$ACTIVE_PID"
wait "$ACTIVE_PID"
CLONE_STATUS=$? ACTIVE_PID="" ACTIVE_PGID=""
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
ACTIVE_PID=$! ACTIVE_PGID=""
ACTIVE_PGID=$(process_group_for_pid "$ACTIVE_PID")
[ -n "$ACTIVE_PGID" ] || ACTIVE_PGID="$ACTIVE_PID"
wait "$ACTIVE_PID"
INSTALL_STATUS=$? ACTIVE_PID="" ACTIVE_PGID=""
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
ACTIVE_PID=$! ACTIVE_PGID=""
ACTIVE_PGID=$(process_group_for_pid "$ACTIVE_PID")
[ -n "$ACTIVE_PGID" ] || ACTIVE_PGID="$ACTIVE_PID"
wait "$ACTIVE_PID"
SEARCH_STATUS=$? ACTIVE_PID="" ACTIVE_PGID=""
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
