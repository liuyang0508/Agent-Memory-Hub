"""Orchestrate offline-first harvesting of CC transcripts into the brain pool.

Flow per transcript: resume from watermark → read new spans → mechanical extract
→ dedup vs pool + watermark → write via WriteService (same durable path as every
other writer) → advance watermark → optionally LLM-enrich raw → distilled.
Idempotent and resumable: safe to re-run; rate-limit during enrich just defers it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agent_brain.memory.store.items_store import ItemsStore, make_item_id
from agent_brain.memory.store.write_service import WriteService, _brain_dir
from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Source
from agent_brain.memory.evidence.harvest.transcript_reader import read_spans, discover_transcripts
from agent_brain.memory.evidence.harvest.watermark import WatermarkStore
from agent_brain.memory.evidence.harvest.extractor import extract_candidates
from agent_brain.memory.evidence.conversation_store import ConversationStore


@dataclass
class HarvestStats:
    written: int = 0
    skipped: int = 0
    enriched: int = 0
    raw_messages: int = 0


class Harvester:
    def __init__(self, transcripts_root: Path | None = None):
        self._root = transcripts_root
        # Freeze the brain dir once (honours $BRAIN_DIR) so the read-side dedup
        # store and the write-side service share one location for this run.
        self._brain = _brain_dir()
        self._wm = WatermarkStore()
        self._svc = WriteService.for_brain(self._brain)

    def _seen_span_hashes(self) -> set[str]:
        seen: set[str] = set()
        store = ItemsStore(items_dir=self._brain / "items")
        for item, _ in store.iter_all():
            sh = getattr(item.source, "span_hash", None)
            if sh:
                seen.add(sh)
        return seen

    def run(self, *, enrich: bool = False) -> HarvestStats:
        stats = HarvestStats()
        seen = self._seen_span_hashes()
        transcripts = (sorted(self._root.glob("*/*.jsonl")) if self._root else discover_transcripts())
        conversation_store = ConversationStore(self._brain)
        for tp in transcripts:
            raw_result = conversation_store.ingest_transcript(
                tp,
                source_agent="claude-code",
                session_id=tp.stem,
                project=tp.parent.name,
                tags=["harvested", "conversation"],
            )
            stats.raw_messages += raw_result.written
            start = self._wm.get_offset(tp)
            last_off = start
            for span in read_spans(tp, start_offset=start):
                last_off = span.end_offset
                for cand in extract_candidates([span]):
                    sh = "sha256:" + cand.span_hash
                    if sh in seen:
                        stats.skipped += 1
                        continue
                    now = datetime.now(timezone.utc).astimezone()
                    item = MemoryItem(
                        id=make_item_id(cand.title, when=now), type=MemoryType(cand.type),
                        created_at=now, title=cand.title, summary=cand.summary,
                        tags=cand.tags, confidence=cand.confidence,
                        source=Source(kind="harvested", transcript_id=tp.stem,
                                      span_hash=sh, extractor="mechanical"),
                    )
                    res = self._svc.write(item=item, body=cand.body, allow_unsafe=True)
                    if res.status == "written":
                        seen.add(sh)
                        stats.written += 1
            self._wm.set_offset(tp, offset=last_off)
        self._wm.save()
        if enrich:
            stats.enriched = self._enrich_raw()
        return stats

    def _enrich_raw(self) -> int:
        try:
            from agent_brain.memory.evidence.harvest.enricher import enrich_pool
            return enrich_pool()
        except Exception:
            return 0   # model unavailable → mechanical layer already persisted
