"""Knowledge lint CLI command."""

from __future__ import annotations

import json

from agent_brain.interfaces.cli._app import app
from agent_brain.interfaces.cli._shared import Table, _store_only, console, typer
from agent_brain.memory.governance.knowledge_lint import KnowledgeLinter, LintReport


@app.command(name="lint")
def lint_memory(
    output_format: str = typer.Option(
        "table",
        "--format",
        help="Output format: table or json",
    ),
    project: str | None = typer.Option(None, "--project", help="Only lint one project"),
    issue_type: str | None = typer.Option(None, "--type", help="Only show one issue type"),
    limit: int | None = typer.Option(None, "--limit", help="Maximum findings to show"),
    cwd: str | None = typer.Option(None, "--cwd", help="Current scope cwd"),
    repo: str | None = typer.Option(None, "--repo", help="Current scope repo"),
    branch: str | None = typer.Option(None, "--branch", help="Current scope branch"),
    os_name: str | None = typer.Option(None, "--os", help="Current scope operating system"),
    adapter: str | None = typer.Option(None, "--adapter", help="Current scope agent adapter"),
) -> None:
    """Run read-only memory health checks."""
    if output_format not in {"table", "json"}:
        typer.echo("format must be table or json", err=True)
        raise typer.Exit(2)
    if limit is not None and limit < 0:
        typer.echo("limit must be non-negative", err=True)
        raise typer.Exit(2)

    current_scope = _current_scope(
        cwd=cwd,
        repo=repo,
        branch=branch,
        os_name=os_name,
        adapter=adapter,
    )
    report = KnowledgeLinter(
        _store_only(),
        current_scope=current_scope,
    ).run(
        project=project,
        issue_type=issue_type,
        limit=limit,
    )

    if output_format == "json":
        typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return

    _print_table(report)


def _current_scope(
    *,
    cwd: str | None,
    repo: str | None,
    branch: str | None,
    os_name: str | None,
    adapter: str | None,
) -> dict[str, str] | None:
    scope = {
        "cwd": cwd,
        "repo": repo,
        "branch": branch,
        "os": os_name,
        "adapter": adapter,
    }
    filtered = {key: value for key, value in scope.items() if value is not None}
    return filtered or None


def _print_table(report: LintReport) -> None:
    table = Table(title=f"Knowledge Lint ({report.total_findings} findings / {report.total_items} items)")
    table.add_column("issue")
    table.add_column("severity")
    table.add_column("item")
    table.add_column("title")
    table.add_column("suggested action")
    for finding in report.findings:
        table.add_row(
            finding.issue_type,
            finding.severity,
            finding.item_id,
            finding.title,
            finding.suggested_action,
        )
    console.print(table)


__all__ = ["lint_memory"]
