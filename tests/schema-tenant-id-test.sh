#!/usr/bin/env bash
#
# tests/schema-tenant-id-test.sh
#
# 验证 write-memory.sh 能写出 tenant_id 字段。
#

set -uo pipefail

TMPDIR=$(mktemp -d -t schema-test-XXXXXX)
trap "rm -rf $TMPDIR" EXIT

WRITE_TOOL="$(cd "$(dirname "$0")/.." && pwd)/agent_runtime_kit/tools/write-memory.sh"

PASS=0
FAIL=0

# Test 1: default behavior — null tenant_id is omitted from compact frontmatter.
echo "Test 1: default tenant_id omitted"
echo "test body" | BRAIN_DIR="$TMPDIR" "$WRITE_TOOL" \
  --type fact --title "schema-test-1" --summary "default tenant" \
  --agent test --sensitivity internal > /dev/null
FILE=$(ls "$TMPDIR/items/" 2>/dev/null | head -1)
if [ -z "$FILE" ]; then
  echo "  ✗ FAIL — no item file written"
  FAIL=$((FAIL+1))
elif ! grep -q '^tenant_id:' "$TMPDIR/items/$FILE"; then
  echo "  ✓ PASS"
  PASS=$((PASS+1))
else
  echo "  ✗ FAIL — tenant_id should be omitted when unset"
  echo "  ---"
  grep -E 'tenant_id|^id:|^type:|^---$' "$TMPDIR/items/$FILE" | head -10
  echo "  ---"
  FAIL=$((FAIL+1))
fi

# Test 2: explicit --tenant-id
rm -rf "$TMPDIR/items"
echo ""
echo "Test 2: explicit --tenant-id acme-corp"
echo "test body" | BRAIN_DIR="$TMPDIR" "$WRITE_TOOL" \
  --type fact --title "schema-test-2" --summary "tenant set" \
  --agent test --tenant-id "acme-corp" --sensitivity internal > /dev/null
FILE=$(ls "$TMPDIR/items/" 2>/dev/null | head -1)
if [ -z "$FILE" ]; then
  echo "  ✗ FAIL — no item file written"
  FAIL=$((FAIL+1))
elif grep -q '^tenant_id: acme-corp$' "$TMPDIR/items/$FILE"; then
  echo "  ✓ PASS"
  PASS=$((PASS+1))
else
  echo "  ✗ FAIL — tenant_id not 'acme-corp'"
  grep -E 'tenant_id|^id:' "$TMPDIR/items/$FILE" | head -5
  FAIL=$((FAIL+1))
fi

echo ""
echo "Result: $PASS passed / $FAIL failed"
[ "$FAIL" -eq 0 ]
