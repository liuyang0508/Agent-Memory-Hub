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
PENDING_SIGNAL_CODE=0
SHUTDOWN_IN_PROGRESS=0

owned_root_is_valid() {
  local parent base marker marker_content
  [ "$BENCH_ROOT_OWNED" -eq 1 ] || return 1
  [ -n "$BENCH_ROOT" ] || return 1
  [ -d "$BENCH_ROOT" ] || return 1
  [ ! -L "$BENCH_ROOT" ] || return 1
  parent=$(cd "$(dirname "$BENCH_ROOT")" && pwd -P) || return 1
  [ "$parent" = "$EXPECTED_TMP_PARENT" ] || return 1
  base=$(basename "$BENCH_ROOT")
  case "$base" in amh-bench-*) ;; *) return 1 ;; esac
  marker="$BENCH_ROOT/$OWNERSHIP_MARKER_NAME"
  [ -f "$marker" ] || return 1
  [ ! -L "$marker" ] || return 1
  marker_content=$(<"$marker") || return 1
  [ "$marker_content" = "$OWNERSHIP_TOKEN" ]
}

cleanup() {
  local owned_root
  [ "$KEEP" -eq 0 ] || return 0
  owned_root_is_valid || return 0
  owned_root="$BENCH_ROOT"
  BENCH_ROOT_OWNED=0
  BENCH_ROOT=""
  rm -rf -- "$owned_root"
}

create_bench_root() {
  local candidate parent base marker
  candidate=$(mktemp -d "$EXPECTED_TMP_PARENT/amh-bench-XXXXXX") || return 1
  [ -n "$candidate" ] || return 1
  [ -d "$candidate" ] || return 1
  [ ! -L "$candidate" ] || return 1
  parent=$(cd "$(dirname "$candidate")" && pwd -P) || return 1
  [ "$parent" = "$EXPECTED_TMP_PARENT" ] || return 1
  base=$(basename "$candidate")
  case "$base" in amh-bench-*) ;; *) return 1 ;; esac
  marker="$candidate/$OWNERSHIP_MARKER_NAME"
  (set -C; printf '%s\n' "$OWNERSHIP_TOKEN" > "$marker") || return 1
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

stop_active_phase() {
  local phase_pid phase_pgid use_group current_pgid benchmark_pgid grace_step
  phase_pid="$ACTIVE_PID"
  phase_pgid="$ACTIVE_PGID"
  ACTIVE_PID="" ACTIVE_PGID=""

  if [ -z "$phase_pid" ]; then
    return
  fi

  use_group=0
  current_pgid=$(process_group_for_pid "$phase_pid")
  benchmark_pgid=$(process_group_for_pid "$$")
  if [ -n "$current_pgid" ] \
    && [ "$current_pgid" = "$phase_pgid" ] \
    && [ "$current_pgid" != "$benchmark_pgid" ] \
    && process_group_exists "$current_pgid"; then
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

defer_signal() {
  if [ "$PENDING_SIGNAL_CODE" -eq 0 ]; then
    PENDING_SIGNAL_CODE="$1"
  fi
  trap '' INT TERM
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
trap 'handle_signal 130' INT
trap 'handle_signal 143' TERM

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
  env -i \
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
  local log_file pgid_attempt pending_code
  log_file="$1"
  shift
  PENDING_SIGNAL_CODE=0
  trap 'defer_signal 130' INT
  trap 'defer_signal 143' TERM
  run_isolated "$@" > "$log_file" 2>&1 &
  ACTIVE_PID=$!
  ACTIVE_PGID=""
  pgid_attempt=0
  while [ "$pgid_attempt" -lt 20 ] && [ -z "$ACTIVE_PGID" ]; do
    ACTIVE_PGID=$(process_group_for_pid "$ACTIVE_PID")
    [ -n "$ACTIVE_PGID" ] || sleep 0.01
    pgid_attempt=$((pgid_attempt + 1))
  done
  trap 'handle_signal 130' INT
  trap 'handle_signal 143' TERM
  pending_code="$PENDING_SIGNAL_CODE"
  PENDING_SIGNAL_CODE=0
  if [ "$pending_code" -ne 0 ]; then
    handle_signal "$pending_code"
  fi
}

TARGET_SECONDS="${AMH_QUICKSTART_TARGET_SECONDS:-120}"
echo "=== Quickstart benchmark — target < ${TARGET_SECONDS}s ==="
echo "Tmp: $BENCH_ROOT"
echo "Source: $SOURCE_REPO"
echo ""

START=$(date +%s)

# Phase 1: clone (simulates new user)
PHASE1_START=$(date +%s)
CLONE_LOG="$BENCH_ROOT/clone.log"
start_isolated_phase "$CLONE_LOG" git clone --depth=1 "$SOURCE_REPO" "$CLONE_DIR"
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
start_isolated_phase "$INSTALL_LOG" "$CLONE_DIR/install.sh" --minimal
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
start_isolated_phase \
  "$SEARCH_LOG" \
  "$CLONE_DIR/agent_runtime_kit/tools/search-memory.sh" \
  "anything"
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
