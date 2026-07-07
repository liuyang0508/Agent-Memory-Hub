"""P3-3: confidence becomes dynamic — +on access, -on contradiction/supersede.

ConfidenceFeedback adjusts an item's stored confidence so it stops being a
static write-time guess. Exercised here against the md store (source of truth).
"""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_brain.memory.governance.feedback import ConfidenceFeedback, CAP
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

ITEM_ID = "mem-20260519-100000-conf"


def _store_with_item(tmp: Path, conf: float) -> ItemsStore:
    store = ItemsStore(items_dir=tmp / "items")
    store.write(
        MemoryItem(
            id=ITEM_ID,
            type=MemoryType.decision,
            created_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
            title="c",
            summary="c",
            confidence=conf,
        ),
        "body",
    )
    return store


def _conf(store: ItemsStore) -> float:
    return store.get(ITEM_ID)[0].confidence


def test_contradiction_lowers_confidence(tmp_path: Path):
    store = _store_with_item(tmp_path, 0.7)
    ConfidenceFeedback(items_store=store).on_contradiction(ITEM_ID, penalty=0.15)
    assert _conf(store) == pytest.approx(0.55)


def test_access_raises_confidence(tmp_path: Path):
    store = _store_with_item(tmp_path, 0.7)
    ConfidenceFeedback(items_store=store).on_access(ITEM_ID, reward=0.05)
    assert _conf(store) == pytest.approx(0.75)


def test_access_capped(tmp_path: Path):
    store = _store_with_item(tmp_path, CAP)
    ConfidenceFeedback(items_store=store).on_access(ITEM_ID, reward=0.5)
    assert _conf(store) <= CAP


def test_confidence_clamped_at_zero(tmp_path: Path):
    store = _store_with_item(tmp_path, 0.1)
    ConfidenceFeedback(items_store=store).on_contradiction(ITEM_ID, penalty=0.5)
    assert _conf(store) == pytest.approx(0.0)
