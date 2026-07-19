# 安装健康度兼容补丁 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 quickstart benchmark 完全隔离宿主持久状态，并让 `memory doctor` 对已配置 Codex/Claude Code adapter 的真实错误返回非零。

**Architecture:** Quickstart 通过 `env -i` explicit allowlist 统一运行 clone、install、search，并以 ownership-verified temp root 和可重入 shutdown 状态机保证宿主不变。Python 侧新增只读 `adapter_health` 服务负责 AMH footprint 识别和结构化诊断，CLI doctor 只负责展示、输出裁剪和最终退出码。

**Tech Stack:** Bash、Python 3.11+、Typer/Rich、pytest、Git worktree。

---

## 工作区与文件边界

执行目录：

```bash
export HOTFIX_WORKTREE="${HOTFIX_WORKTREE:-/path/to/install-health-hotfix}"
export AMH_PYTHON="${AMH_PYTHON:-/path/to/repo-venv/bin/python}"
export AMH_RUFF="${AMH_RUFF:-/path/to/repo-venv/bin/ruff}"
cd "$HOTFIX_WORKTREE"
export PYTHONPATH="$PWD"
```

文件职责：

- Modify: `benchmarks/quickstart-60s.sh` — 只负责真实 quickstart 计时、隔离环境和阶段失败传播。
- Create: `tests/unit/test_quickstart_isolation.py` — 用最小 Git fixture 验证成功、失败、signal、`--keep` 和宿主不变性。
- Create: `agent_brain/platform/adapter_health.py` — 只读识别核心 adapter footprint、运行 adapter diagnose、裁剪诊断文本。
- Create: `tests/unit/test_adapter_health.py` — 覆盖 Codex/Claude 全部 footprint 入口、legacy、malformed-owned JSON 和 diagnose exception。
- Modify: `agent_brain/interfaces/cli/commands/doctor.py` — 移除手写 Claude 摘要，渲染结构化核心 adapter 健康度并计算退出码。
- Create: `tests/unit/test_cli_doctor_adapters.py` — 固定 warn/error、多 adapter、`--fix` 复诊、输出裁剪和 subprocess trust E2E。
- Modify: `tests/unit/test_p3_5_lowsev_fixes.py` — 把旧 Claude doctor 行契约迁移到新的 footprint/adapter 聚合语义，并隔离模块级配置路径。

不修改：

- `agent_brain/agent_integrations/codex_*` 的 trust 算法。
- `agent_brain/agent_integrations/claude_code*` 的 adapter 诊断规则。
- `codex/p0-injection-gateway` worktree 或 PR。
- 版本号、Release workflow 和正式发布资产。

### Task 1: Quickstart 宿主隔离与失败传播

**Files:**

- Create: `tests/unit/test_quickstart_isolation.py`
- Modify: `benchmarks/quickstart-60s.sh`

#### 2026-07-12 已批准架构修订（本节优先）

连续质量审查已经用真实探针证明 denylist 隔离无法闭合：`GIT_CONFIG_GLOBAL` 能注入外部 Hook，`PYTHONPATH` 能通过 `sitecustomize` 写宿主路径；连续信号和失败 `mktemp` 也暴露了独立的数据安全问题。用户已批准以下修订。本节覆盖 Task 1 初版 Step 1 中依赖 `FAKE_*` 透传的 fixture 控制方式，以及初版 Step 3 的 denylist、简单 signal trap 和无 ownership cleanup 示例；下方初版代码块仅保留为第一次 RED/GREEN 的审计记录，不得作为最终实现。

最终 `run_isolated` 必须使用显式 allowlist，代码形态如下；不允许追加通配的宿主变量：

```bash
run_isolated() {
  env -i \
    PATH="${PATH:-/usr/bin:/bin}" \
    LANG="${LANG:-}" \
    LC_ALL="${LC_ALL:-}" \
    LC_CTYPE="${LC_CTYPE:-}" \
    HTTP_PROXY="${HTTP_PROXY:-}" \
    HTTPS_PROXY="${HTTPS_PROXY:-}" \
    ALL_PROXY="${ALL_PROXY:-}" \
    NO_PROXY="${NO_PROXY:-}" \
    http_proxy="${http_proxy:-}" \
    https_proxy="${https_proxy:-}" \
    all_proxy="${all_proxy:-}" \
    no_proxy="${no_proxy:-}" \
    SSL_CERT_FILE="${SSL_CERT_FILE:-}" \
    SSL_CERT_DIR="${SSL_CERT_DIR:-}" \
    REQUESTS_CA_BUNDLE="${REQUESTS_CA_BUNDLE:-}" \
    CURL_CA_BUNDLE="${CURL_CA_BUNDLE:-}" \
    PIP_CERT="${PIP_CERT:-}" \
    GIT_SSL_CAINFO="${GIT_SSL_CAINFO:-}" \
    SSH_AUTH_SOCK="${SSH_AUTH_SOCK:-}" \
    HOME="$BENCH_HOME" \
    BRAIN_DIR="$BENCH_BRAIN" \
    AGENT_MEMORY_HUB_BIN="$BENCH_HOME/.local/bin" \
    AGENT_MEMORY_HUB_HOME="$CLONE_DIR" \
    TMPDIR="$BENCH_ROOT/tmp" \
    TMP="$BENCH_ROOT/tmp" \
    TEMP="$BENCH_ROOT/tmp" \
    TEMPDIR="$BENCH_ROOT/tmp" \
    XDG_CONFIG_HOME="$BENCH_ROOT/xdg-config" \
    XDG_CACHE_HOME="$BENCH_ROOT/cache" \
    XDG_DATA_HOME="$BENCH_ROOT/xdg-data" \
    XDG_STATE_HOME="$BENCH_ROOT/xdg-state" \
    PIP_CONFIG_FILE=/dev/null \
    PIP_CACHE_DIR="$BENCH_ROOT/cache/pip" \
    PYTHONUSERBASE="$BENCH_ROOT/pyuserbase" \
    PYTHONPYCACHEPREFIX="$BENCH_ROOT/pycache" \
    CARGO_HOME="$BENCH_ROOT/cargo" \
    CARGO_TARGET_DIR="$BENCH_ROOT/cargo-target" \
    RUSTUP_HOME="$BENCH_ROOT/rustup" \
    UV_CACHE_DIR="$BENCH_ROOT/uv-cache" \
    "$@"
}
```

Fixture 的 install/search/clone/signal/mktemp 行为必须在生成 fixture repo 或 PATH wrapper 时写死，不能依赖被 allowlist 拦截的 `FAKE_*`、`PYTHONPATH` 或 Git config 变量。外层恶意变量只用于证明 child 看不到它们。

临时根必须 fail-closed：

```bash
BENCH_ROOT=""
BENCH_ROOT_OWNED=0
OWNERSHIP_TOKEN="amh-quickstart:$$:${RANDOM}"

create_bench_root() {
  local candidate parent base marker
  candidate=$(mktemp -d "$EXPECTED_TMP_PARENT/amh-bench-XXXXXX") || return 1
  parent=$(cd "$(dirname "$candidate")" && pwd -P) || return 1
  base=$(basename "$candidate")
  [ "$parent" = "$EXPECTED_TMP_PARENT" ] || return 1
  case "$base" in amh-bench-*) ;; *) return 1 ;; esac
  marker="$candidate/.amh-quickstart-owned"
  (set -C; printf '%s\n' "$OWNERSHIP_TOKEN" > "$marker") || return 1
  BENCH_ROOT="$candidate"
  BENCH_ROOT_OWNED=1
}

cleanup() {
  [ "$KEEP" -eq 0 ] || return 0
  [ "$BENCH_ROOT_OWNED" -eq 1 ] || return 0
  owned_root_is_valid || return 0
  rm -rf -- "$BENCH_ROOT"
  BENCH_ROOT_OWNED=0
}
```

`owned_root_is_valid` 必须重新检查 canonical parent、`amh-bench-*` basename，以及 marker 内容与 `OWNERSHIP_TOKEN` 完全一致。失败的 `mktemp` stdout 只能留在局部 `candidate`，不能赋给 `BENCH_ROOT`，也不能进入 cleanup 删除路径。

signal teardown 必须跨信号可重入：

```bash
SHUTDOWN_IN_PROGRESS=0

handle_signal() {
  local exit_code="$1"
  if [ "$SHUTDOWN_IN_PROGRESS" -eq 1 ]; then
    return
  fi
  SHUTDOWN_IN_PROGRESS=1
  trap '' INT TERM
  stop_active_phase
  exit "$exit_code"
}

trap 'handle_signal 130' INT
trap 'handle_signal 143' TERM
```

`stop_active_phase` 必须验证 active PID 的实时 PGID 与记录值一致、拒绝向 benchmark 自身 PGID 发送 negative-PGID signal，并执行有界 TERM grace → 必要时 KILL 整组 → reap leader。第一次 signal 决定最终退出码；后续 signal 不能打断首次 teardown。

最终行为测试在原 11 条基础上至少新增：

- INT→TERM 与 TERM→INT，断言首个 signal 的 130/143、resistant leader/descendant 全消失。
- 失败 mktemp stdout 返回既存 sentinel 目录，断言目录和 sentinel 完全保留。
- `GIT_CONFIG_GLOBAL` 外部 hooksPath、`PYTHONPATH/sitecustomize.py`、`MEMORY_PYTHON`、`AGENT_MEMORY_HUB_PYTHON` 均不能在 clone/install/search child 中执行或写外部 sentinel。
- `--keep` + clone failure、`--keep` + install failure，断言 owned root 与完整 log 保留，并在 test `finally` 通过 ownership 边界安全删除。

- [ ] **Step 1: 写入 quickstart 的 RED 测试**

创建 `tests/unit/test_quickstart_isolation.py`，使用下面的完整 fixture 结构。fake installer 必须像真实安装器一样优先使用 `AGENT_MEMORY_HUB_BIN`，并在继承到 `PIP_TARGET` 时主动写 sentinel，从而证明修复前会逃逸：

```python
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
QUICKSTART = REPO_ROOT / "benchmarks" / "quickstart-60s.sh"


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "fixture-repo"
    (repo / "benchmarks").mkdir(parents=True)
    shutil.copy2(QUICKSTART, repo / "benchmarks" / "quickstart-60s.sh")
    (repo / "benchmarks" / "quickstart-60s.sh").chmod(0o755)
    _write_executable(
        repo / "install.sh",
        """#!/bin/sh
set -eu
bin_dir="${AGENT_MEMORY_HUB_BIN:-$HOME/.local/bin}"
mkdir -p "$bin_dir" "$HOME/.claude/commands" "$BRAIN_DIR/items" .venv/bin
printf 'remember fixture\n' > "$HOME/.claude/commands/remember.md"
printf '#!/bin/sh\nexit 0\n' > .venv/bin/memory
chmod +x .venv/bin/memory
cat > "$bin_dir/memory" <<EOF
#!/bin/sh
exec "$(pwd)/.venv/bin/memory" "\$@"
EOF
chmod +x "$bin_dir/memory"
if [ -n "${PIP_TARGET:-}" ]; then
  mkdir -p "$PIP_TARGET"
  printf 'escaped\n' > "$PIP_TARGET/quickstart-escaped.txt"
fi
if [ -n "${FAKE_READY_FILE:-}" ]; then
  mkdir -p "$(dirname "$FAKE_READY_FILE")"
  printf 'ready\n' > "$FAKE_READY_FILE"
fi
if [ "${FAKE_INSTALL_WAIT:-0}" = "1" ]; then
  while :; do sleep 1; done
fi
if [ "${FAKE_INSTALL_FAIL:-0}" = "1" ]; then
  exit 42
fi
""",
    )
    _write_executable(
        repo / "agent_runtime_kit" / "tools" / "search-memory.sh",
        """#!/bin/sh
set -eu
if [ "${FAKE_SEARCH_FAIL:-0}" = "1" ]; then
  echo 'forced search failure' >&2
  exit 43
fi
echo 'fixture search ok'
""",
    )
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=AMH Test",
            "-c",
            "user.email=amh-test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    return repo


def _manifest(*roots: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            key = f"{root.name}/{path.relative_to(root)}"
            if path.is_symlink():
                result[key] = f"symlink:{os.readlink(path)}"
            elif path.is_file():
                result[key] = hashlib.sha256(path.read_bytes()).hexdigest()
            elif path.is_dir():
                result[key] = "dir"
    return result


def _environment(tmp_path: Path) -> tuple[dict[str, str], tuple[Path, ...], Path]:
    host_home = tmp_path / "host-home"
    host_bin = tmp_path / "host-bin"
    host_pip_target = tmp_path / "host-pip-target"
    outer_tmp = tmp_path / "outer-tmp"
    for path in (host_home, host_bin, host_pip_target, outer_tmp):
        path.mkdir(parents=True)
    (host_home / "keep.txt").write_text("keep home\n", encoding="utf-8")
    (host_bin / "memory").write_text("host shim\n", encoding="utf-8")
    (host_pip_target / "keep.txt").write_text("keep pip\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(host_home),
            "TMPDIR": str(outer_tmp),
            "AGENT_MEMORY_HUB_BIN": str(host_bin),
            "PIP_TARGET": str(host_pip_target),
            "AMH_QUICKSTART_TARGET_SECONDS": "999",
        }
    )
    return env, (host_home, host_bin, host_pip_target), outer_tmp


def _run(repo: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(repo / "benchmarks" / "quickstart-60s.sh"), *args],
        cwd=repo,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )


def test_quickstart_keeps_host_state_unchanged(tmp_path: Path) -> None:
    repo = _fixture_repo(tmp_path)
    env, roots, outer_tmp = _environment(tmp_path)
    before = _manifest(*roots)

    result = _run(repo, env)

    assert result.returncode == 0, result.stdout
    assert "PASS" in result.stdout
    assert _manifest(*roots) == before
    assert not list(outer_tmp.glob("amh-bench-*"))


def test_quickstart_keep_contains_all_managed_outputs(tmp_path: Path) -> None:
    repo = _fixture_repo(tmp_path)
    env, roots, _outer_tmp = _environment(tmp_path)
    before = _manifest(*roots)

    result = _run(repo, env, "--keep")

    assert result.returncode == 0, result.stdout
    match = re.search(r"^Tmp: (.+)$", result.stdout, re.MULTILINE)
    assert match is not None, result.stdout
    bench_root = Path(match.group(1))
    try:
        shim = bench_root / "home" / ".local" / "bin" / "memory"
        assert shim.exists()
        target_match = re.search(r'exec "([^"]+)"', shim.read_text(encoding="utf-8"))
        assert target_match is not None
        target = Path(target_match.group(1))
        assert target.exists()
        assert bench_root in target.parents
        assert _manifest(*roots) == before
    finally:
        shutil.rmtree(bench_root, ignore_errors=True)


@pytest.mark.parametrize(
    ("flag", "message"),
    [
        ("FAKE_INSTALL_FAIL", "install.sh failed"),
        ("FAKE_SEARCH_FAIL", "first search failed"),
    ],
)
def test_quickstart_propagates_phase_failures_without_host_changes(
    tmp_path: Path,
    flag: str,
    message: str,
) -> None:
    repo = _fixture_repo(tmp_path)
    env, roots, outer_tmp = _environment(tmp_path)
    env[flag] = "1"
    before = _manifest(*roots)

    result = _run(repo, env)

    assert result.returncode != 0
    assert message in result.stdout
    assert "PASS" not in result.stdout
    assert _manifest(*roots) == before
    assert not list(outer_tmp.glob("amh-bench-*"))


@pytest.mark.parametrize("interrupt", [signal.SIGINT, signal.SIGTERM])
def test_quickstart_cleans_up_on_signals(
    tmp_path: Path,
    interrupt: signal.Signals,
) -> None:
    repo = _fixture_repo(tmp_path)
    env, roots, outer_tmp = _environment(tmp_path)
    ready = tmp_path / "control" / "ready"
    env.update({"FAKE_INSTALL_WAIT": "1", "FAKE_READY_FILE": str(ready)})
    before = _manifest(*roots)
    process = subprocess.Popen(
        [str(repo / "benchmarks" / "quickstart-60s.sh")],
        cwd=repo,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    deadline = time.monotonic() + 10
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert ready.exists(), "fake installer did not reach the signal checkpoint"

    os.killpg(process.pid, interrupt)
    stdout, _ = process.communicate(timeout=10)

    assert process.returncode != 0, stdout
    assert _manifest(*roots) == before
    assert not list(outer_tmp.glob("amh-bench-*"))
```

- [ ] **Step 2: 运行 RED 测试并记录精确失败**

Run:

```bash
PYTHONPATH="$PWD" "$AMH_PYTHON" -m pytest -p no:cacheprovider \
  tests/unit/test_quickstart_isolation.py -q
```

Expected：至少 `test_quickstart_keeps_host_state_unchanged` 因宿主 shim 被覆盖失败；search failure 用例因旧脚本错误输出 `PASS` 失败；signal/`--keep` 隔离断言失败。确认失败来自当前脚本行为，而不是 fixture 的 Git 初始化或 shell 语法。

- [ ] **Step 3（初版审计记录）: 用 sanitized environment 重写 quickstart**

下面代码记录第一次 RED 后的最小 GREEN，不是最终执行版本。最终实现必须以本 Task 顶部“2026-07-12 已批准架构修订”为准；三个阶段仍必须统一走 `run_isolated`，阶段命令先写日志，再有界输出，不能依赖可能吞掉退出码的 pipeline：

```bash
#!/usr/bin/env bash
#
# benchmarks/quickstart-60s.sh
#
# Measures: clean clone → minimal install (data dir + /remember + CLI) → first search.
# Default target: total < 120 seconds; override with AMH_QUICKSTART_TARGET_SECONDS.
#
# Usage:
#   ./benchmarks/quickstart-60s.sh [--keep]

set -uo pipefail

KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1

BENCH_ROOT=$(mktemp -d -t amh-bench-XXXXXX)
SOURCE_REPO=$(cd "$(dirname "$0")/.." && pwd)
BENCH_HOME="$BENCH_ROOT/home"
BENCH_BRAIN="$BENCH_ROOT/brain"
CLONE_DIR="$BENCH_ROOT/agent-memory-hub"

mkdir -p \
  "$BENCH_HOME" \
  "$BENCH_BRAIN" \
  "$BENCH_ROOT/cache/pip" \
  "$BENCH_ROOT/tmp" \
  "$BENCH_ROOT/xdg-config" \
  "$BENCH_ROOT/xdg-data" \
  "$BENCH_ROOT/xdg-state" \
  "$BENCH_ROOT/pyuserbase" \
  "$BENCH_ROOT/cargo" \
  "$BENCH_ROOT/rustup"

cleanup() {
  if [ "$KEEP" -eq 0 ]; then
    rm -rf "$BENCH_ROOT"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

run_isolated() (
  unset PIP_TARGET PIP_PREFIX PIP_ROOT PYTHONHOME VIRTUAL_ENV UV_PROJECT_ENVIRONMENT
  export HOME="$BENCH_HOME"
  export BRAIN_DIR="$BENCH_BRAIN"
  export AGENT_MEMORY_HUB_BIN="$BENCH_HOME/.local/bin"
  export AGENT_MEMORY_HUB_HOME="$CLONE_DIR"
  export TMPDIR="$BENCH_ROOT/tmp"
  export XDG_CONFIG_HOME="$BENCH_ROOT/xdg-config"
  export XDG_CACHE_HOME="$BENCH_ROOT/cache"
  export XDG_DATA_HOME="$BENCH_ROOT/xdg-data"
  export XDG_STATE_HOME="$BENCH_ROOT/xdg-state"
  export PIP_CONFIG_FILE=/dev/null
  export PIP_CACHE_DIR="$BENCH_ROOT/cache/pip"
  export PYTHONUSERBASE="$BENCH_ROOT/pyuserbase"
  export CARGO_HOME="$BENCH_ROOT/cargo"
  export RUSTUP_HOME="$BENCH_ROOT/rustup"
  "$@"
)

TARGET_SECONDS="${AMH_QUICKSTART_TARGET_SECONDS:-120}"
echo "=== Quickstart benchmark — target < ${TARGET_SECONDS}s ==="
echo "Tmp: $BENCH_ROOT"
echo "Source: $SOURCE_REPO"
echo ""

START=$(date +%s)

PHASE1_START=$(date +%s)
if ! run_isolated git clone --depth=1 "$SOURCE_REPO" "$CLONE_DIR" > "$BENCH_ROOT/clone.log" 2>&1; then
  echo "  ✗ clone failed:"
  tail -80 "$BENCH_ROOT/clone.log"
  exit 1
fi
tail -1 "$BENCH_ROOT/clone.log"
PHASE1=$(($(date +%s) - PHASE1_START))
echo "  ✓ clone: ${PHASE1}s"

PHASE2_START=$(date +%s)
if ! (cd "$CLONE_DIR" && run_isolated ./install.sh --minimal) > "$BENCH_ROOT/install.log" 2>&1; then
  echo "  ✗ install.sh failed:"
  tail -80 "$BENCH_ROOT/install.log"
  exit 1
fi
PHASE2=$(($(date +%s) - PHASE2_START))
echo "  ✓ install.sh: ${PHASE2}s"

PHASE3_START=$(date +%s)
if ! run_isolated "$CLONE_DIR/agent_runtime_kit/tools/search-memory.sh" "anything" > "$BENCH_ROOT/search.log" 2>&1; then
  echo "  ✗ first search failed:"
  tail -80 "$BENCH_ROOT/search.log"
  exit 1
fi
sed -n '1,3p' "$BENCH_ROOT/search.log"
PHASE3=$(($(date +%s) - PHASE3_START))
echo "  ✓ first search: ${PHASE3}s"

TOTAL=$(($(date +%s) - START))
echo ""
echo "=== Result: total = ${TOTAL}s (target: <${TARGET_SECONDS}s) ==="
echo "    breakdown: clone ${PHASE1}s + install ${PHASE2}s + search ${PHASE3}s"

if [ "$TOTAL" -lt "$TARGET_SECONDS" ]; then
  echo "✅ PASS"
  exit 0
fi

echo "❌ FAIL — over ${TARGET_SECONDS}s budget"
exit 1
```

- [ ] **Step 4: 运行 shell 语法和 GREEN 测试**

Run:

```bash
sh -n benchmarks/quickstart-60s.sh
PYTHONPATH="$PWD" "$AMH_PYTHON" -m pytest -p no:cacheprovider \
  tests/unit/test_quickstart_isolation.py -q
```

Expected：shell syntax exit 0；quickstart isolation 文件全部 PASS。若 signal case 偶发超时，先检查 parent/child process group 和 ready marker，不得通过放宽“宿主不变”或 cleanup 断言来掩盖。

- [ ] **Step 5: 运行一次真实 quickstart**

Run:

```bash
./benchmarks/quickstart-60s.sh
```

Expected：输出 clone/install/search 三段耗时和 `PASS`；退出后 `command -v memory` 与运行前一致，`~/.local/bin/memory` 内容哈希不变。

- [ ] **Step 6: 提交 Quickstart 修复**

```bash
git add benchmarks/quickstart-60s.sh tests/unit/test_quickstart_isolation.py
git diff --cached --check
git commit -m "fix: isolate quickstart benchmark state"
```

### Task 2: Core adapter footprint 与健康服务

**Files:**

- Create: `agent_brain/platform/adapter_health.py`
- Create: `tests/unit/test_adapter_health.py`

- [ ] **Step 1: 写入 footprint 与诊断聚合的 RED 测试**

创建 `tests/unit/test_adapter_health.py`。测试必须 patch adapter 模块级路径常量，不能只修改 `HOME`：

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_brain.agent_integrations import claude_code as cc_mod
from agent_brain.agent_integrations import codex as cx_mod
from agent_brain.agent_integrations.awareness import BEGIN as AWARENESS_BEGIN
from agent_brain.agent_integrations.codex_config import BEGIN as CODEX_BEGIN
from agent_brain.agent_integrations.codex_config import MCP_SECTION
from agent_brain.platform.adapter_health import (
    bounded_diagnostic_text,
    diagnose_configured_core_adapters,
    has_managed_footprint,
)


def _patch_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    paths = {
        "codex_agents": tmp_path / ".codex" / "AGENTS.md",
        "codex_hooks": tmp_path / ".codex" / "hooks.json",
        "codex_config": tmp_path / ".codex" / "config.toml",
        "claude_settings": tmp_path / ".claude" / "settings.json",
        "claude_awareness": tmp_path / ".claude" / "CLAUDE.md",
    }
    monkeypatch.setattr(cx_mod, "AGENTS_MD", paths["codex_agents"])
    monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", paths["codex_hooks"])
    monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", paths["codex_config"])
    monkeypatch.setattr(cc_mod, "SETTINGS_PATH", paths["claude_settings"])
    monkeypatch.setattr(cc_mod, "AWARENESS_PATH", paths["claude_awareness"])
    return paths


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.mark.parametrize("variant", ["agents", "mcp", "hook", "legacy_hook"])
def test_has_managed_footprint_codex_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    if variant == "agents":
        _write(paths["codex_agents"], f"{CODEX_BEGIN}\nmanaged\n")
    elif variant == "mcp":
        _write(paths["codex_config"], f"{MCP_SECTION}\ncommand = 'memory'\n")
    else:
        marker = "/brain/hooks/" if variant == "legacy_hook" else "/agent_runtime_kit/hooks/"
        payload = {"hooks": {"UserPromptSubmit": [{"hooks": [{"command": f"/repo{marker}inject-context.sh"}]}]}}
        _write(paths["codex_hooks"], json.dumps(payload))

    assert has_managed_footprint("codex") is True


@pytest.mark.parametrize("variant", ["awareness", "mcp", "hook", "legacy_hook"])
def test_has_managed_footprint_claude_code_variants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    if variant == "awareness":
        _write(paths["claude_awareness"], f"{AWARENESS_BEGIN}\nmanaged\n")
    elif variant == "mcp":
        _write(paths["claude_settings"], json.dumps({"mcpServers": {"agent-memory-hub": {"command": "memory"}}}))
    else:
        marker = "/brain/hooks/" if variant == "legacy_hook" else "/agent_runtime_kit/hooks/"
        payload = {"hooks": {"UserPromptSubmit": [{"hooks": [{"command": f"/repo{marker}inject-context.sh"}]}]}}
        _write(paths["claude_settings"], json.dumps(payload))

    assert has_managed_footprint("claude_code") is True


def test_has_managed_footprint_skips_non_amh_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(paths["codex_config"], "model = 'gpt-5'\n")
    _write(paths["claude_settings"], json.dumps({"hooks": {"Stop": []}}))

    assert has_managed_footprint("codex") is False
    assert has_managed_footprint("claude_code") is False


def test_malformed_owned_json_is_diagnosed_not_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    brain = tmp_path / "brain"
    (brain / "items").mkdir(parents=True)
    _write(paths["claude_settings"], '{"hooks": "/repo/agent_runtime_kit/hooks/inject-context.sh"')

    reports = diagnose_configured_core_adapters(brain)

    assert [report.adapter for report in reports] == ["claude_code"]
    assert reports[0].status == "error"
    assert any("malformed" in check.detail for check in reports[0].non_ok_checks)


def test_diagnose_exception_becomes_bounded_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _patch_paths(tmp_path, monkeypatch)
    _write(paths["codex_agents"], f"{CODEX_BEGIN}\nmanaged\n")
    brain = tmp_path / "brain"
    (brain / "items").mkdir(parents=True)

    class BrokenAdapter:
        def diagnose(self):
            raise RuntimeError("bad\x00" + "x" * 2000)

    from agent_brain.agent_integrations import registry

    original_get_adapter = registry.get_adapter
    monkeypatch.setattr(
        registry,
        "get_adapter",
        lambda name, brain_dir: BrokenAdapter() if name == "codex" else original_get_adapter(name, brain_dir),
    )

    reports = diagnose_configured_core_adapters(brain)

    assert reports[0].status == "error"
    detail = reports[0].non_ok_checks[0].detail
    assert "\x00" not in detail
    assert len(detail) <= 1200


def test_bounded_diagnostic_text_preserves_lines_and_limits_length() -> None:
    value = "line one\nline two\x00" + "z" * 2000

    result = bounded_diagnostic_text(value)

    assert result.startswith("line one\nline two")
    assert "\x00" not in result
    assert len(result) == 1200
    assert result.endswith("…")
```

- [ ] **Step 2: 运行 RED 测试**

Run:

```bash
PYTHONPATH="$PWD" "$AMH_PYTHON" -m pytest -p no:cacheprovider \
  tests/unit/test_adapter_health.py -q
```

Expected：collection 失败并报告 `ModuleNotFoundError: agent_brain.platform.adapter_health`。这证明测试在新模块出现前确实为红。

- [ ] **Step 3: 实现只读 adapter health 服务**

创建 `agent_brain/platform/adapter_health.py`：

```python
"""Read-only health aggregation for configured core hook adapters."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from agent_brain.agent_integrations.diagnostics import (
    AdapterDiagnosticCheck,
    CheckStatus,
)
from agent_brain.agent_integrations.hook_config import HUB_HOOK_DIR_MARKERS
from agent_brain.platform.install_repair import CORE_HOOK_ADAPTERS


DETAIL_LIMIT = 1200


@dataclass(frozen=True)
class CoreAdapterHealth:
    adapter: str
    status: CheckStatus
    non_ok_checks: tuple[AdapterDiagnosticCheck, ...]


def bounded_diagnostic_text(value: object, *, limit: int = DETAIL_LIMIT) -> str:
    text = str(value)
    cleaned = "".join(
        char if char in "\n\t" or char.isprintable() else " "
        for char in text
    )
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _contains_any(path: Path, markers: tuple[str, ...]) -> bool:
    content = _read_text(path)
    return any(marker in content for marker in markers)


def _contains_hub_hook(path: Path) -> bool:
    return _contains_any(path, HUB_HOOK_DIR_MARKERS)


def _json_mcp_footprint(path: Path, server_name: str) -> bool:
    content = _read_text(path)
    if not content:
        return False
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return '"mcpServers"' in content and f'"{server_name}"' in content
    servers = payload.get("mcpServers") if isinstance(payload, dict) else None
    return isinstance(servers, dict) and server_name in servers


def has_managed_footprint(adapter_name: str) -> bool:
    if adapter_name == "codex":
        from agent_brain.agent_integrations import codex as mod
        from agent_brain.agent_integrations.codex_config import BEGIN, END, MCP_SECTION

        return (
            _contains_any(mod.AGENTS_MD, (BEGIN, END))
            or _contains_hub_hook(mod.CODEX_HOOKS_JSON)
            or _contains_any(mod.CODEX_CONFIG_TOML, (MCP_SECTION,))
        )
    if adapter_name == "claude_code":
        from agent_brain.agent_integrations import claude_code as mod
        from agent_brain.agent_integrations.awareness import BEGIN, END

        return (
            _contains_any(mod.AWARENESS_PATH, (BEGIN, END))
            or _contains_hub_hook(mod.SETTINGS_PATH)
            or _json_mcp_footprint(mod.SETTINGS_PATH, mod.SERVER_NAME)
        )
    return False


def diagnose_configured_core_adapters(brain_dir: Path) -> tuple[CoreAdapterHealth, ...]:
    from agent_brain.agent_integrations import discover_adapters
    from agent_brain.agent_integrations.registry import get_adapter

    discover_adapters()
    results: list[CoreAdapterHealth] = []
    for adapter_name in CORE_HOOK_ADAPTERS:
        if not has_managed_footprint(adapter_name):
            continue
        try:
            report = get_adapter(adapter_name, brain_dir).diagnose()
        except Exception as exc:
            check = AdapterDiagnosticCheck(
                name=f"{adapter_name} adapter doctor",
                status="error",
                detail=bounded_diagnostic_text(exc),
                fix=f"run: memory adapter install {adapter_name}",
            )
            results.append(CoreAdapterHealth(adapter_name, "error", (check,)))
            continue
        non_ok = tuple(check for check in report.checks if check.status != "ok")
        results.append(CoreAdapterHealth(adapter_name, report.overall_status, non_ok))
    return tuple(results)


__all__ = [
    "CoreAdapterHealth",
    "bounded_diagnostic_text",
    "diagnose_configured_core_adapters",
    "has_managed_footprint",
]
```

- [ ] **Step 4: 运行 GREEN、Ruff 和现有 adapter 回归**

Run:

```bash
PYTHONPATH="$PWD" "$AMH_PYTHON" -m pytest -p no:cacheprovider \
  tests/unit/test_adapter_health.py \
  tests/unit/test_cli_adapter.py \
  tests/unit/test_adapters.py -q
"$AMH_RUFF" check agent_brain/platform/adapter_health.py tests/unit/test_adapter_health.py
```

Expected：测试全部 PASS，Ruff 输出 `All checks passed!`。若 `tests/unit/test_adapters.py` 发现真实配置泄漏，修复测试路径隔离，不得让 footprint helper写配置或吞异常。

- [ ] **Step 5: 提交 adapter health 服务**

```bash
git add agent_brain/platform/adapter_health.py tests/unit/test_adapter_health.py
git diff --cached --check
git commit -m "feat: diagnose configured core adapters"
```

### Task 3: General doctor 聚合、退出码和复诊

**Files:**

- Modify: `agent_brain/interfaces/cli/commands/doctor.py`
- Create: `tests/unit/test_cli_doctor_adapters.py`
- Modify: `tests/unit/test_p3_5_lowsev_fixes.py`
- Modify: `tests/unit/test_cli_smoke.py`

- [ ] **Step 1: 写入 doctor 聚合和 subprocess trust E2E 的 RED 测试**

创建 `tests/unit/test_cli_doctor_adapters.py`：

```python
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from typer.testing import CliRunner

from agent_brain.agent_integrations.diagnostics import AdapterDiagnosticCheck
from agent_brain.interfaces.cli import app
from agent_brain.platform.adapter_health import CoreAdapterHealth


REPO_ROOT = Path(__file__).resolve().parents[2]
runner = CliRunner()


def _brain(tmp_path: Path, monkeypatch) -> Path:
    brain = tmp_path / "brain"
    (brain / "items").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    return brain


def _health(adapter: str, status: str, detail: str) -> CoreAdapterHealth:
    checks = ()
    if status != "ok":
        checks = (
            AdapterDiagnosticCheck(
                name=f"{adapter} configuration",
                status=status,
                detail=detail,
                fix=f"run: memory adapter install {adapter}",
            ),
        )
    return CoreAdapterHealth(adapter=adapter, status=status, non_ok_checks=checks)


def _patch_health(monkeypatch, reports: tuple[CoreAdapterHealth, ...]) -> None:
    from agent_brain.platform import adapter_health

    monkeypatch.setattr(
        adapter_health,
        "diagnose_configured_core_adapters",
        lambda brain_dir: reports,
    )


def test_doctor_skips_unconfigured_core_adapters(tmp_path: Path, monkeypatch) -> None:
    _brain(tmp_path, monkeypatch)
    _patch_health(monkeypatch, ())

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "codex adapter" not in result.output
    assert "claude_code adapter" not in result.output


def test_doctor_warns_without_nonzero_exit(tmp_path: Path, monkeypatch) -> None:
    _brain(tmp_path, monkeypatch)
    _patch_health(monkeypatch, (_health("codex", "warn", "runtime not observed"),))

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "codex adapter" in result.output
    assert "WARN" in result.output
    assert "runtime not observed" in result.output


def test_doctor_fails_and_reports_all_configured_adapter_errors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _brain(tmp_path, monkeypatch)
    _patch_health(
        monkeypatch,
        (
            _health("codex", "error", "hook is not trusted"),
            _health("claude_code", "error", "missing hub hook"),
        ),
    )

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "codex adapter" in result.output
    assert "claude_code adapter" in result.output
    assert "hook is not trusted" in result.output
    assert "missing hub hook" in result.output


def test_doctor_fix_rediagnoses_before_success(tmp_path: Path, monkeypatch) -> None:
    _brain(tmp_path, monkeypatch)
    from agent_brain.platform import adapter_health
    from agent_brain.platform import install_repair

    monkeypatch.setattr(
        install_repair,
        "repair_installation",
        lambda brain_dir: [install_repair.RepairAction("memory CLI shim", "fixed", "fixed")],
    )
    monkeypatch.setattr(install_repair, "has_failures", lambda actions: False)
    calls = 0

    def diagnose(brain_dir):
        nonlocal calls
        calls += 1
        return (_health("codex", "error", "still not trusted"),)

    monkeypatch.setattr(adapter_health, "diagnose_configured_core_adapters", diagnose)

    result = runner.invoke(app, ["doctor", "--fix"])

    assert calls == 1
    assert result.exit_code == 1
    assert "still not trusted" in result.output


def test_doctor_bounds_adapter_and_repair_details(tmp_path: Path, monkeypatch) -> None:
    _brain(tmp_path, monkeypatch)
    from agent_brain.platform import install_repair

    long_detail = "bad\x00" + "x" * 3000
    monkeypatch.setattr(
        install_repair,
        "repair_installation",
        lambda brain_dir: [install_repair.RepairAction("installer", "error", long_detail)],
    )
    monkeypatch.setattr(install_repair, "has_failures", lambda actions: True)
    _patch_health(monkeypatch, (_health("codex", "error", long_detail),))

    result = runner.invoke(app, ["doctor", "--fix"])

    assert result.exit_code == 1
    assert "\x00" not in result.output
    assert long_detail not in result.output
    assert "…" in result.output


def _cli(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agent_brain.interfaces.cli", *args],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )


def test_general_doctor_matches_codex_trust_failure_in_fresh_process(tmp_path: Path) -> None:
    home = tmp_path / "home"
    brain = tmp_path / "brain"
    (brain / "items").mkdir(parents=True)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "BRAIN_DIR": str(brain),
            "AGENT_MEMORY_HUB_BIN": str(tmp_path / "bin"),
            "PYTHONPATH": str(REPO_ROOT),
            "MEMORY_HUB_TEST_EMBEDDING": "1",
        }
    )
    installed = _cli(env, "adapter", "install", "codex")
    assert installed.returncode == 0, installed.stdout

    hooks_path = home / ".codex" / "hooks.json"
    hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
    changed = False
    for entries in hooks["hooks"].values():
        for entry in entries:
            for hook in entry.get("hooks", []):
                command = str(hook.get("command", ""))
                if "inject-context.sh" in command:
                    hook["command"] = "AGENT_MEMORY_HUB_HOOK_TRACE_EMPTY=1 " + command
                    changed = True
    assert changed is True
    hooks_path.write_text(json.dumps(hooks, indent=2) + "\n", encoding="utf-8")

    adapter_bad = _cli(env, "adapter", "doctor", "codex", "--format", "json")
    general_bad = _cli(env, "doctor")
    assert adapter_bad.returncode == 1, adapter_bad.stdout
    assert general_bad.returncode == 1, general_bad.stdout
    assert "not trusted" in adapter_bad.stdout
    assert "not trusted" in general_bad.stdout

    repaired = _cli(env, "adapter", "install", "codex")
    adapter_good = _cli(env, "adapter", "doctor", "codex", "--format", "json")
    general_good = _cli(env, "doctor")
    assert repaired.returncode == 0, repaired.stdout
    assert adapter_good.returncode == 0, adapter_good.stdout
    assert general_good.returncode == 0, general_good.stdout
```

- [ ] **Step 2: 迁移旧 doctor 测试到新语义**

在 `tests/unit/test_p3_5_lowsev_fixes.py` 的 doctor 测试前增加模块路径隔离 helper，并让四个 doctor 测试调用它：

```python
def _isolate_core_adapter_paths(tmp_path, monkeypatch, home):
    from agent_brain.agent_integrations import claude_code as cc_mod
    from agent_brain.agent_integrations import codex as cx_mod

    codex_dir = home / ".codex"
    claude_dir = home / ".claude"
    monkeypatch.setattr(cx_mod, "AGENTS_MD", codex_dir / "AGENTS.md")
    monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", codex_dir / "hooks.json")
    monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", codex_dir / "config.toml")
    monkeypatch.setattr(cc_mod, "SETTINGS_PATH", claude_dir / "settings.json")
    monkeypatch.setattr(cc_mod, "AWARENESS_PATH", claude_dir / "CLAUDE.md")
```

对现有断言做以下精确迁移：

```python
# malformed 且没有 AMH marker：应跳过 Claude adapter，而不是显示旧 malformed 行。
assert result.exit_code == 0
assert "claude_code adapter" not in result.output

# 只有 7 个 AMH hooks、缺 awareness/MCP：属于 partial footprint，必须 error。
assert result.exit_code == 1
assert "claude_code adapter" in result.output
assert "ERROR" in result.output

# invalid search index 与 broken shim 保持历史退出码 0；只补路径隔离，不改断言。
```

`tests/unit/test_cli_smoke.py` 的 `seeded_brain` fixture 也必须 patch Codex/Claude 模块级路径到该测试自己的临时 HOME。普通 `doctor` smoke 不得读取开发者真实 `~/.codex` / `~/.claude`，否则本机 trust drift 会令测试非确定性失败。

- [ ] **Step 3: 运行 doctor RED 测试**

Run:

```bash
PYTHONPATH="$PWD" "$AMH_PYTHON" -m pytest -p no:cacheprovider \
  tests/unit/test_cli_doctor_adapters.py \
  tests/unit/test_p3_5_lowsev_fixes.py \
  tests/unit/test_update_repair_cli.py -q
```

Expected：新测试因 `memory doctor` 尚未调用 adapter health、尚未返回 1、尚未裁剪详情而失败；旧测试迁移后也应暴露当前手写 Claude 行冲突。

- [ ] **Step 4: 修改 CLI doctor 聚合结构化 adapter health**

在 `agent_brain/interfaces/cli/commands/doctor.py` 中完成以下精确改动：

1. 删除从 `settings_path = Path.home() / ".claude" / "settings.json"` 开始，到旧 `Claude Code settings/hooks/MCP server` 三行结束的整段手写检查。
2. repair table 的 action name/detail 改用 `bounded_diagnostic_text(...)`，并包装成 literal Rich `Text`；不能把 action/exception 中的方括号解析为 markup。
3. 在 CLI shim 检查后调用 `diagnose_configured_core_adapters(brain)`，把每个结果追加到总表。
4. 表格输出后打印每个 non-ok check 的有界 name/detail/fix，并关闭 Rich markup。
5. `repair_failed` 或任一 adapter status 为 `error` 时退出 1；warn 不改变退出码。

在 `doctor()` 函数内部导入模块，使测试 monkeypatch 和运行时复诊始终读取同一函数对象：

```python
from agent_brain.platform import adapter_health
from rich.text import Text
```

repair row：

```python
repair_table.add_row(
    Text(adapter_health.bounded_diagnostic_text(action.name)),
    f"[{style}]{status}[/{style}]",
    Text(adapter_health.bounded_diagnostic_text(action.detail)),
)
```

在构造主表前聚合：

```python
adapter_reports = adapter_health.diagnose_configured_core_adapters(brain)
for health in adapter_reports:
    value = "configured" if not health.non_ok_checks else f"{len(health.non_ok_checks)} non-ok check(s)"
    checks.append((f"{health.adapter} adapter", value, health.status.upper()))
```

在主表和通过数量后输出详情：

```python
for health in adapter_reports:
    if not health.non_ok_checks:
        continue
    console.print(f"\nAdapter details: {health.adapter}", style="bold", markup=False)
    for check in health.non_ok_checks:
        console.print(
            f"- {adapter_health.bounded_diagnostic_text(check.name)}: "
            f"{adapter_health.bounded_diagnostic_text(check.detail)}",
            markup=False,
        )
        if check.fix:
            console.print(
                f"  fix: {adapter_health.bounded_diagnostic_text(check.fix)}",
                markup=False,
            )

adapter_failed = any(health.status == "error" for health in adapter_reports)
if repair_failed or adapter_failed:
    raise typer.Exit(1)
```

- [ ] **Step 5: 运行 doctor GREEN、相邻回归和 Ruff**

Run:

```bash
PYTHONPATH="$PWD" "$AMH_PYTHON" -m pytest -p no:cacheprovider \
  tests/unit/test_cli_doctor_adapters.py \
  tests/unit/test_adapter_health.py \
  tests/unit/test_p3_5_lowsev_fixes.py \
  tests/unit/test_cli_smoke.py \
  tests/unit/test_update_repair_cli.py \
  tests/unit/test_cli_adapter.py -q
"$AMH_RUFF" check \
  agent_brain/platform/adapter_health.py \
  agent_brain/interfaces/cli/commands/doctor.py \
  tests/unit/test_adapter_health.py \
  tests/unit/test_cli_doctor_adapters.py \
  tests/unit/test_p3_5_lowsev_fixes.py \
  tests/unit/test_cli_smoke.py
```

Expected：全部测试 PASS；Ruff 输出 `All checks passed!`。subprocess E2E 必须证明 trust drift 时两条 doctor 都退出 1，重新 install 后都恢复非 error。

- [ ] **Step 6: 提交 doctor 聚合修复**

```bash
git add \
  agent_brain/interfaces/cli/commands/doctor.py \
  tests/unit/test_cli_doctor_adapters.py \
  tests/unit/test_p3_5_lowsev_fixes.py \
  tests/unit/test_cli_smoke.py
git diff --cached --check
git commit -m "fix: surface core adapter failures in doctor"
```

### Task 4: 全量验证、dogfood 只读验收与 PR 准备

**Files:**

- Verify only; only edit files when a failing check identifies a defect within this spec.

- [ ] **Step 1: 检查分支范围和提交结构**

Run:

```bash
git status --short
git log --oneline --decorate main..HEAD
git diff --stat main...HEAD
git diff --check main...HEAD
```

Expected：只有设计文档和本计划、quickstart、adapter health、doctor 及对应测试；无 `findings.md`、`progress.md`、`task_plan.md`，无 Gateway 文件。

- [ ] **Step 2: 运行完整 focused suite**

Run:

```bash
PYTHONPATH="$PWD" "$AMH_PYTHON" -m pytest -p no:cacheprovider \
  tests/unit/test_quickstart_isolation.py \
  tests/unit/test_adapter_health.py \
  tests/unit/test_cli_doctor_adapters.py \
  tests/unit/test_p3_5_lowsev_fixes.py \
  tests/unit/test_update_repair_cli.py \
  tests/unit/test_cli_adapter.py \
  tests/unit/test_adapters.py -q
```

Expected：全部 PASS，无真实 HOME/config 泄漏。

- [ ] **Step 3: 运行真实 quickstart 和全仓回归**

Run:

```bash
before_memory=$(command -v memory)
before_shim=$(shasum -a 256 "$HOME/.local/bin/memory" 2>/dev/null || true)
./benchmarks/quickstart-60s.sh
after_memory=$(command -v memory)
after_shim=$(shasum -a 256 "$HOME/.local/bin/memory" 2>/dev/null || true)
test "$before_memory" = "$after_memory"
test "$before_shim" = "$after_shim"
PYTHONPATH="$PWD" PYTHONDONTWRITEBYTECODE=1 "$AMH_PYTHON" -m pytest -p no:cacheprovider tests/ -q
```

Expected：真实 quickstart PASS；shim 路径与哈希不变；全仓 pytest PASS。若全仓出现与本分支无关的环境性失败，保存精确命令和错误证据，不得改动无关模块。

- [ ] **Step 4: 运行静态检查**

Run:

```bash
sh -n benchmarks/quickstart-60s.sh
"$AMH_RUFF" check \
  agent_brain/platform/adapter_health.py \
  agent_brain/interfaces/cli/commands/doctor.py \
  tests/unit/test_quickstart_isolation.py \
  tests/unit/test_adapter_health.py \
  tests/unit/test_cli_doctor_adapters.py \
  tests/unit/test_p3_5_lowsev_fixes.py
git diff --check main...HEAD
```

Expected：全部 exit 0。

- [ ] **Step 5: 在真实 dogfood 安装上做只读验收**

使用真实安装入口检查真实安装路径；hotfix worktree 的行为由前面的隔离 HOME subprocess E2E 覆盖。不要通过 `PYTHONPATH` 强制让另一个 worktree 的 adapter 实例检查主仓安装，因为 adapter trust/diagnose 会有意把“配置指向不同 checkout”报告为 error：

```bash
command -v memory
memory doctor
memory adapter doctor codex --format json
memory adapter doctor claude_code --format json
```

Expected：真实安装入口存在；总 doctor 通过；Codex、Claude Code adapter doctor 均非 error。该步骤禁止执行 `--fix`，避免用验收动作修改真实配置。若需要验证 hotfix 总 doctor 的新聚合输出，必须在临时 HOME 中安装同一 hotfix checkout 后再运行，不能把跨 checkout mismatch 当作用户安装损坏。

- [ ] **Step 6: 最终代码审查和 PR 准备**

使用 `superpowers:requesting-code-review` 对 `main...HEAD` 做规格一致性与安全审查。审查通过后：

```bash
git status --short
git log --oneline main..HEAD
git push -u origin codex/install-health-hotfix
```

随后使用 GitHub publish workflow 创建 Draft PR，标题：

```text
fix: isolate quickstart and surface adapter health
```

PR body 必须列出：两个根因、兼容边界、RED/GREEN 证据、全仓结果、真实 quickstart 结果，以及“未包含 Injection Gateway、未发布 Release”。

## 完成定义

- [ ] 三个实现提交均可独立解释和回滚。
- [ ] Quickstart 成功、install/search failure、`INT`、`TERM`、`--keep` 全部有自动化证据。
- [ ] 宿主 `HOME`、自定义 `AGENT_MEMORY_HUB_BIN`、外部 `PIP_TARGET` 均不被修改。
- [ ] `env -i` allowlist 阻断外部 Git Hook、sitecustomize 和 host Python override；未回退为 denylist。
- [ ] 连续交叉 signal 不能中断首次 teardown；首个 signal 决定退出码且无 resistant descendant。
- [ ] 失败 mktemp stdout 无权触发删除；cleanup 只删除 canonical parent/prefix/marker/token 全部匹配的 owned root。
- [ ] `--keep` 在成功与 clone/install failure 下都保留 owned root 和完整日志，并由测试 finally 安全清理。
- [ ] Codex/Claude 全部 current/legacy footprint 入口和 malformed-owned JSON 均有测试。
- [ ] General doctor 对 adapter warn 退出 0、对任一 configured adapter error 退出 1。
- [ ] `doctor --fix` 在复诊仍 error 时退出 1。
- [ ] subprocess E2E 证明 Codex trust drift 与重新 install 的完整闭环。
- [ ] Focused、全仓 pytest、Ruff、shell syntax、`git diff --check` 全绿。
- [ ] 分支已推送并创建独立 Draft PR；未合并、未创建 Release。
