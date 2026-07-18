from __future__ import annotations

import json
import os
import signal
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

    cap = capability_for_adapter(
        "codex",
        get_adapter("codex", tmp_path),
        now=datetime(2026, 6, 21, 10, 6, tzinfo=timezone.utc),
    )

    assert cap.support_level == "install-ready"
    assert cap.runtime_observed is True
    assert cap.runtime_event_count == 1
    assert cap.last_runtime_event is not None
    assert cap.last_runtime_event["event_name"] == "UserPromptSubmit"


def test_adapter_verification_record_promotes_verified_capability(tmp_path):
    from agent_brain.agent_integrations.capabilities import capability_for_adapter
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.agent_integrations.verifications import record_adapter_verification
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort

    record_runtime_event(
        tmp_path,
        adapter="codex",
        event_name="UserPromptSubmit",
        session_id="sess-runtime",
        now=datetime(2026, 6, 21, 10, 0, tzinfo=timezone.utc),
    )
    record_injection_cohort(
        tmp_path,
        adapter="codex",
        session_id="sess-runtime",
        item_ids=["mem-runtime-verification"],
        now=datetime(2026, 6, 21, 10, 1, tzinfo=timezone.utc),
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

    cap = capability_for_adapter(
        "codex",
        get_adapter("codex", tmp_path),
        now=datetime(2026, 6, 21, 10, 6, tzinfo=timezone.utc),
    )

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


def _copy_user_prompt_hook_runtime(
    repo: Path,
    hooks_dir: Path,
    tools_dir: Path,
) -> None:
    shutil.copy2(
        repo / "agent_runtime_kit" / "hooks" / "inject-context.sh",
        hooks_dir / "inject-context.sh",
    )
    shutil.copy2(
        repo / "agent_runtime_kit" / "tools" / "parse-hook-payload.py",
        tools_dir / "parse-hook-payload.py",
    )


def _write_fake_routed_hook_runtime(
    tmp_path: Path,
    *,
    search_stdout: str,
) -> tuple[Path, Path]:
    """Install the real hook around a deterministic fake structured CLI."""
    repo = Path(__file__).resolve().parents[2]
    runtime = tmp_path / "runtime" / "agent_runtime_kit"
    hooks_dir = runtime / "hooks"
    tools_dir = runtime / "tools"
    hooks_dir.mkdir(parents=True)
    tools_dir.mkdir(parents=True)
    _copy_user_prompt_hook_runtime(repo, hooks_dir, tools_dir)
    (tools_dir / "_resolve-python.sh").write_text(
        f'MEMORY_PYTHON="{sys.executable}"\n_PYTHON_OK=0\n',
        encoding="utf-8",
    )
    (tools_dir / "record-runtime-event.sh").write_text(
        "#!/bin/sh\nexit 0\n",
        encoding="utf-8",
    )
    (tools_dir / "search-memory.sh").write_text(
        "#!/bin/sh\npython3 - \"$FAKE_SEARCH_ARGS\" \"$@\" <<'PY'\n"
        "import json\n"
        "import sys\n"
        "with open(sys.argv[1], 'w', encoding='utf-8') as fh:\n"
        "    json.dump(sys.argv[2:], fh, ensure_ascii=False)\n"
        "PY\n"
        + "printf '%s' "
        + repr(search_stdout)
        + "\n",
        encoding="utf-8",
    )
    for script in tools_dir.iterdir():
        script.chmod(0o755)
    return hooks_dir / "inject-context.sh", tmp_path / "search-args.txt"


def _write_preflight_probe_runtime(
    tmp_path: Path,
    *,
    preflight_mode: str = "pass",
    search_status: str = "injected",
) -> tuple[Path, Path, Path]:
    repo = Path(__file__).resolve().parents[2]
    runtime = tmp_path / "runtime" / "agent_runtime_kit"
    hooks_dir = runtime / "hooks"
    tools_dir = runtime / "tools"
    hooks_dir.mkdir(parents=True)
    tools_dir.mkdir(parents=True)
    _copy_user_prompt_hook_runtime(repo, hooks_dir, tools_dir)
    preflight_calls = tmp_path / "preflight-calls.txt"
    legacy_record_calls = tmp_path / "legacy-record-calls.txt"
    python_wrapper = tmp_path / "memory-python"
    python_wrapper.write_text(
        "#!/bin/sh\n"
        "if [ \"${1:-}\" = '-m' ] && "
        "[ \"${2:-}\" = 'agent_brain.memory.evidence.hook_preflight' ]; then\n"
        "  printf 'called\\n' >> \"$PREFLIGHT_CALLS\"\n"
        "  case \"${PREFLIGHT_MODE:-pass}\" in\n"
        "    exit91) exit 91 ;;\n"
        "    wrong-version) "
        "printf 'wrong-version\\000normalized\\000\\000\\000'; exit 0 ;;\n"
        "    junk-after-success)\n"
        "      \"$REAL_PYTHON\" \"$@\" || exit $?\n"
        "      printf 'trailing-junk'\n"
        "      exit 0\n"
        "      ;;\n"
        "  esac\n"
        "fi\n"
        "exec \"$REAL_PYTHON\" \"$@\"\n",
        encoding="utf-8",
    )
    python_wrapper.chmod(0o755)
    (tools_dir / "_resolve-python.sh").write_text(
        f'MEMORY_PYTHON="{python_wrapper}"\n_PYTHON_OK=0\n',
        encoding="utf-8",
    )
    (tools_dir / "record-runtime-event.sh").write_text(
        "#!/bin/sh\n"
        "printf 'called\\n' >> \"$LEGACY_RECORD_CALLS\"\n"
        "exec \"$REAL_PYTHON\" -m agent_brain.agent_integrations.runtime_events "
        "record --brain-dir \"$BRAIN_DIR\" \"$@\"\n",
        encoding="utf-8",
    )
    if search_status == "injected":
        search_payload = {
            "status": "injected",
            "reason": "included",
            "context": "[fact] consolidated preflight context",
            "routes": [
                {
                    "route": "semantic_raw",
                    "status": "ok",
                    "candidate_count": 1,
                    "reason": "route_completed",
                }
            ],
        }
    else:
        search_payload = {
            "status": "empty",
            "reason": "no_candidates",
            "context": "",
            "routes": [],
        }
    (tools_dir / "search-memory.sh").write_text(
        "#!/bin/sh\n"
        "last=''\n"
        "for value in \"$@\"; do last=$value; done\n"
        "printf '%s' \"$last\" > \"$SEARCH_QUERY\"\n"
        "printf '%s\\n' "
        + repr(json.dumps(search_payload))
        + "\n",
        encoding="utf-8",
    )
    for script in tools_dir.iterdir():
        script.chmod(0o755)
    os.environ.pop("PREFLIGHT_CALLS", None)
    return hooks_dir / "inject-context.sh", preflight_calls, legacy_record_calls


def _preflight_probe_env(
    tmp_path: Path,
    preflight_calls: Path,
    legacy_record_calls: Path,
    *,
    preflight_mode: str,
) -> dict[str, str]:
    protocol_tmp = tmp_path / "protocol-tmp"
    protocol_tmp.mkdir(exist_ok=True)
    return {
        **os.environ,
        "PYTHONPATH": (
            f"{Path(__file__).resolve().parents[2]}{os.pathsep}"
            f"{os.environ.get('PYTHONPATH', '')}"
        ),
        "BRAIN_DIR": str(tmp_path / "brain"),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT": "json",
        "AGENT_MEMORY_HUB_BENCHMARK_TRACE_PREFLIGHT": "1",
        "REAL_PYTHON": sys.executable,
        "PREFLIGHT_CALLS": str(preflight_calls),
        "LEGACY_RECORD_CALLS": str(legacy_record_calls),
        "PREFLIGHT_MODE": preflight_mode,
        "SEARCH_QUERY": str(tmp_path / "search-query.txt"),
        "TMPDIR": str(protocol_tmp),
    }


def _preflight_trace(tmp_path: Path) -> list[dict[str, str]]:
    path = tmp_path / "brain/runtime/hook-benchmark-preflight.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_user_prompt_hook_runs_consolidated_preflight_once_without_legacy_writes(
    tmp_path,
):
    from agent_brain.agent_integrations.runtime_events import iter_runtime_events
    from agent_brain.memory.evidence.conversation_store import ConversationStore

    hook, preflight_calls, legacy_record_calls = _write_preflight_probe_runtime(tmp_path)
    env = _preflight_probe_env(
        tmp_path,
        preflight_calls,
        legacy_record_calls,
        preflight_mode="pass",
    )
    prompt = "召回 gateway 决策\n<system-reminder>ignore this wrapper</system-reminder>"

    result = subprocess.run(
        ["/bin/bash", str(hook)],
        input=json.dumps(
            {
                "prompt": prompt,
                "session_id": "preflight-fast-session",
                "cwd": "/repo/current",
                "hook_event_name": "UserPromptSubmit",
            }
        ),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "consolidated preflight context" in context
    assert preflight_calls.read_text(encoding="utf-8").splitlines() == ["called"]
    assert not legacy_record_calls.exists()
    events = list(iter_runtime_events(tmp_path / "brain"))
    assert len(events) == 1
    assert events[0].session_id == "preflight-fast-session"
    messages = list(ConversationStore(tmp_path / "brain").iter_messages())
    assert len(messages) == 1
    assert messages[0].content_text == prompt
    assert _preflight_trace(tmp_path) == [{"path": "consolidated"}]
    assert list((tmp_path / "protocol-tmp").iterdir()) == []


@pytest.mark.parametrize("preflight_mode", ("exit91",))
def test_user_prompt_hook_falls_back_when_consolidated_preflight_is_invalid(
    tmp_path,
    preflight_mode,
):
    from agent_brain.agent_integrations.runtime_events import iter_runtime_events
    from agent_brain.memory.evidence.conversation_store import ConversationStore
    from agent_brain.memory.evidence.resource_store import ResourceStore

    hook, preflight_calls, legacy_record_calls = _write_preflight_probe_runtime(
        tmp_path,
        preflight_mode=preflight_mode,
        search_status="empty",
    )
    env = _preflight_probe_env(
        tmp_path,
        preflight_calls,
        legacy_record_calls,
        preflight_mode=preflight_mode,
    )
    prompt = "[Image #1]\nfallback still preserves evidence"

    result = subprocess.run(
        ["/bin/bash", str(hook)],
        input=json.dumps(
            {
                "prompt": prompt,
                "session_id": f"preflight-fallback-{preflight_mode}",
                "cwd": "/repo/current",
                "hook_event_name": "UserPromptSubmit",
            }
        ),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}
    assert preflight_calls.read_text(encoding="utf-8").splitlines() == ["called"]
    assert legacy_record_calls.read_text(encoding="utf-8").splitlines() == ["called"]
    assert len(list(iter_runtime_events(tmp_path / "brain"))) == 1
    messages = list(ConversationStore(tmp_path / "brain").iter_messages())
    assert len(messages) == 1
    assert messages[0].content_text == prompt
    resources = list(ResourceStore(tmp_path / "brain").iter_resources())
    assert len(resources) == 1
    assert resources[0].metadata["extraction_status"] == "missing"
    assert _preflight_trace(tmp_path) == [{"path": "full_legacy_fallback"}]
    assert list((tmp_path / "protocol-tmp").iterdir()) == []


def test_user_prompt_hook_invalid_success_protocol_reuses_preflight_evidence(
    tmp_path,
):
    from agent_brain.agent_integrations.runtime_events import iter_runtime_events
    from agent_brain.memory.evidence.conversation_store import ConversationStore
    from agent_brain.memory.evidence.resource_store import ResourceStore

    hook, preflight_calls, legacy_record_calls = _write_preflight_probe_runtime(
        tmp_path,
        preflight_mode="junk-after-success",
        search_status="injected",
    )
    env = _preflight_probe_env(
        tmp_path,
        preflight_calls,
        legacy_record_calls,
        preflight_mode="junk-after-success",
    )
    payload = {
        "prompt": "inspect attached failure",
        "session_id": "preflight-junk-after-success",
        "cwd": "/repo/current",
        "hook_event_name": "UserPromptSubmit",
        "images": [
            {
                "name": "failure.png",
                "uri": "memory://failure.png",
                "caption": "gateway timeout caption",
            }
        ],
    }

    result = subprocess.run(
        ["/bin/bash", str(hook)],
        input=json.dumps(payload),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "consolidated preflight context" in context
    assert preflight_calls.read_text(encoding="utf-8").splitlines() == ["called"]
    assert not legacy_record_calls.exists()
    assert len(list(iter_runtime_events(tmp_path / "brain"))) == 1
    messages = list(ConversationStore(tmp_path / "brain").iter_messages())
    assert len(messages) == 1
    assert messages[0].content_text == payload["prompt"]
    resource_store = ResourceStore(tmp_path / "brain")
    assert len(list(resource_store.iter_resources())) == 1
    assert len(list(resource_store.iter_extractions())) == 1
    search_query = (tmp_path / "search-query.txt").read_text(encoding="utf-8")
    assert search_query == "inspect attached failure\ngateway timeout caption"
    assert _preflight_trace(tmp_path) == [{"path": "derivation_only_fallback"}]
    assert list((tmp_path / "protocol-tmp").iterdir()) == []


@pytest.mark.parametrize("raw_payload", ("{", "[]"))
def test_user_prompt_hook_invalid_payload_fails_open_without_side_effects(
    tmp_path,
    raw_payload,
):
    script = (
        Path(__file__).resolve().parents[2]
        / "agent_runtime_kit"
        / "hooks"
        / "inject-context.sh"
    )
    brain_dir = tmp_path / "brain"

    result = subprocess.run(
        ["/bin/bash", str(script)],
        input=raw_payload,
        env={**os.environ, "BRAIN_DIR": str(brain_dir)},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}
    assert not brain_dir.exists()


@pytest.mark.parametrize(
    "parser_mode",
    ("truncated", "wrong-version", "extra-field", "trailing-junk"),
)
def test_user_prompt_hook_rejects_invalid_payload_parser_protocol(
    tmp_path,
    parser_mode,
):
    script = (
        Path(__file__).resolve().parents[2]
        / "agent_runtime_kit"
        / "hooks"
        / "inject-context.sh"
    )
    bin_dir = tmp_path / "bin"
    protocol_tmp = tmp_path / "protocol-tmp"
    bin_dir.mkdir()
    protocol_tmp.mkdir()
    parser_calls = tmp_path / "parser-calls.txt"
    wrapper = bin_dir / "python3"
    wrapper.write_text(
        "#!/bin/sh\n"
        "case \"${1:-}\" in\n"
        "  */parse-hook-payload.py)\n"
        "    printf 'called\\n' >> \"$PARSER_CALLS\"\n"
        "    case \"$PARSER_MODE\" in\n"
        "      truncated) printf 'amh-hook-payload-v1\\000prompt\\000session\\000/repo\\000' ;;\n"
        "      wrong-version) printf 'wrong-version\\000prompt\\000session\\000/repo\\000UserPromptSubmit\\000' ;;\n"
        "      extra-field) printf 'amh-hook-payload-v1\\000prompt\\000session\\000/repo\\000UserPromptSubmit\\000extra\\000' ;;\n"
        "      trailing-junk) printf 'amh-hook-payload-v1\\000prompt\\000session\\000/repo\\000UserPromptSubmit\\000junk' ;;\n"
        "    esac\n"
        "    exit 0\n"
        "    ;;\n"
        "esac\n"
        "exec \"$REAL_PYTHON\" \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    brain_dir = tmp_path / "brain"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "REAL_PYTHON": sys.executable,
        "PARSER_CALLS": str(parser_calls),
        "PARSER_MODE": parser_mode,
        "TMPDIR": str(protocol_tmp),
        "BRAIN_DIR": str(brain_dir),
    }

    result = subprocess.run(
        ["/bin/bash", str(script)],
        input=json.dumps(
            {
                "prompt": "must not reach preflight",
                "session_id": "invalid-parser-protocol",
                "cwd": "/repo",
                "hook_event_name": "UserPromptSubmit",
            }
        ),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}
    assert parser_calls.read_text(encoding="utf-8").splitlines() == ["called"]
    assert not brain_dir.exists()
    assert list(protocol_tmp.iterdir()) == []


def _write_raw_payload_probe_runtime(
    tmp_path: Path,
) -> tuple[Path, dict[str, Path], Path]:
    repo = Path(__file__).resolve().parents[2]
    runtime = tmp_path / "runtime" / "agent_runtime_kit"
    hooks_dir = runtime / "hooks"
    tools_dir = runtime / "tools"
    bin_dir = tmp_path / "bin"
    hooks_dir.mkdir(parents=True)
    tools_dir.mkdir(parents=True)
    bin_dir.mkdir()
    _copy_user_prompt_hook_runtime(repo, hooks_dir, tools_dir)
    markers = {
        name: tmp_path / f"{name}-calls.txt"
        for name in ("parser", "preflight", "legacy-record", "search")
    }
    parser_wrapper = bin_dir / "python3"
    parser_wrapper.write_text(
        "#!/bin/sh\n"
        "case \"${1:-}\" in\n"
        "  */parse-hook-payload.py) printf 'called\\n' >> \"$PARSER_CALLS\" ;;\n"
        "esac\n"
        "exec \"$REAL_PYTHON\" \"$@\"\n",
        encoding="utf-8",
    )
    parser_wrapper.chmod(0o755)
    memory_wrapper = tmp_path / "memory-python"
    memory_wrapper.write_text(
        "#!/bin/sh\n"
        "if [ \"${1:-}\" = '-m' ] && "
        "[ \"${2:-}\" = 'agent_brain.memory.evidence.hook_preflight' ]; then\n"
        "  printf 'called\\n' >> \"$PREFLIGHT_CALLS\"\n"
        "fi\n"
        "exec \"$REAL_PYTHON\" \"$@\"\n",
        encoding="utf-8",
    )
    memory_wrapper.chmod(0o755)
    (tools_dir / "_resolve-python.sh").write_text(
        f'MEMORY_PYTHON="{memory_wrapper}"\n_PYTHON_OK=0\n',
        encoding="utf-8",
    )
    (tools_dir / "record-runtime-event.sh").write_text(
        "#!/bin/sh\n"
        "printf 'called\\n' >> \"$LEGACY_RECORD_CALLS\"\n"
        "exec \"$REAL_PYTHON\" -m agent_brain.agent_integrations.runtime_events "
        "record --brain-dir \"$BRAIN_DIR\" \"$@\"\n",
        encoding="utf-8",
    )
    (tools_dir / "search-memory.sh").write_text(
        "#!/bin/sh\n"
        "printf 'called\\n' >> \"$SEARCH_CALLS\"\n"
        "printf '%s\\n' "
        "'{\"status\":\"empty\",\"reason\":\"no_candidates\","
        "\"context\":\"\",\"routes\":[]}'\n",
        encoding="utf-8",
    )
    for script in tools_dir.iterdir():
        script.chmod(0o755)
    protocol_tmp = tmp_path / "protocol-tmp"
    protocol_tmp.mkdir()
    return hooks_dir / "inject-context.sh", markers, protocol_tmp


@pytest.mark.parametrize(
    ("field", "raw_payload"),
    (
        (
            "prompt",
            b'{"prompt":"before\0after","session_id":"nul-prompt","cwd":"/repo",'
            b'"hook_event_name":"UserPromptSubmit"}',
        ),
        (
            "session_id",
            b'{"prompt":"","session_id":"abc\0def","cwd":"/repo",'
            b'"hook_event_name":"UserPromptSubmit"}',
        ),
        (
            "cwd",
            b'{"prompt":"","session_id":"nul-cwd","cwd":"/repo\0/other",'
            b'"hook_event_name":"UserPromptSubmit"}',
        ),
        (
            "hook_event_name",
            b'{"prompt":"","session_id":"nul-event","cwd":"/repo",'
            b'"hook_event_name":"User\0PromptSubmit"}',
        ),
    ),
)
def test_user_prompt_hook_rejects_raw_nul_without_side_effects(
    tmp_path,
    field,
    raw_payload,
):
    hook, markers, protocol_tmp = _write_raw_payload_probe_runtime(tmp_path)
    brain_dir = tmp_path / "brain"
    env = {
        **os.environ,
        "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
        "PYTHONPATH": (
            f"{Path(__file__).resolve().parents[2]}{os.pathsep}"
            f"{os.environ.get('PYTHONPATH', '')}"
        ),
        "REAL_PYTHON": sys.executable,
        "PARSER_CALLS": str(markers["parser"]),
        "PREFLIGHT_CALLS": str(markers["preflight"]),
        "LEGACY_RECORD_CALLS": str(markers["legacy-record"]),
        "SEARCH_CALLS": str(markers["search"]),
        "TMPDIR": str(protocol_tmp),
        "BRAIN_DIR": str(brain_dir),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
    }

    result = subprocess.run(
        ["/bin/bash", str(hook)],
        input=raw_payload,
        env=env,
        capture_output=True,
        timeout=5,
    )

    assert result.returncode == 0, (field, result.stderr)
    assert result.stdout == b"{}\n"
    assert result.stderr == b""
    assert markers["parser"].read_text(encoding="utf-8").splitlines() == ["called"]
    assert not markers["preflight"].exists()
    assert not markers["legacy-record"].exists()
    assert not markers["search"].exists()
    assert not brain_dir.exists()
    assert list(protocol_tmp.iterdir()) == []


def test_user_prompt_hook_rejects_decoded_nested_nul_before_preflight(tmp_path):
    hook, markers, protocol_tmp = _write_raw_payload_probe_runtime(tmp_path)
    brain_dir = tmp_path / "brain"
    raw_payload = (
        b'{"prompt":"inspect image","session_id":"nested-nul","cwd":"/repo",'
        b'"hook_event_name":"UserPromptSubmit","images":[{"name":"shot.png",'
        b'"caption":"before\\u0000after"}]}'
    )
    env = {
        **os.environ,
        "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
        "PYTHONPATH": (
            f"{Path(__file__).resolve().parents[2]}{os.pathsep}"
            f"{os.environ.get('PYTHONPATH', '')}"
        ),
        "REAL_PYTHON": sys.executable,
        "PARSER_CALLS": str(markers["parser"]),
        "PREFLIGHT_CALLS": str(markers["preflight"]),
        "LEGACY_RECORD_CALLS": str(markers["legacy-record"]),
        "SEARCH_CALLS": str(markers["search"]),
        "TMPDIR": str(protocol_tmp),
        "BRAIN_DIR": str(brain_dir),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
    }

    result = subprocess.run(
        ["/bin/bash", str(hook)],
        input=raw_payload,
        env=env,
        capture_output=True,
        timeout=5,
    )

    assert result.returncode == 0
    assert result.stdout == b"{}\n"
    assert result.stderr == b""
    assert markers["parser"].read_text(encoding="utf-8").splitlines() == ["called"]
    assert not markers["preflight"].exists()
    assert not markers["legacy-record"].exists()
    assert not markers["search"].exists()
    assert not brain_dir.exists()
    assert list(protocol_tmp.iterdir()) == []


@pytest.mark.parametrize(
    "hook_signal",
    (signal.SIGHUP, signal.SIGINT, signal.SIGTERM),
    ids=("hup", "int", "term"),
)
def test_user_prompt_hook_signal_cleans_blocked_payload_protocol_file(
    tmp_path,
    hook_signal,
):
    script = (
        Path(__file__).resolve().parents[2]
        / "agent_runtime_kit"
        / "hooks"
        / "inject-context.sh"
    )
    bin_dir = tmp_path / "bin"
    protocol_tmp = tmp_path / "protocol-tmp"
    bin_dir.mkdir()
    protocol_tmp.mkdir()
    parser_ready = tmp_path / "parser-ready"
    child_pid_path = tmp_path / "parser-child-pid"
    delayed_marker = tmp_path / "parser-delayed"
    wrapper = bin_dir / "python3"
    wrapper.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        "real = os.environ['REAL_PYTHON']\n"
        "if len(sys.argv) > 1 and sys.argv[1].endswith('/parse-hook-payload.py'):\n"
        "    status = subprocess.call([real, *sys.argv[1:]])\n"
        "    if status != 0:\n"
        "        raise SystemExit(status)\n"
        "    open(os.environ['PARSER_CHILD_PID'], 'w').write(str(os.getpid()))\n"
        "    open(os.environ['PARSER_READY'], 'w').write('ready\\n')\n"
        "    time.sleep(30)\n"
        "    open(os.environ['PARSER_DELAYED'], 'w').write('survived\\n')\n"
        "    raise SystemExit(0)\n"
        "os.execv(real, [real, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    raw_prompt = f"{hook_signal.name} must erase this raw prompt"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "REAL_PYTHON": sys.executable,
        "PARSER_READY": str(parser_ready),
        "PARSER_CHILD_PID": str(child_pid_path),
        "PARSER_DELAYED": str(delayed_marker),
        "TMPDIR": str(protocol_tmp),
        "BRAIN_DIR": str(tmp_path / "brain"),
    }
    proc = subprocess.Popen(
        ["/bin/bash", str(script)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    child_pid = None
    try:
        assert proc.stdin is not None
        proc.stdin.write(
            json.dumps(
                {
                    "prompt": raw_prompt,
                    "session_id": f"{hook_signal.name.lower()}-cleanup-session",
                    "cwd": "/repo",
                    "hook_event_name": "UserPromptSubmit",
                }
            )
        )
        proc.stdin.close()
        deadline = time.monotonic() + 5
        while not parser_ready.exists() and time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.02)
        assert parser_ready.exists(), "payload parser never reached the blocked stage"
        raw_files = list(protocol_tmp.glob("amh-hook-raw.*"))
        protocol_files = list(protocol_tmp.glob("amh-hook-payload.*"))
        assert len(raw_files) == 1
        assert len(protocol_files) == 1
        assert raw_files[0].stat().st_mode & 0o777 == 0o600
        assert raw_prompt.encode("utf-8") in raw_files[0].read_bytes()
        assert raw_prompt.encode("utf-8") in protocol_files[0].read_bytes()
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))

        started = time.monotonic()
        os.kill(proc.pid, hook_signal)
        returncode = proc.wait(timeout=5)
        elapsed = time.monotonic() - started
    finally:
        if proc.poll() is None:
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
        if child_pid is not None and _pid_exists(child_pid):
            os.kill(child_pid, signal.SIGKILL)
        if proc.stdin is not None and not proc.stdin.closed:
            proc.stdin.close()
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()

    assert returncode == 128 + hook_signal
    assert elapsed < 1.0
    assert child_pid is not None
    assert not _pid_exists(child_pid)
    time.sleep(0.2)
    assert not delayed_marker.exists()
    assert list(protocol_tmp.iterdir()) == []


def test_user_prompt_hook_parent_term_stops_blocked_preflight_child(tmp_path):
    hook, preflight_calls, legacy_record_calls = _write_preflight_probe_runtime(tmp_path)
    preflight_ready = tmp_path / "preflight-ready"
    child_pid_path = tmp_path / "preflight-child-pid"
    delayed_marker = tmp_path / "preflight-delayed"
    memory_wrapper = tmp_path / "memory-python"
    memory_wrapper.write_text(
        f"#!{sys.executable}\n"
        "import os\n"
        "import sys\n"
        "import time\n"
        "real = os.environ['REAL_PYTHON']\n"
        "if len(sys.argv) > 2 and sys.argv[1:3] == "
        "['-m', 'agent_brain.memory.evidence.hook_preflight']:\n"
        "    open(os.environ['PREFLIGHT_CHILD_PID'], 'w').write(str(os.getpid()))\n"
        "    open(os.environ['PREFLIGHT_READY'], 'w').write('ready\\n')\n"
        "    time.sleep(30)\n"
        "    open(os.environ['PREFLIGHT_DELAYED'], 'w').write('survived\\n')\n"
        "    raise SystemExit(0)\n"
        "os.execv(real, [real, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    memory_wrapper.chmod(0o755)
    env = _preflight_probe_env(
        tmp_path,
        preflight_calls,
        legacy_record_calls,
        preflight_mode="pass",
    )
    env.update(
        {
            "PREFLIGHT_READY": str(preflight_ready),
            "PREFLIGHT_CHILD_PID": str(child_pid_path),
            "PREFLIGHT_DELAYED": str(delayed_marker),
        }
    )
    proc = subprocess.Popen(
        ["/bin/bash", str(hook)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    child_pid = None
    try:
        assert proc.stdin is not None
        proc.stdin.write(
            json.dumps(
                {
                    "prompt": "block consolidated preflight",
                    "session_id": "parent-term-preflight",
                    "cwd": "/repo",
                    "hook_event_name": "UserPromptSubmit",
                }
            )
        )
        proc.stdin.close()
        deadline = time.monotonic() + 5
        while not preflight_ready.exists() and time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.02)
        assert preflight_ready.exists(), "preflight never reached the blocked stage"
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        protocol_tmp = tmp_path / "protocol-tmp"
        assert len(list(protocol_tmp.glob("amh-hook-raw.*"))) == 1
        assert len(list(protocol_tmp.glob("amh-hook-preflight.*"))) == 1

        started = time.monotonic()
        os.kill(proc.pid, signal.SIGTERM)
        returncode = proc.wait(timeout=5)
        elapsed = time.monotonic() - started
    finally:
        if proc.poll() is None:
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
        if child_pid is not None and _pid_exists(child_pid):
            os.kill(child_pid, signal.SIGKILL)
        if proc.stdin is not None and not proc.stdin.closed:
            proc.stdin.close()
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()

    assert returncode == 128 + signal.SIGTERM
    assert elapsed < 1.0
    assert child_pid is not None
    assert not _pid_exists(child_pid)
    time.sleep(0.2)
    assert not delayed_marker.exists()
    assert list((tmp_path / "protocol-tmp").iterdir()) == []


def test_user_prompt_hook_does_not_execute_shell_syntax_from_prompt(tmp_path):
    hook, preflight_calls, legacy_record_calls = _write_preflight_probe_runtime(tmp_path)
    env = _preflight_probe_env(
        tmp_path,
        preflight_calls,
        legacy_record_calls,
        preflight_mode="pass",
    )
    executed = tmp_path / "prompt-was-executed"
    prompt = f"remember gateway $(touch {executed}) `touch {executed}`; touch {executed}"

    result = subprocess.run(
        ["/bin/bash", str(hook)],
        input=json.dumps(
            {
                "prompt": prompt,
                "session_id": "untrusted-prompt-session",
                "cwd": "/repo/current",
                "hook_event_name": "UserPromptSubmit",
            }
        ),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "consolidated preflight context" in result.stdout
    assert not executed.exists()


def _write_oversize_hook_runtime(
    tmp_path: Path,
    *,
    external_timeout: bool,
    valid_json: bool,
) -> tuple[Path, Path]:
    repo = Path(__file__).resolve().parents[2]
    runtime = tmp_path / "runtime" / "agent_runtime_kit"
    hooks_dir = runtime / "hooks"
    tools_dir = runtime / "tools"
    bin_dir = tmp_path / "bin"
    hooks_dir.mkdir(parents=True)
    tools_dir.mkdir(parents=True)
    bin_dir.mkdir()
    _copy_user_prompt_hook_runtime(repo, hooks_dir, tools_dir)
    (bin_dir / "python3").symlink_to(Path(sys.executable))
    for command in ("dirname", "cat"):
        command_path = shutil.which(command)
        assert command_path is not None
        (bin_dir / command).symlink_to(Path(command_path))
    if external_timeout:
        (bin_dir / "timeout").write_text(
            "#!/bin/sh\nshift\nexec \"$@\"\n",
            encoding="utf-8",
        )
        (bin_dir / "timeout").chmod(0o755)
    (tools_dir / "_resolve-python.sh").write_text(
        f'MEMORY_PYTHON="{sys.executable}"\n_PYTHON_OK=0\n',
        encoding="utf-8",
    )
    (tools_dir / "record-runtime-event.sh").write_text(
        "#!/bin/sh\nexit 0\n",
        encoding="utf-8",
    )
    producer = (
        "payload = {\n"
        "    'status': 'injected',\n"
        "    'reason': 'included',\n"
        "    'context': 'OVERSIZE_PRIVATE_CONTEXT_' + 'X' * 1048576,\n"
        "    'routes': [{'route': 'semantic_raw', 'status': 'ok', "
        "'candidate_count': 1, 'reason': 'route_completed'}],\n"
        "}\n"
        "sys.stdout.write(json.dumps(payload))\n"
        if valid_json
        else "sys.stdout.write('OVERSIZE_GARBAGE_' + 'X' * 1048576)\n"
    )
    (tools_dir / "search-memory.sh").write_text(
        "#!/bin/sh\npython3 - <<'PY'\nimport json\nimport sys\n"
        + producer
        + "PY\n",
        encoding="utf-8",
    )
    for script in tools_dir.iterdir():
        script.chmod(0o755)
    return hooks_dir / "inject-context.sh", bin_dir


def _write_descendant_hook_runtime(tmp_path: Path, *, mode: str) -> tuple[Path, Path, Path]:
    repo = Path(__file__).resolve().parents[2]
    runtime = tmp_path / "runtime" / "agent_runtime_kit"
    hooks_dir = runtime / "hooks"
    tools_dir = runtime / "tools"
    bin_dir = tmp_path / "bin"
    hooks_dir.mkdir(parents=True)
    tools_dir.mkdir(parents=True)
    bin_dir.mkdir()
    _copy_user_prompt_hook_runtime(repo, hooks_dir, tools_dir)
    (bin_dir / "python3").symlink_to(Path(sys.executable))
    for command in ("dirname", "cat"):
        command_path = shutil.which(command)
        assert command_path is not None
        (bin_dir / command).symlink_to(Path(command_path))
    (tools_dir / "_resolve-python.sh").write_text(
        f'MEMORY_PYTHON="{sys.executable}"\n_PYTHON_OK=0\n',
        encoding="utf-8",
    )
    (tools_dir / "record-runtime-event.sh").write_text(
        "#!/bin/sh\nexit 0\n",
        encoding="utf-8",
    )
    child_ready_path = tmp_path / "descendant.ready"
    publish_ready = (
        "publish_ready() {\n"
        "  child=$1\n"
        "  kill -0 \"$child\" || exit 91\n"
        "  ready_tmp=\"${CHILD_PID_FILE}.tmp.$$\"\n"
        "  printf 'ready:%s\\n' \"$child\" > \"$ready_tmp\"\n"
        "  /bin/mv \"$ready_tmp\" \"$CHILD_PID_FILE\"\n"
        "  published=$(cat \"$CHILD_PID_FILE\")\n"
        "  [ \"$published\" = \"ready:$child\" ] || exit 92\n"
        "  kill -0 \"$child\" || exit 93\n"
        "}\n"
    )
    if mode == "timeout":
        search_body = (
            "/bin/sleep 30 &\n"
            "child=$!\n"
            "publish_ready \"$child\"\n"
            "wait\n"
        )
    elif mode == "nonzero":
        search_body = (
            "/bin/sleep 30 >/dev/null 2>&1 &\n"
            "child=$!\n"
            "publish_ready \"$child\"\n"
            "exit 9\n"
        )
    else:
        search_body = (
            "/bin/sleep 30 &\n"
            "child=$!\n"
            "publish_ready \"$child\"\n"
            "python3 - <<'PY'\n"
            "import sys\n"
            "sys.stdout.write('X' * 1048577)\n"
            "PY\n"
            "wait\n"
        )
    (tools_dir / "search-memory.sh").write_text(
        "#!/bin/sh\n"
        + publish_ready
        + search_body,
        encoding="utf-8",
    )
    for script in tools_dir.iterdir():
        script.chmod(0o755)
    return hooks_dir / "inject-context.sh", bin_dir, child_ready_path


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.parametrize(
    ("mode", "expected_latency_status"),
    [("timeout", "timeout"), ("overflow", "error"), ("nonzero", "error")],
)
def test_user_prompt_hook_python_fallback_reaps_search_descendants(
    tmp_path,
    mode,
    expected_latency_status,
):
    hook, bin_dir, child_ready_path = _write_descendant_hook_runtime(tmp_path, mode=mode)
    brain_dir = tmp_path / "brain"
    env = {
        **os.environ,
        "PATH": str(bin_dir),
        "PYTHONPATH": f"{Path(__file__).resolve().parents[2]}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
        "BRAIN_DIR": str(brain_dir),
        "CHILD_PID_FILE": str(child_ready_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "AGENT_MEMORY_HUB_SEARCH_TIMEOUT_SECONDS": "2.0",
    }

    started = time.monotonic()
    result = subprocess.run(
        ["/bin/bash", str(hook)],
        input=json.dumps(
            {
                "prompt": "process group cleanup",
                "session_id": f"cleanup-{mode}",
                "cwd": "/repo/current",
                "hook_event_name": "UserPromptSubmit",
            }
        ),
        env=env,
        capture_output=True,
        text=True,
        timeout=7,
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}
    if mode == "timeout":
        assert 1.5 <= elapsed < 5.0, elapsed
    else:
        assert elapsed < 5.0, elapsed
    assert child_ready_path.exists(), "search helper never published its ready sentinel"
    ready = child_ready_path.read_text(encoding="utf-8").strip()
    assert ready.startswith("ready:"), ready
    pid = int(ready.removeprefix("ready:"))
    try:
        deadline = time.monotonic() + 2
        while _pid_exists(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _pid_exists(pid), "search descendant survived fallback cleanup"
    finally:
        if _pid_exists(pid):
            os.kill(pid, signal.SIGKILL)
    rows = [
        json.loads(line)
        for line in (brain_dir / "runtime" / "hook-latency.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert rows[-1]["status"] == expected_latency_status


@pytest.mark.parametrize("external_timeout", (False, True))
@pytest.mark.parametrize("valid_json", (False, True))
def test_user_prompt_hook_bounds_search_stdout_before_shell_capture(
    tmp_path,
    external_timeout,
    valid_json,
):
    hook, bin_dir = _write_oversize_hook_runtime(
        tmp_path,
        external_timeout=external_timeout,
        valid_json=valid_json,
    )
    brain_dir = tmp_path / "brain"
    env = {
        **os.environ,
        "PATH": str(bin_dir),
        "PYTHONPATH": f"{Path(__file__).resolve().parents[2]}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
        "BRAIN_DIR": str(brain_dir),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT": "json",
        "AGENT_MEMORY_HUB_SEARCH_TIMEOUT_SECONDS": "2",
    }

    result = subprocess.run(
        ["/bin/bash", str(hook)],
        input=json.dumps(
            {
                "prompt": "bounded hook output",
                "session_id": "bounded-output-session",
                "cwd": "/repo/current",
                "hook_event_name": "UserPromptSubmit",
            }
        ),
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert "OVERSIZE_" not in result.stdout
    assert json.loads(result.stdout) == {}
    rows = [
        json.loads(line)
        for line in (brain_dir / "runtime" / "hook-latency.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert rows[-1]["stage"] == "search_memory"
    assert rows[-1]["status"] == "error"


@pytest.mark.parametrize(
    "prompt",
    (
        "为什么之前那个方案没有生效",
        "--help",
        "-n something",
        "第一行  保留空格\n第二行\t保留制表符",
    ),
)
def test_user_prompt_hook_delegates_complete_prompt_to_routed_cli(tmp_path, prompt):
    hook, args_path = _write_fake_routed_hook_runtime(
        tmp_path,
        search_stdout=json.dumps(
            {
                "status": "injected",
                "reason": "included",
                "context": "[fact] routed raw recall",
                "routes": [
                    {
                        "route": "semantic_raw",
                        "status": "ok",
                        "candidate_count": 1,
                        "reason": "route_completed",
                    }
                ],
            },
            ensure_ascii=False,
        ),
    )
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path / "brain"),
        "FAKE_SEARCH_ARGS": str(args_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT": "json",
    }

    result = subprocess.run(
        ["/bin/bash", str(hook)],
        input=json.dumps(
            {
                "prompt": prompt,
                "session_id": "routed-hook-session",
                "cwd": "/repo/current",
                "hook_event_name": "UserPromptSubmit",
            },
            ensure_ascii=False,
        ),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "[fact] routed raw recall" in context
    args = json.loads(args_path.read_text(encoding="utf-8"))
    assert args[-2:] == ["--", prompt]
    for flag in (
        "--routed-recall",
        "--context-firewall",
        "--record-injection-cohort",
        "--record-recall-gap",
        "--top-k",
        "--prefer-type",
        "--adapter",
        "--session",
        "--cwd",
    ):
        assert flag in args
    assert args[args.index("--format") + 1] == "hook-json"


def test_user_prompt_hook_does_not_put_large_raw_prompt_in_parser_argv(tmp_path):
    sentinel = "RAW_ARGV_SENTINEL_"
    prompt = (
        "hooks memory\n\n<system-reminder>\n"
        + sentinel
        + ("X" * (2 * 1024 * 1024))
        + "\n</system-reminder>"
    )
    hook, args_path = _write_fake_routed_hook_runtime(
        tmp_path,
        search_stdout=json.dumps(
            {
                "status": "injected",
                "reason": "included",
                "context": "[fact] large raw prompt stayed off argv",
                "routes": [
                    {
                        "route": "lexical_terms",
                        "status": "ok",
                        "candidate_count": 1,
                        "reason": "route_completed",
                    }
                ],
            }
        ),
    )
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path / "brain"),
        "FAKE_SEARCH_ARGS": str(args_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT": "json",
    }

    result = subprocess.run(
        ["/bin/bash", str(hook)],
        input=json.dumps(
            {
                "prompt": prompt,
                "session_id": "large-raw-prompt",
                "cwd": "/repo/current",
                "hook_event_name": "UserPromptSubmit",
            }
        ),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "large raw prompt stayed off argv" in context
    search_args = args_path.read_text(encoding="utf-8")
    assert sentinel not in search_args


def test_user_prompt_hook_fails_closed_on_malformed_hook_json(tmp_path):
    hook, args_path = _write_fake_routed_hook_runtime(
        tmp_path,
        search_stdout="not-json",
    )
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path / "brain"),
        "FAKE_SEARCH_ARGS": str(args_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT": "json",
    }

    result = subprocess.run(
        ["/bin/bash", str(hook)],
        input=json.dumps(
            {
                "prompt": "hooks memory",
                "session_id": "malformed-hook-session",
                "cwd": "/repo/current",
                "hook_event_name": "UserPromptSubmit",
            }
        ),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}


@pytest.mark.parametrize(
    ("route_name", "route_status", "route_reason"),
    (
        ("semantic_raw", "ok", None),
        ("semantic_raw", "ok", "route_error"),
        ("semantic_raw", "skipped", "route_completed"),
        ("semantic_raw", "skipped", "unknown_skip"),
        ("semantic_raw", "timeout", "route_completed"),
        ("semantic_raw", "error", "route_timeout"),
        ("unknown_route", "ok", "route_completed"),
    ),
)
def test_user_prompt_hook_rejects_invalid_route_status_reason_matrix(
    tmp_path,
    route_name,
    route_status,
    route_reason,
):
    hook, args_path = _write_fake_routed_hook_runtime(
        tmp_path,
        search_stdout=json.dumps(
            {
                "status": "injected",
                "reason": "included",
                "context": "PRIVATE_CONTEXT_MUST_FAIL_CLOSED",
                "routes": [
                    {
                        "route": route_name,
                        "status": route_status,
                        "candidate_count": 1,
                        "reason": route_reason,
                    }
                ],
            }
        ),
    )
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path / "brain"),
        "FAKE_SEARCH_ARGS": str(args_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT": "json",
    }

    result = subprocess.run(
        ["/bin/bash", str(hook)],
        input=json.dumps(
            {
                "prompt": "hooks memory",
                "session_id": "invalid-route-matrix",
                "cwd": "/repo/current",
                "hook_event_name": "UserPromptSubmit",
            }
        ),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "PRIVATE_CONTEXT_MUST_FAIL_CLOSED" not in result.stdout
    assert json.loads(result.stdout) == {}


@pytest.mark.parametrize(
    ("route_status", "route_reason"),
    (
        ("ok", "route_completed"),
        ("skipped", "admission_rejected"),
        ("skipped", "lexical_terms_empty"),
        ("skipped", "semantic_not_ready"),
        ("timeout", "route_timeout"),
        ("error", "route_error"),
    ),
)
def test_user_prompt_hook_accepts_closed_route_status_reason_matrix(
    tmp_path,
    route_status,
    route_reason,
):
    hook, args_path = _write_fake_routed_hook_runtime(
        tmp_path,
        search_stdout=json.dumps(
            {
                "status": "injected",
                "reason": "included",
                "context": "[fact] matrix-approved context",
                "routes": [
                    {
                        "route": "semantic_raw",
                        "status": route_status,
                        "candidate_count": 0,
                        "reason": route_reason,
                    }
                ],
            }
        ),
    )
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path / "brain"),
        "FAKE_SEARCH_ARGS": str(args_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT": "json",
    }

    result = subprocess.run(
        ["/bin/bash", str(hook)],
        input=json.dumps(
            {
                "prompt": "hooks memory",
                "session_id": "valid-route-matrix",
                "cwd": "/repo/current",
                "hook_event_name": "UserPromptSubmit",
            }
        ),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "matrix-approved context" in context


@pytest.mark.parametrize(
    ("prompt", "slug"),
    [("--help", "dash-help"), ("-n something", "dash-n-something")],
)
def test_user_prompt_hook_real_cli_injects_option_like_prompt(tmp_path, prompt, slug):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    store = ItemsStore(tmp_path / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_path / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id=f"mem-20260716-190000-{slug}",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title=f"Option-like prompt {prompt}",
        summary=f"Routed hook must recall the literal prompt {prompt}",
    )
    body = f"Option-like prompt boundary for {prompt}"
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
        ["/bin/bash", str(script)],
        input=json.dumps(
            {
                "prompt": prompt,
                "session_id": f"option-like-{slug}",
                "cwd": "/repo/current",
                "hook_event_name": "UserPromptSubmit",
            }
        ),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert item.title in context


@pytest.mark.parametrize(
    ("status", "reason"),
    [("timeout", "overall_timeout"), ("error", "internal_error")],
)
def test_user_prompt_hook_fails_closed_on_non_injected_protocol_status(
    tmp_path,
    status,
    reason,
):
    hook, args_path = _write_fake_routed_hook_runtime(
        tmp_path,
        search_stdout=json.dumps(
            {
                "status": status,
                "reason": reason,
                "context": "",
                "routes": [],
            }
        ),
    )
    env = {
        **os.environ,
        "BRAIN_DIR": str(tmp_path / "brain"),
        "FAKE_SEARCH_ARGS": str(args_path),
        "AGENT_MEMORY_HUB_ADAPTER": "codex",
        "AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT": "json",
    }

    result = subprocess.run(
        ["/bin/bash", str(hook)],
        input=json.dumps(
            {
                "prompt": "hooks memory",
                "session_id": "non-injected-hook-session",
                "cwd": "/repo/current",
                "hook_event_name": "UserPromptSubmit",
            }
        ),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}


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
    assert "reason: no_candidates" in context
    assert "routed recall returned no injectable context" in context
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
    _copy_user_prompt_hook_runtime(repo, hooks_dir, tools_dir)
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
        "AGENT_MEMORY_HUB_SEARCH_TIMEOUT_SECONDS": "0.5",
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
    assert 0.4 <= elapsed < 4.5, (
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
    _copy_user_prompt_hook_runtime(repo, hooks_dir, tools_dir)
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
    assert json.loads(result.stdout) == {}
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
    _copy_user_prompt_hook_runtime(repo, hooks_dir, tools_dir)
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
    assert json.loads(result.stdout) == {}
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
    assert "full-query routed recall" in context
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
    assert "full-query routed recall" in context
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
    assert cohort.query_terms == ()
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
    assert gaps[0].session_id.startswith("sha256:")
    assert gaps[0].cwd.startswith("sha256:")
    assert gaps[0].query.startswith("sha256:")
    assert gaps[0].injected_ids == ()
    assert gaps[0].rejected_ids == ()
    assert gaps[0].evidence == (
        "retrieved_count=0",
        "included_count=0",
        "hydrate_error_count=0",
        "excluded_count=0",
    )
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
    assert len(gaps) == 1
    assert gaps[0].reason == "empty_recall"
    assert gaps[0].query.startswith("sha256:")


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
            "prompt": "召回矩阵 hook 场景",
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
    assert "full-query routed recall" in context
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
    assert "full-query routed recall" in context
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
            "prompt": "AMH README 深度叙事和算法解释二次打磨",
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
    assert "full-query routed recall" in context
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


def test_user_prompt_hook_empty_prompt_uses_caption_for_recall(tmp_path):
    from agent_brain.memory.evidence.resource_store import ResourceStore

    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    store = ItemsStore(tmp_path / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_path / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260718-010000-empty-prompt-gateway-timeout",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="gateway timeout caption target",
        summary="gateway timeout caption target",
    )
    body = "gateway timeout caption target"
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
        ["/bin/bash", str(script)],
        input=json.dumps(
            {
                "prompt": "",
                "session_id": "empty-prompt-caption",
                "cwd": "/repo/current",
                "hook_event_name": "UserPromptSubmit",
                "images": [
                    {
                        "name": "gateway.png",
                        "uri": "memory://gateway.png",
                        "caption": "gateway timeout caption target",
                    }
                ],
            }
        ),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert item.title in context
    resources = ResourceStore(tmp_path)
    assert len(list(resources.iter_resources())) == 1
    assert len(list(resources.iter_extractions())) == 1


def test_user_prompt_hook_empty_prompt_records_missing_multimodal_gap(tmp_path):
    from agent_brain.memory.evidence.resource_store import ResourceStore
    from agent_brain.memory.governance.recall_events import iter_gap_records

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
        ["/bin/bash", str(script)],
        input=json.dumps(
            {
                "prompt": "",
                "session_id": "empty-prompt-gap",
                "cwd": "/repo/current",
                "hook_event_name": "UserPromptSubmit",
                "images": [{"name": "[Image #1]", "uri": "memory://missing.png"}],
            }
        ),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}
    resources = ResourceStore(tmp_path)
    [resource] = list(resources.iter_resources())
    assert resource.metadata["extraction_status"] == "missing"
    gaps = list(iter_gap_records(tmp_path))
    assert len(gaps) == 1
    assert gaps[0].reason == "multimodal_extraction_missing"


def test_user_prompt_hook_empty_prompt_preflight_failure_uses_legacy_multimodal(
    tmp_path,
):
    from agent_brain.agent_integrations.runtime_events import iter_runtime_events
    from agent_brain.memory.evidence.resource_store import ResourceStore

    hook, preflight_calls, legacy_record_calls = _write_preflight_probe_runtime(
        tmp_path,
        preflight_mode="exit91",
        search_status="injected",
    )
    env = _preflight_probe_env(
        tmp_path,
        preflight_calls,
        legacy_record_calls,
        preflight_mode="exit91",
    )
    result = subprocess.run(
        ["/bin/bash", str(hook)],
        input=json.dumps(
            {
                "prompt": "",
                "session_id": "empty-prompt-fallback",
                "cwd": "/repo/current",
                "hook_event_name": "UserPromptSubmit",
                "images": [
                    {
                        "name": "gateway.png",
                        "uri": "memory://gateway.png",
                        "caption": "gateway timeout fallback caption",
                    }
                ],
            }
        ),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "consolidated preflight context" in result.stdout
    assert preflight_calls.read_text(encoding="utf-8").splitlines() == ["called"]
    assert legacy_record_calls.read_text(encoding="utf-8").splitlines() == ["called"]
    assert len(list(iter_runtime_events(tmp_path / "brain"))) == 1
    resources = ResourceStore(tmp_path / "brain")
    assert len(list(resources.iter_resources())) == 1
    assert len(list(resources.iter_extractions())) == 1
    assert (tmp_path / "search-query.txt").read_text(encoding="utf-8") == (
        "gateway timeout fallback caption"
    )


def test_user_prompt_hook_empty_prompt_without_attachment_does_not_search(tmp_path):
    from agent_brain.agent_integrations.runtime_events import iter_runtime_events

    hook, preflight_calls, legacy_record_calls = _write_preflight_probe_runtime(tmp_path)
    env = _preflight_probe_env(
        tmp_path,
        preflight_calls,
        legacy_record_calls,
        preflight_mode="pass",
    )
    result = subprocess.run(
        ["/bin/bash", str(hook)],
        input=json.dumps(
            {
                "prompt": "",
                "session_id": "empty-prompt-no-attachment",
                "cwd": "/repo/current",
                "hook_event_name": "UserPromptSubmit",
            }
        ),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}
    assert preflight_calls.read_text(encoding="utf-8").splitlines() == ["called"]
    assert not legacy_record_calls.exists()
    assert len(list(iter_runtime_events(tmp_path / "brain"))) == 1
    assert not (tmp_path / "search-query.txt").exists()


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
    import hashlib

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

    raw_prompt = "[Image #1]\n我其他同事执行之后有问题\n\n"
    result = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({
            "prompt": raw_prompt,
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
    assert gaps[0].query == "sha256:" + hashlib.sha256(raw_prompt.encode("utf-8")).hexdigest()
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


def test_user_prompt_hook_missing_multimodal_extraction_does_not_record_gap_when_injected(tmp_path):
    script = Path(__file__).resolve().parents[2] / "agent_runtime_kit" / "hooks" / "inject-context.sh"
    store = ItemsStore(tmp_path / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_path / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260716-200000-multimodal-text-recall",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="同事执行之后有问题",
        summary="即使图片提取缺失，文本问题仍能召回这个安全条目。",
    )
    body = "同事执行之后有问题"
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
        ["/bin/bash", str(script)],
        input=json.dumps(
            {
                "prompt": "[Image #1]\n同事执行之后有问题",
                "session_id": "hook-mm-missing-but-injected",
                "cwd": "/repo/current",
                "hook_event_name": "UserPromptSubmit",
            }
        ),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert item.title in context

    from agent_brain.memory.governance.recall_events import iter_gap_records

    assert list(iter_gap_records(tmp_path)) == []


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

    gaps = list(iter_gap_records(tmp_path))
    assert len(gaps) == 1
    assert gaps[0].reason == "empty_recall"
    assert gaps[0].query.startswith("sha256:")


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
