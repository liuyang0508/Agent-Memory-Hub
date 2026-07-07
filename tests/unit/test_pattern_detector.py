"""Tests for evolve/pattern_detector.py — MinHash LSH clustering."""
from datetime import datetime, timezone

from agent_brain.memory.governance.evolve.pattern_detector import (
    CRYSTALLIZE_THRESHOLD,
    PatternCluster,
    detect_patterns,
    _shingles,
    _tokenize,
)
from agent_brain.contracts.memory_item import AbstractionLayer, MemoryItem, MemoryType


def _make_item(suffix: str, title: str, summary: str, tags=None, project="test-proj"):
    return MemoryItem(
        id=f"mem-20260601-100000-{suffix}",
        type=MemoryType.episode,
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        title=title,
        summary=summary,
        tags=tags or [],
        project=project,
        abstraction=AbstractionLayer.L0,
    )


def test_tokenize_cjk_and_latin():
    tokens = _tokenize("Hello 世界 foo-bar baz")
    assert "hello" in tokens
    assert "世界" in tokens
    assert "baz" in tokens


def test_shingles_short_input():
    tokens = ["a", "b"]
    s = _shingles(tokens)
    assert len(s) == 1
    assert "a b" in s


def test_similar_items_cluster_together():
    items = [
        (_make_item("a", "use SSE for real-time push notifications", "SSE is lighter than WebSocket for push", ["streaming", "sse"]), "body a"),
        (_make_item("b", "use SSE for real-time push updates", "SSE lightweight real-time push", ["streaming", "sse"]), "body b"),
        (_make_item("c", "use SSE for real-time push streaming", "SSE real-time push solution", ["streaming", "sse"]), "body c"),
    ]
    clusters = detect_patterns(items, threshold=3)
    assert len(clusters) >= 1
    cluster = clusters[0]
    assert cluster.support_count >= 3
    assert set(cluster.item_ids) == {"mem-20260601-100000-a", "mem-20260601-100000-b", "mem-20260601-100000-c"}


def test_dissimilar_items_no_cluster():
    items = [
        (_make_item("x", "数据库索引优化", "B+Tree 索引策略", ["db"]), "body x"),
        (_make_item("y", "前端 CSS 动画", "transition 性能", ["css"]), "body y"),
        (_make_item("z", "K8s Pod 调度", "资源 request/limit", ["k8s"]), "body z"),
    ]
    clusters = detect_patterns(items, threshold=3)
    assert len(clusters) == 0


def test_threshold_respected():
    items = [
        (_make_item("a", "pattern alpha foo", "summary alpha foo", ["t"]), ""),
        (_make_item("b", "pattern alpha foo", "summary alpha foo", ["t"]), ""),
    ]
    assert detect_patterns(items, threshold=3) == []
    assert len(detect_patterns(items, threshold=2)) >= 1


def test_only_l0_filter():
    item = _make_item("a", "pattern repeat", "pattern repeat", ["t"])
    item.abstraction = AbstractionLayer.L1
    items = [
        (item, ""),
        (_make_item("b", "pattern repeat", "pattern repeat", ["t"]), ""),
        (_make_item("c", "pattern repeat", "pattern repeat", ["t"]), ""),
    ]
    clusters = detect_patterns(items, threshold=2, only_l0=True)
    for c in clusters:
        assert "mem-20260601-100000-a" not in c.item_ids
