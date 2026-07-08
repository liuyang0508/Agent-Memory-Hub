"""Explicit install/update commands."""

from __future__ import annotations

from agent_brain.interfaces.cli._app import app
from agent_brain.interfaces.cli._shared import Table, _brain_dir, console, typer


def _render_actions(title: str, actions: list[object]) -> None:
    table = Table(title=title)
    table.add_column("action")
    table.add_column("status")
    table.add_column("detail")
    for action in actions:
        status = str(getattr(action, "status"))
        color = "green" if status in {"ok", "fixed"} else "yellow" if status == "dry-run" else "red"
        table.add_row(
            str(getattr(action, "name")),
            f"[{color}]{status}[/{color}]",
            str(getattr(action, "detail")),
        )
    console.print(table)


@app.command("self-update")
def self_update(
    repair_hooks: bool = typer.Option(
        False,
        "--repair-hooks",
        help="Also repair the memory CLI shim and reinstall core hook adapters",
    ),
    full: bool = typer.Option(
        False,
        "--full",
        help="Run the full installer instead of the minimal CLI installer",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show the repair/update plan without modifying files",
    ),
) -> None:
    """Refresh the current checkout's install state explicitly.

    This command intentionally does not run from hooks and does not silently pull
    remote code. Update the checkout/release first, then run this command to
    repair local shims and integrations.
    """

    from agent_brain.platform import install_repair

    actions = []
    if dry_run:
        actions.append(install_repair.planned_installer_action(minimal=not full))
    else:
        actions.extend(install_repair.run_installer(minimal=not full))

    if repair_hooks:
        actions.extend(install_repair.repair_installation(_brain_dir(), dry_run=dry_run))

    if dry_run:
        for action in actions:
            console.print(f"dry-run: {action.detail}")
    _render_actions("Agent Memory Hub Self Update", actions)
    if install_repair.has_failures(actions):
        raise typer.Exit(1)


__all__ = ["self_update"]
