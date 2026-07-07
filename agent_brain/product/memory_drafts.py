"""Reviewable MemoryItem drafts produced by local history sync."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Source
from agent_brain.memory.store.items_store import make_item_id
from agent_brain.memory.store.write_service import WriteService


@dataclass(frozen=True)
class MemoryDraftInput:
    title: str
    summary: str
    body: str
    type: str
    tags: list[str]
    source_agent: str
    source_refs: dict[str, Any]
    generation_mode: str
    risk_flags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MemoryDraft:
    draft_id: str
    title: str
    summary: str
    body: str
    type: str
    tags: list[str]
    source_agent: str
    source_refs: dict[str, Any]
    generation_mode: str
    risk_flags: list[str]
    status: str
    created_at: str
    updated_at: str
    item_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DraftStore:
    def __init__(self, brain_dir: Path) -> None:
        self.brain_dir = Path(brain_dir)
        self.draft_dir = self.brain_dir / "drafts" / "history-sync"
        self.draft_dir.mkdir(parents=True, exist_ok=True)

    def create(self, draft: MemoryDraftInput) -> MemoryDraft:
        now = datetime.now(timezone.utc).isoformat()
        record = MemoryDraft(
            draft_id=f"draft-{uuid.uuid4().hex[:16]}",
            title=draft.title,
            summary=draft.summary,
            body=draft.body,
            type=draft.type,
            tags=list(draft.tags),
            source_agent=draft.source_agent,
            source_refs=dict(draft.source_refs),
            generation_mode=draft.generation_mode,
            risk_flags=list(draft.risk_flags),
            status="pending",
            created_at=now,
            updated_at=now,
        )
        self._write(record)
        return record

    def list(self, *, status: str | None = None) -> list[MemoryDraft]:
        drafts = [self._read(path) for path in sorted(self.draft_dir.glob("*.json"))]
        if status:
            drafts = [draft for draft in drafts if draft.status == status]
        return drafts

    def source_keys(self) -> set[tuple[str, str, str]]:
        keys: set[tuple[str, str, str]] = set()
        for draft in self.list():
            source_path = str(draft.source_refs.get("source_path") or "")
            span_hash = str(draft.source_refs.get("span_hash") or "")
            if source_path and span_hash:
                keys.add((draft.source_agent, source_path, span_hash))
        return keys

    def get(self, draft_id: str) -> MemoryDraft:
        path = self._path(draft_id)
        if not path.exists():
            raise ValueError(f"draft not found: {draft_id}")
        return self._read(path)

    def update(self, draft_id: str, **updates: Any) -> MemoryDraft:
        current = self.get(draft_id)
        allowed = {"title", "summary", "body", "type", "tags", "risk_flags"}
        clean = {key: value for key, value in updates.items() if key in allowed and value is not None}
        record = replace(
            current,
            **clean,
            status="edited",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        self._write(record)
        return record

    def skip(self, draft_id: str) -> MemoryDraft:
        current = self.get(draft_id)
        record = replace(
            current,
            status="skipped",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        self._write(record)
        return record

    def apply(self, draft_id: str) -> MemoryDraft:
        current = self.get(draft_id)
        if current.status == "applied":
            return current
        now = datetime.now(timezone.utc).astimezone()
        item = MemoryItem(
            id=make_item_id(current.title, when=now),
            type=MemoryType(current.type),
            created_at=now,
            title=current.title,
            summary=current.summary,
            tags=current.tags,
            source=Source(
                kind="history_sync",
                transcript_id=str(current.source_refs.get("conversation_id") or ""),
                extractor=current.generation_mode,
            ),
            confidence=0.65 if current.generation_mode == "mechanical" else 0.75,
        )
        result = WriteService.for_brain(self.brain_dir).write(item=item, body=current.body, allow_unsafe=True)
        if result.status not in {"written", "merged", "skipped"}:
            raise ValueError(f"draft apply failed: {result.status}")
        record = replace(
            current,
            status="applied",
            item_id=item.id,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        self._write(record)
        return record

    def _path(self, draft_id: str) -> Path:
        return self.draft_dir / f"{draft_id}.json"

    def _write(self, draft: MemoryDraft) -> None:
        self._path(draft.draft_id).write_text(
            json.dumps(draft.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _read(self, path: Path) -> MemoryDraft:
        return MemoryDraft(**json.loads(path.read_text(encoding="utf-8")))
