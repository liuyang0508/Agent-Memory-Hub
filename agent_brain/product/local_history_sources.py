"""Local Agent history source discovery for Web Admin history sync."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_brain.memory.evidence.harvest.transcript_reader import read_spans
from agent_brain.product.claude_history import read_claude_task_spans
from agent_brain.product.cursor_history import count_cursor_composers, cursor_workspace_project

DEFAULT_MAX_FILE_BYTES = 5 * 1024 * 1024
MESSAGE_COUNT_CACHE_VERSION = 1
SUPPORTED_LOCAL_AGENTS = ("codex", "claude_code", "cursor", "qoder", "qoder_work", "wukong")


@dataclass(frozen=True)
class LocalHistorySource:
    agent: str
    source_id: str
    source_type: str
    path: str
    project: str | None = None
    session_id: str | None = None
    session_count: int = 1
    message_count: int = 0
    size_bytes: int = 0
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    already_ingested: bool = False
    risk_flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _MessageCountCache:
    path: Path
    entries: dict[str, dict[str, int]]
    seen: set[str] = field(default_factory=set)
    dirty: bool = False

    @classmethod
    def load(cls, brain: Path) -> "_MessageCountCache":
        path = brain / "cache" / "local-history-message-counts.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls(path=path, entries={})
        if data.get("version") != MESSAGE_COUNT_CACHE_VERSION:
            return cls(path=path, entries={})
        entries = data.get("entries")
        return cls(path=path, entries=entries if isinstance(entries, dict) else {})

    def count(self, path: Path, stat: os.stat_result) -> int:
        key = str(path)
        self.seen.add(key)
        size_bytes = int(stat.st_size)
        mtime_ns = int(stat.st_mtime_ns)
        cached = self.entries.get(key)
        if (
            isinstance(cached, dict)
            and int(cached.get("size_bytes", -1)) == size_bytes
            and int(cached.get("mtime_ns", -1)) == mtime_ns
        ):
            return int(cached.get("message_count", 0))
        count = _count_messages(path)
        self.entries[key] = {
            "size_bytes": size_bytes,
            "mtime_ns": mtime_ns,
            "message_count": count,
        }
        self.dirty = True
        return count

    def save(self) -> None:
        pruned = {key: value for key, value in self.entries.items() if key in self.seen}
        if pruned != self.entries:
            self.entries = pruned
            self.dirty = True
        if not self.dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({
                "version": MESSAGE_COUNT_CACHE_VERSION,
                "entries": self.entries,
            }, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(self.path)


def scan_local_history_sources(
    *,
    home_dir: Path | None = None,
    brain_dir: Path | None = None,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> dict[str, Any]:
    home = Path(home_dir or Path.home())
    brain = Path(brain_dir or home / ".agent-memory-hub")
    ingested_index = _ingested_conversation_index(brain)
    count_cache = _MessageCountCache.load(brain)
    agents: list[dict[str, Any]] = []

    for agent in SUPPORTED_LOCAL_AGENTS:
        sources, risk_flags = _scan_agent(
            agent,
            home,
            brain,
            max_file_bytes=max_file_bytes,
            ingested_index=ingested_index,
            count_cache=count_cache,
        )
        agents.append({
            "agent": agent,
            "source_count": len(sources),
            "session_count": sum(source.session_count for source in sources),
            "message_count": sum(source.message_count for source in sources),
            "risk_flags": sorted(set(risk_flags)),
            "sources": [source.to_dict() for source in sources],
        })
    count_cache.save()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "local",
        "total_sources": sum(agent["source_count"] for agent in agents),
        "total_messages": sum(agent["message_count"] for agent in agents),
        "agents": agents,
    }


def _scan_agent(
    agent: str,
    home: Path,
    brain: Path,
    *,
    max_file_bytes: int,
    ingested_index: dict[str, list[str]],
    count_cache: _MessageCountCache,
) -> tuple[list[LocalHistorySource], list[str]]:
    if agent == "codex":
        return _scan_transcript_roots(
            agent,
            [home / ".codex" / "sessions"],
            brain,
            max_file_bytes=max_file_bytes,
            ingested_index=ingested_index,
            count_cache=count_cache,
        )
    if agent == "claude_code":
        transcript_sources, transcript_flags = _scan_transcript_roots(
            agent,
            [home / ".claude" / "projects"],
            brain,
            max_file_bytes=max_file_bytes,
            ingested_index=ingested_index,
            glob_pattern="**/*.jsonl",
            count_cache=count_cache,
        )
        memory_sources, memory_flags = _scan_claude_code_memory_files(
            home,
            brain,
            max_file_bytes=max_file_bytes,
            ingested_index=ingested_index,
        )
        plan_sources, plan_flags = _scan_claude_code_plan_files(
            home,
            brain,
            max_file_bytes=max_file_bytes,
            ingested_index=ingested_index,
        )
        task_sources, task_flags = _scan_claude_code_task_files(
            home,
            brain,
            max_file_bytes=max_file_bytes,
            ingested_index=ingested_index,
        )
        return (
            transcript_sources + memory_sources + plan_sources + task_sources,
            transcript_flags + memory_flags + plan_flags + task_flags,
        )
    if agent == "cursor":
        root = os.environ.get("MEMORY_HUB_CURSOR_HISTORY_ROOT")
        roots = [Path(root).expanduser()] if root else [home / ".cursor" / "sessions", home / ".cursor" / "projects"]
        transcript_sources, transcript_flags = _scan_transcript_roots(
            agent,
            roots,
            brain,
            max_file_bytes=max_file_bytes,
            ingested_index=ingested_index,
            count_cache=count_cache,
        )
        plan_sources, plan_flags = _scan_cursor_plan_files(
            home,
            brain,
            max_file_bytes=max_file_bytes,
            ingested_index=ingested_index,
        )
        state_sources, state_flags = _scan_cursor_composer_state(
            home,
            brain,
            max_file_bytes=max_file_bytes,
            ingested_index=ingested_index,
        )
        return (
            transcript_sources + plan_sources + state_sources,
            transcript_flags + plan_flags + state_flags,
        )
    if agent == "qoder":
        return _scan_transcript_roots(
            agent,
            [home / ".qoder" / "projects"],
            brain,
            max_file_bytes=max_file_bytes,
            ingested_index=ingested_index,
            count_cache=count_cache,
        )
    if agent == "qoder_work":
        return _scan_transcript_roots(
            agent,
            [home / ".qoderwork" / "projects", home / ".qoderwork" / "workspace"],
            brain,
            max_file_bytes=max_file_bytes,
            ingested_index=ingested_index,
            count_cache=count_cache,
        )
    if agent == "wukong":
        return _scan_wukong_sources(
            home,
            brain,
            max_file_bytes=max_file_bytes,
            ingested_index=ingested_index,
            count_cache=count_cache,
        )
    return [], []


def _scan_wukong_sources(
    home: Path,
    brain: Path,
    *,
    max_file_bytes: int,
    ingested_index: dict[str, list[str]],
    count_cache: _MessageCountCache,
) -> tuple[list[LocalHistorySource], list[str]]:
    sources: list[LocalHistorySource] = []
    risk_flags: list[str] = []
    override = os.environ.get("MEMORY_HUB_WUKONG_HISTORY_ROOT")
    override_roots = [Path(override).expanduser()] if override else []
    transcript_sources, transcript_flags = _scan_transcript_roots(
        "wukong",
        override_roots,
        brain,
        max_file_bytes=max_file_bytes,
        ingested_index=ingested_index,
        count_cache=count_cache,
    )
    sources.extend(transcript_sources)
    risk_flags.extend(transcript_flags)

    server_users = home / "Library" / "Application Support" / "dingtalk-rewind-server" / "users"
    if not server_users.exists():
        return sources, risk_flags
    for user_root in sorted(server_users.glob("user-*")):
        if not user_root.is_dir():
            continue
        brain_db = user_root / "memory" / "brain.db"
        count, flags = _count_sqlite_rows(brain_db, table="memories")
        risk_flags.extend(flags)
        if count > 0:
            sources.append(LocalHistorySource(
                agent="wukong",
                source_id=_source_id("wukong", brain_db),
                source_type="wukong_brain_db",
                path=str(brain_db),
                project=user_root.name,
                session_id="brain",
                message_count=count,
                size_bytes=_safe_size(brain_db),
                already_ingested=_already_ingested(ingested_index, "wukong", brain_db.stem),
            ))
        memory_index = user_root / "storage" / "memory" / "memory.sqlite"
        count, flags = _count_sqlite_rows(memory_index, table="memory_chunks")
        risk_flags.extend(flags)
        if count > 0:
            sources.append(LocalHistorySource(
                agent="wukong",
                source_id=_source_id("wukong", memory_index),
                source_type="wukong_memory_index",
                path=str(memory_index),
                project=user_root.name,
                session_id="memory",
                message_count=count,
                size_bytes=_safe_size(memory_index),
                already_ingested=_already_ingested(ingested_index, "wukong", memory_index.stem),
            ))
    return sources, risk_flags


def _scan_transcript_roots(
    agent: str,
    roots: list[Path],
    brain: Path,
    *,
    max_file_bytes: int,
    ingested_index: dict[str, list[str]],
    count_cache: _MessageCountCache,
    glob_pattern: str = "**/*.jsonl",
) -> tuple[list[LocalHistorySource], list[str]]:
    sources: list[LocalHistorySource] = []
    risk_flags: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.glob(glob_pattern)):
            if not path.is_file() or path.suffix.lower() != ".jsonl":
                continue
            try:
                stat = path.stat()
            except OSError:
                risk_flags.append("unreadable_file_skipped")
                continue
            if stat.st_size > max_file_bytes:
                risk_flags.append("large_transcript_streamed")
            count = count_cache.count(path, stat)
            if count <= 0:
                continue
            sources.append(LocalHistorySource(
                agent=agent,
                source_id=_source_id(agent, path),
                source_type="transcript_jsonl",
                path=str(path),
                project=path.parent.name,
                session_id=path.stem,
                message_count=count,
                size_bytes=stat.st_size,
                already_ingested=_already_ingested(ingested_index, agent, path.stem),
            ))
    return sources, risk_flags


def _scan_claude_code_memory_files(
    home: Path,
    brain: Path,
    *,
    max_file_bytes: int,
    ingested_index: dict[str, list[str]],
) -> tuple[list[LocalHistorySource], list[str]]:
    root = home / ".claude" / "projects"
    sources: list[LocalHistorySource] = []
    risk_flags: list[str] = []
    if not root.exists():
        return sources, risk_flags
    for path in sorted(root.glob("*/memory/*.md")):
        try:
            stat = path.stat()
        except OSError:
            risk_flags.append("unreadable_file_skipped")
            continue
        if stat.st_size > max_file_bytes:
            risk_flags.append("large_file_skipped")
            continue
        sources.append(LocalHistorySource(
            agent="claude_code",
            source_id=_source_id("claude_code", path),
            source_type="agent_memory_file",
            path=str(path),
            project=path.parent.parent.name,
            session_id=path.stem,
            message_count=1,
            size_bytes=stat.st_size,
            already_ingested=_already_ingested(ingested_index, "claude_code", path.stem),
        ))
    return sources, risk_flags


def _scan_claude_code_plan_files(
    home: Path,
    brain: Path,
    *,
    max_file_bytes: int,
    ingested_index: dict[str, list[str]],
) -> tuple[list[LocalHistorySource], list[str]]:
    root = home / ".claude" / "plans"
    sources: list[LocalHistorySource] = []
    risk_flags: list[str] = []
    if not root.exists():
        return sources, risk_flags
    for path in sorted(root.glob("*.md")):
        try:
            stat = path.stat()
        except OSError:
            risk_flags.append("unreadable_file_skipped")
            continue
        if stat.st_size > max_file_bytes:
            risk_flags.append("large_file_skipped")
            continue
        if stat.st_size <= 0:
            continue
        sources.append(LocalHistorySource(
            agent="claude_code",
            source_id=_source_id("claude_code", path),
            source_type="claude_plan_file",
            path=str(path),
            project="plans",
            session_id=path.stem,
            message_count=1,
            size_bytes=stat.st_size,
            already_ingested=_already_ingested(ingested_index, "claude_code", path.stem),
        ))
    return sources, risk_flags


def _scan_claude_code_task_files(
    home: Path,
    brain: Path,
    *,
    max_file_bytes: int,
    ingested_index: dict[str, list[str]],
) -> tuple[list[LocalHistorySource], list[str]]:
    root = home / ".claude" / "tasks"
    sources: list[LocalHistorySource] = []
    risk_flags: list[str] = []
    if not root.exists():
        return sources, risk_flags
    for path in sorted(root.glob("*/*.json")):
        try:
            stat = path.stat()
        except OSError:
            risk_flags.append("unreadable_file_skipped")
            continue
        if stat.st_size > max_file_bytes:
            risk_flags.append("large_file_skipped")
            continue
        if not list(read_claude_task_spans(path)):
            continue
        session_id = f"{path.parent.name}-{path.stem}"
        sources.append(LocalHistorySource(
            agent="claude_code",
            source_id=_source_id("claude_code", path),
            source_type="claude_task_file",
            path=str(path),
            project=path.parent.name,
            session_id=session_id,
            message_count=1,
            size_bytes=stat.st_size,
            already_ingested=_already_ingested(ingested_index, "claude_code", session_id),
        ))
    return sources, risk_flags


def _scan_cursor_plan_files(
    home: Path,
    brain: Path,
    *,
    max_file_bytes: int,
    ingested_index: dict[str, list[str]],
) -> tuple[list[LocalHistorySource], list[str]]:
    root = home / ".cursor" / "plans"
    sources: list[LocalHistorySource] = []
    risk_flags: list[str] = []
    if not root.exists():
        return sources, risk_flags
    for path in sorted(root.glob("*.plan.md")):
        try:
            stat = path.stat()
        except OSError:
            risk_flags.append("unreadable_file_skipped")
            continue
        if stat.st_size > max_file_bytes:
            risk_flags.append("large_file_skipped")
            continue
        if stat.st_size <= 0:
            continue
        sources.append(LocalHistorySource(
            agent="cursor",
            source_id=_source_id("cursor", path),
            source_type="cursor_plan_file",
            path=str(path),
            project="plans",
            session_id=path.stem,
            message_count=1,
            size_bytes=stat.st_size,
            already_ingested=_already_ingested(ingested_index, "cursor", path.stem),
        ))
    return sources, risk_flags


def _scan_cursor_composer_state(
    home: Path,
    brain: Path,
    *,
    max_file_bytes: int,
    ingested_index: dict[str, list[str]],
) -> tuple[list[LocalHistorySource], list[str]]:
    root = home / "Library" / "Application Support" / "Cursor" / "User" / "workspaceStorage"
    sources: list[LocalHistorySource] = []
    risk_flags: list[str] = []
    if not root.exists():
        return sources, risk_flags
    for path in sorted(root.glob("*/state.vscdb")):
        try:
            stat = path.stat()
        except OSError:
            risk_flags.append("unreadable_file_skipped")
            continue
        if stat.st_size > max_file_bytes:
            risk_flags.append("large_file_skipped")
            continue
        count = count_cursor_composers(path)
        if count <= 0:
            continue
        sources.append(LocalHistorySource(
            agent="cursor",
            source_id=_source_id("cursor", path),
            source_type="cursor_composer_state",
            path=str(path),
            project=cursor_workspace_project(path.parent) or path.parent.name,
            session_id=path.parent.name,
            message_count=count,
            size_bytes=stat.st_size,
            already_ingested=_already_ingested(ingested_index, "cursor", path.parent.name),
        ))
    return sources, risk_flags


def _count_messages(path: Path) -> int:
    try:
        return sum(1 for _span in read_spans(path))
    except OSError:
        return 0


def _count_sqlite_rows(path: Path, *, table: str) -> tuple[int, list[str]]:
    if not path.is_file():
        return 0, []
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            exists = conn.execute(
                "select 1 from sqlite_master where type='table' and name=?",
                (table,),
            ).fetchone()
            if not exists:
                return 0, []
            row = conn.execute(f"select count(*) from {table}").fetchone()
            return int(row[0] if row else 0), []
    except sqlite3.Error:
        return 0, ["unreadable_file_skipped"]


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _source_id(agent: str, path: Path) -> str:
    digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
    return f"{agent}:{digest}"


def _ingested_conversation_index(brain: Path) -> dict[str, list[str]]:
    conversations = brain / "sources" / "conversations"
    index: dict[str, list[str]] = {agent: [] for agent in SUPPORTED_LOCAL_AGENTS}
    if not conversations.exists():
        return index
    for path in conversations.glob("*/messages.jsonl"):
        agent = _conversation_agent(path.parent.name)
        if not agent:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        index.setdefault(agent, []).append(text)
    return index


def _conversation_agent(conversation_id: str) -> str | None:
    for agent in SUPPORTED_LOCAL_AGENTS:
        prefix = f"conv-{agent.replace('_', '-')}"
        if conversation_id.startswith(prefix):
            return agent
    return None


def _already_ingested(index: dict[str, list[str]], agent: str, session_id: str) -> bool:
    return any(session_id in text for text in index.get(agent, []))
