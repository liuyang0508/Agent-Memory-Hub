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
SEARCH_OUTPUT_MAX_BYTES=1048576
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
PROMPT_SENTINEL=$'\036'
PROMPT_WITH_SENTINEL=$(printf '%s' "$INPUT" | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
prompt = payload.get("prompt", "")
sys.stdout.write(prompt if isinstance(prompt, str) else "")
sys.stdout.write("\x1e")
' 2>/dev/null || printf '%s' "$PROMPT_SENTINEL")
PROMPT=${PROMPT_WITH_SENTINEL%"$PROMPT_SENTINEL"}
SESSION_ID=$(echo "$INPUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("session_id",""))' 2>/dev/null || true)
CWD=$(echo "$INPUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("cwd",""))' 2>/dev/null || true)
HOOK_EVENT_NAME=$(echo "$INPUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("hook_event_name","UserPromptSubmit"))' 2>/dev/null || echo "UserPromptSubmit")
if [ -n "$PROMPT" ] && [ -f "$PYTHON_RESOLVER" ]; then
  # Resolve and verify once in the parent hook. Child runtime/search shims
  # inherit the exported verdict instead of repeatedly importing the full CLI.
  # Never trust a verdict inherited from the user's outer environment: this
  # process is the authority that creates the short-lived child credential.
  unset AGENT_MEMORY_HUB_PYTHON_RESOLVED || true
  unset AGENT_MEMORY_HUB_PYTHON_RESOLVED_PATH || true
  unset AGENT_MEMORY_HUB_PYTHON_RESOLVED_CANONICAL_PATH || true
  unset AGENT_MEMORY_HUB_PYTHON_RESOLVED_PROJECT_ROOT || true
  unset AGENT_MEMORY_HUB_PYTHON_RESOLVED_IMPORTS || true
  unset AGENT_MEMORY_HUB_PYTHON_RESOLVED_IDENTITY || true
  unset AGENT_MEMORY_HUB_PYTHON_RESOLVED_CREATOR_PID || true
  # shellcheck source=/dev/null
  source "$PYTHON_RESOLVER"
  export MEMORY_PYTHON AGENT_MEMORY_HUB_PYTHON_RESOLVED
fi
if [ -x "$RECORD_TOOL" ]; then
  "$RECORD_TOOL" \
    --adapter "${AGENT_MEMORY_HUB_ADAPTER:-unknown}" \
    --event "$HOOK_EVENT_NAME" \
    --session "$SESSION_ID" \
    --cwd "$CWD" \
    >/dev/null 2>&1 || true
fi

if [ -n "$PROMPT" ] && [ -n "${MEMORY_PYTHON:-}" ]; then
  printf '%s' "$INPUT" | "$MEMORY_PYTHON" -m agent_brain.memory.evidence.hook_capture prompt \
    >/dev/null 2>&1 || true
fi
RECALL_PROMPT="$PROMPT"
MULTIMODAL_GAP_JSON=""
MULTIMODAL_QUERY_HASH=""
if [ -n "$PROMPT" ] && [ -n "${MEMORY_PYTHON:-}" ]; then
  NORMALIZED_PROMPT=$(printf '%s' "$PROMPT" | "$MEMORY_PYTHON" -m agent_brain.memory.context.prompt_normalization 2>/dev/null || true)
  if [ -n "$NORMALIZED_PROMPT" ]; then
    RECALL_PROMPT="$NORMALIZED_PROMPT"
  fi
  MULTIMODAL_RECALL_TEXT=$(printf '%s' "$INPUT" | "$MEMORY_PYTHON" -m agent_brain.memory.evidence.multimodal_capture recall-text 2>/dev/null || true)
  MULTIMODAL_GAP_JSON=$(printf '%s' "$INPUT" | "$MEMORY_PYTHON" -m agent_brain.memory.evidence.multimodal_capture gap-json 2>/dev/null || true)
  if [ -n "$MULTIMODAL_RECALL_TEXT" ]; then
    if [ -n "$RECALL_PROMPT" ]; then
      RECALL_PROMPT="${RECALL_PROMPT}"$'\n'"${MULTIMODAL_RECALL_TEXT}"
    else
      RECALL_PROMPT="$MULTIMODAL_RECALL_TEXT"
    fi
  fi
fi
if [ -n "$MULTIMODAL_GAP_JSON" ] && [ -n "${MEMORY_PYTHON:-}" ]; then
  MULTIMODAL_QUERY_HASH=$(printf '%s' "$PROMPT" | "$MEMORY_PYTHON" -c '
import hashlib
import sys

digest = hashlib.sha256()
while chunk := sys.stdin.buffer.read(65536):
    digest.update(chunk)
sys.stdout.write("sha256:" + digest.hexdigest())
' 2>/dev/null || true)
  if [[ ! "$MULTIMODAL_QUERY_HASH" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    MULTIMODAL_GAP_JSON=""
    MULTIMODAL_QUERY_HASH=""
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
)
if [ -z "$MULTIMODAL_GAP_JSON" ]; then
  SEARCH_ARGS+=("--record-recall-gap")
fi
SEARCH_ARGS+=(
  "--adapter" "${AGENT_MEMORY_HUB_ADAPTER:-unknown}"
  "--session" "$SESSION_ID"
  "--cwd" "$CWD"
  "--"
  "$RECALL_PROMPT"
)

bounded_search_stdout() {
  "${MEMORY_PYTHON:-python3}" -c '
import sys

limit = int(sys.argv[1])
data = sys.stdin.buffer.read(limit + 1)
if len(data) > limit:
    raise SystemExit(125)
sys.stdout.buffer.write(data)
' "$SEARCH_OUTPUT_MAX_BYTES"
}

SEARCH_STATUS=0
RESULTS=""
set +e
if [ -n "$TIMEOUT_BIN" ]; then
  RESULTS=$("$TIMEOUT_BIN" "$SEARCH_TIMEOUT_SECONDS" "${SEARCH_ARGS[@]}" 2>/dev/null | bounded_search_stdout)
  SEARCH_STATUS=$?
elif [ -n "${MEMORY_PYTHON:-}" ]; then
  RESULTS=$(AGENT_MEMORY_HUB_SEARCH_TIMEOUT_SECONDS="$SEARCH_TIMEOUT_SECONDS" "$MEMORY_PYTHON" - "$SEARCH_OUTPUT_MAX_BYTES" "${SEARCH_ARGS[@]}" <<'PY' 2>/dev/null
import os
import selectors
import signal
import subprocess
import sys
import time

timeout = float(os.environ.get("AGENT_MEMORY_HUB_SEARCH_TIMEOUT_SECONDS", "2"))
limit = int(sys.argv[1])
proc = subprocess.Popen(
    sys.argv[2:],
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
)
assert proc.stdout is not None
deadline = time.monotonic() + timeout
buffer = bytearray()
selector = selectors.DefaultSelector()
selector.register(proc.stdout, selectors.EVENT_READ)


def kill_group() -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


def stop(code: int) -> None:
    kill_group()
    raise SystemExit(code)


try:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            stop(124)
        if not selector.select(remaining):
            stop(124)
        chunk = os.read(proc.stdout.fileno(), min(65536, limit + 1 - len(buffer)))
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > limit:
            stop(125)
except (OSError, ValueError):
    kill_group()
    raise

remaining = deadline - time.monotonic()
if remaining <= 0:
    stop(124)
try:
    returncode = proc.wait(timeout=remaining)
except subprocess.TimeoutExpired:
    stop(124)
if returncode != 0:
    kill_group()
    raise SystemExit(returncode)
sys.stdout.buffer.write(buffer)
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
    "$BRAIN_DIR" \
    "${AGENT_MEMORY_HUB_ADAPTER:-unknown}" \
    "$SESSION_ID" \
    "$CWD" \
    "$MULTIMODAL_QUERY_HASH" \
    3<<<"$RESULTS" 4<<<"$MULTIMODAL_GAP_JSON" <<'PY' 2>/dev/null || echo '{}'
import json
import re
import sys
from os import environ
from pathlib import Path


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
route_reasons = {
    "ok": {"route_completed"},
    "skipped": {
        "admission_rejected",
        "lexical_terms_empty",
        "semantic_not_ready",
    },
    "timeout": {"route_timeout"},
    "error": {"route_error"},
}
for route in routes:
    route_status = route.get("status") if isinstance(route, dict) else None
    route_reason = route.get("reason") if isinstance(route, dict) else None
    if (
        not isinstance(route, dict)
        or set(route) != {"route", "status", "candidate_count", "reason"}
        or route.get("route")
        not in {"lexical_terms", "semantic_raw", "lexical_raw_fallback"}
        or route_status not in route_reasons
        or type(route.get("candidate_count")) is not int
        or route["candidate_count"] < 0
        or not isinstance(route_reason, str)
        or route_reason not in route_reasons[route_status]
    ):
        sys.stdout.write("{}\n")
        raise SystemExit(0)

output_format = sys.argv[1]
diagnostics = environ.get("AGENT_MEMORY_HUB_DIAGNOSTICS_CONTEXT", "")
multimodal_gap = None
if status == "empty":
    try:
        with open(4, encoding="utf-8") as gap_stream:
            candidate_gap = json.load(gap_stream)
    except (json.JSONDecodeError, UnicodeDecodeError):
        candidate_gap = None
    if (
        isinstance(candidate_gap, dict)
        and candidate_gap.get("reason") == "multimodal_extraction_missing"
        and isinstance(candidate_gap.get("evidence"), list)
        and all(isinstance(value, str) for value in candidate_gap["evidence"])
    ):
        multimodal_gap = candidate_gap
        from agent_brain.memory.governance.recall_events import record_gap

        query_hash = sys.argv[7]
        if re.fullmatch(r"sha256:[0-9a-f]{64}", query_hash) is None:
            sys.stdout.write("{}\n")
            raise SystemExit(0)
        record_gap(
            Path(sys.argv[3]),
            query=query_hash,
            reason="multimodal_extraction_missing",
            evidence=[f"source_evidence_count={len(candidate_gap['evidence'])}"],
            adapter=sys.argv[4],
            session_id=sys.argv[5] or None,
            cwd=sys.argv[6] or None,
        )
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
elif status == "empty" and multimodal_gap is not None and sys.argv[2] == "1":
    evidence = multimodal_gap["evidence"]
    placeholder = next(
        (
            value.removeprefix("multimodal_placeholders=")
            for value in evidence
            if value.startswith("multimodal_placeholders=")
        ),
        "-",
    )
    safe_detail = "; ".join(
        value
        for value in evidence
        if value.startswith(("multimodal_placeholders=", "extraction_text="))
    )
    emit(
        "\n".join(
            (
                "<agent_brain_diagnostics>",
                "**AMH hook trace**",
                "hook: triggered",
                "decision: no_injection",
                "reason: multimodal_extraction_missing",
                f"keywords: {placeholder}",
                f"detail: {safe_detail}",
                "next: memory hook recent --limit 5",
                "</agent_brain_diagnostics>",
            )
        ),
        output_format,
    )
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
