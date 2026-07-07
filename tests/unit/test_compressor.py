"""Unit tests for semantic compression."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging

import pytest

from agent_brain.memory.store.items_store import ItemsStore, make_item_id
from agent_brain.memory.governance.compressor import (
    CompressionCandidate,
    compress,
    find_compression_candidates,
)
from agent_brain.contracts.memory_item import AbstractionLayer, MemoryItem, MemoryType


def test_compressor_reexports_split_types():
    from agent_brain.memory.governance import compressor
    from agent_brain.memory.governance.compressor_types import (
        CompressionCandidate as SplitCandidate,
        CompressionReport as SplitReport,
    )

    assert compressor.CompressionCandidate is SplitCandidate
    assert compressor.CompressionReport is SplitReport


def test_compressor_reexports_split_candidate_finder():
    from agent_brain.memory.governance import compressor
    from agent_brain.memory.governance.compressor_candidates import (
        find_compression_candidates as split_finder,
    )

    assert compressor.find_compression_candidates is split_finder


def test_compressor_reexports_split_writeback_helpers():
    from agent_brain.memory.governance import compressor
    from agent_brain.memory.governance.compressor_writeback import (
        build_compressed_item,
        mark_sources_superseded,
    )

    assert compressor.build_compressed_item is build_compressed_item
    assert compressor.mark_sources_superseded is mark_sources_superseded


def _populate_store(store, count=5, project="proj-x", tag="api"):
    items = []
    for i in range(count):
        now = datetime(2026, 1, 1 + i, tzinfo=timezone.utc)
        item = MemoryItem(
            id=make_item_id(f"fact-{tag}-{i}", when=now),
            type=MemoryType.fact,
            created_at=now,
            project=project,
            tags=[tag, "common"],
            title=f"Fact about {tag} #{i}",
            summary=f"This is fact {i} about {tag} in {project}",
        )
        body = f"Detailed information about {tag} fact {i}. " * 5
        store.write(item, body)
        items.append(item)
    return items


@pytest.fixture
def rich_store(tmp_path):
    items_dir = tmp_path / "items"
    items_dir.mkdir()
    store = ItemsStore(items_dir=items_dir)
    _populate_store(store, count=5, project="proj-x", tag="api")
    _populate_store(store, count=4, project="proj-x", tag="db")
    _populate_store(store, count=2, project="proj-y", tag="auth")
    return store


class TestFindCandidates:
    def test_finds_groups_above_threshold(self, rich_store):
        candidates = find_compression_candidates(rich_store, min_group_size=3)
        assert len(candidates) >= 1
        for c in candidates:
            assert len(c.items) >= 3

    def test_min_group_size_respected(self, rich_store):
        candidates = find_compression_candidates(rich_store, min_group_size=5)
        for c in candidates:
            assert len(c.items) >= 5

    def test_project_filter(self, rich_store):
        candidates = find_compression_candidates(rich_store, project="proj-y", min_group_size=2)
        for c in candidates:
            for item, _ in c.items:
                assert item.project == "proj-y"

    def test_excludes_l2_items(self, tmp_path):
        items_dir = tmp_path / "items"
        items_dir.mkdir()
        store = ItemsStore(items_dir=items_dir)
        for i in range(5):
            now = datetime(2026, 1, 1 + i, tzinfo=timezone.utc)
            item = MemoryItem(
                id=make_item_id(f"already-compressed-{i}", when=now),
                type=MemoryType.fact,
                created_at=now,
                project="proj-x",
                tags=["test"],
                title=f"Already compressed {i}",
                summary=f"This was already compressed",
                abstraction=AbstractionLayer.L2,
            )
            store.write(item, "body")
        candidates = find_compression_candidates(store, min_group_size=3)
        assert len(candidates) == 0


class TestCompress:
    def test_dry_run_no_writes(self, rich_store):
        report = compress(rich_store, dry_run=True, use_llm=False)
        assert len(report.candidates) >= 1
        assert len(report.compressed) == 0
        assert report.chars_before > 0

    def test_mechanical_compression(self, rich_store):
        report = compress(rich_store, dry_run=False, use_llm=False)
        assert len(report.compressed) >= 1
        for item in report.compressed:
            assert item.abstraction == AbstractionLayer.L2
            assert "compressed" in item.tags
            assert len(item.refs.mems) >= 3

    def test_source_items_superseded(self, rich_store):
        report = compress(rich_store, dry_run=False, use_llm=False)
        if report.compressed:
            compressed_id = report.compressed[0].id
            source_ids = report.compressed[0].refs.mems
            items = {it.id: it for it, _ in rich_store.iter_all()}
            for src_id in source_ids:
                if src_id in items:
                    assert items[src_id].superseded_by == compressed_id

    def test_reduction_ratio(self, rich_store):
        report = compress(rich_store, dry_run=False, use_llm=False)
        if report.chars_before > 0:
            assert report.reduction_ratio > 0

    def test_no_model_falls_back_to_mechanical(self, rich_store, monkeypatch):
        monkeypatch.setenv("MEMORY_HUB_NO_MODEL", "1")
        report = compress(rich_store, dry_run=False, use_llm=True)
        assert len(report.compressed) >= 1

    def test_supersede_update_failures_are_logged(self, rich_store, monkeypatch, caplog):
        original_update = rich_store.update_frontmatter
        calls = 0

        def flaky_update(item_id, **updates):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("frontmatter locked")
            return original_update(item_id, **updates)

        monkeypatch.setattr(rich_store, "update_frontmatter", flaky_update)

        with caplog.at_level(logging.WARNING, logger="agent_brain.memory.governance.compressor_writeback"):
            report = compress(rich_store, dry_run=False, use_llm=False)

        assert report.compressed
        assert "failed to mark source item superseded" in caplog.text

    def test_empty_store(self, tmp_path):
        items_dir = tmp_path / "items"
        items_dir.mkdir()
        store = ItemsStore(items_dir=items_dir)
        report = compress(store, dry_run=False, use_llm=False)
        assert len(report.candidates) == 0
        assert len(report.compressed) == 0
