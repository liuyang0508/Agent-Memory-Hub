from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_brain.contracts.conversation import ConversationMessageRecord, make_message_id
from agent_brain.contracts.resource import sha256_text


def test_conversation_store_ingests_transcript_messages_idempotently(tmp_brain: Path) -> None:
    from agent_brain.memory.evidence.conversation_store import ConversationStore

    transcript = Path("tests/fixtures/sample_transcript.jsonl")
    store = ConversationStore(tmp_brain)

    first = store.ingest_transcript(
        transcript,
        source_agent="claude-code",
        session_id="sess-raw",
        project="agent-memory-hub",
        cwd="/repo/agent-memory-hub",
    )
    second = store.ingest_transcript(
        transcript,
        source_agent="claude-code",
        session_id="sess-raw",
        project="agent-memory-hub",
        cwd="/repo/agent-memory-hub",
    )

    assert first.written == 3
    assert first.skipped == 0
    assert second.written == 0
    assert second.skipped == 3
    assert not list((tmp_brain / "items").glob("*.md"))

    messages = list(store.iter_messages(first.conversation_id))
    assert [message.role for message in messages] == ["user", "assistant", "assistant"]
    assert all(message.source_agent == "claude-code" for message in messages)
    assert all(message.session_id == "sess-raw" for message in messages)
    assert all(message.project == "agent-memory-hub" for message in messages)
    assert all(message.tier == "hot" for message in messages)
    assert all(message.content_sha256 for message in messages)
    assert messages[-1].source_offset_end == transcript.stat().st_size
    assert "mechanical-first" in messages[-1].content_text


def test_conversation_store_ingest_loads_existing_ids_once(
    tmp_brain: Path,
    monkeypatch,
) -> None:
    from agent_brain.memory.evidence.conversation_store import ConversationStore

    transcript = Path("tests/fixtures/sample_transcript.jsonl")
    store = ConversationStore(tmp_brain)
    calls = 0
    original = store._message_ids

    def counted(path: Path) -> set[str]:
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(store, "_message_ids", counted)

    result = store.ingest_transcript(
        transcript,
        source_agent="codex",
        session_id="sess-cache",
        project="agent-memory-hub",
    )

    assert result.written == 3
    assert calls == 1


def test_conversation_store_filters_messages_by_agent_and_project(tmp_brain: Path) -> None:
    from agent_brain.memory.evidence.conversation_store import ConversationStore

    transcript = Path("tests/fixtures/sample_transcript.jsonl")
    store = ConversationStore(tmp_brain)
    match = store.ingest_transcript(
        transcript,
        source_agent="codex",
        session_id="sess-codex",
        project="agent-memory-hub",
    )
    store.ingest_transcript(
        transcript,
        source_agent="claude-code",
        session_id="sess-claude",
        project="other",
    )

    matches = list(store.iter_conversations(source_agent="codex", project="agent-memory-hub"))

    assert [conversation.conversation_id for conversation in matches] == [match.conversation_id]
    assert matches[0].message_count == 3
    assert matches[0].source_agent == "codex"


def test_harvester_snapshots_raw_messages_before_extracting_items(tmp_brain: Path, tmp_path: Path) -> None:
    from agent_brain.memory.evidence.conversation_store import ConversationStore
    from agent_brain.memory.evidence.harvest.harvester import Harvester

    transcript_root = tmp_path / "projects" / "p1"
    transcript_root.mkdir(parents=True)
    transcript = transcript_root / "t.jsonl"
    transcript.write_text(Path("tests/fixtures/sample_transcript.jsonl").read_text(encoding="utf-8"), encoding="utf-8")

    first = Harvester(transcripts_root=tmp_path / "projects").run(enrich=False)
    second = Harvester(transcripts_root=tmp_path / "projects").run(enrich=False)

    assert first.raw_messages == 3
    assert second.raw_messages == 0
    messages = list(ConversationStore(tmp_brain).iter_messages())
    assert len(messages) == 3
    assert any("mechanical-first harvesting" in message.content_text for message in messages)


def _raw_message(
    *,
    conversation_id: str,
    text: str,
    observed_at: datetime,
    access_count: int = 0,
    half_life_days: int = 30,
    importance: float = 0.0,
) -> ConversationMessageRecord:
    return ConversationMessageRecord(
        id=make_message_id(conversation_id=conversation_id, role="user", content_text=text),
        conversation_id=conversation_id,
        source_agent="codex",
        session_id="sess-govern",
        role="user",
        content_text=text,
        content_sha256=sha256_text(text),
        observed_at=observed_at,
        retention={
            "access_count": access_count,
            "half_life_days": half_life_days,
            "importance": importance,
        },
    )


def test_conversation_retention_score_decays_and_access_reinforces() -> None:
    from agent_brain.memory.evidence.conversation_governance import retention_score

    now = datetime(2026, 6, 18, tzinfo=timezone.utc)
    old_unused = _raw_message(
        conversation_id="conv-1111111111111111-decay-test",
        text="old unused evidence",
        observed_at=now - timedelta(days=90),
    )
    old_reused = _raw_message(
        conversation_id="conv-1111111111111111-decay-test",
        text="old reused evidence",
        observed_at=now - timedelta(days=90),
        access_count=8,
        importance=0.8,
    )

    assert retention_score(old_unused, now=now) < 0.2
    assert retention_score(old_reused, now=now) > retention_score(old_unused, now=now)


def test_conversation_forgetting_curve_exposes_multi_axis_components() -> None:
    from agent_brain.memory.evidence.conversation_governance import forgetting_curve_score

    now = datetime(2026, 6, 18, tzinfo=timezone.utc)
    strong = _raw_message(
        conversation_id="conv-1111111111111111-curve-test",
        text="old but repeatedly useful evidence",
        observed_at=now - timedelta(days=90),
        access_count=8,
        importance=0.8,
    ).model_copy(update={
        "metadata": {
            "support_count": 4,
            "gain_score": 0.5,
            "noise_score": 0.0,
        }
    })
    noisy = _raw_message(
        conversation_id="conv-1111111111111111-curve-test",
        text="old noisy contradicted evidence",
        observed_at=now - timedelta(days=90),
        importance=0.2,
    ).model_copy(update={
        "metadata": {
            "contradict_count": 4,
            "noise_score": 0.8,
        }
    })

    strong_score = forgetting_curve_score(strong, now=now)
    noisy_score = forgetting_curve_score(noisy, now=now)

    assert strong_score.score > noisy_score.score
    assert strong_score.components["access_reinforcement"] > 0
    assert strong_score.components["evidence_reinforcement"] > 0
    assert noisy_score.components["noise_penalty"] > 0
    assert noisy_score.components["contradiction_penalty"] > 0


def test_conversation_store_touch_updates_access_metadata(tmp_brain: Path) -> None:
    from agent_brain.memory.evidence.conversation_store import ConversationStore

    now = datetime(2026, 6, 18, tzinfo=timezone.utc)
    store = ConversationStore(tmp_brain)
    conversation_id = "conv-2222222222222222-touch-test"
    assert store.write_message(_raw_message(
        conversation_id=conversation_id,
        text="touch me",
        observed_at=now - timedelta(days=10),
    ))

    touched = store.touch_conversation(conversation_id, now=now)

    assert touched == 1
    [message] = list(store.iter_messages(conversation_id))
    assert message.retention.access_count == 1
    assert message.retention.last_accessed == now


def test_conversation_store_touch_can_target_returned_messages_only(tmp_brain: Path) -> None:
    from agent_brain.memory.evidence.conversation_store import ConversationStore

    now = datetime(2026, 6, 18, tzinfo=timezone.utc)
    store = ConversationStore(tmp_brain)
    conversation_id = "conv-2222222222222222-touch-head"
    first = _raw_message(
        conversation_id=conversation_id,
        text="shown message",
        observed_at=now - timedelta(days=10),
    )
    second = _raw_message(
        conversation_id=conversation_id,
        text="unread tail",
        observed_at=now - timedelta(days=10),
    )
    assert store.write_message(first)
    assert store.write_message(second)

    touched = store.touch_conversation(conversation_id, message_ids=[first.id], now=now)

    assert touched == 1
    by_text = {message.content_text: message for message in store.iter_messages(conversation_id)}
    assert by_text["shown message"].retention.access_count == 1
    assert by_text["shown message"].retention.last_accessed == now
    assert by_text["unread tail"].retention.access_count == 0
    assert by_text["unread tail"].retention.last_accessed is None


def test_conversation_store_rebalances_hot_warm_cold_frozen_tiers(tmp_brain: Path) -> None:
    from agent_brain.memory.evidence.conversation_store import ConversationStore

    now = datetime(2026, 6, 18, tzinfo=timezone.utc)
    store = ConversationStore(tmp_brain)
    conversation_id = "conv-3333333333333333-tier-test"
    messages = [
        _raw_message(conversation_id=conversation_id, text="fresh", observed_at=now - timedelta(days=1)),
        _raw_message(conversation_id=conversation_id, text="warm", observed_at=now - timedelta(days=45), importance=0.5),
        _raw_message(conversation_id=conversation_id, text="cold", observed_at=now - timedelta(days=180)),
        _raw_message(conversation_id=conversation_id, text="frozen", observed_at=now - timedelta(days=420)),
    ]
    for message in messages:
        assert store.write_message(message)

    report = store.rebalance_tiers(now=now)

    assert report.distribution == {"hot": 1, "warm": 1, "cold": 1, "frozen": 1}
    by_text = {message.content_text: message for message in store.iter_messages(conversation_id)}
    assert by_text["fresh"].tier == "hot"
    assert by_text["warm"].tier == "warm"
    assert by_text["cold"].tier == "cold"
    assert by_text["frozen"].tier == "frozen"
