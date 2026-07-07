"""CLI garbage collection command."""
from __future__ import annotations

from agent_brain.interfaces.cli._app import app
from agent_brain.interfaces.cli._shared import *  # noqa: F401,F403


@app.command()
def gc(
    max_age_days: int = typer.Option(7, "--max-age", help="Delete auto-captured items older than N days"),
    gc_tags: str = typer.Option(
        "session-end,auto-captured,needs-review",
        "--tags", help="Comma-separated tags; items with ANY of these are GC candidates",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be deleted without deleting"),
) -> None:
    """Garbage-collect stale auto-captured items (session-end signals, etc.)."""
    store = _store_only()
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    target_tags = {t.strip() for t in gc_tags.split(",") if t.strip()}
    deleted = 0
    for item, _ in store.iter_all():
        item_tags = set(item.tags)
        if not item_tags.intersection(target_tags):
            continue
        if item.created_at >= cutoff:
            continue
        md_path = store.items_dir / f"{item.id}.md"
        if dry_run:
            typer.echo(f"  would delete: [{item.type}] {item.title} ({item.id[:12]})")
            deleted += 1
        else:
            if md_path.exists():
                md_path.unlink()
                _evict_from_index(item.id)
                deleted += 1
    flags_dir = store.items_dir.parent / ".session-flags"
    flags_cleaned = 0
    if flags_dir.is_dir():
        cutoff_ts = cutoff.timestamp()
        for flag in flags_dir.iterdir():
            if flag.stat().st_mtime < cutoff_ts:
                if not dry_run:
                    flag.unlink()
                flags_cleaned += 1
    if dry_run:
        typer.echo(f"(dry run — would delete {deleted} items, {flags_cleaned} session flags)")
    else:
        typer.echo(f"gc: deleted {deleted} items, {flags_cleaned} session flags (older than {max_age_days} days)")


__all__ = ["gc"]
