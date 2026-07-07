#!/usr/bin/env bash
#
# session-end-signal.sh — Agent Stop hook
#
# 协议:
#   stdin:  JSON {"session_id":"...","transcript_path":"...","cwd":"...","hook_event_name":"Stop","stop_hook_active":false}
#   stdout: 空（exit 0 即可，正常完成）
#
# 历史 / 真实语义:
#   部分 Agent 的 Stop hook 在**每个 LLM turn 结束**时触发，不是会话生命周期结束。
#   一个 session 一晚上来回 50 个 turn = Stop fire 50 次。
#
#   v0.3.5 起加 session_id 去重：每个 session_id 只在第一次 turn 结束时
#   写 1 条 signal，后续 turn 结束直接 exit 0。这把 brain pool noise 占比
#   从 ~50% 降到接近 0。
#
# 行为:
#   - 第一次 turn 结束：创建 flag + 写一条 type=signal 标记 session 活跃
#   - 后续 turn 结束：flag 存在 → 啥都不做
#
# 设计权衡:
#   - **不**调用 LLM API（避免外部依赖、API 成本）
#   - **不**读完整 transcript（隐私 + 体积）
#   - 只写"session 第一次活跃"机械事实 → 让用户主动 /remember
#
# 装配（可选）:
#   jq '. + {"hooks": {"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "<path-to>/session-end-signal.sh"}]}]}}' \
#     ~/.claude/settings.json > /tmp/s.json && mv /tmp/s.json ~/.claude/settings.json

set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
HUB_CODE_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
WRITE_TOOL="$HUB_CODE_DIR/tools/write-memory.sh"
RECORD_TOOL="$HUB_CODE_DIR/tools/record-runtime-event.sh"
PYTHON_RESOLVER="$HUB_CODE_DIR/tools/_resolve-python.sh"

INPUT=$(cat)
SESSION_ID_FULL=$(echo "$INPUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("session_id","unknown"))' 2>/dev/null || echo "unknown")
SESSION_ID=$(echo "$SESSION_ID_FULL" | head -c 8)
TRANSCRIPT=$(echo "$INPUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("transcript_path",""))' 2>/dev/null || echo "")
CWD=$(echo "$INPUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("cwd",""))' 2>/dev/null || echo "")
HOOK_EVENT_NAME=$(echo "$INPUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("hook_event_name","Stop"))' 2>/dev/null || echo "Stop")

if [ -x "$RECORD_TOOL" ]; then
  "$RECORD_TOOL" \
    --adapter "${AGENT_MEMORY_HUB_ADAPTER:-unknown}" \
    --event "$HOOK_EVENT_NAME" \
    --session "$SESSION_ID_FULL" \
    --cwd "$CWD" \
    >/dev/null 2>&1 || true
fi

if [ -n "$TRANSCRIPT" ] && [ -f "$PYTHON_RESOLVER" ]; then
  # shellcheck source=/dev/null
  source "$PYTHON_RESOLVER"
  printf '%s' "$INPUT" | "$MEMORY_PYTHON" -m agent_brain.memory.evidence.hook_capture transcript \
    >/dev/null 2>&1 || true
fi

[ -x "$WRITE_TOOL" ] || exit 0

# === DEDUPE: 同一 session_id 只在第一次 turn end 时写 signal ===
BRAIN_DIR="${BRAIN_DIR:-$HOME/.agent-memory-hub}"
FLAGS_DIR="$BRAIN_DIR/.session-flags"
mkdir -p "$FLAGS_DIR" 2>/dev/null
FLAG="$FLAGS_DIR/$SESSION_ID_FULL"

if [ -f "$FLAG" ]; then
    # 已写过此 session 的 signal，本次 turn end 不再重复
    exit 0
fi

# 第一次：标记 flag + 写 signal
touch "$FLAG"

NOW=$(date +"%Y-%m-%d %H:%M")
SOURCE_AGENT="${AGENT_MEMORY_HUB_AGENT:-${AGENT_MEMORY_HUB_ADAPTER:-unknown}}"
case "$SOURCE_AGENT" in
  claude_code) SOURCE_AGENT="claude-code" ;;
esac

echo "**当前状态**：会话 \`${SESSION_ID}\` 在 ${NOW} 第一次 turn 结束（session 已活跃）。

**影响**：本次会话的产出（决策 / 事实 / 阻塞 / 经历）尚未归档到 brain pool。Stop hook 可能是 turn-level，本 signal 在 session 第一次 turn 结束时写一次，后续 turn 结束不再重复。

**期望操作**：此 transcript 已进入 harvest 工作队列，归档有两条路径：
1. 自动（离线）：\`memory harvest\` 机械抽取本 transcript 的 decision/episode 写入 brain pool；\`memory harvest --enrich\` 在有模型时把 raw 升级为 distilled
2. 手动（精挑）：在新会话敲 \`/remember\` 让 LLM 扫描历史并归档值得记的内容
3. 归档后可删除本 signal（或等 GC 自动清理）

**transcript**：\`${TRANSCRIPT}\`
**会话 cwd**：\`${CWD}\`" | "$WRITE_TOOL" \
    --type signal \
    --title "Session ${SESSION_ID} active ${NOW}" \
    --summary "transcript pending harvest（session 第一次 turn 结束，已 dedupe，每 session 只写一次）；可 \`memory harvest\` 离线机械归档或 /remember 精挑" \
    --tags "session-active,harvest-queue,auto-captured,session-${SESSION_ID}" \
    --agent "$SOURCE_AGENT" \
    --session "$SESSION_ID" \
    --cwd "$CWD" \
    --adapter "${AGENT_MEMORY_HUB_ADAPTER:-unknown}" \
    --sensitivity internal > /dev/null 2>&1 || true

exit 0
