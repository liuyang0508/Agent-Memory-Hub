"""Neutral sanitizers for injection telemetry exposed by product read models."""

from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache
from typing import Any, Iterable

from agent_brain.memory.context.injection_gateway import (
    HYDRATE_ERROR_REASON,
    INJECTION_EXCLUSION_REASONS,
)

MAX_SAFE_INTEGER = 2**53 - 1
INJECTION_SOURCES = frozenset({"search"})
PACK_METRICS_ALLOWED_KEYS = {
    "candidate_count",
    "compressed_count",
    "context_pack_chars",
    "detail_refs",
    "excluded_count",
    "excluded_reasons",
    "full_tokens",
    "gateway_candidate_count",
    "hydrate_error_count",
    "included_count",
    "items",
    "packed_tokens",
    "query_terms_count",
    "raw_candidate_count",
    "retrieval_trace",
    "selected_views",
    "trimmed_count",
}
PACK_METRIC_NONNEGATIVE_INT_KEYS = {
    "candidate_count",
    "compressed_count",
    "context_pack_chars",
    "detail_refs",
    "excluded_count",
    "full_tokens",
    "gateway_candidate_count",
    "hydrate_error_count",
    "included_count",
    "packed_tokens",
    "query_terms_count",
    "raw_candidate_count",
    "trimmed_count",
}
PACK_METRIC_AGGREGATE_KEYS = frozenset({
    "candidate_count",
    "compressed_count",
    "excluded_count",
    "excluded_reasons",
    "gateway_candidate_count",
    "hydrate_error_count",
    "included_count",
    "raw_candidate_count",
    "selected_views",
})
PACK_METRIC_BASE_AGGREGATE_COUNT_KEYS = frozenset({
    "candidate_count",
    "excluded_count",
    "included_count",
})
PACK_METRIC_SURFACE_KEYS = frozenset({
    "gateway_candidate_count",
    "hydrate_error_count",
    "raw_candidate_count",
})
CONTEXT_PACK_VIEWS = frozenset({"locator", "overview", "detail"})
RETRIEVAL_TRACE_RANK_KEYS = frozenset({
    "initial_bm25_rank",
    "initial_vector_rank",
    "final_rank",
})
RETRIEVAL_TRACE_SCORE_KEYS = frozenset({"initial_score", "final_score"})
RETRIEVAL_TRACE_STAGE_RANK_KEYS = frozenset({"before_rank", "after_rank"})
RETRIEVAL_TRACE_STAGE_SCORE_KEYS = frozenset({"before_score", "after_score"})
RETRIEVAL_TRACE_STAGE_NAMES = frozenset({
    "cross_encoder_rerank",
    "decay",
    "decay_coefficient",
    "feedback_value",
    "graph",
    "graph_expand",
    "graph_expansion",
    "hopfield",
    "hopfield_expand",
    "metadata_phrase",
    "mmr",
    "retention",
    "runtime_evidence",
    "status_handoff_boost",
    "status_handoff_supplement",
    "supersession_filter",
    "temporal_state_filter",
})
RETRIEVAL_TRACE_STAGE_EFFECTS = frozenset({
    "added",
    "applied",
    "boosted",
    "changed",
    "demoted",
    "kept",
    "reranked",
    "reordered",
    "rescored",
})
_QUERY_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_COHORT_ID_PATTERN = re.compile(
    r"^inj-\d{8}T\d{6}(?:[+-]\d{4})?-[0-9a-f]{8}$"
)


def sanitize_injection_source(value: object) -> str:
    """Return a closed, non-sensitive injection source label."""

    return value if isinstance(value, str) and value in INJECTION_SOURCES else "unknown"


def sanitize_query_sha256(value: object) -> str | None:
    """Return only canonical lowercase SHA-256 fingerprints."""

    if isinstance(value, str) and _QUERY_SHA256_PATTERN.fullmatch(value):
        return value
    return None


def sanitize_cohort_id(value: object) -> str:
    """Return a canonical cohort ID or a stable non-revealing surrogate."""

    if isinstance(value, str) and _COHORT_ID_PATTERN.fullmatch(value):
        return value
    digest = hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()
    return f"inj-invalid-{digest[:16]}"


def sanitize_adapter_name(value: object) -> str:
    """Resolve registered canonical adapter names and fail closed otherwise."""

    if not isinstance(value, str) or value == "unknown":
        return "unknown"
    return _sanitize_adapter_name(value)


@lru_cache(maxsize=128)
def _sanitize_adapter_name(value: str) -> str:
    try:
        from agent_brain.agent_integrations.registry import resolve_adapter_name

        canonical, _alias = resolve_adapter_name(value)
    except Exception:  # noqa: BLE001 - public telemetry boundary fails closed
        return "unknown"
    return canonical


def sanitize_pack_metrics(
    pack_metrics: Any,
    *,
    cohort_item_ids: tuple[str, ...],
    allowed_item_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Return schema-valid metrics associated with allowed public item IDs."""

    if not isinstance(pack_metrics, dict):
        return {}
    original_ids = tuple(item_id for item_id in cohort_item_ids if isinstance(item_id, str))
    allowed = {
        item_id
        for item_id in (original_ids if allowed_item_ids is None else allowed_item_ids)
        if isinstance(item_id, str)
    }
    public_ids = tuple(dict.fromkeys(
        item_id for item_id in original_ids if item_id in allowed
    ))
    public_id_set = set(public_ids)
    sanitized: dict[str, Any] = {}
    independent_keys = (
        PACK_METRICS_ALLOWED_KEYS
        - PACK_METRIC_AGGREGATE_KEYS
        - {"retrieval_trace"}
    )
    for key in sorted(independent_keys):
        if key not in pack_metrics:
            continue
        value = _sanitize_pack_metric_value(
            key,
            pack_metrics[key],
            allowed_item_ids=public_id_set,
        )
        if value not in (None, [], {}):
            sanitized[key] = value
    aggregate = sanitize_pack_metric_aggregate_bundle(pack_metrics)
    if aggregate.get("included_count") == len(public_ids):
        sanitized.update(aggregate)
    if "trimmed_count" not in sanitized:
        legacy_trimmed_count = legacy_trimmed_count_from_ids(
            pack_metrics.get("trimmed_ids")
        )
        if legacy_trimmed_count:
            sanitized["trimmed_count"] = legacy_trimmed_count
    retrieval_trace = pack_metrics.get("retrieval_trace")
    if isinstance(retrieval_trace, dict):
        trace_by_item = {
            item_id: trace
            for item_id, raw_trace in retrieval_trace.items()
            if isinstance(item_id, str)
            and item_id in public_id_set
            and isinstance(raw_trace, dict)
            for trace in [sanitize_retrieval_trace(raw_trace)]
            if trace
        }
        if trace_by_item:
            sanitized["retrieval_trace"] = trace_by_item
    elif isinstance(retrieval_trace, list) and len(retrieval_trace) == len(original_ids):
        trace_rows: list[dict[str, Any]] = []
        valid = True
        for item_id, raw_trace in zip(original_ids, retrieval_trace):
            if item_id not in public_id_set:
                continue
            if not isinstance(raw_trace, dict):
                valid = False
                break
            trace_rows.append(sanitize_retrieval_trace(raw_trace))
        if valid and len(trace_rows) == len(public_ids) and any(trace_rows):
            sanitized["retrieval_trace"] = trace_rows
    return sanitized


def sanitize_pack_metric_aggregate_bundle(pack_metrics: Any) -> dict[str, Any]:
    """Validate an aggregate metrics bundle atomically."""

    if not isinstance(pack_metrics, dict):
        return {}
    present_keys = PACK_METRIC_AGGREGATE_KEYS & pack_metrics.keys()
    if not present_keys:
        return {}
    if not PACK_METRIC_BASE_AGGREGATE_COUNT_KEYS <= pack_metrics.keys():
        return {}

    candidate_count = pack_metrics["candidate_count"]
    included_count = pack_metrics["included_count"]
    excluded_count = pack_metrics["excluded_count"]
    if not all(
        _is_nonnegative_int(value)
        for value in (candidate_count, included_count, excluded_count)
    ):
        return {}
    if candidate_count != included_count + excluded_count:
        return {}

    sanitized: dict[str, Any] = {
        "candidate_count": candidate_count,
        "excluded_count": excluded_count,
        "included_count": included_count,
    }

    selected_views: dict[str, int] | None = None
    if "selected_views" in pack_metrics:
        selected_views = _sanitize_strict_count_map(
            pack_metrics["selected_views"],
            allowed_keys=CONTEXT_PACK_VIEWS,
        )
        if selected_views is None or sum(selected_views.values()) != included_count:
            return {}
        if selected_views:
            sanitized["selected_views"] = selected_views

    if "compressed_count" in pack_metrics:
        compressed_count = pack_metrics["compressed_count"]
        if not _is_nonnegative_int(compressed_count) or compressed_count > included_count:
            return {}
        sanitized["compressed_count"] = compressed_count

    excluded_reasons: dict[str, int] | None = None
    if "excluded_reasons" in pack_metrics:
        excluded_reasons = _sanitize_strict_count_map(
            pack_metrics["excluded_reasons"],
            allowed_keys=INJECTION_EXCLUSION_REASONS,
        )
        if excluded_reasons is None:
            return {}
        if excluded_reasons:
            sanitized["excluded_reasons"] = excluded_reasons

    present_surface_keys = PACK_METRIC_SURFACE_KEYS & pack_metrics.keys()
    if present_surface_keys:
        if present_surface_keys != PACK_METRIC_SURFACE_KEYS:
            return {}
        raw_candidate_count = pack_metrics["raw_candidate_count"]
        gateway_candidate_count = pack_metrics["gateway_candidate_count"]
        hydrate_error_count = pack_metrics["hydrate_error_count"]
        if not all(
            _is_nonnegative_int(value)
            for value in (
                raw_candidate_count,
                gateway_candidate_count,
                hydrate_error_count,
            )
        ):
            return {}
        if (
            candidate_count != raw_candidate_count
            or raw_candidate_count != gateway_candidate_count + hydrate_error_count
        ):
            return {}
        gateway_excluded_count = excluded_count - hydrate_error_count
        if gateway_excluded_count < 0:
            return {}
        if hydrate_error_count > 0:
            if (
                excluded_reasons is None
                or excluded_reasons.get(HYDRATE_ERROR_REASON) != hydrate_error_count
            ):
                return {}
        elif excluded_reasons is not None and HYDRATE_ERROR_REASON in excluded_reasons:
            return {}
        if not _nonhydrate_reasons_cover_partition(
            excluded_reasons,
            partition_count=gateway_excluded_count,
        ):
            return {}
        sanitized.update({
            "gateway_candidate_count": gateway_candidate_count,
            "hydrate_error_count": hydrate_error_count,
            "raw_candidate_count": raw_candidate_count,
        })
    else:
        if excluded_reasons is not None and HYDRATE_ERROR_REASON in excluded_reasons:
            return {}
        if not _nonhydrate_reasons_cover_partition(
            excluded_reasons,
            partition_count=excluded_count,
        ):
            return {}

    return sanitized


def sanitize_retrieval_trace(trace: dict[str, Any]) -> dict[str, Any]:
    """Return the closed, bounded schema for one retrieval trace row."""

    allowed: dict[str, Any] = {}
    for key in RETRIEVAL_TRACE_RANK_KEYS:
        if _is_nonnegative_int(trace.get(key)):
            allowed[key] = trace[key]
    for key in RETRIEVAL_TRACE_SCORE_KEYS:
        score = _finite_number(trace.get(key))
        if score is not None:
            allowed[key] = score
    stages = trace.get("stages")
    if isinstance(stages, list):
        sanitized_stages = [
            _sanitize_retrieval_trace_stage(stage)
            for stage in stages
            if isinstance(stage, dict)
        ]
        sanitized_stages = [stage for stage in sanitized_stages if stage]
        if sanitized_stages:
            allowed["stages"] = sanitized_stages
    signals = _sanitize_retrieval_trace_signals(trace.get("signals"))
    if signals:
        allowed["signals"] = signals
    return allowed


def _sanitize_pack_metric_value(
    key: str,
    value: Any,
    *,
    allowed_item_ids: set[str],
) -> Any:
    if key == "items":
        return _sanitize_pack_metric_items(value, allowed_item_ids=allowed_item_ids)
    if key in PACK_METRIC_NONNEGATIVE_INT_KEYS:
        return value if _is_nonnegative_int(value) else None
    return None


def legacy_trimmed_count_from_ids(value: Any) -> int:
    if not isinstance(value, list):
        return 0
    count = len({item_id for item_id in value if isinstance(item_id, str) and item_id})
    return count if _is_nonnegative_int(count) else 0


def _sanitize_strict_count_map(
    value: Any,
    *,
    allowed_keys: frozenset[str],
) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    sanitized: dict[str, int] = {}
    for raw_key, raw_count in value.items():
        if not isinstance(raw_key, str):
            return None
        key = raw_key.strip().lower()
        if key not in allowed_keys or key in sanitized:
            return None
        if not _is_nonnegative_int(raw_count) or raw_count == 0:
            return None
        sanitized[key] = raw_count
    return dict(sorted(sanitized.items()))


def _nonhydrate_reasons_cover_partition(
    excluded_reasons: dict[str, int] | None,
    *,
    partition_count: int,
) -> bool:
    if partition_count < 0:
        return False
    counts = [
        count
        for reason, count in (excluded_reasons or {}).items()
        if reason != HYDRATE_ERROR_REASON
    ]
    if any(count > partition_count for count in counts):
        return False
    if partition_count == 0:
        return not counts
    return sum(counts) >= partition_count


def _sanitize_pack_metric_items(
    value: Any,
    *,
    allowed_item_ids: set[str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for row in value:
        if not isinstance(row, dict):
            continue
        item_id = row.get("id")
        if not isinstance(item_id, str) or item_id not in allowed_item_ids:
            continue
        item: dict[str, Any] = {"id": item_id}
        selected_view = row.get("selected_view")
        if selected_view in CONTEXT_PACK_VIEWS:
            item["selected_view"] = selected_view
        for key in ("full_tokens", "packed_tokens"):
            if _is_nonnegative_int(row.get(key)):
                item[key] = row[key]
        compressed = row.get("compressed")
        if isinstance(compressed, bool):
            item["compressed"] = compressed
        items.append(item)
    return items


def _sanitize_retrieval_trace_signals(signals: Any) -> list[str]:
    if not isinstance(signals, (list, tuple, set)):
        return []
    safe_signals: list[str] = []
    for signal in signals:
        if not isinstance(signal, str):
            continue
        text = signal.strip().lower()
        if text in {"bm25", "vector"}:
            safe_signals.append(text)
            continue
        stage_name, separator, effect = text.partition(":")
        if (
            separator
            and stage_name in RETRIEVAL_TRACE_STAGE_NAMES
            and effect in RETRIEVAL_TRACE_STAGE_EFFECTS
        ):
            safe_signals.append(f"{stage_name}:{effect}")
    return list(dict.fromkeys(safe_signals))


def _sanitize_retrieval_trace_stage(stage: dict[str, Any]) -> dict[str, Any]:
    name = stage.get("name")
    effect = stage.get("effect")
    if not isinstance(name, str) or not isinstance(effect, str):
        return {}
    name = name.strip().lower()
    effect = effect.strip().lower()
    if (
        name not in RETRIEVAL_TRACE_STAGE_NAMES
        or effect not in RETRIEVAL_TRACE_STAGE_EFFECTS
    ):
        return {}
    allowed: dict[str, Any] = {"name": name, "effect": effect}
    for key in RETRIEVAL_TRACE_STAGE_RANK_KEYS:
        if _is_nonnegative_int(stage.get(key)):
            allowed[key] = stage[key]
    for key in RETRIEVAL_TRACE_STAGE_SCORE_KEYS:
        score = _finite_number(stage.get(key))
        if score is not None:
            allowed[key] = score
    return allowed


def is_safe_nonnegative_int(value: Any) -> bool:
    """Return whether a count/rank is non-negative and JavaScript-safe."""

    return type(value) is int and 0 <= value <= MAX_SAFE_INTEGER


_is_nonnegative_int = is_safe_nonnegative_int


def _finite_number(value: Any) -> int | float | None:
    if type(value) is int:
        return value if abs(value) <= MAX_SAFE_INTEGER else None
    if type(value) is not float:
        return None
    if not math.isfinite(value) or abs(value) > MAX_SAFE_INTEGER:
        return None
    return value


__all__ = [
    "MAX_SAFE_INTEGER",
    "legacy_trimmed_count_from_ids",
    "sanitize_adapter_name",
    "sanitize_cohort_id",
    "sanitize_injection_source",
    "sanitize_pack_metric_aggregate_bundle",
    "sanitize_pack_metrics",
    "sanitize_query_sha256",
    "sanitize_retrieval_trace",
    "is_safe_nonnegative_int",
]
