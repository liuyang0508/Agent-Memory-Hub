"""Three-day data-flow read model for Web and diagnostics.

The ledger is deliberately derived-only. It stitches together runtime sidecars
that already exist, then removes prompt/body/query/question fields before the
data reaches Web or CLI surfaces.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from agent_brain.agent_integrations.runtime_events import iter_runtime_events
from agent_brain.agent_integrations.verifications import iter_adapter_verifications
from agent_brain.memory.context.injection_cohorts import iter_injection_cohorts
from agent_brain.memory.context.injection_gateway import INJECTION_EXCLUSION_REASONS
from agent_brain.memory.governance.recall_events import iter_gap_records, iter_task_outcomes
from agent_brain.memory.loops.loop_events import iter_loop_events
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.product.chain_log import (
    sanitize_pack_metric_aggregate_bundle,
    sanitize_pack_metrics,
)


MAX_WINDOW_HOURS = 72
MAX_LIMIT = 500
REDACTED_KEYS = {
    "body",
    "content",
    "content_text",
    "normalized_query",
    "normalized_question",
    "prompt",
    "query",
    "question",
}
RECALL_GAP_AGGREGATE_KEY_ORDER = (
    "retrieved_count",
    "included_count",
    "hydrate_error_count",
    "excluded_count",
)
RECALL_GAP_AGGREGATE_KEYS = frozenset(RECALL_GAP_AGGREGATE_KEY_ORDER)
MAX_RECALL_GAP_COUNT_DIGITS = 12
RECALL_GAP_REASONS = frozenset({
    "all_candidates_rejected",
    "empty_recall",
    "manual_revalidation",
    "multimodal_extraction_missing",
    "only_rejected",
    "partial_candidates_rejected",
    "query_not_injectable",
})
UNCLASSIFIED_RECALL_GAP_REASON = "unclassified"


@dataclass(frozen=True)
class DataFlowEvent:
    """A Web-safe observation of how data moved through AMH."""

    event_id: str
    timestamp: str
    source: str
    stage: str
    summary: str
    status: str = "observed"
    adapter: str | None = None
    session_id: str | None = None
    loop_id: str | None = None
    item_ids: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["item_ids"] = list(self.item_ids)
        data["evidence"] = list(self.evidence)
        data["metadata"] = _sanitize(self.metadata)
        return data


@dataclass(frozen=True)
class DataFlowSummary:
    """Aggregate counters for a list of data-flow events."""

    window_hours: int
    total: int
    by_source: dict[str, int]
    by_stage: dict[str, int]
    failures: int
    last_event_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DataFlowLedger:
    """Aggregate recent flow events without exposing raw user text."""

    def __init__(self, brain_dir: Path):
        self.brain_dir = Path(brain_dir)
        self._known_item_ids_cache: frozenset[str] | None = None

    def list_events(
        self,
        *,
        now: datetime | None = None,
        since_hours: int = MAX_WINDOW_HOURS,
        limit: int = 200,
        source: str | None = None,
    ) -> list[DataFlowEvent]:
        """Return newest-first events for the last ``since_hours`` hours."""

        window_hours = _bounded_hours(since_hours)
        max_events = _bounded_limit(limit)
        end = _aware(now)
        start = end - timedelta(hours=window_hours)
        events = [
            event
            for event in self._all_events()
            if _within_window(event.timestamp, start, end)
            and (source is None or event.source == source)
        ]
        events.sort(key=lambda event: _sort_key(event.timestamp), reverse=True)
        return events[:max_events]

    def summary(
        self,
        events: Iterable[DataFlowEvent] | None = None,
        *,
        now: datetime | None = None,
        since_hours: int = MAX_WINDOW_HOURS,
        source: str | None = None,
    ) -> DataFlowSummary:
        """Summarize a recent event list or compute one from the ledger."""

        window_hours = _bounded_hours(since_hours)
        rows = list(events) if events is not None else self.list_events(
            now=now,
            since_hours=window_hours,
            source=source,
            limit=MAX_LIMIT,
        )
        return DataFlowSummary(
            window_hours=window_hours,
            total=len(rows),
            by_source=dict(Counter(event.source for event in rows)),
            by_stage=dict(Counter(event.stage for event in rows)),
            failures=sum(1 for event in rows if event.status in {"failed", "blocked", "gap"}),
            last_event_at=rows[0].timestamp if rows else None,
        )

    def _all_events(self) -> list[DataFlowEvent]:
        # Keep one ItemsStore scan per read while reflecting changes when a
        # long-lived ledger instance is queried again.
        self._known_item_ids_cache = None
        events: list[DataFlowEvent] = []
        events.extend(self._adapter_runtime_events())
        events.extend(self._adapter_verification_events())
        events.extend(self._loop_events())
        events.extend(self._recall_gap_events())
        events.extend(self._task_outcome_events())
        events.extend(self._injection_events())
        return events

    def _adapter_runtime_events(self) -> list[DataFlowEvent]:
        return [
            DataFlowEvent(
                event_id=_event_id("adapter-runtime", event.timestamp, event.adapter, event.event_name),
                timestamp=event.timestamp,
                source="adapter_runtime",
                stage="触发采集",
                summary=f"{event.adapter} 触发 {event.event_name}",
                status="observed",
                adapter=event.adapter,
                session_id=event.session_id,
                metadata={
                    "event_name": event.event_name,
                    "source": event.source,
                    "cwd": event.cwd,
                },
            )
            for event in iter_runtime_events(self.brain_dir)
        ]

    def _adapter_verification_events(self) -> list[DataFlowEvent]:
        return [
            DataFlowEvent(
                event_id=_event_id("adapter-verification", record.timestamp, record.adapter),
                timestamp=record.timestamp,
                source="adapter_verification",
                stage="适配器验证",
                summary=f"{record.adapter} 验证 {record.status}",
                status="verified" if record.status == "passed" else "failed",
                adapter=record.adapter,
                evidence=tuple(record.evidence),
                metadata={
                    "verifier": record.verifier,
                    "note": record.note,
                },
            )
            for record in iter_adapter_verifications(self.brain_dir)
        ]

    def _loop_events(self) -> list[DataFlowEvent]:
        return [
            DataFlowEvent(
                event_id=event.event_id,
                timestamp=event.timestamp,
                source="loop",
                stage="循环工程",
                summary=event.summary or event.event_type,
                status=_loop_status(event.event_type),
                loop_id=event.loop_id,
                metadata={
                    "actor": event.actor,
                    "event_type": event.event_type,
                    "payload": _sanitize(event.payload),
                },
            )
            for event in iter_loop_events(self.brain_dir)
        ]

    def _recall_gap_events(self) -> list[DataFlowEvent]:
        events: list[DataFlowEvent] = []
        for record in iter_gap_records(self.brain_dir):
            reason = _sanitize_recall_gap_reason(record.reason)
            injected_ids = self._safe_item_ids(record.injected_ids)
            rejected_ids = self._safe_item_ids(record.rejected_ids)
            events.append(DataFlowEvent(
                event_id=record.gap_id,
                timestamp=record.timestamp,
                source="recall_gap",
                stage="召回诊断",
                summary=f"召回缺口：{reason}",
                status="gap",
                adapter=record.adapter,
                session_id=record.session_id,
                item_ids=_dedupe((*injected_ids, *rejected_ids)),
                evidence=_sanitize_recall_gap_evidence(record.evidence),
                metadata={
                    "reason": reason,
                    "injected_count": len(injected_ids),
                    "rejected_count": len(rejected_ids),
                    "has_query": bool(record.query),
                    "cwd": record.cwd,
                },
            ))
        return events

    def _task_outcome_events(self) -> list[DataFlowEvent]:
        events: list[DataFlowEvent] = []
        for record in iter_task_outcomes(self.brain_dir):
            injected_ids = self._safe_item_ids(record.injected_ids)
            adopted_ids = self._safe_item_ids(record.adopted_ids)
            rejected_ids = self._safe_item_ids(record.rejected_ids)
            events.append(DataFlowEvent(
                event_id=record.outcome_id,
                timestamp=record.timestamp,
                source="task_outcome",
                stage="结果反馈",
                summary=f"任务结果：{record.outcome}",
                status=record.outcome,
                adapter=record.adapter,
                session_id=record.session_id,
                item_ids=_dedupe((*injected_ids, *adopted_ids, *rejected_ids)),
                metadata={
                    "task_id": record.task_id,
                    "confidence": record.confidence,
                    "feedback_signals": record.feedback_signals,
                    "value_tags": record.value_tags,
                    "injected_count": len(injected_ids),
                    "adopted_count": len(adopted_ids),
                    "rejected_count": len(rejected_ids),
                    "has_question": bool(record.question),
                    "cwd": record.cwd,
                },
            ))
        return events

    def _injection_events(self) -> list[DataFlowEvent]:
        events: list[DataFlowEvent] = []
        for cohort in iter_injection_cohorts(self.brain_dir):
            item_ids = self._safe_item_ids(cohort.item_ids)
            events.append(DataFlowEvent(
                event_id=cohort.cohort_id,
                timestamp=cohort.timestamp,
                source="injection",
                stage="上下文注入",
                summary=f"记录 {len(item_ids)} 条 injection cohort 记忆",
                status="injected",
                adapter=cohort.adapter,
                session_id=cohort.session_id,
                item_ids=item_ids,
                metadata={
                    "source": cohort.source,
                    "query_sha256": cohort.query_sha256,
                    "pack_metrics": sanitize_pack_metrics(
                        cohort.pack_metrics,
                        cohort_item_ids=item_ids,
                    ),
                    "cwd": cohort.cwd,
                },
            ))
        return events

    def _safe_item_ids(self, item_ids: Iterable[str]) -> tuple[str, ...]:
        known_ids = self._known_item_ids()
        return _dedupe(item_id for item_id in item_ids if item_id in known_ids)

    def _known_item_ids(self) -> frozenset[str]:
        if self._known_item_ids_cache is None:
            if not (self.brain_dir / "items").exists():
                self._known_item_ids_cache = frozenset()
            else:
                self._known_item_ids_cache = frozenset(
                    item.id
                    for item, _body in ItemsStore(self.brain_dir / "items").iter_all()
                )
        return self._known_item_ids_cache


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, child in value.items():
            text_key = str(key)
            if text_key.lower() in REDACTED_KEYS:
                continue
            cleaned[text_key] = _sanitize(child)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_sanitize(child) for child in value]
    return value


def _sanitize_recall_gap_evidence(
    evidence: Iterable[object],
) -> tuple[str, ...]:
    counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    for raw_value in evidence:
        if not isinstance(raw_value, str):
            continue
        key, separator, raw_count = raw_value.partition("=")
        if key in RECALL_GAP_AGGREGATE_KEYS:
            if (
                key in counts
                or not separator
                or not _is_bounded_ascii_count(raw_count)
            ):
                return ()
            counts[key] = int(raw_count)
            continue
        prefix = "excluded_reason."
        if not key.startswith(prefix):
            continue
        reason = key.removeprefix(prefix)
        if reason not in INJECTION_EXCLUSION_REASONS:
            continue
        if (
            reason in reason_counts
            or not separator
            or not _is_bounded_ascii_count(raw_count)
            or int(raw_count) <= 0
        ):
            return ()
        reason_counts[reason] = int(raw_count)

    if set(counts) != RECALL_GAP_AGGREGATE_KEYS:
        return ()
    retrieved_count = counts["retrieved_count"]
    hydrate_error_count = counts["hydrate_error_count"]
    aggregate = sanitize_pack_metric_aggregate_bundle({
        "candidate_count": retrieved_count,
        "included_count": counts["included_count"],
        "excluded_count": counts["excluded_count"],
        "raw_candidate_count": retrieved_count,
        "gateway_candidate_count": retrieved_count - hydrate_error_count,
        "hydrate_error_count": hydrate_error_count,
        "excluded_reasons": reason_counts,
    })
    if not aggregate:
        return ()
    return tuple(
        [f"{key}={counts[key]}" for key in RECALL_GAP_AGGREGATE_KEY_ORDER]
        + [
            f"excluded_reason.{reason}={count}"
            for reason, count in sorted(reason_counts.items())
        ]
    )


def _sanitize_recall_gap_reason(reason: object) -> str:
    if isinstance(reason, str) and reason in RECALL_GAP_REASONS:
        return reason
    return UNCLASSIFIED_RECALL_GAP_REASON


def _is_bounded_ascii_count(value: str) -> bool:
    return bool(
        value
        and len(value) <= MAX_RECALL_GAP_COUNT_DIGITS
        and value.isascii()
        and value.isdecimal()
    )


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _loop_status(event_type: str) -> str:
    if event_type in {"failed", "cancelled"}:
        return "failed"
    if event_type == "completed":
        return "completed"
    return "observed"


def _within_window(timestamp: str, start: datetime, end: datetime) -> bool:
    parsed = _parse_timestamp(timestamp)
    return bool(parsed and start <= parsed <= end)


def _sort_key(timestamp: str) -> datetime:
    return _parse_timestamp(timestamp) or datetime.min.replace(tzinfo=timezone.utc)


def _parse_timestamp(timestamp: str) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _aware(now: datetime | None = None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bounded_hours(value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = MAX_WINDOW_HOURS
    return max(1, min(MAX_WINDOW_HOURS, parsed))


def _bounded_limit(value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 200
    return max(1, min(MAX_LIMIT, parsed))


def _event_id(prefix: str, *parts: object) -> str:
    compact = "-".join(str(part).replace(":", "").replace("/", "-") for part in parts if part)
    return f"{prefix}-{compact}"


__all__ = ["DataFlowEvent", "DataFlowLedger", "DataFlowSummary"]
