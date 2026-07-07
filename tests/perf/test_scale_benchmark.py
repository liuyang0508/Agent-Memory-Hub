"""Performance benchmark: 1000+ items scale validation.

Tests key paths under load to ensure no algorithmic degradation:
- ItemsStore write/read/iter_all
- BM25 search (FTS5)
- MinHash pattern detection
- Evolve engine full pipeline
- Capacity check

Run with: pytest tests/perf/ -v --no-header -rN
(NOT included in default test suite — opt-in via explicit path)
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_brain.memory.store.items_store import ItemsStore, make_item_id
from agent_brain.contracts.memory_item import MemoryItem, MemoryType


ITEM_COUNT = 1000
TAGS_POOL = ["api", "db", "frontend", "devops", "auth", "perf", "refactor", "bug", "feature", "test"]
PROJECTS = ["proj-alpha", "proj-beta", "proj-gamma", "proj-delta", None]


def _generate_items(count: int) -> list[tuple[MemoryItem, str]]:
    items = []
    for i in range(count):
        item = MemoryItem(
            id=make_item_id(title=f"bench-item-{i:04d}"),
            type=list(MemoryType)[i % len(list(MemoryType))],
            created_at=datetime(2026, 1, 1 + (i % 28), i % 24, tzinfo=timezone.utc),
            title=f"Benchmark item {i}: {'SSE push' if i % 5 == 0 else 'database optimization' if i % 3 == 0 else 'API endpoint design'}",
            summary=f"This is benchmark item {i} testing scale behavior with varied content for search quality",
            project=PROJECTS[i % len(PROJECTS)],
            tags=[TAGS_POOL[i % len(TAGS_POOL)], TAGS_POOL[(i * 3) % len(TAGS_POOL)]],
            confidence=0.5 + (i % 5) * 0.1,
            support_count=i % 7,
            gain_score=(i % 10) * 0.1 - 0.3,
        )
        body = f"Detailed body for item {i}. " * 10
        items.append((item, body))
    return items


@pytest.fixture(scope="module")
def large_store(tmp_path_factory) -> tuple[ItemsStore, list[tuple[MemoryItem, str]]]:
    """Create a store with ITEM_COUNT items."""
    tmp = tmp_path_factory.mktemp("perf_bench")
    items_dir = tmp / "items"
    items_dir.mkdir()
    store = ItemsStore(items_dir=items_dir)
    items = _generate_items(ITEM_COUNT)
    for item, body in items:
        store.write(item, body)
    return store, items


class TestWritePerformance:
    def test_bulk_write_throughput(self, tmp_path):
        """1000 writes should complete in <10s (>100 writes/sec)."""
        store = ItemsStore(items_dir=tmp_path / "items")
        items = _generate_items(ITEM_COUNT)

        start = time.perf_counter()
        for item, body in items:
            store.write(item, body)
        elapsed = time.perf_counter() - start

        rate = ITEM_COUNT / elapsed
        assert elapsed < 10.0, f"Write too slow: {elapsed:.1f}s ({rate:.0f} items/s)"
        print(f"\n  Write: {ITEM_COUNT} items in {elapsed:.2f}s ({rate:.0f}/s)")


class TestReadPerformance:
    def test_iter_all_latency(self, large_store):
        """iter_all over 1000 items should be <2s."""
        store, _ = large_store
        start = time.perf_counter()
        items = list(store.iter_all())
        elapsed = time.perf_counter() - start

        assert len(items) == ITEM_COUNT
        assert elapsed < 2.0, f"iter_all too slow: {elapsed:.1f}s"
        print(f"\n  iter_all: {len(items)} items in {elapsed:.3f}s")

    def test_single_read_latency(self, large_store):
        """Single item read should be <10ms."""
        store, items = large_store
        target_id = items[500][0].id

        start = time.perf_counter()
        item, body = store.get(target_id)
        elapsed = time.perf_counter() - start

        assert elapsed < 0.01, f"Single read too slow: {elapsed*1000:.1f}ms"
        print(f"\n  Single read: {elapsed*1000:.2f}ms")


class TestSearchPerformance:
    def test_bm25_search_latency(self, large_store):
        """BM25 search over 1000 items should be <500ms."""
        store, _ = large_store
        from agent_brain.platform.indexing.index import HubIndex
        from agent_brain.platform.embedding import HashingEmbedder
        from agent_brain.memory.recall.retrieval import Retriever

        brain_dir = store.items_dir.parent
        embedder = HashingEmbedder(dim=64)
        idx = HubIndex(db_path=brain_dir / "index.db", embedding_dim=64)

        for item, body in store.iter_all():
            idx.upsert(item, body, embedding=embedder.embed(f"{item.title} {body[:200]}"))

        retriever = Retriever(index=idx, embedder=embedder)

        start = time.perf_counter()
        results = retriever.search("SSE push real-time", top_k=10)
        elapsed = time.perf_counter() - start

        assert elapsed < 0.5, f"Search too slow: {elapsed*1000:.0f}ms"
        assert len(results) > 0
        print(f"\n  BM25+vec search: {elapsed*1000:.1f}ms, {len(results)} results")


class TestPatternDetectionPerformance:
    def test_minhash_1000_items(self, large_store):
        """Pattern detection over 1000 items should be <5s."""
        _, items = large_store
        from agent_brain.memory.governance.evolve.pattern_detector import detect_patterns

        start = time.perf_counter()
        clusters = detect_patterns(items, threshold=3, only_l0=False)
        elapsed = time.perf_counter() - start

        assert elapsed < 5.0, f"Pattern detection too slow: {elapsed:.1f}s"
        print(f"\n  MinHash LSH: {elapsed:.2f}s, {len(clusters)} clusters from {len(items)} items")


class TestEvolvePerformance:
    def test_evolve_dry_run_1000_items(self, large_store):
        """Full evolve pipeline (dry-run) over 1000 items should be <10s."""
        store, _ = large_store
        from agent_brain.memory.governance.evolve.engine import EvolveEngine

        start = time.perf_counter()
        engine = EvolveEngine(items_store=store, dry_run=True)
        report = engine.evolve()
        elapsed = time.perf_counter() - start

        assert elapsed < 10.0, f"Evolve too slow: {elapsed:.1f}s"
        print(f"\n  Evolve dry-run: {elapsed:.2f}s, {len(report.proposals)} proposals")


class TestCapacityPerformance:
    def test_capacity_check_1000_items(self, large_store):
        """Capacity check over 1000 items should be <3s."""
        store, _ = large_store
        from agent_brain.memory.governance.capacity import check_capacity

        start = time.perf_counter()
        report = check_capacity(store, embedding_dim=384)
        elapsed = time.perf_counter() - start

        assert elapsed < 3.0, f"Capacity check too slow: {elapsed:.1f}s"
        print(f"\n  Capacity: {elapsed:.2f}s | hot={report.hot_count} warm={report.warm_count} cold={report.cold_count} | limit={report.capacity_limit} util={report.utilization:.0%}")
