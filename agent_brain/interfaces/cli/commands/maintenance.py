"""CLI storage and maintenance commands."""

from __future__ import annotations

import json

from agent_brain.interfaces.cli._app import app
from agent_brain.interfaces.cli._shared import (
    CURRENT_SCHEMA_VERSION,
    _brain_dir,
    _store_only,
    typer,
)
from agent_brain.interfaces.cli.commands.index_maintenance import (
    inspect_index_drift,
    reindex_store,
    repair_index_drift,
)
from agent_brain.interfaces.cli.commands.gc import gc
import agent_brain.interfaces.cli as _cli  # noqa: E402  late binding for test-patched helpers


@app.command()
def reindex(
    prune: bool = typer.Option(
        False,
        "--prune",
        help="Drop index rows whose md file no longer exists (removes ghost hits).",
    ),
) -> None:
    """Rebuild the SQLite index from items dir (md is source of truth)."""
    store, idx, _ = _cli._open_components()
    embedder = _cli.get_default_embedder()
    result = reindex_store(store, idx, embedder, prune=prune)
    if prune:
        typer.echo(f"reindexed {result.indexed} items, pruned {result.pruned}")
    else:
        typer.echo(f"reindexed {result.indexed} items")


@app.command()
def verify(
    repair: bool = typer.Option(
        False,
        "--repair",
        help="Repair drift: re-upsert all md items and prune orphan index rows.",
    ),
) -> None:
    """Diff md (source of truth) against the sqlite index and report drift."""
    store, idx, _ = _cli._open_components()
    drift = inspect_index_drift(store, idx)
    typer.echo(f"md items: {len(drift.md_ids)}")
    typer.echo(f"index items: {len(drift.index_ids)}")
    typer.echo(f"missing from index: {len(drift.missing_in_index)}")
    typer.echo(f"orphan index rows: {len(drift.orphan_in_index)}")
    if not repair:
        if drift.missing_in_index or drift.orphan_in_index:
            for mid in sorted(drift.missing_in_index):
                typer.echo(f"  missing: {mid}")
            for oid in sorted(drift.orphan_in_index):
                typer.echo(f"  orphan: {oid}")
            raise typer.Exit(1)
        typer.echo("index in sync")
        return
    embedder = _cli.get_default_embedder()
    result = repair_index_drift(store, idx, embedder, drift)
    typer.echo(f"repaired {result.indexed} items, pruned {result.pruned} orphans")


@app.command("sync-pending")
def sync_pending(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview queued records without replaying them.",
    ),
    limit: int = typer.Option(20, "--limit", help="Maximum records to show in dry-run mode."),
    format: str = typer.Option("text", "--format", help="Output format: text or json."),
) -> None:
    """Replay buffered writes from the pending queue into the brain pool."""
    from agent_brain.memory.store.pending import PendingQueue

    queue = PendingQueue()
    if dry_run:
        preview = queue.preview(limit=limit)
        if format == "json":
            typer.echo(json.dumps(preview.to_dict(), ensure_ascii=False, indent=2))
            return
        if format != "text":
            typer.echo("format must be text or json", err=True)
            raise typer.Exit(2)
        typer.echo(
            f"pending={preview.total} returned={preview.returned} "
            f"truncated={str(preview.truncated).lower()}"
        )
        for record in preview.records:
            if record.malformed:
                typer.echo(f"  malformed {record.path}: {record.error}")
                continue
            typer.echo(
                f"  {record.path}: {record.type or 'unknown'} "
                f"{record.title or '(untitled)'} attempt={record.attempt}"
            )
        typer.echo("(dry run — no pending records replayed)")
        return

    stats = queue.replay()
    if format == "json":
        typer.echo(json.dumps({
            "written": stats.written,
            "failed": stats.failed,
            "dead": stats.dead,
        }, ensure_ascii=False, indent=2))
        return
    if format != "text":
        typer.echo("format must be text or json", err=True)
        raise typer.Exit(2)
    typer.echo(f"written={stats.written} failed={stats.failed} dead={stats.dead}")


@app.command("harvest")
def harvest(
    enrich: bool = typer.Option(False, "--enrich", help="LLM-upgrade raw candidates when a model is reachable"),
    since: int = typer.Option(0, "--since", help="only transcripts modified in last N days"),
) -> None:
    """Harvest CC transcripts into the brain pool (offline-first)."""
    from agent_brain.memory.evidence.harvest.harvester import Harvester

    stats = Harvester().run(enrich=enrich)
    typer.echo(
        f"written={stats.written} skipped={stats.skipped} "
        f"enriched={stats.enriched} raw_messages={stats.raw_messages}"
    )


@app.command()
def migrate(
    to_version: str = typer.Option(
        CURRENT_SCHEMA_VERSION, "--to-version",
        help="Target schema_version to migrate items to (default: current)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview per-file changes without writing"
    ),
    rollback: bool = typer.Option(
        False, "--rollback",
        help="Restore items/ to the git snapshot taken before the last migrate",
    ),
) -> None:
    """Migrate brain-pool md frontmatter to a target schema_version."""
    from agent_brain.platform.history import BrainHistory, BrainHistoryError

    brain = _brain_dir()
    store = _store_only()
    marker = brain / ".migrate-rollback"

    if rollback:
        if not marker.exists():
            typer.echo("no migration snapshot to roll back to", err=True)
            raise typer.Exit(1)
        sha = marker.read_text(encoding="utf-8").strip()
        try:
            BrainHistory(brain).restore(sha)
        except BrainHistoryError as exc:
            typer.echo(f"rollback failed: {exc}", err=True)
            raise typer.Exit(1)
        typer.echo(f"rolled back items/ to {sha[:12]}")
        return

    pending = [
        (item.id, item.schema_version)
        for item, _ in store.iter_all()
        if item.schema_version != to_version
    ]

    if not pending:
        typer.echo(f"all items already at schema_version {to_version}; nothing to migrate")
        return

    if dry_run:
        typer.echo(
            f"migrate --dry-run: {len(pending)} item(s) would move to schema_version {to_version}"
        )
        for item_id, from_v in pending:
            typer.echo(f"  {item_id}: {from_v} -> {to_version}")
        typer.echo("(dry run — no files written; rerun without --dry-run to apply)")
        return

    history = BrainHistory(brain)
    sha = history.snapshot(f"pre-migrate snapshot (schema -> {to_version})")
    if sha is None:
        log = history.log(limit=1)
        sha = log[0]["sha"] if log else None
    if sha:
        marker.write_text(sha, encoding="utf-8")

    migrated = 0
    for item_id, _from_v in pending:
        store.update_frontmatter(item_id, schema_version=to_version)
        migrated += 1
    typer.echo(f"migrated {migrated} item(s) to schema_version {to_version}")
    if sha:
        typer.echo("snapshot saved; run 'memory migrate --rollback' to undo")


__all__ = ["reindex", "verify", "gc", "sync_pending", "harvest", "migrate"]
