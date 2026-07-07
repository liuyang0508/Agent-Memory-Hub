from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def test_record_injection_cohort_appends_bounded_mechanical_fact(tmp_path: Path) -> None:
    from agent_brain.memory.context.injection_cohorts import (
        injection_cohorts_path,
        iter_injection_cohorts,
        latest_injection_cohort,
        record_injection_cohort,
    )

    cohort = record_injection_cohort(
        tmp_path,
        item_ids=["mem-1", "mem-2", "mem-1"],
        adapter="codex",
        session_id="sess-1",
        cwd="/repo",
        query="用户原始问题 should not be stored",
        now=datetime(2026, 6, 11, 13, 0, tzinfo=timezone.utc),
    )

    assert cohort.adapter == "codex"
    assert cohort.session_id == "sess-1"
    assert cohort.cwd == "/repo"
    assert cohort.item_ids == ("mem-1", "mem-2")
    assert cohort.query_sha256 is not None

    row = json.loads(injection_cohorts_path(tmp_path).read_text(encoding="utf-8").strip())
    assert row == cohort.to_dict()
    assert row["item_ids"] == ["mem-1", "mem-2"]
    assert "query" not in row
    assert "prompt" not in row
    assert "用户原始问题" not in json.dumps(row, ensure_ascii=False)

    assert [item.to_dict() for item in iter_injection_cohorts(tmp_path)] == [cohort.to_dict()]
    assert latest_injection_cohort(tmp_path).to_dict() == cohort.to_dict()


def test_latest_injection_cohort_can_filter_by_adapter_and_session(tmp_path: Path) -> None:
    from agent_brain.memory.context.injection_cohorts import latest_injection_cohort, record_injection_cohort

    record_injection_cohort(
        tmp_path,
        item_ids=["mem-claude-old"],
        adapter="claude_code",
        session_id="sess-1",
        now=datetime(2026, 6, 11, 13, 0, tzinfo=timezone.utc),
    )
    record_injection_cohort(
        tmp_path,
        item_ids=["mem-codex-wrong-session"],
        adapter="codex",
        session_id="sess-2",
        now=datetime(2026, 6, 11, 13, 1, tzinfo=timezone.utc),
    )
    record_injection_cohort(
        tmp_path,
        item_ids=["mem-codex-target"],
        adapter="codex",
        session_id="sess-1",
        now=datetime(2026, 6, 11, 13, 2, tzinfo=timezone.utc),
    )

    latest = latest_injection_cohort(tmp_path, adapter="codex", session_id="sess-1")

    assert latest is not None
    assert latest.item_ids == ("mem-codex-target",)


def test_record_injection_cohort_preserves_pack_metrics(tmp_path: Path) -> None:
    from agent_brain.memory.context.injection_cohorts import (
        iter_injection_cohorts,
        record_injection_cohort,
    )

    metrics = {
        "items": [
            {
                "id": "mem-pack-metric",
                "selected_view": "overview",
                "packed_tokens": 4,
                "full_tokens": 40,
                "compressed": True,
            }
        ],
        "packed_tokens": 4,
        "full_tokens": 40,
    }

    record_injection_cohort(
        tmp_path,
        item_ids=["mem-pack-metric"],
        adapter="codex",
        session_id="sess-pack",
        cwd="/tmp/repo",
        query="pack metric",
        pack_metrics=metrics,
    )

    cohort = next(iter_injection_cohorts(tmp_path))
    assert cohort.pack_metrics == metrics
    assert cohort.to_dict()["pack_metrics"] == metrics


def test_iter_injection_cohorts_accepts_old_records_without_pack_metrics(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "injection-cohorts.jsonl").write_text(
        '{"cohort_id":"old","timestamp":"2026-06-20T00:00:00+00:00",'
        '"item_ids":["mem-old"],"adapter":"codex","session_id":"sess",'
        '"cwd":"/tmp/repo","source":"hook","query_sha256":"abc"}\n',
        encoding="utf-8",
    )

    from agent_brain.memory.context.injection_cohorts import iter_injection_cohorts

    cohort = next(iter_injection_cohorts(tmp_path))
    assert cohort.pack_metrics is None
    assert "pack_metrics" not in cohort.to_dict()
