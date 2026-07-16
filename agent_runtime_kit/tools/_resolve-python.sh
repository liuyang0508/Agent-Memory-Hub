#!/usr/bin/env bash
# _resolve-python.sh — 公共 helper：定位能运行 memory CLI 的 Python 解释器
#
# 使用方法（在其他脚本中 source）:
#   SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
#   source "$SCRIPT_DIR/_resolve-python.sh"
#   # 之后可用: $MEMORY_PYTHON  — 能 import 所需 AMH 模块的 python 路径
#   #          memory_cli ...   — 等价于 `memory ...` 但不依赖 PATH
#
# 查找顺序:
#   1. 显式 AGENT_MEMORY_HUB_PYTHON / MEMORY_PYTHON
#   2. 项目 .venv/bin/python3 / python
#   3. memory CLI 同目录的 python3 / python（pip install 产物）
#   4. PATH 上的 python3 / python（如果能 import 所需模块）
#   5. 裸 python3 + 友好报错或 memory 可执行文件回退

_TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_PROJECT_ROOT="$(cd "$_TOOLS_DIR/../.." && pwd)"
case ":${PYTHONPATH:-}:" in
  *":$_PROJECT_ROOT:"*) ;;
  *) export PYTHONPATH="$_PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}" ;;
esac
_REQUIRED_IMPORTS="${AGENT_MEMORY_HUB_PYTHON_IMPORTS:-agent_brain.interfaces.cli}"

_python_can_import_required_modules() {
  local candidate="$1"
  "$candidate" - "$_REQUIRED_IMPORTS" <<'PY' 2>/dev/null
import importlib
import sys

modules = [name.strip() for name in sys.argv[1].replace(",", " ").split() if name.strip()]
for module_name in modules:
    importlib.import_module(module_name)
PY
}

_candidate_exists() {
  local candidate="$1"
  [ -z "$candidate" ] && return 1
  if [ -x "$candidate" ]; then
    return 0
  fi
  case "$candidate" in
    */*) return 1 ;;
    *) command -v "$candidate" >/dev/null 2>&1 ;;
  esac
}

_candidate_path() {
  local candidate="$1"
  case "$candidate" in
    */*) printf '%s\n' "$candidate" ;;
    *) command -v "$candidate" 2>/dev/null ;;
  esac
}

_canonical_candidate_path() {
  local target
  target="$(_candidate_path "$1")" || return 1
  [ -n "$target" ] || return 1
  if command -v realpath >/dev/null 2>&1; then
    realpath "$target" 2>/dev/null
    return $?
  fi
  while [ -L "$target" ]; do
    local link directory
    link="$(readlink "$target")" || return 1
    directory="$(cd -P "$(dirname "$target")" && pwd)" || return 1
    case "$link" in
      /*) target="$link" ;;
      *) target="$directory/$link" ;;
    esac
  done
  local directory
  directory="$(cd -P "$(dirname "$target")" && pwd)" || return 1
  printf '%s/%s\n' "$directory" "$(basename "$target")"
}

_candidate_identity() {
  local candidate identity
  candidate="$(_candidate_path "$1")" || return 1
  [ -n "$candidate" ] || return 1
  # stat follows the executable symlink by default. Binding device, inode,
  # mtime and size therefore detects both in-place replacement and retargeting
  # without re-running the more expensive canonical-path walk in every child.
  if identity="$(stat -Lc '%d:%i:%Y:%s' "$candidate" 2>/dev/null)"; then
    printf '%s\n' "$identity"
    return 0
  fi
  if identity="$(stat -Lf '%d:%i:%m:%z' "$candidate" 2>/dev/null)"; then
    printf '%s\n' "$identity"
    return 0
  fi
  return 1
}

_clear_resolved_python_marker() {
  unset AGENT_MEMORY_HUB_PYTHON_RESOLVED
  unset AGENT_MEMORY_HUB_PYTHON_RESOLVED_PATH
  unset AGENT_MEMORY_HUB_PYTHON_RESOLVED_CANONICAL_PATH
  unset AGENT_MEMORY_HUB_PYTHON_RESOLVED_PROJECT_ROOT
  unset AGENT_MEMORY_HUB_PYTHON_RESOLVED_IMPORTS
  unset AGENT_MEMORY_HUB_PYTHON_RESOLVED_IDENTITY
  unset AGENT_MEMORY_HUB_PYTHON_RESOLVED_CREATOR_PID
}

_resolved_python_marker_is_valid() {
  [ "${AGENT_MEMORY_HUB_PYTHON_RESOLVED:-}" = "1" ] || return 1
  [ -n "${MEMORY_PYTHON:-}" ] || return 1
  _candidate_exists "$MEMORY_PYTHON" || return 1
  [ "${AGENT_MEMORY_HUB_PYTHON_RESOLVED_PATH:-}" = "$MEMORY_PYTHON" ] || return 1
  [ "${AGENT_MEMORY_HUB_PYTHON_RESOLVED_PROJECT_ROOT:-}" = "$_PROJECT_ROOT" ] || return 1
  [ "${AGENT_MEMORY_HUB_PYTHON_RESOLVED_IMPORTS:-}" = "$_REQUIRED_IMPORTS" ] || return 1
  local canonical identity creator_pid
  canonical="$(_canonical_candidate_path "$MEMORY_PYTHON")" || return 1
  identity="$(_candidate_identity "$MEMORY_PYTHON")" || return 1
  [ "${AGENT_MEMORY_HUB_PYTHON_RESOLVED_CANONICAL_PATH:-}" = "$canonical" ] || return 1
  [ "${AGENT_MEMORY_HUB_PYTHON_RESOLVED_IDENTITY:-}" = "$identity" ] || return 1
  creator_pid="${AGENT_MEMORY_HUB_PYTHON_RESOLVED_CREATOR_PID:-}"
  case "$creator_pid" in
    ''|*[!0-9]*) return 1 ;;
  esac
  kill -0 "$creator_pid" 2>/dev/null || return 1
}

_mark_python_resolved() {
  AGENT_MEMORY_HUB_PYTHON_RESOLVED=1
  AGENT_MEMORY_HUB_PYTHON_RESOLVED_PATH="$MEMORY_PYTHON"
  AGENT_MEMORY_HUB_PYTHON_RESOLVED_CANONICAL_PATH="$(_canonical_candidate_path "$MEMORY_PYTHON")"
  AGENT_MEMORY_HUB_PYTHON_RESOLVED_PROJECT_ROOT="$_PROJECT_ROOT"
  AGENT_MEMORY_HUB_PYTHON_RESOLVED_IMPORTS="$_REQUIRED_IMPORTS"
  AGENT_MEMORY_HUB_PYTHON_RESOLVED_IDENTITY="$(_candidate_identity "$MEMORY_PYTHON")"
  AGENT_MEMORY_HUB_PYTHON_RESOLVED_CREATOR_PID="$$"
  export MEMORY_PYTHON AGENT_MEMORY_HUB_PYTHON_RESOLVED
  export AGENT_MEMORY_HUB_PYTHON_RESOLVED_PATH
  export AGENT_MEMORY_HUB_PYTHON_RESOLVED_CANONICAL_PATH
  export AGENT_MEMORY_HUB_PYTHON_RESOLVED_PROJECT_ROOT
  export AGENT_MEMORY_HUB_PYTHON_RESOLVED_IMPORTS
  export AGENT_MEMORY_HUB_PYTHON_RESOLVED_IDENTITY
  export AGENT_MEMORY_HUB_PYTHON_RESOLVED_CREATOR_PID
}

_find_memory_python() {
  local candidates=(
    "${AGENT_MEMORY_HUB_PYTHON:-}"
    "${MEMORY_PYTHON:-}"
    "$_PROJECT_ROOT/.venv/bin/python3"
    "$_PROJECT_ROOT/.venv/bin/python"
  )

  local memory_bin
  memory_bin="$(command -v memory 2>/dev/null || true)"
  if [ -n "$memory_bin" ]; then
    local real_bin
    real_bin="$(readlink -f "$memory_bin" 2>/dev/null || echo "$memory_bin")"
    candidates+=("$(dirname "$real_bin")/../bin/python3")
    candidates+=("$(dirname "$real_bin")/python3")
    candidates+=("$(dirname "$real_bin")/../bin/python")
    candidates+=("$(dirname "$real_bin")/python")
  fi

  candidates+=("$(command -v python3 2>/dev/null || true)")
  candidates+=("$(command -v python 2>/dev/null || true)")

  for candidate in "${candidates[@]}"; do
    _candidate_exists "$candidate" || continue
    if _python_can_import_required_modules "$candidate"; then
      echo "$candidate"
      return 0
    fi
  done

  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 1
  fi
  echo ""
  return 1
}

if _resolved_python_marker_is_valid; then
  # A parent hook already paid the import probe and exported the exact
  # interpreter. Child runtime/search shims reuse that verdict instead of
  # importing the full CLI package again in every short-lived shell.
  _PYTHON_OK=0
elif MEMORY_PYTHON="$(_find_memory_python)"; then
  _PYTHON_OK=0
  _clear_resolved_python_marker
  _mark_python_resolved
else
  _PYTHON_OK=$?
  _clear_resolved_python_marker
fi

memory_cli() {
  if [ $_PYTHON_OK -eq 0 ] && [ -n "$MEMORY_PYTHON" ]; then
    "$MEMORY_PYTHON" -m agent_brain.interfaces.cli "$@"
  elif command -v memory >/dev/null 2>&1; then
    memory "$@"
  else
    echo "ERROR: memory CLI not found. Run: pip install -e <agent-memory-hub-dir>" >&2
    return 1
  fi
}
