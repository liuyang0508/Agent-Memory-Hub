"""Runtime records for memory items actually injected into context.

This log is deliberately mechanical: it records item IDs and adapter/session
metadata, but never prompt text, memory bodies, summaries, or titles.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from agent_brain.memory.context.injection_metrics import (
    sanitize_adapter_name,
    sanitize_cohort_id,
    sanitize_injection_source,
    sanitize_query_sha256,
)
from agent_brain.platform.bounded_jsonl import iter_bounded_jsonl
from agent_brain.platform.telemetry_safety import sanitize_cwd, sanitize_session_id


INJECTION_COHORTS_RELATIVE_PATH = "runtime/injection-cohorts.jsonl"


@dataclass(frozen=True)
class InjectionCohort:
    cohort_id: str
    timestamp: str
    item_ids: tuple[str, ...]
    adapter: str = "unknown"
    session_id: str | None = None
    cwd: str | None = None
    source: str = "search"
    query_sha256: str | None = None
    query_terms: tuple[str, ...] = ()
    pack_metrics: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["item_ids"] = list(self.item_ids)
        data["query_terms"] = list(self.query_terms)
        if not self.query_terms:
            data.pop("query_terms", None)
        if self.pack_metrics is None:
            data.pop("pack_metrics", None)
        return data


def injection_cohorts_path(brain_dir: Path) -> Path:
    return Path(brain_dir) / INJECTION_COHORTS_RELATIVE_PATH


def record_injection_cohort(
    brain_dir: Path,
    *,
    item_ids: list[str] | tuple[str, ...],
    adapter: str = "unknown",
    session_id: str | None = None,
    cwd: str | None = None,
    query: str | None = None,
    query_terms: list[str] | tuple[str, ...] | None = None,
    source: str = "search",
    now: datetime | None = None,
    pack_metrics: dict[str, object] | None = None,
) -> InjectionCohort:
    deduped = _dedupe(item_ids)
    if not deduped:
        raise ValueError("cannot record empty injection cohort")
    timestamp = _timestamp(now)
    cohort = InjectionCohort(
        cohort_id=_cohort_id(timestamp),
        timestamp=timestamp,
        item_ids=deduped,
        adapter=adapter or "unknown",
        session_id=session_id or None,
        cwd=cwd or None,
        source=source,
        query_sha256=_query_hash(query),
        query_terms=_sanitize_query_terms(query_terms),
        pack_metrics=pack_metrics,
    )
    path = injection_cohorts_path(brain_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(cohort.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    return cohort


def iter_injection_cohorts(
    brain_dir: Path,
    *,
    adapter: str | None = None,
    session_id: str | None = None,
    limit: int | None = None,
) -> Iterator[InjectionCohort]:
    path = injection_cohorts_path(brain_dir)
    cohorts = _iter_parsed_injection_cohorts(
        path,
        adapter=adapter,
        session_id=session_id,
    )
    if limit is None:
        return cohorts
    if type(limit) is not int or limit <= 0:
        return iter(())
    return iter(deque(cohorts, maxlen=limit))


def _iter_parsed_injection_cohorts(
    path: Path,
    *,
    adapter: str | None,
    session_id: str | None,
) -> Iterator[InjectionCohort]:
    for data in iter_bounded_jsonl(path):
        try:
            cohort = InjectionCohort(
                cohort_id=sanitize_cohort_id(data["cohort_id"]),
                timestamp=str(data["timestamp"]),
                item_ids=tuple(str(item_id) for item_id in data["item_ids"]),
                adapter=sanitize_adapter_name(data.get("adapter")),
                session_id=sanitize_session_id(data.get("session_id")),
                cwd=sanitize_cwd(data.get("cwd")),
                source=sanitize_injection_source(data.get("source")),
                query_sha256=sanitize_query_sha256(data.get("query_sha256")),
                query_terms=tuple(str(term) for term in data.get("query_terms") or []),
                pack_metrics=data.get("pack_metrics"),
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if adapter and cohort.adapter != adapter:
            continue
        if session_id and cohort.session_id != session_id:
            continue
        yield cohort


def latest_injection_cohort(
    brain_dir: Path,
    *,
    adapter: str | None = None,
    session_id: str | None = None,
) -> InjectionCohort | None:
    latest = None
    for cohort in iter_injection_cohorts(
        brain_dir,
        adapter=adapter,
        session_id=session_id,
    ):
        latest = cohort
    return latest


def _dedupe(item_ids: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for item_id in item_ids:
        if item_id in seen:
            continue
        seen.add(item_id)
        result.append(item_id)
    return tuple(result)


def _timestamp(now: datetime | None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _cohort_id(timestamp: str) -> str:
    compact = timestamp.replace("-", "").replace(":", "").split(".")[0]
    return f"inj-{compact}-{uuid.uuid4().hex[:8]}"


def _query_hash(query: str | None) -> str | None:
    if not query:
        return None
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def _sanitize_query_terms(query_terms: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if not query_terms:
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for term in query_terms:
        value = str(term).strip()
        if not value or len(value) > 64 or value in seen:
            continue
        seen.add(value)
        result.append(value)
        if len(result) >= 12:
            break
    return tuple(result)


__all__ = [
    "INJECTION_COHORTS_RELATIVE_PATH",
    "InjectionCohort",
    "injection_cohorts_path",
    "iter_injection_cohorts",
    "latest_injection_cohort",
    "record_injection_cohort",
]
