from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def _seed(store: ItemsStore, index: HubIndex, suffix: str) -> str:
    item = MemoryItem(
        id=f"mem-20260612-030000-{suffix}",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title=f"Outcome {suffix}",
        summary=f"Outcome summary {suffix}",
        confidence=0.7,
    )
    body = f"body {suffix}"
    store.write(item, body)
    index.upsert(item, body, embedding=HashingEmbedder(dim=8).embed(body))
    return item.id


def test_task_outcome_feedback_applies_explicit_ids_once(tmp_path: Path) -> None:
    from agent_brain.memory.governance.outcome_feedback import apply_task_outcome_feedback_batch
    from agent_brain.memory.governance.recall_events import record_task_outcome

    store = ItemsStore(tmp_path / "items")
    index = HubIndex(tmp_path / "index.db", embedding_dim=8)
    adopted = _seed(store, index, "adopted")
    rejected = _seed(store, index, "rejected")
    ignored = _seed(store, index, "ignored")
    record_task_outcome(
        tmp_path,
        task_id="task-explicit",
        question="fix recall drift",
        outcome="success",
        injected_ids=[adopted, rejected, ignored],
        adopted_ids=[adopted],
        rejected_ids=[rejected],
        confidence=0.95,
        feedback_signals=["explicit_user_confirmed"],
    )

    first = apply_task_outcome_feedback_batch(tmp_path, items_store=store, index=index)
    second = apply_task_outcome_feedback_batch(tmp_path, items_store=store, index=index)

    assert first.applied_count == 1
    assert first.skipped_count == 0
    assert second.applied_count == 0
    assert second.already_applied_count == 1
    assert store.get(adopted)[0].support_count == 1
    assert store.get(rejected)[0].contradict_count == 1
    assert store.get(ignored)[0].support_count == 0
    assert index.get_feedback_data([adopted])[adopted] == (1, 0, 0.1)
    assert index.get_feedback_data([rejected])[rejected] == (0, 1, -0.2)


def test_task_outcome_feedback_skips_ambiguous_implicit_without_explicit_ids(tmp_path: Path) -> None:
    from agent_brain.memory.governance.outcome_feedback import apply_task_outcome_feedback_batch
    from agent_brain.memory.governance.recall_events import record_task_outcome

    store = ItemsStore(tmp_path / "items")
    index = HubIndex(tmp_path / "index.db", embedding_dim=8)
    injected = _seed(store, index, "implicit")
    other_injected = _seed(store, index, "other-implicit")
    record_task_outcome(
        tmp_path,
        task_id="task-implicit",
        question="continue recall drift work",
        outcome="success",
        feedback_signals=["implicit_continue"],
        value_tags=["workflow_pattern"],
        confidence=0.95,
        injected_ids=[injected, other_injected],
    )

    report = apply_task_outcome_feedback_batch(tmp_path, items_store=store, index=index)

    assert report.applied_count == 0
    assert report.skipped_count == 1
    assert report.reports[0].skipped_reason == "no_explicit_feedback"
    assert store.get(injected)[0].support_count == 0
    assert index.get_feedback_data([injected])[injected] == (0, 0, 0.0)


def test_task_outcome_feedback_applies_single_high_confidence_implicit_positive(tmp_path: Path) -> None:
    from agent_brain.memory.governance.outcome_feedback import apply_task_outcome_feedback_batch
    from agent_brain.memory.governance.recall_events import record_task_outcome

    store = ItemsStore(tmp_path / "items")
    index = HubIndex(tmp_path / "index.db", embedding_dim=8)
    injected = _seed(store, index, "single-implicit")
    record_task_outcome(
        tmp_path,
        task_id="task-single-implicit",
        question="continue recall drift work",
        outcome="success",
        feedback_signals=["implicit_continue"],
        value_tags=["workflow pattern"],
        confidence=0.95,
        injected_ids=[injected],
    )

    report = apply_task_outcome_feedback_batch(tmp_path, items_store=store, index=index)

    assert report.applied_count == 1
    assert report.reports[0].adopted == (injected,)
    item = store.get(injected)[0]
    assert item.support_count == 1
    assert item.gain_score == 0.03
    assert "value:implicit-positive" in item.tags
    assert "value:workflow-pattern" in item.tags
    assert index.get_feedback_data([injected])[injected] == (1, 0, 0.03)


def test_task_outcome_feedback_missing_items_can_retry_later(tmp_path: Path) -> None:
    from agent_brain.memory.governance.outcome_feedback import apply_task_outcome_feedback_batch
    from agent_brain.memory.governance.recall_events import record_task_outcome

    store = ItemsStore(tmp_path / "items")
    index = HubIndex(tmp_path / "index.db", embedding_dim=8)
    adopted = "mem-20260612-030000-late-adopted"
    record_task_outcome(
        tmp_path,
        task_id="task-late",
        question="late outcome feedback",
        outcome="success",
        injected_ids=[adopted],
        adopted_ids=[adopted],
    )

    first = apply_task_outcome_feedback_batch(tmp_path, items_store=store, index=index)
    item = MemoryItem(
        id=adopted,
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="Late adopted",
        summary="Late adopted",
    )
    store.write(item, item.summary)
    index.upsert(item, item.summary, embedding=HashingEmbedder(dim=8).embed(item.summary))
    second = apply_task_outcome_feedback_batch(tmp_path, items_store=store, index=index)

    assert first.reports[0].skipped_reason == "missing_feedback_items"
    assert second.applied_count == 1
    assert store.get(adopted)[0].support_count == 1


def test_task_outcome_feedback_tags_adopted_items_with_value_tags(tmp_path: Path) -> None:
    from agent_brain.memory.governance.outcome_feedback import apply_task_outcome_feedback_batch
    from agent_brain.memory.governance.recall_events import record_task_outcome

    store = ItemsStore(tmp_path / "items")
    index = HubIndex(tmp_path / "index.db", embedding_dim=8)
    adopted = _seed(store, index, "tagged")
    rejected = _seed(store, index, "not-tagged")
    store.update_frontmatter(adopted, tags=["existing"])
    item, body = store.get(adopted)
    index.upsert(item, body, embedding=None)
    record_task_outcome(
        tmp_path,
        task_id="task-value-tags",
        question="browser memory should not repeat stale browser limitation",
        outcome="success",
        injected_ids=[adopted, rejected],
        adopted_ids=[adopted],
        rejected_ids=[rejected],
        value_tags=["Browser Fix", "workflow pattern", "value:already-normalized"],
    )

    report = apply_task_outcome_feedback_batch(tmp_path, items_store=store, index=index)

    assert report.applied_count == 1
    adopted_item = store.get(adopted)[0]
    rejected_item = store.get(rejected)[0]
    assert "existing" in adopted_item.tags
    assert "value:browser-fix" in adopted_item.tags
    assert "value:workflow-pattern" in adopted_item.tags
    assert "value:already-normalized" in adopted_item.tags
    assert "value:browser-fix" not in rejected_item.tags
    assert adopted in index.filter_ids(tags=["value:browser-fix"], include_superseded=False)
