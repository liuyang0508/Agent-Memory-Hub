"""Tests for Hermes memory provider."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.contracts.memory_item import MemoryItem, MemoryType


_DIM = 8


def _seed_brain(brain_dir: Path, n: int = 3) -> None:
    """Write n items into the brain pool so Hermes tools have data to work with."""
    from agent_brain.platform.embedding import HashingEmbedder
    from agent_brain.platform.indexing.index import HubIndex

    store = ItemsStore(items_dir=brain_dir / "items")
    embedder = HashingEmbedder(dim=_DIM)
    idx = HubIndex(db_path=brain_dir / "index.db", embedding_dim=_DIM)
    for i in range(n):
        item = MemoryItem(
            id=f"mem-20260527-{i:06d}-test{i}",
            type=MemoryType.fact,
            created_at=datetime.now(timezone.utc),
            title=f"Test item {i}",
            summary=f"Summary of item {i}",
            project="test-project",
            agent="test-agent",
            tags=["test", f"tag{i}"],
        )
        body = f"Body content for test item {i} with keyword searchable"
        store.write(item, body)
        idx.upsert(item, body, embedding=embedder.embed(f"{item.title} {body}"))
    idx.close()


def _patch_brain(brain_dir: Path):
    """Patch both brain_dir and embedder so dimensions match the test index."""
    from contextlib import ExitStack
    from agent_brain.platform.embedding import HashingEmbedder

    stack = ExitStack()
    stack.enter_context(patch("agent_brain.agent_integrations.hermes.provider._brain_dir", return_value=brain_dir))
    stack.enter_context(patch(
        "agent_brain.agent_integrations.hermes.provider.get_default_embedder",
        return_value=HashingEmbedder(dim=_DIM),
    ))
    return stack


def test_hermes_runtime_components_are_split_and_delegated():
    from agent_brain.agent_integrations.hermes.components import build_components
    from agent_brain.agent_integrations.hermes import provider

    assert callable(build_components)
    assert "build_components" in provider._components.__code__.co_names


def test_hermes_profile_enrichment_logs_failures(caplog):
    from agent_brain.agent_integrations.hermes.profile import enrich_profile_with_preferences

    def failing_inferer(_store):
        raise RuntimeError("profile unavailable")

    result = {"summary": "Brain pool", "total_items": 1}

    returned = enrich_profile_with_preferences(
        result,
        store=object(),
        preference_inferer=failing_inferer,
    )

    assert returned is result
    assert result == {"summary": "Brain pool", "total_items": 1}
    assert "Failed to enrich Hermes profile with preferences" in caplog.text


def test_hermes_related_memory_suggestion_logs_failures(caplog):
    from agent_brain.agent_integrations.hermes.related import suggest_related_memories

    class FailingRetriever:
        def search(self, query: str, top_k: int):
            raise RuntimeError(f"search failed: {query} {top_k}")

    related = suggest_related_memories(
        retriever=FailingRetriever(),
        store=object(),
        item_id="mem-1",
        query="related query",
    )

    assert related == []
    assert "Failed to suggest related Hermes memories for mem-1" in caplog.text


def test_hermes_active_recall_logs_failures(caplog):
    from agent_brain.agent_integrations.hermes.context import build_active_recall_payload

    def failing_recall_factory(_retriever):
        raise RuntimeError("recall unavailable")

    assert build_active_recall_payload(
        retriever=object(),
        task_hint="ship refactor",
        project="agent-memory-hub",
        recall_factory=failing_recall_factory,
    ) == []
    assert "Failed to build Hermes active recall context" in caplog.text


def test_hermes_search_formatter_is_split():
    from agent_brain.memory.recall.retrieval_types import RetrievedItem
    from agent_brain.agent_integrations.hermes.search import format_search_hits

    item = MemoryItem(
        id="mem-20260610-000000-search",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Search result",
        summary="Search summary",
        confidence=0.8,
    )

    rows = format_search_hits(
        hits=[
            RetrievedItem(id=item.id, score=0.9, bm25_rank=1, vector_rank=None),
            RetrievedItem(id="missing", score=0.1, bm25_rank=None, vector_rank=None),
        ],
        items_by_id={item.id: item},
        bodies_by_id={item.id: "A" * 250},
    )

    assert rows[0] == {
        "id": item.id,
        "title": "Search result",
        "type": "fact",
        "summary": "Search summary",
        "confidence": 0.8,
        "snippet": "A" * 200,
        "score": 0.9,
    }
    assert rows[1]["id"] == "missing"
    assert rows[1]["title"] is None
    assert rows[1]["type"] is None
    assert rows[1]["snippet"] == ""


def test_hermes_remember_impl_is_split_and_reexported():
    from agent_brain.agent_integrations.hermes import core_tools
    from agent_brain.agent_integrations.hermes.remember import hub_remember_impl

    assert core_tools.hub_remember_impl is hub_remember_impl


class TestHubSearch:
    def test_returns_list(self, tmp_brain_dir: Path):
        _seed_brain(tmp_brain_dir)
        from agent_brain.agent_integrations.hermes.provider import hub_search
        with _patch_brain(tmp_brain_dir):
            results = hub_search("searchable")
        assert isinstance(results, list)
        assert len(results) > 0

    def test_result_fields(self, tmp_brain_dir: Path):
        _seed_brain(tmp_brain_dir)
        from agent_brain.agent_integrations.hermes.provider import hub_search
        with _patch_brain(tmp_brain_dir):
            results = hub_search("test item", top_k=1)
        r = results[0]
        assert "id" in r
        assert "title" in r
        assert "score" in r
        assert "type" in r

    def test_respects_top_k(self, tmp_brain_dir: Path):
        _seed_brain(tmp_brain_dir, n=5)
        from agent_brain.agent_integrations.hermes.provider import hub_search
        with _patch_brain(tmp_brain_dir):
            results = hub_search("test", top_k=2)
        assert len(results) <= 2

    def test_empty_pool(self, tmp_brain_dir: Path):
        from agent_brain.agent_integrations.hermes.provider import hub_search
        with _patch_brain(tmp_brain_dir):
            results = hub_search("anything")
        assert results == []


class TestHubRemember:
    def test_stores_item(self, tmp_brain_dir: Path):
        from agent_brain.agent_integrations.hermes.provider import hub_remember
        with _patch_brain(tmp_brain_dir):
            result = hub_remember(
                content="Important fact about testing",
                title="Testing fact",
                type="fact",
                tags=["test"],
                project="myproject",
                agent="test-agent",
            )
        assert result["stored"] is True
        assert "id" in result
        assert result["id"].startswith("mem-")

    def test_stored_item_is_searchable(self, tmp_brain_dir: Path):
        from agent_brain.agent_integrations.hermes.provider import hub_remember, hub_search
        with _patch_brain(tmp_brain_dir):
            hub_remember(content="unique xylophone content", title="Xylophone item")
            results = hub_search("xylophone")
        assert len(results) >= 1
        assert any("xylophone" in (r.get("title") or "").lower() for r in results)


class TestHubProfile:
    def test_empty_pool(self, tmp_brain_dir: Path):
        from agent_brain.agent_integrations.hermes.provider import hub_profile
        with _patch_brain(tmp_brain_dir):
            profile = hub_profile()
        assert profile["total_items"] == 0
        assert "Empty" in profile["summary"]

    def test_with_items(self, tmp_brain_dir: Path):
        _seed_brain(tmp_brain_dir, n=3)
        from agent_brain.agent_integrations.hermes.provider import hub_profile
        with _patch_brain(tmp_brain_dir):
            profile = hub_profile()
        assert profile["total_items"] == 3
        assert "fact" in profile["type_counts"]
        assert "test-project" in profile["project_counts"]
        assert len(profile["recent_titles"]) <= 5

    def test_profile_preferences_are_project_and_tenant_scoped(self, tmp_brain_dir: Path):
        from agent_brain.agent_integrations.hermes.provider import hub_profile
        from agent_brain.platform.embedding import HashingEmbedder
        from agent_brain.platform.indexing.index import HubIndex

        store = ItemsStore(tmp_brain_dir / "items")
        idx = HubIndex(tmp_brain_dir / "index.db", embedding_dim=_DIM)
        embedder = HashingEmbedder(dim=_DIM)
        items = [
            MemoryItem(
                id="mem-20260703-130000-alphaone",
                type=MemoryType.decision,
                created_at=datetime.now(timezone.utc),
                title="Alpha decision one",
                summary="Alpha summary one",
                project="scope-alpha",
                tenant_id="tenant-a",
                tags=["signal-alpha"],
                gain_score=1.0,
                support_count=2,
            ),
            MemoryItem(
                id="mem-20260703-130001-alphatwo",
                type=MemoryType.decision,
                created_at=datetime.now(timezone.utc),
                title="Alpha decision two",
                summary="Alpha summary two",
                project="scope-alpha",
                tenant_id="tenant-a",
                tags=["signal-alpha"],
                gain_score=1.0,
                support_count=2,
            ),
            MemoryItem(
                id="mem-20260703-130002-betaone",
                type=MemoryType.decision,
                created_at=datetime.now(timezone.utc),
                title="Beta decision one",
                summary="Beta summary one",
                project="scope-beta",
                tenant_id="tenant-b",
                tags=["signal-beta"],
                gain_score=1.0,
                support_count=2,
            ),
            MemoryItem(
                id="mem-20260703-130003-betatwo",
                type=MemoryType.decision,
                created_at=datetime.now(timezone.utc),
                title="Beta decision two",
                summary="Beta summary two",
                project="scope-beta",
                tenant_id="tenant-b",
                tags=["signal-beta"],
                gain_score=1.0,
                support_count=2,
            ),
        ]
        for item in items:
            body = f"Body for {item.id}"
            store.write(item, body)
            idx.upsert(item, body, embedder.embed(f"{item.title} {body}"))
        idx.close()

        with _patch_brain(tmp_brain_dir):
            profile = hub_profile(project="scope-alpha", tenant_id="tenant-a")

        assert profile["total_items"] == 2
        assert profile["project_counts"] == {"scope-alpha": 2}
        preferences = profile["preferences"]
        assert any(pref["tags"] == ["signal-alpha"] for pref in preferences)
        assert not any(pref["tags"] == ["signal-beta"] for pref in preferences)

    def test_profile_preferences_can_use_graph_related_scope(self, tmp_brain_dir: Path):
        from agent_brain.agent_integrations.hermes.provider import hub_profile
        from agent_brain.platform.embedding import HashingEmbedder
        from agent_brain.platform.indexing.index import HubIndex

        store = ItemsStore(tmp_brain_dir / "items")
        idx = HubIndex(tmp_brain_dir / "index.db", embedding_dim=_DIM)
        embedder = HashingEmbedder(dim=_DIM)
        source = MemoryItem(
            id="mem-20260703-131000-source",
            type=MemoryType.decision,
            created_at=datetime.now(timezone.utc),
            title="Source decision",
            summary="Source summary",
            project="scope-alpha",
            tenant_id="tenant-a",
            tags=["signal-alpha"],
            gain_score=1.0,
            support_count=2,
        )
        related_one = MemoryItem(
            id="mem-20260703-131001-relatedone",
            type=MemoryType.decision,
            created_at=datetime.now(timezone.utc),
            title="Related decision one",
            summary="Related summary one",
            project="scope-beta",
            tenant_id="tenant-a",
            tags=["signal-related"],
            gain_score=1.0,
            support_count=2,
        )
        related_two = MemoryItem(
            id="mem-20260703-131002-relatedtwo",
            type=MemoryType.decision,
            created_at=datetime.now(timezone.utc),
            title="Related decision two",
            summary="Related summary two",
            project="scope-beta",
            tenant_id="tenant-a",
            tags=["signal-related"],
            gain_score=1.0,
            support_count=2,
        )
        for item in (source, related_one, related_two):
            body = f"Body for {item.id}"
            store.write(item, body)
            idx.upsert(item, body, embedder.embed(f"{item.title} {body}"))
        idx.add_ref(source.id, related_one.id, "relates-x")
        idx.add_ref(source.id, related_two.id, "relates-y")
        idx.close()

        with _patch_brain(tmp_brain_dir):
            profile = hub_profile(
                project="scope-alpha",
                tenant_id="tenant-a",
                scope_item_ids=[source.id],
            )

        related_preferences = [
            pref for pref in profile["preferences"]
            if pref["tags"] == ["signal-related"]
        ]
        assert related_preferences
        assert related_preferences[0]["scope_match"] == "related"
        assert set(related_preferences[0]["source_item_ids"]) == {related_one.id, related_two.id}

    def test_profile_auto_resolves_project_from_cwd(self, tmp_path: Path):
        from agent_brain.agent_integrations.hermes.provider import hub_profile
        from agent_brain.platform.embedding import HashingEmbedder
        from agent_brain.platform.indexing.index import HubIndex

        brain = tmp_path / "brain"
        workspace = tmp_path / "workspace-delta"
        nested = workspace / "packages" / "service"
        (brain / "items").mkdir(parents=True)
        (workspace / ".git").mkdir(parents=True)
        nested.mkdir(parents=True)
        store = ItemsStore(brain / "items")
        idx = HubIndex(brain / "index.db", embedding_dim=_DIM)
        embedder = HashingEmbedder(dim=_DIM)
        items = [
            MemoryItem(
                id="mem-20260703-132000-cwdone",
                type=MemoryType.decision,
                created_at=datetime.now(timezone.utc),
                title="Workspace decision one",
                summary="Workspace summary one",
                project="workspace-delta",
                tags=["signal-workspace"],
                gain_score=1.0,
                support_count=2,
            ),
            MemoryItem(
                id="mem-20260703-132001-cwdtwo",
                type=MemoryType.decision,
                created_at=datetime.now(timezone.utc),
                title="Workspace decision two",
                summary="Workspace summary two",
                project="workspace-delta",
                tags=["signal-workspace"],
                gain_score=1.0,
                support_count=2,
            ),
            MemoryItem(
                id="mem-20260703-132002-other",
                type=MemoryType.decision,
                created_at=datetime.now(timezone.utc),
                title="Other decision",
                summary="Other summary",
                project="workspace-other",
                tags=["signal-other"],
                gain_score=1.0,
                support_count=2,
            ),
        ]
        for item in items:
            body = f"Body for {item.id}"
            store.write(item, body)
            idx.upsert(item, body, embedder.embed(f"{item.title} {body}"))
        idx.close()

        with _patch_brain(brain):
            profile = hub_profile(cwd=str(nested))

        assert profile["total_items"] == 2
        assert profile["scope_filter"]["project"] == "workspace-delta"
        assert profile["scope_resolution"]["status"] == "resolved"
        assert profile["project_counts"] == {"workspace-delta": 2}


class TestHubContext:
    def test_returns_recent_items(self, tmp_brain_dir: Path):
        _seed_brain(tmp_brain_dir, n=5)
        from agent_brain.agent_integrations.hermes.provider import hub_context
        with _patch_brain(tmp_brain_dir):
            ctx = hub_context(limit=3)
        assert ctx["count"] <= 3
        assert len(ctx["items"]) <= 3

    def test_project_filter(self, tmp_brain_dir: Path):
        _seed_brain(tmp_brain_dir)
        from agent_brain.agent_integrations.hermes.provider import hub_context
        with _patch_brain(tmp_brain_dir):
            ctx = hub_context(project="test-project")
        assert ctx["project_filter"] == "test-project"
        assert ctx["count"] > 0

    def test_auto_resolves_project_from_cwd(self, tmp_path: Path):
        from agent_brain.agent_integrations.hermes.provider import hub_context
        from agent_brain.platform.embedding import HashingEmbedder
        from agent_brain.platform.indexing.index import HubIndex

        brain = tmp_path / "brain"
        workspace = tmp_path / "workspace-context"
        nested = workspace / "module"
        (brain / "items").mkdir(parents=True)
        (workspace / ".git").mkdir(parents=True)
        nested.mkdir(parents=True)
        store = ItemsStore(brain / "items")
        idx = HubIndex(brain / "index.db", embedding_dim=_DIM)
        embedder = HashingEmbedder(dim=_DIM)
        items = [
            MemoryItem(
                id="mem-20260703-133000-contextone",
                type=MemoryType.fact,
                created_at=datetime.now(timezone.utc),
                title="Context item one",
                summary="Context summary one",
                project="workspace-context",
            ),
            MemoryItem(
                id="mem-20260703-133001-contexttwo",
                type=MemoryType.fact,
                created_at=datetime.now(timezone.utc),
                title="Context item two",
                summary="Context summary two",
                project="workspace-context",
            ),
            MemoryItem(
                id="mem-20260703-133002-contextother",
                type=MemoryType.fact,
                created_at=datetime.now(timezone.utc),
                title="Other context item",
                summary="Other context summary",
                project="workspace-other",
            ),
        ]
        for item in items:
            body = f"Body for {item.id}"
            store.write(item, body)
            idx.upsert(item, body, embedder.embed(f"{item.title} {body}"))
        idx.close()

        with _patch_brain(brain):
            ctx = hub_context(cwd=str(nested), limit=10)

        assert ctx["project_filter"] == "workspace-context"
        assert ctx["scope_resolution"]["status"] == "resolved"
        assert ctx["count"] == 2
        assert {item["title"] for item in ctx["items"]} == {"Context item one", "Context item two"}

    def test_project_filter_no_match(self, tmp_brain_dir: Path):
        _seed_brain(tmp_brain_dir)
        from agent_brain.agent_integrations.hermes.provider import hub_context
        with _patch_brain(tmp_brain_dir):
            ctx = hub_context(project="nonexistent")
        assert ctx["count"] == 0

    def test_item_fields(self, tmp_brain_dir: Path):
        _seed_brain(tmp_brain_dir)
        from agent_brain.agent_integrations.hermes.provider import hub_context
        with _patch_brain(tmp_brain_dir):
            ctx = hub_context()
        item = ctx["items"][0]
        assert "id" in item
        assert "type" in item
        assert "title" in item
        assert "created_at" in item
        assert "tags" in item


class TestHubItemUtilities:
    def test_read_item(self, tmp_brain_dir: Path):
        _seed_brain(tmp_brain_dir)
        from agent_brain.agent_integrations.hermes.provider import hub_read

        with _patch_brain(tmp_brain_dir):
            result = hub_read("mem-20260527-000000-test0")

        assert result["frontmatter"]["title"] == "Test item 0"
        assert "keyword searchable" in result["body"]

    def test_list_recent_items(self, tmp_brain_dir: Path):
        _seed_brain(tmp_brain_dir, n=3)
        from agent_brain.agent_integrations.hermes.provider import hub_list

        with _patch_brain(tmp_brain_dir):
            result = hub_list(n=2)

        assert len(result) == 2
        assert all("id" in row for row in result)

    def test_delete_item(self, tmp_brain_dir: Path):
        _seed_brain(tmp_brain_dir)
        from agent_brain.agent_integrations.hermes.provider import hub_delete, hub_read

        with _patch_brain(tmp_brain_dir):
            deleted = hub_delete("mem-20260527-000000-test0")
            missing = hub_read("mem-20260527-000000-test0")

        assert deleted["deleted"] is True
        assert missing["error"] == "item not found: mem-20260527-000000-test0"

    def test_update_item_fields(self, tmp_brain_dir: Path):
        _seed_brain(tmp_brain_dir)
        from agent_brain.agent_integrations.hermes.provider import hub_read, hub_update

        with _patch_brain(tmp_brain_dir):
            result = hub_update(
                "mem-20260527-000000-test0",
                title="Updated title",
                tags=["updated", "hermes"],
                confidence=0.95,
            )
            updated = hub_read("mem-20260527-000000-test0")

        assert set(result["updated_fields"]) == {"title", "tags", "confidence"}
        assert updated["frontmatter"]["title"] == "Updated title"
        assert updated["frontmatter"]["tags"] == ["updated", "hermes"]
        assert updated["frontmatter"]["confidence"] == 0.95

    def test_update_item_rejects_empty_update(self, tmp_brain_dir: Path):
        _seed_brain(tmp_brain_dir)
        from agent_brain.agent_integrations.hermes.provider import hub_update

        with _patch_brain(tmp_brain_dir):
            result = hub_update("mem-20260527-000000-test0")

        assert result["error"] == "no fields to update"

    def test_update_item_reports_missing_item(self, tmp_brain_dir: Path):
        from agent_brain.agent_integrations.hermes.provider import hub_update

        with _patch_brain(tmp_brain_dir):
            result = hub_update("mem-20260527-999999-missing", title="Missing")

        assert result["error"] == "item not found: mem-20260527-999999-missing"


class TestHubConclude:
    def test_stores_conclusion(self, tmp_brain_dir: Path):
        from agent_brain.agent_integrations.hermes.provider import hub_conclude
        with _patch_brain(tmp_brain_dir):
            result = hub_conclude(
                session_summary="Finished refactoring auth module",
                key_decisions=["Switched to JWT", "Removed session cookies"],
                agent="test-agent",
                project="myproject",
            )
        assert result["stored"] is True
        assert "id" in result

    def test_conclusion_is_searchable(self, tmp_brain_dir: Path):
        from agent_brain.agent_integrations.hermes.provider import hub_conclude, hub_search
        with _patch_brain(tmp_brain_dir):
            hub_conclude(session_summary="Unique zorblax conclusion")
            results = hub_search("zorblax")
        assert len(results) >= 1


class TestRegisterHermesTools:
    def test_hermes_tool_registry_is_split(self):
        from agent_brain.agent_integrations.hermes.provider import HERMES_TOOLS
        from agent_brain.agent_integrations.hermes.provider_registry import HERMES_TOOL_NAMES, build_hermes_tools

        tools = build_hermes_tools()

        assert tools is HERMES_TOOLS
        assert tuple(tool.__name__ for tool in tools) == HERMES_TOOL_NAMES

    def test_provider_server_registers_tool_sequence(self):
        from agent_brain.agent_integrations.hermes.provider_server import register_tools

        registered = []

        class FakeMCP:
            def tool(self):
                def decorator(fn):
                    registered.append(fn.__name__)
                    return fn
                return decorator

        def alpha():
            return None

        def beta():
            return None

        register_tools(FakeMCP(), (alpha, beta))
        assert registered == ["alpha", "beta"]

    def test_registers_all_tools(self):
        from agent_brain.agent_integrations.hermes.provider import register_hermes_tools

        registered = []

        class FakeMCP:
            def tool(self):
                def decorator(fn):
                    registered.append(fn.__name__)
                    return fn
                return decorator

        register_hermes_tools(FakeMCP())
        assert set(registered) == {"hub_search", "hub_remember", "hub_profile", "hub_context", "hub_graph", "hub_drift", "hub_evolve", "hub_batch_confirm", "hub_update", "hub_stats", "hub_link", "hub_unlink", "hub_read", "hub_delete", "hub_list", "hub_govern", "hub_conclude", "hub_tag_suggest", "hub_import", "hub_obsidian_export", "hub_obsidian_import", "hub_gc"}
