"""Bounded JSONL reads must skip hostile rows and continue safely."""

from __future__ import annotations

import json

import pytest


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


def test_bounded_jsonl_rejects_entire_file_over_total_byte_budget(
    tmp_path,
    monkeypatch,
) -> None:
    import agent_brain.platform.bounded_jsonl as bounded_jsonl

    path = tmp_path / "over-total-bytes.jsonl"
    raw = b'{"ok":1}\n{"ok":2}\n'
    path.write_bytes(raw)
    monkeypatch.setattr(
        bounded_jsonl,
        "MAX_JSONL_TOTAL_BYTES",
        len(raw) - 1,
        raising=False,
    )

    assert list(bounded_jsonl.iter_bounded_jsonl(path)) == []


def test_bounded_jsonl_rejects_entire_file_over_total_row_budget(
    tmp_path,
    monkeypatch,
) -> None:
    import agent_brain.platform.bounded_jsonl as bounded_jsonl

    path = tmp_path / "over-total-rows.jsonl"
    path.write_text('{"row":1}\n{"row":2}\n{"row":3}\n', encoding="utf-8")
    monkeypatch.setattr(
        bounded_jsonl,
        "MAX_JSONL_TOTAL_ROWS",
        2,
        raising=False,
    )

    assert list(bounded_jsonl.iter_bounded_jsonl(path)) == []


def test_bounded_jsonl_does_not_follow_file_symlinks(tmp_path) -> None:
    from agent_brain.platform.bounded_jsonl import iter_bounded_jsonl

    outside = tmp_path / "outside.jsonl"
    outside.write_text('{"SECRET":"must-not-be-read"}\n', encoding="utf-8")
    path = tmp_path / "linked.jsonl"
    try:
        path.symlink_to(outside)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are unavailable on this platform")

    assert list(iter_bounded_jsonl(path)) == []


class _TrackingRecord:
    live = 0
    peak = 0

    def __init__(self, **values) -> None:
        self.__dict__.update(values)
        type(self).live += 1
        type(self).peak = max(type(self).peak, type(self).live)

    def __del__(self) -> None:
        type(self).live -= 1


def _assert_limit_retains_only_a_bounded_tail(
    monkeypatch,
    *,
    module,
    record_type_name: str,
    iterator,
    rows: list[dict[str, object]],
    tmp_path,
) -> None:
    class TrackingRecord(_TrackingRecord):
        live = 0
        peak = 0

    monkeypatch.setattr(module, record_type_name, TrackingRecord)
    monkeypatch.setattr(module, "iter_bounded_jsonl", lambda _path: iter(rows))

    selected = list(iterator(tmp_path, limit=2))

    assert len(selected) == 2
    assert TrackingRecord.peak <= 3


def test_runtime_event_limit_does_not_retain_the_whole_ledger(
    tmp_path,
    monkeypatch,
) -> None:
    import agent_brain.agent_integrations.runtime_events as runtime_events

    rows = [
        {
            "adapter": "codex",
            "event_name": f"event-{index}",
            "timestamp": f"2026-07-12T00:00:{index:02d}+00:00",
        }
        for index in range(20)
    ]
    _assert_limit_retains_only_a_bounded_tail(
        monkeypatch,
        module=runtime_events,
        record_type_name="AdapterRuntimeEvent",
        iterator=runtime_events.iter_runtime_events,
        rows=rows,
        tmp_path=tmp_path,
    )


def test_adapter_verification_limit_does_not_retain_the_whole_ledger(
    tmp_path,
    monkeypatch,
) -> None:
    import agent_brain.agent_integrations.verifications as verifications

    rows = [
        {
            "adapter": "codex",
            "status": "passed",
            "timestamp": f"2026-07-12T00:00:{index:02d}+00:00",
            "verifier": "pytest",
            "evidence": [],
        }
        for index in range(20)
    ]
    _assert_limit_retains_only_a_bounded_tail(
        monkeypatch,
        module=verifications,
        record_type_name="AdapterVerificationRecord",
        iterator=verifications.iter_adapter_verifications,
        rows=rows,
        tmp_path=tmp_path,
    )


def test_injection_cohort_limit_does_not_retain_the_whole_ledger(
    tmp_path,
    monkeypatch,
) -> None:
    import agent_brain.memory.context.injection_cohorts as injection_cohorts

    rows = [
        {
            "cohort_id": f"inj-20260712T0000{index:02d}-abcdef01",
            "timestamp": f"2026-07-12T00:00:{index:02d}+00:00",
            "item_ids": [f"mem-{index}"],
            "adapter": "unknown",
        }
        for index in range(20)
    ]
    _assert_limit_retains_only_a_bounded_tail(
        monkeypatch,
        module=injection_cohorts,
        record_type_name="InjectionCohort",
        iterator=injection_cohorts.iter_injection_cohorts,
        rows=rows,
        tmp_path=tmp_path,
    )


def test_zero_limit_fails_closed_without_reading_any_ledger(
    tmp_path,
    monkeypatch,
) -> None:
    import agent_brain.agent_integrations.runtime_events as runtime_events
    import agent_brain.agent_integrations.verifications as verifications
    import agent_brain.memory.context.injection_cohorts as injection_cohorts

    def fail_if_read(_path):
        raise AssertionError("limit=0 must not read the ledger")

    for module, iterator in (
        (runtime_events, runtime_events.iter_runtime_events),
        (verifications, verifications.iter_adapter_verifications),
        (injection_cohorts, injection_cohorts.iter_injection_cohorts),
    ):
        monkeypatch.setattr(module, "iter_bounded_jsonl", fail_if_read)
        assert list(iterator(tmp_path, limit=0)) == []
