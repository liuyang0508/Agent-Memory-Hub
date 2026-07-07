from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def _seed(store: ItemsStore, idx: HubIndex, suffix: str, confidence: float = 0.7) -> str:
    item = MemoryItem(
        id=f"mem-20260611-120000-{suffix}",
        type=MemoryType.episode,
        created_at=datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc),
        title=f"Item {suffix}",
        summary=f"Summary {suffix}",
        confidence=confidence,
    )
    body = f"body {suffix}"
    store.write(item, body)
    idx.upsert(item, body, embedding=HashingEmbedder(dim=8).embed(body))
    return item.id


def _item(store: ItemsStore, item_id: str) -> MemoryItem:
    return store.get(item_id)[0]


def test_injection_feedback_only_reinforces_adopted_and_penalizes_rejected(tmp_path: Path) -> None:
    from agent_brain.memory.context.injection_feedback import InjectionFeedback

    store = ItemsStore(tmp_path / "items")
    idx = HubIndex(tmp_path / "index.db", embedding_dim=8)
    adopted = _seed(store, idx, "adopted", confidence=0.7)
    rejected = _seed(store, idx, "rejected", confidence=0.7)
    ignored = _seed(store, idx, "ignored", confidence=0.7)

    report = InjectionFeedback(items_store=store, index=idx).apply(
        injected_ids=[adopted, rejected, ignored],
        adopted_ids=[adopted],
        rejected_ids=[rejected],
    )

    adopted_item = _item(store, adopted)
    rejected_item = _item(store, rejected)
    ignored_item = _item(store, ignored)
    assert report.adopted == (adopted,)
    assert report.rejected == (rejected,)
    assert report.ignored == (ignored,)
    assert adopted_item.support_count == 1
    assert adopted_item.gain_score > 0
    assert adopted_item.confidence > 0.7
    assert rejected_item.contradict_count == 1
    assert rejected_item.gain_score < 0
    assert rejected_item.confidence < 0.7
    assert ignored_item.support_count == 0
    assert ignored_item.contradict_count == 0
    assert ignored_item.gain_score == 0
    assert ignored_item.confidence == 0.7
    assert idx.get_feedback_data([adopted])[adopted] == (1, 0, 0.1)
    assert idx.get_feedback_data([rejected])[rejected] == (0, 1, -0.2)
    assert idx.get_feedback_data([ignored])[ignored] == (0, 0, 0.0)


def test_injection_feedback_rejects_out_of_cohort_feedback(tmp_path: Path) -> None:
    from agent_brain.memory.context.injection_feedback import InjectionFeedback

    store = ItemsStore(tmp_path / "items")
    idx = HubIndex(tmp_path / "index.db", embedding_dim=8)
    injected = _seed(store, idx, "injected")
    not_injected = _seed(store, idx, "notinjected")

    with pytest.raises(ValueError, match="not in injected cohort"):
        InjectionFeedback(items_store=store, index=idx).apply(
            injected_ids=[injected],
            adopted_ids=[not_injected],
        )
