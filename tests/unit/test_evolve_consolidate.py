"""Tests for EvolveEngine consolidate execution."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.governance.evolve.engine import EvolveEngine
from agent_brain.memory.governance.audit.scanner import SkillScanner
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

_DIM = 8


def _item(suffix: str, **kw) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260528-300000-{suffix}",
        type=kw.pop("type", MemoryType.fact),
        created_at=kw.pop("created_at", datetime.now(timezone.utc)),
        title=kw.pop("title", f"Item {suffix}"),
        summary=kw.pop("summary", f"Summary for {suffix}"),
        project=kw.pop("project", "consolproj"),
        tags=kw.pop("tags", ["test"]),
    )


def _seed(brain_dir: Path, items: list[tuple[MemoryItem, str]]) -> HubIndex:
    store = ItemsStore(items_dir=brain_dir / "items")
    idx = HubIndex(db_path=brain_dir / "index.db", embedding_dim=_DIM)
    emb = HashingEmbedder(dim=_DIM)
    for item, body in items:
        store.write(item, body)
        idx.upsert(item, body, embedding=emb.embed(f"{item.title} {body}"))
    return idx


class TestConsolidateExecution:
    def test_consolidate_merges_items(self, tmp_brain_dir: Path):
        a = _item("cons-a", title="First fact", tags=["alpha"])
        b = _item("cons-b", title="Second fact", tags=["beta"])
        c = _item("cons-c", title="Third fact", tags=["alpha", "gamma"])
        d = _item("cons-d", title="Fourth fact", tags=["beta"])
        items = [(a, "Body A content"), (b, "Body B content"), (c, "Body C content"), (d, "Body D content")]
        idx = _seed(tmp_brain_dir, items)
        store = ItemsStore(items_dir=tmp_brain_dir / "items")

        engine = EvolveEngine(
            items_store=store,
            scanner=SkillScanner(),
            dry_run=False,
            index=idx,
        )
        report = engine.evolve()

        consolidate_proposals = [p for p in report.proposals if p.action.value == "consolidate"]
        assert len(consolidate_proposals) >= 1
        assert report.executed >= 1

        assert not (store.items_dir / f"{a.id}.md").exists()
        assert not (store.items_dir / f"{b.id}.md").exists()

        archive_dir = store.items_dir / "archived"
        assert (archive_dir / f"{a.id}.md").exists()
        assert (archive_dir / f"{b.id}.md").exists()

        remaining = [
            it for it, _ in store.iter_all()
            if "Consolidated" in it.title
        ]
        assert len(remaining) == 1
        assert len(remaining[0].refs.mems) == 4

    def test_consolidate_dry_run_no_execution(self, tmp_brain_dir: Path):
        a = _item("dry-a")
        b = _item("dry-b")
        c = _item("dry-c")
        d = _item("dry-d")
        items = [(a, "A"), (b, "B"), (c, "C"), (d, "D")]
        idx = _seed(tmp_brain_dir, items)
        store = ItemsStore(items_dir=tmp_brain_dir / "items")

        engine = EvolveEngine(
            items_store=store,
            scanner=SkillScanner(),
            dry_run=True,
            index=idx,
        )
        report = engine.evolve()
        assert report.executed == 0
        remaining = list(store.iter_all())
        assert len(remaining) == 4

    def test_consolidated_item_tags_merged(self, tmp_brain_dir: Path):
        a = _item("tag-a", tags=["x", "y"])
        b = _item("tag-b", tags=["y", "z"])
        c = _item("tag-c", tags=["x", "z", "w"])
        d = _item("tag-d", tags=["w"])
        items = [(a, "A"), (b, "B"), (c, "C"), (d, "D")]
        idx = _seed(tmp_brain_dir, items)
        store = ItemsStore(items_dir=tmp_brain_dir / "items")

        engine = EvolveEngine(
            items_store=store,
            scanner=SkillScanner(),
            dry_run=False,
            index=idx,
        )
        engine.evolve()

        remaining = list(store.iter_all())
        consolidated = [it for it, _ in remaining if "Consolidated" in it.title]
        assert len(consolidated) == 1
        assert set(consolidated[0].tags) == {"w", "x", "y", "z"}

    def test_consolidated_body_contains_all_sources(self, tmp_brain_dir: Path):
        a = _item("body-a", title="Alpha fact")
        b = _item("body-b", title="Beta fact")
        c = _item("body-c", title="Gamma fact")
        d = _item("body-d", title="Delta fact")
        items = [(a, "Alpha body"), (b, "Beta body"), (c, "Gamma body"), (d, "Delta body")]
        idx = _seed(tmp_brain_dir, items)
        store = ItemsStore(items_dir=tmp_brain_dir / "items")

        engine = EvolveEngine(
            items_store=store,
            scanner=SkillScanner(),
            dry_run=False,
            index=idx,
        )
        engine.evolve()

        remaining = list(store.iter_all())
        for it, body in remaining:
            if "Consolidated" in it.title:
                assert "Alpha body" in body
                assert "Beta body" in body
                assert "Gamma body" in body
                assert "Delta body" in body
                break
        else:
            raise AssertionError("No consolidated item found")
