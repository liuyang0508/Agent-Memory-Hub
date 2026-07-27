from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.memory.context.injection_cohorts import record_injection_cohort
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex


def _seed(store: ItemsStore, index: HubIndex, suffix: str) -> str:
    item = MemoryItem(
        id=f"mem-20260728-030000-{suffix}",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title=f"Auto feedback {suffix}",
        summary=f"Auto feedback summary {suffix}",
    )
    store.write(item, "body")
    index.upsert(item, "body", embedding=HashingEmbedder(dim=8).embed("body"))
    return item.id


def test_explicit_multi_item_feedback_autotunes_once(tmp_path: Path) -> None:
    from agent_brain.memory.governance.auto_feedback import observe_prompt_feedback

    store = ItemsStore(tmp_path / "items")
    index = HubIndex(tmp_path / "index.db", embedding_dim=8)
    first = _seed(store, index, "first")
    second = _seed(store, index, "second")
    record_injection_cohort(
        tmp_path,
        item_ids=[first, second],
        adapter="codex",
        session_id="s1",
    )

    applied = observe_prompt_feedback(
        tmp_path,
        prompt="刚才这些记忆都很有用，继续",
        adapter="codex",
        session_id="s1",
        items_store=store,
        index=index,
    )
    repeated = observe_prompt_feedback(
        tmp_path,
        prompt="刚才这些记忆都很有用",
        adapter="codex",
        session_id="s1",
        items_store=store,
        index=index,
    )

    assert applied.applied is True
    assert applied.adopted == (first, second)
    assert store.get(first)[0].support_count == 1
    assert store.get(second)[0].support_count == 1
    assert repeated.reason == "already_observed"
    assert store.get(first)[0].support_count == 1


def test_ambiguous_multi_item_continue_does_not_tune(tmp_path: Path) -> None:
    from agent_brain.memory.governance.auto_feedback import observe_prompt_feedback

    store = ItemsStore(tmp_path / "items")
    index = HubIndex(tmp_path / "index.db", embedding_dim=8)
    first = _seed(store, index, "ambiguous-first")
    second = _seed(store, index, "ambiguous-second")
    record_injection_cohort(
        tmp_path,
        item_ids=[first, second],
        adapter="codex",
        session_id="s2",
    )

    report = observe_prompt_feedback(
        tmp_path,
        prompt="继续",
        adapter="codex",
        session_id="s2",
        items_store=store,
        index=index,
    )

    assert report.applied is False
    assert report.reason == "ambiguous_or_no_signal"
    assert store.get(first)[0].support_count == 0


def test_single_item_continue_and_explicit_rejection_are_safe(tmp_path: Path) -> None:
    from agent_brain.memory.governance.auto_feedback import observe_prompt_feedback

    store = ItemsStore(tmp_path / "items")
    index = HubIndex(tmp_path / "index.db", embedding_dim=8)
    adopted = _seed(store, index, "single")
    record_injection_cohort(
        tmp_path,
        item_ids=[adopted],
        adapter="codex",
        session_id="s3",
    )
    positive = observe_prompt_feedback(
        tmp_path,
        prompt="继续",
        adapter="codex",
        session_id="s3",
        items_store=store,
        index=index,
    )

    rejected = _seed(store, index, "rejected")
    record_injection_cohort(
        tmp_path,
        item_ids=[rejected],
        adapter="codex",
        session_id="s4",
    )
    negative = observe_prompt_feedback(
        tmp_path,
        prompt="刚才那条记忆不对，已经过期了",
        adapter="codex",
        session_id="s4",
        items_store=store,
        index=index,
    )

    assert positive.adopted == (adopted,)
    assert negative.rejected == (rejected,)
    assert store.get(adopted)[0].gain_score > 0
    assert store.get(rejected)[0].gain_score < 0
