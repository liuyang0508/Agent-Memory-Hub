#!/usr/bin/env bash
#
# lifecycle-event.sh — low-noise lifecycle evidence hook
#
# Protocol:
#   stdin: JSON hook payload with session_id/cwd/hook_event_name/transcript_path.
#   stdout: empty. All recoverable failures are fail-open.
#
# Behavior:
#   - Every lifecycle event records bounded runtime evidence.
#   - PreCompact additionally writes one mechanical signal so resume/harvest can
#     find the compression boundary.
#   - PostCompact/SubagentStart/SubagentStop do not recall context or write long
#     memory items.

set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
HUB_CODE_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
WRITE_TOOL="$HUB_CODE_DIR/tools/write-memory.sh"
RECORD_TOOL="$HUB_CODE_DIR/tools/record-runtime-event.sh"

INPUT=$(cat)

json_field() {
  local field="$1"
  echo "$INPUT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('$field',''))" 2>/dev/null || true
}

SESSION_ID_FULL=$(json_field "session_id")
[ -z "$SESSION_ID_FULL" ] && SESSION_ID_FULL="unknown"
SESSION_ID=$(printf '%s' "$SESSION_ID_FULL" | head -c 8)
CWD=$(json_field "cwd")
TRANSCRIPT=$(json_field "transcript_path")
HOOK_EVENT_NAME=$(json_field "hook_event_name")
[ -z "$HOOK_EVENT_NAME" ] && HOOK_EVENT_NAME="Lifecycle"

if [ -x "$RECORD_TOOL" ]; then
  "$RECORD_TOOL" \
    --adapter "${AGENT_MEMORY_HUB_ADAPTER:-unknown}" \
    --event "$HOOK_EVENT_NAME" \
    --session "$SESSION_ID_FULL" \
    --cwd "$CWD" \
    >/dev/null 2>&1 || true
fi

if [ "$HOOK_EVENT_NAME" != "PreCompact" ]; then
  exit 0
fi

[ -x "$WRITE_TOOL" ] || exit 0

BRAIN_DIR="${BRAIN_DIR:-$HOME/.agent-memory-hub}"
FLAGS_DIR="$BRAIN_DIR/.session-flags"
mkdir -p "$FLAGS_DIR" 2>/dev/null || true
FLAG="$FLAGS_DIR/precompact-$SESSION_ID_FULL"

if [ -f "$FLAG" ]; then
  exit 0
fi
touch "$FLAG" 2>/dev/null || true

NOW=$(date +"%Y-%m-%d %H:%M")

printf '%s\n' \
  "**当前状态**：会话 \`${SESSION_ID}\` 在 ${NOW} 触发 \`PreCompact\`，即将发生上下文压缩。" \
  "" \
  "**影响**：压缩边界之前的对话细节可能在后续上下文中丢失；本 signal 只记录机械断点，不声称已经总结过 transcript。" \
  "" \
  "**期望操作**：如果后续需要恢复压缩前细节，优先读取 transcript 或运行 \`memory harvest\` / \`/remember\` 做人工筛选归档；不要把本 signal 当作完整 handoff。" \
  "" \
  "**transcript**：\`${TRANSCRIPT}\`" \
  "**会话 cwd**：\`${CWD}\`" | "$WRITE_TOOL" \
    --type signal \
    --title "Session ${SESSION_ID} compact boundary ${NOW}" \
    --summary "PreCompact boundary observed; transcript may need harvest or /remember for detailed handoff." \
    --tags "precompact,compact-boundary,lifecycle,auto-captured,session-${SESSION_ID}" \
    --agent "${AGENT_MEMORY_HUB_ADAPTER:-unknown}" \
    --session "$SESSION_ID" \
    --cwd "$CWD" \
    --adapter "${AGENT_MEMORY_HUB_ADAPTER:-unknown}" \
    --sensitivity internal >/dev/null 2>&1 || true

exit 0
