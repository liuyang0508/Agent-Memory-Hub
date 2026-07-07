from __future__ import annotations

import json
from json import JSONDecodeError
from pathlib import Path

import typer
from rich.table import Table

from agent_brain.evaluation.memory_eval import MemoryEvalReport, default_suite, load_suite
from agent_brain.evaluation.memory_eval import MemoryEvalHarness
from agent_brain.interfaces.cli._app import eval_app
from agent_brain.interfaces.cli._shared import console


@eval_app.command(name="run")
def memory_eval_run(
    suite: Path | None = typer.Option(None, "--suite", help="JSON suite file"),
    top_k: int | None = typer.Option(None, "--top-k", help="Override suite retrieval depth"),
    keep_temp: bool = typer.Option(False, "--keep-temp", help="Keep and report the temporary brain dir"),
    format_: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """Run the offline Memory Eval P0 scenario harness."""

    normalized_format = format_.strip().lower()
    if normalized_format not in {"table", "json"}:
        typer.echo("format must be table or json", err=True)
        raise typer.Exit(2)
    if top_k is not None and top_k <= 0:
        typer.echo("top-k must be positive", err=True)
        raise typer.Exit(2)

    if suite is None:
        suite_payload = default_suite()
    else:
        if not suite.exists():
            typer.echo(f"suite not found: {suite}", err=True)
            raise typer.Exit(2)
        try:
            suite_payload = load_suite(suite)
        except JSONDecodeError as exc:
            typer.echo(f"suite parse error: {exc}", err=True)
            raise typer.Exit(2)

    report = MemoryEvalHarness(keep_temp=keep_temp).run(suite_payload, top_k=top_k)
    if normalized_format == "json":
        typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_report_table(report)

    if not report.passed:
        raise typer.Exit(1)


def _print_report_table(report: MemoryEvalReport) -> None:
    table = Table(title="Memory Eval P0")
    table.add_column("case_id")
    table.add_column("type")
    table.add_column("status")
    table.add_column("key metrics")
    table.add_column("failures")

    for case in report.cases:
        table.add_row(
            case.case_id,
            case.case_type,
            "PASS" if case.passed else "FAIL",
            _compact_metrics(case.metrics),
            ", ".join(case.failures),
        )
    console.print(table)
    console.print("cases: " + ", ".join(case.case_id for case in report.cases))
    console.print(
        "PASS" if report.passed else "FAIL",
        json.dumps(report.metrics, ensure_ascii=False, sort_keys=True),
    )
    if report.temp_brain_dir:
        console.print(f"temp_brain_dir={report.temp_brain_dir}")


def _compact_metrics(metrics: dict[str, float]) -> str:
    return " ".join(f"{key}={value:.3f}" for key, value in sorted(metrics.items()))


__all__ = ["memory_eval_run"]
