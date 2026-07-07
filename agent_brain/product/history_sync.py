"""Local Agent history sync job orchestration."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from agent_brain.contracts.conversation import (
    ConversationMessageRecord,
    make_conversation_id,
    make_message_id,
)
from agent_brain.contracts.memory_item import MemoryType
from agent_brain.contracts.resource import sha256_text
from agent_brain.memory.evidence.conversation_store import ConversationIngestResult, ConversationStore
from agent_brain.memory.evidence.harvest.dedup import span_hash
from agent_brain.memory.evidence.harvest.extractor import Candidate, extract_candidates
from agent_brain.memory.evidence.harvest.transcript_reader import TranscriptSpan, read_spans
from agent_brain.product.claude_history import read_claude_markdown_spans, read_claude_task_spans
from agent_brain.product.cursor_history import read_cursor_composer_spans, read_cursor_plan_spans
from agent_brain.product.memory_drafts import DraftStore, MemoryDraftInput

HISTORY_SYNC_SPAN_BATCH_SIZE = 500


@dataclass(frozen=True)
class HistorySyncRequest:
    agent: str
    source_paths: list[str]
    use_llm: bool = False
    draft_limit: int = 50


def run_history_sync(brain_dir: Path, request: HistorySyncRequest) -> dict[str, Any]:
    job_id = f"job-{uuid.uuid4().hex[:16]}"
    brain = Path(brain_dir)
    draft_store = DraftStore(brain)
    conversation_store = ConversationStore(brain)
    llm_available = _llm_available()
    generation_mode = "llm" if request.use_llm and llm_available else "mechanical"
    risk_flags: list[str] = []
    if request.use_llm and not llm_available:
        risk_flags.append("llm_unavailable")
    raw_messages = 0
    drafts_created = 0
    drafts_skipped = 0
    draft_source_keys = draft_store.source_keys()
    source_results: list[dict[str, Any]] = []

    for source_path in request.source_paths:
        if drafts_created >= request.draft_limit:
            break
        path = Path(source_path).expanduser()
        if not path.exists() or not path.is_file():
            source_results.append({"path": str(path), "status": "skipped", "reason": "missing_file"})
            continue
        if path.suffix.lower() in {".db", ".sqlite"}:
            sqlite_result = _sync_sqlite_memory_source(
                draft_store,
                request=request,
                path=path,
                generation_mode=generation_mode,
                risk_flags=risk_flags,
                remaining=max(request.draft_limit - drafts_created, 0),
                draft_source_keys=draft_source_keys,
            )
            raw_messages += sqlite_result["raw_messages"]
            drafts_created += sqlite_result["drafts_created"]
            drafts_skipped += sqlite_result["drafts_skipped"]
            source_results.append({
                "path": str(path),
                "status": sqlite_result["status"],
                "raw_written": sqlite_result["raw_messages"],
                "raw_skipped": 0,
                "drafts_created": sqlite_result["drafts_created"],
                "drafts_skipped": sqlite_result["drafts_skipped"],
            })
            continue
        source_written = 0
        source_skipped = 0
        source_drafts_created = 0
        source_drafts_skipped = 0
        source_path = str(path)
        for spans in _span_batches(_read_history_spans(path, request.agent), HISTORY_SYNC_SPAN_BATCH_SIZE):
            ingest = _ingest_history_spans(
                conversation_store,
                path,
                spans,
                source_agent=request.agent,
                session_id=_session_id(path, request.agent),
                project=_project_name(path, request.agent),
                tags=["history-sync", request.agent],
            )
            raw_messages += ingest.written
            source_written += ingest.written
            source_skipped += ingest.skipped
            if drafts_created >= request.draft_limit:
                continue
            for candidate in _extract_history_candidates(spans, request.agent):
                if drafts_created >= request.draft_limit:
                    break
                source_key = (request.agent, source_path, candidate.span_hash)
                if source_key in draft_source_keys:
                    drafts_skipped += 1
                    source_drafts_skipped += 1
                    continue
                draft_store.create(MemoryDraftInput(
                    title=candidate.title,
                    summary=candidate.summary,
                    body=candidate.body,
                    type=MemoryType(candidate.type).value,
                    tags=list(dict.fromkeys([*candidate.tags, "history-sync", request.agent])),
                    source_agent=request.agent,
                    source_refs={
                        "conversation_id": ingest.conversation_id,
                        "source_path": source_path,
                        "span_hash": candidate.span_hash,
                    },
                    generation_mode=generation_mode,
                    risk_flags=list(risk_flags),
                ))
                draft_source_keys.add(source_key)
                drafts_created += 1
                source_drafts_created += 1
        source_results.append({
            "path": str(path),
            "status": "processed",
            "raw_written": source_written,
            "raw_skipped": source_skipped,
            "drafts_created": source_drafts_created,
            "drafts_skipped": source_drafts_skipped,
        })

    return {
        "job_id": job_id,
        "status": "awaiting_review",
        "agent": request.agent,
        "raw_messages": raw_messages,
        "drafts_created": drafts_created,
        "drafts_skipped": drafts_skipped,
        "use_llm": request.use_llm,
        "generation_mode": generation_mode,
        "risk_flags": risk_flags,
        "sources": source_results,
    }


def _llm_available() -> bool:
    import os

    if os.environ.get("MEMORY_HUB_NO_MODEL") == "1":
        return False
    try:
        import litellm  # noqa: F401
        return True
    except ImportError:
        return False


def _sync_sqlite_memory_source(
    draft_store: DraftStore,
    *,
    request: HistorySyncRequest,
    path: Path,
    generation_mode: str,
    risk_flags: list[str],
    remaining: int,
    draft_source_keys: set[tuple[str, str, str]],
) -> dict[str, Any]:
    if remaining <= 0:
        return {"status": "skipped", "raw_messages": 0, "drafts_created": 0, "drafts_skipped": 0}
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            if _sqlite_has_table(conn, "memories"):
                return _sync_wukong_brain_db(
                    conn,
                    draft_store,
                    request=request,
                    path=path,
                    generation_mode=generation_mode,
                    risk_flags=risk_flags,
                    remaining=remaining,
                    draft_source_keys=draft_source_keys,
                )
            if _sqlite_has_table(conn, "memory_chunks"):
                return _sync_wukong_memory_index(
                    conn,
                    draft_store,
                    request=request,
                    path=path,
                    generation_mode=generation_mode,
                    risk_flags=risk_flags,
                    remaining=remaining,
                    draft_source_keys=draft_source_keys,
                )
    except sqlite3.Error as exc:
        return {
            "status": "skipped",
            "raw_messages": 0,
            "drafts_created": 0,
            "drafts_skipped": 0,
            "reason": f"sqlite_error:{exc}",
        }
    return {
        "status": "skipped",
        "raw_messages": 0,
        "drafts_created": 0,
        "drafts_skipped": 0,
        "reason": "unsupported_sqlite_schema",
    }


def _sync_wukong_brain_db(
    conn: sqlite3.Connection,
    draft_store: DraftStore,
    *,
    request: HistorySyncRequest,
    path: Path,
    generation_mode: str,
    risk_flags: list[str],
    remaining: int,
    draft_source_keys: set[tuple[str, str, str]],
) -> dict[str, Any]:
    rows = conn.execute(
        "select coalesce(key, id, 'Wukong memory') as title, content from memories "
        "where content is not null and trim(content) != '' limit ?",
        (remaining,),
    ).fetchall()
    return _create_sqlite_memory_drafts(
        draft_store,
        request=request,
        rows=rows,
        path=path,
        fallback_title="Wukong memory",
        source_kind="wukong-brain-db",
        generation_mode=generation_mode,
        risk_flags=risk_flags,
        draft_source_keys=draft_source_keys,
    )


def _sync_wukong_memory_index(
    conn: sqlite3.Connection,
    draft_store: DraftStore,
    *,
    request: HistorySyncRequest,
    path: Path,
    generation_mode: str,
    risk_flags: list[str],
    remaining: int,
    draft_source_keys: set[tuple[str, str, str]],
) -> dict[str, Any]:
    rows = conn.execute(
        "select coalesce(path, id, 'Wukong memory chunk') as title, text from memory_chunks "
        "where text is not null and trim(text) != '' limit ?",
        (remaining,),
    ).fetchall()
    return _create_sqlite_memory_drafts(
        draft_store,
        request=request,
        rows=rows,
        path=path,
        fallback_title="Wukong memory chunk",
        source_kind="wukong-memory-index",
        generation_mode=generation_mode,
        risk_flags=risk_flags,
        draft_source_keys=draft_source_keys,
    )


def _create_sqlite_memory_drafts(
    draft_store: DraftStore,
    *,
    request: HistorySyncRequest,
    rows: list[tuple[Any, Any]],
    path: Path,
    fallback_title: str,
    source_kind: str,
    generation_mode: str,
    risk_flags: list[str],
    draft_source_keys: set[tuple[str, str, str]],
) -> dict[str, Any]:
    source_path = str(path)
    drafts_created = 0
    drafts_skipped = 0
    for title, content in rows:
        body = str(content)
        h = span_hash(body)
        source_key = (request.agent, source_path, h)
        if source_key in draft_source_keys:
            drafts_skipped += 1
            continue
        summary = _first_non_empty_line(body)[:180] or str(title or fallback_title)[:180]
        draft_store.create(MemoryDraftInput(
            title=str(title or fallback_title)[:120],
            summary=summary,
            body=body,
            type=MemoryType.fact.value,
            tags=list(dict.fromkeys(["history-sync", request.agent, source_kind])),
            source_agent=request.agent,
            source_refs={
                "source_path": source_path,
                "source_kind": source_kind,
                "span_hash": h,
            },
            generation_mode=generation_mode,
            risk_flags=list(risk_flags),
        ))
        draft_source_keys.add(source_key)
        drafts_created += 1
    return {
        "status": "processed",
        "raw_messages": len(rows),
        "drafts_created": drafts_created,
        "drafts_skipped": drafts_skipped,
    }


def _first_non_empty_line(text: str) -> str:
    for line in text.splitlines():
        clean = line.strip().strip("#").strip()
        if clean:
            return clean
    return ""


def _sqlite_has_table(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "select 1 from sqlite_master where type='table' and name=?",
        (table,),
    ).fetchone() is not None


def _read_history_spans(path: Path, agent: str) -> Iterable[TranscriptSpan]:
    if agent == "claude_code" and path.suffix == ".md":
        return list(read_claude_markdown_spans(path))
    if agent == "claude_code" and path.suffix == ".json":
        return list(read_claude_task_spans(path))
    if agent == "cursor" and path.name.endswith(".plan.md"):
        return list(read_cursor_plan_spans(path))
    if agent == "cursor" and path.name == "state.vscdb":
        return list(read_cursor_composer_spans(path))
    return read_spans(path)


def _span_batches(spans: Iterable[TranscriptSpan], batch_size: int) -> Iterator[list[TranscriptSpan]]:
    batch: list[TranscriptSpan] = []
    for span in spans:
        batch.append(span)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def _ingest_history_spans(
    conversation_store: ConversationStore,
    path: Path,
    spans: list[TranscriptSpan],
    *,
    source_agent: str,
    session_id: str,
    project: str | None,
    tags: list[str],
) -> ConversationIngestResult:
    conversation_id = make_conversation_id(source_agent, session_id)
    source_path = str(path.resolve(strict=False))
    try:
        source_uri = path.resolve(strict=False).as_uri()
    except ValueError:
        source_uri = source_path
    observed_at = datetime.now(timezone.utc)
    written = 0
    skipped = 0
    for span in spans:
        record = ConversationMessageRecord(
            id=make_message_id(
                conversation_id=conversation_id,
                role=span.role or "unknown",
                content_text=span.text,
                source_uri=source_uri,
                source_offset_start=span.start_offset,
                source_offset_end=span.end_offset,
            ),
            conversation_id=conversation_id,
            source_agent=source_agent,
            session_id=session_id,
            role=span.role or "unknown",
            content_text=span.text,
            content_sha256=sha256_text(span.text),
            observed_at=observed_at,
            source_uri=source_uri,
            source_path=source_path,
            source_offset_start=span.start_offset,
            source_offset_end=span.end_offset,
            project=project,
            tags=tags,
        )
        if conversation_store.write_message(record):
            written += 1
        else:
            skipped += 1
    return ConversationIngestResult(conversation_id=conversation_id, written=written, skipped=skipped)


def _extract_history_candidates(spans: list[TranscriptSpan], agent: str) -> list[Candidate]:
    candidates = extract_candidates(spans)
    if agent not in {"claude_code", "cursor"}:
        return candidates
    existing_hashes = {candidate.span_hash for candidate in candidates}
    for span in spans:
        if span.role not in {
            "claude_memory",
            "claude_plan",
            "claude_task",
            "cursor_plan",
            "cursor_composer",
        }:
            continue
        h = span_hash(span.text)
        if h in existing_hashes:
            continue
        body = span.text.strip()
        if not body:
            continue
        title = body.splitlines()[0][:80]
        typ = "episode" if span.role in {"claude_task", "cursor_composer"} else "artifact"
        tag_by_role = {
            "claude_memory": "claude-memory",
            "claude_plan": "claude-plan",
            "claude_task": "claude-task",
            "cursor_plan": "cursor-plan",
            "cursor_composer": "cursor-composer",
        }
        candidates.append(Candidate(
            typ,
            title,
            body[:200],
            body,
            span_hash=h,
            tags=["harvested", tag_by_role[span.role]],
        ))
        existing_hashes.add(h)
    return candidates


def _session_id(path: Path, agent: str) -> str:
    if agent == "cursor" and path.name == "state.vscdb":
        return path.parent.name
    return path.stem


def _project_name(path: Path, agent: str) -> str | None:
    if agent == "cursor" and path.name == "state.vscdb":
        return path.parent.name
    return path.parent.name
