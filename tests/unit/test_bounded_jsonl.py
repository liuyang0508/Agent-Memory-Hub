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


def test_bounded_jsonl_rejects_short_js_unsafe_and_nonfinite_numbers(tmp_path) -> None:
    from agent_brain.platform.bounded_jsonl import iter_bounded_jsonl

    path = tmp_path / "hostile-numbers.jsonl"
    hostile_rows = [
        '{"value":' + ("9" * 400) + "}",
        '{"value":9007199254740992}',
        '{"value":NaN}',
        '{"value":Infinity}',
        '{"value":-Infinity}',
        '{"value":1e400}',
    ]
    path.write_text(
        "\n".join([*hostile_rows, '{"ok":1}', '{"ok":0.5}']) + "\n",
        encoding="utf-8",
    )

    assert list(iter_bounded_jsonl(path)) == [{"ok": 1}, {"ok": 0.5}]


def test_bounded_jsonl_skips_recursion_bomb_and_keeps_following_row(tmp_path) -> None:
    from agent_brain.platform.bounded_jsonl import iter_bounded_jsonl

    path = tmp_path / "deep-recursion.jsonl"
    depth = 10_000
    deep_row = '{"nested":' + ("[" * depth) + "0" + ("]" * depth) + "}"
    path.write_text(deep_row + '\n{"ok":1}\n', encoding="utf-8")

    assert list(iter_bounded_jsonl(path)) == [{"ok": 1}]


def test_bounded_jsonl_enforces_exported_iterative_nesting_limit(tmp_path) -> None:
    from agent_brain.platform.bounded_jsonl import (
        MAX_JSON_NESTING,
        iter_bounded_jsonl,
    )

    path = tmp_path / "bounded-nesting.jsonl"
    at_limit_lists = MAX_JSON_NESTING - 1
    at_limit = (
        '{"nested":'
        + ("[" * at_limit_lists)
        + "0"
        + ("]" * at_limit_lists)
        + "}"
    )
    over_limit = (
        '{"nested":'
        + ("[" * MAX_JSON_NESTING)
        + "0"
        + ("]" * MAX_JSON_NESTING)
        + "}"
    )
    path.write_text(at_limit + "\n" + over_limit + '\n{"ok":1}\n', encoding="utf-8")

    rows = list(iter_bounded_jsonl(path))

    assert len(rows) == 2
    assert rows[-1] == {"ok": 1}
