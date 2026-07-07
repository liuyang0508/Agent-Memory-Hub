#!/usr/bin/env bash
#
# tests/playbook-schema-test.sh
#
# 验证 playbook namespace 端到端：
# - 3 个 PoC item frontmatter 符合 schema
# - list-playbook.sh 各种 filter 正确
# - id 前缀必须是 play-
#

set -uo pipefail

PLAYBOOK="$HOME/.agent-memory-hub/playbook"
LIST_TOOL="$(cd "$(dirname "$0")/.." && pwd)/agent_runtime_kit/tools/list-playbook.sh"

PASS=0
FAIL=0

# Test 1: schema validation - all 3 items have required fields
echo "Test 1: 3 PoC items 必填 frontmatter 字段齐全"
REQUIRED_FIELDS="^id:|^schema_version:|^kind:|^created_at:|^agent:|^target_agents:|^title:|^summary:"
ALL_OK=1
for f in "$PLAYBOOK/disciplines/agent-memory.md" "$PLAYBOOK/skills/write-good-decision.md" "$PLAYBOOK/hooks/session-start-discipline-injector.md"; do
  if [ ! -f "$f" ]; then
    echo "  ✗ FAIL — file missing: $f"; ALL_OK=0; continue
  fi
  COUNT=$(grep -cE "$REQUIRED_FIELDS" "$f")
  if [ "$COUNT" -ge 8 ]; then
    echo "  ✓ $(basename $f): $COUNT required fields"
  else
    echo "  ✗ FAIL — $(basename $f): only $COUNT required fields (need ≥8)"
    ALL_OK=0
  fi
done
if [ "$ALL_OK" -eq 1 ]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi

# Test 2: id prefix must be play-
echo ""
echo "Test 2: 所有 playbook id 必须以 'play-' 开头"
ALL_OK=1
for f in $(find "$PLAYBOOK" -mindepth 2 -name "*.md"); do
  ID=$(grep '^id:' "$f" | sed 's/id: *//')
  if [[ "$ID" != play-* ]]; then
    echo "  ✗ FAIL — $(basename $f): id='$ID' (must start with play-)"
    ALL_OK=0
  fi
done
if [ "$ALL_OK" -eq 1 ]; then echo "  ✓ all play- prefix"; PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi

# Test 3: list-playbook.sh no filter returns 3 items
echo ""
echo "Test 3: list-playbook.sh 不带 filter 返回 3 条"
N=$("$LIST_TOOL" 2>&1 | grep -c "^─── play-" || echo 0)
if [ "$N" -eq 3 ]; then
  echo "  ✓ 3 items"; PASS=$((PASS+1))
else
  echo "  ✗ FAIL — got $N items, want 3"; FAIL=$((FAIL+1))
fi

# Test 4: --kind skill returns 1
echo ""
echo "Test 4: --kind skill 过滤"
N=$("$LIST_TOOL" --kind skill 2>&1 | grep -c "^─── play-" || echo 0)
if [ "$N" -eq 1 ]; then
  echo "  ✓ 1 skill"; PASS=$((PASS+1))
else
  echo "  ✗ FAIL — got $N, want 1"; FAIL=$((FAIL+1))
fi

# Test 5: --target-agent codex 应命中 codex 显式 + any 通配（共 3 条）
echo ""
echo "Test 5: --target-agent codex（命中 codex 或 any）"
N=$("$LIST_TOOL" --target-agent codex 2>&1 | grep -c "^─── play-" || echo 0)
if [ "$N" -eq 3 ]; then
  echo "  ✓ 3 items (1 codex 显式 + 2 any 通配)"; PASS=$((PASS+1))
else
  echo "  ✗ FAIL — got $N, want 3"; FAIL=$((FAIL+1))
fi

# Test 6: --kind 非法值应报错
echo ""
echo "Test 6: --kind invalid 应报 unknown"
OUT=$("$LIST_TOOL" --kind invalid 2>&1 || true)
if echo "$OUT" | grep -q "Error.*kind"; then
  echo "  ✓ rejected invalid kind"; PASS=$((PASS+1))
else
  echo "  ✗ FAIL — should reject 'invalid' kind"
  echo "    output: $OUT"
  FAIL=$((FAIL+1))
fi

echo ""
echo "Result: $PASS passed / $FAIL failed"
[ "$FAIL" -eq 0 ]
