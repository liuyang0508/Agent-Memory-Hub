"""Hermes import/export and garbage-collection tool implementations."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.store.write_service import WriteService


ComponentsFactory = Callable[[], tuple[Any, Any, Any]]
EmbedderFactory = Callable[[], Any]


def hub_import_impl(
    components: ComponentsFactory,
    embedder_factory: EmbedderFactory,
    data: str,
    format: str = "jsonl",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Import memory items from JSON or JSONL string."""
    if format == "json":
        records = json.loads(data)
        if isinstance(records, dict) and "items" in records:
            records = records["items"]
    else:
        records = [json.loads(line) for line in data.strip().splitlines() if line.strip()]

    store, idx, _ = components()
    embedder = embedder_factory()
    write_service = WriteService(store, idx, embedder, brain_dir=store.items_dir.parent)
    imported, skipped, blocked, errors = 0, 0, 0, []
    for rec in records:
        try:
            fm = rec.get("frontmatter", rec)
            body = rec.get("body", "")
            item = MemoryItem(**fm)
            with store.locked_catalog():
                md_path = store.items_dir / f"{item.id}.md"
                if md_path.exists():
                    if not overwrite:
                        skipped += 1
                        continue
                    store.delete(item.id)
                result = write_service.write(item=item, body=body)
            if result.status == "blocked":
                blocked += 1
                continue
            imported += 1
        except Exception as e:
            errors.append(str(e))
    return {"imported": imported, "skipped": skipped, "blocked": blocked, "errors": errors}


def hub_obsidian_export_impl(
    components: ComponentsFactory,
    vault_dir: str,
    project: str | None = None,
    type: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Export brain pool items to an Obsidian vault as markdown files."""
    from agent_brain.memory.evidence.integrations.obsidian import ObsidianSync

    store, _, _ = components()
    sync = ObsidianSync(items_store=store, vault_dir=Path(vault_dir).expanduser())
    report = sync.export_all(project=project, type=type, overwrite=overwrite)
    return {
        "exported": report.exported,
        "skipped": report.skipped,
        "errors": report.errors,
        "vault_dir": vault_dir,
    }


def hub_obsidian_import_impl(
    components: ComponentsFactory,
    embedder_factory: EmbedderFactory,
    vault_dir: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Import Obsidian markdown files back into brain pool."""
    from agent_brain.memory.evidence.integrations.obsidian import ObsidianSync

    store, idx, _ = components()
    sync = ObsidianSync(
        items_store=store,
        vault_dir=Path(vault_dir).expanduser(),
        index=idx,
        embedder=embedder_factory(),
    )
    report = sync.import_from_vault(overwrite=overwrite)
    return {
        "imported": report.exported,
        "skipped": report.skipped,
        "errors": report.errors,
        "vault_dir": vault_dir,
    }


def hub_gc_impl(
    components: ComponentsFactory,
    max_age_days: int = 7,
    tags: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Garbage-collect stale auto-captured items."""
    store, idx, _ = components()
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
            if store.delete(item.id):
                try:
                    idx.delete(item.id)
                except Exception:  # noqa: BLE001 - index eviction is best-effort
                    pass
                deleted += 1
    return {
        "deleted": deleted,
        "candidates": candidates,
        "dry_run": dry_run,
        "max_age_days": max_age_days,
    }
