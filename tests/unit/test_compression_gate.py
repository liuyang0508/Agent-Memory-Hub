from __future__ import annotations

import json

from typer.testing import CliRunner

from agent_brain.interfaces.cli import app


runner = CliRunner()


def test_compression_gate_catches_missing_required_anchor() -> None:
    from agent_brain.evaluation.compression_gate import (
        CompressionCase,
        CompressionOutput,
        evaluate_compression_cases,
    )

    case = CompressionCase(
        name="log-backfire",
        text="\n".join(
            [
                *(f"INFO setup {i}" for i in range(10)),
                "ERROR failed to connect to database",
                "Traceback (most recent call last):",
                '  File "app.py", line 12, in main',
                "ConnectionError: refused",
                "FAILED tests/test_db.py::test_connect - ConnectionError",
            ]
        ),
        budget_chars=180,
        expected_content_type="build_log",
        must_keep=("Traceback", "FAILED tests/test_db.py"),
        must_drop=("INFO setup 9",),
        max_compression_ratio=0.75,
        min_tokens_saved=1,
    )

    def bad_compressor(_case: CompressionCase) -> CompressionOutput:
        return CompressionOutput(
            text="ERROR failed to connect to database",
            content_type="build_log",
            strategy="bad_drop_trace",
            original_tokens=100,
            compressed_tokens=8,
            compression_ratio=0.2,
            reversible=True,
        )

    report = evaluate_compression_cases([case], compressor=bad_compressor)

    assert report.passed is False
    assert report.metrics["pass_rate"] == 0.0
    assert any("missing required anchor" in failure for failure in report.failures)
    assert report.cases[0]["passed"] is False


def test_builtin_compression_fewshot_gate_passes_current_strategy() -> None:
    from agent_brain.evaluation.compression_gate import (
        evaluate_compression_cases,
        load_builtin_compression_cases,
    )

    report = evaluate_compression_cases(load_builtin_compression_cases())

    assert report.passed is True
    assert report.metrics["num_cases"] >= 4
    assert report.metrics["pass_rate"] == 1.0
    assert report.metrics["mean_tokens_saved"] > 0
    assert report.metrics["mean_compression_ratio"] < 0.8


def test_cli_benchmark_compression_runs_builtin_fewshot_gate() -> None:
    result = runner.invoke(app, ["benchmark", "compression", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["metrics"]["num_cases"] >= 4
    assert payload["metrics"]["pass_rate"] == 1.0
