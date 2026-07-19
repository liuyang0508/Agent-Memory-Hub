"""CLI diagnostic commands."""

from __future__ import annotations

from agent_brain.interfaces.cli._app import app
from agent_brain.interfaces.cli._shared import Path, _doctor_offline, os, typer


def _search_index_status(idx_path: Path) -> str:
    if not idx_path.exists():
        return "MISSING (run: memory reindex)"
    try:
        import sqlite3

        with sqlite3.connect(str(idx_path)) as connection:
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual')"
                ).fetchall()
            }
            required = {"items_meta", "items_fts", "items_vec"}
            if not required.issubset(names):
                return "INVALID (run: memory reindex)"
            connection.execute("SELECT COUNT(*) FROM items_meta").fetchone()
            connection.execute("SELECT COUNT(*) FROM items_fts").fetchone()
        return "OK"
    except Exception:
        return "INVALID (run: memory reindex)"


@app.command()
def doctor(
    offline: bool = typer.Option(
        False, "--offline", help="Report which capabilities work offline right now"
    ),
    verbose: bool = typer.Option(
        False, "--verbose", help="Show bounded detail rows for degraded checks"
    ),
    repair_malformed: bool = typer.Option(
        False,
        "--repair-malformed",
        help="Plan quarantine moves for malformed memory item files",
    ),
    restore_malformed: str | None = typer.Option(
        None,
        "--restore-malformed",
        help="Plan restore of one manually repaired file from items/archived/malformed",
    ),
    apply_repair: bool = typer.Option(
        False,
        "--apply",
        help="Actually apply --repair-malformed quarantine moves or --restore-malformed restore",
    ),
    fix: bool = typer.Option(
        False,
        "--fix",
        help="Repair install-time drift: CLI shim and core hook adapters",
    ),
) -> None:
    """Run diagnostic checks on the Agent Memory Hub installation."""
    import shutil
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    console = Console()

    if fix and offline:
        typer.echo("--fix cannot be combined with --offline", err=True)
        raise typer.Exit(2)

    if fix and (repair_malformed or restore_malformed or apply_repair):
        typer.echo("--fix cannot be combined with malformed-item repair options", err=True)
        raise typer.Exit(2)

    if repair_malformed and restore_malformed:
        typer.echo("--repair-malformed and --restore-malformed are mutually exclusive", err=True)
        raise typer.Exit(2)

    if apply_repair and not (repair_malformed or restore_malformed):
        typer.echo("--apply requires --repair-malformed or --restore-malformed", err=True)
        raise typer.Exit(2)

    if (repair_malformed or restore_malformed) and not offline:
        typer.echo(
            "--repair-malformed and --restore-malformed are only available with --offline", err=True
        )
        raise typer.Exit(2)

    if offline:
        _doctor_offline(
            console,
            verbose=verbose,
            repair_malformed=repair_malformed,
            restore_malformed=restore_malformed,
            apply_repair=apply_repair,
        )
        return

    repair_failed = False
    if fix:
        from agent_brain.platform import install_repair
        from agent_brain.platform.adapter_health import bounded_diagnostic_text

        actions = install_repair.repair_installation(
            brain_dir=Path(os.environ.get("BRAIN_DIR", os.path.expanduser("~/.agent-memory-hub"))),
        )
        repair_failed = install_repair.has_failures(actions)
        repair_table = Table(title="Installation Repair")
        repair_table.add_column("Action", overflow="fold")
        repair_table.add_column("Status")
        repair_table.add_column("Detail", overflow="fold")
        for action in actions:
            status = action.status
            style = (
                "green"
                if status in {"ok", "fixed"}
                else ("yellow" if status == "dry-run" else "red")
            )
            repair_table.add_row(
                Text(bounded_diagnostic_text(action.name)),
                Text(bounded_diagnostic_text(status), style=style),
                Text(bounded_diagnostic_text(action.detail)),
            )
        console.print(repair_table)
        console.print("")

    console.print("[bold]Agent Memory Hub Doctor[/bold]\n")
    checks: list[tuple[str, str, str]] = []

    brain = Path(os.environ.get("BRAIN_DIR", os.path.expanduser("~/.agent-memory-hub")))
    items_dir = brain / "items"
    checks.append(("Brain directory", str(brain), "OK" if brain.exists() else "MISSING"))
    checks.append(("Items directory", str(items_dir), "OK" if items_dir.exists() else "MISSING"))

    item_count = len(list(items_dir.glob("*.md"))) if items_dir.exists() else 0
    checks.append(("Memory items", str(item_count), "OK" if item_count > 0 else "EMPTY"))

    idx_path = brain / "index.db"
    checks.append(("Search index", str(idx_path), _search_index_status(idx_path)))

    try:
        from sentence_transformers import SentenceTransformer  # noqa: F401

        checks.append(("sentence-transformers", "installed", "OK"))
    except ImportError:
        checks.append(("sentence-transformers", "missing", "ERROR"))

    try:
        from fastapi import FastAPI  # noqa: F401

        checks.append(("FastAPI (web)", "installed", "OK"))
    except ImportError:
        checks.append(("FastAPI (web)", "missing", "WARN (pip install 'agent-memory-hub[web]')"))

    remember_cmd = Path.home() / ".claude" / "commands" / "remember.md"
    checks.append(
        ("/remember command", str(remember_cmd), "OK" if remember_cmd.exists() else "MISSING")
    )

    disk_usage = shutil.disk_usage(str(brain)) if brain.exists() else None
    if disk_usage:
        brain_size = sum(f.stat().st_size for f in brain.rglob("*") if f.is_file()) / (1024 * 1024)
        checks.append(("Brain size", f"{brain_size:.1f} MB", "OK"))

    from agent_brain.platform.doctor import probe_memory_cli_shim

    shim = probe_memory_cli_shim()
    if shim["present"]:
        status = "OK" if shim["target_exists"] else "WARN (re-run install.sh)"
        value = f"{shim['path']} -> {shim['target'] or 'unknown target'}"
    else:
        status = "WARN (run install.sh)"
        value = str(shim["path"])
    checks.append(("memory CLI shim", value, status))

    from agent_brain.platform import adapter_health

    adapter_reports = adapter_health.diagnose_configured_core_adapters(brain)
    adapter_failed = any(report.status == "error" for report in adapter_reports)
    for report in adapter_reports:
        value = (
            "configured"
            if not report.non_ok_checks
            else f"{len(report.non_ok_checks)} non-ok check(s)"
        )
        checks.append((f"{report.adapter} adapter", value, report.status.upper()))

    table = Table(title="Diagnostic Results")
    table.add_column("Check", style="bold")
    table.add_column("Value")
    table.add_column("Status")
    for name, value, status in checks:
        style = "green" if status == "OK" else ("yellow" if "WARN" in status else "red")
        table.add_row(name, value, f"[{style}]{status}[/{style}]")
    console.print(table)

    ok_count = sum(1 for _, _, s in checks if s == "OK")
    console.print(f"\n[bold]{ok_count}/{len(checks)} checks passed[/bold]")
    for report in adapter_reports:
        if not report.non_ok_checks:
            continue
        console.print(f"\nAdapter details: {report.adapter}", style="bold", markup=False)
        for check in report.non_ok_checks:
            console.print(
                f"- {adapter_health.bounded_diagnostic_text(check.name)}: "
                f"{adapter_health.bounded_diagnostic_text(check.detail)}",
                markup=False,
            )
            if check.fix:
                console.print(
                    f"  fix: {adapter_health.bounded_diagnostic_text(check.fix)}",
                    markup=False,
                )
    if repair_failed or adapter_failed:
        raise typer.Exit(1)


__all__ = ["doctor"]
