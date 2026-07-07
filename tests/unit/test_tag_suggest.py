"""Tests for tag suggestion based on similar items."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.recall.retrieval import suggest_tags
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

_DIM = 8


def test_tag_suggestion_helper_is_split_and_reexported():
    from agent_brain.memory.recall import retrieval
    from agent_brain.memory.recall.retrieval_tags import suggest_tags as split_suggest_tags

    assert retrieval.suggest_tags is split_suggest_tags


def _item(suffix: str, **kw) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260528-300000-{suffix}",
        type=kw.pop("type", MemoryType.fact),
        created_at=kw.pop("created_at", datetime(2026, 5, 28, 10, 0, 0, tzinfo=timezone.utc)),
        title=kw.pop("title", f"Test {suffix}"),
        summary=kw.pop("summary", f"Summary {suffix}"),
        tags=kw.pop("tags", ["test"]),
        **kw,
    )


def _seed(brain_dir: Path, items: list[tuple[MemoryItem, str]]) -> tuple[ItemsStore, HubIndex]:
    store = ItemsStore(items_dir=brain_dir / "items")
    embedder = HashingEmbedder(dim=_DIM)
    idx = HubIndex(db_path=brain_dir / "index.db", embedding_dim=_DIM)
    for item, body in items:
        store.write(item, body)
        idx.upsert(item, body, embedding=embedder.embed(f"{item.title} {body}"))
    return store, idx


def _patch_hermes(brain_dir: Path):
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch("agent_brain.agent_integrations.hermes.provider._brain_dir", return_value=brain_dir))
    stack.enter_context(patch(
        "agent_brain.agent_integrations.hermes.provider.get_default_embedder",
        return_value=HashingEmbedder(dim=_DIM),
    ))
    return stack


class TestSuggestTags:
    def test_returns_tags_from_similar(self, tmp_brain_dir: Path):
        items = [
            (_item("st-a", title="Python Flask API", tags=["python", "flask", "api"]), "flask api routes"),
            (_item("st-b", title="Python Django API", tags=["python", "django", "api"]), "django api views"),
            (_item("st-c", title="Rust CLI tool", tags=["rust", "cli"]), "rust command line tool"),
        ]
        _, idx = _seed(tmp_brain_dir, items)
        embedder = HashingEmbedder(dim=_DIM)
        suggestions = suggest_tags(idx, embedder, "Python FastAPI web framework", max_tags=5)
        tags = [tag for tag, _ in suggestions]
        assert len(tags) > 0
        assert "python" in tags or "api" in tags

    def test_empty_pool(self, tmp_brain_dir: Path):
        idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=_DIM)
        embedder = HashingEmbedder(dim=_DIM)
        suggestions = suggest_tags(idx, embedder, "anything")
        assert suggestions == []

    def test_max_tags_limit(self, tmp_brain_dir: Path):
        items = [
            (_item(f"mt-{i}", title=f"Item {i}", tags=[f"tag{j}" for j in range(10)]),
             f"content {i}")
            for i in range(5)
        ]
        _, idx = _seed(tmp_brain_dir, items)
        embedder = HashingEmbedder(dim=_DIM)
        suggestions = suggest_tags(idx, embedder, "item content", max_tags=3)
        assert len(suggestions) <= 3

    def test_frequency_ordering(self, tmp_brain_dir: Path):
        items = [
            (_item("fo-a", title="Database query", tags=["db", "sql", "common"]), "database sql query"),
            (_item("fo-b", title="Database index", tags=["db", "sql", "common"]), "database sql index"),
            (_item("fo-c", title="Database backup", tags=["db", "ops"]), "database backup ops"),
        ]
        _, idx = _seed(tmp_brain_dir, items)
        embedder = HashingEmbedder(dim=_DIM)
        suggestions = suggest_tags(idx, embedder, "database management", max_tags=5)
        if suggestions:
            assert suggestions[0][1] >= suggestions[-1][1]


class TestHermesTagSuggest:
    def test_returns_suggestions(self, tmp_brain_dir: Path):
        items = [
            (_item("hs-a", title="Auth JWT token", tags=["auth", "jwt"]), "jwt authentication"),
            (_item("hs-b", title="Auth OAuth flow", tags=["auth", "oauth"]), "oauth authentication"),
        ]
        _seed(tmp_brain_dir, items)
        from agent_brain.agent_integrations.hermes.provider import hub_tag_suggest
        with _patch_hermes(tmp_brain_dir):
            result = hub_tag_suggest("authentication system design")
        assert "suggestions" in result
        assert isinstance(result["suggestions"], list)

    def test_empty_pool_returns_empty(self, tmp_brain_dir: Path):
        HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=_DIM)
        from agent_brain.agent_integrations.hermes.provider import hub_tag_suggest
        with _patch_hermes(tmp_brain_dir):
            result = hub_tag_suggest("anything")
        assert result["suggestions"] == []
