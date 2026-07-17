#!/usr/bin/env python3
"""Parse a hook JSON payload into a fixed NUL-delimited protocol."""

from __future__ import annotations

import json
import sys
from typing import Any


PROTOCOL_VERSION = "amh-hook-payload-v1"


def _fail(message: str, *, code: int = 1) -> int:
    sys.stderr.write(f"parse-hook-payload: {message}\n")
    return code


def _contains_decoded_nul(value: Any) -> bool:
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            if "\0" in current:
                return True
        elif isinstance(current, dict):
            for key, nested in current.items():
                if isinstance(key, str) and "\0" in key:
                    return True
                stack.append(nested)
        elif isinstance(current, list):
            stack.extend(current)
    return False


def _metadata_string(payload: dict[str, Any], key: str, default: str = "") -> str:
    value = payload.get(key, default)
    rendered = value if isinstance(value, str) else str(value)
    return rendered.rstrip("\n")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError, OSError):
        return _fail("invalid JSON")

    if not isinstance(payload, dict):
        return _fail("expected JSON object")
    if _contains_decoded_nul(payload):
        return _fail("decoded NUL in JSON string", code=2)

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

    try:
        encoded_fields = [value.encode("utf-8") for _, value in fields]
    except UnicodeEncodeError:
        return _fail("protocol field is not valid UTF-8")

    sys.stdout.buffer.write(b"\0".join(encoded_fields) + b"\0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
