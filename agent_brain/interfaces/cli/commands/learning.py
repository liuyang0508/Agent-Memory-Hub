"""Commands for bounded, replay-gated recall learning."""
# mypy: disable-error-code=untyped-decorator

from __future__ import annotations

import json

from agent_brain.interfaces.cli._app import learning_app
from agent_brain.interfaces.cli._shared import Table, _brain_dir, console, typer
from agent_brain.memory.recall.adaptive_learning import (
    REPORT_RELATIVE_PATH,
    load_learning_profile,
    refresh_learning_profile,
    rollback_learning_profile,
)


@learning_app.command("status")
def learning_status(
    output_format: str = typer.Option("table", "--format", help="Output: table or json"),
) -> None:
    """Show the active derived profile and last replay-gate result."""

    brain = _brain_dir()
    profile = load_learning_profile(brain)
    report_path = brain / REPORT_RELATIVE_PATH
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        report = {}
    profiles = profile.get("profiles")
    rows = profiles if isinstance(profiles, list) else []
    payload = {
        "active": bool(rows),
        "profile_count": len(rows),
        "weighted_items": sum(
            len(row.get("weights", {}))
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("weights"), dict)
        ),
        "last_run": report if isinstance(report, dict) else {},
    }
    _emit(payload, output_format=output_format, title="Adaptive Recall Learning")


@learning_app.command("refresh")
def learning_refresh(
    output_format: str = typer.Option("table", "--format", help="Output: table or json"),
) -> None:
    """Rebuild a candidate profile and activate it only if replay does not regress."""

    _emit(
        refresh_learning_profile(_brain_dir()).to_dict(),
        output_format=output_format,
        title="Adaptive Recall Refresh",
    )


@learning_app.command("rollback")
def learning_rollback() -> None:
    """Restore the previously active profile snapshot."""

    if not rollback_learning_profile(_brain_dir()):
        typer.echo("no previous learning profile", err=True)
        raise typer.Exit(1)
    typer.echo("restored previous learning profile")


def _emit(payload: dict[str, object], *, output_format: str, title: str) -> None:
    if output_format == "json":
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if output_format != "table":
        typer.echo("--format must be table or json", err=True)
        raise typer.Exit(2)
    table = Table(title=title)
    table.add_column("metric")
    table.add_column("value", justify="right")
    for key, value in payload.items():
        table.add_row(key, json.dumps(value, ensure_ascii=False, sort_keys=True))
    console.print(table)


__all__ = ["learning_refresh", "learning_rollback", "learning_status"]
