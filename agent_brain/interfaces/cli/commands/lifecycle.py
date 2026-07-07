"""CLI lifecycle commands. Bodies moved verbatim from cli.py (decorators kept →
Typer self-registers on import)."""
from __future__ import annotations

from agent_brain.interfaces.cli._app import (
    app, audit_app, govern_app, tier_app, entity_app, adapter_app,
)
from agent_brain.interfaces.cli._shared import *  # noqa: F401,F403  (imports, helpers, console, CURRENT_SCHEMA_VERSION)
from agent_brain.interfaces.cli.commands.evolution import consolidate, dream, evolve
import agent_brain.interfaces.cli as _cli  # noqa: E402  late binding for test-patched helpers


@app.command(name="decay-status")
def decay_status(
    top_n: int = typer.Option(20, "--top", help="Show top N items by effective score"),
    project: str | None = typer.Option(None, "--project", help="Filter by project"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
) -> None:
    """Show memory items ranked by effective score (confidence × retention decay)."""
    import math
    from datetime import timezone
    from agent_brain.memory.recall.retrieval import retention_factor
    from agent_brain.contracts.memory_item import DECAY_HALF_LIFE_DAYS

    store = _store_only()
    now = datetime.now(timezone.utc)
    rows = []
    for item, _body in store.iter_all():
        if project and item.project != project:
            continue
        last_acc = item.retention.last_accessed
        if last_acc:
            if last_acc.tzinfo is None:
                last_acc = last_acc.replace(tzinfo=timezone.utc)
            days = (now - last_acc).total_seconds() / 86400
        else:
            if item.created_at.tzinfo is None:
                created = item.created_at.replace(tzinfo=timezone.utc)
            else:
                created = item.created_at
            days = (now - created).total_seconds() / 86400
        dc = str(item.retention.decay_class)
        rf = retention_factor(dc, days)
        effective = item.confidence * rf
        rows.append({
            "id": item.id,
            "type": str(item.type),
            "title": item.title[:40],
            "confidence": item.confidence,
            "decay_class": dc,
            "half_life": DECAY_HALF_LIFE_DAYS.get(dc, 60),
            "days_since": round(days, 1),
            "retention": round(rf, 3),
            "effective": round(effective, 3),
            "access_count": item.retention.access_count,
        })

    rows.sort(key=lambda r: r["effective"], reverse=True)
    rows = rows[:top_n]

    if format == "json":
        import json
        typer.echo(json.dumps(rows, indent=2, ensure_ascii=False))
        return

    if not rows:
        typer.echo("No items found.")
        return

    table = Table(title="Decay Status (effective = confidence × retention)")
    table.add_column("rank", justify="right")
    table.add_column("id")
    table.add_column("type")
    table.add_column("conf", justify="right")
    table.add_column("decay", justify="right")
    table.add_column("days", justify="right")
    table.add_column("retention", justify="right")
    table.add_column("effective", justify="right")
    table.add_column("accesses", justify="right")
    for i, r in enumerate(rows, 1):
        eff_color = "green" if r["effective"] >= 0.5 else "yellow" if r["effective"] >= 0.2 else "red"
        table.add_row(
            str(i), r["id"], r["type"],
            f"{r['confidence']:.2f}", r["decay_class"],
            str(r["days_since"]), f"{r['retention']:.3f}",
            f"[{eff_color}]{r['effective']:.3f}[/{eff_color}]",
            str(r["access_count"]),
        )
    console.print(table)


@app.command(name="anti-drift")
def anti_drift(
    staleness_days: int = typer.Option(180, "--staleness-days", help="Staleness threshold in days"),
    format: str = typer.Option("markdown", "--format", help="Output format: json or markdown"),
    check_urls: bool = typer.Option(
        False,
        "--check-urls",
        help="Probe each URL with HTTP HEAD. Slow + requires network. Default off.",
    ),
    url_timeout: float = typer.Option(
        5.0, "--url-timeout", help="Per-URL HTTP HEAD timeout in seconds (--check-urls only)"
    ),
    semantic: bool = typer.Option(
        False,
        "--semantic",
        help="Enable embedding-based semantic contradiction detection (loads model).",
    ),
) -> None:
    """Run drift detector to find stale/contradictory memory items."""
    from agent_brain.memory.governance.drift import DriftDetector

    store = _store_only()
    embedder = _cli.get_default_embedder() if semantic else None
    detector = DriftDetector(
        items_store=store,
        staleness_days=staleness_days,
        check_urls=check_urls,
        url_timeout=url_timeout,
        embedder=embedder,
    )
    report = detector.detect()
    
    if format == "json":
        import json
        data = {
            "scanned_items": report.scanned_items,
            "total_findings": report.total_findings,
            "clean": report.clean,
            "contradictions": report.contradictions,
            "stale": report.stale,
            "citation_rot": report.citation_rot,
            "drift_clusters": report.drift_clusters,
            "findings": [
                {
                    "drift_type": finding.drift_type.value,
                    "item_ids": finding.item_ids,
                    "confidence": finding.confidence,
                    "description": finding.description,
                    "evidence": finding.evidence,
                }
                for finding in report.findings
            ],
        }
        typer.echo(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        # Markdown format
        lines = []
        lines.append('# Anti-Drift Report')
        lines.append('')
        lines.append(f'**Scanned Items**: {report.scanned_items}')
        lines.append(f'**Total Findings**: {report.total_findings}')
        lines.append(f'**Clean**: {"Yes" if report.clean else "No"}')
        lines.append('')
        lines.append('## Summary')
        lines.append('')
        lines.append(f'- Contradictions: {report.contradictions}')
        lines.append(f'- Stale Items: {report.stale}')
        lines.append(f'- Citation ROT: {report.citation_rot}')
        lines.append(f'- Drift Clusters: {report.drift_clusters}')
        lines.append('')
        
        if report.findings:
            lines.append('## Findings')
            lines.append('')
            for i, finding in enumerate(report.findings, 1):
                lines.append(f'### {i}. [{finding.drift_type.value}]')
                lines.append('')
                lines.append(f'- **Item IDs**: {", ".join(finding.item_ids)}')
                lines.append(f'- **Confidence**: {finding.confidence}')
                lines.append(f'- **Description**: {finding.description}')
                lines.append(f'- **Evidence**: {finding.evidence}')
                lines.append('')
        
        typer.echo('\n'.join(lines))
    
    # Exit code: 0 = clean, 1 = has findings
    raise typer.Exit(code=0 if report.clean else 1)


__all__ = ['decay_status', 'anti_drift', 'consolidate', 'evolve', 'dream']
