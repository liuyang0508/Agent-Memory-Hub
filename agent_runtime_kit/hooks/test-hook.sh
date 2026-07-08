#!/usr/bin/env bash
# test-hook.sh — 单元测试 inject-context.sh 的协议正确性

set -uo pipefail

HOOK="$(cd "$(dirname "$0")" && pwd)/inject-context.sh"
TOOLS_DIR="$(cd "$(dirname "$0")/../tools" && pwd)"
PASS=0
FAIL=0
TEST_BRAIN=$(mktemp -d)
export BRAIN_DIR="$TEST_BRAIN"
export MEMORY_HUB_TEST_EMBEDDING=1
export MEMORY_HUB_EMBEDDING_OFFLINE=1
export AGENT_MEMORY_HUB_SEARCH_TIMEOUT_SECONDS="${AGENT_MEMORY_HUB_SEARCH_TIMEOUT_SECONDS:-10}"
export AGENT_MEMORY_HUB_ADAPTER="hook-test"
trap 'rm -rf "$TEST_BRAIN"' EXIT

# shellcheck source=/dev/null
source "$TOOLS_DIR/_resolve-python.sh"

"$MEMORY_PYTHON" - <<'PY'
from datetime import datetime, timezone
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs
from agent_brain.memory.store.items_store import ItemsStore

brain = Path(__import__("os").environ["BRAIN_DIR"])
store = ItemsStore(brain / "items")
fixtures = [
    (
        "mem-20260707-000001-hook-test-csv",
        "CSV export workflow",
        "csv export work should be recalled by hook tests",
        ["csv", "export", "hook-test"],
        "继续 csv 导出工作 needs this memory.",
    ),
    (
        "mem-20260707-000002-hook-test-cua",
        "CUA beta header research",
        "cua beta header research should be recalled by hook tests",
        ["cua", "beta", "header", "hook-test"],
        "调研 cua beta header needs this memory.",
    ),
    (
        "mem-20260707-000003-hook-test-weather-cli",
        "weather-cli csv encoding issue",
        "weather-cli csv encoding issue should be recalled by hook tests",
        ["weather-cli", "csv", "encoding", "hook-test"],
        "weather-cli 的 csv 编码问题 needs this memory.",
    ),
]
for item_id, title, summary, tags, body in fixtures:
    store.write(
        MemoryItem(
            id=item_id,
            type=MemoryType.fact,
            created_at=datetime.now(timezone.utc),
            title=title,
            summary=summary,
            tags=tags,
            refs=Refs(files=["agent_runtime_kit/hooks/test-hook.sh"]),
        ),
        body,
    )
PY

memory_cli reindex >/dev/null

run_test() {
  local name="$1"
  local prompt="$2"
  local expect="$3"  # "match" | "empty"
  
  local out
  out=$(echo "{\"prompt\":\"$prompt\"}" | "$HOOK" 2>/dev/null)
  
  case "$expect" in
    match)
      if echo "$out" | grep -q "additionalContext"; then
        echo "✅ PASS: $name"
        PASS=$((PASS+1))
      else
        echo "❌ FAIL: $name (expected match, got: $out)"
        FAIL=$((FAIL+1))
      fi
      ;;
    empty)
      if [ "$out" = "{}" ]; then
        echo "✅ PASS: $name"
        PASS=$((PASS+1))
      else
        echo "❌ FAIL: $name (expected {}, got: $out)"
        FAIL=$((FAIL+1))
      fi
      ;;
  esac
}

echo "=== Hook 协议单元测试 ==="
echo ""
run_test "已知 query 'csv'" "继续 csv 导出工作" "match"
run_test "已知 query 'cua'" "调研 cua beta header" "match"
run_test "无关 query 'pasta'" "how do I cook pasta" "empty"
run_test "空 prompt" "" "empty"
run_test "全停用词 prompt" "the a an is are" "empty"
run_test "中英文混合 query" "weather-cli 的 csv 编码问题" "match"

echo ""
echo "=== 结果: $PASS passed / $FAIL failed ==="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
