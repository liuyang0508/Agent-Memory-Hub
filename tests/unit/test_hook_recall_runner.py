from __future__ import annotations

import json


def test_parse_hook_output_accepts_exact_injection_envelope() -> None:
    from agent_brain.evaluation.hook_recall_runner import parse_hook_output

    raw = json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "[fact] fixture (id:mem-a conf:0.9)",
        }
    }).encode()

    result = parse_hook_output(raw)

    assert result.status == "injected"
    assert result.item_ids == ("mem-a",)
    assert result.protocol_valid is True
    assert result.reason == "included"


def test_parse_hook_output_accepts_exact_empty_object() -> None:
    from agent_brain.evaluation.hook_recall_runner import parse_hook_output

    result = parse_hook_output(b"{}\n")

    assert result.status == "empty"
    assert result.item_ids == ()
    assert result.protocol_valid is True


def test_parse_hook_output_rejects_stdout_contamination() -> None:
    from agent_brain.evaluation.hook_recall_runner import parse_hook_output

    result = parse_hook_output(b"debug\n{}\n")

    assert result.status == "error"
    assert result.protocol_valid is False
    assert result.reason == "malformed_hook_json"


def test_parse_hook_output_rejects_extra_envelope_fields() -> None:
    from agent_brain.evaluation.hook_recall_runner import parse_hook_output

    raw = json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "",
            "debug": "unsafe",
        }
    }).encode()

    result = parse_hook_output(raw)

    assert result.status == "error"
    assert result.protocol_valid is False
    assert result.reason == "invalid_hook_envelope"

