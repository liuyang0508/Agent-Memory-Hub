"""Tests for automatic related-item detection on write."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

_DIM = 8


def _item(suffix: str, **kw) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260528-500000-{suffix}",
        type=kw.pop("type", MemoryType.fact),
        created_at=kw.pop("created_at", datetime.now(timezone.utc)),
        title=kw.pop("title", f"Item {suffix}"),
        summary=kw.pop("summary", f"Summary for {suffix}"),
        project=kw.pop("project", "relproj"),
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


class TestHermesRememberRelated:
    def test_remember_with_existing_similar(self, tmp_brain_dir: Path):
        a = _item("rel-a", title="Python async patterns", summary="How to use asyncio")
        _seed(tmp_brain_dir, [(a, "Python asyncio tutorial and patterns")])
        from agent_brain.agent_integrations.hermes.provider import hub_remember
        with _patch_hermes(tmp_brain_dir):
            result = hub_remember(
                content="Advanced Python asyncio patterns for concurrency",
                title="Python async advanced",
            )
        assert result["stored"] is True
        assert "id" in result

    def test_remember_no_related_when_empty(self, tmp_brain_dir: Path):
        _seed(tmp_brain_dir, [])
        from agent_brain.agent_integrations.hermes.provider import hub_remember
        with _patch_hermes(tmp_brain_dir):
            result = hub_remember(
                content="Completely unique content",
                title="Unique title xyz",
            )
        assert result["stored"] is True
        assert "related" not in result or result.get("related") == []

    def test_remember_result_structure(self, tmp_brain_dir: Path):
        a = _item("rel-struct", title="Database optimization tips")
        _seed(tmp_brain_dir, [(a, "SQL optimization and indexing strategies")])
        from agent_brain.agent_integrations.hermes.provider import hub_remember
        with _patch_hermes(tmp_brain_dir):
            result = hub_remember(
                content="Database query optimization techniques",
                title="DB optimization",
            )
        assert "id" in result
        assert "stored" in result
        assert "path" in result
        if "related" in result:
            for r in result["related"]:
                assert "id" in r
                assert "title" in r
                assert "score" in r
