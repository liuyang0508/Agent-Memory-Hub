"""CLI knowledge-graph link mutation commands."""
from __future__ import annotations

import typer

from agent_brain.interfaces.cli._app import app
from agent_brain.interfaces.cli._shared import *  # noqa: F401,F403
from agent_brain.memory.governance.graph_links import link_memory_ref
import agent_brain.interfaces.cli as _cli  # noqa: E402  late binding for test-patched helpers


@app.command()
def link(
    source: str = typer.Argument(..., help="Source item ID"),
    target: str = typer.Argument(..., help="Target item ID"),
    label: str = typer.Option("related", "--label"),
) -> None:
    """Create a knowledge-graph link between two memory items."""
    if label.strip().lower() == "supersedes":
        typer.echo(
            "supersedes requires governed lifecycle mutation; use "
            "`memory govern apply-lifecycle --supersede OLD:NEW` "
            "(obsolete:replacement; preview before --apply)",
            err=True,
        )
        raise typer.Exit(2)
    with _cli._managed_components() as (store, idx, _):
        result = link_memory_ref(store, idx, source, target, label)
    if not result.linked:
        typer.echo(f"link blocked: {result.reason}", err=True)
        raise typer.Exit(2)
    if result.index_repair_required:
        typer.echo("link persisted; index repair required", err=True)
        raise typer.Exit(1)
    typer.echo(f"linked: {source} --[{label}]--> {target}")


@app.command()
def unlink(
    source: str = typer.Argument(..., help="Source item ID"),
    target: str = typer.Argument(..., help="Target item ID"),
) -> None:
    """Remove a knowledge-graph link between two memory items."""
    with _cli._managed_components() as (store, idx, _):
        idx.remove_ref(source, target)
        store.unlink_mem(source, target)
    typer.echo(f"unlinked: {source} --> {target}")


__all__ = ["link", "unlink"]
