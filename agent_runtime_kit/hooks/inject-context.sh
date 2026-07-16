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

# Query-signal diagnostics remain opt-in observability only. Admission and
# recall routing are owned by the Python routed-recall pipeline below.
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

# 跨平台 timeout（mac 没 timeout，gtimeout 在 brew coreutils）
TIMEOUT_BIN=""
for t in gtimeout timeout; do
  if command -v "$t" > /dev/null 2>&1; then TIMEOUT_BIN="$t"; break; fi
done

SEARCH_ARGS=(
  "$SEARCH_TOOL"
  "--top-k" "$TOP_K"
  "--prefer-type" "$PREFER_TYPES"
  "--routed-recall"
  "--context-firewall"
  "--format" "hook-json"
  "--record-injection-cohort"
  "--record-recall-gap"
  "--adapter" "${AGENT_MEMORY_HUB_ADAPTER:-unknown}"
  "--session" "$SESSION_ID"
  "--cwd" "$CWD"
  "--"
  "$RECALL_PROMPT"
)

SEARCH_STATUS=0
RESULTS=""
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
    RESULTS=""
    ;;
  0)
    ;;
  *)
    record_hook_latency "search_memory" "error" "search exited nonzero"
    RESULTS=""
    ;;
esac

if [ "$SEARCH_STATUS" -ne 0 ]; then
  echo '{}'
  exit 0
fi

# Parse the structured protocol exactly once. Any stdout contamination,
# malformed schema, timeout/error status, or invalid context fails closed.
AGENT_MEMORY_HUB_DIAGNOSTICS_CONTEXT="$DIAGNOSTICS_CONTEXT" \
  "${MEMORY_PYTHON:-python3}" - \
    "${AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT:-json}" \
    "${AGENT_MEMORY_HUB_HOOK_TRACE_EMPTY:-0}" \
    3<<<"$RESULTS" <<'PY' 2>/dev/null || echo '{}'
import json
import sys
from os import environ


def emit(context: str, output_format: str) -> None:
    if output_format in {"plain", "text"}:
        sys.stdout.write(context)
        if context:
            sys.stdout.write("\n")
        return
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")


try:
    with open(3, encoding="utf-8") as protocol_stream:
        payload = json.load(protocol_stream)
except (json.JSONDecodeError, UnicodeDecodeError):
    sys.stdout.write("{}\n")
    raise SystemExit(0)

if not isinstance(payload, dict) or set(payload) != {"status", "reason", "context", "routes"}:
    sys.stdout.write("{}\n")
    raise SystemExit(0)
status = payload.get("status")
reason = payload.get("reason")
context = payload.get("context")
routes = payload.get("routes")
reasons_by_status = {
    "injected": {"included"},
    "empty": {"admission_rejected", "no_candidates", "all_rejected"},
    "timeout": {"overall_timeout"},
    "error": {"internal_error"},
}
if (
    status not in {"injected", "empty", "timeout", "error"}
    or not isinstance(reason, str)
    or not isinstance(context, str)
    or not isinstance(routes, list)
    or reason not in reasons_by_status.get(status, set())
    or (status == "injected") != bool(context)
):
    sys.stdout.write("{}\n")
    raise SystemExit(0)
for route in routes:
    if (
        not isinstance(route, dict)
        or set(route) != {"route", "status", "candidate_count", "reason"}
        or route.get("route")
        not in {"lexical_terms", "semantic_raw", "lexical_raw_fallback"}
        or route.get("status") not in {"ok", "skipped", "timeout", "error"}
        or type(route.get("candidate_count")) is not int
        or route["candidate_count"] < 0
        or not (route.get("reason") is None or isinstance(route.get("reason"), str))
    ):
        sys.stdout.write("{}\n")
        raise SystemExit(0)

output_format = sys.argv[1]
diagnostics = environ.get("AGENT_MEMORY_HUB_DIAGNOSTICS_CONTEXT", "")
if status == "injected" and context:
    parts = []
    if diagnostics:
        parts.extend((diagnostics, ""))
    parts.extend(
        (
            "<agent_brain>",
            "**Auto-injected memory candidates, not chat history** (full-query routed recall)",
            "",
            context,
            "",
            "> 这些 memory items 由 hook 自动注入。如果跟当前任务相关请参考；不相关请忽略。",
            "> Source boundary: these are retrieved memory candidates, not the current conversation transcript.",
            "> 不要把它们说成“之前的对话历史”；需要提及时，说“召回的 memory item / 记忆候选”。",
            "> Staleness boundary: treat every item as unverified until current cwd/adapter/date/source agree with the task.",
            "> The current user message and live tool evidence override injected memory.",
            "> If [artifact] or [episode] candidates directly answer a summary/status question, answer from the injected pack first; re-search only for current verification, conflicts, or exact file/log evidence.",
            "> For “做了什么 / 解决了什么 / 如何解决” questions, organize around: problem -> fix -> evidence/verification -> remaining boundary.",
            "> For terse project/name prompts, answer with what the candidates establish first, then ask what the user wants next.",
            "</agent_brain>",
        )
    )
    wrapped = "\n".join(parts)
    emit(wrapped, output_format)
elif status == "empty" and diagnostics:
    emit(diagnostics, output_format)
elif status == "empty" and sys.argv[2] == "1":
    emit(
        "\n".join(
            (
                "<agent_brain_diagnostics>",
                "**AMH hook trace**",
                "hook: triggered",
                "decision: no_injection",
                f"reason: {reason}",
                "detail: routed recall returned no injectable context",
                "next: memory hook recent --limit 5",
                "</agent_brain_diagnostics>",
            )
        ),
        output_format,
    )
else:
    sys.stdout.write("{}\n")
PY
