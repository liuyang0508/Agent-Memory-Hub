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
    offline: bool = typer.Option(False, "--offline", help="Report which capabilities work offline right now"),
    verbose: bool = typer.Option(False, "--verbose", help="Show bounded detail rows for degraded checks"),
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
) -> None:
    """Run diagnostic checks on the Agent Memory Hub installation."""
    import shutil
    from rich.console import Console
    from rich.table import Table

    console = Console()

    if repair_malformed and restore_malformed:
        typer.echo("--repair-malformed and --restore-malformed are mutually exclusive", err=True)
        raise typer.Exit(2)

    if apply_repair and not (repair_malformed or restore_malformed):
        typer.echo("--apply requires --repair-malformed or --restore-malformed", err=True)
        raise typer.Exit(2)

    if (repair_malformed or restore_malformed) and not offline:
        typer.echo("--repair-malformed and --restore-malformed are only available with --offline", err=True)
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

    settings_path = Path.home() / ".claude" / "settings.json"
    if settings_path.exists():
        import json
        try:
            settings = json.loads(settings_path.read_text())
            if not isinstance(settings, dict):
                raise ValueError("settings.json is not a JSON object")
        except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError) as e:
            settings = None
            checks.append(("Claude Code settings", f"malformed: {str(e)[:40]}", "ERROR"))
        if settings is not None:
            hook_events = [
                "SessionStart",
                "UserPromptSubmit",
                "Stop",
                "PreCompact",
                "PostCompact",
                "SubagentStart",
                "SubagentStop",
            ]
            hooks = settings.get("hooks", {})
            amh_hooks_count = 0
            if isinstance(hooks, dict):
                for event in hook_events:
                    entries = hooks.get(event, [])
                    if not isinstance(entries, list):
                        continue
                    for entry in entries:
                        if not isinstance(entry, dict):
                            continue
                        for hook in entry.get("hooks", []):
                            if not isinstance(hook, dict):
                                continue
                            command = str(hook.get("command", ""))
                            if (
                                "AGENT_MEMORY_HUB_ADAPTER=claude_code" in command
                                and "agent_runtime_kit/hooks/" in command
                            ):
                                amh_hooks_count += 1
            checks.append((
                "Claude Code hooks",
                f"{amh_hooks_count} AMH registered",
                "OK" if amh_hooks_count > 0 else "MISSING",
            ))
            mcp = settings.get("mcpServers", {}).get("agent-memory-hub")
            checks.append(("MCP server", "configured" if mcp else "missing", "OK" if mcp else "MISSING"))
    else:
        checks.append(("Claude Code settings", "not found", "WARN"))

    remember_cmd = Path.home() / ".claude" / "commands" / "remember.md"
    checks.append(("/remember command", str(remember_cmd), "OK" if remember_cmd.exists() else "MISSING"))

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


__all__ = ["doctor"]
