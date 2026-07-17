# Hook Preflight Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 UserPromptSubmit hook 的重复 Python 预处理进程合并为一次轻量 payload 解析和一次 AMH preflight，同时完整保留证据采集、安全门禁与异常兼容路径，并稳定通过 2 秒单次性能门禁。

**Architecture:** 新增无 AMH 依赖的 NUL 分帧 payload parser，以及只编排现有领域函数的 `hook_preflight` 模块。`inject-context.sh` 正常路径消费两个固定版本协议；任一协议失败时执行原有多进程预处理回退，搜索、Gateway、timeout 和 adapter envelope 不变。

**Tech Stack:** Bash 3.2-compatible shell、Python 3.10+、pytest、Typer/现有 AMH contracts、JSON/NUL-framed subprocess protocol、现有 hook benchmark runner。

---

## 文件职责与变更地图

- Create: `agent_runtime_kit/tools/parse-hook-payload.py` — 只负责无依赖解析 hook JSON，并输出固定 NUL 字段。
- Create: `agent_brain/memory/evidence/hook_preflight.py` — 编排 runtime event、live prompt、multimodal capture、normalization 与 recall enrichment。
- Create: `tests/unit/test_hook_payload_parser.py` — payload parser 协议、恶意边界与字段类型测试。
- Create: `tests/unit/test_hook_preflight.py` — preflight 领域函数编排、失败隔离、隐私与协议测试。
- Modify: `agent_runtime_kit/hooks/inject-context.sh` — 接入 fast path，保留原逻辑为异常 fallback。
- Modify: `tests/unit/test_adapter_runtime_events.py` — 正常/回退真实 hook 证据与多模态回归。
- Modify: `tests/unit/test_prompt_injection_gateway_contract.py` — 静态授权链与无 `eval` 合同。
- Modify: `tests/unit/test_docs_truth_contract.py` — 性能 run history、固定 commit 与发布状态事实源。
- Modify: `docs/evaluation/dual-route-hook-benchmark-report.json` — 记录历史失败和优化后连续确认轮。
- Modify: `docs/evaluation/dual-route-release-readiness.zh.md` — 更新性能、校准和升级边界。
- Modify: `docs/architecture.md`、`CHANGELOG.md` — 描述 preflight 与最终 release gate。

### Task 1: 建立无依赖 payload parser

**Files:**
- Create: `agent_runtime_kit/tools/parse-hook-payload.py`
- Create: `tests/unit/test_hook_payload_parser.py`

- [ ] **Step 1: 写正常协议和恶意输入失败测试**

```python
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
PARSER = ROOT / "agent_runtime_kit/tools/parse-hook-payload.py"


def _run(payload: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(PARSER)],
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_payload_parser_emits_versioned_nul_fields_without_shell_code(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    prompt = f"$(touch {marker}) ' quoted"
    completed = _run(json.dumps({
        "prompt": prompt,
        "session_id": "session-1",
        "cwd": "/repo/current",
        "hook_event_name": "UserPromptSubmit",
    }).encode())

    assert completed.returncode == 0
    assert completed.stderr == b""
    assert completed.stdout.split(b"\0") == [
        b"amh-hook-payload-v1",
        prompt.encode(),
        b"session-1",
        b"/repo/current",
        b"UserPromptSubmit",
        b"",
    ]
    assert not marker.exists()


@pytest.mark.parametrize(
    "payload",
    [
        b"not-json",
        b"[]",
        json.dumps({"prompt": "bad\u0000prompt"}).encode(),
    ],
)
def test_payload_parser_rejects_malformed_non_object_and_nul(payload: bytes) -> None:
    completed = _run(payload)

    assert completed.returncode == 2
    assert completed.stdout == b""


def test_payload_parser_normalizes_non_string_fields() -> None:
    completed = _run(json.dumps({
        "prompt": 42,
        "session_id": None,
        "cwd": ["unsafe"],
        "hook_event_name": False,
    }).encode())

    assert completed.returncode == 0
    assert completed.stdout.split(b"\0") == [
        b"amh-hook-payload-v1", b"", b"", b"", b"UserPromptSubmit", b""
    ]
```

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q tests/unit/test_hook_payload_parser.py
```

Expected: FAIL，原因是 `parse-hook-payload.py` 不存在。

- [ ] **Step 3: 实现固定 NUL 协议 parser**

```python
#!/usr/bin/env python3
"""Parse one hook payload without importing Agent Memory Hub."""

from __future__ import annotations

import json
import sys


PROTOCOL_VERSION = "amh-hook-payload-v1"


def _field(payload: dict[str, object], name: str, default: str = "") -> str:
    value = payload.get(name)
    return value if isinstance(value, str) else default


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return 2
    if not isinstance(payload, dict):
        return 2
    fields = (
        PROTOCOL_VERSION,
        _field(payload, "prompt"),
        _field(payload, "session_id"),
        _field(payload, "cwd"),
        _field(payload, "hook_event_name", "UserPromptSubmit"),
    )
    if any("\0" in value for value in fields):
        return 2
    sys.stdout.buffer.write(b"\0".join(value.encode("utf-8") for value in fields) + b"\0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行 parser 测试、Ruff 与语法检查**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q tests/unit/test_hook_payload_parser.py
.venv/bin/ruff check agent_runtime_kit/tools/parse-hook-payload.py tests/unit/test_hook_payload_parser.py
.venv/bin/python -m py_compile agent_runtime_kit/tools/parse-hook-payload.py
```

Expected: 全部通过。

- [ ] **Step 5: 提交 parser**

```bash
git add agent_runtime_kit/tools/parse-hook-payload.py tests/unit/test_hook_payload_parser.py
git commit -m "perf: parse hook payload once"
```

### Task 2: 建立单进程 AMH preflight

**Files:**
- Create: `agent_brain/memory/evidence/hook_preflight.py`
- Create: `tests/unit/test_hook_preflight.py`

- [ ] **Step 1: 写领域编排与协议 RED tests**

```python
from __future__ import annotations

import json
from pathlib import Path

from agent_brain.agent_integrations.runtime_events import iter_runtime_events
from agent_brain.memory.evidence.conversation_store import ConversationStore


def test_preflight_writes_runtime_and_live_prompt_and_emits_fixed_protocol(tmp_path: Path) -> None:
    from agent_brain.memory.evidence.hook_preflight import run_hook_preflight, serialize_result

    payload = {
        "prompt": "  hooks   recall  ",
        "session_id": "session-1",
        "cwd": "/repo/current",
        "hook_event_name": "UserPromptSubmit",
    }
    result = run_hook_preflight(payload, brain_dir=tmp_path, adapter="codex")

    assert result.normalized_prompt == "hooks recall"
    assert result.multimodal_recall_text == ""
    assert result.multimodal_gap_json == ""
    assert serialize_result(result).split(b"\0") == [
        b"amh-hook-preflight-v1", b"hooks recall", b"", b"", b""
    ]
    events = list(iter_runtime_events(tmp_path, adapter="codex"))
    assert len(events) == 1
    messages = list(ConversationStore(tmp_path).iter_messages())
    assert [message.content_text for message in messages] == ["hooks   recall"]


def test_preflight_attachment_returns_recall_text_not_gap(tmp_path: Path) -> None:
    from agent_brain.memory.evidence.hook_preflight import run_hook_preflight

    image = tmp_path / "screen.png"
    image.write_bytes(b"PNG")
    result = run_hook_preflight({
        "prompt": "[Image #1] 帮我看看",
        "session_id": "session-mm",
        "images": [{
            "name": "[Image #1]",
            "path": str(image),
            "caption": "截图显示 version.json API_URL failed",
        }],
    }, brain_dir=tmp_path, adapter="codex")

    assert "version.json API_URL failed" in result.multimodal_recall_text
    assert result.multimodal_gap_json == ""


def test_preflight_isolates_evidence_write_failures(monkeypatch, tmp_path: Path) -> None:
    from agent_brain.memory.evidence import hook_preflight

    def fail(*_args, **_kwargs):
        raise OSError("synthetic evidence failure")

    monkeypatch.setattr(hook_preflight, "record_runtime_event", fail)
    monkeypatch.setattr(hook_preflight, "capture_prompt_payload", fail)
    result = hook_preflight.run_hook_preflight(
        {"prompt": "  hooks recall  "},
        brain_dir=tmp_path,
        adapter="codex",
    )

    assert result.normalized_prompt == "hooks recall"
```

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q tests/unit/test_hook_preflight.py
```

Expected: FAIL，原因是 `hook_preflight` 模块不存在。

- [ ] **Step 3: 实现 preflight 模块与 CLI 协议**

```python
"""Consolidated evidence and recall preprocessing for prompt hooks."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import Any

from agent_brain.agent_integrations.runtime_events import record_runtime_event
from agent_brain.memory.context.prompt_normalization import normalize_hook_prompt_for_recall
from agent_brain.memory.evidence.hook_capture import capture_prompt_payload
from agent_brain.memory.evidence.multimodal_capture import (
    capture_multimodal_prompt_resources,
    multimodal_gap_payload_for_payload,
    recall_text_for_payload,
)


PROTOCOL_VERSION = "amh-hook-preflight-v1"


@dataclass(frozen=True)
class HookPreflightResult:
    normalized_prompt: str
    multimodal_recall_text: str
    multimodal_gap_json: str


def run_hook_preflight(
    payload: dict[str, Any],
    *,
    brain_dir: Path,
    adapter: str,
) -> HookPreflightResult:
    prompt = payload.get("prompt") if isinstance(payload.get("prompt"), str) else ""
    session_id = payload.get("session_id") if isinstance(payload.get("session_id"), str) else None
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
    event_name = (
        payload.get("hook_event_name")
        if isinstance(payload.get("hook_event_name"), str)
        else "UserPromptSubmit"
    )
    try:
        record_runtime_event(
            brain_dir,
            adapter=adapter,
            event_name=event_name,
            session_id=session_id,
            cwd=cwd,
        )
    except Exception:  # noqa: BLE001 - runtime evidence is fail-open
        pass
    try:
        capture_prompt_payload({**payload, "adapter": adapter}, root_dir=brain_dir)
    except Exception:  # noqa: BLE001 - conversation evidence is fail-open
        try:
            capture_multimodal_prompt_resources(payload, root_dir=brain_dir)
        except Exception:  # noqa: BLE001 - resource evidence is fail-open
            pass
    normalized = normalize_hook_prompt_for_recall(prompt)
    try:
        recall_text = recall_text_for_payload(payload, root_dir=brain_dir)
    except Exception:  # noqa: BLE001 - multimodal enrichment is fail-open
        recall_text = ""
    try:
        gap = multimodal_gap_payload_for_payload(payload, root_dir=brain_dir)
    except Exception:  # noqa: BLE001 - multimodal gap evidence is fail-open
        gap = None
    gap_json = (
        json.dumps(gap, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if gap is not None
        else ""
    )
    return HookPreflightResult(normalized, recall_text, gap_json)


def serialize_result(result: HookPreflightResult) -> bytes:
    fields = (
        PROTOCOL_VERSION,
        result.normalized_prompt,
        result.multimodal_recall_text,
        result.multimodal_gap_json,
    )
    if any("\0" in value for value in fields):
        raise ValueError("preflight fields must not contain NUL")
    return b"\0".join(value.encode("utf-8") for value in fields) + b"\0"


def _load_payload() -> dict[str, Any]:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise ValueError("hook payload must be an object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brain-dir", type=Path, required=True)
    parser.add_argument("--adapter", default=os.environ.get("AGENT_MEMORY_HUB_ADAPTER", "unknown"))
    args = parser.parse_args(argv)
    try:
        result = run_hook_preflight(
            _load_payload(),
            brain_dir=args.brain_dir,
            adapter=args.adapter,
        )
        sys.stdout.buffer.write(serialize_result(result))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["HookPreflightResult", "main", "run_hook_preflight", "serialize_result"]
```

- [ ] **Step 4: 增加 malformed/NUL/4000 字符边界测试并运行 GREEN**

```python
def test_preflight_serialization_rejects_nul() -> None:
    import pytest
    from agent_brain.memory.evidence.hook_preflight import HookPreflightResult, serialize_result

    with pytest.raises(ValueError, match="NUL"):
        serialize_result(HookPreflightResult("bad\0prompt", "", ""))
```

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/unit/test_hook_preflight.py \
  tests/unit/test_multimodal_hook_capture.py
.venv/bin/ruff check agent_brain/memory/evidence/hook_preflight.py tests/unit/test_hook_preflight.py
```

Expected: 全部通过；现有 multimodal 单元测试无回归。

- [ ] **Step 5: 提交 preflight 核心**

```bash
git add agent_brain/memory/evidence/hook_preflight.py tests/unit/test_hook_preflight.py
git commit -m "perf: consolidate hook evidence preflight"
```

### Task 3: 接入 hook fast path 与异常 fallback

**Files:**
- Modify: `agent_runtime_kit/hooks/inject-context.sh`
- Modify: `tests/unit/test_adapter_runtime_events.py`
- Modify: `tests/unit/test_prompt_injection_gateway_contract.py`

- [ ] **Step 1: 写静态 fast-path 合同和真实 fallback RED tests**

在 `tests/unit/test_prompt_injection_gateway_contract.py` 增加：

```python
def test_hook_uses_versioned_preflight_without_eval():
    source = (ROOT / "agent_runtime_kit/hooks/inject-context.sh").read_text(encoding="utf-8")

    assert "parse-hook-payload.py" in source
    assert "agent_brain.memory.evidence.hook_preflight" in source
    assert "amh-hook-payload-v1" in source
    assert "amh-hook-preflight-v1" in source
    assert not re.search(r"(^|[;\s])eval([;\s]|$)", source)
```

在 `tests/unit/test_adapter_runtime_events.py` 增加：

```python
def test_preflight_process_failure_falls_back_to_existing_evidence_path(tmp_path):
    from agent_brain.agent_integrations.runtime_events import iter_runtime_events
    from agent_brain.memory.evidence.conversation_store import ConversationStore

    root = Path(__file__).resolve().parents[2]
    script = root / "agent_runtime_kit/hooks/inject-context.sh"
    wrapper = tmp_path / "verified-python"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        "for arg in \"$@\"; do\n"
        "  if [ \"$arg\" = agent_brain.memory.evidence.hook_preflight ]; then exit 91; fi\n"
        "done\n"
        f"exec {sys.executable} \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    (tmp_path / "items").mkdir()
    payload = {
        "prompt": "preflight fallback evidence sentinel",
        "session_id": "preflight-fallback-session",
        "cwd": "/repo/current",
        "hook_event_name": "UserPromptSubmit",
    }
    completed = subprocess.run(
        ["/bin/bash", str(script)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "BRAIN_DIR": str(tmp_path),
            "AGENT_MEMORY_HUB_ADAPTER": "codex",
            "AGENT_MEMORY_HUB_PYTHON": str(wrapper),
            "MEMORY_HUB_TEST_EMBEDDING": "1",
            "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
            "PYTHONPATH": str(root),
        },
    )

    assert completed.returncode == 0, completed.stderr
    assert len(list(iter_runtime_events(tmp_path, adapter="codex"))) == 1
    messages = list(ConversationStore(tmp_path).iter_messages())
    assert [message.content_text for message in messages] == [payload["prompt"]]
```

- [ ] **Step 2: 运行新增测试确认 RED**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/unit/test_prompt_injection_gateway_contract.py::test_hook_uses_versioned_preflight_without_eval \
  tests/unit/test_adapter_runtime_events.py -k 'preflight or raw_prompt or multimodal'
```

Expected: 静态合同 FAIL；fallback 新测试 FAIL。

- [ ] **Step 3: 将四次初始 JSON 解析替换为 parser 协议**

在 `inject-context.sh` 定义：

```bash
PAYLOAD_PARSER="$HUB_CODE_DIR/tools/parse-hook-payload.py"
```

用下面区块替换现有 prompt/session/cwd/event 四次解析：

```bash
INPUT=$(cat)
HOOK_FIELDS=()
while IFS= read -r -d '' field; do
  HOOK_FIELDS+=("$field")
done < <(printf '%s' "$INPUT" | python3 "$PAYLOAD_PARSER" 2>/dev/null)
if [ "${#HOOK_FIELDS[@]}" -ne 5 ] || [ "${HOOK_FIELDS[0]}" != "amh-hook-payload-v1" ]; then
  echo '{}'
  exit 0
fi
PROMPT="${HOOK_FIELDS[1]}"
SESSION_ID="${HOOK_FIELDS[2]}"
CWD="${HOOK_FIELDS[3]}"
HOOK_EVENT_NAME="${HOOK_FIELDS[4]}"
```

- [ ] **Step 4: 把原预处理移动到 `run_legacy_preflight`**

函数只在 consolidated preflight 协议失败时执行，并完整保留原调用：

```bash
run_legacy_preflight() {
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
  if [ -n "$PROMPT" ] && [ -n "${MEMORY_PYTHON:-}" ]; then
    NORMALIZED_PROMPT=$(printf '%s' "$PROMPT" | \
      "$MEMORY_PYTHON" -m agent_brain.memory.context.prompt_normalization \
      2>/dev/null || true)
    [ -z "$NORMALIZED_PROMPT" ] || RECALL_PROMPT="$NORMALIZED_PROMPT"
    MULTIMODAL_RECALL_TEXT=$(printf '%s' "$INPUT" | \
      "$MEMORY_PYTHON" -m agent_brain.memory.evidence.multimodal_capture recall-text \
      2>/dev/null || true)
    MULTIMODAL_GAP_JSON=$(printf '%s' "$INPUT" | \
      "$MEMORY_PYTHON" -m agent_brain.memory.evidence.multimodal_capture gap-json \
      2>/dev/null || true)
    if [ -n "$MULTIMODAL_RECALL_TEXT" ]; then
      RECALL_PROMPT="${RECALL_PROMPT}"$'\n'"${MULTIMODAL_RECALL_TEXT}"
    fi
  fi
}
```

- [ ] **Step 5: 接入 consolidated preflight 正常路径**

在 Python resolver 成功后读取固定字段：

```bash
PREFLIGHT_FIELDS=()
while IFS= read -r -d '' field; do
  PREFLIGHT_FIELDS+=("$field")
done < <(
  printf '%s' "$INPUT" | "$MEMORY_PYTHON" -m agent_brain.memory.evidence.hook_preflight \
    --brain-dir "$BRAIN_DIR" \
    --adapter "${AGENT_MEMORY_HUB_ADAPTER:-unknown}" \
    2>/dev/null
)
RECALL_PROMPT="$PROMPT"
MULTIMODAL_GAP_JSON=""
if [ "${#PREFLIGHT_FIELDS[@]}" -eq 4 ] \
  && [ "${PREFLIGHT_FIELDS[0]}" = "amh-hook-preflight-v1" ]; then
  [ -z "${PREFLIGHT_FIELDS[1]}" ] || RECALL_PROMPT="${PREFLIGHT_FIELDS[1]}"
  if [ -n "${PREFLIGHT_FIELDS[2]}" ]; then
    RECALL_PROMPT="${RECALL_PROMPT}"$'\n'"${PREFLIGHT_FIELDS[2]}"
  fi
  MULTIMODAL_GAP_JSON="${PREFLIGHT_FIELDS[3]}"
else
  run_legacy_preflight
fi
```

保留 gap query hash、dynamic top-k、debug diagnostics、search timeout、stdout cap 和最终
adapter-envelope parser 原样。

- [ ] **Step 6: 运行 hook 合同、Bash syntax 与 shellcheck 范围检查**

Run:

```bash
bash -n agent_runtime_kit/hooks/inject-context.sh
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/unit/test_adapter_runtime_events.py \
  tests/unit/test_prompt_injection_gateway_contract.py \
  tests/unit/test_write_shim_fallback.py \
  tests/unit/test_adapters.py
```

Expected: 全部通过；preflight 正常与 fallback 均保留证据。

- [ ] **Step 7: 提交 hook 接线**

```bash
git add agent_runtime_kit/hooks/inject-context.sh \
  tests/unit/test_adapter_runtime_events.py \
  tests/unit/test_prompt_injection_gateway_contract.py
git commit -m "perf: route hook preprocessing through preflight"
```

### Task 4: 完整功能、安全与回滚回归

**Files:**
- Modify: `tests/unit/test_adapter_runtime_events.py`（仅在缺失 case 时）
- Modify: `tests/unit/test_routed_cli.py`（仅在 degraded Hindi case 需适配时）

- [ ] **Step 1: 跑关键功能矩阵并记录任何真实失败**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/unit/test_hook_payload_parser.py \
  tests/unit/test_hook_preflight.py \
  tests/unit/test_multimodal_hook_capture.py \
  tests/unit/test_adapter_runtime_events.py \
  tests/unit/test_routed_cli.py \
  tests/unit/test_recall_admission.py \
  tests/unit/test_routed_retrieval.py \
  tests/unit/test_routed_answerability.py \
  tests/unit/test_injection_gateway.py \
  tests/system/test_dual_route_recall_matrix.py
```

Expected: 全部通过；41-case 为 0 FP / 0 FN，Hindi degraded hook 注入目标。

- [ ] **Step 2: 跑 feature-off、timeout、stdout cap 与 descendant cleanup 定向测试**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q tests/unit/test_adapter_runtime_events.py \
  -k 'rollback or timeout or stdout or descendant or malformed or empty or preflight'
```

Expected: 全部通过，feature-off 只改变 candidate generation，Gateway 与 preflight 仍启用。

- [ ] **Step 3: 运行 lint、静态旁路和 diff check**

Run:

```bash
.venv/bin/ruff check agent_brain agent_runtime_kit tests scripts
bash -n agent_runtime_kit/hooks/inject-context.sh
rg -n 'AGENT_MEMORY_HUB_RAW_QUERY|brief[[:space:]]*\|\||3[-–]5 keywords|no matches' \
  agent_runtime_kit agent_brain tests docs
rg -n 'TODO|TBD|FIXME|XXX' \
  agent_brain/memory/evidence/hook_preflight.py \
  agent_runtime_kit/tools/parse-hook-payload.py \
  agent_runtime_kit/hooks/inject-context.sh \
  tests/unit/test_hook_payload_parser.py \
  tests/unit/test_hook_preflight.py
git diff --check
```

Expected: Ruff/Bash/diff 全绿；第一组只命中已有明确 legacy/test/docs 边界；第二组无命中。

- [ ] **Step 4: 提交必要的回归修正**

仅当 Step 1–3 为修复真实回归修改了测试或实现时提交：

```bash
git add agent_brain agent_runtime_kit tests
git commit -m "test: harden consolidated hook preflight"
```

如果 worktree clean，不创建空提交。

### Task 5: 固定 candidate 并连续运行两轮正式性能门禁

**Files:**
- No code changes before both benchmark rounds complete.

- [ ] **Step 1: 冻结 clean candidate commit**

Run:

```bash
git status --short
git rev-parse HEAD
```

Expected: status 为空；保存精确 HEAD 为 `CANDIDATE_COMMIT`。benchmark 期间禁止改文件。

- [ ] **Step 2: 对每一轮创建全新公开 fixture brain**

两轮分别执行，`BRAIN` 必须是尚不存在的路径：

```bash
PYTHONPATH="$CAND" "$PY" "$CAND/scripts/materialize-dual-route-hook-benchmark.py" \
  --brain-dir "$BRAIN"
```

Expected: `fixture_id=dual-route-hook-public-v1`、`item_count=1`；重复路径必须失败。

- [ ] **Step 3: 运行第一轮正式 30-run**

```bash
PYTHONPATH="$CAND" "$PY" "$CAND/scripts/benchmark-dual-route-hook.py" \
  --old-command "$OLD" \
  --new-command "$NEW" \
  --payload "$PAYLOAD" \
  --protocol adapter-envelope \
  --context-sentinel "$CONTEXT_SENTINEL" \
  --repeats 30 \
  --warmup 3 \
  --min-samples 30 \
  --timeout-seconds 5
```

Expected: exit 0；new 30 samples、0 errors、0 timeouts、max < 2000ms、p95 delta <= 150ms。

- [ ] **Step 4: 使用第二个全新 brain 重复正式 30-run**

Run: 与 Step 3 完全相同，只替换为第二个全新 `BRAIN`。

Expected: 再次 exit 0 且满足同一硬门槛。任一轮失败立即停止发布收口，保留 BLOCKED，回到
Task 3 做根因分析；不得运行第三轮覆盖失败。

### Task 6: 更新机器报告、发布文档并完成全仓验收

**Files:**
- Modify: `docs/evaluation/dual-route-hook-benchmark-report.json`
- Modify: `tests/unit/test_docs_truth_contract.py`
- Modify: `docs/evaluation/dual-route-release-readiness.zh.md`
- Modify: `docs/architecture.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: 先写 run-history 与 release PASS 文档合同测试**

在 `tests/unit/test_docs_truth_contract.py` 更新断言：

```python
assert report["performance_gate"] == "PASS"
assert report["overall_release_gate"] == "PASS"
assert report["blocking_calibration_case"] is None
candidate_commit = report["provenance"]["candidate_commit"]
assert re.fullmatch(r"[0-9a-f]{40}", candidate_commit)
subprocess.run(
    ["git", "cat-file", "-e", f"{candidate_commit}^{{commit}}"],
    cwd=ROOT,
    check=True,
    capture_output=True,
)
confirmations = [
    run for run in report["run_history"]
    if run["phase"] == "post_optimization_confirmation"
]
assert len(confirmations) == 2
assert all(run["result"]["passed"] for run in confirmations)
assert all(run["result"]["new"]["max_ms"] < 2000 for run in confirmations)
assert any(
    run["phase"] == "pre_optimization_blocker"
    and not run["result"]["passed"]
    for run in report["run_history"]
)
```

同时要求 architecture、CHANGELOG、release-readiness 均写明 preflight、连续两轮门禁和
旧用户 refresh/repair，不再声称 `multi-hi-08` 或性能为当前 blocker。

- [ ] **Step 2: 运行文档合同确认 RED**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q tests/unit/test_docs_truth_contract.py
```

Expected: FAIL，旧报告仍为 BLOCKED 且无新 run history。

- [ ] **Step 3: 用精确 benchmark 输出更新机器报告**

报告必须包含：

- `candidate_commit` 为 Task 5 冻结的精确 clean commit；
- hook、runner、materializer、payload、fixture item 的 SHA-256；
- `run_history` 保留 `895e472` 的 2400.187ms 与 2081.018ms 失败轮；
- 两个 `post_optimization_confirmation` 保存 Task 5 的原始聚合数字；
- 不保存命令绝对临时路径、raw prompt、context 或 hook stdout；
- 只有两轮确认都通过时 `performance_gate` 与 `overall_release_gate` 才为 `PASS`，
  `blocking_calibration_case` 为 `null`。

- [ ] **Step 4: 更新架构、发布准备与 Changelog**

文档写明：

- preflight 合并的是进程，不合并/删除证据语义；
- preflight 整体失败才回退旧多进程路径；
- Gateway、2 秒搜索预算、stdout cap、descendant cleanup、feature-off 边界不变；
- calibration heldout 为 11/11、0 FP/0 FN；
- 连续两轮正式性能门禁的精确 p50/p95/max；
- 旧用户必须升级 package 并 refresh/repair adapter。

- [ ] **Step 5: 运行文档合同、targeted suite 与全仓测试**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q tests/unit/test_docs_truth_contract.py
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/unit/test_hook_payload_parser.py \
  tests/unit/test_hook_preflight.py \
  tests/unit/test_adapter_runtime_events.py \
  tests/unit/test_prompt_injection_gateway_contract.py \
  tests/unit/test_routed_cli.py \
  tests/system/test_dual_route_recall_matrix.py
ulimit -n 8192
PYTHONPATH=. .venv/bin/python -m pytest -q
.venv/bin/ruff check agent_brain agent_runtime_kit tests scripts
bash -n agent_runtime_kit/hooks/inject-context.sh
git diff --check
```

Expected: 全仓通过；只允许既有显式 skip，不允许 failure/error；lint、Bash 和 diff 全绿。

- [ ] **Step 6: 提交发布证据**

```bash
git add docs/evaluation/dual-route-hook-benchmark-report.json \
  docs/evaluation/dual-route-release-readiness.zh.md \
  docs/architecture.md \
  CHANGELOG.md \
  tests/unit/test_docs_truth_contract.py
git commit -m "docs: publish consolidated hook release evidence"
```

- [ ] **Step 7: 独立规格与质量审查**

先对照 `2026-07-18-hook-preflight-consolidation-design.md` 审查 spec compliance，再做代码质量、
安全、兼容性与测试证据审查。任何 Critical/Important 问题必须修复并重新运行受影响测试；
修复后重新审查，不以作者自述替代证据。

- [ ] **Step 8: 最终状态与 GitHub 发布前检查**

Run:

```bash
git status --short --branch
git log --oneline --decorate -12
git diff --stat origin/codex/p0-injection-gateway...HEAD
git rev-list --left-right --count origin/codex/p0-injection-gateway...HEAD
```

Expected: worktree clean；最终交付列出校准指标、两轮性能数字、全仓测试数、skip 数、审查结论
和精确 commits。仅在这些证据全部满足后推送当前分支到 GitHub。
