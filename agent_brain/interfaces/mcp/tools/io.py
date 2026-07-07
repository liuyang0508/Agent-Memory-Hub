"""MCP io tier tools. Bodies moved verbatim from mcp_server.py (design §6.2)."""
# ruff: noqa: F405
from __future__ import annotations

from agent_brain.memory.evidence.import_service import import_records
from agent_brain.interfaces.mcp.tools._shared import *  # noqa: F401,F403
from agent_brain.interfaces.mcp.tools.io_export import build_export_payload


def export_memory(
    type: str | None = None,
    project: str | None = None,
    tenant_id: str | None = None,
    format: str = "json",
) -> dict[str, Any]:
    """Export memory items as structured data.

    Filters by type, project, tenant_id. Returns items with frontmatter + body.
    Format: 'json' (list of objects) or 'jsonl' (newline-delimited JSON strings).

    WHEN TO USE
    -----------
    User asks to backup, migrate, share, or version-control the brain. For
    Obsidian-specific sync use `obsidian_export` instead (preserves wikilinks).
    """
    store, _, _ = _components()
    return build_export_payload(
        list(store.iter_all()),
        type=type,
        project=project,
        tenant_id=tenant_id,
        format=format,
    )


def import_memory(
    data: str,
    format: str = "jsonl",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Import memory items from JSON or JSONL string (reverse of export_memory).

    Each record should have 'frontmatter' (dict) and 'body' (str).
    Format: 'jsonl' (one JSON object per line) or 'json' (array of objects).

    WHEN TO USE
    -----------
    Restoring a backup, migrating from another brain instance, or bulk-loading
    curated knowledge. `overwrite=False` (default) is safe: existing ids are
    skipped so accidental re-imports never destroy newer data.
    """
    import json as _json

    if format == "json":
        records = _json.loads(data)
        if isinstance(records, dict) and "items" in records:
            records = records["items"]
    else:
        records = [_json.loads(line) for line in data.strip().splitlines() if line.strip()]

    store, idx, _ = _components()
    embedder = get_default_embedder()
    result = import_records(records, store=store, index=idx, embedder=embedder, overwrite=overwrite)
    return {"imported": result.imported, "skipped": result.skipped, "errors": result.errors}


def obsidian_export(
    vault_dir: str,
    project: str | None = None,
    type: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Export brain pool items to an Obsidian vault as markdown files.

    Each item becomes a .md file with YAML frontmatter, wikilinks to related
    items, and memory/-prefixed tags compatible with Obsidian's tag system.

    WHEN TO USE
    -----------
    User wants to browse / edit the brain inside Obsidian, build a Graph view,
    or feed it to other knowledge tools that consume Obsidian vaults. Pairs
    with `obsidian_import` for round-trip editing.
    """
    from agent_brain.memory.evidence.integrations.obsidian import ObsidianSync

    store, idx, _ = _components()
    sync = ObsidianSync(items_store=store, vault_dir=Path(vault_dir).expanduser())
    report = sync.export_all(project=project, type=type, overwrite=overwrite)
    return {
        "exported": report.exported,
        "skipped": report.skipped,
        "errors": report.errors,
        "vault_dir": str(vault_dir),
    }


def obsidian_import(
    vault_dir: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Import Obsidian markdown files back into brain pool.

    Only imports files with frontmatter containing a valid mem-* ID.

    WHEN TO USE
    -----------
    After the user edited items inside Obsidian and wants those edits to
    flow back into the brain (frontmatter changes, body refinements, new
    wikilinks). Default `overwrite=False` skips ids that already exist in
    the brain unless they were Obsidian-originated.
    """
    from agent_brain.memory.evidence.integrations.obsidian import ObsidianSync

    store, idx, _ = _components()
    sync = ObsidianSync(items_store=store, vault_dir=Path(vault_dir).expanduser(), index=idx)
    report = sync.import_from_vault(overwrite=overwrite)
    return {
        "imported": report.exported,
        "skipped": report.skipped,
        "errors": report.errors,
        "vault_dir": str(vault_dir),
    }


def gc_memory(
    max_age_days: int = 7,
    tags: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Garbage-collect stale auto-captured items (session-end signals, etc.).

    Deletes items older than max_age_days that have ANY of the specified tags.
    Default tags: session-end, auto-captured, needs-review.

    WHEN TO USE
    -----------
    Routine hygiene at session end OR weekly cron. Use `dry_run=True` first
    to preview candidates. Safe because it ONLY touches items tagged as
    transient (session-end / auto-captured / needs-review) — long-term
    knowledge is never affected.
    """
    from datetime import timedelta

    store, idx, _ = _components()
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    target_tags = set(tags) if tags else {"session-end", "auto-captured", "needs-review"}
    candidates: list[dict[str, str]] = []
    deleted = 0
    for item, _ in store.iter_all():
        if not set(item.tags).intersection(target_tags):
            continue
        if item.created_at >= cutoff:
            continue
        candidates.append({"id": item.id, "title": item.title, "type": str(item.type)})
        if not dry_run:
            md_path = store.items_dir / f"{item.id}.md"
            if md_path.exists():
                md_path.unlink()
                idx.delete(item.id)
                deleted += 1
    return {
        "deleted": deleted,
        "candidates": candidates,
        "dry_run": dry_run,
        "max_age_days": max_age_days,
    }


def register(mcp) -> None:
    """Register this tier's tools on the FastMCP instance (called by server.register_all)."""
    mcp.tool()(export_memory)
    mcp.tool()(import_memory)
    mcp.tool()(obsidian_export)
    mcp.tool()(obsidian_import)
    mcp.tool()(gc_memory)


__all__ = ['export_memory', 'import_memory', 'obsidian_export', 'obsidian_import', 'gc_memory']
