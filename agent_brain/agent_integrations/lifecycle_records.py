"""Low-sensitive adapter lifecycle provenance and evidence freshness."""

from __future__ import annotations

import json
import os
import subprocess
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Literal, get_args

from agent_brain._version import __version__
from agent_brain.memory.context.injection_cohorts import latest_injection_cohort
from agent_brain.platform.bounded_jsonl import iter_bounded_jsonl

from .manifests import MANIFEST_SCHEMA_VERSION
from .runtime_events import runtime_event_summary
from .verifications import adapter_verification_summary


LIFECYCLE_RECORDS_RELATIVE_PATH = "runtime/adapter-lifecycle.jsonl"
LifecycleAction = Literal[
    "install",
    "verify",
    "doctor",
    "repair",
    "upgrade",
    "uninstall",
    "release",
]
LifecycleStatus = Literal["passed", "failed", "blocked"]
LifecycleReasonCode = Literal[
    "OK",
    "UNKNOWN_ADAPTER",
    "ADAPTER_WIP",
    "ADAPTER_DISABLED",
    "CLIENT_MISSING",
    "CONFIG_MALFORMED",
    "DOCTOR_FAILED",
    "RUNTIME_MISSING",
    "CONTEXT_MISSING",
    "EVIDENCE_STALE",
    "OWNERSHIP_CONFLICT",
    "BACKUP_FAILED",
    "ROLLBACK_FAILED",
    "INVALID_PROMOTION",
    "INTERNAL_ERROR",
]

_ACTIONS = frozenset(get_args(LifecycleAction))
_STATUSES = frozenset(get_args(LifecycleStatus))
_REASON_CODES = frozenset(get_args(LifecycleReasonCode))
_FUTURE_TOLERANCE_SECONDS = 5


@dataclass(frozen=True)
class AdapterLifecycleRecord:
    adapter: str
    action: LifecycleAction
    status: LifecycleStatus
    reason_code: LifecycleReasonCode
    timestamp: str
    package_version: str
    commit: str
    manifest_version: str
    artifact_hashes: dict[str, str]
    backup_id: str | None = None
    cohort: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceFreshness:
    source: str
    observed: bool
    fresh: bool
    timestamp: str | None
    age_seconds: int | None
    ttl_seconds: int
    invalid_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AdapterLifecycleEvidenceSummary:
    runtime: EvidenceFreshness
    context_injection: EvidenceFreshness
    verification: EvidenceFreshness
    stale_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def lifecycle_records_path(brain_dir: Path) -> Path:
    return Path(brain_dir) / LIFECYCLE_RECORDS_RELATIVE_PATH


def record_lifecycle_event(
    brain_dir: Path,
    *,
    adapter: str,
    action: LifecycleAction,
    status: LifecycleStatus,
    reason_code: LifecycleReasonCode,
    artifact_hashes: dict[str, str] | None = None,
    backup_id: str | None = None,
    cohort: str | None = None,
    now: datetime | None = None,
) -> AdapterLifecycleRecord:
    """Append one privacy-bounded lifecycle record using mode 0600."""

    if action not in _ACTIONS:
        raise ValueError(f"unsupported lifecycle action: {action}")
    if status not in _STATUSES:
        raise ValueError(f"unsupported lifecycle status: {status}")
    if reason_code not in _REASON_CODES:
        raise ValueError(f"unsupported lifecycle reason code: {reason_code}")
    record = AdapterLifecycleRecord(
        adapter=_safe_label(adapter),
        action=action,
        status=status,
        reason_code=reason_code,
        timestamp=_timestamp(now),
        package_version=__version__,
        commit=_repository_commit(),
        manifest_version=MANIFEST_SCHEMA_VERSION,
        artifact_hashes=_safe_artifact_hashes(artifact_hashes),
        backup_id=_safe_optional_label(backup_id),
        cohort=_safe_optional_label(cohort),
    )
    path = lifecycle_records_path(brain_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    finally:
        path.chmod(0o600)
    return record


def iter_lifecycle_records(
    brain_dir: Path,
    *,
    adapter: str | None = None,
    limit: int | None = None,
) -> Iterator[AdapterLifecycleRecord]:
    records = _iter_parsed_records(lifecycle_records_path(brain_dir), adapter=adapter)
    if limit is None:
        return records
    if type(limit) is not int or limit <= 0:
        return iter(())
    return iter(deque(records, maxlen=limit))


def _iter_parsed_records(
    path: Path,
    *,
    adapter: str | None,
) -> Iterator[AdapterLifecycleRecord]:
    for data in iter_bounded_jsonl(path):
        action = str(data.get("action") or "")
        status = str(data.get("status") or "")
        reason_code = str(data.get("reason_code") or "")
        if action not in _ACTIONS or status not in _STATUSES or reason_code not in _REASON_CODES:
            continue
        try:
            record = AdapterLifecycleRecord(
                adapter=_safe_label(data["adapter"]),
                action=action,  # type: ignore[arg-type]
                status=status,  # type: ignore[arg-type]
                reason_code=reason_code,  # type: ignore[arg-type]
                timestamp=str(data["timestamp"]),
                package_version=str(data["package_version"]),
                commit=str(data["commit"]),
                manifest_version=str(data["manifest_version"]),
                artifact_hashes=_safe_artifact_hashes(data.get("artifact_hashes")),
                backup_id=_safe_optional_label(data.get("backup_id")),
                cohort=_safe_optional_label(data.get("cohort")),
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if adapter and record.adapter != adapter:
            continue
        yield record


def evidence_freshness(
    source: str,
    timestamp: str | None,
    *,
    now: datetime,
    ttl_seconds: int,
) -> EvidenceFreshness:
    """Classify one timestamp; malformed/future values fail closed."""

    if not timestamp:
        return EvidenceFreshness(source, False, False, None, None, ttl_seconds)
    try:
        observed_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        observed_at = observed_at.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return EvidenceFreshness(
            source,
            True,
            False,
            timestamp,
            None,
            ttl_seconds,
            "invalid_timestamp",
        )
    evaluated_at = _utc(now)
    age = int((evaluated_at - observed_at).total_seconds())
    if age < -_FUTURE_TOLERANCE_SECONDS:
        return EvidenceFreshness(
            source,
            True,
            False,
            timestamp,
            age,
            ttl_seconds,
            "future_timestamp",
        )
    normalized_age = max(age, 0)
    return EvidenceFreshness(
        source,
        True,
        normalized_age <= ttl_seconds,
        timestamp,
        normalized_age,
        ttl_seconds,
    )


def lifecycle_evidence_summary(
    brain_dir: Path,
    adapter: str,
    *,
    now: datetime,
    runtime_ttl_seconds: int,
    context_ttl_seconds: int,
    verification_ttl_seconds: int,
) -> AdapterLifecycleEvidenceSummary:
    runtime = runtime_event_summary(brain_dir, adapter)
    verification = adapter_verification_summary(brain_dir, adapter)
    cohort = latest_injection_cohort(brain_dir, adapter=adapter)
    runtime_freshness = evidence_freshness(
        "runtime",
        str((runtime.last_event or {}).get("timestamp") or "") or None,
        now=now,
        ttl_seconds=runtime_ttl_seconds,
    )
    context_freshness = evidence_freshness(
        "context_injection",
        cohort.timestamp if cohort else None,
        now=now,
        ttl_seconds=context_ttl_seconds,
    )
    verification_freshness = evidence_freshness(
        "verification",
        str((verification.last_record or {}).get("timestamp") or "") or None,
        now=now,
        ttl_seconds=verification_ttl_seconds,
    )
    stale_reasons: list[str] = []
    if runtime_freshness.observed and not runtime_freshness.fresh:
        stale_reasons.append("runtime evidence stale")
    if context_freshness.observed and not context_freshness.fresh:
        stale_reasons.append("context injection evidence stale")
    if verification_freshness.observed and not verification_freshness.fresh:
        stale_reasons.append("verification evidence stale")
    return AdapterLifecycleEvidenceSummary(
        runtime=runtime_freshness,
        context_injection=context_freshness,
        verification=verification_freshness,
        stale_reasons=tuple(stale_reasons),
    )


def _timestamp(now: datetime | None) -> str:
    return _utc(now or datetime.now(timezone.utc)).isoformat()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_label(value: object) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 128 or any(char in text for char in "\r\n\t"):
        raise ValueError("invalid lifecycle label")
    return text


def _safe_optional_label(value: object) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    return _safe_label(value)


def _safe_artifact_hashes(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("artifact_hashes must be an object")
    result: dict[str, str] = {}
    for raw_name, raw_digest in value.items():
        name = Path(_safe_label(raw_name)).name
        digest = _safe_label(raw_digest)
        if len(result) >= 32:
            break
        result[name] = digest
    return result


@lru_cache(maxsize=1)
def _repository_commit() -> str:
    root = Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    commit = result.stdout.strip()
    return commit if len(commit) == 40 else "unknown"


__all__ = [
    "AdapterLifecycleEvidenceSummary",
    "AdapterLifecycleRecord",
    "EvidenceFreshness",
    "LIFECYCLE_RECORDS_RELATIVE_PATH",
    "LifecycleAction",
    "LifecycleReasonCode",
    "LifecycleStatus",
    "evidence_freshness",
    "iter_lifecycle_records",
    "lifecycle_evidence_summary",
    "lifecycle_records_path",
    "record_lifecycle_event",
]
