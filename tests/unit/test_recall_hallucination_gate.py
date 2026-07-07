from __future__ import annotations

import json

from typer.testing import CliRunner

from agent_brain.interfaces.cli import app


runner = CliRunner()


def test_recall_hallucination_gate_blocks_false_context_injection() -> None:
    from agent_brain.evaluation.recall_hallucination import run_recall_hallucination_gate

    report = run_recall_hallucination_gate(top_k=8)
    payload = report.to_dict()
    metrics = payload["metrics"]

    assert payload["passed"] is True
    assert metrics["negative_cases"] >= 5
    assert metrics["false_injection_count"] == 0
    assert metrics["false_injection_rate"] == 0.0
    assert metrics["positive_cases"] >= 2
    assert metrics["positive_recall_rate"] == 1.0

    rows = {row["name"]: row for row in payload["cases"]}
    assert rows["generic-cjk-noise"]["included_ids"] == []
    assert rows["attachment-placeholder-noise"]["included_ids"] == []
    assert rows["metadata-backed-beta-runtime"]["expected_ids_included"] is True
    assert rows["metadata-backed-alpha-capsule"]["expected_ids_included"] is True
    assert all(not row["forbidden_included_ids"] for row in payload["cases"])


def test_recall_hallucination_gate_cli_outputs_json() -> None:
    result = runner.invoke(app, ["benchmark", "recall-hallucination", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["metrics"]["false_injection_count"] == 0
    assert payload["metrics"]["positive_recall_rate"] == 1.0
