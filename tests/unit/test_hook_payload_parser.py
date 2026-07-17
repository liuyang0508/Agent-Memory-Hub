from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "agent_runtime_kit"
    / "tools"
    / "parse-hook-payload.py"
)
MISSING = object()


def _run(raw_payload: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=raw_payload,
        capture_output=True,
        check=False,
    )


def _run_payload(payload: object) -> subprocess.CompletedProcess[bytes]:
    return _run(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _fields(stdout: bytes) -> list[str]:
    assert stdout.endswith(b"\0")
    return [field.decode("utf-8") for field in stdout[:-1].split(b"\0")]


def test_parser_emits_versioned_fields_in_fixed_order_with_trailing_nul():
    result = _run_payload(
        {
            "prompt": "remember the gateway",
            "session_id": "session-1",
            "cwd": "/repo",
            "hook_event_name": "UserPromptSubmit",
        }
    )

    assert result.returncode == 0
    assert result.stderr == b""
    assert _fields(result.stdout) == [
        "amh-hook-payload-v1",
        "remember the gateway",
        "session-1",
        "/repo",
        "UserPromptSubmit",
    ]
    assert result.stdout.count(b"\0") == 5


def test_parser_preserves_unicode_and_multiline_prompt():
    prompt = "召回记忆\ncache TTL कितना है"

    result = _run_payload(
        {
            "prompt": prompt,
            "session_id": "会话-一",
            "cwd": "/项目/共享大脑",
            "hook_event_name": "用户提示",
        }
    )

    assert result.returncode == 0
    assert _fields(result.stdout) == [
        "amh-hook-payload-v1",
        prompt,
        "会话-一",
        "/项目/共享大脑",
        "用户提示",
    ]


def test_parser_applies_existing_defaults_to_missing_fields():
    result = _run_payload({})

    assert result.returncode == 0
    assert _fields(result.stdout) == [
        "amh-hook-payload-v1",
        "",
        "",
        "",
        "UserPromptSubmit",
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(MISSING, "", id="missing"),
        pytest.param(None, "", id="null"),
        pytest.param(True, "", id="bool"),
        pytest.param(["value"], "", id="list"),
        pytest.param({"key": 1}, "", id="dict"),
        pytest.param(7, "", id="number"),
    ],
)
def test_parser_preserves_legacy_prompt_type_semantics(value: object, expected: str):
    payload = {} if value is MISSING else {"prompt": value}

    result = _run_payload(payload)

    assert result.returncode == 0
    assert _fields(result.stdout)[1] == expected


@pytest.mark.parametrize("field_index,field", [(2, "session_id"), (3, "cwd")])
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(MISSING, "", id="missing"),
        pytest.param(None, "None", id="null"),
        pytest.param(True, "True", id="bool"),
        pytest.param(["value"], "['value']", id="list"),
        pytest.param({"key": 1}, "{'key': 1}", id="dict"),
        pytest.param(7, "7", id="number"),
    ],
)
def test_parser_preserves_legacy_session_and_cwd_type_semantics(
    field_index: int,
    field: str,
    value: object,
    expected: str,
):
    payload = {} if value is MISSING else {field: value}

    result = _run_payload(payload)

    assert result.returncode == 0
    assert _fields(result.stdout)[field_index] == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(MISSING, "UserPromptSubmit", id="missing"),
        pytest.param(None, "None", id="null"),
        pytest.param(True, "True", id="bool"),
        pytest.param(["value"], "['value']", id="list"),
        pytest.param({"key": 1}, "{'key': 1}", id="dict"),
        pytest.param(7, "7", id="number"),
    ],
)
def test_parser_preserves_legacy_event_type_semantics(value: object, expected: str):
    payload = {} if value is MISSING else {"hook_event_name": value}

    result = _run_payload(payload)

    assert result.returncode == 0
    assert _fields(result.stdout)[4] == expected


@pytest.mark.parametrize("field_index,field", [(2, "session_id"), (3, "cwd"), (4, "hook_event_name")])
def test_parser_strips_all_trailing_newlines_from_metadata(field_index: int, field: str):
    result = _run_payload({field: "value\n\n"})

    assert result.returncode == 0
    assert _fields(result.stdout)[field_index] == "value"


def test_parser_preserves_trailing_newlines_in_prompt():
    prompt = "line one\nline two\n\n"

    result = _run_payload({"prompt": prompt})

    assert result.returncode == 0
    assert _fields(result.stdout)[1] == prompt


def test_parser_preserves_current_compound_type_compatibility_boundary():
    result = _run_payload(
        {
            "prompt": ["not", "a", "string"],
            "session_id": ["session"],
            "cwd": {"path": "/repo"},
            "hook_event_name": {"name": "UserPromptSubmit"},
        }
    )

    assert result.returncode == 0
    assert _fields(result.stdout) == [
        "amh-hook-payload-v1",
        "",
        "['session']",
        "{'path': '/repo'}",
        "{'name': 'UserPromptSubmit'}",
    ]


@pytest.mark.parametrize("raw_payload", [b"", b"{", b'{"prompt": }'])
def test_parser_rejects_invalid_json_without_stdout(raw_payload: bytes):
    result = _run(raw_payload)

    assert result.returncode != 0
    assert result.stdout == b""
    assert result.stderr == b"parse-hook-payload: invalid JSON\n"


@pytest.mark.parametrize("payload", [[], "prompt", 42, True, None])
def test_parser_rejects_non_object_json_without_stdout(payload: object):
    result = _run_payload(payload)

    assert result.returncode != 0
    assert result.stdout == b""
    assert result.stderr == b"parse-hook-payload: expected JSON object\n"


@pytest.mark.parametrize(
    "field",
    ["prompt", "session_id", "cwd", "hook_event_name"],
)
def test_parser_rejects_nul_in_protocol_strings(field: str):
    payload = {
        "prompt": "prompt",
        "session_id": "session",
        "cwd": "/repo",
        "hook_event_name": "UserPromptSubmit",
    }
    payload[field] = "before\0after"

    result = _run_payload(payload)

    assert result.returncode != 0
    assert result.stdout == b""
    assert result.stderr == f"parse-hook-payload: NUL byte in field: {field}\n".encode()


def test_parser_is_executable_and_does_not_import_agent_brain():
    source = SCRIPT.read_text(encoding="utf-8")

    assert os.access(SCRIPT, os.X_OK)
    assert "agent_brain" not in source
