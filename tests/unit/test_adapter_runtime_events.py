from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_brain.agent_integrations.registry import get_adapter
from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def _vision_ocr_available() -> bool:
    try:
        import objc  # noqa: F401

        namespace = {}
        objc.loadBundle("Vision", namespace, bundle_path="/System/Library/Frameworks/Vision.framework")
        return "VNRecognizeTextRequest" in namespace
    except Exception:
        return False


def _write_ocr_fixture(path: Path) -> None:
    import pytest

    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    image = Image.new("RGB", (900, 220), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 80), "version.json API_URL failed", fill="black")
    image.save(path)


def _write_pdf_fixture(path: Path, text: str) -> None:
    canvas_module = pytest.importorskip("reportlab.pdfgen.canvas")
    canvas = canvas_module.Canvas(str(path))
    canvas.drawString(72, 720, text)
    canvas.save()


def test_record_runtime_event_appends_bounded_mechanical_fact(tmp_path):
    from agent_brain.agent_integrations.runtime_events import (
        iter_runtime_events,
        record_runtime_event,
        runtime_events_path,
    )

    event = record_runtime_event(
        tmp_path,
        adapter="codex",
        event_name="UserPromptSubmit",
        session_id="sess-1",
        cwd="/repo",
        source="hook",
        now=datetime(2026, 6, 9, 9, 0, tzinfo=timezone.utc),
    )

    assert event.adapter == "codex"
    assert event.event_name == "UserPromptSubmit"
    assert event.session_id == "sess-1"
    assert event.cwd == "/repo"
    assert event.source == "hook"

    path = runtime_events_path(tmp_path)
    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert row == event.to_dict()
    assert "prompt" not in row
    assert "body" not in row

    assert [item.to_dict() for item in iter_runtime_events(tmp_path)] == [event.to_dict()]


def test_runtime_summary_is_adapter_scoped_and_uses_latest_event(tmp_path):
    from agent_brain.agent_integrations.runtime_events import record_runtime_event, runtime_event_summary

    record_runtime_event(
        tmp_path,
        adapter="claude_code",
        event_name="SessionStart",
        session_id="old",
        now=datetime(2026, 6, 9, 9, 0, tzinfo=timezone.utc),
    )
    record_runtime_event(
        tmp_path,
        adapter="codex",
        event_name="UserPromptSubmit",
        session_id="latest",
        now=datetime(2026, 6, 9, 10, 0, tzinfo=timezone.utc),
    )

    summary = runtime_event_summary(tmp_path, "codex")

    assert summary.observed is True
    assert summary.count == 1
    assert summary.last_event is not None
    assert summary.last_event["event_name"] == "UserPromptSubmit"
    assert summary.last_event["session_id"] == "latest"


def test_capability_projection_reports_runtime_observation_without_promoting_support_level(tmp_path):
    from agent_brain.agent_integrations.capabilities import capability_for_adapter
    from agent_brain.agent_integrations.runtime_events import record_runtime_event

    record_runtime_event(
        tmp_path,
        adapter="codex",
        event_name="UserPromptSubmit",
        session_id="sess-verified-by-hook",
        now=datetime(2026, 6, 9, 10, 0, tzinfo=timezone.utc),
    )

    cap = capability_for_adapter("codex", get_adapter("codex", tmp_path))

    assert cap.support_level == "install-ready"
    assert cap.runtime_observed is True
    assert cap.runtime_event_count == 1
    assert cap.last_runtime_event is not None
    assert cap.last_runtime_event["event_name"] == "UserPromptSubmit"


def test_adapter_verification_record_promotes_verified_capability(tmp_path):
    from agent_brain.agent_integrations.capabilities import capability_for_adapter
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.agent_integrations.verifications import record_adapter_verification

    record_runtime_event(
        tmp_path,
        adapter="codex",
        event_name="UserPromptSubmit",
        session_id="sess-runtime",
        now=datetime(2026, 6, 21, 10, 0, tzinfo=timezone.utc),
    )
    record_adapter_verification(
        tmp_path,
        adapter="codex",
        status="passed",
        verifier="pytest",
        evidence=["memory adapter doctor codex --format json"],
        note="doctor passed and runtime hook event observed",
        now=datetime(2026, 6, 21, 10, 5, tzinfo=timezone.utc),
    )

    cap = capability_for_adapter("codex", get_adapter("codex", tmp_path))

    assert cap.support_level == "verified"
    assert cap.evidence_level == "verified"
    assert cap.verified is True
    assert cap.verification_status == "verified"
    assert cap.verification_blockers == []
    assert "memory adapter doctor codex --format json" in cap.evidence_paths


def test_runtime_diagnostic_reports_ok_after_adapter_event(tmp_path):
    from agent_brain.agent_integrations.diagnostics import diagnose_runtime_evidence
    from agent_brain.agent_integrations.runtime_events import record_runtime_event

    missing = diagnose_runtime_evidence(
        brain_dir=tmp_path,
        adapter="codex",
        check_name="Codex runtime evidence",
    )
    assert missing.status == "warn"

    record_runtime_event(
        tmp_path,
        adapter="codex",
        event_name="UserPromptSubmit",
        session_id="sess-runtime",
        now=datetime(2026, 6, 9, 10, 0, tzinfo=timezone.utc),
    )

    observed = diagnose_runtime_evidence(
        brain_dir=tmp_path,
        adapter="codex",
        check_name="Codex runtime evidence",
    )
    assert observed.status == "ok"
    assert "observed 1 runtime event" in observed.detail


def test_record_runtime_event_shell_wrapper_uses_brain_dir_env(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "tools" / "record-runtime-event.sh"

    result = subprocess.run(
        [
            "bash",
            str(script),
            "--adapter",
            "codex",
            "--event",
            "SessionStart",
            "--session",
            "sess-from-hook",
            "--cwd",
            "/repo",
        ],
        env={**os.environ, "BRAIN_DIR": str(tmp_path)},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["adapter"] == "codex"
    assert data["event_name"] == "SessionStart"

    from agent_brain.agent_integrations.runtime_events import runtime_event_summary

    summary = runtime_event_summary(tmp_path, "codex")
    assert summary.observed is True
    assert summary.count == 1


def test_session_start_hook_records_runtime_event_when_adapter_env_is_set(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-discipline.sh"
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
    }

    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "session_id": "hook-session",
            "cwd": "/repo",
            "hook_event_name": "SessionStart",
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "hookSpecificOutput" in payload
    assert payload["suppressOutput"] is True
    context = payload["hookSpecificOutput"]["additionalContext"]
    assert "Agent Memory Hub is active" in context
    assert "# Agent Memory Discipline" not in context

    from agent_brain.agent_integrations.runtime_events import runtime_event_summary

    summary = runtime_event_summary(tmp_path, "codex")
    assert summary.observed is True
    assert summary.count == 1
    assert summary.last_event is not None
    assert summary.last_event["event_name"] == "SessionStart"
    assert summary.last_event["session_id"] == "hook-session"


def test_user_prompt_hook_records_injection_cohort_when_context_is_injected(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    store = ItemsStore(tmp_path / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_path / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260611-140000-python-hook-context",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="Python hook context",
        summary="Python hook context",
    )
    body = "Python hook context body"
    store.write(item, body)
    idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()

    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT": "json",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }
    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": "Python hook context",
            "session_id": "hook-session",
            "cwd": "/repo",
            "hook_event_name": "UserPromptSubmit",
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "Python hook context" in payload["hookSpecificOutput"]["additionalContext"]

    from agent_brain.memory.context.injection_cohorts import latest_injection_cohort

    cohort = latest_injection_cohort(tmp_path, adapter="codex", session_id="hook-session")
    assert cohort is not None
    assert cohort.item_ids == (item.id,)
    assert cohort.cwd == "/repo"
    assert cohort.pack_metrics is not None
    assert "items" not in cohort.pack_metrics
    assert cohort.pack_metrics["included_count"] == 1
    assert sum(cohort.pack_metrics["selected_views"].values()) == 1
    assert isinstance(cohort.pack_metrics["compressed_count"], int)
    assert cohort.pack_metrics["packed_tokens"] > 0
    assert item.id not in repr(cohort.pack_metrics)
    assert item.title not in repr(cohort.pack_metrics)
    assert item.summary not in repr(cohort.pack_metrics)
    assert body not in repr(cohort.pack_metrics)


def test_user_prompt_hook_debug_mode_reports_query_signal_when_blocked(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT": "json",
        "AGENT_MEMORY_HUB_DEBUG_QUERY_SIGNAL": "1",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }
    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": "继续",
            "session_id": "hook-debug-blocked",
            "cwd": "/repo",
            "hook_event_name": "UserPromptSubmit",
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    context = payload["hookSpecificOutput"]["additionalContext"]
    assert "<agent_brain_diagnostics>" in context
    assert "decision: block" in context
    assert "reason: too_weak" in context
    assert "weak_noise: 继续" in context


def test_user_prompt_hook_trace_empty_reports_triggered_keywords_without_injection(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    (tmp_path / "items").mkdir()
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT": "json",
        "AGENT_MEMORY_HUB_HOOK_TRACE_EMPTY": "1",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }

    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": (
                "新增这两个接口的理由给我，因为我之前理解既然只是扩展了数据结构 "
                '{"defaultSceneIdentify":"ZTJD","sceneEvaluationType":{"ZTJD":"quantitative"},'
                '"serviceQualityScoreConfig":{"ZTJD":{"convertToPercentage":true}}} '
                "不应该是复用原来的接口吗"
            ),
            "session_id": "hook-empty-trace-session",
            "cwd": "/repo",
            "hook_event_name": "UserPromptSubmit",
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    context = payload["hookSpecificOutput"]["additionalContext"]
    assert "<agent_brain_diagnostics>" in context
    assert "hook: triggered" in context
    assert "decision: no_injection" in context
    assert "reason: search_no_context" in context
    assert "keywords: 新增接口|复用接口|数据结构" in context
    assert "servicequalityscoreconfig" in context
    assert "<agent_brain>" not in context


def test_user_prompt_hook_can_emit_plain_context_for_qoder(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    store = ItemsStore(tmp_path / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_path / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260629-150000-python-hook-context",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="Python hook context",
        summary="Python hook context should be raw stdout",
    )
    body = "Python hook context body"
    store.write(item, body)
    idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()

    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "qoder",
        "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT": "plain",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }
    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": "Python hook context",
            "session_id": "qoder-plain-context-session",
            "cwd": "/repo",
            "hook_event_name": "UserPromptSubmit",
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("<agent_brain>")
    assert "Python hook context" in result.stdout
    assert "hookSpecificOutput" not in result.stdout


def test_user_prompt_hook_fails_open_when_search_times_out_without_external_timeout(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    runtime = tmp_path / "runtime" / "agent_runtime_kit"
    hooks_dir = runtime / "hooks"
    tools_dir = runtime / "tools"
    bin_dir = tmp_path / "bin"
    hooks_dir.mkdir(parents=True)
    tools_dir.mkdir(parents=True)
    bin_dir.mkdir()
    shutil.copy2(repo / "agent_runtime_kit" / "hooks" / "inject-context.sh", hooks_dir / "inject-context.sh")
    (bin_dir / "python3").symlink_to(Path(sys.executable))
    for command in ("dirname", "cat"):
        command_path = shutil.which(command)
        assert command_path is not None
        (bin_dir / command).symlink_to(Path(command_path))
    (tools_dir / "_resolve-python.sh").write_text(
        f"""#!/usr/bin/env bash
MEMORY_PYTHON="{sys.executable}"
_PYTHON_OK=0
memory_cli() {{
  "$MEMORY_PYTHON" -m agent_brain.interfaces.cli "$@"
}}
""",
        encoding="utf-8",
    )
    (tools_dir / "record-runtime-event.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (tools_dir / "search-memory.sh").write_text(
        "#!/bin/sh\n/bin/sleep 15\necho 'unexpected slow search output'\n",
        encoding="utf-8",
    )
    for script in tools_dir.iterdir():
        script.chmod(0o755)

    brain_dir = tmp_path / "brain"
    env = {
        **os.environ,
        "PATH": str(bin_dir),
        "PYTHONPATH": f"{repo}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
        "BRAIN_DIR": str(brain_dir),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
        "AGENT_MEMORY_HUB_SEARCH_TIMEOUT_SECONDS": "1",
    }

    started = time.monotonic()
    result = subprocess.run(
        ["/bin/bash", str(hooks_dir / "inject-context.sh")],
        input=json.dumps({
            "prompt": "Python hook context",
            "session_id": "hook-search-timeout-session",
            "cwd": "/repo/current",
            "hook_event_name": "UserPromptSubmit",
        }),
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}
    assert elapsed < 6, (
        "hook should fail open before the 15s search command completes; "
        f"elapsed={elapsed:.2f}s"
    )
    rows = [
        json.loads(line)
        for line in (brain_dir / "runtime" / "hook-latency.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert rows[-1]["stage"] == "search_memory"
    assert rows[-1]["status"] == "timeout"
    assert rows[-1]["adapter"] == "codex"
    assert rows[-1]["session_id"] == "hook-search-timeout-session"


def test_user_prompt_hook_never_injects_partial_stdout_from_failed_search(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    runtime = tmp_path / "runtime" / "agent_runtime_kit"
    hooks_dir = runtime / "hooks"
    tools_dir = runtime / "tools"
    bin_dir = tmp_path / "bin"
    hooks_dir.mkdir(parents=True)
    tools_dir.mkdir(parents=True)
    bin_dir.mkdir()
    shutil.copy2(
        repo / "agent_runtime_kit" / "hooks" / "inject-context.sh",
        hooks_dir / "inject-context.sh",
    )
    (bin_dir / "python3").symlink_to(Path(sys.executable))
    for command in ("dirname", "cat", "head", "grep"):
        command_path = shutil.which(command)
        assert command_path is not None
        (bin_dir / command).symlink_to(Path(command_path))
    (tools_dir / "_resolve-python.sh").write_text(
        f'''#!/usr/bin/env bash
MEMORY_PYTHON="{sys.executable}"
_PYTHON_OK=0
memory_cli() {{
  "$MEMORY_PYTHON" -m agent_brain.interfaces.cli "$@"
}}
''',
        encoding="utf-8",
    )
    (tools_dir / "record-runtime-event.sh").write_text(
        "#!/bin/sh\nexit 0\n",
        encoding="utf-8",
    )
    malicious = "MALICIOUS_PARTIAL_PRIVATE_MEMORY_BODY"
    (tools_dir / "search-memory.sh").write_text(
        f"#!/bin/sh\necho '{malicious}'\nexit 1\n",
        encoding="utf-8",
    )
    for script in tools_dir.iterdir():
        script.chmod(0o755)

    env = {
        **os.environ,
        "PATH": str(bin_dir),
        "PYTHONPATH": f"{repo}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
        "BRAIN_DIR": str(tmp_path / "brain"),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT": "json",
        "AGENT_MEMORY_HUB_HOOK_TRACE_EMPTY": "1",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }

    result = subprocess.run(
        ["/bin/bash", str(hooks_dir / "inject-context.sh")],
        input=json.dumps({
            "prompt": "Python hook context",
            "session_id": "hook-search-error-session",
            "cwd": "/repo/current",
            "hook_event_name": "UserPromptSubmit",
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert malicious not in result.stdout
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "decision: no_injection" in context
    assert "reason: search_error" in context
    runtime_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "brain" / "runtime").glob("*.jsonl")
    )
    assert malicious not in runtime_text


def test_user_prompt_hook_external_timeout_wrapper_drops_failed_search_stdout(
    tmp_path,
):
    repo = Path(__file__).resolve().parents[2]
    runtime = tmp_path / "runtime" / "agent_runtime_kit"
    hooks_dir = runtime / "hooks"
    tools_dir = runtime / "tools"
    bin_dir = tmp_path / "bin"
    hooks_dir.mkdir(parents=True)
    tools_dir.mkdir(parents=True)
    bin_dir.mkdir()
    shutil.copy2(
        repo / "agent_runtime_kit" / "hooks" / "inject-context.sh",
        hooks_dir / "inject-context.sh",
    )
    (bin_dir / "python3").symlink_to(Path(sys.executable))
    for command in ("dirname", "cat", "head", "grep"):
        command_path = shutil.which(command)
        assert command_path is not None
        (bin_dir / command).symlink_to(Path(command_path))
    (bin_dir / "timeout").write_text(
        "#!/bin/sh\nshift\nexec \"$@\"\n",
        encoding="utf-8",
    )
    (tools_dir / "_resolve-python.sh").write_text(
        f'''#!/usr/bin/env bash
MEMORY_PYTHON="{sys.executable}"
_PYTHON_OK=0
memory_cli() {{
  "$MEMORY_PYTHON" -m agent_brain.interfaces.cli "$@"
}}
''',
        encoding="utf-8",
    )
    (tools_dir / "record-runtime-event.sh").write_text(
        "#!/bin/sh\nexit 0\n",
        encoding="utf-8",
    )
    sentinel = "EXTERNAL_TIMEOUT_PARTIAL_PRIVATE_MEMORY_BODY"
    (tools_dir / "search-memory.sh").write_text(
        f"#!/bin/sh\necho '{sentinel}'\nexit 2\n",
        encoding="utf-8",
    )
    for script in (*tools_dir.iterdir(), bin_dir / "timeout"):
        script.chmod(0o755)

    brain_dir = tmp_path / "brain"
    env = {
        **os.environ,
        "PATH": str(bin_dir),
        "PYTHONPATH": f"{repo}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
        "BRAIN_DIR": str(brain_dir),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT": "json",
        "AGENT_MEMORY_HUB_HOOK_TRACE_EMPTY": "1",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }

    result = subprocess.run(
        ["/bin/bash", str(hooks_dir / "inject-context.sh")],
        input=json.dumps({
            "prompt": "Python external timeout wrapper boundary",
            "session_id": "hook-external-search-error-session",
            "cwd": "/repo/current",
            "hook_event_name": "UserPromptSubmit",
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert sentinel not in result.stdout
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "reason: search_error" in context
    runtime_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (brain_dir / "runtime").glob("*.jsonl")
    )
    assert sentinel not in runtime_text


def test_user_prompt_hook_uses_sanitized_qoderwork_prompt_for_recall(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    store = ItemsStore(tmp_path / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_path / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260622-104510-wukong-linux-realtime-render-f",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="悟空适配 Linux realtime render fix package 20260622",
        summary="悟空适配 Linux realtime remote-task render fix package",
        tags=["wukong", "linux", "realtime"],
    )
    body = "悟空适配 Linux realtime render fix package and curl bash entry."
    store.write(item, body)
    idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()

    prompt = """悟空适配Linux

<system-reminder>
The user is using QoderWork.
Available MCP servers:
Available tools:
Current workspace may include AIagent/alpha and other directories.
</system-reminder>
"""
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "qoder_work",
        "AGENT_MEMORY_HUB_SEARCH_TIMEOUT_SECONDS": "5",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }
    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": prompt,
            "session_id": "qoderwork-mixed-prompt-session",
            "cwd": "<repo>",
            "hook_event_name": "UserPromptSubmit",
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "悟空适配 Linux realtime render fix package" in context
    assert "keywords: 悟空适配|linux" in context
    assert "system-reminder" not in context
    assert "alpha" not in context.lower()


def test_user_prompt_hook_injects_known_short_project_entity_for_qoderwork(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    store = ItemsStore(tmp_path / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_path / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260627-180100-alpha-known-project",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="Alpha全国推荐能力",
        summary="Alpha项目支持全国推荐和数据健康页",
        tags=["Alpha", "alpha"],
    )
    body = "Alpha项目支持全国推荐、数据健康页和分省 readiness。"
    store.write(item, body)
    idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()

    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "qoder_work",
        "AGENT_MEMORY_HUB_SEARCH_TIMEOUT_SECONDS": "5",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }
    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": "Alpha",
            "session_id": "qoderwork-alpha-short-entity-session",
            "cwd": "~/.qoderwork/workspace/example",
            "hook_event_name": "UserPromptSubmit",
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "Alpha全国推荐能力" in context
    assert "keywords: alpha" in context
    assert "For terse project/name prompts" in context
    assert "answer with what the candidates establish first" in context
    assert "answer from the injected pack first" in context
    assert "problem -> fix -> evidence/verification -> remaining boundary" in context

    from agent_brain.memory.context.injection_cohorts import latest_injection_cohort
    from agent_brain.memory.governance.recall_events import iter_gap_records

    cohort = latest_injection_cohort(
        tmp_path,
        adapter="qoder_work",
        session_id="qoderwork-alpha-short-entity-session",
    )
    assert cohort is not None
    assert cohort.item_ids == (item.id,)
    assert cohort.query_terms == ("alpha",)
    assert list(iter_gap_records(tmp_path)) == []


def test_user_prompt_hook_marks_injected_memory_as_candidates_not_chat_history(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    store = ItemsStore(tmp_path / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_path / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260612-040000-browser-history-boundary",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Browser hook source boundary",
        summary="Browser hook source boundary",
        refs={"urls": ["https://example.test/browser"]},
    )
    body = "Browser hook source boundary body"
    store.write(item, body)
    idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()

    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT": "json",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }
    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": "Browser hook source boundary",
            "session_id": "hook-source-boundary-session",
            "cwd": "/repo",
            "hook_event_name": "UserPromptSubmit",
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "memory candidates, not chat history" in context
    assert "not the current conversation transcript" in context
    assert "不要把它们说成“之前的对话历史”" in context
    assert "current user message and live tool evidence override injected memory" in context
    assert "view=" in context
    assert "packed=" in context
    assert 'retrieve="memory read ' in context
    assert "meta:" not in context


def test_user_prompt_hook_excludes_scope_mismatch_state(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    store = ItemsStore(tmp_path / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_path / "index.db", embedding_dim=embedder.dim)
    keep = MemoryItem(
        id="mem-20260612-030000-browser-current",
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc),
        title="Browser current hook repo",
        summary="Browser available in current hook repo",
        tags=["browser", "runtime"],
        validity={"cwd": "/repo/current", "adapter": "codex"},
    )
    drop = MemoryItem(
        id="mem-20260612-030000-browser-other",
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc),
        title="Browser other hook repo",
        summary="Browser unavailable in another hook repo",
        tags=["browser", "runtime"],
        validity={"cwd": "/repo/other", "adapter": "codex"},
    )
    for item in (keep, drop):
        body = f"{item.title} body Browser"
        store.write(item, body)
        idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()

    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT": "json",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }
    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": "Browser hook repo",
            "session_id": "hook-scope-session",
            "cwd": "/repo/current",
            "hook_event_name": "UserPromptSubmit",
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    context = payload["hookSpecificOutput"]["additionalContext"]
    assert "Browser current hook repo" in context
    assert "Browser other hook repo" not in context


def test_user_prompt_hook_records_recall_gap_when_no_context_matches(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    (tmp_path / "items").mkdir()
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT": "json",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }

    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": "Browser hook missing context",
            "session_id": "hook-empty-session",
            "cwd": "/repo/current",
            "hook_event_name": "UserPromptSubmit",
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}
    from agent_brain.memory.governance.recall_events import iter_gap_records

    gaps = list(iter_gap_records(tmp_path))
    assert len(gaps) == 1
    assert gaps[0].reason == "empty_recall"
    assert gaps[0].adapter == "codex"
    assert gaps[0].session_id == "hook-empty-session"
    assert gaps[0].cwd == "/repo/current"
    assert gaps[0].query.startswith("sha256:")
    assert gaps[0].injected_ids == ()
    assert gaps[0].rejected_ids == ()
    assert gaps[0].evidence == ("retrieved_count=0",)
    raw_gap = (tmp_path / "runtime" / "recall-gaps.jsonl").read_text(encoding="utf-8")
    assert "Browser hook missing context" not in raw_gap


def test_user_prompt_hook_records_query_gate_gap_for_specific_weak_prompt(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    (tmp_path / "items").mkdir()
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }

    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": "验证",
            "session_id": "hook-query-gate-session",
            "cwd": "/repo/current",
            "hook_event_name": "UserPromptSubmit",
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}
    from agent_brain.memory.governance.recall_events import iter_gap_records

    gaps = list(iter_gap_records(tmp_path))
    assert gaps == []


def test_user_prompt_hook_injects_context_for_metadata_anchored_recall_prompt(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    (tmp_path / "items").mkdir()
    embedder = HashingEmbedder()
    store = ItemsStore(tmp_path / "items")
    idx = HubIndex(tmp_path / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260628-130000-hook-recall-matrix",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="召回矩阵 hook 场景",
        summary="召回矩阵 hook 场景说明",
        tags=["recall-matrix"],
    )
    body = "召回矩阵 hook 场景 body"
    store.write(item, body)
    idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }

    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": "为什么召回矩阵没有进入后处理",
            "session_id": "hook-recall-matrix-session",
            "cwd": "/repo/current",
            "hook_event_name": "UserPromptSubmit",
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    context = payload["hookSpecificOutput"]["additionalContext"]
    assert "<agent_brain>" in context
    assert "keywords: 召回矩阵" in context
    assert "keywords: 召回矩阵|召回|后处理" not in context
    assert "召回矩阵 hook 场景" in context


def test_user_prompt_hook_keeps_long_mixed_agent_prompt_domain_keywords(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    (tmp_path / "items").mkdir()
    embedder = HashingEmbedder()
    store = ItemsStore(tmp_path / "items")
    idx = HubIndex(tmp_path / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260707-230100-amh-shared-fact-layer",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="AMH 多 Agent 协作共享可信事实层",
        summary="长期记忆、上下文工程、多 Agent 协作、共享可信上下文和共享记忆层。",
        tags=["agent", "agent-memory-hub", "长期记忆", "上下文工程"],
        abstraction="L1",
    )
    body = (
        "多agent协作 多 Agent 协作 长期记忆 上下文工程 共享可信上下文 可信上下文 "
        "共享记忆层 Hopfield 联想召回 遗忘曲线 证据门禁 可信事实层 数据孤岛 上下文噪音"
    )
    store.write(item, body)
    idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }
    prompt = (
        "欢迎对 AI Agent、长期记忆、上下文工程、多 Agent 协作感兴趣的朋友交流。\n\n"
        "多 Agent 协作真正难的不是让一个 Agent 更聪明，而是让不同 Agent 之间能够共享可信上下文。\n\n"
        "基于 Hopfield 式联想召回、可治理的遗忘曲线和证据门禁，让你的 AI Agent 工具共享同一份可信事实层。\n\n"
        "告别数据孤岛\n降低上下文噪音\n\n"
        "综上帮我整合润色"
    )

    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": prompt,
            "session_id": "hook-long-mixed-agent-keywords-session",
            "cwd": "/repo/current",
            "hook_event_name": "UserPromptSubmit",
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "<agent_brain>" in context
    assert "keywords: 多agent协作|长期记忆|上下文工程" in context
    assert "keywords: agent" not in context
    assert "AMH 多 Agent 协作共享可信事实层" in context


def test_user_prompt_hook_replays_readme_deep_polish_prompt_end_to_end(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    (tmp_path / "items").mkdir()
    embedder = HashingEmbedder()
    store = ItemsStore(tmp_path / "items")
    idx = HubIndex(tmp_path / "index.db", embedding_dim=embedder.dim)
    rows = [
        MemoryItem(
            id="mem-20260628-021045-readme-runtime-model",
            type=MemoryType.artifact,
            created_at=datetime.now(timezone.utc),
            title="AMH README 渐进叙事与算法/运行时协作模型重构",
            summary="README.zh.md 增加 agent_runtime_kit 与 agent_integrations 协作模型，重写检索算法解释。",
            tags=["agent-memory-hub", "readme"],
        ),
        MemoryItem(
            id="mem-20260628-023227-readme-deep-polish",
            type=MemoryType.artifact,
            created_at=datetime.now(timezone.utc),
            title="AMH README 深度叙事和算法解释二次打磨",
            summary="README.zh.md 调整阅读路线、运行时接入、维护链路、召回链路、Loop Engineering 和算法公式。",
            tags=["agent-memory-hub", "readme"],
        ),
    ]
    for item in rows:
        body = f"{item.title}\n{item.summary}\n深度叙事 算法解释 二次打磨"
        store.write(item, body)
        idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }

    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": "关于多智能体共享第二单的深度叙事和算法解释二次打磨，都做了什么",
            "session_id": "hook-readme-deep-polish-session",
            "cwd": "<repo>",
            "hook_event_name": "UserPromptSubmit",
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    context = payload["hookSpecificOutput"]["additionalContext"]
    assert "<agent_brain>" in context
    assert "keywords: 深度叙事和算法解释二次打磨" in context
    assert "AMH README 深度叙事和算法解释二次打磨" in context

    from agent_brain.memory.context.injection_cohorts import latest_injection_cohort
    from agent_brain.memory.governance.recall_events import iter_gap_records

    cohort = latest_injection_cohort(
        tmp_path,
        adapter="codex",
        session_id="hook-readme-deep-polish-session",
    )
    assert cohort is not None
    assert rows[1].id in cohort.item_ids
    assert list(iter_gap_records(tmp_path)) == []


def test_user_prompt_hook_uses_raw_prompt_for_resolution_answerability(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    (tmp_path / "items").mkdir()
    embedder = HashingEmbedder()
    store = ItemsStore(tmp_path / "items")
    idx = HubIndex(tmp_path / "index.db", embedding_dim=embedder.dim)
    topic_only = MemoryItem(
        id="mem-20260703-010900-topic-only",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="多Agent共享第二大脑架构概览",
        summary="多Agent共享第二大脑模块边界和信息架构说明。",
        tags=["多agent共享第二大脑"],
    )
    resolution = MemoryItem(
        id="mem-20260703-011000-resolution",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="多Agent共享第二大脑召回错乱修复",
        summary="修复 weak prompt 和 scope-only 召回污染，验证 recall hallucination gate passed。",
        tags=["多agent共享第二大脑", "recall"],
    )
    for item, body in [
        (topic_only, "多Agent共享第二大脑 架构 概览 模块 信息"),
        (resolution, "多Agent共享第二大脑 召回错乱 修复 验证 passed"),
    ]:
        store.write(item, body)
        idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }

    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": "多Agent共享第二大脑 召回错乱怎么处理",
            "session_id": "hook-resolution-answerability-session",
            "cwd": "<repo>",
            "hook_event_name": "UserPromptSubmit",
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "多Agent共享第二大脑召回错乱修复" in context
    assert "多Agent共享第二大脑架构概览" not in context


def test_user_prompt_hook_captures_raw_prompt_to_conversations(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    (tmp_path / "items").mkdir()
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }

    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": "AMH raw prompt capture sentinel",
            "session_id": "hook-raw-conversation-session",
            "cwd": "/repo/current",
            "hook_event_name": "UserPromptSubmit",
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    from agent_brain.memory.evidence.conversation_store import ConversationStore

    messages = list(ConversationStore(tmp_path).iter_messages())
    assert len(messages) == 1
    assert messages[0].source_agent == "codex"
    assert messages[0].session_id == "hook-raw-conversation-session"
    assert messages[0].role == "user"
    assert messages[0].content_text == "AMH raw prompt capture sentinel"
    assert messages[0].cwd == "/repo/current"


def test_user_prompt_hook_uses_multimodal_caption_for_recall(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    image_path = tmp_path / "runtime-screenshot.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    store = ItemsStore(tmp_path / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_path / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260629-180000-version-json-api-url",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="version.json API URL 契约",
        summary="web image build 安装脚本读取 version.json 时必须传入 API_URL。",
    )
    body = "web image build 安装脚本读取 version.json 失败时，先检查 API_URL / SERVER_API_URL / NEXT_PUBLIC_API_URL 是否在 build args 中传入。"
    store.write(item, body)
    idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }

    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": "[Image #1]\n我其他同事执行之后有问题",
            "session_id": "hook-mm-caption-session",
            "cwd": "/repo/current",
            "hook_event_name": "UserPromptSubmit",
            "images": [
                {
                    "name": "[Image #1]",
                    "path": str(image_path),
                    "mime_type": "image/png",
                    "caption": "截图显示 web image build 安装脚本读取 version.json 失败。",
                }
            ],
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    context = payload["hookSpecificOutput"]["additionalContext"]
    assert "version.json API URL 契约" in context
    assert "version.json" in context
    assert "version.json" in context

    from agent_brain.memory.evidence.resource_store import ResourceStore

    assert len(list(ResourceStore(tmp_path).iter_extractions())) == 1


def test_user_prompt_hook_uses_image_path_ocr_for_recall(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    image_path = tmp_path / "ocr-screenshot.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    ocr_script = tmp_path / "fake_ocr.py"
    ocr_script.write_text("print('version.json API_URL failed')\n", encoding="utf-8")
    store = ItemsStore(tmp_path / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_path / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260629-182000-version-json-ocr",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="version.json API_URL failed OCR recall",
        summary="version.json API_URL failed should recall API_URL build contract.",
    )
    body = "version.json API_URL failed when build args do not pass API_URL."
    store.write(item, body)
    idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT": "json",
        "AGENT_MEMORY_HUB_OCR_COMMAND": f"python {ocr_script} {{path}}",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }

    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": "[Image #1]",
            "session_id": "hook-mm-ocr-session",
            "cwd": "/repo/current",
            "hook_event_name": "UserPromptSubmit",
            "images": [
                {
                    "name": "[Image #1]",
                    "path": str(image_path),
                    "mime_type": "image/png",
                }
            ],
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "version.json API_URL failed OCR recall" in context

    from agent_brain.memory.evidence.resource_store import ResourceStore

    extractions = list(ResourceStore(tmp_path).iter_extractions())
    assert len(extractions) == 1
    assert extractions[0].kind == "ocr"
    assert extractions[0].extractor == "amh.hook.ocr-command"


def test_user_prompt_hook_uses_pdf_path_text_for_recall(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    pdf_path = tmp_path / "contract.pdf"
    _write_pdf_fixture(pdf_path, "PDF contract requires SERVER_API_URL build arg")
    store = ItemsStore(tmp_path / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_path / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260629-183000-server-api-url-pdf",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="SERVER_API_URL build arg PDF recall",
        summary="PDF contract requires SERVER_API_URL build arg.",
    )
    body = "PDF contract requires SERVER_API_URL build arg when Docker image builds web assets."
    store.write(item, body)
    idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }

    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": "[PDF #1]",
            "session_id": "hook-mm-pdf-session",
            "cwd": "/repo/current",
            "hook_event_name": "UserPromptSubmit",
            "files": [
                {
                    "name": "[PDF #1]",
                    "path": str(pdf_path),
                    "mime_type": "application/pdf",
                }
            ],
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "SERVER_API_URL build arg PDF recall" in context

    from agent_brain.memory.evidence.resource_store import ResourceStore

    extractions = list(ResourceStore(tmp_path).iter_extractions())
    assert len(extractions) == 1
    assert extractions[0].kind == "text"
    assert extractions[0].extractor.startswith("amh.hook.local-pdf-text")


def test_user_prompt_hook_uses_asr_command_for_audio_recall(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    audio_path = tmp_path / "meeting.wav"
    audio_path.write_bytes(b"RIFF....WAVEfmt ")
    asr_script = tmp_path / "fake_asr.py"
    asr_script.write_text("print('Audio says NEXT_PUBLIC_API_URL missing')\n", encoding="utf-8")
    store = ItemsStore(tmp_path / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_path / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260629-183500-next-public-audio",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="NEXT_PUBLIC_API_URL audio recall",
        summary="NEXT_PUBLIC_API_URL missing appears in audio transcript.",
    )
    body = "Audio says NEXT_PUBLIC_API_URL missing when build args omit the public API URL."
    store.write(item, body)
    idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "AGENT_MEMORY_HUB_ASR_COMMAND": f"python {asr_script} {{path}}",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }

    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": "[Audio #1]",
            "session_id": "hook-mm-audio-session",
            "cwd": "/repo/current",
            "hook_event_name": "UserPromptSubmit",
            "attachments": [
                {
                    "name": "[Audio #1]",
                    "path": str(audio_path),
                    "mime_type": "audio/wav",
                }
            ],
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "NEXT_PUBLIC_API_URL audio recall" in context

    from agent_brain.memory.evidence.resource_store import ResourceStore

    extractions = list(ResourceStore(tmp_path).iter_extractions())
    assert len(extractions) == 1
    assert extractions[0].kind == "asr"
    assert extractions[0].extractor == "amh.hook.asr-command"


def test_user_prompt_hook_auto_detects_whisper_for_audio_recall(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    audio_path = tmp_path / "meeting.wav"
    audio_path.write_bytes(b"RIFF....WAVEfmt ")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    whisper = bin_dir / "whisper"
    whisper.write_text("#!/bin/sh\necho 'Auto whisper says SERVER_API_URL missing'\n", encoding="utf-8")
    whisper.chmod(0o755)
    store = ItemsStore(tmp_path / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_path / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260629-184500-server-api-audio-auto",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="SERVER_API_URL auto ASR recall",
        summary="Auto whisper says SERVER_API_URL missing in audio transcript.",
    )
    body = "Auto whisper says SERVER_API_URL missing when the audio is transcribed."
    store.write(item, body)
    idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }
    env.pop("AGENT_MEMORY_HUB_ASR_COMMAND", None)

    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": "[Audio #1]",
            "session_id": "hook-mm-audio-auto-session",
            "cwd": "/repo/current",
            "hook_event_name": "UserPromptSubmit",
            "attachments": [
                {
                    "name": "[Audio #1]",
                    "path": str(audio_path),
                    "mime_type": "audio/wav",
                }
            ],
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "SERVER_API_URL auto ASR recall" in context

    from agent_brain.memory.evidence.resource_store import ResourceStore

    extractions = list(ResourceStore(tmp_path).iter_extractions())
    assert len(extractions) == 1
    assert extractions[0].kind == "asr"
    assert extractions[0].extractor == "amh.hook.asr-auto.whisper"


def test_user_prompt_hook_records_multimodal_gap_without_injecting_image_memory(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    store = ItemsStore(tmp_path / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_path / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260629-181000-hero-image-prompt",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="AMH README shared second brain hero image prompt",
        summary="Created image prompt artifacts for README hero image output.",
    )
    body = "shared second brain hero image prompt"
    store.write(item, body)
    idx.upsert(item, body, embedding=embedder.embed(body))
    idx.close()
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }

    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": "[Image #1]\n我其他同事执行之后有问题",
            "session_id": "hook-mm-missing-session",
            "cwd": "/repo/current",
            "hook_event_name": "UserPromptSubmit",
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}

    from agent_brain.memory.evidence.resource_store import ResourceStore
    from agent_brain.memory.governance.recall_events import iter_gap_records

    resources = list(ResourceStore(tmp_path).iter_resources())
    assert len(resources) == 1
    assert resources[0].metadata["extraction_status"] == "missing"
    gaps = list(iter_gap_records(tmp_path))
    assert len(gaps) == 1
    assert gaps[0].reason == "multimodal_extraction_missing"
    assert gaps[0].query.startswith("sha256:")
    assert gaps[0].injected_ids == ()
    assert gaps[0].rejected_ids == ()
    assert gaps[0].evidence == ("source_evidence_count=3",)
    raw_gap = (tmp_path / "runtime" / "recall-gaps.jsonl").read_text(encoding="utf-8")
    assert "[Image #1]" not in raw_gap
    assert "我其他同事执行之后有问题" not in raw_gap
    assert "Image#1" not in raw_gap


def test_user_prompt_hook_trace_empty_reports_multimodal_missing_extraction(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    (tmp_path / "items").mkdir()
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT": "json",
        "AGENT_MEMORY_HUB_HOOK_TRACE_EMPTY": "1",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }

    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": "[Image #1]\n治理方向没问题，在推进之前，我再反馈一个问题",
            "session_id": "hook-mm-trace-session",
            "cwd": "/repo/current",
            "hook_event_name": "UserPromptSubmit",
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "<agent_brain_diagnostics>" in context
    assert "hook: triggered" in context
    assert "decision: no_injection" in context
    assert "reason: multimodal_extraction_missing" in context
    assert "keywords: Image#1" in context
    assert "multimodal_placeholders=Image#1" in context
    assert "extraction_text=missing" in context


def test_user_prompt_hook_does_not_record_query_gate_gap_for_connective_noise(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    (tmp_path / "items").mkdir()
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }

    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": "就像",
            "session_id": "hook-noise-session",
            "cwd": "/repo/current",
            "hook_event_name": "UserPromptSubmit",
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}
    from agent_brain.memory.governance.recall_events import iter_gap_records

    assert list(iter_gap_records(tmp_path)) == []


def test_session_end_signal_writes_validity_scope(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "session-end-signal.sh"
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }

    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "session_id": "sess-stop-validity",
            "cwd": "/repo/current",
            "hook_event_name": "Stop",
            "transcript_path": "/tmp/transcript.jsonl",
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    item = next(item for item, _body in ItemsStore(tmp_path / "items").iter_all())
    assert item.type == "signal"
    assert item.agent == "codex"
    assert item.validity.cwd == "/repo/current"
    assert item.validity.adapter == "codex"


def test_session_end_signal_ingests_transcript_to_conversations(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "session-end-signal.sh"
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join([
            json.dumps({"role": "user", "content": "raw stop transcript user"}),
            json.dumps({"role": "assistant", "content": "raw stop transcript assistant"}),
        ])
        + "\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }

    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "session_id": "sess-stop-raw-conversation",
            "cwd": "/repo/current",
            "hook_event_name": "Stop",
            "transcript_path": str(transcript),
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    from agent_brain.memory.evidence.conversation_store import ConversationStore

    messages = list(ConversationStore(tmp_path).iter_messages())
    assert [message.role for message in messages] == ["user", "assistant"]
    assert [message.content_text for message in messages] == [
        "raw stop transcript user",
        "raw stop transcript assistant",
    ]
    assert all(message.source_agent == "codex" for message in messages)
    assert all(message.session_id == "sess-stop-raw-conversation" for message in messages)
    assert all(message.cwd == "/repo/current" for message in messages)


def test_stop_transcript_supersedes_live_prompt_capture(tmp_path):
    inject_script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    stop_script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "session-end-signal.sh"
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join([
            json.dumps({"role": "user", "content": "same user input"}),
            json.dumps({"role": "assistant", "content": "assistant answer"}),
        ])
        + "\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }

    prompt_result = subprocess.run(
        ["bash", str(inject_script)],
        input=json.dumps({
            "prompt": "same user input",
            "session_id": "sess-stop-supersedes-live",
            "cwd": "/repo/current",
            "hook_event_name": "UserPromptSubmit",
        }),
        env=env,
        capture_output=True,
        text=True,
    )
    stop_result = subprocess.run(
        ["bash", str(stop_script)],
        input=json.dumps({
            "session_id": "sess-stop-supersedes-live",
            "cwd": "/repo/current",
            "hook_event_name": "Stop",
            "transcript_path": str(transcript),
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert prompt_result.returncode == 0, prompt_result.stderr
    assert stop_result.returncode == 0, stop_result.stderr
    from agent_brain.memory.evidence.conversation_store import ConversationStore

    messages = list(ConversationStore(tmp_path).iter_messages())
    assert [message.content_text for message in messages] == ["same user input", "assistant answer"]
    assert all(message.metadata.get("capture_kind") != "prompt" for message in messages)


def test_lifecycle_precompact_writes_signal(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "lifecycle-event.sh"
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }

    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "session_id": "sess-precompact",
            "cwd": "/repo/current",
            "hook_event_name": "PreCompact",
            "transcript_path": "/tmp/transcript.jsonl",
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    item, body = next(ItemsStore(tmp_path / "items").iter_all())
    assert item.type == "signal"
    assert item.title.startswith("Session sess-pre compact boundary")
    assert item.validity.cwd == "/repo/current"
    assert item.validity.adapter == "codex"
    assert "PreCompact" in body
    assert "/tmp/transcript.jsonl" in body

    from agent_brain.agent_integrations.runtime_events import runtime_event_summary

    summary = runtime_event_summary(tmp_path, "codex")
    assert summary.count == 1
    assert summary.last_event is not None
    assert summary.last_event["event_name"] == "PreCompact"


def test_lifecycle_subagent_start_only_records_event(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "lifecycle-event.sh"
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "MEMORY_HUB_TEST_EMBEDDING": "1",
        "MEMORY_HUB_EMBEDDING_OFFLINE": "1",
    }

    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "session_id": "sess-subagent",
            "cwd": "/repo/current",
            "hook_event_name": "SubagentStart",
        }),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "items").exists()

    from agent_brain.agent_integrations.runtime_events import runtime_event_summary

    summary = runtime_event_summary(tmp_path, "codex")
    assert summary.count == 1
    assert summary.last_event is not None
    assert summary.last_event["event_name"] == "SubagentStart"
