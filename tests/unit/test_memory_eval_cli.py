from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from agent_brain.interfaces.cli import app


runner = CliRunner()


def test_memory_eval_run_cli_outputs_json_and_uses_isolated_brain(tmp_path, monkeypatch) -> None:
    real_brain = tmp_path / "real-brain"
    monkeypatch.setenv("BRAIN_DIR", str(real_brain))
    monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")

    result = runner.invoke(app, ["eval", "run", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["metrics"]["recall_at_5"] == 1.0
    assert payload["temp_brain_dir"] is None
    assert not (real_brain / "items").exists()


def test_memory_eval_run_cli_accepts_suite_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")
    suite = Path("tests/fixtures/memory_eval/p0_suite.json")

    result = runner.invoke(app, ["eval", "run", "--suite", str(suite), "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [case["case_type"] for case in payload["cases"]] == [
        "conversation_replay",
        "recall",
        "dynamic_update",
    ]


def test_memory_eval_run_cli_table_output(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")

    result = runner.invoke(app, ["eval", "run"])

    assert result.exit_code == 0, result.output
    assert "Memory Eval P0" in result.output
    assert "conversation-mechanical-harvest" in result.output


def test_memory_eval_run_cli_reports_missing_suite(tmp_path) -> None:
    result = runner.invoke(app, ["eval", "run", "--suite", str(tmp_path / "missing.json")])

    assert result.exit_code == 2
    assert "suite not found" in result.output


def test_memory_eval_run_cli_rejects_non_positive_top_k() -> None:
    result = runner.invoke(app, ["eval", "run", "--top-k", "0"])

    assert result.exit_code == 2
    assert "top-k must be positive" in result.output


def test_memory_eval_run_cli_keep_temp_reports_existing_brain(monkeypatch) -> None:
    monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")

    result = runner.invoke(app, ["eval", "run", "--keep-temp", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["temp_brain_dir"]
    assert Path(payload["temp_brain_dir"]).is_dir()
