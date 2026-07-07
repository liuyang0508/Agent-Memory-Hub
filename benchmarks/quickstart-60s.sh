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

KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1

TMPDIR=$(mktemp -d -t amh-bench-XXXXXX)
SOURCE_REPO=$(cd "$(dirname "$0")/.." && pwd)
BENCH_BRAIN="$TMPDIR/.brain"

# Backup user's /remember command if exists (avoid pollution)
REMEMBER_MD="$HOME/.claude/commands/remember.md"
REMEMBER_BACKUP=""
if [ -f "$REMEMBER_MD" ]; then
  REMEMBER_BACKUP="${REMEMBER_MD}.benchmark-backup-$$"
  cp "$REMEMBER_MD" "$REMEMBER_BACKUP"
fi

cleanup() {
  if [ "$KEEP" -eq 0 ]; then rm -rf "$TMPDIR"; fi
  if [ -n "$REMEMBER_BACKUP" ] && [ -f "$REMEMBER_BACKUP" ]; then
    mv "$REMEMBER_BACKUP" "$REMEMBER_MD"
  fi
}
trap cleanup EXIT

TARGET_SECONDS="${AMH_QUICKSTART_TARGET_SECONDS:-120}"
echo "=== Quickstart benchmark — target < ${TARGET_SECONDS}s ==="
echo "Tmp: $TMPDIR"
echo "Source: $SOURCE_REPO"
echo ""

START=$(date +%s)

# Phase 1: clone (simulates new user)
PHASE1_START=$(date +%s)
git clone --depth=1 "$SOURCE_REPO" "$TMPDIR/agent-memory-hub" 2>&1 | tail -1
PHASE1=$(($(date +%s) - PHASE1_START))
echo "  ✓ clone: ${PHASE1}s"

# Phase 2: minimal install keeps the quickstart target focused on first-use setup.
PHASE2_START=$(date +%s)
cd "$TMPDIR/agent-memory-hub"
BRAIN_DIR="$BENCH_BRAIN" ./install.sh --minimal > "$TMPDIR/install.log" 2>&1 || {
  echo "  ✗ install.sh failed:"
  cat "$TMPDIR/install.log"
  exit 1
}
PHASE2=$(($(date +%s) - PHASE2_START))
echo "  ✓ install.sh: ${PHASE2}s"

# Phase 3: first query (search-memory.sh against empty brain)
PHASE3_START=$(date +%s)
BRAIN_DIR="$BENCH_BRAIN" ./agent_runtime_kit/tools/search-memory.sh "anything" 2>&1 | head -3
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
