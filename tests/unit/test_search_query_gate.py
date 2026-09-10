"""MEMORY_HUB_SEARCH_QUERY_GATE=off keeps scoped CJK recall working.

The injectability gate rejects any long CJK clause as ``unanchored`` because
its anchors (files, JSON fields, ASCII identifiers) never occur in dialog-style
queries.  Programmatic scoped callers would lose every BM25 hit; the opt-out
must restore recall while keeping item-level firewall exclusions.
"""

from datetime import datetime, timezone

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
        id=f"mem-20260910-010000-{suffix}",
        type=MemoryType.fact,
        created_at=datetime(2026, 9, 10, 1, 0, tzinfo=timezone.utc),
        title=f"可上课时间：工作日晚七点后、周末随时 {suffix}",
        summary=f"学习时段事实 {suffix}",
        tags=tags or [],
        sensitivity=sensitivity,
        superseded_by=superseded_by,
        refs={"urls": [f"https://example.test/{suffix}"]},
        confidence=0.9,
    )


def body_for(value):
    return f"学员表示工作日晚上七点之后或周末随时都有时间上课 {value.title}"


def seed(brain, items):
    store = ItemsStore(brain / "items")
    embedder = HashingEmbedder()
    index = HubIndex(brain / "index.db", embedding_dim=embedder.dim)
    for value in items:
        body = body_for(value)
        store.write(value, body)
        index.upsert(value, body, embedding=embedder.embed(body))
    index.close()


LONG_CJK_QUERY = (
    "关于老师，我平时只有晚上和周末有时间学习，你帮我按这个时间安排一下课程吧，"
    "检索当前学员本轮回复所需的相关历史事实与偏好。"
)


def test_query_gate_disabled_reads_env(monkeypatch):
    from agent_brain.memory.context.injection_gateway import search_query_gate_disabled

    monkeypatch.delenv("MEMORY_HUB_SEARCH_QUERY_GATE", raising=False)
    assert search_query_gate_disabled() is False
    monkeypatch.setenv("MEMORY_HUB_SEARCH_QUERY_GATE", "off")
    assert search_query_gate_disabled() is True
    monkeypatch.setenv("MEMORY_HUB_SEARCH_QUERY_GATE", "OFF")
    assert search_query_gate_disabled() is True
    monkeypatch.setenv("MEMORY_HUB_SEARCH_QUERY_GATE", "on")
    assert search_query_gate_disabled() is False


def test_default_gate_still_blocks_long_cjk_clause(tmp_brain):
    seed(tmp_brain, [memory("a"), memory("b")])

    import agent_brain.interfaces.mcp.server as mcp

    result = mcp.search_memory(LONG_CJK_QUERY, top_k=10, verbosity="detail")
    assert result == []


def test_gate_off_returns_hits_and_keeps_item_firewall(tmp_brain, monkeypatch):
    monkeypatch.setenv("MEMORY_HUB_SEARCH_QUERY_GATE", "off")
    safe = memory("safe")
    private = memory("private", sensitivity=Sensitivity.private)
    review = memory("review", tags=["needs-review"])
    superseded = memory("old", superseded_by=safe.id)
    seed(tmp_brain, [safe, private, review, superseded])

    import agent_brain.interfaces.mcp.server as mcp

    result = mcp.search_memory(LONG_CJK_QUERY, top_k=10, verbosity="detail")

    assert [row["id"] for row in result] == [safe.id]
    serialized = repr(result)
    for forbidden in (private, review, superseded):
        assert forbidden.id not in serialized
        assert forbidden.title not in serialized
    assert result[0]["title"] == safe.title
    assert body_for(safe) in result[0]["body"]


def test_gate_off_respects_scope_filters(tmp_brain, monkeypatch):
    monkeypatch.setenv("MEMORY_HUB_SEARCH_QUERY_GATE", "off")
    tagged = memory("tagged", tags=["subject-x"])
    other = memory("other", tags=["subject-y"])
    seed(tmp_brain, [tagged, other])

    import agent_brain.interfaces.mcp.server as mcp

    result = mcp.search_memory(
        LONG_CJK_QUERY, top_k=10, verbosity="detail", tags=["subject-x"]
    )
    assert [row["id"] for row in result] == [tagged.id]
