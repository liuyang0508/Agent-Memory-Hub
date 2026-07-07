"""Small retrieval benchmark gate.

The gate evaluates expected memory IDs against a caller-provided search
function. CLI/Web integrations can wire this to Retriever without coupling
tests to a specific index implementation.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class RetrievalCase:
    query: str
    expected_ids: list[str]
    weight: float = 1.0


@dataclass(frozen=True)
class RetrievalGateReport:
    passed: bool
    metrics: dict[str, float]
    cases: list[dict[str, object]]
    failures: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "metrics": self.metrics,
            "cases": self.cases,
            "failures": self.failures,
        }


SearchFn = Callable[[str, int], list[str]]


def evaluate_rankings(
    cases: list[RetrievalCase],
    search: SearchFn,
    *,
    top_k: int = 10,
    min_recall_at_1: float = 0.6,
    min_mrr: float = 0.6,
) -> RetrievalGateReport:
    rows: list[dict[str, object]] = []
    weighted_total = sum(max(case.weight, 0.0) for case in cases) or 1.0
    recall_at_1 = 0.0
    recall_at_k = 0.0
    mrr = 0.0
    for case in cases:
        ranking = search(case.query, top_k)
        rank = _first_rank(ranking, case.expected_ids)
        weight = max(case.weight, 0.0)
        if rank == 1:
            recall_at_1 += weight
        if rank is not None and rank <= top_k:
            recall_at_k += weight
            mrr += weight * (1.0 / rank)
        rows.append(
            {
                "query": case.query,
                "expected_ids": list(case.expected_ids),
                "ranking": ranking,
                "rank": rank,
                "weight": case.weight,
            }
        )
    metrics = {
        "recall_at_1": round(recall_at_1 / weighted_total, 6),
        f"recall_at_{top_k}": round(recall_at_k / weighted_total, 6),
        "mrr": round(mrr / weighted_total, 6),
    }
    failures = []
    if metrics["recall_at_1"] < min_recall_at_1:
        failures.append(f"recall_at_1 {metrics['recall_at_1']:.4f} < {min_recall_at_1:.4f}")
    if metrics["mrr"] < min_mrr:
        failures.append(f"mrr {metrics['mrr']:.4f} < {min_mrr:.4f}")
    return RetrievalGateReport(passed=not failures, metrics=metrics, cases=rows, failures=failures)


def load_cases(path: Path) -> list[RetrievalCase]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    raw_cases = payload.get("cases", payload if isinstance(payload, list) else [])
    return [RetrievalCase(**case) for case in raw_cases]


def write_report(path: Path, report: RetrievalGateReport) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")


def _first_rank(ranking: list[str], expected_ids: list[str]) -> int | None:
    expected = set(expected_ids)
    for index, item_id in enumerate(ranking, start=1):
        if item_id in expected:
            return index
    return None


__all__ = [
    "RetrievalCase",
    "RetrievalGateReport",
    "evaluate_rankings",
    "load_cases",
    "write_report",
]
