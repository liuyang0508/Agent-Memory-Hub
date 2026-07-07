"""Runtime sidecar records for recall gaps and task/question outcomes."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


RECALL_GAPS_RELATIVE_PATH = "runtime/recall-gaps.jsonl"
TASK_OUTCOMES_RELATIVE_PATH = "runtime/task-outcomes.jsonl"
TASK_OUTCOME_FEEDBACK_RELATIVE_PATH = "runtime/task-outcome-feedback.jsonl"


@dataclass(frozen=True)
class GapRecord:
    gap_id: str
    timestamp: str
    query: str
    normalized_query: str
    reason: str
    injected_ids: tuple[str, ...] = ()
    rejected_ids: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    adapter: str = "unknown"
    session_id: str | None = None
    cwd: str | None = None

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["injected_ids"] = list(self.injected_ids)
        data["rejected_ids"] = list(self.rejected_ids)
        data["evidence"] = list(self.evidence)
        return data


@dataclass(frozen=True)
class TaskOutcome:
    outcome_id: str
    timestamp: str
    task_id: str
    question: str
    normalized_question: str
    outcome: str
    feedback_signals: tuple[str, ...] = ()
    value_tags: tuple[str, ...] = ()
    confidence: float = 0.5
    injected_ids: tuple[str, ...] = ()
    adopted_ids: tuple[str, ...] = ()
    rejected_ids: tuple[str, ...] = ()
    adapter: str = "unknown"
    session_id: str | None = None
    cwd: str | None = None

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["feedback_signals"] = list(self.feedback_signals)
        data["value_tags"] = list(self.value_tags)
        data["injected_ids"] = list(self.injected_ids)
        data["adopted_ids"] = list(self.adopted_ids)
        data["rejected_ids"] = list(self.rejected_ids)
        return data


@dataclass(frozen=True)
class TaskOutcomeFeedbackApplication:
    application_id: str
    timestamp: str
    outcome_id: str
    applied: bool
    adopted_ids: tuple[str, ...] = ()
    rejected_ids: tuple[str, ...] = ()
    skipped_reason: str | None = None
    adapter: str = "unknown"
    session_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["adopted_ids"] = list(self.adopted_ids)
        data["rejected_ids"] = list(self.rejected_ids)
        return data


def recall_gaps_path(brain_dir: Path) -> Path:
    return Path(brain_dir) / RECALL_GAPS_RELATIVE_PATH


def task_outcomes_path(brain_dir: Path) -> Path:
    return Path(brain_dir) / TASK_OUTCOMES_RELATIVE_PATH


def task_outcome_feedback_path(brain_dir: Path) -> Path:
    return Path(brain_dir) / TASK_OUTCOME_FEEDBACK_RELATIVE_PATH


def record_gap(
    brain_dir: Path,
    *,
    query: str,
    reason: str,
    injected_ids: list[str] | tuple[str, ...] = (),
    rejected_ids: list[str] | tuple[str, ...] = (),
    evidence: list[str] | tuple[str, ...] = (),
    adapter: str = "unknown",
    session_id: str | None = None,
    cwd: str | None = None,
    now: datetime | None = None,
) -> GapRecord:
    timestamp = _timestamp(now)
    record = GapRecord(
        gap_id=_record_id("gap", timestamp),
        timestamp=timestamp,
        query=query,
        normalized_query=_normalize(query),
        reason=reason,
        injected_ids=_dedupe(injected_ids),
        rejected_ids=_dedupe(rejected_ids),
        evidence=tuple(evidence),
        adapter=adapter or "unknown",
        session_id=session_id or None,
        cwd=cwd or None,
    )
    _append_jsonl(recall_gaps_path(brain_dir), record.to_dict())
    return record


def record_task_outcome(
    brain_dir: Path,
    *,
    task_id: str,
    question: str,
    outcome: str,
    feedback_signals: list[str] | tuple[str, ...] = (),
    value_tags: list[str] | tuple[str, ...] = (),
    confidence: float = 0.5,
    injected_ids: list[str] | tuple[str, ...] = (),
    adopted_ids: list[str] | tuple[str, ...] = (),
    rejected_ids: list[str] | tuple[str, ...] = (),
    adapter: str = "unknown",
    session_id: str | None = None,
    cwd: str | None = None,
    now: datetime | None = None,
) -> TaskOutcome:
    timestamp = _timestamp(now)
    record = TaskOutcome(
        outcome_id=_record_id("out", timestamp),
        timestamp=timestamp,
        task_id=task_id,
        question=question,
        normalized_question=_normalize(question),
        outcome=outcome,
        feedback_signals=_dedupe(feedback_signals),
        value_tags=_dedupe(value_tags),
        confidence=_clamp(confidence),
        injected_ids=_dedupe(injected_ids),
        adopted_ids=_dedupe(adopted_ids),
        rejected_ids=_dedupe(rejected_ids),
        adapter=adapter or "unknown",
        session_id=session_id or None,
        cwd=cwd or None,
    )
    _append_jsonl(task_outcomes_path(brain_dir), record.to_dict())
    return record


def record_task_outcome_feedback_application(
    brain_dir: Path,
    *,
    outcome_id: str,
    applied: bool,
    adopted_ids: list[str] | tuple[str, ...] = (),
    rejected_ids: list[str] | tuple[str, ...] = (),
    skipped_reason: str | None = None,
    adapter: str = "unknown",
    session_id: str | None = None,
    now: datetime | None = None,
) -> TaskOutcomeFeedbackApplication:
    timestamp = _timestamp(now)
    record = TaskOutcomeFeedbackApplication(
        application_id=_record_id("outfb", timestamp),
        timestamp=timestamp,
        outcome_id=outcome_id,
        applied=applied,
        adopted_ids=_dedupe(adopted_ids),
        rejected_ids=_dedupe(rejected_ids),
        skipped_reason=skipped_reason,
        adapter=adapter or "unknown",
        session_id=session_id or None,
    )
    _append_jsonl(task_outcome_feedback_path(brain_dir), record.to_dict())
    return record


def iter_gap_records(brain_dir: Path) -> Iterator[GapRecord]:
    for data in _iter_jsonl(recall_gaps_path(brain_dir)):
        try:
            yield GapRecord(
                gap_id=str(data["gap_id"]),
                timestamp=str(data["timestamp"]),
                query=str(data["query"]),
                normalized_query=str(data["normalized_query"]),
                reason=str(data["reason"]),
                injected_ids=tuple(str(value) for value in data.get("injected_ids", [])),
                rejected_ids=tuple(str(value) for value in data.get("rejected_ids", [])),
                evidence=tuple(str(value) for value in data.get("evidence", [])),
                adapter=str(data.get("adapter") or "unknown"),
                session_id=data.get("session_id"),
                cwd=data.get("cwd"),
            )
        except (KeyError, TypeError, ValueError):
            continue


def iter_task_outcomes(brain_dir: Path) -> Iterator[TaskOutcome]:
    for data in _iter_jsonl(task_outcomes_path(brain_dir)):
        try:
            yield TaskOutcome(
                outcome_id=str(data["outcome_id"]),
                timestamp=str(data["timestamp"]),
                task_id=str(data["task_id"]),
                question=str(data["question"]),
                normalized_question=str(data["normalized_question"]),
                outcome=str(data["outcome"]),
                feedback_signals=tuple(
                    str(value) for value in data.get("feedback_signals", [])
                ),
                value_tags=tuple(str(value) for value in data.get("value_tags", [])),
                confidence=float(data.get("confidence", 0.5)),
                injected_ids=tuple(str(value) for value in data.get("injected_ids", [])),
                adopted_ids=tuple(str(value) for value in data.get("adopted_ids", [])),
                rejected_ids=tuple(str(value) for value in data.get("rejected_ids", [])),
                adapter=str(data.get("adapter") or "unknown"),
                session_id=data.get("session_id"),
                cwd=data.get("cwd"),
            )
        except (KeyError, TypeError, ValueError):
            continue


def iter_task_outcome_feedback_applications(
    brain_dir: Path,
) -> Iterator[TaskOutcomeFeedbackApplication]:
    for data in _iter_jsonl(task_outcome_feedback_path(brain_dir)):
        try:
            yield TaskOutcomeFeedbackApplication(
                application_id=str(data["application_id"]),
                timestamp=str(data["timestamp"]),
                outcome_id=str(data["outcome_id"]),
                applied=bool(data["applied"]),
                adopted_ids=tuple(str(value) for value in data.get("adopted_ids", [])),
                rejected_ids=tuple(str(value) for value in data.get("rejected_ids", [])),
                skipped_reason=data.get("skipped_reason"),
                adapter=str(data.get("adapter") or "unknown"),
                session_id=data.get("session_id"),
            )
        except (KeyError, TypeError, ValueError):
            continue


def _append_jsonl(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")


def _iter_jsonl(path: Path) -> Iterator[dict[str, object]]:
    if not path.exists():
        return iter(())

    def _read() -> Iterator[dict[str, object]]:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    yield data

    return _read()


def _timestamp(now: datetime | None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _record_id(prefix: str, timestamp: str) -> str:
    compact = timestamp.replace("-", "").replace(":", "").split(".")[0]
    return f"{prefix}-{compact}-{uuid.uuid4().hex[:8]}"


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _dedupe(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


__all__ = [
    "GapRecord",
    "RECALL_GAPS_RELATIVE_PATH",
    "TASK_OUTCOMES_RELATIVE_PATH",
    "TASK_OUTCOME_FEEDBACK_RELATIVE_PATH",
    "TaskOutcome",
    "TaskOutcomeFeedbackApplication",
    "iter_gap_records",
    "iter_task_outcome_feedback_applications",
    "iter_task_outcomes",
    "recall_gaps_path",
    "record_gap",
    "record_task_outcome",
    "record_task_outcome_feedback_application",
    "task_outcome_feedback_path",
    "task_outcomes_path",
]
