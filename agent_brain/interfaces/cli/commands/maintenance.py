"""CLI storage and maintenance commands."""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

from agent_brain.interfaces.cli._app import app
from agent_brain.interfaces.cli._shared import (
    CURRENT_SCHEMA_VERSION,
    _brain_dir,
    _store_only,
    typer,
)
from agent_brain.interfaces.cli.commands.index_maintenance import (
    IndexRepairResult,
    inspect_index_drift,
    reindex_store,
    repair_index_drift,
    repair_index_health,
)
from agent_brain.memory.governance.index_health import IndexHealthReport
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.indexing.graph_index import GraphIndex
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.product.governance_readiness import collect_index_health_readonly
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
    with _cli._managed_components() as (store, idx, _):
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
    format: str = typer.Option(
        "text",
        "--format",
        help="Output format: text or json.",
    ),
) -> None:
    """Diff md (source of truth) against the sqlite index and report drift."""
    if format not in {"text", "json"}:
        typer.echo("format must be text or json", err=True)
        raise typer.Exit(2)
    if not repair:
        report = collect_index_health_readonly(_brain_dir())
        _emit_verify_report(report, format=format)
        if report.status != "clean":
            raise typer.Exit(1)
        return

    brain = _brain_dir()
    before = collect_index_health_readonly(brain)
    if before.status == "clean":
        result = IndexRepairResult(0, 0, 0, 0, 0)
        _emit_repair_report(before, result, before, format=format)
        return
    if before.status in {"unavailable", "corrupt"}:
        _emit_repair_report(before, None, None, format=format)
        raise typer.Exit(1)

    items_dir = brain / "items"
    store = (
        ItemsStore(items_dir)
        if items_dir.exists()
        else SimpleNamespace(items_dir=items_dir, last_scan=None, iter_all=lambda: iter(()))
    )
    needs_full_index = bool(
        before.missing_ids | before.active_dirty_ids | before.orphan_ids
    )
    needs_graph_write = bool(before.frontmatter_only_edges or before.graph_only_edges)
    connection: sqlite3.Connection | None = None
    idx: object | None = None
    if needs_full_index:
        idx = HubIndex(brain / "index.db")
    elif needs_graph_write:
        database_uri = f"{(brain / 'index.db').resolve(strict=False).as_uri()}?mode=rw"
        connection = sqlite3.connect(database_uri, uri=True, timeout=5.0)
        connection.execute("PRAGMA busy_timeout=5000")
        graph = GraphIndex(connection)
        idx = SimpleNamespace(reconcile_supersedes=graph.replace_supersedes)
    try:
        result = repair_index_health(
            store,
            idx,
            before,
            embedder_factory=_cli.get_default_embedder,
        )
    finally:
        close_index = getattr(idx, "close", None)
        if callable(close_index):
            close_index()
        if connection is not None:
            connection.close()
    after = collect_index_health_readonly(brain)
    _emit_repair_report(before, result, after, format=format)
    if after.status != "clean":
        raise typer.Exit(1)


def _emit_verify_report(report: IndexHealthReport, *, format: str) -> None:
    if format == "json":
        typer.echo(json.dumps(report.to_summary_dict(), ensure_ascii=False, indent=2))
        return
    typer.echo(f"md items: {report.md_count}")
    typer.echo(f"index items: {report.index_count}")
    typer.echo(f"missing from index: {len(report.missing_ids)}")
    typer.echo(f"orphan index rows: {len(report.orphan_ids)}")
    typer.echo(
        f"dirty marker: {report.dirty_status} "
        f"(entries={report.dirty_entry_count}, unique={report.dirty_unique_count}, "
        f"active={len(report.active_dirty_ids)}, orphan={len(report.orphan_dirty_ids)}, "
        f"retired={len(report.retired_dirty_ids)}, "
        f"duplicates={report.duplicate_dirty_entries})"
    )
    typer.echo(
        f"supersession graph: {report.graph_status} "
        f"(expected={len(report.expected_supersedes)}, "
        f"indexed={len(report.indexed_supersedes)}, "
        f"frontmatter-only: {len(report.frontmatter_only_edges)}, "
        f"graph-only: {len(report.graph_only_edges)})"
    )
    if report.status == "clean":
        typer.echo("index in sync")
        return
    for item_id in sorted(report.missing_ids):
        typer.echo(f"  missing: {item_id}")
    for item_id in sorted(report.orphan_ids):
        typer.echo(f"  orphan: {item_id}")


def _emit_repair_report(
    before: IndexHealthReport,
    repair: IndexRepairResult | None,
    after: IndexHealthReport | None,
    *,
    format: str,
) -> None:
    repair_summary = (
        {
            "upserted": repair.upserted,
            "pruned": repair.pruned,
            "supersedes_deleted": repair.supersedes_deleted,
            "supersedes_inserted": repair.supersedes_inserted,
            "marker_entries_cleared": repair.marker_entries_cleared,
        }
        if repair is not None
        else None
    )
    if format == "json":
        typer.echo(
            json.dumps(
                {
                    "schema_version": 1,
                    "before": before.to_summary_dict(),
                    "repair": repair_summary,
                    "after": after.to_summary_dict() if after is not None else None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if repair is None or after is None:
        _emit_verify_report(before, format="text")
        return
    if before.status == "clean":
        _emit_verify_report(after, format="text")
        return
    typer.echo("before repair:")
    _emit_verify_report(before, format="text")
    typer.echo(f"repaired {repair.upserted} items, pruned {repair.pruned} orphans")
    typer.echo(
        f"reconciled supersedes: deleted={repair.supersedes_deleted}, "
        f"inserted={repair.supersedes_inserted}; "
        f"cleared marker entries={repair.marker_entries_cleared}"
    )
    typer.echo("after repair:")
    _emit_verify_report(after, format="text")


def _pending_resolution_key(
    action: object,
    record_id: object,
    target: object,
) -> tuple[str, str, str, str] | None:
    if type(action) is not str or type(record_id) is not str:
        return None
    if target is None:
        return action, record_id, "none", ""
    if type(target) is not str:
        return None
    return action, record_id, "string", target


@app.command("sync-pending")
def sync_pending(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply explicitly selected safe records.",
    ),
    record_ids: list[str] = typer.Option(
        [],
        "--record",
        help="Pending record id to apply; repeatable.",
    ),
    safe_only: bool = typer.Option(
        False,
        "--safe-only",
        help="Apply all records classified ready.",
    ),
    approve_audit: list[str] = typer.Option(
        [],
        "--approve-audit",
        help="Approve one public/internal audit-blocked record; repeatable.",
    ),
    accept_duplicate: list[str] = typer.Option(
        [],
        "--accept-duplicate",
        help="Accept an exact duplicate as ID:ITEM; repeatable.",
    ),
    convert_type: list[str] = typer.Option(
        [],
        "--convert-type",
        help="Convert legacy feedback as ID:decision; repeatable.",
    ),
    gc_orphan_locks: bool = typer.Option(
        False,
        "--gc-orphan-locks",
        help="Preview orphan record locks; delete only with --apply.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Compatibility alias for preview; always disables apply.",
    ),
    limit: int = typer.Option(20, "--limit", help="Maximum records to show in preview mode."),
    format: str = typer.Option("text", "--format", help="Output format: text or json."),
    summary_only: bool = typer.Option(
        False,
        "--summary-only",
        help="Emit low-sensitivity aggregate counts without record details.",
    ),
) -> None:
    """Preview pending writes by default; apply only an explicit selection."""
    from agent_brain.memory.store.pending import (
        PendingQueue,
        PendingResolutionAction,
        PendingResolutionStats,
    )

    if format not in {"text", "json"}:
        typer.echo("format must be text or json", err=True)
        raise typer.Exit(2)
    resolution_actions = [
        PendingResolutionAction("approve_audit", record_id)
        for record_id in approve_audit
        if record_id
    ]
    if len(resolution_actions) != len(approve_audit):
        typer.echo("--approve-audit requires ID", err=True)
        raise typer.Exit(2)
    for value in accept_duplicate:
        record_id, separator, target_id = value.rpartition(":")
        if value.count(":") != 1 or not separator or not record_id or not target_id:
            typer.echo("--accept-duplicate requires ID:ITEM", err=True)
            raise typer.Exit(2)
        resolution_actions.append(
            PendingResolutionAction("accept_duplicate", record_id, target_id)
        )
    for value in convert_type:
        record_id, separator, target_type = value.rpartition(":")
        if (
            value.count(":") != 1
            or not separator
            or not record_id
            or target_type != "decision"
        ):
            typer.echo("--convert-type requires ID:decision", err=True)
            raise typer.Exit(2)
        resolution_actions.append(
            PendingResolutionAction("convert_type", record_id, target_type)
        )

    selection_groups = sum(
        (bool(record_ids), safe_only, bool(resolution_actions))
    )
    if selection_groups > 1:
        typer.echo(
            "--record, --safe-only, and resolution options are mutually exclusive",
            err=True,
        )
        raise typer.Exit(2)
    if gc_orphan_locks and (record_ids or safe_only):
        typer.echo(
            "--gc-orphan-locks requires standalone or resolution mode",
            err=True,
        )
        raise typer.Exit(2)

    queue = PendingQueue()
    effective_apply = apply and not dry_run

    if resolution_actions or gc_orphan_locks:
        if resolution_actions:
            resolution_stats = queue.resolve(
                resolution_actions,
                apply=effective_apply,
                gc_orphan_locks=gc_orphan_locks,
            )
            if gc_orphan_locks and not effective_apply:
                resolution_stats.lock_gc_report = queue.collect_orphan_locks(
                    apply=False
                )
        else:
            resolution_stats = PendingResolutionStats(dry_run=not effective_apply)
            resolution_stats.lock_gc_report = queue.collect_orphan_locks(
                apply=effective_apply
            )

        if format == "json":
            payload = (
                resolution_stats.to_summary_dict()
                if summary_only
                else resolution_stats.to_dict()
            )
            typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        elif summary_only:
            summary = resolution_stats.to_summary_dict()
            typer.echo(
                f"dry_run={str(resolution_stats.dry_run).lower()} "
                f"actions={summary['action_counts']} "
                f"statuses={summary['status_counts']}"
            )
        else:
            typer.echo(
                f"dry_run={str(resolution_stats.dry_run).lower()} "
                f"results={len(resolution_stats.results)}"
            )
            for resolution_result in resolution_stats.results:
                typer.echo(
                    f"  {resolution_result.record_id}: "
                    f"action={resolution_result.action} "
                    f"status={resolution_result.status} "
                    f"reason={resolution_result.reason}"
                )
        lock_report = resolution_stats.lock_gc_report
        if format == "text" and lock_report is not None:
            typer.echo(
                f"lock_gc total={lock_report.total} orphan={lock_report.orphan} "
                f"deleted={lock_report.deleted} unsafe={lock_report.unsafe} "
                f"truncated={str(lock_report.truncated).lower()}"
            )
        unsuccessful = resolution_stats.governance_reason is not None
        if resolution_actions:
            expected_keys: set[tuple[str, str, str, str]] = set()
            coverage_valid = True
            for resolution_action in resolution_actions:
                expected_key = _pending_resolution_key(
                    resolution_action.action,
                    resolution_action.record_id,
                    resolution_action.target,
                )
                if expected_key is None:
                    coverage_valid = False
                    continue
                expected_keys.add(expected_key)
            result_keys: set[tuple[str, str, str, str]] = set()
            for resolution_result in resolution_stats.results:
                result_key = _pending_resolution_key(
                    resolution_result.action,
                    resolution_result.record_id,
                    resolution_result.target,
                )
                if result_key is None or result_key in result_keys:
                    coverage_valid = False
                    continue
                result_keys.add(result_key)
            coverage_valid = (
                coverage_valid
                and result_keys == expected_keys
                and resolution_stats.dry_run is (not effective_apply)
            )
            unsuccessful = unsuccessful or not coverage_valid
            expected_status = "applied" if effective_apply else "ready"
            unsuccessful = unsuccessful or any(
                resolution_result.status != expected_status
                for resolution_result in resolution_stats.results
            )
            if effective_apply:
                unsuccessful = unsuccessful or (
                    resolution_stats.receipt is None
                    or resolution_stats.receipt.state != "completed"
                )
        if lock_report is not None:
            unsuccessful = unsuccessful or (
                lock_report.unsafe > 0
                or lock_report.truncated
                or lock_report.reason is not None
            )
        if unsuccessful:
            raise typer.Exit(1)
        return

    if not effective_apply:
        preview = queue.preview(limit=limit)
        if summary_only:
            summary = preview.to_summary_dict()
            if format == "json":
                typer.echo(json.dumps(summary, ensure_ascii=False, indent=2))
            else:
                groups = summary["groups"]
                assert isinstance(groups, dict)
                typer.echo(
                    f"pending={summary['total']} returned={summary['returned']} "
                    f"truncated={str(summary['truncated']).lower()} "
                    f"ready={groups['ready']} review={groups['review']} "
                    f"blocker={groups['blocker']}"
                )
                typer.echo("(summary-only preview — no pending records applied)")
        elif format == "json":
            payload = preview.to_dict()
            typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
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
            typer.echo("(preview — no pending records applied)")
        return

    if not record_ids and not safe_only:
        typer.echo(
            "--apply requires --record or --safe-only, a resolution option, "
            "or --gc-orphan-locks",
            err=True,
        )
        raise typer.Exit(2)

    stats = queue.apply(record_ids=record_ids or None, safe_only=safe_only)
    if record_ids:
        unsuccessful = any(
            apply_result.status not in {"written", "already_written"}
            for apply_result in stats.results
        )
    else:
        unsuccessful = stats.failed > 0 or any(
            apply_result.classification in {"audit_blocked", "conflict", "malformed"}
            for apply_result in stats.results
        )
    unsuccessful = unsuccessful or stats.governance_reason is not None
    if stats.lock_gc_report is not None:
        unsuccessful = unsuccessful or (
            stats.lock_gc_report.unsafe > 0
            or stats.lock_gc_report.truncated
            or stats.lock_gc_report.reason is not None
        )
    if format == "json":
        payload = stats.to_summary_dict() if summary_only else stats.to_dict()
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        typer.echo(
            f"written={stats.written} already_written={stats.already_written} "
            f"review_required={stats.review_required} skipped={stats.skipped} "
            f"failed={stats.failed} dead={stats.dead}"
        )
        if not summary_only:
            for apply_result in stats.results:
                typer.echo(
                    f"  {apply_result.record_id}: status={apply_result.status} "
                    f"classification={apply_result.classification or 'unknown'} "
                    f"reason={apply_result.reason}"
                )
    if unsuccessful:
        raise typer.Exit(1)


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
