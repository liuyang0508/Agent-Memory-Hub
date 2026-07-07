"""Tests for evolve engine execution (archive, promote, decay-aware)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.governance.evolve.engine import EvolveAction, EvolveEngine
from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Sensitivity

_DIM = 8


def _make_item(
    suffix: str,
    type: MemoryType = MemoryType.fact,
    project: str | None = None,
    tags: list[str] | None = None,
    created_at: datetime | None = None,
    confidence: float = 0.7,
) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260528-100000-{suffix}",
        type=type,
        created_at=created_at or datetime.now(timezone.utc),
        title=f"Item {suffix}",
        summary=f"Summary {suffix}",
        project=project,
        tags=tags or [],
        confidence=confidence,
    )


def _seed_with_index(brain_dir: Path, items: list[tuple[MemoryItem, str]]) -> HubIndex:
    store = ItemsStore(items_dir=brain_dir / "items")
    idx = HubIndex(db_path=brain_dir / "index.db", embedding_dim=_DIM)
    emb = HashingEmbedder(dim=_DIM)
    for item, body in items:
        store.write(item, body)
        idx.upsert(item, body, embedding=emb.embed(f"{item.title} {body}"))
    return idx


# ── Archive execution ──


class TestArchiveExecution:
    def test_archive_moves_file(self, tmp_brain_dir: Path):
        old = datetime.now(timezone.utc) - timedelta(days=40)
        item = _make_item("old-sig", type=MemoryType.signal, created_at=old)
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        store.write(item, "old signal body")

        engine = EvolveEngine(items_store=store, scanner=None, dry_run=False)
        report = engine.evolve()

        archive_proposals = [p for p in report.proposals if p.action == EvolveAction.ARCHIVE]
        assert len(archive_proposals) >= 1
        assert report.executed >= 1

        src = tmp_brain_dir / "items" / f"{item.id}.md"
        dst = tmp_brain_dir / "items" / "archived" / f"{item.id}.md"
        assert not src.exists()
        assert dst.exists()

    def test_archive_removes_from_index(self, tmp_brain_dir: Path):
        old = datetime.now(timezone.utc) - timedelta(days=40)
        item = _make_item("idx-sig", type=MemoryType.signal, created_at=old)
        idx = _seed_with_index(tmp_brain_dir, [(item, "signal body")])

        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        engine = EvolveEngine(items_store=store, scanner=None, dry_run=False, index=idx)
        report = engine.evolve()

        assert report.executed >= 1
        row = idx.connection.execute(
            "SELECT id FROM items_meta WHERE id = ?", (item.id,)
        ).fetchone()
        assert row is None

    def test_dry_run_does_not_archive(self, tmp_brain_dir: Path):
        old = datetime.now(timezone.utc) - timedelta(days=40)
        item = _make_item("dry-sig", type=MemoryType.signal, created_at=old)
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        store.write(item, "signal body")

        engine = EvolveEngine(items_store=store, scanner=None, dry_run=True)
        report = engine.evolve()

        assert report.executed == 0
        src = tmp_brain_dir / "items" / f"{item.id}.md"
        assert src.exists()


# ── Promote execution ──


class TestPromoteExecution:
    def test_promote_changes_type(self, tmp_brain_dir: Path):
        item = _make_item("ep-promo", type=MemoryType.episode)
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        store.write(item, "We should always follow this pattern. Never skip. This is a rule and lesson.")

        engine = EvolveEngine(items_store=store, scanner=None, dry_run=False)
        report = engine.evolve()

        promote_proposals = [p for p in report.proposals if p.action == EvolveAction.PROMOTE]
        assert len(promote_proposals) >= 1

        reloaded = list(store.iter_all())
        found = [it for it, _ in reloaded if it.id == item.id]
        assert len(found) == 1
        assert found[0].type == "decision"
        assert found[0].confidence == 0.8

    def test_dry_run_does_not_promote(self, tmp_brain_dir: Path):
        item = _make_item("ep-dry", type=MemoryType.episode)
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        store.write(item, "We should always follow this pattern. A key lesson.")

        engine = EvolveEngine(items_store=store, scanner=None, dry_run=True)
        report = engine.evolve()

        assert report.executed == 0
        reloaded = list(store.iter_all())
        found = [it for it, _ in reloaded if it.id == item.id]
        assert found[0].type == "episode"


# ── Decay-aware archive ──


class TestDecayAwareArchive:
    def test_low_decay_score_triggers_archive(self, tmp_brain_dir: Path):
        item = _make_item("decay-dead", confidence=0.05)
        idx = _seed_with_index(tmp_brain_dir, [(item, "some body")])
        # Simulate old access — set last_accessed far in the past
        long_ago = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
        idx.connection.execute(
            "UPDATE items_meta SET last_accessed = ? WHERE id = ?",
            (long_ago, item.id),
        )
        idx.connection.commit()

        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        engine = EvolveEngine(
            items_store=store, scanner=None, dry_run=False,
            index=idx, decay_archive_threshold=0.1,
        )
        report = engine.evolve()

        archive_proposals = [p for p in report.proposals if p.action == EvolveAction.ARCHIVE]
        assert len(archive_proposals) >= 1
        assert "decayed" in archive_proposals[0].title.lower() or "decay" in archive_proposals[0].rationale.lower()

    def test_healthy_item_not_archived(self, tmp_brain_dir: Path):
        item = _make_item("healthy", confidence=0.9)
        idx = _seed_with_index(tmp_brain_dir, [(item, "healthy body")])
        # Recent access
        now = datetime.now(timezone.utc).isoformat()
        idx.record_access(item.id, now)

        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        engine = EvolveEngine(
            items_store=store, scanner=None, dry_run=True,
            index=idx, decay_archive_threshold=0.1,
        )
        report = engine.evolve()

        archive_proposals = [p for p in report.proposals if p.action == EvolveAction.ARCHIVE]
        archived_ids = {pid for p in archive_proposals for pid in p.item_ids}
        assert item.id not in archived_ids

    def test_no_index_skips_decay_check(self, tmp_brain_dir: Path):
        item = _make_item("no-idx", confidence=0.01)
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        store.write(item, "body")

        engine = EvolveEngine(items_store=store, scanner=None, dry_run=True, index=None)
        report = engine.evolve()

        archive_proposals = [p for p in report.proposals if p.action == EvolveAction.ARCHIVE]
        assert all(item.id not in p.item_ids for p in archive_proposals)


# ── Report executed count ──


class TestReportExecutedCount:
    def test_executed_count_matches(self, tmp_brain_dir: Path):
        old = datetime.now(timezone.utc) - timedelta(days=40)
        items_data = [
            (_make_item("ex1", type=MemoryType.signal, created_at=old), "sig 1"),
            (_make_item("ex2", type=MemoryType.signal, created_at=old), "sig 2"),
        ]
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        for item, body in items_data:
            store.write(item, body)

        engine = EvolveEngine(items_store=store, scanner=None, dry_run=False)
        report = engine.evolve()

        archive_proposals = [p for p in report.proposals if p.action == EvolveAction.ARCHIVE]
        assert report.executed == len(archive_proposals)
