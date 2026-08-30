"""Runtime sidecar records for recall gaps and task/question outcomes."""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from agent_brain.platform.bounded_jsonl import iter_bounded_jsonl
from agent_brain.memory.context.injection_contract import INJECTION_EXCLUSION_REASONS
from agent_brain.platform.telemetry_safety import (
    sanitize_gap_evidence,
    sanitize_cwd,
    sanitize_observation_id,
    sanitize_session_id,
    telemetry_digest,
)


RECALL_GAPS_RELATIVE_PATH = "runtime/recall-gaps.jsonl"
TASK_OUTCOMES_RELATIVE_PATH = "runtime/task-outcomes.jsonl"
TASK_OUTCOME_FEEDBACK_RELATIVE_PATH = "runtime/task-outcome-feedback.jsonl"
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
class GapRecord:
    schema_version: int
    gap_id: str
    timestamp: str
    query_digest: str
    query_shape: str
    reason: str
    injected_ids: tuple[str, ...] = ()
    rejected_ids: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    adapter: str = "unknown"
    session_digest: str | None = None
    scope_digest: str | None = None

    @property
    def query(self) -> str:
        """Compatibility alias; raw runtime prompts are never returned."""

        return self.query_digest

    @property
    def normalized_query(self) -> str:
        """Compatibility alias for the non-reversible query shape."""

        return self.query_shape

    @property
    def session_id(self) -> str | None:
        return self.session_digest

    @property
    def cwd(self) -> str | None:
        return self.scope_digest

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
        schema_version=2,
        gap_id=_record_id("gap", timestamp),
        timestamp=timestamp,
        query_digest=_query_digest(query),
        query_shape=_query_shape(query),
        reason=sanitize_recall_gap_reason(reason),
        injected_ids=_dedupe([_safe_gap_item_id(value) for value in injected_ids]),
        rejected_ids=_dedupe([_safe_gap_item_id(value) for value in rejected_ids]),
        evidence=tuple(_sanitize_gap_evidence(value) for value in evidence),
        adapter=_safe_adapter(adapter),
        session_digest=(telemetry_digest(session_id) if session_id else None),
        scope_digest=(telemetry_digest(cwd) if cwd else None),
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
            schema_version = int(str(data.get("schema_version", 1)))
            if schema_version == 2:
                query_digest = _safe_stored_digest(data.get("query_digest"))
                query_shape = _safe_query_shape(data.get("query_shape"))
                if query_digest is None or query_shape is None:
                    continue
                session_digest = _safe_stored_digest(data.get("session_digest"))
                scope_digest = _safe_stored_digest(data.get("scope_digest"))
            elif schema_version == 1:
                legacy_query = str(data.get("normalized_query") or data.get("query") or "")
                query_digest = _query_digest(legacy_query)
                query_shape = _query_shape(legacy_query)
                legacy_session = sanitize_session_id(data.get("session_id"))
                legacy_cwd = sanitize_cwd(data.get("cwd"))
                session_digest = telemetry_digest(legacy_session) if legacy_session else None
                scope_digest = telemetry_digest(legacy_cwd) if legacy_cwd else None
            else:
                continue
            yield GapRecord(
                schema_version=2,
                gap_id=sanitize_observation_id(data["gap_id"], prefix="gap"),
                timestamp=str(data["timestamp"]),
                query_digest=query_digest,
                query_shape=query_shape,
                reason=sanitize_recall_gap_reason(data.get("reason")),
                injected_ids=_dedupe([
                    _safe_gap_item_id(value)
                    for value in data.get("injected_ids", [])
                ]),
                rejected_ids=_dedupe([
                    _safe_gap_item_id(value)
                    for value in data.get("rejected_ids", [])
                ]),
                evidence=tuple(
                    _sanitize_gap_evidence(value)
                    for value in data.get("evidence", [])
                ),
                adapter=_safe_adapter(data.get("adapter")),
                session_digest=session_digest,
                scope_digest=scope_digest,
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            continue


def iter_task_outcomes(brain_dir: Path) -> Iterator[TaskOutcome]:
    for data in _iter_jsonl(task_outcomes_path(brain_dir)):
        try:
            yield TaskOutcome(
                outcome_id=sanitize_observation_id(data["outcome_id"], prefix="out"),
                timestamp=str(data["timestamp"]),
                # Keep the internal correlation key for hook feedback; public
                # DataFlow/Chain read models replace it with an opaque digest.
                task_id=str(data["task_id"]),
                question=str(data["question"]),
                normalized_question=str(data["normalized_question"]),
                outcome=str(data["outcome"]),
                feedback_signals=tuple(
                    str(value) for value in data.get("feedback_signals", [])
                ),
                value_tags=tuple(str(value) for value in data.get("value_tags", [])),
                confidence=_strict_confidence(data.get("confidence", 0.5)),
                injected_ids=tuple(str(value) for value in data.get("injected_ids", [])),
                adopted_ids=tuple(str(value) for value in data.get("adopted_ids", [])),
                rejected_ids=tuple(str(value) for value in data.get("rejected_ids", [])),
                adapter=str(data.get("adapter") or "unknown"),
                session_id=sanitize_session_id(data.get("session_id")),
                cwd=sanitize_cwd(data.get("cwd")),
            )
        except (KeyError, TypeError, ValueError, OverflowError):
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
                session_id=sanitize_session_id(data.get("session_id")),
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            continue


def _append_jsonl(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")


def _iter_jsonl(path: Path) -> Iterator[dict[str, object]]:
    return iter_bounded_jsonl(path)


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


def _query_digest(value: str) -> str:
    normalized = _normalize(value)
    if re.fullmatch(r"sha256:[0-9a-f]{64}", normalized):
        return normalized
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


_QUERY_SHAPE_LABELS: dict[str, tuple[str, ...]] = {
    "browser": ("browser", "浏览器"),
    "permission": ("permission", "权限", "受限"),
    "stale-state": ("stale", "outdated", "已修复", "已经修复", "fixed"),
    "quota": ("quota", "额度", "限额", "424"),
    "model": ("model", "模型"),
    "auth": ("auth", "login", "token", "登录", "鉴权", "认证"),
    "install": ("install", "安装", "setup"),
    "scope": ("cwd", "branch", "adapter", "repo", "作用域"),
    "hook": ("hook", "userpromptsubmit", "stop hook"),
    "memory": ("memory", "记忆", "召回"),
}


def _query_shape(query: str) -> str:
    normalized = _normalize(query)
    has_cjk = any("\u3400" <= ch <= "\u9fff" for ch in normalized)
    has_ascii = any(ch.isascii() and ch.isalpha() for ch in normalized)
    language = "mixed" if has_cjk and has_ascii else "cjk" if has_cjk else "ascii" if has_ascii else "other"
    length = "short" if len(normalized) <= 16 else "medium" if len(normalized) <= 80 else "long"
    intent = "question" if re.search(r"[?？]|怎么|如何|为什么|多少|多久|吗$", normalized) else "statement"
    labels = sorted(
        label
        for label, terms in _QUERY_SHAPE_LABELS.items()
        if any(term in normalized for term in terms)
    )
    label_value = ",".join(labels) if labels else "none"
    return f"lang:{language}|length:{length}|intent:{intent}|labels:{label_value}"


def _safe_stored_digest(value: object) -> str | None:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return None
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        return None
    return value


def _safe_query_shape(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    pattern = (
        r"lang:(?:mixed|cjk|ascii|other)\|"
        r"length:(?:short|medium|long)\|"
        r"intent:(?:question|statement)\|"
        r"labels:(?:none|[a-z-]+(?:,[a-z-]+)*)"
    )
    if not re.fullmatch(pattern, value):
        return None
    raw_labels = value.rsplit("labels:", 1)[1]
    if raw_labels != "none":
        allowed = set(_QUERY_SHAPE_LABELS)
        if any(label not in allowed for label in raw_labels.split(",")):
            return None
    return value


def _sanitize_gap_evidence(value: object) -> str:
    if isinstance(value, str):
        raw_item_id, separator, raw_reason = value.partition(":")
        if (
            separator
            and _safe_gap_item_id(raw_item_id) == raw_item_id
            and raw_reason in INJECTION_EXCLUSION_REASONS
        ):
            return value
    return sanitize_gap_evidence(
        value,
        allowed_exclusion_reasons=INJECTION_EXCLUSION_REASONS,
    )


def _safe_gap_item_id(value: object) -> str:
    if isinstance(value, str) and re.fullmatch(r"mem-[a-z0-9][a-z0-9-]{0,127}", value):
        return value
    return telemetry_digest(value, prefix="mem-observed")


def _safe_adapter(value: object) -> str:
    if isinstance(value, str) and re.fullmatch(r"[a-z0-9_.-]{1,32}", value.lower()):
        return value.lower()
    return "unknown"


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


def _strict_confidence(value: object) -> float:
    if type(value) not in {int, float}:
        raise TypeError("confidence must be a JSON number")
    confidence = float(value)
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be finite and between zero and one")
    return confidence


def sanitize_recall_gap_reason(reason: object) -> str:
    """Return a closed, non-sensitive recall-gap reason label."""

    if isinstance(reason, str) and reason in RECALL_GAP_REASONS:
        return reason
    return UNCLASSIFIED_RECALL_GAP_REASON


__all__ = [
    "GapRecord",
    "RECALL_GAPS_RELATIVE_PATH",
    "RECALL_GAP_REASONS",
    "TASK_OUTCOMES_RELATIVE_PATH",
    "TASK_OUTCOME_FEEDBACK_RELATIVE_PATH",
    "TaskOutcome",
    "TaskOutcomeFeedbackApplication",
    "UNCLASSIFIED_RECALL_GAP_REASON",
    "iter_gap_records",
    "iter_task_outcome_feedback_applications",
    "iter_task_outcomes",
    "recall_gaps_path",
    "record_gap",
    "record_task_outcome",
    "record_task_outcome_feedback_application",
    "sanitize_recall_gap_reason",
    "task_outcome_feedback_path",
    "task_outcomes_path",
]
