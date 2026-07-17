from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest


def _module():
    from agent_brain.memory.evidence import hook_preflight

    return hook_preflight


def _fields(stdout: bytes) -> list[str]:
    assert stdout.endswith(b"\0")
    return [field.decode("utf-8") for field in stdout[:-1].split(b"\0")]


def _run_cli(raw_payload: bytes, brain_dir, *, adapter: str | None = None):
    env = dict(os.environ)
    if adapter is None:
        env.pop("AGENT_MEMORY_HUB_ADAPTER", None)
    else:
        env["AGENT_MEMORY_HUB_ADAPTER"] = adapter
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_brain.memory.evidence.hook_preflight",
            "--brain-dir",
            str(brain_dir),
        ],
        input=raw_payload,
        capture_output=True,
        check=False,
        env=env,
    )


def _run_payload(payload: object, brain_dir, *, adapter: str | None = None):
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _run_cli(raw, brain_dir, adapter=adapter)


def test_preflight_records_runtime_live_prompt_and_normalizes_prompt(tmp_path) -> None:
    from agent_brain.agent_integrations.runtime_events import iter_runtime_events
    from agent_brain.memory.evidence.conversation_store import ConversationStore

    preflight = _module()
    payload = {
        "prompt": "请召回 gateway 决策\n<system-reminder>available tools: secret</system-reminder>",
        "session_id": "sess-preflight",
        "cwd": "/repo/current",
        "hook_event_name": "UserPromptSubmit",
    }

    result = preflight.run_hook_preflight(payload, brain_dir=tmp_path, adapter="codex")

    assert result == preflight.HookPreflightResult(
        normalized_prompt="请召回 gateway 决策",
        multimodal_recall_text="",
        multimodal_gap_json="",
    )
    [event] = list(iter_runtime_events(tmp_path))
    assert event.adapter == "codex"
    assert event.event_name == "UserPromptSubmit"
    assert event.session_id == "sess-preflight"
    assert event.cwd == "/repo/current"
    [message] = list(ConversationStore(tmp_path).iter_messages())
    assert message.content_text == payload["prompt"]
    assert message.source_agent == "codex"

    assert _fields(preflight.serialize_result(result)) == [
        "amh-hook-preflight-v1",
        "请召回 gateway 决策",
        "",
        "",
    ]


def test_preflight_treats_non_string_prompt_as_empty_and_defaults_event(tmp_path) -> None:
    from agent_brain.agent_integrations.runtime_events import iter_runtime_events

    preflight = _module()

    result = preflight.run_hook_preflight(
        {"prompt": ["not", "text"], "session_id": 7},
        brain_dir=tmp_path,
        adapter="codex",
    )

    assert result.normalized_prompt == ""
    [event] = list(iter_runtime_events(tmp_path))
    assert event.event_name == "UserPromptSubmit"
    assert event.session_id == "7"


def test_preflight_uses_attachment_caption_for_recall_without_gap(tmp_path) -> None:
    preflight = _module()
    payload = {
        "prompt": "[Image #1]\n这个报错是什么？",
        "session_id": "sess-caption",
        "hook_event_name": "UserPromptSubmit",
        "images": [
            {
                "name": "[Image #1]",
                "uri": "memory://screenshot-1",
                "caption": "截图显示 gateway timeout 30s。",
            }
        ],
    }

    result = preflight.run_hook_preflight(payload, brain_dir=tmp_path, adapter="qoder")

    assert result.normalized_prompt == "这个报错是什么？"
    assert result.multimodal_recall_text == "gateway timeout 30s。"
    assert result.multimodal_gap_json == ""


def test_preflight_emits_stable_compact_gap_json_for_missing_attachment_text(tmp_path) -> None:
    preflight = _module()
    payload = {
        "prompt": "[Image #1]\n帮我看一下",
        "session_id": "sess-gap",
        "hook_event_name": "UserPromptSubmit",
    }

    result = preflight.run_hook_preflight(payload, brain_dir=tmp_path, adapter="codex")

    gap = json.loads(result.multimodal_gap_json)
    assert gap["reason"] == "multimodal_extraction_missing"
    assert "multimodal_placeholders=Image#1" in gap["evidence"]
    assert result.multimodal_gap_json == json.dumps(
        gap,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert ": " not in result.multimodal_gap_json


def test_runtime_event_failure_does_not_block_live_prompt_or_normalization(
    tmp_path,
    monkeypatch,
) -> None:
    from agent_brain.memory.evidence.conversation_store import ConversationStore

    preflight = _module()
    monkeypatch.setattr(
        preflight,
        "record_runtime_event",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("event unavailable")),
    )

    result = preflight.run_hook_preflight(
        {"prompt": "保留正文<agent_brain>旧候选</agent_brain>", "session_id": "sess-event-fail"},
        brain_dir=tmp_path,
        adapter="codex",
    )

    assert result.normalized_prompt == "保留正文"
    [message] = list(ConversationStore(tmp_path).iter_messages())
    assert message.content_text == "保留正文<agent_brain>旧候选</agent_brain>"


def test_normalization_failure_falls_back_after_evidence_and_keeps_enrichments_running(
    tmp_path,
    monkeypatch,
) -> None:
    from agent_brain.agent_integrations.runtime_events import iter_runtime_events
    from agent_brain.memory.evidence.conversation_store import ConversationStore

    preflight = _module()
    steps: list[str] = []
    actual_record = preflight.record_runtime_event
    actual_capture = preflight.capture_prompt_payload

    def record_event(*args, **kwargs):
        steps.append("runtime")
        return actual_record(*args, **kwargs)

    def capture_prompt(*args, **kwargs):
        steps.append("capture")
        return actual_capture(*args, **kwargs)

    def fail_normalization(prompt):
        steps.append("normalize")
        raise OSError("normalizer unavailable")

    def recall_text(*args, **kwargs):
        steps.append("recall")
        return "attachment context"

    def gap_payload(*args, **kwargs):
        steps.append("gap")
        return None

    monkeypatch.setattr(preflight, "record_runtime_event", record_event)
    monkeypatch.setattr(preflight, "capture_prompt_payload", capture_prompt)
    monkeypatch.setattr(preflight, "normalize_hook_prompt_for_recall", fail_normalization)
    monkeypatch.setattr(preflight, "recall_text_for_payload", recall_text)
    monkeypatch.setattr(preflight, "multimodal_gap_payload_for_payload", gap_payload)
    prompt = "原始 prompt <system-reminder>仍需回退原文</system-reminder>"

    result = preflight.run_hook_preflight(
        {"prompt": prompt, "session_id": "sess-normalize-fail"},
        brain_dir=tmp_path,
        adapter="codex",
    )

    assert steps == ["runtime", "capture", "normalize", "recall", "gap"]
    assert result.normalized_prompt == prompt
    assert result.multimodal_recall_text == "attachment context"
    assert list(iter_runtime_events(tmp_path))[0].session_id == "sess-normalize-fail"
    assert list(ConversationStore(tmp_path).iter_messages())[0].content_text == prompt


def test_false_live_prompt_capture_triggers_multimodal_resource_capture(
    tmp_path,
    monkeypatch,
) -> None:
    preflight = _module()
    resource_calls: list[dict[str, object]] = []
    monkeypatch.setattr(preflight, "capture_prompt_payload", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        preflight,
        "capture_multimodal_prompt_resources",
        lambda payload, **kwargs: resource_calls.append(payload) or [],
    )

    result = preflight.run_hook_preflight(
        {"prompt": "ordinary prompt"},
        brain_dir=tmp_path,
        adapter="codex",
    )

    assert result.normalized_prompt == "ordinary prompt"
    assert len(resource_calls) == 1
    assert resource_calls[0]["prompt"] == "ordinary prompt"


def test_live_prompt_failure_still_attempts_multimodal_resource_capture(
    tmp_path,
    monkeypatch,
) -> None:
    preflight = _module()
    calls: list[object] = []
    actual_capture = preflight.capture_multimodal_prompt_resources

    def fail_prompt_capture(*args, **kwargs):
        raise OSError("conversation unavailable")

    def capture_resources(*args, **kwargs):
        calls.append(args[0])
        return actual_capture(*args, **kwargs)

    monkeypatch.setattr(preflight, "capture_prompt_payload", fail_prompt_capture)
    monkeypatch.setattr(preflight, "capture_multimodal_prompt_resources", capture_resources)
    payload = {
        "prompt": "[Image #1]",
        "session_id": "sess-resource-fallback",
        "images": [{"name": "[Image #1]", "caption": "缓存 TTL 为 30 秒"}],
    }

    result = preflight.run_hook_preflight(payload, brain_dir=tmp_path, adapter="codex")

    assert len(calls) == 1
    assert calls[0]["prompt"] == payload["prompt"]
    assert result.multimodal_recall_text == "缓存 TTL 为 30 秒"
    assert result.multimodal_gap_json == ""


def test_recall_failure_does_not_block_independent_gap_generation(tmp_path, monkeypatch) -> None:
    preflight = _module()
    monkeypatch.setattr(
        preflight,
        "recall_text_for_payload",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("recall unavailable")),
    )
    monkeypatch.setattr(
        preflight,
        "multimodal_gap_payload_for_payload",
        lambda *args, **kwargs: {"reason": "missing", "evidence": ["资源不存在"]},
    )

    result = preflight.run_hook_preflight(
        {"prompt": "[Image #1]"},
        brain_dir=tmp_path,
        adapter="codex",
    )

    assert result.multimodal_recall_text == ""
    assert result.multimodal_gap_json == '{"evidence":["资源不存在"],"reason":"missing"}'


def test_gap_failure_does_not_discard_recall_text(tmp_path, monkeypatch) -> None:
    preflight = _module()
    monkeypatch.setattr(preflight, "recall_text_for_payload", lambda *args, **kwargs: "caption")
    monkeypatch.setattr(
        preflight,
        "multimodal_gap_payload_for_payload",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("gap unavailable")),
    )

    result = preflight.run_hook_preflight(
        {"prompt": "[Image #1]"},
        brain_dir=tmp_path,
        adapter="codex",
    )

    assert result.multimodal_recall_text == "caption"
    assert result.multimodal_gap_json == ""


@pytest.mark.parametrize(
    "raw_payload",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"{", id="malformed"),
        pytest.param(b'{"prompt":"\xff"}', id="invalid-utf8"),
        pytest.param(b'{"session_id":' + (b"9" * 5000) + b"}", id="oversized-integer"),
        pytest.param(
            b'{"prompt":' + (b"[" * 10_000) + b"0" + (b"]" * 10_000) + b"}",
            id="deep-json",
        ),
    ],
)
def test_cli_stabilizes_invalid_input_without_stdout_or_payload_leak(
    tmp_path,
    raw_payload: bytes,
) -> None:
    result = _run_cli(raw_payload, tmp_path)

    assert result.returncode == 2
    assert result.stdout == b""
    assert b"Traceback" not in result.stderr
    if raw_payload:
        assert raw_payload[:40] not in result.stderr
    assert result.stderr.startswith(b"hook-preflight: ")


@pytest.mark.parametrize("payload", [[], "prompt", 42, True, None])
def test_cli_rejects_non_object_json(tmp_path, payload: object) -> None:
    result = _run_payload(payload, tmp_path)

    assert result.returncode == 2
    assert result.stdout == b""
    assert result.stderr == b"hook-preflight: expected JSON object\n"


def test_serialize_rejects_nul_in_every_result_field() -> None:
    preflight = _module()

    for field in (
        "normalized_prompt",
        "multimodal_recall_text",
        "multimodal_gap_json",
    ):
        values = {
            "normalized_prompt": "prompt",
            "multimodal_recall_text": "recall",
            "multimodal_gap_json": "gap",
        }
        values[field] = "before\0after"
        with pytest.raises(ValueError, match="NUL byte"):
            preflight.serialize_result(preflight.HookPreflightResult(**values))


def test_preflight_preserves_unicode_and_bounds_attachment_recall_near_4000_chars(
    tmp_path,
) -> None:
    preflight = _module()
    caption = "记" * 4100

    result = preflight.run_hook_preflight(
        {
            "prompt": "[Image #1]\n分析附件",
            "session_id": "边界会话",
            "images": [{"name": "[Image #1]", "caption": caption}],
        },
        brain_dir=tmp_path,
        adapter="悟空",
    )

    assert result.normalized_prompt == "分析附件"
    assert result.multimodal_recall_text == "记" * 4000
    encoded = preflight.serialize_result(result)
    assert encoded.count(b"\0") == 4
    assert _fields(encoded) == [
        "amh-hook-preflight-v1",
        "分析附件",
        "记" * 4000,
        "",
    ]


def test_cli_emits_only_fixed_protocol_and_uses_adapter_environment(tmp_path) -> None:
    from agent_brain.agent_integrations.runtime_events import iter_runtime_events

    payload = {
        "prompt": "召回 gateway",
        "session_id": "sess-cli",
        "cwd": "/repo",
        "hook_event_name": "UserPromptSubmit",
    }

    result = _run_payload(payload, tmp_path, adapter="qoder_work")

    assert result.returncode == 0
    assert result.stderr == b""
    assert result.stdout.count(b"\0") == 4
    assert _fields(result.stdout) == [
        "amh-hook-preflight-v1",
        "召回 gateway",
        "",
        "",
    ]
    [event] = list(iter_runtime_events(tmp_path))
    assert event.adapter == "qoder_work"


def test_cli_protocol_nul_failure_is_stable_and_does_not_write_stdout(tmp_path) -> None:
    result = _run_payload({"prompt": "before\0after"}, tmp_path)

    assert result.returncode == 2
    assert result.stdout == b""
    assert result.stderr == b"hook-preflight: invalid protocol field\n"
    assert b"before" not in result.stderr


def test_cli_read_io_failure_is_stable(tmp_path, monkeypatch, capsys) -> None:
    preflight = _module()

    class BrokenBuffer:
        def read(self):
            raise OSError("private input path")

    class BrokenInput:
        buffer = BrokenBuffer()

    monkeypatch.setattr(preflight.sys, "stdin", BrokenInput())

    assert preflight.main(["--brain-dir", str(tmp_path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "hook-preflight: invalid JSON\n"


def test_cli_does_not_swallow_stdout_write_failure(tmp_path, monkeypatch) -> None:
    preflight = _module()

    class InputBuffer:
        def read(self):
            return b'{"prompt":"recall"}'

    class Input:
        buffer = InputBuffer()

    class BrokenOutputBuffer:
        def write(self, data):
            raise OSError("stdout closed")

    class BrokenOutput:
        buffer = BrokenOutputBuffer()

    monkeypatch.setattr(preflight.sys, "stdin", Input())
    monkeypatch.setattr(preflight.sys, "stdout", BrokenOutput())

    with pytest.raises(OSError, match="stdout closed"):
        preflight.main(["--brain-dir", str(tmp_path)])
