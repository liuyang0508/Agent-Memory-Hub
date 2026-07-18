"""Deterministic six-layer recall-quality report aggregation."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class RecallQualityObservation:
    case_id: str
    split: str
    adapter: str
    project_scope: str
    language: str
    category: str
    expected_item_ids: tuple[str, ...]
    allowed_item_ids: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    injected_ids: tuple[str, ...]
    prohibited_item_ids: tuple[str, ...]
    expected_admission: bool
    admission_allowed: bool
    admission_reason: str
    expected_answerability: str
    actual_answerability: str
    expected_temporal: str
    actual_temporal: str
    expected_abstention: bool
    actual_abstention: bool
    expected_injection: bool
    excluded_reasons: tuple[str, ...] = ()
    used_tokens: int = 0
    project_mismatch_count: int = 0


def build_recall_quality_report(
    observations: Iterable[RecallQualityObservation],
    *,
    corpus_sha256: dict[str, str],
    implementation_sha256: str,
    evaluation_now: str,
) -> dict[str, object]:
    rows = tuple(observations)
    if not rows:
        raise ValueError("recall quality observations must be non-empty")
    case_ids = [row.case_id for row in rows]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("recall quality case ids must be unique")
    required_splits = {"calibration", "heldout", "production_replay"}
    observed_splits = {row.split for row in rows}
    if observed_splits != required_splits:
        raise ValueError(
            f"recall quality report requires all three splits: {sorted(observed_splits)}"
        )
    for row in rows:
        if row.used_tokens < 0 or row.project_mismatch_count < 0:
            raise ValueError("recall quality counters must be non-negative")

    overall = _aggregate(rows)
    breakdowns = {
        dimension: {
            key: _aggregate(tuple(row for row in rows if getter(row) == key))
            for key in sorted({getter(row) for row in rows})
        }
        for dimension, getter in (
            ("split", lambda row: row.split),
            ("adapter", lambda row: row.adapter),
            ("project_scope", lambda row: row.project_scope),
            ("language", lambda row: row.language),
            ("category", lambda row: row.category),
        )
    }
    failed_gates = _failed_gates(overall, breakdowns["split"])
    return {
        "schema_version": 1,
        "status": "pass" if not failed_gates else "fail",
        "evaluation_now": evaluation_now,
        "implementation_sha256": implementation_sha256,
        "corpus_sha256": dict(sorted(corpus_sha256.items())),
        "case_count": len(rows),
        "failed_gates": failed_gates,
        "layers": overall,
        "breakdowns": breakdowns,
        "cases": [asdict(row) for row in rows],
    }


def _aggregate(rows: tuple[RecallQualityObservation, ...]) -> dict[str, object]:
    retrieval = _retrieval_metrics(rows)
    admission_tp = sum(row.expected_admission and row.admission_allowed for row in rows)
    admission_fp = sum(not row.expected_admission and row.admission_allowed for row in rows)
    admission_fn = sum(row.expected_admission and not row.admission_allowed for row in rows)
    answerability_mismatches = sum(
        row.expected_answerability != row.actual_answerability for row in rows
    )
    temporal_mismatches = sum(
        row.expected_temporal != row.actual_temporal for row in rows
    )
    abstention_tp = sum(row.expected_abstention and row.actual_abstention for row in rows)
    abstention_fp = sum(not row.expected_abstention and row.actual_abstention for row in rows)
    abstention_fn = sum(row.expected_abstention and not row.actual_abstention for row in rows)
    injection = _injection_metrics(rows)
    return {
        "retrieval": retrieval,
        "admission": {
            "case_count": len(rows),
            "tp": admission_tp,
            "fp": admission_fp,
            "fn": admission_fn,
            "reason_counts": dict(sorted(Counter(row.admission_reason for row in rows).items())),
        },
        "answerability": {
            "case_count": len(rows),
            "mismatch_count": answerability_mismatches,
            "expected_counts": _counts(row.expected_answerability for row in rows),
            "actual_counts": _counts(row.actual_answerability for row in rows),
        },
        "temporal": {
            "case_count": len(rows),
            "mismatch_count": temporal_mismatches,
            "expected_counts": _counts(row.expected_temporal for row in rows),
            "actual_counts": _counts(row.actual_temporal for row in rows),
        },
        "abstention": {
            "case_count": len(rows),
            "tp": abstention_tp,
            "fp": abstention_fp,
            "fn": abstention_fn,
            "precision": _ratio(abstention_tp, abstention_tp + abstention_fp),
            "recall": _ratio(abstention_tp, abstention_tp + abstention_fn),
        },
        "injection": injection,
    }


def _retrieval_metrics(rows: tuple[RecallQualityObservation, ...]) -> dict[str, object]:
    positive = [row for row in rows if row.expected_item_ids]
    reciprocal_ranks: list[float] = []
    recall_at: dict[int, int] = {1: 0, 5: 0, 10: 0}
    false_negatives = 0
    prohibited_candidates = 0
    for row in rows:
        expected = set(row.expected_item_ids)
        candidates = list(row.candidate_ids)
        prohibited_candidates += len(set(candidates) & set(row.prohibited_item_ids))
        if not expected:
            continue
        ranks = [index for index, item_id in enumerate(candidates, 1) if item_id in expected]
        reciprocal_ranks.append(1 / min(ranks) if ranks else 0.0)
        false_negatives += len(expected - set(candidates))
        for top_k in recall_at:
            if expected & set(candidates[:top_k]):
                recall_at[top_k] += 1
    denominator = len(positive)
    return {
        "case_count": len(rows),
        "positive_case_count": denominator,
        "recall_at_1": _ratio(recall_at[1], denominator),
        "recall_at_5": _ratio(recall_at[5], denominator),
        "recall_at_10": _ratio(recall_at[10], denominator),
        "mrr": sum(reciprocal_ranks) / denominator if denominator else 1.0,
        "false_negative_item_count": false_negatives,
        "prohibited_candidate_count": prohibited_candidates,
    }


def _injection_metrics(rows: tuple[RecallQualityObservation, ...]) -> dict[str, object]:
    tp = fp = fn = prohibited = included = 0
    excluded_reasons: Counter[str] = Counter()
    for row in rows:
        expected = set(row.expected_item_ids) if row.expected_injection else set()
        injected = set(row.injected_ids)
        allowed = expected | set(row.allowed_item_ids)
        tp += len(expected & injected)
        fp += len(injected - allowed)
        fn += len(expected - injected)
        prohibited += len(injected & set(row.prohibited_item_ids))
        included += len(injected)
        excluded_reasons.update(row.excluded_reasons)
    return {
        "case_count": len(rows),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "prohibited_injection_count": prohibited,
        "included_count": included,
        "excluded_reason_counts": dict(sorted(excluded_reasons.items())),
        "used_tokens": sum(row.used_tokens for row in rows),
        "project_mismatch_count": sum(row.project_mismatch_count for row in rows),
    }


def _failed_gates(
    overall: dict[str, object],
    split_metrics: dict[str, dict[str, object]],
) -> list[str]:
    failed: list[str] = []
    for split, layers in split_metrics.items():
        injection = layers["injection"]
        if injection["fp"] or injection["fn"] or injection["prohibited_injection_count"]:
            failed.append(f"{split}:injection")
        admission = layers["admission"]
        if admission["fp"] or admission["fn"]:
            failed.append(f"{split}:admission")
        if layers["answerability"]["mismatch_count"]:
            failed.append(f"{split}:answerability")
        if layers["temporal"]["mismatch_count"]:
            failed.append(f"{split}:temporal")
        abstention = layers["abstention"]
        if abstention["fp"] or abstention["fn"]:
            failed.append(f"{split}:abstention")
    if overall["injection"]["prohibited_injection_count"]:
        failed.append("overall:prohibited_injection")
    return sorted(set(failed))


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


__all__ = ["RecallQualityObservation", "build_recall_quality_report"]
