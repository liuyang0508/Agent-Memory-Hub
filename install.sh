#!/bin/sh
#
# install.sh — Agent Memory Hub 一键安装
#
# 用法:
#   curl -fsSL https://github.com/liuyang0508/agent-memory-hub/releases/latest/download/install.sh | sh
#   ./install.sh              一键全装（CLI + Hooks + MCP + Web Admin）
#   ./install.sh --verify-only 安装前自检（不写配置）
#   ./install.sh --uninstall  卸载（保留用户数据）
#   ./install.sh --minimal    最小装（仅 CLI + /remember）
#

set -eu
if (set -o pipefail) 2>/dev/null; then
  set -o pipefail
fi

RELEASE_REPO="__AMH_GITHUB_REPOSITORY__"
RELEASE_REF="__AMH_GITHUB_REF_NAME__"
if [ -n "${AMH_REPO_SLUG:-}" ]; then
  RELEASE_REPO="$AMH_REPO_SLUG"
fi
if [ -n "${AMH_RELEASE_REF:-}" ]; then
  RELEASE_REF="$AMH_RELEASE_REF"
fi
if [ "$RELEASE_REPO" = "__AMH_GITHUB_REPOSITORY__" ]; then
  RELEASE_REPO="liuyang0508/agent-memory-hub"
fi
if [ "$RELEASE_REF" = "__AMH_GITHUB_REF_NAME__" ]; then
  RELEASE_REF="main"
fi
if [ "$RELEASE_REPO" = "liuyang0508/agent-memory-hub" ]; then
  REPO_URL="${AMH_REPO_URL:-https://github.com/liuyang0508/agent-memory-hub.git}"
else
  REPO_URL="${AMH_REPO_URL:-https://github.com/$RELEASE_REPO.git}"
fi
REF="${AMH_REF:-${AMH_BRANCH:-$RELEASE_REF}}"
TARGET_DIR="${AGENT_MEMORY_HUB_HOME:-$HOME/agent-memory-hub}"
DRY_RUN=false
VERIFY_ONLY=false

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --verify-only) VERIFY_ONLY=true ;;
  esac
done

SCRIPT_PATH="${0:-install.sh}"
case "$SCRIPT_PATH" in
  */*) SCRIPT_DIR=$(CDPATH= cd "$(dirname "$SCRIPT_PATH")" && pwd) ;;
  *) SCRIPT_DIR=$(pwd) ;;
esac

if [ ! -f "$SCRIPT_DIR/pyproject.toml" ] || [ ! -d "$SCRIPT_DIR/agent_brain" ]; then
  if [ "$DRY_RUN" = true ]; then
    cat <<EOF
Agent Memory Hub remote install dry run
  repo:   $REPO_URL
  ref:    $REF
  target: $TARGET_DIR
EOF
    exit 0
  fi

  if [ "$VERIFY_ONLY" = true ]; then
    cat <<EOF
Agent Memory Hub remote install verification
  repo:   $REPO_URL
  ref:    $REF
  target: $TARGET_DIR
EOF
    if ! command -v git >/dev/null 2>&1; then
      echo "installer_self_check=failed"
      echo "missing: git" >&2
      exit 1
    fi
    if [ -e "$TARGET_DIR" ] && [ ! -d "$TARGET_DIR/.git" ]; then
      echo "installer_self_check=failed"
      echo "target exists but is not a git checkout: $TARGET_DIR" >&2
      exit 1
    fi
    echo "git=ok"
    echo "installer_self_check=ok"
    exit 0
  fi

  if ! command -v git >/dev/null 2>&1; then
    echo "git is required. Install git first, then rerun this command." >&2
    exit 1
  fi

  if [ -d "$TARGET_DIR/.git" ]; then
    echo "Updating existing Agent Memory Hub checkout: $TARGET_DIR"
    git -C "$TARGET_DIR" fetch --depth=1 origin "$REF"
    git -C "$TARGET_DIR" checkout --detach FETCH_HEAD
  elif [ -e "$TARGET_DIR" ]; then
    echo "Target exists but is not a git checkout: $TARGET_DIR" >&2
    echo "Set AGENT_MEMORY_HUB_HOME to another directory or move the existing path." >&2
    exit 1
  else
    echo "Cloning Agent Memory Hub into $TARGET_DIR"
    git clone --depth=1 --branch "$REF" "$REPO_URL" "$TARGET_DIR"
  fi

  cd "$TARGET_DIR"
  exec ./install.sh "$@"
fi

CODE_DIR="$SCRIPT_DIR"
USER_DATA="${BRAIN_DIR:-$HOME/.agent-memory-hub}"
ACTION="install"
MINIMAL=false

for arg in "$@"; do
  case "$arg" in
    --uninstall) ACTION="uninstall" ;;
    --minimal) MINIMAL=true ;;
    --dry-run) ;;
    --verify-only) ;;
  esac
done

if [ "$DRY_RUN" = true ]; then
  cat <<EOF
Agent Memory Hub local install dry run
  code:    $CODE_DIR
  data:    $USER_DATA
  action:  $ACTION
  minimal: $MINIMAL
EOF
  exit 0
fi

if [ "$VERIFY_ONLY" = true ]; then
  echo "Agent Memory Hub local install verification"
  echo "  code:    $CODE_DIR"
  echo "  data:    $USER_DATA"
  echo "  action:  $ACTION"
  echo "  minimal: $MINIMAL"
  ERR=0
  check_file() {
    if [ -f "$CODE_DIR/$1" ]; then
      echo "ok: $1"
    else
      echo "missing: $1" >&2
      ERR=1
    fi
  }
  check_exec() {
    if [ -x "$CODE_DIR/$1" ]; then
      echo "ok: $1"
    else
      echo "missing-or-not-executable: $1" >&2
      ERR=1
    fi
  }
  check_dir() {
    if [ -d "$CODE_DIR/$1" ]; then
      echo "ok: $1"
    else
      echo "missing: $1" >&2
      ERR=1
    fi
  }
  check_file "pyproject.toml"
  check_dir "agent_brain"
  check_file "agent_runtime_kit/templates/remember.md.template"
  check_exec "agent_runtime_kit/hooks/inject-context.sh"
  check_exec "agent_runtime_kit/hooks/inject-discipline.sh"
  check_exec "agent_runtime_kit/hooks/session-end-signal.sh"
  check_exec "agent_runtime_kit/hooks/lifecycle-event.sh"
  check_exec "agent_runtime_kit/tools/write-memory.sh"
  check_exec "agent_runtime_kit/tools/search-memory.sh"
  check_exec "agent_runtime_kit/mcp/server.sh"
  if command -v python3 >/dev/null 2>&1; then
    PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PY_MAJOR=$(echo "$PYVER" | cut -d. -f1)
    PY_MINOR=$(echo "$PYVER" | cut -d. -f2)
    if [ "$PY_MAJOR" -gt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -ge 11 ]; }; then
      echo "python3=$PYVER"
    else
      echo "python3-too-old=$PYVER" >&2
      ERR=1
    fi
  else
    echo "missing: python3" >&2
    ERR=1
  fi
  if [ "$ERR" -eq 0 ]; then
    echo "installer_self_check=ok"
  else
    echo "installer_self_check=failed"
  fi
  exit "$ERR"
fi

SETTINGS="$HOME/.claude/settings.json"
COMMANDS_DIR="$HOME/.claude/commands"
TARGET_CMD="$COMMANDS_DIR/remember.md"
USER_BIN="${AGENT_MEMORY_HUB_BIN:-$HOME/.local/bin}"
MEMORY_SHIM="$USER_BIN/memory"
APP_VENV="$CODE_DIR/.venv"
APP_PYTHON="$APP_VENV/bin/python"
MEMORY_BIN="$APP_VENV/bin/memory"
AMH_INSTALL_ADAPTERS="${AMH_INSTALL_ADAPTERS:-codex claude_code wukong cursor cline continue_dev hermes_agent qoder qoder_work aider github_copilot aone_copilot openhuman opensquilla openclaw}"
AMH_STRICT_ADAPTER_INSTALL="${AMH_STRICT_ADAPTER_INSTALL:-0}"
AMH_ADAPTER_COMMAND_TIMEOUT="${AMH_ADAPTER_COMMAND_TIMEOUT:-5}"

run_memory_adapter_command() {
  action="$1"
  adapter="$2"
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$MEMORY_BIN" "$action" "$adapter" "$AMH_ADAPTER_COMMAND_TIMEOUT" <<'PY'
import subprocess
import sys

memory_bin, action, adapter, timeout_raw = sys.argv[1:5]
try:
    timeout_s = float(timeout_raw)
except ValueError:
    timeout_s = 5.0

try:
    result = subprocess.run(
        [memory_bin, "adapter", action, adapter],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout_s,
        check=False,
    )
    if result.stdout:
        print(result.stdout, end="")
    raise SystemExit(result.returncode)
except subprocess.TimeoutExpired as exc:
    output = exc.stdout or exc.output or ""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    if output:
        print(output, end="")
    print(f"adapter {action} timed out after {timeout_s:g}s: {adapter}", file=sys.stderr)
    raise SystemExit(124)
PY
    return $?
  fi
  case "$action" in
    install) "$MEMORY_BIN" adapter install "$adapter" ;;
    uninstall) "$MEMORY_BIN" adapter uninstall "$adapter" ;;
    *) echo "unknown adapter action: $action" >&2; return 2 ;;
  esac
}

# ============================================================
# UNINSTALL
# ============================================================
if [ "$ACTION" = "uninstall" ]; then
  echo "╔══════════════════════════════════════╗"
  echo "║   Agent Memory Hub — 卸载            ║"
  echo "╚══════════════════════════════════════╝"
  echo ""
  echo "  项目代码: $CODE_DIR"
  echo "  用户数据: ${USER_DATA}（保留，手动 rm -rf 删除）"
  echo ""

  if [ -f "$SETTINGS" ] && command -v python3 >/dev/null 2>&1; then
    cp "$SETTINGS" "${SETTINGS}.bak.uninstall.$(date +%s)"
    python3 - "$SETTINGS" "$CODE_DIR" <<'PY'
import json
import shlex
import sys
from pathlib import Path

settings_path = Path(sys.argv[1])
code_dir = sys.argv[2]
data = json.loads(settings_path.read_text(encoding="utf-8-sig"))
hooks = data.get("hooks", {})
prefixes = [
    f"{code_dir}/agent_runtime_kit/hooks",
    f"{code_dir}/brain/hooks",
]


def references_prefix(command: str, prefix: str) -> bool:
    if command.startswith(prefix):
        return True
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    return any(token.startswith(prefix) for token in tokens)


def entry_is_hub_owned(entry: dict) -> bool:
    entry_hooks = entry.get("hooks") or []
    if not entry_hooks:
        return False
    return all(
        any(references_prefix(str(hook.get("command", "")), prefix) for prefix in prefixes)
        for hook in entry_hooks
    )


removed_hooks = 0
for event in (
    "SessionStart",
    "UserPromptSubmit",
    "Stop",
    "PreCompact",
    "PostCompact",
    "SubagentStart",
    "SubagentStop",
):
    entries = hooks.get(event) or []
    kept = [entry for entry in entries if not entry_is_hub_owned(entry)]
    removed_hooks += len(entries) - len(kept)
    hooks[event] = kept

mcp = data.get("mcpServers")
removed_mcp = False
if isinstance(mcp, dict) and "agent-memory-hub" in mcp:
    del mcp["agent-memory-hub"]
    removed_mcp = True

tmp = settings_path.with_suffix(settings_path.suffix + ".tmp")
tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
tmp.replace(settings_path)
print(f"removed_hooks={removed_hooks}")
print(f"removed_mcp={int(removed_mcp)}")
PY
    echo "  ✅ Hooks/MCP 配置已移除"
  fi

  [ -f "$TARGET_CMD" ] && rm "$TARGET_CMD" && echo "  ✅ /remember 已移除"
  if [ -f "$MEMORY_SHIM" ] && grep -F "$CODE_DIR/.venv/bin/memory" "$MEMORY_SHIM" >/dev/null 2>&1; then
    rm "$MEMORY_SHIM"
    echo "  ✅ memory CLI shim 已移除"
  fi

  if [ -x "$MEMORY_BIN" ]; then
    echo ""
    echo "  移除全部已支持 Agent 适配器配置..."
    ADAPTER_UNINSTALL_FAILED=""
    for adapter in $AMH_INSTALL_ADAPTERS; do
      printf "    • %-16s " "$adapter"
      set +e
      ADAPTER_OUTPUT=$(run_memory_adapter_command uninstall "$adapter" 2>&1)
      ADAPTER_RC=$?
      set -e
      if [ "$ADAPTER_RC" -eq 0 ]; then
        echo "✅"
      else
        echo "⚠️"
        printf "%s\n" "$ADAPTER_OUTPUT" | sed 's/^/      /'
        ADAPTER_UNINSTALL_FAILED="$ADAPTER_UNINSTALL_FAILED $adapter"
      fi
    done
    if [ -n "$ADAPTER_UNINSTALL_FAILED" ]; then
      echo "  adapter_uninstall_partial_failures:$ADAPTER_UNINSTALL_FAILED"
    fi
  fi

  echo ""
  echo "  卸载完成。数据保留: $USER_DATA"
  echo "  彻底清除: rm -rf $USER_DATA"
  exit 0
fi

# ============================================================
# INSTALL
# ============================================================
echo ""
echo "╔══════════════════════════════════════╗"
echo "║   Agent Memory Hub — 安装            ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ─── Step 1: 数据目录 + /remember ───
echo "① 初始化数据目录..."
mkdir -p "$USER_DATA/items"
ITEM_COUNT=$(find "$USER_DATA/items" -maxdepth 1 -type f -name '*.md' 2>/dev/null | wc -l | tr -d ' ')
echo "   ✅ $USER_DATA/items ($ITEM_COUNT 条记忆)"

mkdir -p "$COMMANDS_DIR"
TEMPLATE="$CODE_DIR/agent_runtime_kit/templates/remember.md.template"
if [ -f "$TEMPLATE" ]; then
  sed "s|__HUB_CODE_DIR__|$CODE_DIR|g" "$TEMPLATE" > "$TARGET_CMD"
  echo "   ✅ /remember 命令已装"
fi

# ─── Step 2: Python core + Web ───
echo ""
echo "② 安装 Python 核心..."
if command -v python3 >/dev/null 2>&1; then
    PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PY_MAJOR=$(echo "$PYVER" | cut -d. -f1)
    PY_MINOR=$(echo "$PYVER" | cut -d. -f2)
    if [ "$PY_MAJOR" -gt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -ge 11 ]; }; then
        if [ ! -x "$APP_PYTHON" ]; then
            python3 -m venv "$APP_VENV"
            echo "   ✅ 项目 venv 已创建: $APP_VENV"
        else
            echo "   ✅ 项目 venv 已存在: $APP_VENV"
        fi
        if [ "$MINIMAL" = true ]; then
            "$APP_PYTHON" -m pip install -e "$CODE_DIR" -q
            echo "   ✅ CLI 核心已装"
        else
            "$APP_PYTHON" -m pip install -e "$CODE_DIR[web,embeddings]" -q
            echo "   ✅ CLI 核心 + Web Admin + 本地 Embeddings 已装"
        fi
        mkdir -p "$USER_BIN"
        cat > "$MEMORY_SHIM" <<EOF
#!/bin/sh
exec "$MEMORY_BIN" "\$@"
EOF
        chmod +x "$MEMORY_SHIM"
        echo "   ✅ memory CLI shim: $MEMORY_SHIM"
        if [ -x "$MEMORY_BIN" ]; then
            echo "   Building search index..."
            if "$MEMORY_BIN" reindex 2>&1; then
                echo "   ✅ 索引构建完成"
            else
                echo "   ⚠️ 索引构建失败；核心安装已继续"
                echo "      修复后可运行: memory reindex"
            fi
        else
            echo "   ❌ memory CLI 未生成: $MEMORY_BIN" >&2
            exit 1
        fi
    else
        echo "   ❌ 需要 Python 3.11+，当前 $PYVER" >&2
        exit 1
    fi
else
    echo "   ❌ 未检测到 python3" >&2
    exit 1
fi

if [ "$MINIMAL" = true ]; then
    echo ""
    echo "  最小安装完成。完整安装: ./install.sh"
    exit 0
fi

# ─── Step 3: Hooks ───
echo ""
echo "③ 配置 Claude Code Hooks..."
if [ ! -f "$SETTINGS" ]; then
  echo '{"hooks":{}}' > "$SETTINGS"
fi
cp "$SETTINGS" "${SETTINGS}.bak.$(date +%s)"

if command -v python3 >/dev/null 2>&1; then
  python3 - "$SETTINGS" "$CODE_DIR" <<'PY'
import json
import shlex
import sys
from pathlib import Path

settings_path = Path(sys.argv[1])
code_dir = sys.argv[2]
try:
    data = json.loads(settings_path.read_text(encoding="utf-8-sig"))
except json.JSONDecodeError as exc:
    raise SystemExit(f"settings.json is malformed: {exc}")

hooks = data.setdefault("hooks", {})
specs = {
    "SessionStart": "inject-discipline.sh",
    "UserPromptSubmit": "inject-context.sh",
    "Stop": "session-end-signal.sh",
    "PreCompact": "lifecycle-event.sh",
    "PostCompact": "lifecycle-event.sh",
    "SubagentStart": "lifecycle-event.sh",
    "SubagentStop": "lifecycle-event.sh",
}
path_prefix = "PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin${PATH:+:$PATH}"


def references_path(command: str, path: str) -> bool:
    if command == path:
        return True
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    return path in tokens


changed = 0
added = 0
for event, script in specs.items():
    current = f"{code_dir}/agent_runtime_kit/hooks/{script}"
    legacy = f"{code_dir}/brain/hooks/{script}"
    expected = f"{path_prefix} AGENT_MEMORY_HUB_ADAPTER=claude_code {current}"
    entries = hooks.setdefault(event, [])
    found = False
    for entry in entries:
        entry.setdefault("matcher", "")
        for hook in entry.get("hooks", []):
            command = str(hook.get("command", ""))
            if not (references_path(command, current) or references_path(command, legacy)):
                continue
            found = True
            if hook.get("command") != expected:
                hook["command"] = expected
                changed += 1
            if hook.get("type") != "command":
                hook["type"] = "command"
                changed += 1
    if not found:
        entries.append({
            "matcher": "",
            "hooks": [{"type": "command", "command": expected}],
        })
        added += 1

tmp = settings_path.with_suffix(settings_path.suffix + ".tmp")
tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
tmp.replace(settings_path)
print(f"added={added}")
print(f"updated={changed}")
PY
  echo "   ✅ Hooks 已同步（备份: ${SETTINGS}.bak.*）"
else
  echo "   ⚠️  未检测到 python3，跳过 Hook 配置"
fi

# ─── Step 4: MCP Server ───
echo ""
echo "④ 配置 MCP Server..."
if [ -x "$APP_PYTHON" ]; then
  echo "   ✅ MCP 使用项目 venv: $APP_VENV"
else
  echo "   ❌ 项目 venv 缺失，无法配置 MCP" >&2
  exit 1
fi

if [ -f "$SETTINGS" ] && command -v python3 >/dev/null 2>&1; then
  python3 - "$SETTINGS" "$CODE_DIR/agent_runtime_kit/mcp/server.sh" <<'PY'
import json
import sys
from pathlib import Path

settings_path = Path(sys.argv[1])
server_path = sys.argv[2]
data = json.loads(settings_path.read_text(encoding="utf-8-sig"))
mcp = data.setdefault("mcpServers", {})
current = mcp.get("agent-memory-hub")
changed = not isinstance(current, dict) or current.get("command") != server_path
if changed:
    mcp["agent-memory-hub"] = {"command": server_path}
    tmp = settings_path.with_suffix(settings_path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(settings_path)
print(f"updated={int(changed)}")
PY
  echo "   ✅ MCP 配置已同步"
fi

# ─── Step 5: Agent Adapter ───
echo ""
echo "⑤ 配置可用 Agent 接入..."
ADAPTER_INSTALL_OK=""
ADAPTER_INSTALL_FAILED=""
for adapter in $AMH_INSTALL_ADAPTERS; do
  printf "   • %-16s " "$adapter"
  set +e
  ADAPTER_OUTPUT=$(run_memory_adapter_command install "$adapter" 2>&1)
  ADAPTER_RC=$?
  set -e
  if [ "$ADAPTER_RC" -eq 0 ]; then
    echo "✅"
    ADAPTER_INSTALL_OK="$ADAPTER_INSTALL_OK $adapter"
  else
    echo "⚠️"
    printf "%s\n" "$ADAPTER_OUTPUT" | sed 's/^/      /'
    ADAPTER_INSTALL_FAILED="$ADAPTER_INSTALL_FAILED $adapter"
  fi
done
if [ -n "$ADAPTER_INSTALL_FAILED" ]; then
  echo "   optional_adapter_not_configured:$ADAPTER_INSTALL_FAILED"
  echo "   说明：这些是可选接入项，通常因为本机未安装对应客户端、配置文件损坏，或客户端 CLI 不在 PATH。"
  echo "   AMH 核心、Claude Code Hooks 和 MCP Server 已完成配置；需要时可单独运行: memory adapter install <adapter>"
  if [ "$AMH_STRICT_ADAPTER_INSTALL" = "1" ]; then
    exit 1
  fi
else
  echo "   ✅ 所有可用 adapter 已配置完成"
fi

ADAPTER_CONFIGURED_COPY="$ADAPTER_INSTALL_OK"
if [ -z "$ADAPTER_CONFIGURED_COPY" ]; then
  ADAPTER_CONFIGURED_COPY=" 无"
fi

ADAPTER_STATUS_LABEL="✅ 安装完成！"
ADAPTER_MODULE_COPY="已配置 adapter:${ADAPTER_CONFIGURED_COPY}"
if [ -n "$ADAPTER_INSTALL_FAILED" ]; then
  ADAPTER_STATUS_LABEL="ℹ️ 核心安装完成；部分可选 Agent 未配置"
  ADAPTER_MODULE_COPY="已配置 adapter:${ADAPTER_CONFIGURED_COPY}；可选未配置 adapter:${ADAPTER_INSTALL_FAILED}"
fi

# ─── Step 6: 自检 ───
echo ""
echo "⑥ 自检..."
if [ -x "$MEMORY_BIN" ]; then
  "$MEMORY_BIN" doctor 2>&1 || true
else
  "$CODE_DIR/agent_runtime_kit/hooks/test-hook.sh" 2>&1 | tail -3 || true
fi

# ─── 完成 ───
echo ""
echo "╔══════════════════════════════════════╗"
echo "║   $ADAPTER_STATUS_LABEL"
echo "╚══════════════════════════════════════╝"
echo ""
echo "  已安装模块:"
echo "    • CLI 命令行 (memory search / write / stats / ...)"
echo "    • Web 管理平台 (memory serve)"
echo "    • Claude Code Hooks (自动注入记忆上下文)"
echo "    • MCP Server (IDE 集成)"
echo "    • Agent Adapter: $ADAPTER_MODULE_COPY"
echo "    • /remember 命令 (会话结束智能归档)"
echo ""
echo "  下一步:"
echo "    1. 退出当前会话 → 重新打开 Claude Code"
echo "    2. memory serve --port 8765  ← 启动 Web 管理平台"
echo "    3. 打开 http://localhost:8765 点 Init Admin 创建管理员"
echo ""
echo "  常用命令:"
echo "    memory search \"关键词\"      搜索记忆"
echo "    memory stats                统计总览"
echo "    memory health               健康检查"
echo "    memory doctor               安装诊断"
echo "    memory serve                启动 Web 管理平台"
echo "    memory serve --open         启动并自动打开浏览器"
echo "    memory api-docs             查看全部 API 端点"
echo "    memory gc --dry-run         预览 GC 清理"
echo "    memory decay-status         衰减状态"
echo ""
echo "  卸载: ./install.sh --uninstall"
