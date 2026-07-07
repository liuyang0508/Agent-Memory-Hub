"""Tests for update_memory, search snippet, and brain_stats MCP tools."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.recall.retrieval import Retriever, SearchFilter
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

_DIM = 8


def _item(suffix: str, **kw) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260528-400000-{suffix}",
        type=kw.pop("type", MemoryType.fact),
        created_at=kw.pop("created_at", datetime.now(timezone.utc)),
        title=kw.pop("title", f"Item {suffix}"),
        summary=kw.pop("summary", f"Summary for {suffix} with enough chars"),
        project=kw.pop("project", "testproj"),
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


def _patch_hermes(brain_dir: Path):
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch("agent_brain.agent_integrations.hermes.provider._brain_dir", return_value=brain_dir))
    stack.enter_context(patch(
        "agent_brain.agent_integrations.hermes.provider.get_default_embedder",
        return_value=HashingEmbedder(dim=_DIM),
    ))
    return stack


class TestUpdateMemory:
    def test_update_title(self, tmp_brain_dir: Path):
        a = _item("upd-a", title="Old title")
        _seed(tmp_brain_dir, [(a, "body content")])
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        updated = store.update_frontmatter(a.id, title="New title")
        assert updated.title == "New title"

    def test_update_tags(self, tmp_brain_dir: Path):
        a = _item("upd-tags", tags=["old"])
        _seed(tmp_brain_dir, [(a, "body")])
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        updated = store.update_frontmatter(a.id, tags=["new1", "new2"])
        assert updated.tags == ["new1", "new2"]

    def test_update_type(self, tmp_brain_dir: Path):
        a = _item("upd-type", type=MemoryType.episode)
        _seed(tmp_brain_dir, [(a, "body")])
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        updated = store.update_frontmatter(a.id, type="decision")
        assert str(updated.type) == "decision"

    def test_update_nonexistent_raises(self, tmp_brain_dir: Path):
        _seed(tmp_brain_dir, [])
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        import pytest
        with pytest.raises(FileNotFoundError):
            store.update_frontmatter("mem-20260528-999999-nope", title="x")

    def test_update_multiple_fields(self, tmp_brain_dir: Path):
        a = _item("upd-multi")
        _seed(tmp_brain_dir, [(a, "body")])
        store = ItemsStore(items_dir=tmp_brain_dir / "items")
        updated = store.update_frontmatter(a.id, title="New", summary="New summary", confidence=0.95)
        assert updated.title == "New"
        assert updated.summary == "New summary"
        assert updated.confidence == 0.95


class TestSearchSnippet:
    def test_hermes_search_returns_snippet(self, tmp_brain_dir: Path):
        a = _item("snp-a", title="snippet test item")
        body = "This is the body content that should appear as a snippet in search results."
        _seed(tmp_brain_dir, [(a, body)])
        from agent_brain.agent_integrations.hermes.provider import hub_search
        with _patch_hermes(tmp_brain_dir):
            results = hub_search("snippet test", top_k=5)
        assert len(results) >= 1
        hit = next(r for r in results if r["id"] == a.id)
        assert "snippet" in hit
        assert hit["snippet"].startswith("This is the body")

    def test_snippet_truncated_at_200(self, tmp_brain_dir: Path):
        a = _item("snp-long", title="long body test")
        body = "X" * 500
        _seed(tmp_brain_dir, [(a, body)])
        from agent_brain.agent_integrations.hermes.provider import hub_search
        with _patch_hermes(tmp_brain_dir):
            results = hub_search("long body", top_k=5)
        hit = next(r for r in results if r["id"] == a.id)
        assert len(hit["snippet"]) == 200


class TestBrainStatsCore:
    def test_collect_stats_empty(self, tmp_brain_dir: Path):
        from agent_brain.observability import collect_stats
        stats = collect_stats([])
        assert stats.total_items == 0

    def test_collect_stats_counts(self, tmp_brain_dir: Path):
        from agent_brain.observability import collect_stats
        a = _item("st-a", project="p1")
        b = _item("st-b", project="p2")
        stats = collect_stats([(a, "body a"), (b, "body b")])
        assert stats.total_items == 2
        assert "p1" in stats.project_counts
        assert "p2" in stats.project_counts

    def test_collect_stats_project_filter(self, tmp_brain_dir: Path):
        from agent_brain.observability import collect_stats
        a = _item("sf-a", project="p1")
        b = _item("sf-b", project="p2")
        stats = collect_stats([(a, "a"), (b, "b")], project_filter="p1")
        assert stats.total_items == 1

    def test_health_grade_a(self, tmp_brain_dir: Path):
        from agent_brain.observability import HealthScore
        h = HealthScore(total_items=10, items_with_issues=0)
        assert h.grade == "A"
        assert h.healthy is True

    def test_health_grade_d(self, tmp_brain_dir: Path):
        from agent_brain.observability import HealthScore
        h = HealthScore(total_items=10, items_with_issues=8)
        assert h.grade == "D"
        assert h.healthy is False
