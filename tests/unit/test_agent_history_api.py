from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path / ".agent-memory-hub"))
    monkeypatch.setenv("MEMORY_HUB_RATE_LIMIT", "0")
    from web.app import app

    return TestClient(app)


def _admin_token(client: TestClient) -> str:
    resp = client.post("/api/auth/init", json={"username": "admin", "password": "test123"})
    assert resp.status_code == 200
    return resp.json()["token"]


def test_local_history_requires_auth(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    resp = client.get("/api/agents/local-history")

    assert resp.status_code == 401


def test_local_history_scan_returns_agents(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    token = _admin_token(client)

    resp = client.get("/api/agents/local-history", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["scope"] == "local"
    assert any(agent["agent"] == "codex" for agent in data["agents"])


def test_local_history_get_reuses_cached_scan_until_refresh(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    token = _admin_token(client)
    from web.api.routes import agent_history as route

    calls: list[Path] = []

    def fake_scan(*, brain_dir: Path):
        calls.append(Path(brain_dir))
        return {
            "generated_at": f"scan-{len(calls)}",
            "scope": "local",
            "total_sources": len(calls),
            "total_messages": 0,
            "agents": [
                {
                    "agent": "codex",
                    "source_count": len(calls),
                    "session_count": 0,
                    "message_count": 0,
                    "risk_flags": [],
                    "sources": [],
                }
            ],
        }

    monkeypatch.setattr(route, "scan_local_history_sources", fake_scan)
    route._clear_local_history_cache()

    first = client.get("/api/agents/local-history", headers={"Authorization": f"Bearer {token}"})
    second = client.get("/api/agents/local-history", headers={"Authorization": f"Bearer {token}"})
    refresh = client.post("/api/agents/local-history/scan", headers={"Authorization": f"Bearer {token}"})
    third = client.get("/api/agents/local-history", headers={"Authorization": f"Bearer {token}"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert refresh.status_code == 200
    assert third.status_code == 200
    assert first.json()["generated_at"] == "scan-1"
    assert second.json()["generated_at"] == "scan-1"
    assert refresh.json()["generated_at"] == "scan-2"
    assert third.json()["generated_at"] == "scan-2"
    assert len(calls) == 2


def test_local_history_sync_requires_admin(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    resp = client.post("/api/agents/codex/local-history/sync", json={"source_paths": []})

    assert resp.status_code == 401


def test_local_history_sync_creates_drafts(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    token = _admin_token(client)
    transcript = tmp_path / ".codex" / "sessions" / "rollout.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        json.dumps({"role": "user", "content": "事实：AMH 只扫描本机历史"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    resp = client.post(
        "/api/agents/codex/local-history/sync",
        headers={"Authorization": f"Bearer {token}"},
        json={"source_paths": [str(transcript)], "use_llm": False, "draft_limit": 10},
    )

    assert resp.status_code == 200
    assert resp.json()["drafts_created"] == 1

    drafts = client.get(
        "/api/agents/local-history/drafts",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert drafts.status_code == 200
    assert len(drafts.json()["drafts"]) == 1


def test_local_history_sync_reports_existing_drafts(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    token = _admin_token(client)
    transcript = tmp_path / ".codex" / "sessions" / "rollout.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        json.dumps({"role": "user", "content": "事实：重复同步不应重复建草稿"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    payload = {"source_paths": [str(transcript)], "use_llm": False, "draft_limit": 50}

    first = client.post(
        "/api/agents/codex/local-history/sync",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    second = client.post(
        "/api/agents/codex/local-history/sync",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["drafts_created"] == 1
    assert second.json()["drafts_created"] == 0
    assert second.json()["drafts_skipped"] == 1


def test_cursor_history_sync_creates_drafts_from_plan_and_composer_state(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    token = _admin_token(client)
    plan = tmp_path / ".cursor" / "plans" / "agent治理_abc123.plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("---\nname: Agent 治理\n---\n\n规划：接入 AMH。", encoding="utf-8")
    workspace_dir = tmp_path / "cursor-workspace"
    workspace_dir.mkdir()
    db = workspace_dir / "state.vscdb"
    with sqlite3.connect(db) as con:
        con.execute("create table ItemTable (key text primary key, value blob)")
        con.execute(
            "insert into ItemTable(key, value) values (?, ?)",
            (
                "composer.composerData",
                json.dumps({
                    "allComposers": [
                        {
                            "composerId": "c1",
                            "name": "修复历史同步",
                            "subtitle": "Root cause: Cursor stores local history in state.vscdb.",
                            "createdAt": 1,
                            "lastUpdatedAt": 2,
                        }
                    ]
                }, ensure_ascii=False),
            ),
        )

    resp = client.post(
        "/api/agents/cursor/local-history/sync",
        headers={"Authorization": f"Bearer {token}"},
        json={"source_paths": [str(plan), str(db)], "use_llm": False, "draft_limit": 10},
    )

    assert resp.status_code == 200
    assert resp.json()["drafts_created"] == 2


def test_claude_history_sync_creates_drafts_from_memory_plan_and_task(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    token = _admin_token(client)
    memory_file = tmp_path / ".claude" / "projects" / "-tmp-repo" / "memory" / "memory.md"
    memory_file.parent.mkdir(parents=True)
    memory_file.write_text("事实：Claude memory file should produce a draft.", encoding="utf-8")
    plan = tmp_path / ".claude" / "plans" / "sync-plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# Plan\n\n事实：Claude plan should produce a draft.", encoding="utf-8")
    task = tmp_path / ".claude" / "tasks" / "session" / "1.json"
    task.parent.mkdir(parents=True)
    task.write_text(
        json.dumps({
            "id": "1",
            "subject": "修复 Claude 同步",
            "description": "Root cause: markdown and task files were not parsed.",
            "status": "done",
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    resp = client.post(
        "/api/agents/claude_code/local-history/sync",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "source_paths": [str(memory_file), str(plan), str(task)],
            "use_llm": False,
            "draft_limit": 10,
        },
    )

    assert resp.status_code == 200
    assert resp.json()["drafts_created"] == 3


def test_claude_history_sync_processes_jsonl_in_batches(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    token = _admin_token(client)
    transcript = tmp_path / ".claude" / "projects" / "-tmp-repo" / "large.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        "".join(
            json.dumps({"role": "user", "content": f"事实：batch {index}"}, ensure_ascii=False) + "\n"
            for index in range(5)
        ),
        encoding="utf-8",
    )
    from agent_brain.product import history_sync

    original_ingest = history_sync._ingest_history_spans
    batch_lengths: list[int] = []

    def wrapped_ingest(*args, **kwargs):
        spans = list(args[2])
        batch_lengths.append(len(spans))
        return original_ingest(args[0], args[1], spans, **kwargs)

    monkeypatch.setattr(history_sync, "HISTORY_SYNC_SPAN_BATCH_SIZE", 2, raising=False)
    monkeypatch.setattr(history_sync, "_ingest_history_spans", wrapped_ingest)

    resp = client.post(
        "/api/agents/claude_code/local-history/sync",
        headers={"Authorization": f"Bearer {token}"},
        json={"source_paths": [str(transcript)], "use_llm": False, "draft_limit": 10},
    )

    assert resp.status_code == 200
    assert resp.json()["raw_messages"] == 5
    assert resp.json()["drafts_created"] == 5
    assert batch_lengths == [2, 2, 1]


def test_apply_draft_requires_admin_and_writes_item(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    token = _admin_token(client)
    transcript = tmp_path / ".codex" / "sessions" / "rollout.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        json.dumps({"role": "user", "content": "决策：保留人工审核"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    sync = client.post(
        "/api/agents/codex/local-history/sync",
        headers={"Authorization": f"Bearer {token}"},
        json={"source_paths": [str(transcript)], "use_llm": False, "draft_limit": 10},
    )
    assert sync.status_code == 200
    drafts = client.get("/api/agents/local-history/drafts", headers={"Authorization": f"Bearer {token}"}).json()
    draft_id = drafts["drafts"][0]["draft_id"]

    applied = client.post(
        f"/api/agents/local-history/drafts/{draft_id}/apply",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert applied.status_code == 200
    assert applied.json()["status"] == "applied"
