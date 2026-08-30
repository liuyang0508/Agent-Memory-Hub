"""CLI knowledge-graph link mutation commands."""
from __future__ import annotations

import typer

from agent_brain.interfaces.cli._app import app
from agent_brain.interfaces.cli._shared import *  # noqa: F401,F403
import agent_brain.interfaces.cli as _cli  # noqa: E402  late binding for test-patched helpers


@app.command()
def link(
    source: str = typer.Argument(..., help="Source item ID"),
    target: str = typer.Argument(..., help="Target item ID"),
    label: str = typer.Option("related", "--label"),
) -> None:
    """Create a knowledge-graph link between two memory items."""
    with _cli._managed_components() as (store, idx, _):
        idx.add_ref(source, target, label)
        store.link_mem(source, target)
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
