#!/usr/bin/env bash
#
# inject-context.sh — Agent UserPromptSubmit hook
#
# 协议:
#   stdin:  JSON {"prompt":"...", "session_id":"...", ...}
#   stdout: JSON {"hookSpecificOutput": {"hookEventName":"UserPromptSubmit", "additionalContext":"..."}}
#

set -euo pipefail

# 工具位置（hook 所在 project 的 tools/，独立于数据位置）
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
HUB_CODE_DIR=$(cd "$SCRIPT_DIR/.." && pwd)

# 数据位置：用户级（可用 env BRAIN_DIR 覆盖）
BRAIN_DIR="${BRAIN_DIR:-$HOME/.agent-memory-hub}"
SEARCH_TOOL="$HUB_CODE_DIR/tools/search-memory.sh"
RECORD_TOOL="$HUB_CODE_DIR/tools/record-runtime-event.sh"
PYTHON_RESOLVER="$HUB_CODE_DIR/tools/_resolve-python.sh"
SEARCH_TIMEOUT_SECONDS="${AGENT_MEMORY_HUB_SEARCH_TIMEOUT_SECONDS:-2}"
if [ "${MEMORY_HUB_TEST_EMBEDDING:-}" = "1" ] && [ -z "${AGENT_MEMORY_HUB_SEARCH_TIMEOUT_SECONDS:-}" ]; then
  # CI runners can spend more than 2s on a cold Python CLI startup; keep the
  # production hook budget unchanged while making injection assertions stable.
  SEARCH_TIMEOUT_SECONDS=5
fi

# 优先注入的 type 顺序（v0.3.3 reranking）—— decision/signal 是行动需要的硬上下文，
# fact/episode 提供背景；artifact 在最后（多是产出汇总，相关度通常低于其他类型）
PREFER_TYPES="decision,signal,fact,episode,handoff,artifact"

[ -x "$SEARCH_TOOL" ] || { echo '{}'; exit 0; }

record_hook_latency() {
  local stage="$1"
  local status="$2"
  local detail="${3:-}"
  [ -n "${MEMORY_PYTHON:-}" ] || return 0
  "$MEMORY_PYTHON" - "$BRAIN_DIR" "$stage" "$status" "$detail" \
    "${AGENT_MEMORY_HUB_ADAPTER:-unknown}" "${SESSION_ID:-}" "${CWD:-}" \
    "${HOOK_EVENT_NAME:-UserPromptSubmit}" "$SEARCH_TIMEOUT_SECONDS" <<'PY' >/dev/null 2>&1 || true
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

brain_dir, stage, status, detail, adapter, session_id, cwd, event_name, timeout_seconds = sys.argv[1:10]
runtime_dir = Path(brain_dir) / "runtime"
runtime_dir.mkdir(parents=True, exist_ok=True)
row = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "source": "hook",
    "adapter": adapter,
    "session_id": session_id or None,
    "cwd": cwd or None,
    "event_name": event_name,
    "stage": stage,
    "status": status,
    "detail": detail,
    "timeout_seconds": float(timeout_seconds),
}
with (runtime_dir / "hook-latency.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
PY
}

emit_hook_context() {
  local context="$1"
  case "${AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT:-json}" in
    plain|text)
      printf '%s\n' "$context"
      ;;
    *)
      printf '%s' "$context" | python3 -c '
import json, sys
context = sys.stdin.read()
output = {
    "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": context
    }
}
print(json.dumps(output, ensure_ascii=False))
'
      ;;
  esac
}

emit_empty_trace_context() {
  local reason="$1"
  local keywords="${2:-}"
  local detail="${3:-}"
  [ "${AGENT_MEMORY_HUB_HOOK_TRACE_EMPTY:-}" = "1" ] || return 1
  local context
  context=$(python3 - "$reason" "$keywords" "$detail" <<'PY'
import sys

reason, keywords, detail = sys.argv[1:4]
lines = [
    "<agent_brain_diagnostics>",
    "**AMH hook trace**",
    "hook: triggered",
    "decision: no_injection",
    f"reason: {reason or '-'}",
    f"keywords: {keywords or '-'}",
]
if detail:
    lines.append(f"detail: {detail}")
lines.extend([
    "next: memory hook recent --limit 5",
    "</agent_brain_diagnostics>",
])
sys.stdout.write("\n".join(lines))
PY
)
  emit_hook_context "$context"
  return 0
}

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("prompt",""))' 2>/dev/null || true)
SESSION_ID=$(echo "$INPUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("session_id",""))' 2>/dev/null || true)
CWD=$(echo "$INPUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("cwd",""))' 2>/dev/null || true)
HOOK_EVENT_NAME=$(echo "$INPUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("hook_event_name","UserPromptSubmit"))' 2>/dev/null || echo "UserPromptSubmit")
if [ -x "$RECORD_TOOL" ]; then
  "$RECORD_TOOL" \
    --adapter "${AGENT_MEMORY_HUB_ADAPTER:-unknown}" \
    --event "$HOOK_EVENT_NAME" \
    --session "$SESSION_ID" \
    --cwd "$CWD" \
    >/dev/null 2>&1 || true
fi

if [ -n "$PROMPT" ] && [ -f "$PYTHON_RESOLVER" ]; then
  # shellcheck source=/dev/null
  source "$PYTHON_RESOLVER"
  printf '%s' "$INPUT" | "$MEMORY_PYTHON" -m agent_brain.memory.evidence.hook_capture prompt \
    >/dev/null 2>&1 || true
fi
RECALL_PROMPT="$PROMPT"
if [ -n "$PROMPT" ] && [ -n "${MEMORY_PYTHON:-}" ]; then
  NORMALIZED_PROMPT=$(printf '%s' "$PROMPT" | "$MEMORY_PYTHON" -m agent_brain.memory.context.prompt_normalization 2>/dev/null || true)
  if [ -n "$NORMALIZED_PROMPT" ]; then
    RECALL_PROMPT="$NORMALIZED_PROMPT"
  fi
  MULTIMODAL_RECALL_TEXT=$(printf '%s' "$INPUT" | "$MEMORY_PYTHON" -m agent_brain.memory.evidence.multimodal_capture recall-text 2>/dev/null || true)
  if [ -n "$MULTIMODAL_RECALL_TEXT" ]; then
    if [ -n "$RECALL_PROMPT" ]; then
      RECALL_PROMPT="${RECALL_PROMPT}"$'\n'"${MULTIMODAL_RECALL_TEXT}"
    else
      RECALL_PROMPT="$MULTIMODAL_RECALL_TEXT"
    fi
  fi
fi
[ -z "$RECALL_PROMPT" ] && { echo '{}'; exit 0; }

# 动态 K — 根据 prompt 长度调整注入条数（短 prompt 不需要太多上下文）
PROMPT_LEN=${#RECALL_PROMPT}
if [ "$PROMPT_LEN" -lt 30 ]; then
  TOP_K=2
elif [ "$PROMPT_LEN" -lt 200 ]; then
  TOP_K=3
else
  TOP_K=5
fi

# Query signal gate. Weak connective prompts such as "就像" should fail closed
# before search runs; otherwise any arbitrary hit can pollute the next turn.
if [ -f "$PYTHON_RESOLVER" ]; then
  # shellcheck source=/dev/null
  source "$PYTHON_RESOLVER"
  KEYWORDS=$("$MEMORY_PYTHON" -m agent_brain.memory.context.query_signal --brain-dir "$BRAIN_DIR" "$RECALL_PROMPT" 2>/dev/null || true)
else
  KEYWORDS=""
fi
DIAGNOSTICS_CONTEXT=""
if [ "${AGENT_MEMORY_HUB_DEBUG_QUERY_SIGNAL:-}" = "1" ] && [ -n "${MEMORY_PYTHON:-}" ]; then
  DIAGNOSTICS_JSON=$("$MEMORY_PYTHON" -m agent_brain.memory.context.query_signal --brain-dir "$BRAIN_DIR" --diagnose-json "$RECALL_PROMPT" 2>/dev/null || true)
  if [ -n "$DIAGNOSTICS_JSON" ]; then
    DIAGNOSTICS_CONTEXT=$("$MEMORY_PYTHON" - "$DIAGNOSTICS_JSON" <<'PY' 2>/dev/null || true
import json
import sys

payload = json.loads(sys.argv[1])

def join(name: str) -> str:
    values = payload.get(name) or []
    return "|".join(str(value) for value in values) if values else "-"

lines = [
    "<agent_brain_diagnostics>",
    "**Query signal diagnostics**",
    f"decision: {payload.get('decision')}",
    f"reason: {payload.get('reason')}",
    f"keywords: {payload.get('keywords') or '-'}",
    f"anchors: {join('anchors')}",
    f"kept_terms: {join('kept_terms')}",
    f"weak_noise: {join('weak_noise')}",
    f"trace: {join('trace')}",
    "</agent_brain_diagnostics>",
]
sys.stdout.write("\n".join(lines))
PY
)
  fi
fi

if [ -z "$KEYWORDS" ]; then
  GAP_JSON=""
  if [ -n "${MEMORY_PYTHON:-}" ]; then
    GAP_JSON=$(printf '%s' "$INPUT" | "$MEMORY_PYTHON" -m agent_brain.memory.evidence.multimodal_capture gap-json 2>/dev/null || true)
  fi
  if [ -z "$GAP_JSON" ] && [ -n "${MEMORY_PYTHON:-}" ]; then
    GAP_JSON=$("$MEMORY_PYTHON" -m agent_brain.memory.context.query_signal --brain-dir "$BRAIN_DIR" --gate-gap-json "$RECALL_PROMPT" 2>/dev/null || true)
  fi
  if [ -n "$GAP_JSON" ]; then
    GAP_QUERY="$RECALL_PROMPT"
    if [ -n "$PROMPT" ]; then
      GAP_QUERY="$PROMPT"
    fi
    "$MEMORY_PYTHON" - "$BRAIN_DIR" "$GAP_QUERY" "$GAP_JSON" \
      "${AGENT_MEMORY_HUB_ADAPTER:-unknown}" "$SESSION_ID" "$CWD" <<'PY' >/dev/null 2>&1 || true
import json
import sys
from pathlib import Path

from agent_brain.memory.governance.recall_events import record_gap

brain_dir, prompt, payload_json, adapter, session_id, cwd = sys.argv[1:7]
payload = json.loads(payload_json)
record_gap(
    Path(brain_dir),
    query=prompt,
    reason=str(payload.get("reason") or "query_not_injectable"),
    evidence=[str(value) for value in payload.get("evidence", [])],
    adapter=adapter,
    session_id=session_id or None,
    cwd=cwd or None,
)
PY
  fi
  if [ -n "$DIAGNOSTICS_CONTEXT" ]; then
    emit_hook_context "$DIAGNOSTICS_CONTEXT"
  elif [ -n "$GAP_JSON" ]; then
    EMPTY_REASON=$(python3 - "$GAP_JSON" <<'PY' 2>/dev/null || true
import json
import sys
try:
    payload = json.loads(sys.argv[1])
except (IndexError, json.JSONDecodeError):
    payload = {}
print(str(payload.get("reason") or "query_not_injectable"))
PY
)
    EMPTY_KEYWORDS=$(python3 - "$GAP_JSON" <<'PY' 2>/dev/null || true
import json
import sys
try:
    payload = json.loads(sys.argv[1])
except (IndexError, json.JSONDecodeError):
    payload = {}
for value in payload.get("evidence", []) or []:
    text = str(value)
    if text.startswith("terms="):
        print(text.removeprefix("terms="))
        break
    if text.startswith("multimodal_placeholders="):
        print(text.removeprefix("multimodal_placeholders="))
        break
PY
)
    EMPTY_DETAIL=$(python3 - "$GAP_JSON" <<'PY' 2>/dev/null || true
import json
import sys
try:
    payload = json.loads(sys.argv[1])
except (IndexError, json.JSONDecodeError):
    payload = {}
evidence = [str(value) for value in payload.get("evidence", []) or []]
print("; ".join(evidence))
PY
)
    [ -n "$EMPTY_DETAIL" ] || EMPTY_DETAIL="query signal did not produce injectable keywords"
    emit_empty_trace_context "${EMPTY_REASON:-query_not_injectable}" "${EMPTY_KEYWORDS:-"-"}" "$EMPTY_DETAIL" || echo '{}'
  elif emit_empty_trace_context "query_not_injectable" "-" "query signal did not produce injectable keywords"; then
    :
  else
    echo '{}'
  fi
  exit 0
fi

SEARCH_QUERY="$KEYWORDS"
export AGENT_MEMORY_HUB_RAW_QUERY="$RECALL_PROMPT"

# 跨平台 timeout（mac 没 timeout，gtimeout 在 brew coreutils）
TIMEOUT_BIN=""
for t in gtimeout timeout; do
  if command -v "$t" > /dev/null 2>&1; then TIMEOUT_BIN="$t"; break; fi
done

# session-end / auto-captured 默认通过 search 层 --exclude-tag 排除；
# user 若显式查询 session 相关关键词则跳过排除，让 LLM 能拉到这些 item。
# 用未加引号的 $EXCLUDE 让 shell 分词（兼容 bash 3.2 + set -u；空时为零参数）。
EXCLUDE=""
case "$KEYWORDS" in
  *session*|*归档*|*needs-review*|*handoff*|*auto-captured*) ;;
  *) EXCLUDE="--exclude-tag session-end,auto-captured" ;;
esac

SEARCH_ARGS=(
  "$SEARCH_TOOL"
  "$SEARCH_QUERY"
  "--top-k" "$TOP_K"
  "--prefer-type" "$PREFER_TYPES"
  "--format" "text"
  "--context-firewall"
  "--record-injection-cohort"
  "--record-recall-gap"
  "--adapter" "${AGENT_MEMORY_HUB_ADAPTER:-unknown}"
  "--session" "$SESSION_ID"
  "--cwd" "$CWD"
)
if [ -n "$EXCLUDE" ]; then
  SEARCH_ARGS+=("--exclude-tag" "session-end,auto-captured")
fi

SEARCH_STATUS=0
EMPTY_SEARCH_REASON="search_no_context"
set +e
if [ -n "$TIMEOUT_BIN" ]; then
  RESULTS=$("$TIMEOUT_BIN" "$SEARCH_TIMEOUT_SECONDS" "${SEARCH_ARGS[@]}" 2>/dev/null)
  SEARCH_STATUS=$?
elif [ -n "${MEMORY_PYTHON:-}" ]; then
  RESULTS=$(AGENT_MEMORY_HUB_SEARCH_TIMEOUT_SECONDS="$SEARCH_TIMEOUT_SECONDS" "$MEMORY_PYTHON" - "${SEARCH_ARGS[@]}" <<'PY' 2>/dev/null
import os
import subprocess
import sys

timeout = float(os.environ.get("AGENT_MEMORY_HUB_SEARCH_TIMEOUT_SECONDS", "2"))
try:
    proc = subprocess.run(
        sys.argv[1:],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=timeout,
    )
except subprocess.TimeoutExpired:
    raise SystemExit(124)

sys.stdout.write(proc.stdout)
raise SystemExit(proc.returncode)
PY
)
  SEARCH_STATUS=$?
else
  RESULTS=""
  SEARCH_STATUS=124
fi
set -e

case "$SEARCH_STATUS" in
  124|137)
    record_hook_latency "search_memory" "timeout" "search exceeded internal hook budget"
    EMPTY_SEARCH_REASON="search_timeout"
    RESULTS=""
    ;;
esac

if [ -z "$RESULTS" ] || echo "$RESULTS" | head -1 | grep -q "no matches"; then
  if [ -n "$DIAGNOSTICS_CONTEXT" ]; then
    emit_hook_context "$DIAGNOSTICS_CONTEXT"
  elif emit_empty_trace_context "$EMPTY_SEARCH_REASON" "$KEYWORDS" "search returned no injectable memory candidates"; then
    :
  else
    echo '{}'
  fi
  exit 0
fi

CONTEXT=$(cat <<INNER
<agent_brain>
**Auto-injected memory candidates, not chat history** (keywords: ${KEYWORDS})

${RESULTS}

> 这些 memory items 由 hook 自动注入。如果跟当前任务相关请参考；不相关请忽略。
> Source boundary: these are retrieved memory candidates, not the current conversation transcript.
> 不要把它们说成“之前的对话历史”；需要提及时，说“召回的 memory item / 记忆候选”。
> Staleness boundary: treat every item as unverified until current cwd/adapter/date/source agree with the task.
> The current user message and live tool evidence override injected memory.
> 关键的 [decision] 和 [signal] 类 item 必须读，否则可能踩前人踩过的坑。
> If [artifact] or [episode] candidates directly answer a summary/status question, answer from the injected pack first; re-search only for current verification, conflicts, or exact file/log evidence.
> For “做了什么 / 解决了什么 / 如何解决” questions, organize around: problem -> fix -> evidence/verification -> remaining boundary.
> For terse project/name prompts, answer with what the candidates establish first, then ask what the user wants next.
</agent_brain>
INNER
)
if [ -n "$DIAGNOSTICS_CONTEXT" ]; then
  CONTEXT="${DIAGNOSTICS_CONTEXT}"$'\n'"${CONTEXT}"
fi

emit_hook_context "$CONTEXT"
