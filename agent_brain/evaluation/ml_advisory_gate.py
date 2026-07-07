"""Few-shot gate for ML/DL advisory features.

The gate deliberately evaluates ML/DL as an advisory candidate, not as a
production default. A strong score delta is insufficient unless retrieval,
compression, and privacy evidence are present.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MLAdvisoryCase:
    name: str
    baseline_score: float
    candidate_score: float
    candidate_mode: str = "advisory"
    required_gates: tuple[str, ...] = ("retrieval", "compression", "privacy")
    passed_gates: tuple[str, ...] = ()
    min_delta: float = 0.03
    expected_recommendation: str = "hold"
    expected_allows_default: bool = False
    max_latency_ms: float | None = 250.0
    candidate_latency_ms: float | None = 80.0
    privacy_mode: str = "local"

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "MLAdvisoryCase":
        data = dict(payload)
        data["required_gates"] = tuple(
            str(value) for value in data.get("required_gates", ()) or ()
        )
        data["passed_gates"] = tuple(str(value) for value in data.get("passed_gates", ()) or ())
        return cls(**data)


@dataclass(frozen=True)
class MLAdvisoryDecision:
    recommendation: str
    allows_default: bool
    delta: float
    risk: str
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "recommendation": self.recommendation,
            "allows_default": self.allows_default,
            "delta": round(self.delta, 6),
            "risk": self.risk,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class MLAdvisoryGateReport:
    passed: bool
    metrics: dict[str, float | int]
    cases: list[dict[str, object]]
    failures: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "metrics": self.metrics,
            "cases": self.cases,
            "failures": self.failures,
        }


def decide_ml_advisory(case: MLAdvisoryCase) -> MLAdvisoryDecision:
    """Return a deterministic decision for a model-backed candidate."""

    reasons: list[str] = []
    delta = case.candidate_score - case.baseline_score
    missing_gates = tuple(
        gate for gate in case.required_gates if gate not in set(case.passed_gates)
    )
    if missing_gates:
        reasons.append(f"missing required gates: {', '.join(missing_gates)}")
    if delta < case.min_delta:
        reasons.append(f"delta {delta:.4f} < min_delta {case.min_delta:.4f}")
    if case.privacy_mode not in {"local", "offline"}:
        reasons.append(f"privacy_mode {case.privacy_mode!r} is not local/offline")
    if (
        case.max_latency_ms is not None
        and case.candidate_latency_ms is not None
        and case.candidate_latency_ms > case.max_latency_ms
    ):
        reasons.append(
            f"latency {case.candidate_latency_ms:.1f}ms > {case.max_latency_ms:.1f}ms"
        )

    mode = case.candidate_mode.lower().strip()
    if mode == "default":
        reasons.append("default promotion is blocked until a separate release decision")
    if reasons:
        return MLAdvisoryDecision(
            recommendation="hold",
            allows_default=False,
            delta=delta,
            risk="high" if mode == "default" else "medium",
            reasons=tuple(reasons),
        )
    if mode == "advisory":
        return MLAdvisoryDecision(
            recommendation="advisory_only",
            allows_default=False,
            delta=delta,
            risk="low",
            reasons=("eligible only for advisory/report output",),
        )
    if mode == "experiment":
        return MLAdvisoryDecision(
            recommendation="eligible_for_experiment",
            allows_default=False,
            delta=delta,
            risk="medium",
            reasons=("eligible for opt-in experiment behind release gate",),
        )
    return MLAdvisoryDecision(
        recommendation="hold",
        allows_default=False,
        delta=delta,
        risk="medium",
        reasons=(f"unknown candidate_mode {case.candidate_mode!r}",),
    )


def evaluate_ml_advisory_cases(
    cases: list[MLAdvisoryCase],
    *,
    min_pass_rate: float = 1.0,
    max_unsafe_promotions: int = 0,
) -> MLAdvisoryGateReport:
    """Evaluate ML/DL advisory proposals against deterministic safety policy."""

    rows: list[dict[str, object]] = []
    failures: list[str] = []
    passed_count = 0
    delta_sum = 0.0
    advisory_count = 0
    experiment_count = 0
    hold_count = 0
    unsafe_promotion_count = 0

    for case in cases:
        decision = decide_ml_advisory(case)
        delta_sum += decision.delta
        if decision.recommendation == "advisory_only":
            advisory_count += 1
        if decision.recommendation == "eligible_for_experiment":
            experiment_count += 1
        if decision.recommendation == "hold":
            hold_count += 1
        if decision.allows_default:
            unsafe_promotion_count += 1

        case_failures: list[str] = []
        if decision.recommendation != case.expected_recommendation:
            case_failures.append(
                f"recommendation {decision.recommendation!r} != "
                f"{case.expected_recommendation!r}"
            )
        if decision.allows_default != case.expected_allows_default:
            case_failures.append(
                f"allows_default {decision.allows_default!r} != "
                f"{case.expected_allows_default!r}"
            )
        if not case_failures:
            passed_count += 1
        failures.extend(f"{case.name}: {failure}" for failure in case_failures)
        row = {
            "name": case.name,
            "passed": not case_failures,
            "baseline_score": round(case.baseline_score, 6),
            "candidate_score": round(case.candidate_score, 6),
            "candidate_mode": case.candidate_mode,
            "required_gates": list(case.required_gates),
            "passed_gates": list(case.passed_gates),
            "failures": case_failures,
        }
        row.update(decision.to_dict())
        rows.append(row)

    num_cases = len(cases)
    divisor = max(1, num_cases)
    pass_rate = passed_count / divisor
    metrics = {
        "num_cases": num_cases,
        "passed_cases": passed_count,
        "pass_rate": round(pass_rate, 6),
        "mean_delta": round(delta_sum / divisor, 6),
        "advisory_count": advisory_count,
        "experiment_count": experiment_count,
        "hold_count": hold_count,
        "unsafe_promotion_count": unsafe_promotion_count,
    }
    if pass_rate < min_pass_rate:
        failures.append(f"pass_rate {pass_rate:.4f} < {min_pass_rate:.4f}")
    if unsafe_promotion_count > max_unsafe_promotions:
        failures.append(
            f"unsafe_promotion_count {unsafe_promotion_count} > {max_unsafe_promotions}"
        )
    return MLAdvisoryGateReport(
        passed=not failures,
        metrics=metrics,
        cases=rows,
        failures=failures,
    )


def load_builtin_ml_advisory_cases() -> list[MLAdvisoryCase]:
    """Return few-shot cases that lock ML/DL into advisory/eval mode."""

    gates = ("retrieval", "compression", "privacy")
    return [
        MLAdvisoryCase(
            name="semantic-rerank-advisory-improves-query-fit",
            baseline_score=0.68,
            candidate_score=0.79,
            candidate_mode="advisory",
            passed_gates=gates,
            expected_recommendation="advisory_only",
        ),
        MLAdvisoryCase(
            name="cluster-labeler-experiment-after-gates",
            baseline_score=0.63,
            candidate_score=0.74,
            candidate_mode="experiment",
            passed_gates=gates,
            expected_recommendation="eligible_for_experiment",
        ),
        MLAdvisoryCase(
            name="default-reranker-blocked-even-with-good-delta",
            baseline_score=0.70,
            candidate_score=0.91,
            candidate_mode="default",
            passed_gates=gates,
            expected_recommendation="hold",
        ),
        MLAdvisoryCase(
            name="weak-delta-hold",
            baseline_score=0.72,
            candidate_score=0.735,
            candidate_mode="advisory",
            passed_gates=gates,
            min_delta=0.03,
            expected_recommendation="hold",
        ),
        MLAdvisoryCase(
            name="remote-model-without-privacy-gate-hold",
            baseline_score=0.66,
            candidate_score=0.84,
            candidate_mode="experiment",
            passed_gates=("retrieval", "compression"),
            privacy_mode="remote",
            expected_recommendation="hold",
        ),
    ]


def load_cases(path: Path) -> list[MLAdvisoryCase]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    raw_cases = payload.get("cases", payload if isinstance(payload, list) else [])
    return [MLAdvisoryCase.from_dict(case) for case in raw_cases]


def write_report(path: Path, report: MLAdvisoryGateReport) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")


__all__ = [
    "MLAdvisoryCase",
    "MLAdvisoryDecision",
    "MLAdvisoryGateReport",
    "decide_ml_advisory",
    "evaluate_ml_advisory_cases",
    "load_builtin_ml_advisory_cases",
    "load_cases",
    "write_report",
]
