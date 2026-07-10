from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Sensitivity
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex


@pytest.fixture(autouse=True)
def close_mcp_components():
    from agent_brain.interfaces.mcp.tools._shared import _components_cache

    for _store, index, _retriever in _components_cache.values():
        index.close()
    _components_cache.clear()
    yield
    for _store, index, _retriever in _components_cache.values():
        index.close()
    _components_cache.clear()


def memory(suffix, *, sensitivity=Sensitivity.internal, tags=None, superseded_by=None):
    return MemoryItem(
        id=f"mem-20260711-010000-{suffix}",
        type=MemoryType.fact,
        created_at=datetime(2026, 7, 11, 1, 0, tzinfo=timezone.utc),
        title=f"Injection gateway boundary {suffix}",
        summary=f"Safe boundary query {suffix}",
        tags=tags or [],
        sensitivity=sensitivity,
        superseded_by=superseded_by,
        refs={"urls": [f"https://example.test/{suffix}"]},
        confidence=0.9,
    )


def body_for(value):
    return f"Injection gateway boundary body {value.title}"


def seed(brain, items):
    store = ItemsStore(brain / "items")
    embedder = HashingEmbedder()
    index = HubIndex(brain / "index.db", embedding_dim=embedder.dim)
    for value in items:
        body = body_for(value)
        store.write(value, body)
        index.upsert(value, body, embedding=embedder.embed(body))
    index.close()


def test_mcp_search_never_serializes_rejected_memory_content(tmp_brain):
    safe = memory("safe")
    private = memory("private", sensitivity=Sensitivity.private)
    secret = memory("secret", sensitivity=Sensitivity.secret)
    review = memory("review", tags=["needs-review"])
    superseded = memory("superseded", superseded_by=safe.id)
    seed(tmp_brain, [safe, private, secret, review, superseded])

    import agent_brain.interfaces.mcp.server as mcp

    result = mcp.search_memory("injection gateway boundary", top_k=10, verbosity="detail")

    assert [row["id"] for row in result] == [safe.id]
    serialized = repr(result)
    for forbidden in (private, secret, review, superseded):
        assert forbidden.id not in serialized
        assert forbidden.title not in serialized
        assert forbidden.summary not in serialized
        assert body_for(forbidden) not in serialized
    assert result[0]["context_pack"]["item_id"] == safe.id
    assert result[0]["title"] == safe.title
    assert result[0]["summary"] == safe.summary
    assert result[0]["body"] == f"{body_for(safe)}\n"
    from agent_brain.interfaces.mcp.tools._shared import _components_cache

    _store, index, _retriever = next(iter(_components_cache.values()))
    access = index.get_decay_data([
        safe.id,
        private.id,
        secret.id,
        review.id,
        superseded.id,
    ])
    assert access[safe.id][4] == 1
    assert all(
        access[value.id][4] == 0
        for value in (private, secret, review, superseded)
    )


@pytest.mark.parametrize("query", ["memory", ""])
def test_mcp_search_returns_empty_for_noninjectable_query(tmp_brain, query):
    value = memory("weak-memory")
    seed(tmp_brain, [value])

    import agent_brain.interfaces.mcp.server as mcp

    try:
        result = mcp.search_memory(query, top_k=10, verbosity="auto")
    except Exception as exc:
        pytest.fail(f"noninjectable query escaped fail-closed boundary: {exc!r}")
    assert result == []


def test_mcp_search_drops_index_hit_that_cannot_be_hydrated(tmp_brain, caplog):
    ghost = memory("hydrate-error")
    embedder = HashingEmbedder()
    index = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    body = "Injection gateway boundary hydrate error"
    index.upsert(ghost, body, embedding=embedder.embed(body))
    index.close()

    import agent_brain.interfaces.mcp.server as mcp

    result = mcp.search_memory("injection gateway boundary hydrate error", top_k=10)

    assert result == []
    assert ghost.id not in repr(result)
    assert "surface=mcp-search reason=hydrate_error count=1" in caplog.text
    from agent_brain.interfaces.mcp.tools._shared import _components_cache

    _store, cached_index, _retriever = next(iter(_components_cache.values()))
    assert cached_index.get_decay_data([ghost.id])[ghost.id][4] == 0


def test_mcp_search_never_falls_back_to_raw_when_gateway_fails(
    tmp_brain,
    monkeypatch,
):
    value = memory("gateway-failure")
    seed(tmp_brain, [value])

    import agent_brain.interfaces.mcp.server as mcp
    from agent_brain.interfaces.mcp.tools import search_tools

    def fail_closed(*_args, **_kwargs):
        raise RuntimeError("synthetic gateway failure")

    monkeypatch.setattr(
        search_tools,
        "build_injection_context",
        fail_closed,
        raising=False,
    )
    with pytest.raises(RuntimeError, match="synthetic gateway failure"):
        mcp.search_memory("injection gateway boundary", top_k=10)


def test_mcp_search_records_access_only_after_gateway_includes_hit(tmp_brain):
    value = memory("no-access")
    seed(tmp_brain, [value])

    import agent_brain.interfaces.mcp.server as mcp
    from agent_brain.interfaces.mcp.tools._shared import _components_cache

    result = mcp.search_memory("injection gateway boundary no access", top_k=10)

    assert [row["id"] for row in result] == [value.id]
    _store, index, _retriever = next(iter(_components_cache.values()))
    assert index.get_decay_data([value.id])[value.id][4] == 1


def test_mcp_search_uses_per_call_record_access_override(
    tmp_brain,
    monkeypatch,
):
    value = memory("per-call-access")
    seed(tmp_brain, [value])

    from agent_brain.memory.recall.retrieval import Retriever

    original_search = Retriever.search
    calls = []

    def capture_search(self, *args, record_access=None, **kwargs):
        calls.append((record_access, self.record_access))
        if record_access is None:
            return original_search(self, *args, **kwargs)
        return original_search(
            self,
            *args,
            record_access=record_access,
            **kwargs,
        )

    monkeypatch.setattr(Retriever, "search", capture_search)

    import agent_brain.interfaces.mcp.server as mcp

    result = mcp.search_memory("injection gateway boundary per call access", top_k=10)

    assert [row["id"] for row in result] == [value.id]
    assert calls == [(False, True)]


def test_mcp_search_only_returns_trace_for_gateway_included_items(tmp_brain):
    safe = memory("safe-trace")
    private = memory("private-trace", sensitivity=Sensitivity.private)
    seed(tmp_brain, [safe, private])

    import agent_brain.interfaces.mcp.server as mcp

    result = mcp.search_memory(
        "injection gateway boundary trace",
        top_k=10,
        include_trace=True,
    )

    assert [row["id"] for row in result] == [safe.id]
    assert "retrieval_trace" in result[0]
    assert private.id not in repr(result)
    assert private.title not in repr(result)


def test_mcp_search_rejects_nonfinite_retrieval_score_before_json(
    tmp_brain,
    monkeypatch,
):
    value = memory("nonfinite-score")
    seed(tmp_brain, [value])

    from agent_brain.memory.recall.retrieval import Retriever

    monkeypatch.setattr(
        Retriever,
        "search",
        lambda *_args, **_kwargs: [
            SimpleNamespace(id=value.id, score=float("nan"), trace=None)
        ],
    )

    import agent_brain.interfaces.mcp.server as mcp

    result = mcp.search_memory("injection gateway boundary nonfinite score", top_k=1)

    assert result == []
