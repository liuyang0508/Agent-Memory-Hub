"""Offline doctor presenter for the CLI."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from rich.table import Table


def _brain_dir() -> Path:
    return Path(os.environ.get("BRAIN_DIR", os.path.expanduser("~/.agent-memory-hub")))


def doctor_offline(
    console: Any,
    verbose: bool = False,
    repair_malformed: bool = False,
    restore_malformed: str | None = None,
    apply_repair: bool = False,
) -> None:
    """Report which capabilities work offline right now (no network calls)."""
    from agent_brain.platform.doctor import run_doctor
    from agent_brain.product.governance_readiness import (
        build_memory_lifecycle_readiness,
    )
    import shutil as _sh

    rep = run_doctor(offline=True)
    lifecycle = build_memory_lifecycle_readiness(_brain_dir())

    rows: list[tuple[str, str, str]] = []
    rows.append((
        "core write/read/search (BM25)",
        "local md + sqlite FTS, no model",
        "available" if rep.checks["core.md_store.writable"] else "BROKEN (md not writable)",
    ))
    routed_status = rep.checks["recall.routed.status"]
    rows.append((
        "routed recall",
        "AGENT_MEMORY_HUB_ROUTED_RECALL=0 rolls back candidate generation only",
        routed_status,
    ))
    gateway_available = rep.checks["security.injection_gateway.available"]
    rows.append((
        "prompt injection gateway",
        "Gateway API import/callable probe + closed exclusion reasons",
        "available" if gateway_available else "degraded -> gateway unavailable",
    ))
    semantic_status = rep.checks["recall.semantic_provider.status"]
    semantic_basis = (
        "already warm; no hook cold load"
        if semantic_status == "fast_ready"
        else "dependency install alone does not make hooks fast-ready"
    )
    rows.append(("semantic provider", semantic_basis, semantic_status))
    rows.append((
        "lexical raw fallback",
        "current index FTS5/BM25 + routed CLI protocol",
        rep.checks["recall.lexical_raw_fallback.status"],
    ))
    rows.append(("govern / audit / anti-drift", "offline by construction", "available"))
    rows.append(("git snapshots (history)", "local git", "available" if _sh.which("git") else "unavailable (git not found)"))
    rows.append(("citation-rot URL probe", "network, opt-in (--check-urls)", "opt-in (off by default)"))

    lifecycle_metrics = lifecycle.metrics
    pending_groups = lifecycle_metrics["pending_groups"]
    oldest_age = lifecycle_metrics["pending_oldest_age_seconds"]
    oldest_text = _format_age(oldest_age)
    pending_requires_attention = bool(
        lifecycle_metrics["pending_total"]
        or lifecycle_metrics["pending_dead_count"]
        or lifecycle_metrics["pending_scan_unavailable"]
        or lifecycle_metrics["pending_truncated"]
    )
    if pending_requires_attention:
        preview_command = "memory sync-pending --format json"
    elif lifecycle_metrics["review_queue_count"]:
        preview_command = "memory govern plan --category lifecycle --format markdown"
    else:
        preview_command = "memory verify"
    if lifecycle.status == "fail":
        lifecycle_status = f"blocked -> preview: {preview_command}"
    elif lifecycle.status == "warn" or pending_requires_attention:
        lifecycle_status = f"review required -> preview: {preview_command}"
    else:
        lifecycle_status = "available"
    rows.append((
        "lifecycle / pending governance",
        (
            f"ready={pending_groups['ready']}, "
            f"review={pending_groups['review']}, "
            f"blocker={pending_groups['blocker']}, "
            f"oldest={oldest_text}"
        ),
        lifecycle_status,
    ))
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
        style = (
            "green"
            if status in {"available", "enabled", "fast_ready", "ready"}
            else (
                "yellow"
                if (
                    "degraded" in status
                    or "review required" in status
                    or "opt-in" in status
                    or status in {"rollback", "not_fast_ready"}
                )
                else "red"
            )
        )
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
    displayed_overall = rep.overall
    if lifecycle.status != "pass" and displayed_overall == "OK":
        displayed_overall = "DEGRADED"
    console.print(f"\n[bold]overall: {displayed_overall}[/bold]")
    console.print(
        "[dim]Core read/write stays offline; routed generation, semantic readiness, lexical fallback, and Gateway health degrade independently.[/dim]"
    )


def _format_age(age_seconds: object) -> str:
    if not isinstance(age_seconds, int) or age_seconds < 0:
        return "none"
    days, remainder = divmod(age_seconds, 86400)
    hours = remainder // 3600
    if days:
        return f"{days}d {hours}h"
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes % 60}m"
    return f"{minutes}m"


__all__ = ["doctor_offline"]
