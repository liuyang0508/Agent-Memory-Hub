"""Unit tests for the Agent SDK (MemoryClient)."""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from agent_brain.interfaces.sdk import MemoryClient, SearchResult


def test_sdk_search_firewall_defaults_are_secure():
    from agent_brain.interfaces.sdk.query import search_items

    assert inspect.signature(MemoryClient.search).parameters["context_firewall"].default is True
    assert inspect.signature(search_items).parameters["context_firewall"].default is True


def test_sdk_query_helpers_are_split_and_reexported():
    from agent_brain.interfaces.sdk import sdk
    from agent_brain.interfaces.sdk.query import (
        SearchResult as QuerySearchResult,
        build_brief_payload,
        list_recent_items,
        read_item,
        search_items,
    )

    assert SearchResult is QuerySearchResult
    assert sdk.SearchResult is QuerySearchResult
    assert callable(search_items)
    assert callable(read_item)
    assert callable(list_recent_items)
    assert callable(build_brief_payload)


def test_sdk_components_are_split_and_delegated(tmp_path):
    from agent_brain.interfaces.sdk.components import ClientComponents

    client = MemoryClient(brain_dir=tmp_path)

    assert isinstance(client._components, ClientComponents)
    assert client._components.get_store() is client._components.get_store()
    assert client._components.get_index() is client._components.get_index()
    assert client._components.get_embedder() is client._components.get_embedder()


def test_sdk_facade_does_not_keep_redundant_component_getters():
    redundant_getters = {
        "_get_store",
        "_get_index",
        "_get_embedder",
        "_get_retriever",
        "_get_feedback",
    }

    assert redundant_getters.isdisjoint(MemoryClient.__dict__)


def test_sdk_config_helper_is_split_and_reexported(tmp_path, monkeypatch):
    from agent_brain.interfaces.sdk import sdk
    from agent_brain.interfaces.sdk.config import resolve_brain_dir

    monkeypatch.setenv("BRAIN_DIR", str(tmp_path / "brain"))

    assert sdk.resolve_brain_dir is resolve_brain_dir
    assert resolve_brain_dir(None) == tmp_path / "brain"
    assert resolve_brain_dir(tmp_path / "explicit") == tmp_path / "explicit"


def test_sdk_write_indexer_logs_best_effort_failures(caplog):
    from agent_brain.interfaces.sdk.write_index import ClientWriteIndexer

    def failing_index():
        raise RuntimeError("index unavailable")

    indexer = ClientWriteIndexer(index_getter=failing_index, embedder_getter=lambda: None)
    item = SimpleNamespace(id="mem-1", title="Title", summary="Summary")

    assert indexer.index(item, "Body") is False
    assert "Failed to index SDK-written item mem-1" in caplog.text


def test_sdk_write_helper_is_split_and_reexported():
    from agent_brain.interfaces.sdk import sdk
    from agent_brain.interfaces.sdk.write import write_item

    assert sdk.write_item is write_item


def test_sdk_feedback_helpers_are_split_and_reexported():
    from agent_brain.interfaces.sdk import sdk
    from agent_brain.interfaces.sdk.feedback import (
        apply_confirm,
        apply_reaffirm,
        apply_reject,
    )

    assert sdk.apply_reaffirm is apply_reaffirm
    assert sdk.apply_reject is apply_reject
    assert sdk.apply_confirm is apply_confirm


@pytest.fixture
def client(tmp_path):
    return MemoryClient(brain_dir=tmp_path, agent="test-agent", project="test-proj")


class TestMemoryClientWrite:
    def test_write_returns_id(self, client):
        item_id = client.write(
            type="decision",
            title="Use SSE",
            summary="SSE is simpler for push",
            body="**决策** SSE\n**理由** simpler",
            tags=["api", "sse"],
        )
        assert item_id.startswith("mem-")

    def test_write_persists(self, client):
        client.write(type="fact", title="Python 3.12", summary="We use Python 3.12")
        recent = client.list_recent(n=5)
        assert len(recent) == 1
        assert recent[0]["title"] == "Python 3.12"

    def test_write_uses_defaults(self, client):
        item_id = client.write(type="episode", title="Bug found", summary="A bug")
        result = client.read(item_id)
        assert result is not None
        assert result["item"]["agent"] == "test-agent"
        assert result["item"]["project"] == "test-proj"

    def test_write_accepts_validity_scope(self, client):
        item_id = client.write(
            type="signal",
            title="Browser unavailable",
            summary="Browser unavailable in this repo",
            validity={"cwd": "/repo/current", "adapter": "codex"},
        )

        result = client.read(item_id)

        assert result is not None
        assert result["item"]["validity"]["cwd"] == "/repo/current"
        assert result["item"]["validity"]["adapter"] == "codex"


class TestMemoryClientSearch:
    def test_search_finds_written_items(self, client):
        client.write(
            type="decision",
            title="SSE over WebSocket",
            summary="Chose SSE for push",
            tags=["api"],
            refs={"urls": ["https://example.test/sse"]},
        )
        client.write(
            type="fact",
            title="Redis cache TTL",
            summary="TTL set to 300s",
            tags=["cache"],
            refs={"urls": ["https://example.test/redis"]},
        )

        results = client.search("SSE WebSocket push")
        assert results
        assert any("SSE" in result.title for result in results)

    def test_search_returns_search_result_objects(self, client):
        client.write(
            type="episode",
            title="SDK search result object",
            summary="SDK search result object contract",
        )

        results = client.search("SDK search result object")

        assert results
        assert all(isinstance(result, SearchResult) for result in results)
        assert results[0].score > 0

    def test_search_defaults_to_gateway_and_hides_forbidden_items(self, client):
        safe_id = client.write(
            type="episode",
            title="SDK gateway cohort safe",
            summary="SDK gateway cohort boundary",
        )
        forbidden = [
            client.write(
                type="episode",
                title="SDK gateway cohort private",
                summary="SDK private forbidden summary",
                body="SDK private forbidden body",
                sensitivity="private",
                allow_unsafe=True,
            ),
            client.write(
                type="episode",
                title="SDK gateway cohort secret",
                summary="SDK secret forbidden summary",
                body="SDK secret forbidden body",
                sensitivity="secret",
                allow_unsafe=True,
            ),
            client.write(
                type="episode",
                title="SDK gateway cohort review",
                summary="SDK review forbidden summary",
                body="SDK review forbidden body",
                tags=["needs-review"],
                allow_unsafe=True,
            ),
            client.write(
                type="episode",
                title="SDK gateway cohort superseded",
                summary="SDK superseded forbidden summary",
                body="SDK superseded forbidden body",
            ),
        ]
        client._components.get_store().update_frontmatter(
            forbidden[-1],
            superseded_by=safe_id,
        )

        results = client.search("SDK gateway cohort boundary", top_k=10)

        assert [result.id for result in results] == [safe_id]
        serialized = repr(results)
        for item_id in forbidden:
            assert item_id not in serialized
        for marker in ("private", "secret", "review", "superseded"):
            assert f"SDK gateway cohort {marker}" not in serialized
            assert f"SDK {marker} forbidden summary" not in serialized
            assert f"SDK {marker} forbidden body" not in serialized

    def test_explicit_raw_search_keeps_diagnostics_without_context_pack(self, client):
        item_id = client.write(
            type="episode",
            title="SDK raw injection gateway",
            summary="SDK raw boundary",
            body="sdk raw body",
            sensitivity="secret",
            allow_unsafe=True,
        )

        results = client.search(
            "SDK raw injection gateway",
            context_firewall=False,
            include_trace=True,
        )

        assert len(results) == 1
        assert results[0].id == item_id
        assert results[0].title == "SDK raw injection gateway"
        assert results[0].summary == "SDK raw boundary"
        assert results[0].snippet.strip() == "sdk raw body"
        assert results[0].retrieval_trace is not None
        assert results[0].context_pack is None
        assert results[0].firewall is None

    def test_gateway_failure_never_falls_back_to_raw(self, client, monkeypatch):
        client.write(
            type="episode",
            title="SDK gateway failure boundary",
            summary="SDK gateway failure boundary",
        )
        import agent_brain.memory.context.injection_gateway as gateway_module

        def fail_closed(*_args, **_kwargs):
            raise RuntimeError("synthetic gateway failure")

        monkeypatch.setattr(gateway_module, "build_injection_context", fail_closed)

        with pytest.raises(RuntimeError, match="synthetic gateway failure"):
            client.search("SDK gateway failure boundary")

    @pytest.mark.parametrize("context_firewall", [True, False])
    def test_invalid_verbosity_is_rejected_in_safe_and_raw_modes(
        self,
        client,
        context_firewall,
    ):
        client.write(
            type="episode",
            title="SDK invalid verbosity boundary",
            summary="SDK invalid verbosity boundary",
        )

        with pytest.raises(
            ValueError,
            match="verbosity must be one of: locator, overview, detail, auto",
        ):
            client.search(
                "SDK invalid verbosity boundary",
                verbosity="bogus",
                context_firewall=context_firewall,
            )

    def test_search_items_secure_path_overfetches_and_restores_record_access(self, tmp_path):
        from agent_brain.interfaces.sdk.query import search_items
        from agent_brain.memory.store.items_store import ItemsStore

        calls = []

        class FakeRetriever:
            record_access = True

            def search(self, _query, *, top_k, filters, explain):
                calls.append((top_k, filters, explain, self.record_access))
                return []

        retriever = FakeRetriever()

        results = search_items(
            query="SDK overfetch boundary",
            top_k=2,
            type=None,
            project=None,
            tags=None,
            default_project=None,
            retriever=retriever,
            store=ItemsStore(tmp_path / "items"),
        )

        assert results == []
        assert calls == [(6, None, False, False)]
        assert retriever.record_access is True

    def test_search_items_reports_ghost_hydrate_only_as_aggregate(
        self,
        tmp_path,
        caplog,
    ):
        from agent_brain.interfaces.sdk.query import search_items
        from agent_brain.memory.store.items_store import ItemsStore

        ghost_id = "mem-20260711-010000-sdk-ghost-private-title"
        ghost_hit = SimpleNamespace(id=ghost_id, score=0.9, trace=None)
        retriever = SimpleNamespace(
            record_access=True,
            search=lambda *_args, **_kwargs: [ghost_hit],
        )

        results = search_items(
            query="SDK ghost private query content",
            top_k=1,
            type=None,
            project=None,
            tags=None,
            default_project=None,
            retriever=retriever,
            store=ItemsStore(tmp_path / "items"),
            context_firewall=True,
        )

        assert results == []
        assert "surface=sdk-search reason=hydrate_error count=1" in caplog.text
        assert ghost_id not in caplog.text
        assert "SDK ghost private query content" not in caplog.text

    def test_search_can_return_trace_context_and_firewall_diagnostics(self, client):
        client.write(
            type="episode",
            title="SDK trace context",
            summary="sdk trace context locator",
            body="SDK trace context body",
        )

        results = client.search(
            "sdk trace context",
            top_k=1,
            verbosity="auto",
            include_trace=True,
        )

        assert len(results) == 1
        result = results[0]
        assert result.context_pack is not None
        assert result.context_pack["item_id"] == result.id
        assert result.context_pack["detail_uri"].startswith("memory://items/")
        assert result.snippet == result.context_pack["text"]
        assert result.retrieval_trace is not None
        assert result.retrieval_trace["final_rank"] == 1
        assert result.firewall is not None
        assert result.firewall["action"] in {"include", "demote"}

    def test_empty_search(self, client):
        results = client.search("nonexistent gibberish xyz123")
        assert results == []


class TestMemoryClientRead:
    def test_read_existing(self, client):
        item_id = client.write(type="fact", title="Read test", summary="Testing read")
        result = client.read(item_id)
        assert result is not None
        assert result["item"]["title"] == "Read test"
        assert "body" in result

    def test_read_nonexistent(self, client):
        assert client.read("mem-99999999-999999-nonexistent") is None


class TestMemoryClientFeedback:
    def test_reaffirm(self, client):
        item_id = client.write(type="decision", title="Test reaffirm", summary="Testing")
        client.reaffirm(item_id)
        result = client.read(item_id)
        assert result["item"]["support_count"] == 1
        assert result["item"]["gain_score"] > 0

    def test_reject(self, client):
        item_id = client.write(type="decision", title="Test reject", summary="Testing")
        client.reject(item_id)
        result = client.read(item_id)
        assert result["item"]["contradict_count"] == 1
        assert result["item"]["gain_score"] < 0

    def test_confirm(self, client):
        item_id = client.write(type="fact", title="Confirm test", summary="Testing",
                               confidence=0.5)
        client.confirm(item_id, confidence=0.95)
        result = client.read(item_id)
        assert result["item"]["confidence"] == 0.95

    def test_injection_feedback_batch(self, client):
        adopted = client.write(type="episode", title="Batch adopted", summary="Testing")
        rejected = client.write(type="episode", title="Batch rejected", summary="Testing")
        ignored = client.write(type="episode", title="Batch ignored", summary="Testing")

        report = client.injection_feedback(
            injected_ids=[adopted, rejected, ignored],
            adopted_ids=[adopted],
            rejected_ids=[rejected],
        )

        assert report["adopted"] == [adopted]
        assert report["rejected"] == [rejected]
        assert report["ignored"] == [ignored]
        assert client.read(adopted)["item"]["support_count"] == 1
        assert client.read(rejected)["item"]["contradict_count"] == 1
        assert client.read(ignored)["item"]["support_count"] == 0

    def test_apply_task_outcome_feedback(self, client):
        from agent_brain.memory.governance.recall_events import record_task_outcome

        adopted = client.write(type="episode", title="Outcome adopted", summary="Testing")
        rejected = client.write(type="episode", title="Outcome rejected", summary="Testing")
        record_task_outcome(
            client.brain_dir,
            task_id="task-sdk",
            question="sdk outcome feedback",
            outcome="success",
            injected_ids=[adopted, rejected],
            adopted_ids=[adopted],
            rejected_ids=[rejected],
        )

        first = client.apply_task_outcome_feedback()
        second = client.apply_task_outcome_feedback()

        assert first["applied_count"] == 1
        assert second["already_applied_count"] == 1
        assert client.read(adopted)["item"]["support_count"] == 1
        assert client.read(rejected)["item"]["contradict_count"] == 1


class TestMemoryClientStats:
    def test_stats_helper_exists(self, client):
        from agent_brain.interfaces.sdk.stats import build_client_stats

        stats = build_client_stats(client._components.get_store())
        assert stats["total_items"] == 0
        assert "health_grade" in stats

    def test_stats_empty(self, client):
        stats = client.stats()
        assert stats["total_items"] == 0
        assert "health_grade" in stats

    def test_stats_with_items(self, client):
        for i in range(3):
            client.write(type="fact", title=f"Item {i}", summary=f"Fact {i}")
        stats = client.stats()
        assert stats["total_items"] == 3


class TestMemoryClientBrief:
    def test_brief(self, client):
        item_id = client.write(
            type="decision",
            title="Brief gateway test",
            summary="Testing gated brief",
            refs={"urls": ["https://example.test/brief"]},
        )
        client.write(
            type="signal",
            title="Private brief signal",
            summary="Must be withheld",
            sensitivity="private",
        )

        payload = client.brief()

        assert payload["total_shown"] == 1
        assert payload["total_withheld"] == 1
        assert payload["tiers"][2]["items"] == [
            {"id": item_id, "title": "Brief gateway test"},
        ]
        assert "Private brief signal" not in repr(payload)
