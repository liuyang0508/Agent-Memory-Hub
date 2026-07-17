#!/usr/bin/env python3
"""Parse a hook JSON payload into a fixed NUL-delimited protocol."""

from __future__ import annotations

import json
import sys
from typing import Any


PROTOCOL_VERSION = "amh-hook-payload-v1"


def _fail(message: str) -> int:
    sys.stderr.write(f"parse-hook-payload: {message}\n")
    return 1


def _metadata_string(payload: dict[str, Any], key: str, default: str = "") -> str:
    value = payload.get(key, default)
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _fail("invalid JSON")

    if not isinstance(payload, dict):
        return _fail("expected JSON object")

    prompt_value = payload.get("prompt", "")
    prompt = prompt_value if isinstance(prompt_value, str) else ""
    fields = [
        ("protocol", PROTOCOL_VERSION),
        ("prompt", prompt),
        ("session_id", _metadata_string(payload, "session_id")),
        ("cwd", _metadata_string(payload, "cwd")),
        (
            "hook_event_name",
            _metadata_string(payload, "hook_event_name", "UserPromptSubmit"),
        ),
    ]

    for name, value in fields:
        if "\0" in value:
            return _fail(f"NUL byte in field: {name}")

    try:
        encoded_fields = [value.encode("utf-8") for _, value in fields]
    except UnicodeEncodeError:
        return _fail("protocol field is not valid UTF-8")

    sys.stdout.buffer.write(b"\0".join(encoded_fields) + b"\0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
