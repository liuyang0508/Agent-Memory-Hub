"""CLI governance and entity subapp commands."""
from __future__ import annotations

import typer
from rich.table import Table

from agent_brain.interfaces.cli._app import (
    govern_app, entity_app,
)
from agent_brain.interfaces.cli._shared import (
    HubIndex,
    _brain_dir,
    _store_only,
    console,
    get_default_embedder,
)
from agent_brain.contracts.memory_enums import memory_enum_value


@govern_app.command()
def run(
    ttl_days: int = typer.Option(90, "--ttl-days", help="TTL in days for expiry check"),
    format: str = typer.Option("markdown", "--format", help="Output format: json or markdown"),
) -> None:
    """Run governance pipeline to check memory quality."""
    from agent_brain.memory.governance.pipeline import GovernancePipeline

    store = _store_only()
    pipeline = GovernancePipeline(items_store=store, ttl_days=ttl_days)
    report = pipeline.run()
    
    if format == "json":
        import json
        data = {
            "scanned_items": report.scanned_items,
            "total_issues": report.total_issues,
            "duplicates": report.duplicates,
            "noise": report.noise,
            "expired": report.expired,
            "low_quality": report.low_quality,
            "healthy": report.healthy,
            "issues": [
                {
                    "item_id": issue.item_id,
                    "issue_type": issue.issue_type,
                    "severity": issue.severity,
                    "description": issue.description,
                    "suggestion": issue.suggestion,
                }
                for issue in report.issues
            ],
        }
        typer.echo(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        # Markdown format
        lines = []
        lines.append('# Governance Report')
        lines.append('')
        lines.append(f'**Scanned Items**: {report.scanned_items}')
        lines.append(f'**Total Issues**: {report.total_issues}')
        lines.append(f'**Healthy**: {"Yes" if report.healthy else "No"}')
        lines.append('')
        lines.append('## Summary')
        lines.append('')
        lines.append(f'- Duplicates: {report.duplicates}')
        lines.append(f'- Noise: {report.noise}')
        lines.append(f'- Expired: {report.expired}')
        lines.append(f'- Low Quality: {report.low_quality}')
        lines.append('')
        
        if report.issues:
            lines.append('## Issues')
            lines.append('')
            for i, issue in enumerate(report.issues, 1):
                lines.append(f'### {i}. [{issue.severity.upper()}] {issue.issue_type}')
                lines.append('')
                lines.append(f'- **Item ID**: {issue.item_id}')
                lines.append(f'- **Description**: {issue.description}')
                lines.append(f'- **Suggestion**: {issue.suggestion}')
                lines.append('')
        
        typer.echo('\n'.join(lines))
    
    # Exit code: 0 = healthy, 1 = has errors
    raise typer.Exit(code=0 if report.healthy else 1)


@govern_app.command("maturity")
def maturity_report(
    apply: bool = typer.Option(False, "--apply", help="Persist recommended maturity/abstraction"),
    format: str = typer.Option("table", "--format", help="Output format: json or table"),
    top: int = typer.Option(50, "--top", help="Max rows to print; 0 means all"),
    changed_only: bool = typer.Option(
        True,
        "--changed-only/--include-unchanged",
        help="Show only rows where the recommendation differs from current frontmatter",
    ),
) -> None:
    """Score maturity recommendations for memory items."""
    import json

    from agent_brain.memory.governance.maturity_scoring import score_maturity

    store = _store_only()
    rows = []
    applied = 0
    apply_errors: list[dict[str, str]] = []
    for item, _body in store.iter_all():
        score = score_maturity(item)
        current_maturity = memory_enum_value(item.maturity)
        current_abstraction = memory_enum_value(item.abstraction)
        changed = (
            current_maturity != score.maturity
            or current_abstraction != score.abstraction
        )
        if changed_only and not changed:
            continue
        row = {
            "id": item.id,
            "title": item.title,
            "type": str(item.type),
            "current_maturity": current_maturity,
            "current_abstraction": current_abstraction,
            "recommended_maturity": score.maturity,
            "recommended_abstraction": score.abstraction,
            "score": round(score.score, 4),
            "changed": changed,
            "reasons": list(score.reasons),
        }
        if apply and changed:
            try:
                store.update_frontmatter(
                    item.id,
                    maturity=score.maturity,
                    abstraction=score.abstraction,
                )
                row["applied"] = True
                applied += 1
            except FileNotFoundError as exc:
                row["applied"] = False
                row["apply_error"] = str(exc)
                apply_errors.append({"id": item.id, "error": str(exc)})
        rows.append(row)

    rows.sort(key=lambda row: (row["changed"], row["score"]), reverse=True)
    visible_rows = rows if top <= 0 else rows[:top]

    if format == "json":
        typer.echo(json.dumps({
            "scanned_items": len(list(store.iter_all())),
            "returned_items": len(visible_rows),
            "changed_items": sum(1 for row in rows if row["changed"]),
            "applied_items": applied,
            "apply_errors": apply_errors,
            "items": visible_rows,
        }, indent=2, ensure_ascii=False))
        return
    if format != "table":
        typer.echo("format must be json or table", err=True)
        raise typer.Exit(2)

    table = Table(title=f"Maturity recommendations ({len(visible_rows)}/{len(rows)})")
    table.add_column("id")
    table.add_column("type")
    table.add_column("current")
    table.add_column("recommended")
    table.add_column("score", justify="right")
    table.add_column("reasons")
    for row in visible_rows:
        table.add_row(
            row["id"],
            row["type"],
            f"{row['current_maturity']}/{row['current_abstraction']}",
            f"{row['recommended_maturity']}/{row['recommended_abstraction']}",
            f"{row['score']:.4f}",
            ",".join(row["reasons"][:4]),
        )
    console.print(table)
    if apply:
        typer.echo(f"applied maturity recommendations: {applied}")


@govern_app.command("plan")
def maintenance_plan(
    format: str = typer.Option("markdown", "--format", help="Output format: json or markdown"),
    limit: int = typer.Option(20, "--limit", help="Maximum actions to show per lane"),
    action: str | None = typer.Option(None, "--action", help="Show only one action type"),
    category: str | None = typer.Option(None, "--category", help="Show only one action category"),
    index_repair: bool = typer.Option(
        True,
        "--index-repair/--no-index-repair",
        help="Include derived index drift in the maintenance plan",
    ),
    evolve: bool = typer.Option(
        True,
        "--evolve/--no-evolve",
        help="Include evolve proposals in the maintenance plan",
    ),
    conversations: bool = typer.Option(
        True,
        "--conversations/--no-conversations",
        help="Include raw conversation tier maintenance in the plan",
    ),
) -> None:
    """Build a read-only maintenance plan from governance and drift signals."""
    import json

    from agent_brain.memory.governance.auto_governance import AutoGovernanceCycle
    from agent_brain.memory.governance.maintenance_plan import build_maintenance_plan

    brain = _brain_dir()
    store = _store_only()
    index = None
    if index_repair:
        db_path = brain / "index.db"
        if db_path.exists():
            index = HubIndex(db_path=db_path)

    try:
        report = AutoGovernanceCycle(
            brain_dir=brain,
            items_store=store,
            index=index,
            include_index=index is not None,
            include_evolve=evolve,
            include_conversations=conversations,
        ).run(apply=False)
    finally:
        if index is not None:
            index.close()

    plan = build_maintenance_plan(
        report,
        limit_per_lane=limit,
        action_filter=action,
        category_filter=category,
    )

    if format == "json":
        typer.echo(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False))
        return
    if format != "markdown":
        typer.echo("format must be json or markdown", err=True)
        raise typer.Exit(2)

    lines = [
        "# Maintenance Plan",
        "",
        f"**Dry Run**: {'Yes' if plan.dry_run else 'No'}",
        f"**Scanned Items**: {plan.scanned_items}",
        f"**Total Actions**: {plan.action_count}",
        f"**Raw Actions**: {plan.raw_action_count}",
        f"**Suppressed Duplicates**: {plan.suppressed_action_count}",
        "",
        "## Summary",
        "",
        f"- Safe Apply: {plan.safe_apply_count}",
        f"- Review Required: {plan.review_required_count}",
        f"- Blocked: {plan.blocked_count}",
        "",
    ]
    if action or category:
        lines.append("## Filters")
        lines.append("")
        if action:
            lines.append(f"- Action: `{action}`")
        if category:
            lines.append(f"- Category: `{category}`")
        lines.append("")
    if plan.next_commands:
        lines.append("## Next Commands")
        lines.append("")
        for command in plan.next_commands:
            lines.append(f"- `{command}`")
        lines.append("")

    for lane in plan.lanes:
        lines.append(f"## {lane.title}")
        lines.append("")
        lines.append(lane.description)
        lines.append("")
        lines.append(
            f"Count: {lane.count}; Returned: {lane.returned}; "
            f"Truncated: {'Yes' if lane.truncated else 'No'}"
        )
        if lane.next_command:
            lines.append(f"Next: `{lane.next_command}`")
        lines.append("")
        for action in lane.actions:
            lines.append(f"- **{action.action}** [{action.category}]: {action.title}")
            lines.append(f"  - Items: {', '.join(action.item_ids[:5])}")
            lines.append(f"  - Reason: {action.reason}")
            if action.command:
                lines.append(f"  - Command: `{action.command}`")
        lines.append("")

    lines.append("dry-run: no memory items, conversations, or index rows were changed.")
    typer.echo("\n".join(lines))


@govern_app.command("apply-summary-rewrites")
def apply_summary_rewrites_command(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview rewrites without updating memory items.",
    ),
    rollback: bool = typer.Option(
        False,
        "--rollback",
        help="Restore the last summary rewrite snapshot.",
    ),
    limit: int = typer.Option(10, "--limit", help="Maximum summaries to rewrite."),
    target_length: int = typer.Option(200, "--target-length", help="Target summary length."),
    snapshot: bool = typer.Option(
        True,
        "--snapshot/--no-snapshot",
        help="Create a rollback snapshot before applying.",
    ),
    format: str = typer.Option("markdown", "--format", help="Output format: json or markdown"),
) -> None:
    """Apply controlled rewrites for overlong memory summaries."""
    import json

    from agent_brain.memory.governance.summary_rewrite_apply import (
        apply_summary_rewrites,
        rollback_summary_rewrites,
    )

    brain = _brain_dir()
    if rollback:
        try:
            sha = rollback_summary_rewrites(brain_dir=brain)
        except FileNotFoundError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1)
        typer.echo(f"rolled back summary rewrites to {sha[:12]}")
        return

    result = apply_summary_rewrites(
        brain_dir=brain,
        items_store=_store_only(),
        limit=limit,
        target_length=target_length,
        dry_run=dry_run,
        snapshot=snapshot,
    )

    if format == "json":
        typer.echo(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return
    if format != "markdown":
        typer.echo("format must be json or markdown", err=True)
        raise typer.Exit(2)

    lines = [
        "# Summary Rewrite Apply",
        "",
        f"**Dry Run**: {'Yes' if result.dry_run else 'No'}",
        f"**Scanned Items**: {result.scanned_items}",
        f"**Candidates**: {result.candidate_count}",
        f"**Returned**: {result.returned_count}",
        f"**Applied**: {result.applied_count}",
        f"**Target Length**: {result.target_length}",
    ]
    if result.snapshot_sha:
        lines.append(f"**Snapshot**: `{result.snapshot_sha}`")
    lines.append("")
    for change in result.changes:
        lines.append(f"- **{change.item_id}**: {change.current_length} -> {change.candidate_length}")
        lines.append(f"  - Applied: {'yes' if change.applied else 'no'}")
        lines.append(f"  - Candidate: {change.candidate_summary}")
    if result.dry_run:
        lines.append("")
        lines.append("dry-run: no memory items were changed.")
    typer.echo("\n".join(lines))


@govern_app.command("auto")
def auto_governance(
    apply: bool = typer.Option(False, "--apply", help="Apply safe actions only"),
    format: str = typer.Option("table", "--format", help="Output format: json or table"),
    index_repair: bool = typer.Option(
        True,
        "--index-repair/--no-index-repair",
        help="Inspect and optionally repair derived index drift",
    ),
) -> None:
    """Run a safe auto-governance cycle.

    High-risk actions such as archive, delete, consolidate, supersede, and skill
    synthesis are reported as review-required and are never auto-applied here.
    """
    import json

    from agent_brain.memory.governance.auto_governance import AutoGovernanceCycle

    brain = _brain_dir()
    store = _store_only()
    index = None
    embedder = None
    if index_repair:
        db_path = brain / "index.db"
        if db_path.exists() or apply:
            if apply:
                embedder = get_default_embedder()
                index = HubIndex(db_path=db_path, embedding_dim=embedder.dim)
            else:
                index = HubIndex(db_path=db_path)

    try:
        report = AutoGovernanceCycle(
            brain_dir=brain,
            items_store=store,
            index=index,
            embedder=embedder,
            include_index=index is not None,
        ).run(apply=apply)
    finally:
        if index is not None:
            index.close()

    if format == "json":
        typer.echo(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        return
    if format != "table":
        typer.echo("format must be json or table", err=True)
        raise typer.Exit(2)

    table = Table(title=f"Auto governance actions ({len(report.actions)})")
    table.add_column("risk")
    table.add_column("action")
    table.add_column("applied")
    table.add_column("items", justify="right")
    table.add_column("reason")
    for action in report.actions:
        table.add_row(
            action.risk,
            action.action,
            "yes" if action.applied else "no",
            str(len(action.item_ids)),
            action.reason,
        )
    console.print(table)
    typer.echo(
        "safe_apply="
        f"{report.safe_apply_count} review_required={report.review_required_count} "
        f"blocked={report.blocked_count} applied={report.applied_count}"
    )


@entity_app.command("list")
def entity_list(
    min_tag_count: int = typer.Option(3, "--min-tag-count", help="Min items for a tag to count as an entity"),
) -> None:
    """List derived entities (projects / agents / frequent tags)."""
    from agent_brain.memory.governance.entities import extract_entities

    store = _store_only()
    entities = extract_entities(list(store.iter_all()), min_tag_count=min_tag_count)
    if not entities:
        typer.echo("No entities found.")
        return
    table = Table(title=f"Entities ({len(entities)})")
    table.add_column("name")
    table.add_column("kind")
    table.add_column("#items", justify="right")
    for e in entities:
        table.add_row(e.name, e.kind, str(len(e.item_ids)))
    console.print(table)


@entity_app.command("show")
def entity_show(
    name: str = typer.Argument(..., help="Entity name (project / agent / tag)"),
    min_tag_count: int = typer.Option(3, "--min-tag-count", help="Min items for a tag to count as an entity"),
) -> None:
    """Show everything the pool knows about one entity."""
    from agent_brain.memory.governance.entities import build_entity_page, extract_entities

    store = _store_only()
    items = list(store.iter_all())
    entities = extract_entities(items, min_tag_count=min_tag_count)
    match = next((e for e in entities if e.name == name), None)
    if match is None:
        typer.echo(f"Entity not found: {name}", err=True)
        raise typer.Exit(1)
    items_by_id = {it.id: it for it, _ in items}
    typer.echo(build_entity_page(match, items_by_id))


__all__ = ['run', 'maturity_report', 'auto_governance', 'entity_list', 'entity_show']
