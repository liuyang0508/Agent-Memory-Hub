from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from typer.testing import CliRunner

from agent_brain.interfaces.cli import app
from agent_brain.memory.context.injection_cohorts import record_injection_cohort
from agent_brain.memory.governance.recall_events import record_gap, record_task_outcome


runner = CliRunner()


def test_hook_recent_json_reports_gaps_injections_and_latency(tmp_path):
    os.environ["BRAIN_DIR"] = str(tmp_path)
    try:
        record_injection_cohort(
            tmp_path,
            item_ids=["mem-a", "mem-b"],
            adapter="codex",
            session_id="s1",
            cwd="/repo",
            query="q",
            now=datetime(2026, 7, 8, 10, 0, tzinfo=timezone.utc),
        )
        record_gap(
            tmp_path,
            query="新增接口|复用接口",
            reason="all_candidates_rejected",
            rejected_ids=["mem-c"],
            evidence=["mem-c:answerability_mismatch"],
            adapter="codex",
            session_id="s1",
            cwd="/repo",
            now=datetime(2026, 7, 8, 10, 1, tzinfo=timezone.utc),
        )
        runtime = tmp_path / "runtime"
        runtime.mkdir(exist_ok=True)
        with (runtime / "hook-latency.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "timestamp": "2026-07-08T10:02:00+00:00",
                "source": "hook",
                "adapter": "codex",
                "session_id": "s1",
                "cwd": "/repo",
                "event_name": "UserPromptSubmit",
                "stage": "search_memory",
                "status": "timeout",
                "detail": "search exceeded internal hook budget",
                "timeout_seconds": 2.0,
            }) + "\n")

        result = runner.invoke(app, ["hook", "recent", "--format", "json", "--limit", "3"])

        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        assert [row["kind"] for row in rows] == ["injection", "recall_gap", "latency"]
        assert rows[0]["status"] == "injected:2"
        assert rows[1]["status"] == "all_candidates_rejected"
        assert rows[1]["rejected_ids"] == ["mem-c"]
        assert rows[2]["status"] == "timeout"
    finally:
        os.environ.pop("BRAIN_DIR", None)


def test_hook_recent_table_filters_by_adapter(tmp_path):
    os.environ["BRAIN_DIR"] = str(tmp_path)
    try:
        record_gap(
            tmp_path,
            query="q",
            reason="empty_recall",
            adapter="codex",
            now=datetime(2026, 7, 8, 10, 0, tzinfo=timezone.utc),
        )
        record_gap(
            tmp_path,
            query="q",
            reason="empty_recall",
            adapter="qoder",
            now=datetime(2026, 7, 8, 10, 1, tzinfo=timezone.utc),
        )

        result = runner.invoke(app, ["hook", "recent", "--adapter", "codex"])

        assert result.exit_code == 0, result.output
        assert "codex" in result.output
        assert "qoder" not in result.output
    finally:
        os.environ.pop("BRAIN_DIR", None)


def test_hook_recent_json_reports_injection_outcome_usage(tmp_path):
    os.environ["BRAIN_DIR"] = str(tmp_path)
    try:
        cohort = record_injection_cohort(
            tmp_path,
            item_ids=["mem-a", "mem-b", "mem-c"],
            adapter="codex",
            session_id="s1",
            cwd="/repo",
            query="q",
            now=datetime(2026, 7, 8, 10, 0, tzinfo=timezone.utc),
        )
        record_task_outcome(
            tmp_path,
            task_id=f"injection-feedback:{cohort.cohort_id}",
            question=f"injection cohort {cohort.cohort_id}",
            outcome="corrected",
            feedback_signals=["injection_feedback", "user_correction"],
            injected_ids=["mem-a", "mem-b", "mem-c"],
            adopted_ids=["mem-a"],
            rejected_ids=["mem-c"],
            adapter="codex",
            session_id="s1",
            cwd="/repo",
            now=datetime(2026, 7, 8, 10, 1, tzinfo=timezone.utc),
        )

        result = runner.invoke(app, ["hook", "recent", "--format", "json", "--limit", "5"])

        assert result.exit_code == 0, result.output
        rows = json.loads(result.output)
        outcome = next(row for row in rows if row["kind"] == "outcome")
        assert outcome["cohort_id"] == cohort.cohort_id
        assert outcome["status"] == "corrected"
        assert outcome["usage"] == {
            "injected": 3,
            "adopted": 1,
            "rejected": 1,
            "ignored": 1,
        }
        assert outcome["ignored_ids"] == ["mem-b"]
    finally:
        os.environ.pop("BRAIN_DIR", None)
