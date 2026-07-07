#!/usr/bin/env bash
#
# inject-discipline.sh — Agent SessionStart hook
#
# 协议:
#   stdin:  JSON {"session_id":"...","cwd":"...","hook_event_name":"SessionStart","source":"startup|resume|clear"}
#   stdout: JSON {"hookSpecificOutput": {"hookEventName":"SessionStart", "additionalContext":"..."}}
#           Codex additionally supports top-level suppressOutput=true to keep
#           injected context out of the TUI while still passing it to the model.
#
# 行为:
#   会话开始时把 AGENT_MEMORY_DISCIPLINE.md 注入到 system context，
#   让 LLM 一开始就知道有 brain pool、何时该写、怎么调工具。
#
# 设计权衡:
#   - 只在 SessionStart 注入一次（不像 UserPromptSubmit 每次 prompt 都注入），减少 token 浪费
#   - 同时拉一次"最近 signal"（type=signal --since 3）放在 discipline 后面，让 Agent 看到当前阻塞
#

set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
HUB_CODE_DIR=$(cd "$SCRIPT_DIR/.." && pwd)
DISCIPLINE_FILE="$HUB_CODE_DIR/AGENT_MEMORY_DISCIPLINE.md"
SEARCH_TOOL="$HUB_CODE_DIR/tools/search-memory.sh"
RECORD_TOOL="$HUB_CODE_DIR/tools/record-runtime-event.sh"

INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("session_id",""))' 2>/dev/null || true)
CWD=$(echo "$INPUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("cwd",""))' 2>/dev/null || true)
HOOK_EVENT_NAME=$(echo "$INPUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("hook_event_name","SessionStart"))' 2>/dev/null || echo "SessionStart")
if [ -x "$RECORD_TOOL" ]; then
  "$RECORD_TOOL" \
    --adapter "${AGENT_MEMORY_HUB_ADAPTER:-unknown}" \
    --event "$HOOK_EVENT_NAME" \
    --session "$SESSION_ID" \
    --cwd "$CWD" \
    >/dev/null 2>&1 || true
fi

# Discipline 文件必须存在
[ -r "$DISCIPLINE_FILE" ] || { echo '{}'; exit 0; }

# 读 discipline，把 <HUB> 占位符替换成实际路径
DISCIPLINE=$(sed "s|<HUB>|$HUB_CODE_DIR|g" "$DISCIPLINE_FILE")

# 拉最近 3 天的 signal 类（让 Agent 看到当前阻塞）
# session-end / auto-captured 噪声通过 --exclude-tag 在搜索层直接排除（v0.3.3）
RECENT_SIGNALS=""
if [ -x "$SEARCH_TOOL" ]; then
  RECENT_SIGNALS=$("$SEARCH_TOOL" "" --type signal --since 3 --top-k 5 \
    --exclude-tag "session-end,auto-captured" --format text 2>/dev/null || true)
fi

if [ -n "$RECENT_SIGNALS" ] && ! echo "$RECENT_SIGNALS" | head -1 | grep -q "no matches"; then
  SIGNALS_BLOCK="

## 当前 brain pool 中的活跃 signal（最近 3 天）

$RECENT_SIGNALS

> 这些是上轮会话/其他 Agent 留下的阻塞信号。如果你的当前任务跟这些相关，
> 请考虑是否要先解决/更新这些 signal（解决后可标 sensitivity 或写 episode）。"
else
  SIGNALS_BLOCK=""
fi

FULL_CONTEXT="<agent_brain_discipline>

$DISCIPLINE

$SIGNALS_BLOCK

</agent_brain_discipline>"

if [ "${AGENT_MEMORY_HUB_ADAPTER:-}" = "codex" ] && [ "${AGENT_MEMORY_HUB_CODEX_FULL_SESSION_CONTEXT:-}" != "1" ]; then
  CONTEXT="<agent_brain_discipline>
Agent Memory Hub is active. Follow the agent-memory-hub block already loaded from ~/.codex/AGENTS.md.
Use $HUB_CODE_DIR/tools/search-memory.sh for targeted memory lookup and write memory only for decision/fact/signal/episode/artifact triggers.
</agent_brain_discipline>"
else
  CONTEXT="$FULL_CONTEXT"
fi

# 输出 hook 协议 JSON
echo "$CONTEXT" | python3 -c '
import json, os, sys
context = sys.stdin.read()
output = {
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": context
    }
}
if os.environ.get("AGENT_MEMORY_HUB_ADAPTER") == "codex":
    output["suppressOutput"] = True
print(json.dumps(output, ensure_ascii=False))
'
