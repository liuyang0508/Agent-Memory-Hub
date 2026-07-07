"""Unit tests for causal chain reasoning."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_brain.memory.store.items_store import ItemsStore, make_item_id
from agent_brain.memory.governance.reasoning.causal_chain import (
    CausalChain,
    CausalTrace,
    add_causal_link,
)
from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs


def _make_item(store, title, mem_type, days_ago, project="proj-x", tags=None, refs_mems=None):
    now = datetime.now(timezone.utc) - timedelta(days=days_ago)
    item = MemoryItem(
        id=make_item_id(title, when=now),
        type=mem_type,
        created_at=now,
        project=project,
        tags=tags or ["api"],
        title=title,
        summary=f"Summary: {title}",
        refs=Refs(mems=refs_mems or []),
    )
    store.write(item, f"Body for {title}")
    return item


@pytest.fixture
def causal_store(tmp_path):
    """Store with a causal chain: decision → episode (bug)."""
    items_dir = tmp_path / "items"
    items_dir.mkdir()
    store = ItemsStore(items_dir=items_dir)

    decision = _make_item(store, "Switch to async SSE", MemoryType.decision, days_ago=10)
    episode = _make_item(store, "Bug: SSE connections dropping", MemoryType.episode, days_ago=3,
                         tags=["api", "sse"])
    signal = _make_item(store, "Alert: high latency on SSE endpoint", MemoryType.signal, days_ago=1,
                        tags=["api", "sse"])

    return store, decision, episode, signal


class TestCausalChain:
    def test_causal_chain_reexports_shared_types(self):
        from agent_brain.memory.governance.reasoning import causal_chain
        from agent_brain.memory.governance.reasoning.causal_types import CausalCandidate, CausalLink

        assert causal_chain.CausalTrace is CausalTrace
        assert causal_chain.CausalLink is CausalLink
        assert causal_chain.CausalCandidate is CausalCandidate

    def test_causal_chain_delegates_scoring_strategy(self, causal_store):
        from agent_brain.memory.governance.reasoning.causal_scoring import CausalScorer

        store, _decision, _episode, _signal = causal_store
        chain = CausalChain(store)

        assert isinstance(chain.scorer, CausalScorer)

    def test_causal_chain_delegates_explicit_cause_extraction(self, tmp_path):
        from agent_brain.memory.governance.reasoning.causal_explicit import get_explicit_causes

        items_dir = tmp_path / "items"
        items_dir.mkdir()
        store = ItemsStore(items_dir=items_dir)

        cause = _make_item(store, "Explicit cause", MemoryType.decision, days_ago=4)
        effect = _make_item(
            store,
            "Explicit effect",
            MemoryType.episode,
            days_ago=1,
            refs_mems=[cause.id],
        )

        chain = CausalChain(store)
        items = chain._load_items()

        direct = get_explicit_causes(None, effect.id, items)
        delegated = chain._get_explicit_causes(effect.id, items)

        assert [candidate.item.id for candidate in direct] == [cause.id]
        assert [candidate.item.id for candidate in delegated] == [cause.id]

    def test_implicit_cause_finder_filters_time_window_and_types(self, tmp_path):
        from agent_brain.memory.governance.reasoning.causal_inference import find_implicit_causes

        items_dir = tmp_path / "items"
        items_dir.mkdir()
        store = ItemsStore(items_dir=items_dir)

        cause = _make_item(store, "Recent decision", MemoryType.decision, days_ago=4)
        old = _make_item(store, "Old decision", MemoryType.decision, days_ago=30)
        future = _make_item(store, "Future signal", MemoryType.signal, days_ago=0)
        note = _make_item(store, "Recent fact", MemoryType.fact, days_ago=3)
        effect = _make_item(store, "Current incident", MemoryType.episode, days_ago=1)

        class FixedScorer:
            def compute(self, *, cause, effect):
                return 0.5, ["fixed"]

        items = {item.id: (item, "") for item in (cause, old, future, note, effect)}
        candidates = find_implicit_causes(
            item_id=effect.id,
            visited={effect.id},
            items=items,
            scorer=FixedScorer(),
            temporal_window_days=10,
        )

        assert [candidate.item.id for candidate in candidates] == [cause.id]

    def test_related_decision_finder_is_split(self):
        from agent_brain.memory.governance.reasoning import causal_chain
        from agent_brain.memory.governance.reasoning.causal_related import find_related_decisions

        assert causal_chain.find_related_decisions is find_related_decisions

    def test_trace_finds_temporal_cause(self, causal_store):
        store, decision, episode, signal = causal_store
        chain = CausalChain(store)
        trace = chain.trace_cause(signal.id, max_depth=3)
        assert trace.origin_id == signal.id
        assert trace.depth >= 1
        ids_in_chain = [link.source_id for link in trace.chain]
        assert episode.id in ids_in_chain or decision.id in ids_in_chain

    def test_trace_effects_forward(self, causal_store):
        store, decision, episode, signal = causal_store
        chain = CausalChain(store)
        trace = chain.trace_effects(decision.id, max_depth=3)
        assert trace.origin_id == decision.id
        if trace.depth > 0:
            effect_ids = [link.target_id for link in trace.chain]
            assert episode.id in effect_ids or signal.id in effect_ids

    def test_find_related_decisions(self, causal_store):
        store, decision, episode, signal = causal_store
        chain = CausalChain(store)
        candidates = chain.find_related_decisions(episode.id)
        decision_ids = [c.item.id for c in candidates]
        assert decision.id in decision_ids

    def test_explicit_refs_boost(self, tmp_path):
        items_dir = tmp_path / "items"
        items_dir.mkdir()
        store = ItemsStore(items_dir=items_dir)

        cause = _make_item(store, "Chose REST over gRPC", MemoryType.decision, days_ago=20)
        effect = _make_item(store, "Performance issue in API layer", MemoryType.episode,
                           days_ago=2, refs_mems=[cause.id])

        chain = CausalChain(store)
        trace = chain.trace_cause(effect.id)
        assert trace.depth >= 1
        assert cause.id in [link.source_id for link in trace.chain]

    def test_different_project_reduces_score(self, tmp_path):
        items_dir = tmp_path / "items"
        items_dir.mkdir()
        store = ItemsStore(items_dir=items_dir)

        unrelated = _make_item(store, "Auth decision", MemoryType.decision,
                              days_ago=5, project="proj-other", tags=["auth"])
        related = _make_item(store, "API decision", MemoryType.decision,
                            days_ago=5, project="proj-x", tags=["api"])
        bug = _make_item(store, "API bug found", MemoryType.episode,
                        days_ago=1, project="proj-x", tags=["api"])

        chain = CausalChain(store)
        candidates = chain.find_related_decisions(bug.id)
        if len(candidates) >= 2:
            scores = {c.item.id: c.score for c in candidates}
            assert scores.get(related.id, 0) > scores.get(unrelated.id, 0)

    def test_empty_store(self, tmp_path):
        items_dir = tmp_path / "items"
        items_dir.mkdir()
        store = ItemsStore(items_dir=items_dir)
        chain = CausalChain(store)
        trace = chain.trace_cause("nonexistent-id")
        assert trace.depth == 0

    def test_max_depth_respected(self, tmp_path):
        items_dir = tmp_path / "items"
        items_dir.mkdir()
        store = ItemsStore(items_dir=items_dir)

        items = []
        for i in range(10):
            item = _make_item(store, f"Chain item {i}", MemoryType.decision,
                            days_ago=10 - i, tags=["chain"])
            items.append(item)

        chain = CausalChain(store)
        trace = chain.trace_cause(items[-1].id, max_depth=3)
        assert trace.depth <= 3

    def test_item_ids_in_order(self, causal_store):
        store, decision, episode, signal = causal_store
        chain = CausalChain(store)
        trace = chain.trace_cause(signal.id, max_depth=5)
        if trace.depth > 0:
            ids = trace.item_ids_in_order
            assert ids[-1] == signal.id
