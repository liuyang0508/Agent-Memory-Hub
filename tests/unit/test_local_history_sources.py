from __future__ import annotations

import json
import sqlite3
from pathlib import Path


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_scan_codex_sessions_counts_messages(tmp_path: Path) -> None:
    from agent_brain.product.local_history_sources import scan_local_history_sources

    sessions = tmp_path / ".codex" / "sessions" / "2026" / "07" / "03"
    _write_jsonl(
        sessions / "rollout-abc.jsonl",
        [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "记住偏好"}]},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "已记录"}]},
        ],
    )

    report = scan_local_history_sources(home_dir=tmp_path, brain_dir=tmp_path / ".agent-memory-hub")

    codex = next(agent for agent in report["agents"] if agent["agent"] == "codex")
    assert codex["source_count"] == 1
    assert codex["message_count"] == 2
    assert codex["sources"][0]["source_type"] == "transcript_jsonl"
    assert codex["sources"][0]["session_count"] == 1


def test_scan_indexes_ingested_conversations_once(tmp_path: Path, monkeypatch) -> None:
    from agent_brain.product.local_history_sources import scan_local_history_sources

    sessions = tmp_path / ".codex" / "sessions"
    for name in ("session-a", "session-b", "session-c"):
        _write_jsonl(sessions / f"{name}.jsonl", [{"role": "user", "content": name}])

    brain = tmp_path / ".agent-memory-hub"
    _write_jsonl(
        brain / "sources" / "conversations" / "conv-codex-001" / "messages.jsonl",
        [{"session_id": "session-a", "content": "already ingested"}],
    )
    _write_jsonl(
        brain / "sources" / "conversations" / "conv-qoder-001" / "messages.jsonl",
        [{"session_id": "qoder-session", "content": "other agent"}],
    )

    original_read_text = Path.read_text
    reads: list[Path] = []

    def counting_read_text(self: Path, *args, **kwargs):
        if self.name == "messages.jsonl":
            reads.append(self)
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)

    report = scan_local_history_sources(home_dir=tmp_path, brain_dir=brain)

    codex = next(agent for agent in report["agents"] if agent["agent"] == "codex")
    sources = {Path(source["path"]).stem: source["already_ingested"] for source in codex["sources"]}
    assert sources == {"session-a": True, "session-b": False, "session-c": False}
    assert len(reads) == 2


def test_scan_claude_code_memory_files_are_separate_source_type(tmp_path: Path) -> None:
    from agent_brain.product.local_history_sources import scan_local_history_sources

    project = tmp_path / ".claude" / "projects" / "-tmp-repo"
    _write_jsonl(project / "session.jsonl", [{"message": {"role": "user", "content": "alpha"}}])
    memory_dir = project / "memory"
    memory_dir.mkdir()
    (memory_dir / "memory.md").write_text("# Project memory\n\nUse pytest.", encoding="utf-8")

    report = scan_local_history_sources(home_dir=tmp_path, brain_dir=tmp_path / ".agent-memory-hub")
    claude = next(agent for agent in report["agents"] if agent["agent"] == "claude_code")

    source_types = {source["source_type"] for source in claude["sources"]}
    assert source_types == {"transcript_jsonl", "agent_memory_file"}
    assert claude["source_count"] == 2


def test_scan_claude_code_nested_transcripts_plans_and_tasks(tmp_path: Path) -> None:
    from agent_brain.product.local_history_sources import scan_local_history_sources

    project = tmp_path / ".claude" / "projects" / "-tmp-repo"
    _write_jsonl(project / "session.jsonl", [{"role": "user", "content": "top level"}])
    _write_jsonl(project / "session" / "subagents" / "worker.jsonl", [
        {"role": "assistant", "content": "nested worker"},
    ])
    plan = tmp_path / ".claude" / "plans" / "careful-plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# Plan\n\n事实：Claude plan should sync.", encoding="utf-8")
    task = tmp_path / ".claude" / "tasks" / "session" / "1.json"
    task.parent.mkdir(parents=True)
    task.write_text(
        json.dumps({
            "id": "1",
            "subject": "修复 Claude 同步",
            "description": "Root cause: nested transcripts were not scanned.",
            "status": "done",
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    report = scan_local_history_sources(home_dir=tmp_path, brain_dir=tmp_path / ".agent-memory-hub")

    claude = next(agent for agent in report["agents"] if agent["agent"] == "claude_code")
    source_types = {source["source_type"] for source in claude["sources"]}
    assert {"transcript_jsonl", "claude_plan_file", "claude_task_file"} <= source_types
    assert claude["source_count"] == 4
    assert claude["message_count"] == 4


def test_scan_claude_code_streams_large_jsonl_transcripts(tmp_path: Path) -> None:
    from agent_brain.product.local_history_sources import scan_local_history_sources

    transcript = tmp_path / ".claude" / "projects" / "-tmp-repo" / "large.jsonl"
    _write_jsonl(transcript, [
        {"role": "user", "content": "事实：" + "x" * 80},
        {"role": "assistant", "content": "修复：" + "y" * 80},
    ])

    report = scan_local_history_sources(
        home_dir=tmp_path,
        brain_dir=tmp_path / ".agent-memory-hub",
        max_file_bytes=32,
    )

    claude = next(agent for agent in report["agents"] if agent["agent"] == "claude_code")
    assert claude["source_count"] == 1
    assert claude["message_count"] == 2
    assert claude["risk_flags"] == ["large_transcript_streamed"]


def test_scan_reuses_cached_jsonl_message_counts(tmp_path: Path, monkeypatch) -> None:
    from agent_brain.product import local_history_sources

    transcript = tmp_path / ".codex" / "sessions" / "rollout.jsonl"
    _write_jsonl(transcript, [
        {"role": "user", "content": "事实：缓存本机历史计数"},
        {"role": "assistant", "content": "修复：第二次扫描不应重读 JSONL"},
    ])
    brain = tmp_path / ".agent-memory-hub"

    first = local_history_sources.scan_local_history_sources(home_dir=tmp_path, brain_dir=brain)
    codex = next(agent for agent in first["agents"] if agent["agent"] == "codex")
    assert codex["message_count"] == 2

    def fail_read_spans(*_args, **_kwargs):
        raise AssertionError("cached transcript count should avoid read_spans")

    monkeypatch.setattr(local_history_sources, "read_spans", fail_read_spans)
    second = local_history_sources.scan_local_history_sources(home_dir=tmp_path, brain_dir=brain)
    codex = next(agent for agent in second["agents"] if agent["agent"] == "codex")
    assert codex["message_count"] == 2


def test_scan_missing_paths_returns_empty_agents(tmp_path: Path) -> None:
    from agent_brain.product.local_history_sources import scan_local_history_sources

    report = scan_local_history_sources(home_dir=tmp_path, brain_dir=tmp_path / ".agent-memory-hub")

    assert report["total_sources"] == 0
    assert {agent["agent"] for agent in report["agents"]} >= {"codex", "claude_code", "qoder", "qoder_work", "wukong"}


def test_scan_includes_cursor_agent_even_without_sources(tmp_path: Path) -> None:
    from agent_brain.product.local_history_sources import scan_local_history_sources

    report = scan_local_history_sources(home_dir=tmp_path, brain_dir=tmp_path / ".agent-memory-hub")

    cursor = next(agent for agent in report["agents"] if agent["agent"] == "cursor")
    assert cursor["source_count"] == 0
    assert cursor["message_count"] == 0


def test_cursor_can_scan_configured_history_root(tmp_path: Path, monkeypatch) -> None:
    from agent_brain.product.local_history_sources import scan_local_history_sources

    root = tmp_path / "cursor-history"
    _write_jsonl(root / "session.jsonl", [{"role": "user", "content": "cursor local history"}])
    monkeypatch.setenv("MEMORY_HUB_CURSOR_HISTORY_ROOT", str(root))

    report = scan_local_history_sources(home_dir=tmp_path, brain_dir=tmp_path / ".agent-memory-hub")

    cursor = next(agent for agent in report["agents"] if agent["agent"] == "cursor")
    assert cursor["source_count"] == 1
    assert cursor["message_count"] == 1


def test_cursor_scans_local_plan_files_and_composer_state(tmp_path: Path) -> None:
    from agent_brain.product.local_history_sources import scan_local_history_sources

    plan = tmp_path / ".cursor" / "plans" / "agent治理_abc123.plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("---\nname: Agent 治理\n---\n\n规划：接入 AMH。", encoding="utf-8")
    workspace_dir = (
        tmp_path
        / "Library"
        / "Application Support"
        / "Cursor"
        / "User"
        / "workspaceStorage"
        / "workspace-1"
    )
    workspace_dir.mkdir(parents=True)
    (workspace_dir / "workspace.json").write_text(
        json.dumps({"folder": "file:///tmp/agent-memory-hub"}), encoding="utf-8"
    )
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
                            "totalLinesAdded": 3,
                            "totalLinesRemoved": 1,
                        }
                    ]
                }, ensure_ascii=False),
            ),
        )

    report = scan_local_history_sources(home_dir=tmp_path, brain_dir=tmp_path / ".agent-memory-hub")

    cursor = next(agent for agent in report["agents"] if agent["agent"] == "cursor")
    source_types = {source["source_type"] for source in cursor["sources"]}
    assert {"cursor_plan_file", "cursor_composer_state"} <= source_types
    assert cursor["source_count"] == 2
    assert cursor["message_count"] == 2


def test_scan_ignores_non_jsonl_and_streams_large_invalid_transcripts(tmp_path: Path) -> None:
    from agent_brain.product.local_history_sources import scan_local_history_sources

    root = tmp_path / ".codex" / "sessions"
    root.mkdir(parents=True)
    (root / "notes.txt").write_text("not a transcript", encoding="utf-8")
    large = root / "large.jsonl"
    large.write_bytes(b"x" * (6 * 1024 * 1024))

    report = scan_local_history_sources(
        home_dir=tmp_path,
        brain_dir=tmp_path / ".agent-memory-hub",
        max_file_bytes=1024,
    )
    codex = next(agent for agent in report["agents"] if agent["agent"] == "codex")

    assert codex["source_count"] == 0
    assert codex["risk_flags"] == ["large_transcript_streamed"]


def test_scan_qoder_and_qoder_work_jsonl_sources(tmp_path: Path) -> None:
    from agent_brain.product.local_history_sources import scan_local_history_sources

    _write_jsonl(tmp_path / ".qoder" / "projects" / "alpha" / "session.jsonl", [
        {"role": "user", "content": "qoder memory"},
    ])
    _write_jsonl(tmp_path / ".qoderwork" / "workspace" / "task" / "session.jsonl", [
        {"role": "assistant", "content": "qoderwork memory"},
    ])

    report = scan_local_history_sources(home_dir=tmp_path, brain_dir=tmp_path / ".agent-memory-hub")

    qoder = next(agent for agent in report["agents"] if agent["agent"] == "qoder")
    qoder_work = next(agent for agent in report["agents"] if agent["agent"] == "qoder_work")
    assert qoder["source_count"] == 1
    assert qoder_work["source_count"] == 1


def test_wukong_can_scan_configured_history_root(tmp_path: Path, monkeypatch) -> None:
    from agent_brain.product.local_history_sources import scan_local_history_sources

    root = tmp_path / "wukong-history"
    _write_jsonl(root / "session.jsonl", [{"role": "user", "content": "wukong local history"}])
    monkeypatch.setenv("MEMORY_HUB_WUKONG_HISTORY_ROOT", str(root))

    report = scan_local_history_sources(home_dir=tmp_path, brain_dir=tmp_path / ".agent-memory-hub")

    wukong = next(agent for agent in report["agents"] if agent["agent"] == "wukong")
    assert wukong["source_count"] == 1
    assert wukong["message_count"] == 1


def test_wukong_auto_discovers_local_memory_databases(tmp_path: Path, monkeypatch) -> None:
    import sqlite3

    from agent_brain.product.local_history_sources import scan_local_history_sources

    monkeypatch.delenv("MEMORY_HUB_WUKONG_HISTORY_ROOT", raising=False)
    user_root = (
        tmp_path
        / "Library"
        / "Application Support"
        / "dingtalk-rewind-server"
        / "users"
        / "user-local"
    )
    brain_db = user_root / "memory" / "brain.db"
    brain_db.parent.mkdir(parents=True)
    with sqlite3.connect(brain_db) as conn:
        conn.execute("create table memories (id text, key text, content text)")
        conn.execute(
            "insert into memories values (?, ?, ?)",
            ("mem-1", "shipping boundary", "事实：Wukong 本机记忆可自动发现"),
        )
    memory_index = user_root / "storage" / "memory" / "memory.sqlite"
    memory_index.parent.mkdir(parents=True)
    with sqlite3.connect(memory_index) as conn:
        conn.execute("create table memory_chunks (id text, path text, text text)")
        conn.execute(
            "insert into memory_chunks values (?, ?, ?)",
            ("chunk-1", "notes.md", "决策：历史同步从后管生成草稿"),
        )

    report = scan_local_history_sources(home_dir=tmp_path, brain_dir=tmp_path / ".agent-memory-hub")

    wukong = next(agent for agent in report["agents"] if agent["agent"] == "wukong")
    source_types = {source["source_type"] for source in wukong["sources"]}
    assert source_types == {"wukong_brain_db", "wukong_memory_index"}
    assert wukong["source_count"] == 2
    assert wukong["message_count"] == 2
