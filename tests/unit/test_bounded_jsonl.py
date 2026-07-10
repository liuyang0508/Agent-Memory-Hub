"""Bounded JSONL reads must skip hostile rows and continue safely."""

from __future__ import annotations

import json


def test_bounded_jsonl_skips_hostile_rows_and_continues_without_logging_content(
    tmp_path,
    caplog,
) -> None:
    from agent_brain.platform.bounded_jsonl import (
        MAX_JSONL_LINE_BYTES,
        iter_bounded_jsonl,
    )

    path = tmp_path / "hostile.jsonl"
    huge_integer = b'{"SECRET_DIGITS":' + (b"9" * 5000) + b"}\n"
    oversized = b'{"SECRET_OVERSIZED":"' + (
        b"x" * (MAX_JSONL_LINE_BYTES + 100)
    ) + b'"}\n'
    malformed_utf8 = b'{"SECRET_UTF8":"\xff"}\n'
    valid = json.dumps({"ok": 1}).encode("utf-8") + b"\n"
    path.write_bytes(huge_integer + oversized + malformed_utf8 + valid)

    assert list(iter_bounded_jsonl(path)) == [{"ok": 1}]
    assert "SECRET" not in caplog.text

