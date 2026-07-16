"""CLI insight commands. Bodies moved verbatim from cli.py (decorators kept →
Typer self-registers on import)."""
from __future__ import annotations

from agent_brain.interfaces.cli._app import app
from agent_brain.interfaces.cli._shared import *  # noqa: F401,F403  (imports, helpers, console, CURRENT_SCHEMA_VERSION)
from agent_brain.interfaces.cli.commands.graph import graph


@app.command()
def version() -> None:
    """Show version."""
    typer.echo(__version__)


@app.command()
def inspect(item_id: str = typer.Argument(..., help="Full or prefix of item ID")) -> None:
    """Inspect a single memory item with governance issues and drift findings (supports ID prefix)."""
    import json
    from agent_brain.memory.governance.pipeline import GovernancePipeline, GovernanceIssue
    from agent_brain.memory.governance.drift import DriftDetector, DriftFinding

    store = _store_only()
    item_id = _resolve_id(store, item_id)

    # Find the item
    target_item = None
    target_body = None
    for item, body in store.iter_all():
        if item.id == item_id:
            target_item = item
            target_body = body
            break
    
    if not target_item:
        typer.echo(f"Error: Item '{item_id}' not found", err=True)
        raise typer.Exit(code=1)
    
    # Output item JSON
    typer.echo("## Memory Item")
    typer.echo("")
    typer.echo(target_item.model_dump_json(indent=2))
    typer.echo("")
    typer.echo("## Body")
    typer.echo("")
    typer.echo(target_body)
    typer.echo("")
    
    # Run governance checks on this item
    pipeline = GovernancePipeline(items_store=store)
    govern_report = pipeline.run()
    item_govern_issues = [issue for issue in govern_report.issues if issue.item_id == item_id]
    
    if item_govern_issues:
        typer.echo("## Governance Issues")
        typer.echo("")
        for issue in item_govern_issues:
            typer.echo(f"- **[{issue.severity.upper()}]** {issue.issue_type}: {issue.description}")
            typer.echo(f"  - Suggestion: {issue.suggestion}")
        typer.echo("")
    else:
        typer.echo("## Governance Issues")
        typer.echo("")
        typer.echo("No governance issues found.")
        typer.echo("")
    
    # Run drift detection on this item
    detector = DriftDetector(items_store=store)
    drift_report = detector.detect()
    item_drift_findings = [
        finding for finding in drift_report.findings if item_id in finding.item_ids
    ]
    
    if item_drift_findings:
        typer.echo("## Drift Findings")
        typer.echo("")
        for finding in item_drift_findings:
            typer.echo(f"- **[{finding.drift_type.value}]** Confidence: {finding.confidence}")
            typer.echo(f"  - Description: {finding.description}")
            typer.echo(f"  - Evidence: {finding.evidence}")
        typer.echo("")
    else:
        typer.echo("## Drift Findings")
        typer.echo("")
        typer.echo("No drift findings found.")
        typer.echo("")


@app.command()
def serve(
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="Bind host. Use 0.0.0.0 only when you intentionally expose Web Admin on the network.",
    ),
    port: int = typer.Option(8765, "--port", help="Bind port"),
    open_browser: bool = typer.Option(False, "--open", help="Open browser on start"),
) -> None:
    """Start the web admin server."""
    try:
        from web.app import serve as _serve
    except ImportError as e:
        typer.echo(f"Web dependencies missing: {e}\npip install 'agent-memory-hub[web]'", err=True)
        raise typer.Exit(1)
    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{display_host}:{port}"
    typer.echo(f"Starting Admin UI on {url}")
    if host in {"0.0.0.0", "::"}:
        typer.echo(
            "Warning: Web Admin is listening on all network interfaces. "
            "Use --host 0.0.0.0 only when you intentionally need LAN or remote access."
        )
    if open_browser:
        import threading
        import webbrowser
        threading.Timer(1.5, webbrowser.open, args=[url]).start()
    _serve(host=host, port=port)


@app.command()
def brief(
    project: str | None = typer.Option(None, "--project", help="Only this project's items"),
    budget_tokens: int = typer.Option(1500, "--budget-tokens", help="Approx token budget for the briefing"),
    query: str | None = typer.Option(None, "--query", help="Bias the briefing toward this task/topic"),
    fail_empty: bool = typer.Option(
        False,
        "--fail-empty",
        help="Exit with code 3 when the brief contains no active memory items.",
    ),
) -> None:
    """Token-budgeted resume briefing (summaries only) — run this first when picking up work."""
    from agent_brain.memory.recall.brief import build_brief

    store = _store_only()
    b = build_brief(store, project=project, budget_tokens=budget_tokens, query=query)
    if b.total_shown == 0:
        typer.echo("no active context to resume")
        if fail_empty:
            raise typer.Exit(3)
        return
    _titles = {"open_signals": "Open signals (blockers)", "recent_handoffs": "Recent handoffs",
               "key_decisions": "Key decisions", "recent_episodes": "Recent episodes"}
    for tier in b.tiers:
        if not tier.shown:
            continue
        typer.echo(f"\n## {_titles[tier.name]}")
        for it in tier.shown:
            typer.echo(it.render())
    if b.total_withheld:
        typer.echo(f"\n… +{b.total_withheld} more items not shown "
                   f"(raise --budget-tokens, or `memory search <topic>` to drill in)")
    typer.echo(f"\n{b.footer}")


__all__ = ['version', 'inspect', 'graph', 'serve', 'brief']
