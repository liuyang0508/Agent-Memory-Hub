"""Request-chain log read model for Web diagnostics.

This module is observational only. It joins existing runtime sidecars into request
chains and never returns raw prompt/query/question/body content.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from agent_brain.agent_integrations.runtime_events import AdapterRuntimeEvent, iter_runtime_events
from agent_brain.memory.context.injection_gateway import INJECTION_EXCLUSION_REASONS
from agent_brain.memory.context.injection_cohorts import InjectionCohort, iter_injection_cohorts
from agent_brain.memory.governance.recall_events import GapRecord, TaskOutcome, iter_gap_records, iter_task_outcomes
from agent_brain.memory.store.items_store import ItemsStore


MAX_WINDOW_HOURS = 72
MAX_LIMIT = 500


STAGE_CONTRACT: tuple[tuple[str, str], ...] = (
    ("hook_capture", "Hook Capture"),
    ("prompt_frame", "Prompt Frame"),
    ("query_gate", "Query Gate"),
    ("retrieval", "Retrieval"),
    ("context_firewall", "Context Firewall"),
    ("context_loading", "Context Loading"),
    ("packing", "Packing"),
    ("injection", "Injection"),
    ("feedback", "Feedback / Gap"),
)

ALGORITHM_CONTRACT: tuple[tuple[str, str], ...] = (
    ("metadata_filter", "Metadata Filter"),
    ("bm25", "BM25"),
    ("vector", "Vector"),
    ("rrf", "RRF Fusion"),
    ("cross_encoder", "Cross-Encoder Rerank"),
    ("retention", "Retention"),
    ("decay_coefficient", "Decay Coefficient"),
    ("feedback_value", "Feedback Value"),
    ("runtime_status", "Runtime / Status Boost"),
    ("temporal_supersession", "Temporal Supersession"),
    ("mmr", "MMR"),
    ("hopfield", "Hopfield"),
    ("graph_expansion", "Graph Expansion"),
    ("budget_trim", "Budget Trim"),
)

REDACTED_KEYS = {
    "body",
    "content",
    "content_text",
    "normalized_query",
    "normalized_question",
    "prompt",
    "query",
    "question",
    "tool_args",
    "tool_args_raw",
    "tool_arguments",
    "arguments",
    "args_raw",
    "retrieval_query",
}

CHAIN_ANCHOR_WINDOW = timedelta(minutes=5)
RETRIEVAL_TRACE_ALLOWED_KEYS = {
    "initial_bm25_rank",
    "initial_vector_rank",
    "initial_score",
    "final_rank",
    "final_score",
    "signals",
    "stages",
}
RETRIEVAL_TRACE_STAGE_ALLOWED_KEYS = {
    "name",
    "before_rank",
    "after_rank",
    "before_score",
    "after_score",
    "effect",
}
PACK_METRICS_ALLOWED_KEYS = {
    "candidate_count",
    "compressed_count",
    "context_pack_chars",
    "detail_refs",
    "excluded_count",
    "excluded_reasons",
    "full_tokens",
    "included_count",
    "items",
    "packed_tokens",
    "query_terms_count",
    "retrieval_trace",
    "selected_views",
    "trimmed_ids",
}
PACK_METRIC_ITEM_ALLOWED_KEYS = {
    "compressed",
    "full_tokens",
    "id",
    "packed_tokens",
    "selected_view",
}
RETRIEVAL_TRACE_SIGNAL_ALLOWED_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_.:-/")
MMR_APPLIED_EFFECTS = {
    "added",
    "applied",
    "boosted",
    "changed",
    "demoted",
    "reranked",
    "reordered",
    "rescored",
}


@dataclass(frozen=True)
class ChainStage:
    stage_id: str
    name: str
    status: str
    summary: str
    preview: dict[str, Any] = field(default_factory=dict)
    evidence: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = list(self.evidence)
        data["preview"] = _sanitize(self.preview)
        data["raw"] = _sanitize(self.raw)
        return data


@dataclass(frozen=True)
class AlgorithmStage:
    algorithm_id: str
    name: str
    status: str
    summary: str
    input_count: int | None = None
    output_count: int | None = None
    reason: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _sanitize(asdict(self))


@dataclass(frozen=True)
class CandidateTrace:
    item_id: str
    title: str | None = None
    summary: str | None = None
    type: str | None = None
    project: str | None = None
    maturity: str | None = None
    final_rank: int | None = None
    final_score: float | None = None
    firewall_action: str = "defer"
    firewall_reasons: tuple[str, ...] = ()
    loaded_view: str | None = None
    score_trace: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["firewall_reasons"] = list(self.firewall_reasons)
        data["score_trace"] = _sanitize(self.score_trace)
        return _sanitize(data)


@dataclass(frozen=True)
class ChainSummary:
    chain_id: str
    adapter: str
    session_id: str | None
    cwd: str | None
    final_outcome: str
    injected_count: int
    rejected_count: int
    gap_reason: str | None
    completeness: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return _sanitize(asdict(self))


@dataclass(frozen=True)
class ChainDetail:
    chain_id: str
    adapter: str
    session_id: str | None
    cwd: str | None
    started_at: str
    completed_at: str | None
    final_outcome: str
    completeness: dict[str, Any]
    stages: tuple[ChainStage, ...]
    algorithm_trace: tuple[AlgorithmStage, ...]
    candidates: tuple[CandidateTrace, ...]
    evidence: tuple[str, ...]
    boundaries: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "adapter": self.adapter,
            "session_id": self.session_id,
            "cwd": self.cwd,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "final_outcome": self.final_outcome,
            "completeness": _sanitize(self.completeness),
            "stages": [stage.to_dict() for stage in self.stages],
            "algorithm_trace": [stage.to_dict() for stage in self.algorithm_trace],
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "evidence": list(self.evidence),
            "boundaries": list(self.boundaries),
        }


@dataclass(frozen=True)
class ChainLogReport:
    filters: dict[str, Any]
    summary: dict[str, Any]
    chains: tuple[ChainSummary, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "filters": _sanitize(self.filters),
            "summary": _sanitize(self.summary),
            "chains": [chain.to_dict() for chain in self.chains],
        }


@dataclass
class _ChainBucket:
    key: str
    anchor_id: str
    runtime_events: list[AdapterRuntimeEvent] = field(default_factory=list)
    injections: list[InjectionCohort] = field(default_factory=list)
    gaps: list[GapRecord] = field(default_factory=list)
    outcomes: list[TaskOutcome] = field(default_factory=list)


def build_chain_log_report(
    brain_dir: Path,
    hours: int = MAX_WINDOW_HOURS,
    limit: int = 100,
    adapter: str | None = None,
    session_id: str | None = None,
    cwd: str | None = None,
    status: str | None = None,
) -> ChainLogReport:
    """Build a request-chain report view for Web."""

    bounded_hours = _bounded_hours(hours)
    bounded_limit = _bounded_limit(limit)
    chains = _chain_details(brain_dir, hours=bounded_hours)
    if adapter:
        chains = [chain for chain in chains if chain.adapter == adapter]
    if session_id:
        chains = [chain for chain in chains if chain.session_id == session_id]
    if cwd:
        chains = [chain for chain in chains if _cwd_matches(chain.cwd, cwd)]
    if status:
        chains = [chain for chain in chains if chain.final_outcome == status]
    chains.sort(key=lambda chain: chain.started_at, reverse=True)
    bounded_chains = chains[:bounded_limit]
    return ChainLogReport(
        filters={
            "hours": bounded_hours,
            "limit": bounded_limit,
            "adapter": adapter,
            "session_id": session_id,
            "cwd": cwd,
            "status": status,
        },
        summary={
            "total_chains": len(bounded_chains),
            "by_outcome": dict(Counter(chain.final_outcome for chain in bounded_chains)),
            "by_adapter": dict(Counter(chain.adapter for chain in bounded_chains)),
        },
        chains=tuple(_summary_from_detail(chain) for chain in bounded_chains),
    )


def build_chain_log_detail(brain_dir: Path, chain_id: str, hours: int = MAX_WINDOW_HOURS) -> ChainDetail:
    for chain in _chain_details(brain_dir, hours=hours):
        if chain.chain_id == chain_id:
            return chain
    raise KeyError(chain_id)


def _chain_details(brain_dir: Path, *, hours: int) -> list[ChainDetail]:
    bounded_hours = _bounded_hours(hours)
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=bounded_hours)
    grouped: dict[str, list[tuple[datetime, str, Any]]] = {}

    for event in iter_runtime_events(Path(brain_dir)):
        parsed = _parse_time(event.timestamp)
        if parsed is None or not _within(event.timestamp, start, end=now):
            continue
        _grouped_records(grouped, _bucket_key(event.session_id, event.adapter, event.cwd)).append((parsed, "runtime", event))

    for cohort in iter_injection_cohorts(Path(brain_dir)):
        parsed = _parse_time(cohort.timestamp)
        if parsed is None or not _within(cohort.timestamp, start, end=now):
            continue
        _grouped_records(grouped, _bucket_key(cohort.session_id, cohort.adapter, cohort.cwd)).append((parsed, "injection", cohort))

    for gap in iter_gap_records(Path(brain_dir)):
        parsed = _parse_time(gap.timestamp)
        if parsed is None or not _within(gap.timestamp, start, end=now):
            continue
        _grouped_records(grouped, _bucket_key(gap.session_id, gap.adapter, gap.cwd)).append((parsed, "gap", gap))

    for outcome in iter_task_outcomes(Path(brain_dir)):
        parsed = _parse_time(outcome.timestamp)
        if parsed is None or not _within(outcome.timestamp, start, end=now):
            continue
        _grouped_records(grouped, _bucket_key(outcome.session_id, outcome.adapter, outcome.cwd)).append((parsed, "outcome", outcome))

    buckets: dict[str, _ChainBucket] = {}
    for scope_key, records in grouped.items():
        chain_buckets = _bucket_by_anchors(records, scope_key)
        for chain_key, bucket in chain_buckets.items():
            buckets[chain_key] = bucket

    item_meta = _items_by_id(Path(brain_dir))
    return [_detail_from_bucket(bucket, item_meta) for bucket in buckets.values()]


def _grouped_records(
    grouped: dict[str, list[tuple[datetime, str, Any]]],
    scope_key: str,
) -> list[tuple[datetime, str, Any]]:
    if scope_key not in grouped:
        grouped[scope_key] = []
    return grouped[scope_key]


def _bucket_by_anchors(
    records: list[tuple[datetime, str, Any]],
    scope_key: str,
) -> dict[str, _ChainBucket]:
    if not records:
        return {}

    records = sorted(records, key=lambda item: item[0])
    anchors: list[tuple[datetime, str]] = []
    buckets: dict[str, _ChainBucket] = {}

    for index, (timestamp, kind, row) in enumerate(records):
        if kind == "runtime" and row.event_name == "UserPromptSubmit":
            anchor_id = f"prompt-submit-{timestamp.timestamp()}-{index}"
            bucket = _bucket(buckets, _chain_bucket_key(scope_key, anchor_id), anchor_id)
            bucket.runtime_events.append(row)
            anchors.append((timestamp, anchor_id))
            continue

        anchor_id = _nearest_prompt_anchor(timestamp, anchors)
        if anchor_id is None:
            anchor_id = _orphan_anchor_id(timestamp, kind, row, index)
        bucket = _bucket(buckets, _chain_bucket_key(scope_key, anchor_id), anchor_id)
        if kind == "runtime":
            bucket.runtime_events.append(row)
        elif kind == "injection":
            bucket.injections.append(row)
        elif kind == "gap":
            bucket.gaps.append(row)
        elif kind == "outcome":
            bucket.outcomes.append(row)

    return buckets


def _nearest_prompt_anchor(
    timestamp: datetime,
    anchors: list[tuple[datetime, str]],
) -> str | None:
    for anchor_ts, anchor_id in reversed(anchors):
        if anchor_ts <= timestamp and timestamp - anchor_ts <= CHAIN_ANCHOR_WINDOW:
            return anchor_id
    return None


def _orphan_anchor_id(timestamp: datetime, kind: str, row: Any, index: int) -> str:
    identifier = "unknown"
    if kind == "runtime":
        identifier = row.event_name
    elif kind == "injection":
        identifier = row.cohort_id
    elif kind == "gap":
        identifier = row.gap_id
    elif kind == "outcome":
        identifier = row.outcome_id
    return f"orphan-{kind}:{identifier}:{timestamp.isoformat()}:{index}"


def _detail_from_bucket(bucket: _ChainBucket, item_meta: dict[str, Any]) -> ChainDetail:
    adapter = _first_present(
        [event.adapter for event in bucket.runtime_events],
        [cohort.adapter for cohort in bucket.injections],
        [gap.adapter for gap in bucket.gaps],
        [outcome.adapter for outcome in bucket.outcomes],
    ) or "unknown"
    session_id = _first_present(
        [event.session_id for event in bucket.runtime_events],
        [cohort.session_id for cohort in bucket.injections],
        [gap.session_id for gap in bucket.gaps],
        [outcome.session_id for outcome in bucket.outcomes],
    )
    cwd = _first_present(
        [event.cwd for event in bucket.runtime_events],
        [cohort.cwd for cohort in bucket.injections],
        [gap.cwd for gap in bucket.gaps],
        [outcome.cwd for outcome in bucket.outcomes],
    )

    timestamps = [event.timestamp for event in bucket.runtime_events]
    timestamps.extend(cohort.timestamp for cohort in bucket.injections)
    timestamps.extend(gap.timestamp for gap in bucket.gaps)
    timestamps.extend(outcome.timestamp for outcome in bucket.outcomes)
    parsed = [_parse_time(value) for value in timestamps]
    parsed = [value for value in parsed if value is not None]
    started_at_dt = min(parsed) if parsed else datetime.now(timezone.utc)
    completed_at_dt = max(parsed) if len(parsed) > 1 else None
    started_at = started_at_dt.isoformat()
    completed_at = completed_at_dt.isoformat() if completed_at_dt else None

    final_outcome = _final_outcome(bucket)
    trace_by_item = _retrieval_trace_by_item(bucket)
    candidates = tuple(_candidate_traces(bucket, item_meta, trace_by_item))
    stages = tuple(_chain_stages(bucket, final_outcome))
    algorithms = tuple(_algorithm_stages(bucket, candidates, trace_by_item))
    completeness = _completeness(stages, algorithms, final_outcome)

    return ChainDetail(
        chain_id=_chain_id(session_id, adapter, cwd, started_at, bucket),
        adapter=adapter,
        session_id=session_id,
        cwd=cwd,
        started_at=started_at,
        completed_at=completed_at,
        final_outcome=final_outcome,
        completeness=completeness,
        stages=stages,
        algorithm_trace=algorithms,
        candidates=candidates,
        evidence=tuple(_evidence(bucket)),
        boundaries=(
            "Web chain logs expose sanitized metadata only; no raw prompt/query/question/body/tool arguments.",
            "A stage marked not_observed means we have no sidecar evidence for it.",
            "Algorithm stages are fixed by contract and rendered even when not_observed.",
        ),
    )


def _chain_stages(bucket: _ChainBucket, final_outcome: str) -> list[ChainStage]:
    runtime_count = len(bucket.runtime_events)
    injection_count = sum(len(cohort.item_ids) for cohort in bucket.injections)
    rejected_count = sum(len(gap.rejected_ids) for gap in bucket.gaps)
    gap_reason = bucket.gaps[-1].reason if bucket.gaps else None

    statuses = {
        "hook_capture": "passed" if runtime_count else "not_observed",
        "prompt_frame": "passed" if runtime_count or bucket.gaps or bucket.injections else "not_observed",
        "query_gate": (
            "blocked"
            if gap_reason == "query_not_injectable"
            else ("partial" if bucket.gaps else ("passed" if injection_count else "not_observed"))
        ),
        "retrieval": "passed" if injection_count or bucket.gaps else "not_observed",
        "context_firewall": (
            "partial" if rejected_count and injection_count else ("blocked" if rejected_count else ("passed" if injection_count else "not_observed"))
        ),
        "context_loading": "passed" if injection_count else "not_observed",
        "packing": "passed" if injection_count else "not_observed",
        "injection": "passed" if injection_count else ("blocked" if final_outcome == "blocked" else "not_observed"),
        "feedback": "passed" if bucket.outcomes else ("partial" if bucket.gaps else "not_observed"),
    }

    previews = {
        "hook_capture": {"events": runtime_count},
        "query_gate": {"gap_reason": gap_reason, "has_gap": bool(bucket.gaps)},
        "retrieval": {"injected_count": injection_count, "rejected_count": rejected_count},
        "context_firewall": {"rejected_count": rejected_count},
        "packing": {"pack_metrics": [_sanitize_pack_metrics(cohort.pack_metrics or {}) for cohort in bucket.injections]},
        "injection": {"cohort_count": len(bucket.injections), "item_count": injection_count},
        "feedback": {"outcomes": len(bucket.outcomes), "gaps": len(bucket.gaps)},
    }

    return [
        ChainStage(
            stage_id=stage_id,
            name=name,
            status=statuses[stage_id],
            summary=_stage_summary(stage_id, statuses[stage_id], injection_count, rejected_count, gap_reason),
            preview=previews.get(stage_id, {}),
            evidence=tuple(_stage_evidence(stage_id, bucket)),
        )
        for stage_id, name in STAGE_CONTRACT
    ]


def _retrieval_trace_by_item(bucket: _ChainBucket) -> dict[str, dict[str, Any]]:
    trace_by_item: dict[str, dict[str, Any]] = {}
    for cohort in bucket.injections:
        if not isinstance(cohort.pack_metrics, dict):
            continue
        retrieval_trace = cohort.pack_metrics.get("retrieval_trace")
        if isinstance(retrieval_trace, dict):
            trace_rows = retrieval_trace.items()
        elif isinstance(retrieval_trace, list):
            if len(retrieval_trace) != len(cohort.item_ids):
                # Ordered traces carry no item IDs. A cardinality mismatch
                # makes positional binding ambiguous, so fail closed.
                continue
            trace_rows = zip(cohort.item_ids, retrieval_trace)
        else:
            continue
        for item_id, trace in trace_rows:
            if not isinstance(trace, dict):
                continue
            sanitized = _sanitize_retrieval_trace(trace)
            if sanitized:
                trace_by_item[str(item_id)] = sanitized
    return trace_by_item


def _sanitize_pack_metrics(pack_metrics: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key in sorted(PACK_METRICS_ALLOWED_KEYS - {"retrieval_trace"}):
        if key not in pack_metrics:
            continue
        value = _sanitize_pack_metric_value(key, pack_metrics[key])
        if value not in (None, [], {}):
            sanitized[key] = value
    retrieval_trace = pack_metrics.get("retrieval_trace")
    if isinstance(retrieval_trace, dict):
        trace_by_item = {
            str(item_id): trace
            for item_id, raw_trace in retrieval_trace.items()
            if isinstance(raw_trace, dict)
            for trace in [_sanitize_retrieval_trace(raw_trace)]
            if trace
        }
        if trace_by_item:
            sanitized["retrieval_trace"] = trace_by_item
    elif isinstance(retrieval_trace, list):
        trace_rows = [
            trace
            for raw_trace in retrieval_trace
            if isinstance(raw_trace, dict)
            for trace in [_sanitize_retrieval_trace(raw_trace)]
            if trace
        ]
        if trace_rows:
            sanitized["retrieval_trace"] = trace_rows
    return _sanitize(sanitized)


def _sanitize_pack_metric_value(key: str, value: Any) -> Any:
    if key == "items":
        return _sanitize_pack_metric_items(value)
    if key == "trimmed_ids":
        return [str(item_id) for item_id in value if isinstance(item_id, str)] if isinstance(value, list) else []
    if key == "selected_views":
        return _sanitize_count_map(
            value,
            allowed_keys={"locator", "overview", "detail"},
        )
    if key == "excluded_reasons":
        return _sanitize_count_map(
            value,
            allowed_keys=set(INJECTION_EXCLUSION_REASONS),
        )
    if key in {
        "candidate_count",
        "compressed_count",
        "excluded_count",
        "included_count",
    }:
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    return value if isinstance(value, (bool, int, float, str)) else None


def _sanitize_count_map(
    value: Any,
    *,
    allowed_keys: set[str] | None = None,
) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    sanitized: dict[str, int] = {}
    for raw_key, raw_count in value.items():
        if not isinstance(raw_key, str):
            continue
        key = raw_key.strip().lower()
        if allowed_keys is not None and key not in allowed_keys:
            continue
        if allowed_keys is None and (
            not key
            or len(key) > 80
            or any(char not in RETRIEVAL_TRACE_SIGNAL_ALLOWED_CHARS for char in key)
        ):
            continue
        if not isinstance(raw_count, int) or isinstance(raw_count, bool) or raw_count < 0:
            continue
        sanitized[key] = raw_count
    return dict(sorted(sanitized.items()))


def _sanitize_pack_metric_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for row in value:
        if not isinstance(row, dict):
            continue
        item = {
            key: row[key]
            for key in sorted(PACK_METRIC_ITEM_ALLOWED_KEYS)
            if key in row and isinstance(row[key], (bool, int, float, str))
        }
        if item:
            items.append(item)
    return items


def _sanitize_retrieval_trace(trace: dict[str, Any]) -> dict[str, Any]:
    allowed: dict[str, Any] = {}
    for key in RETRIEVAL_TRACE_ALLOWED_KEYS:
        if key not in trace:
            continue
        if key == "stages":
            stages = trace.get("stages")
            if not isinstance(stages, list):
                continue
            sanitized_stages = [
                _sanitize_retrieval_trace_stage(stage)
                for stage in stages
                if isinstance(stage, dict)
            ]
            sanitized_stages = [stage for stage in sanitized_stages if stage]
            if sanitized_stages:
                allowed["stages"] = sanitized_stages
            continue
        if key == "signals":
            signals = _sanitize_retrieval_trace_signals(trace.get("signals"))
            if signals:
                allowed["signals"] = signals
            continue
        allowed[key] = trace[key]
    sanitized = _sanitize(allowed)
    return sanitized if isinstance(sanitized, dict) else {}


def _sanitize_retrieval_trace_signals(signals: Any) -> list[str]:
    if not isinstance(signals, (list, tuple, set)):
        return []
    safe_signals: list[str] = []
    for signal in signals:
        if not isinstance(signal, str):
            continue
        text = signal.strip().lower()
        if not text or len(text) > 80:
            continue
        if any(char not in RETRIEVAL_TRACE_SIGNAL_ALLOWED_CHARS for char in text):
            continue
        safe_signals.append(text)
    return _dedupe(safe_signals)


def _sanitize_retrieval_trace_stage(stage: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        key: stage[key]
        for key in RETRIEVAL_TRACE_STAGE_ALLOWED_KEYS
        if key in stage
    }
    sanitized = _sanitize(allowed)
    return sanitized if isinstance(sanitized, dict) else {}


def _algorithm_stages(
    bucket: _ChainBucket,
    candidates: tuple[CandidateTrace, ...],
    trace_by_item: dict[str, dict[str, Any]],
) -> list[AlgorithmStage]:
    injected_count = sum(len(cohort.item_ids) for cohort in bucket.injections)
    rejected_count = sum(len(gap.rejected_ids) for gap in bucket.gaps)
    candidate_count = len(candidates)
    trace_statuses = _algorithm_statuses_from_retrieval_trace(trace_by_item)
    observed = {
        "metadata_filter": bool(bucket.injections or bucket.gaps),
        "bm25": bool(bucket.injections),
        "vector": bool(bucket.injections),
        "rrf": bool(bucket.injections),
        "cross_encoder": any(cohort.query_sha256 for cohort in bucket.injections),
        "retention": False,
        "decay_coefficient": False,
        "feedback_value": bool(bucket.outcomes or bucket.gaps),
        "runtime_status": bool(bucket.runtime_events),
        "temporal_supersession": bool(bucket.injections),
        "mmr": False,
        "hopfield": False,
        "graph_expansion": False,
        "budget_trim": any((cohort.pack_metrics or {}).get("trimmed_ids") for cohort in bucket.injections),
    }

    stages: list[AlgorithmStage] = []
    for algorithm_id, name in ALGORITHM_CONTRACT:
        status = trace_statuses.get(algorithm_id)
        if status is None:
            status = "applied" if observed.get(algorithm_id, False) else "not_observed"
        stages.append(
            AlgorithmStage(
                algorithm_id=algorithm_id,
                name=name,
                status=status,
                summary=_algorithm_summary(algorithm_id, status),
                input_count=candidate_count if candidate_count else None,
                output_count=(
                    injected_count
                    if algorithm_id in {"budget_trim", "rrf"} and status == "applied"
                    else None
                ),
                reason=(
                    None
                    if status in {"applied", "no_change"}
                    else "runtime sidecar has no structured evidence for this algorithm"
                ),
                metrics={
                    "injected_count": injected_count,
                    "rejected_count": rejected_count,
                },
            )
        )
    return stages


def _algorithm_statuses_from_retrieval_trace(trace_by_item: dict[str, dict[str, Any]]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for trace in trace_by_item.values():
        stages = trace.get("stages")
        if not isinstance(stages, list):
            continue
        for stage in stages:
            if not isinstance(stage, dict):
                continue
            name = str(stage.get("name") or "").strip().lower()
            effect = str(stage.get("effect") or "").strip().lower()
            if name == "mmr":
                if effect == "kept":
                    _mark_algorithm_status(statuses, "mmr", "no_change")
                elif effect in MMR_APPLIED_EFFECTS:
                    _mark_algorithm_status(statuses, "mmr", "applied")
            elif name == "decay":
                _mark_algorithm_status(statuses, "retention", "applied")
                _mark_algorithm_status(statuses, "decay_coefficient", "applied")
            elif name == "retention":
                _mark_algorithm_status(statuses, "retention", "applied")
            elif name == "decay_coefficient":
                _mark_algorithm_status(statuses, "decay_coefficient", "applied")
            elif name == "feedback_value":
                _mark_algorithm_status(statuses, "feedback_value", "applied")
            elif name in {"hopfield", "hopfield_expand"}:
                _mark_algorithm_status(statuses, "hopfield", "applied")
            elif name in {"graph", "graph_expand", "graph_expansion"}:
                _mark_algorithm_status(statuses, "graph_expansion", "applied")
    return statuses


def _mark_algorithm_status(statuses: dict[str, str], algorithm_id: str, status: str) -> None:
    if status == "applied" or algorithm_id not in statuses:
        statuses[algorithm_id] = status


def _candidate_traces(
    bucket: _ChainBucket,
    item_meta: dict[str, Any],
    trace_by_item: dict[str, dict[str, Any]],
) -> list[CandidateTrace]:
    injected = list(_dedupe(_iter_item_ids(cohort.item_ids for cohort in bucket.injections)))
    rejected_reasons: dict[str, list[str]] = {}

    for gap in bucket.gaps:
        for item_id in gap.rejected_ids:
            rejected_reasons.setdefault(item_id, []).append(gap.reason)
        for evidence in gap.evidence:
            if ":" in evidence:
                item_id, reason = evidence.split(":", 1)
                rejected_reasons.setdefault(item_id, []).append(reason)
            else:
                rejected_reasons.setdefault(evidence, []).append(gap.reason)

    rows: list[CandidateTrace] = []
    seen: set[str] = set()
    rank = 1
    for item_id in injected:
        seen.add(item_id)
        rows.append(
            _candidate(
                item_id,
                item_meta,
                action="include",
                rank=rank,
                score_trace=trace_by_item.get(item_id, {}),
            )
        )
        rank += 1

    for item_id, reasons in rejected_reasons.items():
        if item_id in seen:
            continue
        rows.append(
            _candidate(
                item_id,
                item_meta,
                action="exclude",
                reasons=tuple(_dedupe(reasons)),
                score_trace=trace_by_item.get(item_id, {}),
            )
        )
    return rows


def _candidate(
    item_id: str,
    item_meta: dict[str, Any],
    *,
    action: str,
    rank: int | None = None,
    reasons: tuple[str, ...] = (),
    score_trace: dict[str, Any] | None = None,
) -> CandidateTrace:
    item = item_meta.get(item_id)
    return CandidateTrace(
        item_id=item_id,
        title=getattr(item, "title", None),
        summary=getattr(item, "summary", None),
        type=str(getattr(item, "type", "")) if item else None,
        project=getattr(item, "project", None),
        maturity=str(getattr(item, "maturity", "")) if item else None,
        final_rank=rank,
        firewall_action=action,
        firewall_reasons=reasons,
        loaded_view="overview" if action == "include" else None,
        score_trace=score_trace or {},
    )


def _summary_from_detail(detail: ChainDetail) -> ChainSummary:
    return ChainSummary(
        chain_id=detail.chain_id,
        adapter=detail.adapter,
        session_id=detail.session_id,
        cwd=detail.cwd,
        final_outcome=detail.final_outcome,
        injected_count=sum(1 for candidate in detail.candidates if candidate.firewall_action == "include"),
        rejected_count=sum(1 for candidate in detail.candidates if candidate.firewall_action == "exclude"),
        gap_reason=next(
            (
                stage.preview.get("gap_reason")
                for stage in detail.stages
                if stage.stage_id == "query_gate"
            ),
            None,
        ),
        completeness=detail.completeness,
    )


def _items_by_id(brain: Path) -> dict[str, Any]:
    return {
        item.id: item
        for item, _body in ItemsStore(brain / "items").iter_all()
    }


def _bucket(buckets: dict[str, _ChainBucket], key: str, anchor_id: str) -> _ChainBucket:
    if key not in buckets:
        buckets[key] = _ChainBucket(key=key, anchor_id=anchor_id)
    return buckets[key]


def _bucket_key(session_id: str | None, adapter: str | None, cwd: str | None) -> str:
    return "|".join([session_id or "no-session", adapter or "unknown", cwd or "no-cwd"])


def _chain_bucket_key(scope_key: str, anchor_id: str) -> str:
    return f"{scope_key}|{anchor_id}"


def _chain_id(
    session_id: str | None,
    adapter: str,
    cwd: str | None,
    started_at: str,
    bucket: _ChainBucket,
) -> str:
    query_hash = next((cohort.query_sha256 for cohort in bucket.injections if cohort.query_sha256), None)
    seed = "|".join(
        [session_id or "", adapter, cwd or "", bucket.anchor_id or "", query_hash or started_at]
    )
    return "chain-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _final_outcome(bucket: _ChainBucket) -> str:
    injected_count = sum(len(cohort.item_ids) for cohort in bucket.injections)
    rejected_count = sum(len(gap.rejected_ids) for gap in bucket.gaps)
    if injected_count and rejected_count:
        return "partial"
    if injected_count:
        return "injected"
    if bucket.gaps:
        return "blocked"
    return "not_observed"


def _completeness(stages: tuple[ChainStage, ...], algorithms: tuple[AlgorithmStage, ...], outcome: str) -> dict[str, Any]:
    observed_stages = [stage for stage in stages if stage.status != "not_observed"]
    observed_algorithms = [stage for stage in algorithms if stage.status not in {"not_observed", "not_enabled"}]
    return {
        "expected_stage_count": len(STAGE_CONTRACT),
        "observed_stage_count": len(observed_stages),
        "missing_stage_ids": [stage.stage_id for stage in stages if stage.status == "not_observed"],
        "blocked_stage_id": next((stage.stage_id for stage in stages if stage.status == "blocked"), None),
        "final_outcome": outcome,
        "algorithm_expected_count": len(ALGORITHM_CONTRACT),
        "algorithm_observed_count": len(observed_algorithms),
        "evidence_quality": "complete" if len(observed_stages) == len(stages) else "partial",
    }


def _stage_summary(stage_id: str, status: str, injected_count: int, rejected_count: int, gap_reason: str | None) -> str:
    if stage_id == "query_gate" and gap_reason:
        return f"{status}: {gap_reason}"
    if stage_id == "retrieval":
        return f"{status}: injected={injected_count}, rejected={rejected_count}"
    return status


def _algorithm_summary(algorithm_id: str, status: str) -> str:
    if status == "applied":
        return f"{algorithm_id} applied with observed evidence"
    if status == "no_change":
        return f"{algorithm_id} observed with no ranking change"
    return f"{algorithm_id} not observed"


def _stage_evidence(stage_id: str, bucket: _ChainBucket) -> list[str]:
    if stage_id == "hook_capture":
        return [f"adapter-events:{event.event_name}" for event in bucket.runtime_events]
    if stage_id in {"packing", "injection"}:
        return [f"injection-cohorts:{cohort.cohort_id}" for cohort in bucket.injections]
    if stage_id in {"query_gate", "context_firewall", "feedback"}:
        return [f"recall-gaps:{gap.gap_id}" for gap in bucket.gaps]
    if stage_id == "retrieval":
        return [f"recall-gaps:{gap.gap_id}" for gap in bucket.gaps] + [
            f"injection-cohorts:{cohort.cohort_id}" for cohort in bucket.injections
        ]
    return []


def _evidence(bucket: _ChainBucket) -> list[str]:
    evidence: list[str] = []
    for stage_id, _name in STAGE_CONTRACT:
        evidence.extend(_stage_evidence(stage_id, bucket))
    return _dedupe(evidence)


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize(child)
            for key, child in value.items()
            if str(key).lower() not in REDACTED_KEYS
        }
    if isinstance(value, tuple):
        return [_sanitize(child) for child in value]
    if isinstance(value, list):
        return [_sanitize(child) for child in value]
    if isinstance(value, set):
        return [_sanitize(child) for child in value]
    return value


def _bounded_hours(hours: int) -> int:
    try:
        value = int(hours)
    except (TypeError, ValueError):
        return MAX_WINDOW_HOURS
    return max(1, min(value, MAX_WINDOW_HOURS))


def _bounded_limit(limit: int) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return 100
    return max(1, min(value, MAX_LIMIT))


def _within(timestamp: str, start: datetime, *, end: datetime | None = None) -> bool:
    end = end or datetime.now(timezone.utc)
    parsed = _parse_time(timestamp)
    return parsed is not None and start <= parsed <= end


def _parse_time(timestamp: str) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iter_item_ids(groups: Iterable[Iterable[str]]) -> list[str]:
    for group in groups:
        for item_id in group:
            yield item_id


def _first_present(*groups: Iterable[Any]) -> Any:
    for group in groups:
        for value in group:
            if value:
                return value
    return None


def _dedupe(values: Iterable[Any]) -> list[Any]:
    seen: set[Any] = set()
    result: list[Any] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _cwd_matches(chain_cwd: str | None, query: str) -> bool:
    if chain_cwd is None:
        return False
    return query in chain_cwd or chain_cwd.endswith(query)


def _chain_id_from_row(item: dict[str, Any] | Any) -> str:
    if isinstance(item, dict):
        return str(item.get("chain_id") or "")
    return str(item)


__all__ = [
    "ALGORITHM_CONTRACT",
    "STAGE_CONTRACT",
    "build_chain_log_detail",
    "build_chain_log_report",
]
