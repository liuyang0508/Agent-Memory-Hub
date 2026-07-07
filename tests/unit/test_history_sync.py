from __future__ import annotations

import json
from pathlib import Path


def test_history_sync_ingests_raw_and_creates_drafts(tmp_path: Path, monkeypatch) -> None:
    from agent_brain.memory.evidence.conversation_store import ConversationStore
    from agent_brain.product.history_sync import HistorySyncRequest, run_history_sync
    from agent_brain.product.memory_drafts import DraftStore

    monkeypatch.setenv("BRAIN_DIR", str(tmp_path / ".agent-memory-hub"))
    transcript = tmp_path / ".codex" / "sessions" / "rollout.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        json.dumps({"role": "user", "content": "决定：本机历史同步不接云端 API"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    result = run_history_sync(
        tmp_path / ".agent-memory-hub",
        HistorySyncRequest(agent="codex", source_paths=[str(transcript)], use_llm=False, draft_limit=10),
    )

    assert result["status"] == "awaiting_review"
    assert result["raw_messages"] == 1
    assert result["drafts_created"] == 1
    conversations = list(ConversationStore(tmp_path / ".agent-memory-hub").iter_conversations(source_agent="codex"))
    assert len(conversations) == 1
    drafts = DraftStore(tmp_path / ".agent-memory-hub").list()
    assert drafts[0].source_agent == "codex"
    assert drafts[0].generation_mode == "mechanical"


def test_history_sync_respects_draft_limit(tmp_path: Path, monkeypatch) -> None:
    from agent_brain.product.history_sync import HistorySyncRequest, run_history_sync
    from agent_brain.product.memory_drafts import DraftStore

    monkeypatch.setenv("BRAIN_DIR", str(tmp_path / ".agent-memory-hub"))
    transcript = tmp_path / ".codex" / "sessions" / "rollout.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        "".join(
            json.dumps({"role": "user", "content": f"决定：第 {idx} 条"}, ensure_ascii=False) + "\n"
            for idx in range(5)
        ),
        encoding="utf-8",
    )

    result = run_history_sync(
        tmp_path / ".agent-memory-hub",
        HistorySyncRequest(agent="codex", source_paths=[str(transcript)], use_llm=False, draft_limit=2),
    )

    assert result["drafts_created"] == 2
    assert len(DraftStore(tmp_path / ".agent-memory-hub").list()) == 2


def test_history_sync_is_idempotent_for_existing_drafts(tmp_path: Path, monkeypatch) -> None:
    from agent_brain.product.history_sync import HistorySyncRequest, run_history_sync
    from agent_brain.product.memory_drafts import DraftStore

    monkeypatch.setenv("BRAIN_DIR", str(tmp_path / ".agent-memory-hub"))
    transcript = tmp_path / ".codex" / "sessions" / "rollout.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        "".join(
            json.dumps({"role": "user", "content": f"事实：第 {idx} 条"}, ensure_ascii=False) + "\n"
            for idx in range(5)
        ),
        encoding="utf-8",
    )
    request = HistorySyncRequest(
        agent="codex",
        source_paths=[str(transcript)],
        use_llm=False,
        draft_limit=50,
    )

    first = run_history_sync(tmp_path / ".agent-memory-hub", request)
    second = run_history_sync(tmp_path / ".agent-memory-hub", request)

    assert first["drafts_created"] == 5
    assert first["drafts_skipped"] == 0
    assert second["drafts_created"] == 0
    assert second["drafts_skipped"] == 5
    assert len(DraftStore(tmp_path / ".agent-memory-hub").list()) == 5


def test_history_sync_reports_llm_disabled_when_no_model(tmp_path: Path, monkeypatch) -> None:
    from agent_brain.product.history_sync import HistorySyncRequest, run_history_sync

    monkeypatch.setenv("BRAIN_DIR", str(tmp_path / ".agent-memory-hub"))
    monkeypatch.setenv("MEMORY_HUB_NO_MODEL", "1")
    transcript = tmp_path / ".codex" / "sessions" / "rollout.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        json.dumps({"role": "user", "content": "事实：没有 LLM 也要能生成草稿"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    result = run_history_sync(
        tmp_path / ".agent-memory-hub",
        HistorySyncRequest(agent="codex", source_paths=[str(transcript)], use_llm=True, draft_limit=10),
    )

    assert result["generation_mode"] == "mechanical"
    assert "llm_unavailable" in result["risk_flags"]


def test_history_sync_creates_drafts_from_wukong_brain_db(tmp_path: Path, monkeypatch) -> None:
    import sqlite3

    from agent_brain.product.history_sync import HistorySyncRequest, run_history_sync
    from agent_brain.product.memory_drafts import DraftStore

    monkeypatch.setenv("BRAIN_DIR", str(tmp_path / ".agent-memory-hub"))
    brain_db = tmp_path / "wukong" / "memory" / "brain.db"
    brain_db.parent.mkdir(parents=True)
    with sqlite3.connect(brain_db) as conn:
        conn.execute("create table memories (id text, key text, content text, category text, source text)")
        conn.execute(
            "insert into memories values (?, ?, ?, ?, ?)",
            ("mem-1", "README history sync", "事实：在后管 Agent 管理页生成草稿", "fact", "wukong"),
        )

    result = run_history_sync(
        tmp_path / ".agent-memory-hub",
        HistorySyncRequest(agent="wukong", source_paths=[str(brain_db)], use_llm=False, draft_limit=10),
    )

    assert result["status"] == "awaiting_review"
    assert result["raw_messages"] == 1
    assert result["drafts_created"] == 1
    drafts = DraftStore(tmp_path / ".agent-memory-hub").list()
    assert drafts[0].source_agent == "wukong"
    assert drafts[0].title == "README history sync"
    assert "wukong-brain-db" in drafts[0].tags
