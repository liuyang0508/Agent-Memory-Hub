from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_brain.contracts.conversation import (
    ConversationMessageRecord,
    ConversationRetention,
    ConversationTier,
)
from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.contracts.resource import sha256_text
from agent_brain.memory.evidence.conversation_store import ConversationStore
from agent_brain.memory.store.items_store import ItemsStore


def _matureable_item(item_id: str = "mem-20260618-160000-auto-govern") -> MemoryItem:
    return MemoryItem(
        id=item_id,
        type=MemoryType.decision,
        created_at=datetime(2026, 6, 18, tzinfo=timezone.utc),
        title="Auto governance maturity",
        summary="Auto governance maturity locator",
        confidence=0.85,
        tags=["auto-governance"],
        refs={"files": ["docs/architecture.md"], "mems": ["mem-20260618-010101-source"]},
        support_count=4,
        gain_score=0.3,
        context_views={
            "locator": "Auto governance maturity locator",
            "overview": "Auto governance maturity overview with reusable evidence.",
        },
    )


def _expired_signal() -> MemoryItem:
    return MemoryItem(
        id="mem-20250101-000000-expired-signal",
        type=MemoryType.signal,
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        title="Expired signal",
        summary="Expired signal that should require review before archive",
        confidence=0.4,
        tags=["signal"],
    )


def test_auto_governance_item_snapshot_preserves_excluded_schema_and_nested_isolation() -> None:
    from agent_brain.memory.governance.auto_governance import AutoGovernanceReport

    item = _matureable_item().model_copy(
        update={"schema_version": "99"},
        deep=True,
    )
    report = AutoGovernanceReport(
        scanned_items=1,
        actions=[],
        items_by_id={item.id: item},
    )
    item.refs.files.append("caller-mutation.md")
    first = report.items_by_id[item.id]
    first.refs.files.append("returned-mutation.md")
    second = report.items_by_id[item.id]

    assert second.schema_version == "99"
    assert second.refs.files == ["docs/architecture.md"]
    with pytest.raises(TypeError):
        report.items_by_id[item.id] = item  # type: ignore[index]


def test_auto_governance_preview_rewrites_long_summary_without_mutating(
    tmp_brain_dir: Path,
) -> None:
    from agent_brain.memory.governance.auto_governance import AutoGovernanceCycle

    long_summary = (
        "This summary is intentionally long and contains the important locator "
        "up front. It then keeps adding operational detail about validation, "
        "handoff, commands, and historical context until it crosses the quality "
        "threshold used by the governance pipeline."
    )
    store = ItemsStore(tmp_brain_dir / "items")
    item = MemoryItem(
        id="mem-20260618-160010-long-summary",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Long summary preview",
        summary=long_summary,
        tags=["quality"],
    )
    store.write(item, "body")

    report = AutoGovernanceCycle(
        brain_dir=tmp_brain_dir,
        items_store=store,
        include_evolve=False,
        include_index=False,
        include_conversations=False,
    ).run(apply=False)

    action = next(action for action in report.actions if action.action == "review_quality")
    assert action.details["summary_rewrite"]["current_summary"] == long_summary
    assert action.details["summary_rewrite"]["current_length"] == len(long_summary)
    assert action.details["summary_rewrite"]["target_length"] == 200
    assert len(action.details["summary_rewrite"]["candidate_summary"]) <= 200
    assert action.details["summary_rewrite"]["candidate_summary"] != long_summary

    unchanged, _ = store.get(item.id)
    assert unchanged.summary == long_summary


def test_auto_governance_dry_run_plans_maturity_without_mutating(tmp_brain_dir: Path) -> None:
    from agent_brain.memory.governance.auto_governance import AutoGovernanceCycle

    store = ItemsStore(tmp_brain_dir / "items")
    item = _matureable_item()
    store.write(item, "body")

    report = AutoGovernanceCycle(
        brain_dir=tmp_brain_dir,
        items_store=store,
        include_evolve=False,
        include_index=False,
        include_conversations=False,
    ).run(apply=False)

    assert report.safe_apply_count == 1
    assert report.applied_count == 0
    assert report.review_required_count == 0
    action = report.actions[0]
    assert action.action == "update_maturity"
    assert action.risk == "safe_apply"
    assert action.applied is False

    unchanged, _ = store.get(item.id)
    assert unchanged.maturity == "raw"
    assert unchanged.abstraction == "L0"


def test_auto_governance_ignores_default_enum_noop_maturity(
    tmp_brain_dir: Path,
) -> None:
    from agent_brain.memory.governance.auto_governance import AutoGovernanceCycle

    store = ItemsStore(tmp_brain_dir / "items")
    (store.items_dir / "mem-20260618-160003-default-enum-noop.md").write_text(
        """---
id: mem-20260618-160003-default-enum-noop
type: fact
created_at: '2026-06-18T16:00:03+00:00'
title: Default enum no-op
summary: Low evidence item remains raw L0
confidence: 0.2
tags: []
sensitivity: internal
refs:
  files: []
  urls: []
  mems: []
  commits: []
retention:
  access_count: 0
  decay_class: fact
---

body
""",
        encoding="utf-8",
    )

    report = AutoGovernanceCycle(
        brain_dir=tmp_brain_dir,
        items_store=store,
        include_evolve=False,
        include_index=False,
        include_conversations=False,
    ).run(apply=False)

    assert [
        action
        for action in report.actions
        if action.action == "update_maturity"
    ] == []


def test_auto_governance_apply_updates_only_safe_maturity_actions(tmp_brain_dir: Path) -> None:
    from agent_brain.memory.governance.auto_governance import AutoGovernanceCycle

    store = ItemsStore(tmp_brain_dir / "items")
    matureable = _matureable_item("mem-20260618-160001-auto-govern-apply")
    expired = _expired_signal()
    store.write(matureable, "body")
    store.write(expired, "expired body")

    report = AutoGovernanceCycle(
        brain_dir=tmp_brain_dir,
        items_store=store,
        include_evolve=False,
        include_index=False,
        include_conversations=False,
    ).run(apply=True)

    assert report.safe_apply_count == 1
    assert report.applied_count == 1
    assert report.review_required_count >= 1
    assert any(action.action == "review_archive" for action in report.actions)
    assert all(
        action.applied is False
        for action in report.actions
        if action.risk == "review_required"
    )

    updated, _ = store.get(matureable.id)
    assert updated.maturity == "consolidated"
    assert updated.abstraction == "L1"
    still_present, _ = store.get(expired.id)
    assert still_present.id == expired.id


def test_auto_governance_rebalances_conversations_only_when_apply_is_true(
    tmp_brain_dir: Path,
) -> None:
    from agent_brain.memory.governance.auto_governance import AutoGovernanceCycle

    store = ItemsStore(tmp_brain_dir / "items")
    conversation_store = ConversationStore(tmp_brain_dir)
    observed_at = datetime.now(timezone.utc) - timedelta(days=90)
    message = ConversationMessageRecord(
        id="cmsg-111111111111111111111111",
        conversation_id="conv-1111111111111111-auto-govern",
        source_agent="codex",
        session_id="auto-govern",
        role="user",
        content_text="old low-importance raw evidence",
        content_sha256=sha256_text("old low-importance raw evidence"),
        observed_at=observed_at,
        sensitivity="internal",
        tier=ConversationTier.hot,
        retention=ConversationRetention(half_life_days=1, importance=0.0),
    )
    assert conversation_store.write_message(message) is True

    dry_run = AutoGovernanceCycle(
        brain_dir=tmp_brain_dir,
        items_store=store,
        conversation_store=conversation_store,
        include_evolve=False,
        include_index=False,
    ).run(apply=False)

    assert any(action.action == "conversation_rebalance" for action in dry_run.actions)
    unchanged = list(conversation_store.iter_messages(message.conversation_id))[0]
    assert unchanged.tier == "hot"

    applied = AutoGovernanceCycle(
        brain_dir=tmp_brain_dir,
        items_store=store,
        conversation_store=conversation_store,
        include_evolve=False,
        include_index=False,
    ).run(apply=True)

    assert any(
        action.action == "conversation_rebalance" and action.applied is True
        for action in applied.actions
    )
    rebalanced = list(conversation_store.iter_messages(message.conversation_id))[0]
    assert rebalanced.tier != "hot"


def test_auto_governance_repairs_index_drift_only_when_apply_is_true(
    tmp_brain_dir: Path,
) -> None:
    from agent_brain.memory.governance.auto_governance import AutoGovernanceCycle

    class FakeIndex:
        def __init__(self) -> None:
            self.ids = {"ghost"}
            self.deleted: list[str] = []
            self.upserted: list[str] = []

        def all_ids(self) -> set[str]:
            return set(self.ids)

        def delete(self, item_id: str) -> None:
            self.deleted.append(item_id)
            self.ids.discard(item_id)

        def upsert(self, item: MemoryItem, body: str, *, embedding: list[float]) -> None:
            self.upserted.append(item.id)
            self.ids.add(item.id)

    class FakeEmbedder:
        def embed(self, text: str) -> list[float]:
            return [float(len(text))]

    store = ItemsStore(tmp_brain_dir / "items")
    item = _matureable_item("mem-20260618-160002-index-drift")
    store.write(item, "body")
    index = FakeIndex()

    dry_run = AutoGovernanceCycle(
        brain_dir=tmp_brain_dir,
        items_store=store,
        index=index,
        embedder=FakeEmbedder(),
        include_evolve=False,
        include_conversations=False,
    ).run(apply=False)

    assert any(action.action == "index_repair" for action in dry_run.actions)
    assert index.deleted == []
    assert index.upserted == []

    applied = AutoGovernanceCycle(
        brain_dir=tmp_brain_dir,
        items_store=store,
        index=index,
        embedder=FakeEmbedder(),
        include_evolve=False,
        include_conversations=False,
    ).run(apply=True)

    assert any(
        action.action == "index_repair" and action.applied is True
        for action in applied.actions
    )
    assert index.deleted == ["ghost"]
    assert item.id in index.upserted


def test_auto_governance_supersedes_only_exact_scoped_duplicates(
    tmp_brain_dir: Path,
) -> None:
    from agent_brain.memory.governance.auto_governance import AutoGovernanceCycle

    store = ItemsStore(tmp_brain_dir / "items")
    first = MemoryItem(
        id="mem-20260701-000001-exact-first",
        type=MemoryType.fact,
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        title="Exact duplicate",
        summary="Same locator",
        project="p",
        support_count=2,
    )
    second = first.model_copy(
        update={
            "id": "mem-20260702-000001-exact-second",
            "created_at": datetime(2026, 7, 2, tzinfo=timezone.utc),
            "support_count": 0,
        }
    )
    other_tenant = first.model_copy(
        update={
            "id": "mem-20260703-000001-exact-other-tenant",
            "tenant_id": "other",
        }
    )
    for item in (first, second, other_tenant):
        store.write(item, "identical body")

    report = AutoGovernanceCycle(
        brain_dir=tmp_brain_dir,
        items_store=store,
        include_evolve=False,
        include_index=False,
        include_conversations=False,
    ).run(apply=True)

    action = next(
        action for action in report.actions
        if action.action == "supersede_exact_duplicate"
    )
    assert action.applied is True
    assert store.get(second.id)[0].superseded_by == first.id
    assert store.get(other_tenant.id)[0].superseded_by is None


def test_auto_governance_archives_only_explicit_ttl_expiration(
    tmp_brain_dir: Path,
) -> None:
    from agent_brain.memory.governance.auto_governance import AutoGovernanceCycle

    store = ItemsStore(tmp_brain_dir / "items")
    expired = MemoryItem(
        id="mem-20260701-000002-expired-ttl",
        type=MemoryType.handoff,
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        title="Expired handoff",
        summary="Explicitly expired transient state",
        validity={"observed_at": "2026-07-01T00:00:00+00:00", "ttl_hours": 24},
    )
    protected = expired.model_copy(
        update={
            "id": "mem-20260701-000003-protected-expired-ttl",
            "tags": ["blocker"],
        }
    )
    store.write(expired, "old state")
    store.write(protected, "protected state")

    report = AutoGovernanceCycle(
        brain_dir=tmp_brain_dir,
        items_store=store,
        now=datetime(2026, 7, 10, tzinfo=timezone.utc),
        include_evolve=False,
        include_index=False,
        include_conversations=False,
    ).run(apply=True)

    action = next(
        action for action in report.actions
        if action.action == "archive_expired_ttl"
    )
    assert action.applied is True
    assert not (store.items_dir / f"{expired.id}.md").exists()
    assert (store.items_dir / "archived" / f"{expired.id}.md").exists()
    assert (store.items_dir / f"{protected.id}.md").exists()


def test_auto_governance_contains_conflicts_once(tmp_brain_dir: Path) -> None:
    from agent_brain.memory.governance.auto_governance import AutoGovernanceCycle

    store = ItemsStore(tmp_brain_dir / "items")
    first = MemoryItem(
        id="mem-20260701-000004-redis-choice",
        type=MemoryType.decision,
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        title="Caching choice Redis",
        summary="Use Redis",
        project="p",
        tags=["caching"],
        confidence=0.8,
    )
    second = first.model_copy(
        update={
            "id": "mem-20260702-000004-memcached-choice",
            "created_at": datetime(2026, 7, 2, tzinfo=timezone.utc),
            "title": "Caching choice Memcached",
            "summary": "Use Memcached",
        }
    )
    store.write(first, "We decided to use Redis for caching.")
    store.write(second, "We chose Memcached instead of Redis for caching.")
    cycle = AutoGovernanceCycle(
        brain_dir=tmp_brain_dir,
        items_store=store,
        include_evolve=False,
        include_index=False,
        include_conversations=False,
    )

    first_run = cycle.run(apply=True)
    second_run = cycle.run(apply=True)

    assert any(
        action.action == "contain_conflict" and action.applied
        for action in first_run.actions
    )
    assert "contested" in store.get(first.id)[0].tags
    assert "needs-review" in store.get(second.id)[0].tags
    assert not any(action.action == "contain_conflict" for action in second_run.actions)


def test_daily_maintenance_runs_once_and_archives_explicit_ttl(
    tmp_brain_dir: Path,
) -> None:
    from agent_brain.memory.governance.daily_maintenance import run_daily_maintenance

    store = ItemsStore(tmp_brain_dir / "items")
    item = MemoryItem(
        id="mem-20260701-000005-daily-expired",
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc) - timedelta(days=3),
        title="Daily maintenance expired signal",
        summary="A short-lived signal with an explicit expired TTL",
        validity={
            "observed_at": datetime.now(timezone.utc) - timedelta(days=3),
            "ttl_hours": 1,
        },
    )
    store.write(item, "expired")

    first = run_daily_maintenance(tmp_brain_dir)
    second = run_daily_maintenance(tmp_brain_dir)

    assert first["status"] == "completed"
    assert first["applied_count"] >= 1
    assert second["status"] == "already_completed"
    assert not (tmp_brain_dir / "items" / f"{item.id}.md").exists()
    assert (tmp_brain_dir / "items" / "archived" / f"{item.id}.md").exists()
