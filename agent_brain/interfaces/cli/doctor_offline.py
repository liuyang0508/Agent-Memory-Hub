"""Offline doctor presenter for the CLI."""
from __future__ import annotations

import os
from pathlib import Path

from rich.table import Table


def _brain_dir() -> Path:
    return Path(os.environ.get("BRAIN_DIR", os.path.expanduser("~/.agent-memory-hub")))


def doctor_offline(
    console,
    verbose: bool = False,
    repair_malformed: bool = False,
    restore_malformed: str | None = None,
    apply_repair: bool = False,
) -> None:
    """Report which capabilities work offline right now (no network calls)."""
    from agent_brain.platform.doctor import run_doctor
    import shutil as _sh

    rep = run_doctor(offline=True)

    rows: list[tuple[str, str, str]] = []
    rows.append((
        "core write/read/search (BM25)",
        "local md + sqlite FTS, no model",
        "available" if rep.checks["core.md_store.writable"] else "BROKEN (md not writable)",
    ))
    if rep.checks["core.embedder.tier"] == "semantic":
        rows.append(("vector / semantic search", "model loads on first use; BM25 fallback if it can't", "available"))
    else:
        rows.append(("vector / semantic search", "embedder degraded to hashing", "degraded -> BM25-only"))
    gateway_available = rep.checks["security.injection_gateway.available"]
    rows.append((
        "prompt injection gateway",
        "Gateway APIs importable; surface contract tests prove mandatory enforcement",
        "available" if gateway_available else "degraded -> gateway unavailable",
    ))
    rows.append(("govern / audit / anti-drift", "offline by construction", "available"))
    rows.append(("git snapshots (history)", "local git", "available" if _sh.which("git") else "unavailable (git not found)"))
    rows.append(("citation-rot URL probe", "network, opt-in (--check-urls)", "opt-in (off by default)"))

    pending_depth = rep.checks["pending.depth"]
    pending_dead = rep.checks["pending.dead"]
    if pending_depth or pending_dead:
        rows.append((
            "pending writes (durable buffer)",
            f"{pending_depth} buffered, {pending_dead} dead",
            "degraded -> run: memory sync-pending",
        ))
    else:
        rows.append(("pending writes (durable buffer)", "none buffered", "available"))
    skipped_items = rep.checks.get("core.items.skipped", 0)
    if skipped_items:
        rows.append((
            "malformed memory items",
            f"{skipped_items} skipped",
            "degraded -> inspect: memory govern run",
        ))
    else:
        rows.append(("malformed memory items", "none skipped", "available"))

    table = Table(title="Offline capability check")
    table.add_column("Capability", style="bold")
    table.add_column("Basis")
    table.add_column("Status")
    for name, basis, status in rows:
        style = "green" if status == "available" else ("yellow" if ("degraded" in status or "opt-in" in status) else "red")
        table.add_row(name, basis, f"[{style}]{status}[/{style}]")
    console.print("[bold]Agent Memory Hub - offline self-check[/bold]\n")
    console.print(table)
    if verbose and skipped_items:
        detail_table = Table(title="Malformed item details")
        detail_table.add_column("File", style="bold")
        detail_table.add_column("Path", style="bold")
        detail_table.add_column("Reason")
        for rec in rep.details.get("core.items.skipped", []):
            path = str(rec.get("path", ""))
            detail_table.add_row(Path(path).name, path, str(rec.get("reason", "")))
        console.print()
        console.print(detail_table)
    if repair_malformed:
        from agent_brain.memory.store.malformed_repair import quarantine_malformed_items

        repair = quarantine_malformed_items(_brain_dir() / "items", apply=apply_repair)
        plan_table = Table(title="Malformed item quarantine plan")
        plan_table.add_column("Mode", style="bold")
        plan_table.add_column("File", style="bold")
        plan_table.add_column("Destination")
        plan_table.add_column("Reason")
        mode = "apply" if apply_repair else "dry-run"
        for action in repair.actions:
            plan_table.add_row(
                mode,
                action.source.name,
                str(action.destination),
                action.reason,
            )
        console.print()
        console.print(plan_table)
        noun = "item" if repair.moved == 1 else "items"
        if apply_repair:
            console.print(f"[bold]moved {repair.moved} malformed {noun}[/bold]")
        else:
            console.print(
                f"[bold]dry-run: {repair.found} malformed "
                f"{'item' if repair.found == 1 else 'items'} would be moved[/bold]"
            )
    if restore_malformed:
        from agent_brain.memory.store.malformed_repair import restore_malformed_item

        restore = restore_malformed_item(
            _brain_dir() / "items",
            restore_malformed,
            apply=apply_repair,
        )
        restore_table = Table(title="Malformed item restore plan")
        restore_table.add_column("Mode", style="bold")
        restore_table.add_column("File", style="bold")
        restore_table.add_column("Destination")
        restore_table.add_column("Status")
        mode = "apply" if apply_repair else "dry-run"
        for action in restore.actions:
            destination = str(action.destination) if action.destination is not None else ""
            status = "valid" if action.valid else f"invalid: {action.reason}"
            restore_table.add_row(mode, action.source.name, destination, status)
        console.print()
        console.print(restore_table)
        noun = "item" if restore.restored == 1 else "items"
        if apply_repair:
            console.print(f"[bold]restored {restore.restored} malformed {noun}[/bold]")
        else:
            console.print(
                f"[bold]dry-run: {restore.found} archived malformed "
                f"{'item' if restore.found == 1 else 'items'} checked[/bold]"
            )
    console.print(f"\n[bold]overall: {rep.overall}[/bold]")
    console.print(
        "[dim]Core read/write stays offline; semantic recall and prompt-injection API availability degrade independently.[/dim]"
    )


__all__ = ["doctor_offline"]
