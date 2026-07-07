from __future__ import annotations

import json

from typer.testing import CliRunner

from agent_brain.interfaces.cli import app


runner = CliRunner()


def test_ml_advisory_gate_blocks_default_promotion_without_evidence() -> None:
    from agent_brain.evaluation.ml_advisory_gate import (
        MLAdvisoryCase,
        evaluate_ml_advisory_cases,
    )

    case = MLAdvisoryCase(
        name="unsafe-default-promotion",
        baseline_score=0.72,
        candidate_score=0.94,
        candidate_mode="default",
        passed_gates=("retrieval",),
        required_gates=("retrieval", "compression", "privacy"),
        expected_recommendation="hold",
    )

    report = evaluate_ml_advisory_cases([case])

    assert report.passed is True
    assert report.metrics["unsafe_promotion_count"] == 0
    assert report.cases[0]["recommendation"] == "hold"
    assert report.cases[0]["allows_default"] is False
    assert any("missing required gates" in reason for reason in report.cases[0]["reasons"])


def test_builtin_ml_advisory_gate_passes_current_policy() -> None:
    from agent_brain.evaluation.ml_advisory_gate import (
        evaluate_ml_advisory_cases,
        load_builtin_ml_advisory_cases,
    )

    report = evaluate_ml_advisory_cases(load_builtin_ml_advisory_cases())

    assert report.passed is True
    assert report.metrics["num_cases"] >= 4
    assert report.metrics["pass_rate"] == 1.0
    assert report.metrics["unsafe_promotion_count"] == 0
    assert report.metrics["mean_delta"] > 0


def test_cli_benchmark_ml_advisory_runs_builtin_fewshot_gate() -> None:
    result = runner.invoke(app, ["benchmark", "ml-advisory", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["metrics"]["num_cases"] >= 4
    assert payload["metrics"]["unsafe_promotion_count"] == 0
