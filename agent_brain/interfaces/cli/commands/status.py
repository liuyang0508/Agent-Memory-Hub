"""CLI status commands for statistics and health checks."""
from __future__ import annotations

from agent_brain.interfaces.cli._app import app
from agent_brain.interfaces.cli._shared import *  # noqa: F401,F403  (imports, helpers, console)
from agent_brain.interfaces.cli.status_payloads import build_health_json_payload, build_stats_json_payload


@app.command()
def stats(
    project: str | None = typer.Option(None, "--project", help="Filter by project"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """Show brain pool statistics: item counts, type/project/agent distribution, weekly trend."""
    store = _store_only()
    items = list(store.iter_all())
    s = collect_stats(items, project_filter=project, skipped_count=store.last_scan.skipped_count)

    if format == "json":
        import json

        typer.echo(json.dumps(build_stats_json_payload(s), indent=2, ensure_ascii=False))
        return

    if s.total_items == 0:
        typer.echo("Brain pool is empty.")
        return

    title = "Brain Pool Stats"
    if project:
        title += f" (project={project})"
    console.print(f"[bold]{title}[/bold]\n")

    overview = Table(show_header=False, box=None, padding=(0, 2))
    overview.add_column(style="dim")
    overview.add_column()
    overview.add_row("Total items", str(s.total_items))
    if s.skipped_count:
        overview.add_row("Skipped (parse errors)", str(s.skipped_count))
    overview.add_row("Date range", f"{s.oldest:%Y-%m-%d} .. {s.newest:%Y-%m-%d}" if s.oldest else "—")
    overview.add_row("Avg body length", f"{s.avg_body_length:.0f} chars")
    console.print(overview)
    console.print()

    type_table = Table(title="By Type")
    type_table.add_column("type")
    type_table.add_column("count", justify="right")
    type_table.add_column("pct", justify="right")
    for t, c in s.type_counts.items():
        type_table.add_row(t, str(c), f"{c / s.total_items * 100:.0f}%")
    console.print(type_table)

    if len(s.project_counts) > 1 or "(none)" not in s.project_counts:
        proj_table = Table(title="By Project (top 10)")
        proj_table.add_column("project")
        proj_table.add_column("count", justify="right")
        for p, c in s.project_counts.items():
            proj_table.add_row(p, str(c))
        console.print(proj_table)

    agent_table = Table(title="By Agent")
    agent_table.add_column("agent")
    agent_table.add_column("count", justify="right")
    for a, c in s.agent_counts.items():
        agent_table.add_row(a, str(c))
    console.print(agent_table)

    if s.weekly_trend:
        console.print("\n[bold]Weekly Trend[/bold] (last 8 weeks)")
        max_count = max(c for _, c in s.weekly_trend) if s.weekly_trend else 1
        for week, count in s.weekly_trend:
            bar_len = int(count / max_count * 20) if max_count else 0
            bar = "#" * bar_len
            console.print(f"  {week}  {bar} {count}")

    if s.tag_counts:
        console.print("\n[bold]Top Tags[/bold]")
        tag_parts = [f"{tag}({c})" for tag, c in list(s.tag_counts.items())[:15]]
        console.print("  " + "  ".join(tag_parts))


@app.command()
def health(
    project: str | None = typer.Option(None, "--project", help="Filter by project"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """Run governance + drift checks and show a composite health grade."""
    from agent_brain.memory.governance.drift import DriftDetector
    from agent_brain.memory.governance.pipeline import GovernancePipeline

    store = _store_only()

    pipeline = GovernancePipeline(items_store=store)
    gov_report = pipeline.run()

    detector = DriftDetector(items_store=store)
    drift_report = detector.detect()

    unique_item_ids = {issue.item_id for issue in gov_report.issues}
    score = HealthScore(
        total_items=gov_report.scanned_items,
        items_with_issues=len(unique_item_ids),
        governance_issues=gov_report.total_issues,
        duplicates=gov_report.duplicates,
        noise=gov_report.noise,
        expired=gov_report.expired,
        low_quality=gov_report.low_quality,
        drift_findings=drift_report.total_findings,
        contradictions=drift_report.contradictions,
        stale=drift_report.stale,
        citation_rot=drift_report.citation_rot,
        drift_clusters=drift_report.drift_clusters,
        skipped_items=store.last_scan.skipped_count,
    )

    if format == "json":
        import json

        typer.echo(json.dumps(build_health_json_payload(score), indent=2, ensure_ascii=False))
        raise typer.Exit(code=0 if score.healthy else 1)

    grade_color = "green" if score.healthy else "yellow" if score.grade == "C" else "red"
    console.print(f"\n[bold]Brain Pool Health: [{grade_color}]{score.grade}[/{grade_color}][/bold]\n")

    overview = Table(show_header=False, box=None, padding=(0, 2))
    overview.add_column(style="dim")
    overview.add_column()
    overview.add_row("Total items", str(score.total_items))
    overview.add_row("Issue rate", f"{score.issue_rate:.1%}")
    if score.skipped_items:
        overview.add_row("Skipped (parse errors)", str(score.skipped_items))
    console.print(overview)
    console.print()

    gov_table = Table(title="Governance")
    gov_table.add_column("check")
    gov_table.add_column("count", justify="right")
    gov_table.add_column("status")
    gov_table.add_row("duplicates", str(score.duplicates), "ok" if score.duplicates == 0 else "!")
    gov_table.add_row("noise", str(score.noise), "ok" if score.noise == 0 else "!")
    gov_table.add_row("expired", str(score.expired), "ok" if score.expired == 0 else "!")
    gov_table.add_row("low quality", str(score.low_quality), "ok" if score.low_quality == 0 else "!")
    console.print(gov_table)

    drift_table = Table(title="Anti-Drift")
    drift_table.add_column("check")
    drift_table.add_column("count", justify="right")
    drift_table.add_column("status")
    drift_table.add_row("contradictions", str(score.contradictions), "ok" if score.contradictions == 0 else "!")
    drift_table.add_row("stale", str(score.stale), "ok" if score.stale == 0 else "!")
    drift_table.add_row("citation rot", str(score.citation_rot), "ok" if score.citation_rot == 0 else "!")
    drift_table.add_row("drift clusters", str(score.drift_clusters), "ok" if score.drift_clusters == 0 else "!")
    console.print(drift_table)

    if not score.healthy:
        console.print("\n[dim]Run 'memory govern run' and 'memory anti-drift' for details.[/dim]")

    raise typer.Exit(code=0 if score.healthy else 1)


__all__ = ["stats", "health"]
