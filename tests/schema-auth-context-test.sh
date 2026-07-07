#!/usr/bin/env bash
#
# tests/schema-auth-context-test.sh
#
# 验证 write-memory.sh 写出的 frontmatter 含 auth_context 字段（默认 null）。
# L3 多角色 RBAC 接口预留，配合 tenant_id 用。
#

set -uo pipefail

TMPDIR=$(mktemp -d -t auth-test-XXXXXX)
trap "rm -rf $TMPDIR" EXIT

WRITE_TOOL="$(cd "$(dirname "$0")/.." && pwd)/agent_runtime_kit/tools/write-memory.sh"

PASS=0
FAIL=0

# Test 1: default behavior — auth_context should be null
echo "Test 1: default auth_context = null"
echo "test body" | BRAIN_DIR="$TMPDIR" "$WRITE_TOOL" \
  --type fact --title "auth-test-1" --summary "default auth" \
  --agent test --sensitivity internal > /dev/null
FILE=$(ls "$TMPDIR/items/" 2>/dev/null | head -1)
if [ -z "$FILE" ]; then
  echo "  ✗ FAIL — no item file written"; FAIL=$((FAIL+1))
elif grep -q '^auth_context: null$' "$TMPDIR/items/$FILE"; then
  echo "  ✓ PASS"; PASS=$((PASS+1))
else
  echo "  ✗ FAIL — auth_context field not 'null' in frontmatter"
  grep -E 'auth_context|tenant_id|^id:' "$TMPDIR/items/$FILE" | head -5
  FAIL=$((FAIL+1))
fi

# Test 2: explicit --auth-context
rm -rf "$TMPDIR/items"
echo ""
echo "Test 2: explicit --auth-context user=alice,role=admin"
echo "test body" | BRAIN_DIR="$TMPDIR" "$WRITE_TOOL" \
  --type fact --title "auth-test-2" --summary "auth set" \
  --agent test --auth-context "user=alice,role=admin" --sensitivity internal > /dev/null
FILE=$(ls "$TMPDIR/items/" 2>/dev/null | head -1)
if [ -z "$FILE" ]; then
  echo "  ✗ FAIL — no item file written"; FAIL=$((FAIL+1))
elif grep -q '^auth_context: user=alice,role=admin$' "$TMPDIR/items/$FILE"; then
  echo "  ✓ PASS"; PASS=$((PASS+1))
else
  echo "  ✗ FAIL — auth_context not 'user=alice,role=admin'"
  grep -E 'auth_context|^id:' "$TMPDIR/items/$FILE" | head -5
  FAIL=$((FAIL+1))
fi

# Test 3: tenant_id + auth_context 共存
rm -rf "$TMPDIR/items"
echo ""
echo "Test 3: tenant_id + auth_context 共存"
echo "test body" | BRAIN_DIR="$TMPDIR" "$WRITE_TOOL" \
  --type fact --title "auth-test-3" --summary "both fields" \
  --agent test --tenant-id "acme-corp" --auth-context "user=alice" --sensitivity internal > /dev/null
FILE=$(ls "$TMPDIR/items/" 2>/dev/null | head -1)
if [ -z "$FILE" ]; then
  echo "  ✗ FAIL — no item file written"; FAIL=$((FAIL+1))
elif grep -q '^tenant_id: acme-corp$' "$TMPDIR/items/$FILE" && \
     grep -q '^auth_context: user=alice$' "$TMPDIR/items/$FILE"; then
  echo "  ✓ PASS"; PASS=$((PASS+1))
else
  echo "  ✗ FAIL — 两字段不全"
  grep -E 'tenant_id|auth_context|^id:' "$TMPDIR/items/$FILE" | head -5
  FAIL=$((FAIL+1))
fi

echo ""
echo "Result: $PASS passed / $FAIL failed"
[ "$FAIL" -eq 0 ]
