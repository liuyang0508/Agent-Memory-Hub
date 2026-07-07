"""CLI evolution commands."""
from __future__ import annotations

from agent_brain.interfaces.cli._app import app
from agent_brain.interfaces.cli._shared import *  # noqa: F401,F403


@app.command()
def consolidate(
    project: str | None = typer.Option(None, "--project", help="Limit to one project"),
    tag: str | None = typer.Option(None, "--tag", help="Limit to one tag"),
    min_group: int = typer.Option(3, "--min-group", help="Minimum L0 facts per group"),
    min_confidence: float = typer.Option(0.6, "--min-confidence", help="Skip facts below this confidence"),
    apply: bool = typer.Option(False, "--apply", help="Write L1 items (default: dry-run preview)"),
) -> None:
    """Consolidate L0 raw facts into L1 items along the abstraction axis (non-destructive)."""
    from agent_brain.memory.governance.consolidation import consolidate as run_consolidate

    store = _store_only()
    report = run_consolidate(
        store,
        min_group=min_group,
        min_confidence=min_confidence,
        project=project,
        tag=tag,
        apply=apply,
    )

    if not report.groups:
        typer.echo(f"No consolidation groups found (scanned {report.scanned} items).")
        return

    if apply:
        typer.echo(
            f"Consolidated {len(report.created)} L1 item(s) from {report.scanned} scanned:"
        )
        for item in report.created:
            typer.echo(f"  + {item.id}  ({len(item.refs.mems)} sources)")
        typer.echo(
            "\nSources kept (non-destructive). Run `memory reindex` to index the new items."
        )
        return

    table = Table(title=f"Consolidation preview (dry-run) — {len(report.groups)} group(s)")
    table.add_column("project")
    table.add_column("tag")
    table.add_column("#src", justify="right")
    table.add_column("mean conf", justify="right")
    table.add_column("source IDs")
    for g in report.groups:
        confs = [it.confidence for it, _ in g.sources]
        mean_c = sum(confs) / len(confs)
        ids_preview = ", ".join(sid[-12:] for sid in g.source_ids[:3])
        if len(g.source_ids) > 3:
            ids_preview += f" (+{len(g.source_ids) - 3})"
        table.add_row(g.project, g.tag, str(len(g.sources)), f"{mean_c:.2f}", ids_preview)
    console.print(table)
    typer.echo("\ndry-run: no items written. Re-run with --apply to create L1 items.")


@app.command(name="evolve")
def evolve(
    apply: bool = typer.Option(False, "--apply", help="Execute approved proposals (default: dry-run only)"),
    format: str = typer.Option("markdown", "--format", help="Output format: json or markdown"),
) -> None:
    """Run self-evolve engine to analyze and propose memory evolution."""
    store = _store_only()
    scanner = SkillScanner()
    idx = None
    if apply:
        brain = _brain_dir()
        idx = HubIndex(db_path=brain / "index.db")

    engine = EvolveEngine(items_store=store, scanner=scanner, dry_run=not apply, index=idx)
    report = engine.evolve()

    if format == "json":
        import json
        data = {
            "scanned_items": report.scanned_items,
            "proposals": [
                {
                    "action": p.action.value,
                    "item_ids": p.item_ids,
                    "title": p.title,
                    "description": p.description,
                    "rationale": p.rationale,
                    "confidence": p.confidence,
                    "audit_passed": p.audit_passed,
                }
                for p in report.proposals
            ],
            "audit_blocked": report.audit_blocked,
            "approved_count": len(report.approved_proposals),
            "executed": report.executed,
        }
        typer.echo(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        typer.echo("# Self-Evolve Report\n")
        typer.echo(f"**Scanned Items**: {report.scanned_items}")
        typer.echo(f"**Total Proposals**: {len(report.proposals)}")
        typer.echo(f"**Audit Blocked**: {report.audit_blocked}")
        typer.echo(f"**Approved**: {len(report.approved_proposals)}")
        if not (not apply):
            typer.echo(f"**Executed**: {report.executed}")
        typer.echo("")

        if report.proposals:
            typer.echo("## Proposals\n")
            for i, proposal in enumerate(report.proposals, 1):
                status_icon = "✅" if proposal.audit_passed is True else ("❌" if proposal.audit_passed is False else "⏳")
                typer.echo(f"### {i}. {status_icon} {proposal.title}\n")
                typer.echo(f"- **Action**: {proposal.action.value}")
                typer.echo(f"- **Items**: {', '.join(proposal.item_ids[:5])}" + (f" (+{len(proposal.item_ids) - 5} more)" if len(proposal.item_ids) > 5 else ""))
                typer.echo(f"- **Confidence**: {proposal.confidence:.2f}")
                typer.echo(f"- **Audit**: {'Passed' if proposal.audit_passed is True else ('Blocked' if proposal.audit_passed is False else 'Pending')}")
                typer.echo("")
                typer.echo(f"**Description**: {proposal.description}\n")
                typer.echo(f"**Rationale**: {proposal.rationale}\n")
                typer.echo("---\n")
        else:
            typer.echo("No evolution proposals generated.\n")

    if report.audit_blocked > 0:
        raise typer.Exit(code=1)


@app.command()
def dream(
    daemon: bool = typer.Option(False, "--daemon", help="Run as persistent background daemon"),
    interval: int = typer.Option(3600, "--interval", help="Seconds between cycles (daemon mode)"),
    no_harvest: bool = typer.Option(False, "--no-harvest", help="Skip transcript harvesting"),
) -> None:
    """Run a dreaming cycle — background memory consolidation (pattern→policy→skill)."""
    from agent_brain.memory.governance.evolve.dreaming import DreamingWorker

    worker = DreamingWorker(
        brain_dir=_brain_dir(),
        interval_seconds=interval,
        harvest_transcripts=not no_harvest,
    )

    if daemon:
        typer.echo(f"dreaming daemon started (interval={interval}s). Ctrl+C to stop.")
        worker.start_daemon()
        try:
            while worker.is_running:
                import time
                time.sleep(1)
        except KeyboardInterrupt:
            worker.stop_daemon()
            typer.echo("daemon stopped.")
    else:
        report = worker.dream_once()
        typer.echo(f"patterns found:        {report.patterns_found}")
        typer.echo(f"policies crystallized: {report.policies_crystallized}")
        typer.echo(f"skills synthesized:    {report.skills_synthesized}")
        typer.echo(f"items archived:        {report.items_archived}")
        typer.echo(f"items harvested:       {report.items_harvested}")
        typer.echo(f"duration:              {report.duration_seconds:.1f}s")
        if report.errors:
            typer.echo(f"errors:                {len(report.errors)}")
            for e in report.errors:
                typer.echo(f"  - {e}", err=True)


__all__ = ["consolidate", "evolve", "dream"]
