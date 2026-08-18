from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.memory.context.injection_cohorts import InjectionCohort, record_injection_cohort
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


def _cohort(item_ids: tuple[str, ...]) -> InjectionCohort:
    return InjectionCohort(
        cohort_id="c1",
        timestamp=datetime(2026, 8, 19, tzinfo=timezone.utc).isoformat(),
        item_ids=item_ids,
        adapter="claude_code",
    )


_NEGATIVE_PROMPTS = [
    "刚才的回答不准确",
    "上次的建议误导了我",
    "之前的内容没有用",
    "之前的内容没帮到我",
    "这些回答都不正确",
    "the last answer is inaccurate",
    "all of those were misleading",
    "those notes were not relevant",
    "the previous answer didn't help",
]

_POSITIVE_PROMPTS = [
    "刚才的回答准确",
    "上次的建议帮了大忙",
    "the previous answer was spot on",
    "those notes are still relevant",
    "the last answer was accurate",
]

_AMBIGUOUS_PROMPTS = [
    "the last answer was correct but the previous one was wrong",
    "刚才的回答 incorrect, but useful",
    "今天天气怎么样",
]


@pytest.mark.parametrize("prompt", _NEGATIVE_PROMPTS)
def test_expanded_negative_wording_classifies_rejected(prompt: str) -> None:
    from agent_brain.memory.governance.auto_feedback import _classify

    assert _classify(prompt, _cohort(("mem-1", "mem-2"))) == "rejected"


@pytest.mark.parametrize("prompt", _POSITIVE_PROMPTS)
def test_expanded_positive_wording_classifies_adopted(prompt: str) -> None:
    from agent_brain.memory.governance.auto_feedback import _classify

    assert _classify(prompt, _cohort(("mem-1", "mem-2"))) == "adopted"


@pytest.mark.parametrize("prompt", _AMBIGUOUS_PROMPTS)
def test_ambiguous_wording_stays_none(prompt: str) -> None:
    from agent_brain.memory.governance.auto_feedback import _classify

    assert _classify(prompt, _cohort(("mem-1", "mem-2"))) == "none"


def test_single_item_continue_still_adopted() -> None:
    from agent_brain.memory.governance.auto_feedback import _classify

    assert _classify("继续", _cohort(("mem-1",))) == "adopted"
    assert _classify("继续", _cohort(("mem-1", "mem-2"))) == "none"
