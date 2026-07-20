"""Shared import path for CLI and MCP memory import entry points."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.recall.embedding_text import embedding_text_for_item


@dataclass
class ImportResult:
    imported: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def import_records(
    records: Iterable[dict | str],
    *,
    store: ItemsStore,
    index,
    embedder,
    overwrite: bool = False,
    brain_dir: Path | None = None,
) -> ImportResult:
    """Import exported records and repair the index best-effort.

    Markdown remains the source of truth. If md writes successfully but
    embedding/indexing fails, the item is counted as imported and recorded in
    ``.index-dirty`` so a later reindex can repair the derived SQLite tables.
    """
    result = ImportResult()
    for rec in records:
        try:
            if isinstance(rec, str):
                rec = json.loads(rec)
            fm = rec.get("frontmatter", rec)
            body = rec.get("body", "")
            item = MemoryItem(**fm)
            md_path = store.items_dir / f"{item.id}.md"
            with store.locked_catalog():
                if md_path.exists():
                    if not overwrite:
                        result.skipped += 1
                        continue
                    store.delete(item.id)
                store.write(item, body)
            result.imported += 1
            try:
                index.upsert(
                    item,
                    body,
                    embedding=embedder.embed(embedding_text_for_item(item)),
                )
            except Exception as exc:  # noqa: BLE001 - import still landed in md
                _mark_dirty(item.id, brain_dir=brain_dir)
                result.errors.append(f"{item.id}: index failed: {exc}")
        except Exception as exc:  # noqa: BLE001 - keep importing later records
            result.errors.append(str(exc))
    return result


def _mark_dirty(item_id: str, *, brain_dir: Path | None = None) -> None:
    try:
        if brain_dir is None:
            from agent_brain.memory.store.pending import dirty_index_path

            path = dirty_index_path()
        else:
            path = brain_dir / ".index-dirty"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(item_id + "\n")
    except Exception:
        pass


__all__ = ["ImportResult", "import_records"]
