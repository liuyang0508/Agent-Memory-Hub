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
BENCH_ROOT_OWNED=0
EXPECTED_TMP_PARENT=""
OWNERSHIP_TOKEN="amh-quickstart:$$:${RANDOM}"
OWNERSHIP_MARKER_NAME=".amh-quickstart-owned"
ACTIVE_PID=""
ACTIVE_PGID=""
ACTIVE_BENCHMARK_PGID=""
ACTIVE_GROUP_ANCHORED=0
FIRST_SIGNAL_CODE=0
PHASE_STARTING=0
SHUTDOWN_IN_PROGRESS=0
CLEANUP_IN_PROGRESS=0

root_path_is_canonical_direct_child() {
  local root raw_parent base expected_root canonical_root
  root="$1"
  [ -n "$root" ] || return 1
  case "$root" in /*) ;; *) return 1 ;; esac
  case "$root" in */) return 1 ;; esac
  raw_parent=${root%/*}
  [ -n "$raw_parent" ] || raw_parent="/"
  [ "$raw_parent" = "$EXPECTED_TMP_PARENT" ] || return 1
  base=${root##*/}
  case "$base" in amh-bench-*) ;; *) return 1 ;; esac
  if [ "$EXPECTED_TMP_PARENT" = "/" ]; then
    expected_root="/$base"
  else
    expected_root="$EXPECTED_TMP_PARENT/$base"
  fi
  [ "$root" = "$expected_root" ] || return 1
  [ -d "$root" ] || return 1
  [ ! -L "$root" ] || return 1
  canonical_root=$(cd "$root" && pwd -P) || return 1
  [ "$canonical_root" = "$root" ]
}

owned_root_is_valid() {
  local marker
  [ "$BENCH_ROOT_OWNED" -eq 1 ] || return 1
  root_path_is_canonical_direct_child "$BENCH_ROOT" || return 1
  marker="$BENCH_ROOT/$OWNERSHIP_MARKER_NAME"
  [ -f "$marker" ] || return 1
  [ ! -L "$marker" ] || return 1
  printf '%s\n' "$OWNERSHIP_TOKEN" | cmp -s - "$marker"
}

cleanup() {
  local owned_root
  [ "$CLEANUP_IN_PROGRESS" -eq 0 ] || return 0
  CLEANUP_IN_PROGRESS=1
  trap '' INT TERM
  [ "$KEEP" -eq 0 ] || return 0
  owned_root_is_valid || return 0
  owned_root="$BENCH_ROOT"
  if rm -rf -- "$owned_root"; then
    BENCH_ROOT_OWNED=0
    BENCH_ROOT=""
  else
    echo "warning: failed to remove owned benchmark root: $owned_root (ownership retained)" >&2
  fi
  return 0
}

create_bench_root() {
  local candidate marker
  candidate=$(mktemp -d "$EXPECTED_TMP_PARENT/amh-bench-XXXXXX") || return 1
  root_path_is_canonical_direct_child "$candidate" || return 1
  marker="$candidate/$OWNERSHIP_MARKER_NAME"
  (set -C; printf '%s\n' "$OWNERSHIP_TOKEN" > "$marker") || return 1
  [ -f "$marker" ] || return 1
  [ ! -L "$marker" ] || return 1
  printf '%s\n' "$OWNERSHIP_TOKEN" | cmp -s - "$marker" || return 1
  BENCH_ROOT="$candidate"
  BENCH_ROOT_OWNED=1
  owned_root_is_valid
}

process_group_exists() {
  kill -0 -- "-$1" 2>/dev/null
}

process_group_for_pid() {
  ps -o pgid= -p "$1" 2>/dev/null | tr -d ' '
}

process_state_for_pid() {
  ps -o stat= -p "$1" 2>/dev/null | tr -d ' '
}

stop_active_phase() {
  local phase_pid phase_pgid benchmark_pgid group_anchored grace_step
  phase_pid="$ACTIVE_PID"
  phase_pgid="$ACTIVE_PGID"
  benchmark_pgid="$ACTIVE_BENCHMARK_PGID"
  group_anchored="$ACTIVE_GROUP_ANCHORED"
  ACTIVE_PID="" ACTIVE_PGID="" ACTIVE_BENCHMARK_PGID="" ACTIVE_GROUP_ANCHORED=0

  if [ -z "$phase_pid" ]; then
    return
  fi

  [ "$group_anchored" -eq 1 ] || return
  [ -n "$phase_pgid" ] || return
  [ "$phase_pgid" = "$phase_pid" ] || return
  [ -n "$benchmark_pgid" ] || return
  [ "$phase_pgid" != "$benchmark_pgid" ] || return

  # The paused-launch anchor proves PGID == the direct child PID, so the
  # negative-PGID signals include the leader without a PID-reuse fallback.
  kill -TERM -- "-$phase_pgid" 2>/dev/null || true

  grace_step=0
  while [ "$grace_step" -lt 10 ]; do
    process_group_exists "$phase_pgid" || break
    sleep 0.1
    grace_step=$((grace_step + 1))
  done

  if process_group_exists "$phase_pgid"; then
    kill -KILL -- "-$phase_pgid" 2>/dev/null || true
  fi
  wait "$phase_pid" 2>/dev/null || true
}

dispatch_signal() {
  if [ "$FIRST_SIGNAL_CODE" -eq 0 ]; then
    FIRST_SIGNAL_CODE="$1"
  fi
  if [ "$PHASE_STARTING" -eq 1 ]; then
    return
  fi
  handle_signal "$FIRST_SIGNAL_CODE"
}

handle_signal() {
  local exit_code="$1"
  if [ "$SHUTDOWN_IN_PROGRESS" -eq 1 ]; then
    return
  fi
  SHUTDOWN_IN_PROGRESS=1
  trap '' INT TERM
  stop_active_phase
  exit "$exit_code"
}

trap cleanup EXIT
trap 'dispatch_signal 130' INT
trap 'dispatch_signal 143' TERM

if ! EXPECTED_TMP_PARENT=$(cd "${TMPDIR:-/tmp}" && pwd -P); then
  echo "failed to create benchmark root" >&2
  exit 1
fi
if ! create_bench_root; then
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
  "$BENCH_ROOT/tmp" \
  "$BENCH_ROOT/xdg-config" \
  "$BENCH_ROOT/xdg-data" \
  "$BENCH_ROOT/xdg-state" \
  "$BENCH_ROOT/pyuserbase" \
  "$BENCH_ROOT/pycache" \
  "$BENCH_ROOT/cargo" \
  "$BENCH_ROOT/cargo-target" \
  "$BENCH_ROOT/rustup" \
  "$BENCH_ROOT/uv-cache"; then
  echo "failed to initialize benchmark root" >&2
  exit 1
fi

run_isolated() {
  exec env -i \
    PATH="${PATH:-/usr/bin:/bin}" \
    LANG="${LANG:-}" \
    LC_ALL="${LC_ALL:-}" \
    LC_CTYPE="${LC_CTYPE:-}" \
    HTTP_PROXY="${HTTP_PROXY:-}" \
    HTTPS_PROXY="${HTTPS_PROXY:-}" \
    ALL_PROXY="${ALL_PROXY:-}" \
    NO_PROXY="${NO_PROXY:-}" \
    http_proxy="${http_proxy:-}" \
    https_proxy="${https_proxy:-}" \
    all_proxy="${all_proxy:-}" \
    no_proxy="${no_proxy:-}" \
    SSL_CERT_FILE="${SSL_CERT_FILE:-}" \
    SSL_CERT_DIR="${SSL_CERT_DIR:-}" \
    REQUESTS_CA_BUNDLE="${REQUESTS_CA_BUNDLE:-}" \
    CURL_CA_BUNDLE="${CURL_CA_BUNDLE:-}" \
    PIP_CERT="${PIP_CERT:-}" \
    GIT_SSL_CAINFO="${GIT_SSL_CAINFO:-}" \
    SSH_AUTH_SOCK="${SSH_AUTH_SOCK:-}" \
    HOME="$BENCH_HOME" \
    BRAIN_DIR="$BENCH_BRAIN" \
    AGENT_MEMORY_HUB_BIN="$BENCH_HOME/.local/bin" \
    AGENT_MEMORY_HUB_HOME="$CLONE_DIR" \
    TMPDIR="$BENCH_ROOT/tmp" \
    TMP="$BENCH_ROOT/tmp" \
    TEMP="$BENCH_ROOT/tmp" \
    TEMPDIR="$BENCH_ROOT/tmp" \
    XDG_CONFIG_HOME="$BENCH_ROOT/xdg-config" \
    XDG_CACHE_HOME="$BENCH_ROOT/cache" \
    XDG_DATA_HOME="$BENCH_ROOT/xdg-data" \
    XDG_STATE_HOME="$BENCH_ROOT/xdg-state" \
    PIP_CONFIG_FILE=/dev/null \
    PIP_CACHE_DIR="$BENCH_ROOT/cache/pip" \
    PYTHONUSERBASE="$BENCH_ROOT/pyuserbase" \
    PYTHONPYCACHEPREFIX="$BENCH_ROOT/pycache" \
    CARGO_HOME="$BENCH_ROOT/cargo" \
    CARGO_TARGET_DIR="$BENCH_ROOT/cargo-target" \
    RUSTUP_HOME="$BENCH_ROOT/rustup" \
    UV_CACHE_DIR="$BENCH_ROOT/uv-cache" \
    "$@"
}

start_isolated_phase() {
  local log_file pgid_attempt candidate_pgid current_state benchmark_pgid
  log_file="$1"
  shift
  FIRST_SIGNAL_CODE=0
  PHASE_STARTING=1
  run_isolated \
    "$BASH" \
    -c 'kill -STOP "$$"; exec "$@"' \
    amh-quickstart-phase \
    "$@" > "$log_file" 2>&1 &
  ACTIVE_PID=$!
  ACTIVE_PGID=""
  ACTIVE_BENCHMARK_PGID=""
  ACTIVE_GROUP_ANCHORED=0
  pgid_attempt=0
  while [ "$pgid_attempt" -lt 50 ] && [ -z "$ACTIVE_PGID" ]; do
    candidate_pgid=$(process_group_for_pid "$ACTIVE_PID")
    current_state=$(process_state_for_pid "$ACTIVE_PID")
    benchmark_pgid=$(process_group_for_pid "$$")
    case "$current_state" in
      T*)
        if [ -n "$candidate_pgid" ] \
          && [ -n "$benchmark_pgid" ] \
          && [ "$candidate_pgid" = "$ACTIVE_PID" ] \
          && [ "$candidate_pgid" != "$benchmark_pgid" ] \
          && process_group_exists "$candidate_pgid"; then
          ACTIVE_PGID="$candidate_pgid"
          ACTIVE_BENCHMARK_PGID="$benchmark_pgid"
          ACTIVE_GROUP_ANCHORED=1
        fi
        ;;
    esac
    [ -n "$ACTIVE_PGID" ] || sleep 0.01
    pgid_attempt=$((pgid_attempt + 1))
  done
  if [ -z "$ACTIVE_PGID" ]; then
    kill -KILL "$ACTIVE_PID" 2>/dev/null || true
    wait "$ACTIVE_PID" 2>/dev/null || true
    ACTIVE_PID="" ACTIVE_PGID="" ACTIVE_BENCHMARK_PGID="" ACTIVE_GROUP_ANCHORED=0
    PHASE_STARTING=0
    if [ "$FIRST_SIGNAL_CODE" -ne 0 ]; then
      handle_signal "$FIRST_SIGNAL_CODE"
    fi
    return 1
  fi
  PHASE_STARTING=0
  if [ "$FIRST_SIGNAL_CODE" -ne 0 ]; then
    handle_signal "$FIRST_SIGNAL_CODE"
  fi
  if ! kill -CONT "$ACTIVE_PID" 2>/dev/null; then
    kill -KILL -- "-$ACTIVE_PGID" 2>/dev/null || true
    wait "$ACTIVE_PID" 2>/dev/null || true
    ACTIVE_PID="" ACTIVE_PGID="" ACTIVE_BENCHMARK_PGID="" ACTIVE_GROUP_ANCHORED=0
    return 1
  fi
}

TARGET_SECONDS="${AMH_QUICKSTART_TARGET_SECONDS:-120}"
echo "=== Quickstart benchmark — target < ${TARGET_SECONDS}s ==="
echo "Tmp: $BENCH_ROOT"
echo "Ownership: $OWNERSHIP_TOKEN"
echo "Source: $SOURCE_REPO"
echo ""

START=$(date +%s)

# Phase 1: clone (simulates new user)
PHASE1_START=$(date +%s)
CLONE_LOG="$BENCH_ROOT/clone.log"
if ! start_isolated_phase "$CLONE_LOG" git clone --depth=1 "$SOURCE_REPO" "$CLONE_DIR"; then
  echo "failed to establish isolated process group" >&2
  exit 1
fi
wait "$ACTIVE_PID"
CLONE_STATUS=$? ACTIVE_PID="" ACTIVE_PGID="" ACTIVE_BENCHMARK_PGID="" ACTIVE_GROUP_ANCHORED=0
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
if ! start_isolated_phase "$INSTALL_LOG" "$CLONE_DIR/install.sh" --minimal; then
  echo "failed to establish isolated process group" >&2
  exit 1
fi
wait "$ACTIVE_PID"
INSTALL_STATUS=$? ACTIVE_PID="" ACTIVE_PGID="" ACTIVE_BENCHMARK_PGID="" ACTIVE_GROUP_ANCHORED=0
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
if ! start_isolated_phase \
  "$SEARCH_LOG" \
  "$CLONE_DIR/agent_runtime_kit/tools/search-memory.sh" \
  "anything"; then
  echo "failed to establish isolated process group" >&2
  exit 1
fi
wait "$ACTIVE_PID"
SEARCH_STATUS=$? ACTIVE_PID="" ACTIVE_PGID="" ACTIVE_BENCHMARK_PGID="" ACTIVE_GROUP_ANCHORED=0
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
