"""Few-shot quality gate for adaptive context compression."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from agent_brain.memory.context.adaptive_compression import compress_text


@dataclass(frozen=True)
class CompressionCase:
    name: str
    text: str
    query: str | None = None
    budget_chars: int = 1200
    detail_uri: str | None = "memory://compression-gate/body"
    expected_content_type: str | None = None
    expected_strategy: str | None = None
    must_keep: tuple[str, ...] = ()
    must_drop: tuple[str, ...] = ()
    max_compression_ratio: float = 1.0
    min_tokens_saved: int = 0
    require_reversible: bool = True

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "CompressionCase":
        data = dict(payload)
        data["must_keep"] = tuple(str(value) for value in data.get("must_keep", ()) or ())
        data["must_drop"] = tuple(str(value) for value in data.get("must_drop", ()) or ())
        return cls(**data)


@dataclass(frozen=True)
class CompressionOutput:
    text: str
    content_type: str
    strategy: str
    original_tokens: int
    compressed_tokens: int
    compression_ratio: float
    reversible: bool

    @property
    def tokens_saved(self) -> int:
        return max(0, self.original_tokens - self.compressed_tokens)


@dataclass(frozen=True)
class CompressionGateReport:
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


class CompressionResultLike(Protocol):
    text: str
    content_type: str
    strategy: str
    original_tokens: int
    compressed_tokens: int
    compression_ratio: float
    reversible: bool
    tokens_saved: int


CompressorFn = Callable[[CompressionCase], CompressionResultLike]


def evaluate_compression_cases(
    cases: list[CompressionCase],
    *,
    compressor: CompressorFn | None = None,
    min_pass_rate: float = 1.0,
    max_mean_compression_ratio: float = 0.8,
    min_mean_tokens_saved: float = 1.0,
) -> CompressionGateReport:
    """Evaluate compression on few-shot cases and catch evidence-loss regressions."""

    run_compressor = compressor or _default_compressor
    rows: list[dict[str, object]] = []
    failures: list[str] = []
    total_required = 0
    total_kept = 0
    ratio_sum = 0.0
    tokens_saved_sum = 0.0
    passed_count = 0

    for case in cases:
        result = _normalize_output(run_compressor(case))
        case_failures = _case_failures(case, result)
        rows.append(
            {
                "name": case.name,
                "passed": not case_failures,
                "content_type": result.content_type,
                "strategy": result.strategy,
                "compression_ratio": result.compression_ratio,
                "tokens_saved": result.tokens_saved,
                "reversible": result.reversible,
                "failures": case_failures,
            }
        )
        if not case_failures:
            passed_count += 1
        failures.extend(f"{case.name}: {failure}" for failure in case_failures)
        total_required += len(case.must_keep)
        total_kept += sum(1 for anchor in case.must_keep if anchor in result.text)
        ratio_sum += result.compression_ratio
        tokens_saved_sum += result.tokens_saved

    num_cases = len(cases)
    divisor = max(1, num_cases)
    pass_rate = passed_count / divisor
    mean_ratio = ratio_sum / divisor
    mean_tokens_saved = tokens_saved_sum / divisor
    anchor_recall = total_kept / max(1, total_required)
    metrics = {
        "num_cases": num_cases,
        "passed_cases": passed_count,
        "pass_rate": round(pass_rate, 6),
        "anchor_recall": round(anchor_recall, 6),
        "mean_compression_ratio": round(mean_ratio, 6),
        "mean_tokens_saved": round(mean_tokens_saved, 6),
    }
    if pass_rate < min_pass_rate:
        failures.append(f"pass_rate {pass_rate:.4f} < {min_pass_rate:.4f}")
    if mean_ratio > max_mean_compression_ratio:
        failures.append(
            f"mean_compression_ratio {mean_ratio:.4f} > {max_mean_compression_ratio:.4f}"
        )
    if mean_tokens_saved < min_mean_tokens_saved:
        failures.append(
            f"mean_tokens_saved {mean_tokens_saved:.4f} < {min_mean_tokens_saved:.4f}"
        )
    return CompressionGateReport(
        passed=not failures,
        metrics=metrics,
        cases=rows,
        failures=failures,
    )


def load_builtin_compression_cases() -> list[CompressionCase]:
    """Return the built-in few-shot gate covering AMH's deterministic strategies."""

    return [
        CompressionCase(
            name="search-results-target-handler",
            text="\n".join(
                [
                    "src/app.py:10:def unrelated(): pass",
                    "src/app.py:20:def target_handler(): return True",
                    "src/app.py:30:target_handler()",
                    "src/app.py:40:print('noise')",
                    "tests/test_app.py:5:def test_target_handler(): pass",
                    "tests/test_app.py:20:assert target_handler() is True",
                    "README.md:8:general target documentation",
                    *(f"docs/noise.md:{i}:target filler {i}" for i in range(50, 70)),
                ]
            ),
            query="target handler",
            budget_chars=260,
            expected_content_type="search_results",
            expected_strategy="search_topn",
            must_keep=("src/app.py", "tests/test_app.py", "target_handler"),
            must_drop=("docs/noise.md:69",),
            max_compression_ratio=0.6,
            min_tokens_saved=1,
        ),
        CompressionCase(
            name="build-log-keeps-error-stack",
            text="\n".join(
                [
                    *(f"INFO task {i} completed" for i in range(40)),
                    "ERROR failed to connect to database",
                    "Traceback (most recent call last):",
                    '  File "app.py", line 12, in main',
                    "ConnectionError: refused",
                    "=== short test summary info ===",
                    "FAILED tests/test_db.py::test_connect - ConnectionError",
                    *(f"DEBUG retry noise {i}" for i in range(40)),
                ]
            ),
            budget_chars=280,
            expected_content_type="build_log",
            expected_strategy="log_errors",
            must_keep=("ERROR failed to connect", "Traceback", "FAILED tests/test_db.py"),
            must_drop=("DEBUG retry noise 39",),
            max_compression_ratio=0.55,
            min_tokens_saved=20,
        ),
        CompressionCase(
            name="git-diff-keeps-risky-change",
            text="\n".join(
                [
                    "diff --git a/app.py b/app.py",
                    "--- a/app.py",
                    "+++ b/app.py",
                    "@@ -10,8 +10,10 @@ def handle():",
                    *(f" context filler {i}" for i in range(30)),
                    "-    return safe_result",
                    '+    raise RuntimeError("bad token")',
                    "+    return unsafe_result",
                ]
            ),
            query="RuntimeError bad token",
            budget_chars=260,
            expected_content_type="git_diff",
            expected_strategy="diff_hunks",
            must_keep=("diff --git", 'raise RuntimeError("bad token")'),
            must_drop=("context filler 29",),
            max_compression_ratio=0.7,
            min_tokens_saved=5,
        ),
        CompressionCase(
            name="json-array-keeps-critical-row",
            text=json.dumps(
                [
                    {"id": "row-0", "summary": "routine note zero"},
                    {"id": "row-1", "summary": "low value noise one"},
                    {"id": "row-2", "summary": "critical token leak in adapter config"},
                    {"id": "row-3", "summary": "low value noise three"},
                    {"id": "row-4", "summary": "routine note four"},
                    {"id": "row-5", "summary": "routine note five"},
                    {"id": "row-6", "summary": "routine note six"},
                    {"id": "row-7", "summary": "routine note seven"},
                ],
                ensure_ascii=False,
            ),
            query="critical token leak",
            budget_chars=300,
            expected_content_type="json_array",
            expected_strategy="json_sample",
            must_keep=("critical token leak", '"omitted"'),
            max_compression_ratio=0.8,
            min_tokens_saved=1,
        ),
        CompressionCase(
            name="plain-text-keeps-decisions-and-blockers",
            text="\n".join(
                [
                    *(f"filler line {i}" for i in range(25)),
                    "Decision: keep compression deterministic until gate passes",
                    "Blocked: do not promote ML compression without eval evidence",
                    *(f"tail filler {i}" for i in range(25)),
                ]
            ),
            budget_chars=320,
            expected_content_type="plain_text",
            expected_strategy="important_lines",
            must_keep=("Decision:", "Blocked:"),
            must_drop=("filler line 20",),
            max_compression_ratio=0.55,
            min_tokens_saved=20,
        ),
    ]


def load_cases(path: Path) -> list[CompressionCase]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    raw_cases = payload.get("cases", payload if isinstance(payload, list) else [])
    return [CompressionCase.from_dict(case) for case in raw_cases]


def write_report(path: Path, report: CompressionGateReport) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")


def _default_compressor(case: CompressionCase) -> CompressionResultLike:
    return compress_text(
        case.text,
        budget_chars=case.budget_chars,
        detail_uri=case.detail_uri,
        query=case.query,
    )


def _normalize_output(result: CompressionResultLike) -> CompressionOutput:
    return CompressionOutput(
        text=result.text,
        content_type=result.content_type,
        strategy=result.strategy,
        original_tokens=result.original_tokens,
        compressed_tokens=result.compressed_tokens,
        compression_ratio=result.compression_ratio,
        reversible=result.reversible,
    )


def _case_failures(case: CompressionCase, result: CompressionOutput) -> list[str]:
    failures: list[str] = []
    for anchor in case.must_keep:
        if anchor not in result.text:
            failures.append(f"missing required anchor {anchor!r}")
    for noise in case.must_drop:
        if noise in result.text:
            failures.append(f"kept forbidden noise {noise!r}")
    if case.expected_content_type and result.content_type != case.expected_content_type:
        failures.append(
            f"content_type {result.content_type!r} != {case.expected_content_type!r}"
        )
    if case.expected_strategy and result.strategy != case.expected_strategy:
        failures.append(f"strategy {result.strategy!r} != {case.expected_strategy!r}")
    if result.compression_ratio > case.max_compression_ratio:
        failures.append(
            f"compression_ratio {result.compression_ratio:.4f} > {case.max_compression_ratio:.4f}"
        )
    if result.tokens_saved < case.min_tokens_saved:
        failures.append(f"tokens_saved {result.tokens_saved} < {case.min_tokens_saved}")
    if case.require_reversible and not result.reversible:
        failures.append("result is not reversible")
    if len(result.text) > case.budget_chars:
        failures.append(f"compressed text {len(result.text)} chars exceeds budget {case.budget_chars}")
    return failures


__all__ = [
    "CompressionCase",
    "CompressionGateReport",
    "CompressionOutput",
    "evaluate_compression_cases",
    "load_builtin_compression_cases",
    "load_cases",
    "write_report",
]
