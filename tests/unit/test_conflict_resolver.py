"""Unit tests for conflict auto-resolution."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest

from agent_brain.memory.store.items_store import ItemsStore, make_item_id
from agent_brain.memory.governance.conflict_resolver import (
    ConflictReport,
    ResolutionStrategy,
    resolve_conflicts,
)
from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def test_conflict_resolver_reexports_split_types():
    from agent_brain.memory.governance import conflict_resolver
    from agent_brain.memory.governance.conflict_types import (
        ConflictReport as SplitReport,
        Resolution as SplitResolution,
        ResolutionStrategy as SplitStrategy,
    )

    assert conflict_resolver.ConflictReport is SplitReport
    assert conflict_resolver.Resolution is SplitResolution
    assert conflict_resolver.ResolutionStrategy is SplitStrategy


def test_conflict_resolver_reexports_split_strategy_selector():
    from agent_brain.memory.governance import conflict_resolver
    from agent_brain.memory.governance.conflict_strategy import select_strategy

    assert conflict_resolver._select_strategy is select_strategy


def test_conflict_resolver_reexports_split_actions():
    from agent_brain.memory.governance import conflict_resolver
    from agent_brain.memory.governance.conflict_actions import resolve_keep_newer

    assert conflict_resolver._resolve_keep_newer is resolve_keep_newer


def _make_decision(store, title, body, project="proj-x", confidence=0.7, days_ago=0, tags=None):
    now = datetime.now(timezone.utc) - timedelta(days=days_ago)
    item = MemoryItem(
        id=make_item_id(title, when=now),
        type=MemoryType.decision,
        created_at=now,
        project=project,
        tags=tags or ["api"],
        title=title,
        summary=f"Decision: {title}",
        confidence=confidence,
    )
    store.write(item, body)
    return item


@pytest.fixture
def store_with_contradictions(tmp_path):
    """Store with two contradicting decisions (use X vs use Y)."""
    items_dir = tmp_path / "items"
    items_dir.mkdir()
    store = ItemsStore(items_dir=items_dir)

    a = _make_decision(
        store, "Use Redis for caching",
        "**决策** use Redis\n**理由** fast\n**改回去的代价** medium",
        days_ago=30, confidence=0.7,
    )
    b = _make_decision(
        store, "Use Memcached for caching",
        "**决策** use Memcached\n**理由** simpler\n**改回去的代价** low",
        days_ago=5, confidence=0.7,
    )
    return store, a, b


class TestResolveConflicts:
    def test_dry_run_does_not_modify(self, store_with_contradictions):
        store, item_a, item_b = store_with_contradictions
        report = resolve_conflicts(store, dry_run=True)
        assert report.contradictions_found >= 1
        for item, _ in store.iter_all():
            assert item.superseded_by is None
            assert "contested" not in item.tags

    def test_keep_newer_strategy(self, store_with_contradictions):
        store, item_a, item_b = store_with_contradictions
        report = resolve_conflicts(
            store, dry_run=False,
            strategy_override=ResolutionStrategy.KEEP_NEWER,
        )
        assert report.resolved_count >= 1
        items = {it.id: it for it, _ in store.iter_all()}
        older = items[item_a.id]
        assert older.superseded_by == item_b.id

    def test_keep_higher_confidence(self, tmp_path):
        items_dir = tmp_path / "items"
        items_dir.mkdir()
        store = ItemsStore(items_dir=items_dir)

        low = _make_decision(store, "Use SQLite for DB",
                             "**决策** use SQLite\n**理由** simple",
                             confidence=0.4)
        high = _make_decision(store, "Use Postgres for DB",
                              "**决策** use Postgres\n**理由** scale",
                              confidence=0.9)

        report = resolve_conflicts(
            store, dry_run=False,
            strategy_override=ResolutionStrategy.KEEP_HIGHER_CONFIDENCE,
        )
        if report.contradictions_found > 0:
            items = {it.id: it for it, _ in store.iter_all()}
            assert items[low.id].superseded_by == high.id

    def test_mark_contested(self, store_with_contradictions):
        store, item_a, item_b = store_with_contradictions
        report = resolve_conflicts(
            store, dry_run=False,
            strategy_override=ResolutionStrategy.MARK_CONTESTED,
        )
        if report.contradictions_found > 0:
            items = {it.id: it for it, _ in store.iter_all()}
            assert "contested" in items[item_a.id].tags
            assert "contested" in items[item_b.id].tags
            assert items[item_a.id].confidence < 0.7
            assert items[item_b.id].confidence < 0.7

    def test_merge_resolution(self, store_with_contradictions):
        store, item_a, item_b = store_with_contradictions
        report = resolve_conflicts(
            store, dry_run=False,
            strategy_override=ResolutionStrategy.MERGE_RESOLUTION,
        )
        if report.contradictions_found > 0:
            items = {it.id: it for it, _ in store.iter_all()}
            assert items[item_a.id].superseded_by is not None
            assert items[item_b.id].superseded_by is not None
            resolution_id = items[item_a.id].superseded_by
            assert resolution_id in items
            resolution = items[resolution_id]
            assert "conflict-resolution" in resolution.tags

    def test_auto_strategy_selection_confidence_gap(self, tmp_path):
        """When confidence gap >= 0.3, should pick KEEP_HIGHER_CONFIDENCE."""
        items_dir = tmp_path / "items"
        items_dir.mkdir()
        store = ItemsStore(items_dir=items_dir)

        _make_decision(store, "Use Flask framework",
                       "**决策** use Flask\n**理由** lightweight",
                       confidence=0.4)
        _make_decision(store, "Use FastAPI framework",
                       "**决策** use FastAPI\n**理由** modern async",
                       confidence=0.9)

        report = resolve_conflicts(store, dry_run=True)
        if report.contradictions_found > 0:
            strategies = [r.strategy for r in report.resolutions]
            assert ResolutionStrategy.KEEP_HIGHER_CONFIDENCE in strategies

    def test_no_contradictions_means_empty_report(self, tmp_path):
        items_dir = tmp_path / "items"
        items_dir.mkdir()
        store = ItemsStore(items_dir=items_dir)
        _make_decision(store, "Use Python",
                       "**决策** use Python\n**理由** team preference",
                       project="proj-a")
        _make_decision(store, "Use Go for microservices",
                       "**决策** use Go\n**理由** performance",
                       project="proj-b")
        report = resolve_conflicts(store, dry_run=True)
        assert report.contradictions_found == 0
