from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from agent_brain.contracts.conversation import (
    ConversationMessageRecord,
    ConversationSummary,
    ConversationTier,
    make_conversation_id,
    make_message_id,
)
from agent_brain.contracts.memory_enums import Sensitivity
from agent_brain.contracts.resource import sha256_text
from agent_brain.memory.evidence.conversation_governance import (
    ConversationRebalanceReport,
    ConversationTierThresholds,
    classify_tier,
)
from agent_brain.memory.evidence.harvest.transcript_reader import read_spans


@dataclass(frozen=True)
class ConversationIngestResult:
    conversation_id: str
    written: int = 0
    skipped: int = 0


class ConversationStore:
    """Local source-evidence store for raw conversation messages.

    Messages live under ``sources/conversations/<conversation_id>/messages.jsonl``
    and are intentionally separate from ``items/``. A later extractor can cite
    these message ids, but raw conversation text does not become injectable
    knowledge merely because it was captured.
    """

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = Path(root_dir)
        self.conversations_dir = self.root_dir / "sources" / "conversations"
        self.conversations_dir.mkdir(parents=True, exist_ok=True)

    def ingest_transcript(
        self,
        path: Path,
        *,
        source_agent: str,
        session_id: str | None = None,
        project: str | None = None,
        cwd: str | None = None,
        tenant_id: str | None = None,
        tags: list[str] | None = None,
        sensitivity: Sensitivity | str = Sensitivity.internal,
        tier: ConversationTier | str = ConversationTier.hot,
    ) -> ConversationIngestResult:
        transcript = Path(path).expanduser()
        session = session_id or transcript.stem
        conversation_id = make_conversation_id(source_agent, session)
        source_path = str(transcript.resolve(strict=False))
        try:
            source_uri = transcript.resolve(strict=False).as_uri()
        except ValueError:
            source_uri = source_path

        observed_at = datetime.now(timezone.utc)
        messages_path = self._messages_path(conversation_id)
        messages_path.parent.mkdir(parents=True, exist_ok=True)
        known_ids = self._message_ids(messages_path)
        written = 0
        skipped = 0
        for span in read_spans(transcript):
            message = ConversationMessageRecord(
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
                session_id=session,
                role=span.role or "unknown",
                content_text=span.text,
                content_sha256=sha256_text(span.text),
                observed_at=observed_at,
                source_uri=source_uri,
                source_path=source_path,
                source_offset_start=span.start_offset,
                source_offset_end=span.end_offset,
                project=project,
                cwd=cwd,
                tenant_id=tenant_id,
                tags=tags or [],
                sensitivity=sensitivity,
                tier=tier,
            )
            if self._append_message_if_new(message, messages_path, known_ids):
                written += 1
            else:
                skipped += 1
        return ConversationIngestResult(
            conversation_id=conversation_id,
            written=written,
            skipped=skipped,
        )

    def write_message(self, record: ConversationMessageRecord) -> bool:
        """Append ``record`` if its message id has not already been stored."""
        path = self._messages_path(record.conversation_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        return self._append_message_if_new(record, path, self._message_ids(path))

    def remove_messages(self, conversation_id: str, message_ids: Iterable[str]) -> int:
        """Remove messages by id from one conversation and return the count."""
        target_ids = set(message_ids)
        if not target_ids:
            return 0
        messages = list(self.iter_messages(conversation_id))
        if not messages:
            return 0
        kept = [message for message in messages if message.id not in target_ids]
        removed = len(messages) - len(kept)
        if removed:
            self._rewrite_messages(conversation_id, kept)
        return removed

    def _append_message_if_new(
        self,
        record: ConversationMessageRecord,
        path: Path,
        known_ids: set[str],
    ) -> bool:
        """Append one record and update the caller-owned id cache."""
        if record.id in known_ids:
            return False
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    record.model_dump(mode="json", exclude_none=False),
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
        known_ids.add(record.id)
        return True

    def touch_conversation(
        self,
        conversation_id: str,
        *,
        message_ids: Iterable[str] | None = None,
        now: datetime | None = None,
    ) -> int:
        """Record that raw evidence was read, strengthening returned messages."""
        now = now or datetime.now(timezone.utc)
        target_ids = None if message_ids is None else set(message_ids)
        if target_ids is not None and not target_ids:
            return 0
        messages = list(self.iter_messages(conversation_id))
        if not messages:
            return 0
        touched: list[ConversationMessageRecord] = []
        touched_count = 0
        for message in messages:
            if target_ids is not None and message.id not in target_ids:
                touched.append(message)
                continue
            retention = message.retention.model_copy(update={
                "last_accessed": now,
                "access_count": message.retention.access_count + 1,
            })
            touched.append(message.model_copy(update={"retention": retention}))
            touched_count += 1
        if not touched_count:
            return 0
        self._rewrite_messages(conversation_id, touched)
        return touched_count

    def rebalance_tiers(
        self,
        *,
        now: datetime | None = None,
        thresholds: ConversationTierThresholds | None = None,
    ) -> ConversationRebalanceReport:
        """Recompute hot/warm/cold/frozen tiers for every raw message."""
        now = now or datetime.now(timezone.utc)
        report = ConversationRebalanceReport()
        grouped: dict[str, list[ConversationMessageRecord]] = {}
        for message in self.iter_messages():
            grouped.setdefault(message.conversation_id, []).append(message)

        for conversation_id, messages in grouped.items():
            rewritten: list[ConversationMessageRecord] = []
            for message in messages:
                tier = classify_tier(message, now=now, thresholds=thresholds)
                report.scanned += 1
                report.distribution[tier.value] = report.distribution.get(tier.value, 0) + 1
                if str(message.tier) != tier.value:
                    report.updated += 1
                rewritten.append(message.model_copy(update={"tier": tier}))
            self._rewrite_messages(conversation_id, rewritten)
        return report

    def iter_messages(self, conversation_id: str | None = None) -> Iterator[ConversationMessageRecord]:
        paths = [self._messages_path(conversation_id)] if conversation_id else sorted(
            self.conversations_dir.glob("*/messages.jsonl")
        )
        for path in paths:
            if not path.exists():
                continue
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        yield ConversationMessageRecord.model_validate(json.loads(line))

    def iter_conversations(
        self,
        *,
        source_agent: str | None = None,
        project: str | None = None,
        tenant_id: str | None = None,
    ) -> Iterator[ConversationSummary]:
        grouped: dict[str, list[ConversationMessageRecord]] = {}
        for message in self.iter_messages():
            if source_agent is not None and message.source_agent != source_agent:
                continue
            if project is not None and message.project != project:
                continue
            if tenant_id is not None and message.tenant_id != tenant_id:
                continue
            grouped.setdefault(message.conversation_id, []).append(message)

        summaries = []
        for conversation_id, messages in grouped.items():
            messages.sort(key=lambda message: message.observed_at)
            first = messages[0]
            summaries.append(ConversationSummary(
                conversation_id=conversation_id,
                source_agent=first.source_agent,
                session_id=first.session_id,
                project=first.project,
                message_count=len(messages),
                first_observed_at=messages[0].observed_at,
                last_observed_at=messages[-1].observed_at,
                tier=_highest_temperature(messages),
            ))
        yield from sorted(
            summaries,
            key=lambda summary: (summary.last_observed_at, summary.conversation_id),
            reverse=True,
        )

    def _messages_path(self, conversation_id: str) -> Path:
        return self.conversations_dir / conversation_id / "messages.jsonl"

    def _rewrite_messages(
        self,
        conversation_id: str,
        messages: list[ConversationMessageRecord],
    ) -> None:
        path = self._messages_path(conversation_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for message in messages:
                handle.write(
                    json.dumps(
                        message.model_dump(mode="json", exclude_none=False),
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )

    @staticmethod
    def _message_ids(path: Path) -> set[str]:
        if not path.exists():
            return set()
        ids = set()
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    ids.add(json.loads(line)["id"])
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
        return ids


def _highest_temperature(messages: list[ConversationMessageRecord]) -> ConversationTier:
    order = {
        ConversationTier.hot: 0,
        ConversationTier.warm: 1,
        ConversationTier.cold: 2,
        ConversationTier.frozen: 3,
    }
    return min((ConversationTier(message.tier) for message in messages), key=lambda tier: order[tier])


__all__ = ["ConversationIngestResult", "ConversationStore"]
