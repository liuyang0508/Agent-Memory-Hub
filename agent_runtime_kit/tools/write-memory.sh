#!/usr/bin/env bash
# write-memory.sh — thin write shim with a durable pending fallback (Stage C).
#
# What it does:
#   Parses the documented write flags, captures the item body from stdin, then
#   tries the Python `memory write` CLI. If no interpreter can run it, the write
#   fails, or MEMORY_HUB_FORCE_PENDING=1 is set, it appends a durable *pending*
#   record under $BRAIN_DIR/pending/ instead of dropping the write. A later
#   `memory sync-pending` drains that queue through the one WriteService funnel,
#   so the markdown pool eventually converges. Markdown is the source of truth;
#   this shim's job is to guarantee the intent is never silently lost.
#
# Usage:
#   echo "body text" | write-memory.sh \
#       --type fact|episode|decision|artifact|signal|handoff \
#       --title "one-line title" --summary "1-2 sentence summary" \
#       [--project P] [--tags "t1,t2"] [--agent NAME] [--session SID] \
#       [--tenant-id TENANT] \
#       [--cwd DIR] [--adapter NAME] \
#       [--ref-file PATH] [--ref-url URL] [--ref-mem ID] [--ref-commit REF] \
#       [--ref-resource RES_ID] [--ref-extraction EXT_ID] \
#       [--sensitivity public|internal|private|secret]
#
# Depends on: bash + coreutils (date, mkdir) for the always-available fallback;
#   optionally python3 (robust JSON encoding) and a agent_brain-capable Python
#   resolved via _resolve-python.sh. Neither python is required for the queue
#   path — that is the whole point of Stage C.
#
# Note: `set -e` is intentionally omitted. A failing interpreter probe must fall
#   through to the pending queue rather than abort the script.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- arg parse ---------------------------------------------------------------
# Keep the originals untouched so the full, unmodified flag set is forwarded to
# the Python CLI; the parsed copies below only feed the pending record.
ORIG_ARGS=("$@")
TYPE="fact"
TITLE=""
SUMMARY=""
TAGS=""
SENSITIVITY="internal"
PROJECT=""
TENANT_ID=""
AGENT="${AGENT:-claude-code}"
SESSION=""
VALIDITY_CWD=""
VALIDITY_REPO=""
VALIDITY_BRANCH=""
VALIDITY_OS=""
VALIDITY_ADAPTER=""
VALIDITY_TTL_HOURS=""
REF_FILES=""
REF_URLS=""
REF_MEMS=""
REF_COMMITS=""
REF_RESOURCES=""
REF_EXTRACTIONS=""
_append_line() {
  local current="$1" value="$2"
  if [ -z "$value" ]; then
    printf '%s' "$current"
  elif [ -z "$current" ]; then
    printf '%s' "$value"
  else
    printf '%s\n%s' "$current" "$value"
  fi
}
while [ $# -gt 0 ]; do
  case "$1" in
    --type)        TYPE="${2:-}"; shift 2 ;;
    --title)       TITLE="${2:-}"; shift 2 ;;
    --summary)     SUMMARY="${2:-}"; shift 2 ;;
    --tags)        TAGS="${2:-}"; shift 2 ;;
    --sensitivity) SENSITIVITY="${2:-}"; shift 2 ;;
    --project)     PROJECT="${2:-}"; shift 2 ;;
    --tenant-id|--tenant) TENANT_ID="${2:-}"; shift 2 ;;
    --agent)       AGENT="${2:-}"; shift 2 ;;
    --session)     SESSION="${2:-}"; shift 2 ;;
    --cwd)         VALIDITY_CWD="${2:-}"; shift 2 ;;
    --adapter)     VALIDITY_ADAPTER="${2:-}"; shift 2 ;;
    --validity-cwd) VALIDITY_CWD="${2:-}"; shift 2 ;;
    --validity-repo) VALIDITY_REPO="${2:-}"; shift 2 ;;
    --validity-branch) VALIDITY_BRANCH="${2:-}"; shift 2 ;;
    --validity-os) VALIDITY_OS="${2:-}"; shift 2 ;;
    --validity-adapter) VALIDITY_ADAPTER="${2:-}"; shift 2 ;;
    --validity-ttl-hours) VALIDITY_TTL_HOURS="${2:-}"; shift 2 ;;
    --ref-file) REF_FILES="$(_append_line "$REF_FILES" "${2:-}")"; shift 2 ;;
    --ref-url) REF_URLS="$(_append_line "$REF_URLS" "${2:-}")"; shift 2 ;;
    --ref-mem) REF_MEMS="$(_append_line "$REF_MEMS" "${2:-}")"; shift 2 ;;
    --ref-commit) REF_COMMITS="$(_append_line "$REF_COMMITS" "${2:-}")"; shift 2 ;;
    --ref-resource) REF_RESOURCES="$(_append_line "$REF_RESOURCES" "${2:-}")"; shift 2 ;;
    --ref-extraction) REF_EXTRACTIONS="$(_append_line "$REF_EXTRACTIONS" "${2:-}")"; shift 2 ;;
    *)             shift ;;  # other documented flags pass through via ORIG_ARGS
  esac
done

# --- capture body from stdin -------------------------------------------------
# Read once so the body can be both forwarded to the CLI and buffered on failure.
BODY=""
if [ ! -t 0 ]; then BODY="$(cat)"; fi

# --- pending fallback --------------------------------------------------------
_json_escape() {
  # Escape a string for safe embedding inside a JSON double-quoted value. Used
  # only by the no-python3 last resort; python3's json.dumps handles the rest.
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  s="${s//$'\t'/\\t}"
  printf '%s' "$s"
}

_json_array_from_lines() {
  local input="$1" arr="" line
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    arr="$arr,\"$(_json_escape "$line")\""
  done <<EOF
$input
EOF
  printf '[%s]' "${arr#,}"
}

_emit_pending_shell() {
  # Hand-rolled record for the rare environment with no python3 at all.
  local f="$1" tags_json="[]" arr="" t
  if [ -n "$TAGS" ]; then
    local IFS=,
    for t in $TAGS; do
      [ -z "$t" ] && continue
      arr="$arr,\"$(_json_escape "$t")\""
    done
    tags_json="[${arr#,}]"
  fi
  local validity_json="{}" comma=""
  if [ -n "$VALIDITY_CWD" ] || [ -n "$VALIDITY_ADAPTER" ]; then
    validity_json="{"
    if [ -n "$VALIDITY_CWD" ]; then
      validity_json="${validity_json}\"cwd\":\"$(_json_escape "$VALIDITY_CWD")\""
      comma=","
    fi
    if [ -n "$VALIDITY_ADAPTER" ]; then
      validity_json="${validity_json}${comma}\"adapter\":\"$(_json_escape "$VALIDITY_ADAPTER")\""
    fi
    validity_json="${validity_json}}"
  fi
  local refs_json
  refs_json="{\"files\":$(_json_array_from_lines "$REF_FILES"),\"urls\":$(_json_array_from_lines "$REF_URLS"),\"mems\":$(_json_array_from_lines "$REF_MEMS"),\"commits\":$(_json_array_from_lines "$REF_COMMITS"),\"resources\":$(_json_array_from_lines "$REF_RESOURCES"),\"extractions\":$(_json_array_from_lines "$REF_EXTRACTIONS")}"
  printf '{"v":1,"op":"write","origin":"hook","attempt":0,"item":{"type":"%s","title":"%s","summary":"%s","body":"%s","tags":%s,"sensitivity":"%s","project":"%s","tenant_id":"%s","agent":"%s","session":"%s","validity":%s,"refs":%s,"confidence":0.7,"allow_unsafe":false}}\n' \
    "$(_json_escape "$TYPE")" "$(_json_escape "$TITLE")" "$(_json_escape "$SUMMARY")" \
    "$(_json_escape "$BODY")" "$tags_json" "$(_json_escape "$SENSITIVITY")" \
    "$(_json_escape "$PROJECT")" "$(_json_escape "$TENANT_ID")" \
    "$(_json_escape "$AGENT")" "$(_json_escape "$SESSION")" \
    "$validity_json" "$refs_json" >"$f"
}

_emit_pending() {
  local bdir="${BRAIN_DIR:-$HOME/.agent-memory-hub}"
  mkdir -p "$bdir/pending"
  local f="$bdir/pending/$(date -u +%Y%m%dT%H%M%SZ)-$$$RANDOM.jsonl"
  # Prefer python3 for correct JSON encoding (unicode, newlines, quotes); fall
  # back to a pure-shell encoder so the queue path needs no python whatsoever.
  TYPE="$TYPE" TITLE="$TITLE" SUMMARY="$SUMMARY" TAGS="$TAGS" \
  SENSITIVITY="$SENSITIVITY" PROJECT="$PROJECT" AGENT="$AGENT" SESSION="$SESSION" \
  TENANT_ID="$TENANT_ID" \
  VALIDITY_CWD="$VALIDITY_CWD" VALIDITY_REPO="$VALIDITY_REPO" \
  VALIDITY_BRANCH="$VALIDITY_BRANCH" VALIDITY_OS="$VALIDITY_OS" \
  VALIDITY_ADAPTER="$VALIDITY_ADAPTER" VALIDITY_TTL_HOURS="$VALIDITY_TTL_HOURS" \
  REF_FILES="$REF_FILES" REF_URLS="$REF_URLS" REF_MEMS="$REF_MEMS" \
  REF_COMMITS="$REF_COMMITS" REF_RESOURCES="$REF_RESOURCES" \
  REF_EXTRACTIONS="$REF_EXTRACTIONS" \
  BODY="$BODY" python3 - "$f" <<'PY' 2>/dev/null || _emit_pending_shell "$f"
import json, os, sys

tags = [t for t in os.environ.get("TAGS", "").split(",") if t]
def lines(name):
    return [v for v in os.environ.get(name, "").splitlines() if v]

validity = {
    "cwd": os.environ.get("VALIDITY_CWD") or None,
    "repo": os.environ.get("VALIDITY_REPO") or None,
    "branch": os.environ.get("VALIDITY_BRANCH") or None,
    "os": os.environ.get("VALIDITY_OS") or None,
    "adapter": os.environ.get("VALIDITY_ADAPTER") or None,
}
ttl_hours = os.environ.get("VALIDITY_TTL_HOURS")
if ttl_hours:
    try:
        validity["ttl_hours"] = int(ttl_hours)
    except ValueError:
        pass
validity = {k: v for k, v in validity.items() if v is not None}
rec = {
    "v": 1, "op": "write", "origin": "hook", "attempt": 0,
    "item": {
        "type": os.environ.get("TYPE", "fact"),
        "title": os.environ.get("TITLE", ""),
        "summary": os.environ.get("SUMMARY", ""),
        "body": os.environ.get("BODY", ""),
        "tags": tags,
        "sensitivity": os.environ.get("SENSITIVITY", "internal"),
        "project": os.environ.get("PROJECT") or None,
        "tenant_id": os.environ.get("TENANT_ID") or None,
        "agent": os.environ.get("AGENT") or None,
        "session": os.environ.get("SESSION") or None,
        "validity": validity,
        "refs": {
            "files": lines("REF_FILES"),
            "urls": lines("REF_URLS"),
            "mems": lines("REF_MEMS"),
            "commits": lines("REF_COMMITS"),
            "resources": lines("REF_RESOURCES"),
            "extractions": lines("REF_EXTRACTIONS"),
        },
        "confidence": 0.7,
        "allow_unsafe": False,
    },
}
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
PY
  echo "queued: $f"
}

# --- dispatch ----------------------------------------------------------------
# Forced-offline path (tests / known-degraded hosts): skip Python entirely.
if [ "${MEMORY_HUB_FORCE_PENDING:-0}" = "1" ]; then
  _emit_pending
  exit 0
fi

# Prefer the Python CLI; resolve an interpreter that can import agent_brain.
source "$SCRIPT_DIR/_resolve-python.sh"
# Hook writes must be durable and fast even on offline machines. Full semantic
# indexing can be rebuilt later; do not block a write on model downloads.
if printf '%s' "$BODY" | MEMORY_HUB_EMBEDDING_OFFLINE="${MEMORY_HUB_EMBEDDING_OFFLINE:-1}" memory_cli write "${ORIG_ARGS[@]}"; then
  exit 0
fi

# Interpreter missing, store locked, or the write crashed: never lose the write.
_emit_pending
exit 0
