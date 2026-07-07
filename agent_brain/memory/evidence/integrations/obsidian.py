"""Obsidian vault sync — export brain pool items as Obsidian-compatible markdown.

Each memory item becomes a markdown file with YAML frontmatter, body content,
and Obsidian-style [[wikilinks]] to related items (from refs.mems).

Usage:
    from agent_brain.memory.evidence.integrations.obsidian import ObsidianSync
    sync = ObsidianSync(items_store=store, vault_dir=Path("~/obsidian-vault/brain"))
    report = sync.export_all()
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.recall.embedding_text import embedding_text_for_item
from agent_brain.memory.evidence.integrations.obsidian_export import render_obsidian_markdown
from agent_brain.memory.evidence.integrations.obsidian_import import parse_obsidian_memory_markdown
from agent_brain.contracts.memory_item import MemoryItem


def _slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text.lower().strip())
    return re.sub(r"[-\s]+", "-", slug)[:60]


@dataclass
class SyncReport:
    exported: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


class ObsidianSync:
    """Sync brain pool items to an Obsidian vault directory."""

    def __init__(
        self,
        items_store: ItemsStore,
        vault_dir: Path,
        index: Any = None,
        embedder: Any = None,
    ):
        self.items_store = items_store
        self.vault_dir = vault_dir
        self.index = index
        self.embedder = embedder

    def _get_embedder(self) -> Any:
        """Lazily resolve an embedder for indexing imported items.

        Callers may pass one explicitly; otherwise fall back to the shared
        default embedder. Returns None if none can be constructed, in which
        case the item is still BM25-searchable (FTS upsert needs no vector).
        """
        if self.embedder is not None:
            return self.embedder
        try:
            from agent_brain.platform.embedding import get_default_embedder

            self.embedder = get_default_embedder()
        except Exception:
            self.embedder = None
        return self.embedder

    def export_all(
        self,
        project: str | None = None,
        type: str | None = None,
        overwrite: bool = False,
    ) -> SyncReport:
        """Export all matching items to the Obsidian vault directory."""
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        report = SyncReport()

        items_by_id: dict[str, MemoryItem] = {}
        for item, body in self.items_store.iter_all():
            items_by_id[item.id] = item

        for item, body in self.items_store.iter_all():
            if project and item.project != project:
                continue
            if type and str(item.type) != type:
                continue
            try:
                self._export_item(item, body, items_by_id, overwrite)
                report.exported += 1
            except FileExistsError:
                report.skipped += 1
            except Exception as e:
                report.errors.append(f"{item.id}: {e}")

        return report

    def _export_item(
        self,
        item: MemoryItem,
        body: str,
        items_by_id: dict[str, MemoryItem],
        overwrite: bool,
    ) -> Path:
        """Export a single item as an Obsidian-friendly markdown file."""
        # Key the filename on the immutable item id so two distinct items that
        # share a title cannot collide onto one slug file (the second used to
        # raise FileExistsError and be silently counted as skipped).
        filename = f"{item.id}.md"
        out_path = self.vault_dir / filename
        if out_path.exists() and not overwrite:
            raise FileExistsError(out_path)

        content = render_obsidian_markdown(item, body, items_by_id=items_by_id)
        out_path.write_text(content, encoding="utf-8")
        return out_path

    def import_from_vault(
        self,
        overwrite: bool = False,
    ) -> SyncReport:
        """Import Obsidian markdown files back into brain pool.

        Only imports files with frontmatter containing an 'id' field
        matching the mem-XXXXXXXX-XXXXXX-slug pattern.
        """
        report = SyncReport()

        for md_path in sorted(self.vault_dir.glob("*.md")):
            try:
                text = md_path.read_text(encoding="utf-8")
                parsed = parse_obsidian_memory_markdown(text, fallback_stem=md_path.stem)
                if parsed is None:
                    report.skipped += 1
                    continue
                item = parsed.item
                body = parsed.body

                existing = self.items_store.items_dir / f"{item.id}.md"
                if existing.exists() and not overwrite:
                    report.skipped += 1
                    continue

                if existing.exists():
                    existing.unlink()

                self.items_store.write(item, body)
                # Index the imported item so it is immediately searchable;
                # ItemsStore.write alone leaves the shadow index stale.
                if self.index is not None:
                    embedding = None
                    embedder = self._get_embedder()
                    if embedder is not None:
                        try:
                            vec = embedder.embed(embedding_text_for_item(item))
                            if len(vec) == getattr(self.index, "embedding_dim", len(vec)):
                                embedding = vec
                        except Exception:
                            embedding = None
                    self.index.upsert(item, body, embedding)
                report.exported += 1

            except Exception as e:
                report.errors.append(f"{md_path.name}: {e}")

        return report
