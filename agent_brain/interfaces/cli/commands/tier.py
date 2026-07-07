"""CLI storage tier subcommands."""
from __future__ import annotations

from agent_brain.interfaces.cli._app import tier_app
from agent_brain.interfaces.cli._shared import *  # noqa: F401,F403


@tier_app.command("show")
def tier_show(
    project: str | None = typer.Option(None, "--project", help="Limit to one project"),
    verbose: bool = typer.Option(False, "--verbose", help="List every item with its tier"),
) -> None:
    """Show the hot/warm/cold distribution (derived, offline — no index needed)."""
    from agent_brain.memory.governance.tiering import Tier, scan_tiers, tier_distribution

    store = _store_only()
    pairs = [
        (item, tier)
        for item, tier in scan_tiers(store.items_dir)
        if project is None or item.project == project
    ]
    dist = tier_distribution(t for _, t in pairs)

    table = Table(title="Storage tier distribution")
    table.add_column("tier")
    table.add_column("count", justify="right")
    for t in Tier:
        table.add_row(t.value, str(dist.get(t, 0)))
    console.print(table)

    if verbose:
        for item, tier in sorted(pairs, key=lambda pair: pair[1].value):
            typer.echo(f"  {tier.value:<4}  {item.id}  {item.title[:50]}")


@tier_app.command("rebalance")
def tier_rebalance(
    apply: bool = typer.Option(False, "--apply", help="Persist computed tiers to the index"),
) -> None:
    """Recompute tiers; with --apply, persist them into the sqlite index."""
    from agent_brain.memory.governance.tiering import Tier, rebalance

    store = _store_only()
    idx = HubIndex(db_path=_brain_dir() / "index.db") if apply else None
    report = rebalance(store, index=idx, apply=apply)

    table = Table(title="Tier rebalance")
    table.add_column("tier")
    table.add_column("count", justify="right")
    for t in Tier:
        table.add_row(t.value, str(report.distribution.get(t, 0)))
    console.print(table)

    if apply:
        if idx is not None:
            idx.close()
        typer.echo(f"\nPersisted tier for {report.applied} item(s) to the index.")
    else:
        typer.echo(
            "\ndry-run: tiers computed but not persisted. Re-run with --apply to write to the index."
        )


__all__ = ["tier_show", "tier_rebalance"]
