"""Commands for injection-effectiveness measurement.

Read-only aggregate view over closed injection cohorts: adoption, rejection and
signal-coverage rates. Complements the per-event ``memory hook recent`` view —
same vocabulary, aggregate denominator. Silence is never treated as adoption.
"""
# mypy: disable-error-code=untyped-decorator

from __future__ import annotations

import json

from agent_brain.interfaces.cli._app import measure_app
from agent_brain.interfaces.cli._shared import Table, _brain_dir, console, typer
from agent_brain.memory.governance.injection_effectiveness import (
    measure_injection_effectiveness,
)


@measure_app.command("injection")
def measure_injection(
    since_days: int = typer.Option(
        0, "--since-days", help="Only count cohorts within the last N days (0 = all)"
    ),
    adapter: str = typer.Option("", "--adapter", help="Filter to one adapter"),
    group_by: str = typer.Option(
        "adapter", "--group-by", help="Grouping dimension: adapter"
    ),
    output_format: str = typer.Option(
        "table", "--format", help="Output: table or json"
    ),
) -> None:
    """Report adoption/rejection/coverage rates over closed injection cohorts."""

    brain = _brain_dir()
    report = measure_injection_effectiveness(
        brain,
        since_days=since_days if since_days > 0 else None,
        adapter=adapter or None,
        group_by=(group_by,) if group_by else (),
    )
    payload = report.to_dict()
    if output_format == "json":
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    rates = report.rates
    table = Table(title="Injection Effectiveness")
    table.add_column("metric", style="cyan")
    table.add_column("value", justify="right")
    table.add_row("closed cohorts", str(rates.closed_total))
    table.add_row("adopted", str(rates.adopted_count))
    table.add_row("rejected", str(rates.rejected_count))
    table.add_row("no signal", str(rates.no_signal_count))
    table.add_row("signal coverage", _pct(rates.signal_coverage))
    table.add_row("adoption rate", _pct(rates.adoption_rate))
    table.add_row("rejection rate", _pct(rates.rejection_rate))
    console.print(table)

    for dimension, groups in report.by_group.items():
        if not groups:
            continue
        group_table = Table(title=f"By {dimension}")
        group_table.add_column(dimension, style="cyan")
        group_table.add_column("closed", justify="right")
        group_table.add_column("adopted", justify="right")
        group_table.add_column("rejected", justify="right")
        group_table.add_column("coverage", justify="right")
        for key in sorted(groups):
            group_rates = groups[key]
            group_table.add_row(
                key,
                str(group_rates.closed_total),
                str(group_rates.adopted_count),
                str(group_rates.rejected_count),
                _pct(group_rates.signal_coverage),
            )
        console.print(group_table)


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


__all__ = ["measure_injection"]
