"""System-level few-shot benchmark for memory injection readiness.

This gate checks the full deterministic path before a memory candidate reaches
an agent prompt: query gating, retrieval, firewall filtering, and reversible
context packing. It uses a temporary index so real brain data is read-only
during benchmark runs.
"""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from agent_brain.memory.context.context_firewall_rules import (
    REVIEW_REQUIRED_TAGS,
    SOURCE_REQUIRED_TYPES,
    age_days,
    has_source_refs,
    has_strong_negative_feedback,
    is_l0_evidence_only,
)
from agent_brain.memory.context.query_signal import analyze_injection_query

if TYPE_CHECKING:
    from agent_brain.contracts.memory_item import MemoryItem
    from agent_brain.memory.context.query_signal import QuerySignal
    from agent_brain.memory.recall.retrieval import Retriever, SearchFilter


DEFAULT_WEAK_PROMPTS = (
    "继续",
    "好的",
    "确认",
    "为什么",
    "再说说",
    "可以可以",
    "就像",
    "不不不",
    "然后呢",
    "看不懂",
    "不满意",
    "这些呢",
)


@dataclass(frozen=True)
class SystemBenchmarkCase:
    name: str
    query: str
    expected_decision: str
    expected_ids: tuple[str, ...] = ()
    category: str = "synthetic"
    weight: float = 1.0
    filters: dict[str, object] = field(default_factory=dict)
    expect_retrieval: bool = True
    expect_firewall_include: bool | None = None
    expect_pack_reversible: bool | None = None
    assert_firewall: bool = True

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "SystemBenchmarkCase":
        data = dict(payload)
        data["expected_ids"] = tuple(str(value) for value in data.get("expected_ids", ()) or ())
        data["filters"] = dict(data.get("filters", {}) or {})
        return cls(**data)


@dataclass(frozen=True)
class SystemBenchmarkReport:
    passed: bool
    metrics: dict[str, object]
    cases: list[dict[str, object]]
    failures: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "metrics": self.metrics,
            "cases": self.cases,
            "failures": self.failures,
        }


def load_cases(path: Path) -> list[SystemBenchmarkCase]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    raw_cases = payload.get("cases", payload if isinstance(payload, list) else [])
    if not isinstance(raw_cases, list):
        raise ValueError(f"system benchmark cases must be a list: {path}")
    return [SystemBenchmarkCase.from_dict(case) for case in raw_cases]


def write_report(path: Path, report: SystemBenchmarkReport) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def load_items(brain_dir: Path, *, max_items: int | None = None) -> list[tuple[MemoryItem, str]]:
    from agent_brain.memory.store.items_store import ItemsStore

    rows: list[tuple[MemoryItem, str]] = []
    store = ItemsStore(Path(brain_dir) / "items")
    for item, body in store.iter_all():
        rows.append((item, body))
        if max_items is not None and len(rows) >= max_items:
            break
    return rows


def build_synthetic_system_cases(
    items: list[tuple[MemoryItem, str]],
    *,
    max_cases: int = 100,
    weak_prompts: tuple[str, ...] = DEFAULT_WEAK_PROMPTS,
) -> list[SystemBenchmarkCase]:
    """Generate balanced few-shot cases from real MemoryItem metadata."""

    if max_cases <= 0:
        return []

    cases: list[SystemBenchmarkCase] = []
    for index, prompt in enumerate(weak_prompts, start=1):
        if len(cases) >= max_cases:
            return cases
        cases.append(
            SystemBenchmarkCase(
                name=f"weak-intent-{index:02d}",
                query=prompt,
                expected_decision="block",
                category="weak_intent_block",
            )
        )

    ordered_items = [
        *[
            (item, body)
            for item, body in items
            if _expected_retrieval(item, body) and _firewall_expectation(item) == "include"
        ],
        *[
            (item, body)
            for item, body in items
            if _expected_retrieval(item, body) and _firewall_expectation(item) == "exclude"
        ],
        *[
            (item, body)
            for item, body in items
            if _expected_retrieval(item, body) and _firewall_expectation(item) == "unasserted"
        ],
        *[
            (item, body)
            for item, body in items
            if not _expected_retrieval(item, body)
        ],
    ]

    for item, body in ordered_items:
        if len(cases) >= max_cases:
            break
        title = item.title.strip()
        if title:
            cases.append(
                SystemBenchmarkCase(
                    name=f"title-recall-{_short_id(item.id)}",
                    query=title,
                    expected_decision="inject",
                    expected_ids=(item.id,),
                    category="title_recall",
                    expect_retrieval=_expected_retrieval(item, body),
                    expect_firewall_include=_expected_firewall_include(item),
                    assert_firewall=_expected_retrieval(item, body) and _firewall_expectation(item) != "unasserted",
                    filters={"type": _field_value(item.type)},
                )
            )
        if len(cases) >= max_cases:
            break
        locator = (item.context_views.locator or item.summary or "").strip()
        if locator and locator != title:
            cases.append(
                SystemBenchmarkCase(
                    name=f"locator-recall-{_short_id(item.id)}",
                    query=locator,
                    expected_decision="inject",
                    expected_ids=(item.id,),
                    category="locator_recall",
                    expect_retrieval=_expected_retrieval(item, body),
                    expect_firewall_include=_expected_firewall_include(item),
                    assert_firewall=_expected_retrieval(item, body) and _firewall_expectation(item) != "unasserted",
                    filters={"type": _field_value(item.type)},
                )
            )

    return cases[:max_cases]


def run_system_benchmark(
    brain_dir: Path,
    cases: list[SystemBenchmarkCase],
    *,
    top_k: int = 10,
    max_items: int | None = None,
    min_block_accuracy: float = 0.98,
    min_inject_accuracy: float = 0.95,
    min_recall_at_k: float = 0.85,
    min_firewall_include_rate: float = 0.85,
    min_pack_reversible_rate: float = 1.0,
) -> SystemBenchmarkReport:
    from agent_brain.memory.recall.embedding_text import embedding_text_for_item
    from agent_brain.memory.recall.retrieval import Retriever
    from agent_brain.platform.embedding import HashingEmbedder
    from agent_brain.platform.indexing.index import HubIndex

    items = load_items(brain_dir, max_items=max_items)
    return run_system_benchmark_on_items(
        brain_dir,
        items,
        cases,
        top_k=top_k,
        min_block_accuracy=min_block_accuracy,
        min_inject_accuracy=min_inject_accuracy,
        min_recall_at_k=min_recall_at_k,
        min_firewall_include_rate=min_firewall_include_rate,
        min_pack_reversible_rate=min_pack_reversible_rate,
    )


def run_system_benchmark_on_items(
    brain_dir: Path,
    items: list[tuple[MemoryItem, str]],
    cases: list[SystemBenchmarkCase],
    *,
    top_k: int = 10,
    min_block_accuracy: float = 0.98,
    min_inject_accuracy: float = 0.95,
    min_recall_at_k: float = 0.85,
    min_firewall_include_rate: float = 0.85,
    min_pack_reversible_rate: float = 1.0,
) -> SystemBenchmarkReport:
    from agent_brain.memory.recall.embedding_text import embedding_text_for_item
    from agent_brain.memory.recall.retrieval import Retriever
    from agent_brain.platform.embedding import HashingEmbedder
    from agent_brain.platform.indexing.index import HubIndex

    item_bodies = {item.id: (item, body) for item, body in items}
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmpdir:
        embedder = HashingEmbedder()
        index = HubIndex(Path(tmpdir) / "system-benchmark-index.db", embedding_dim=embedder.dim)
        try:
            for item, body in items:
                index.upsert(item, body, embedding=embedder.embed(embedding_text_for_item(item)))
            index_build_time_s = time.perf_counter() - t0
            retriever = Retriever(
                index=index,
                embedder=embedder,
                record_access=False,
                rerank=False,
                bm25_top=max(50, top_k * 4),
                vector_top=max(50, top_k * 4),
                graph_expand=True,
                mmr_lambda=0.7,
            )
            rows = [
                _run_case(
                    brain_dir,
                    case,
                    retriever,
                    item_bodies,
                    top_k=top_k,
                )
                for case in cases
            ]
        finally:
            index.close()
    total_time_s = time.perf_counter() - t0
    metrics = _aggregate_metrics(
        rows,
        items_indexed=len(items),
        index_build_time_s=index_build_time_s,
        total_time_s=total_time_s,
        top_k=top_k,
    )
    failures = _gate_failures(
        metrics,
        min_block_accuracy=min_block_accuracy,
        min_inject_accuracy=min_inject_accuracy,
        min_recall_at_k=min_recall_at_k,
        min_firewall_include_rate=min_firewall_include_rate,
        min_pack_reversible_rate=min_pack_reversible_rate,
    )
    failures.extend(_case_failures(rows))
    return SystemBenchmarkReport(
        passed=not failures,
        metrics=metrics,
        cases=rows,
        failures=failures,
    )


def _run_case(
    brain_dir: Path,
    case: SystemBenchmarkCase,
    retriever: Retriever,
    item_bodies: dict[str, tuple[MemoryItem, str]],
    *,
    top_k: int,
) -> dict[str, object]:
    signal = analyze_injection_query(case.query, brain_dir=brain_dir)
    expected_decision_ok = _expected_decision_ok(case.expected_decision, signal)
    expected_firewall_include = (
        bool(case.expected_ids)
        if case.assert_firewall and case.expect_firewall_include is None
        else case.expect_firewall_include
    )
    expected_pack_reversible = (
        bool(expected_firewall_include)
        if case.expect_pack_reversible is None
        else case.expect_pack_reversible
    )
    retrieval_stage: dict[str, object] = {
        "skipped": not signal.injectable,
        "query": "",
        "ranking": [],
        "expected_retrieval": case.expect_retrieval,
        "expected_rank": None,
        "expected_found": False,
        "signals": [],
        "hits": [],
    }
    firewall_stage: dict[str, object] = {
        "skipped": True,
        "expected_included": False,
        "included_ids": [],
        "excluded_ids": [],
        "reasons": [],
        "expected_include": expected_firewall_include,
        "asserted": case.assert_firewall,
        "expected_outcome_ok": not case.assert_firewall,
    }
    context_stage: dict[str, object] = {
        "skipped": True,
        "expected_reversible": False,
        "expected_pack_reversible": expected_pack_reversible,
        "skipped_expected_exclusion": False,
        "packed_ids": [],
        "packed_tokens": 0,
        "full_tokens": 0,
    }

    hits = []
    expected_included = False
    firewall_outcome_ok = not case.assert_firewall or not bool(case.expected_ids)
    expected_reversible = False
    if signal.injectable:
        from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall
        from agent_brain.memory.context.context_packing import pack_decisions

        filters = _search_filter(case.filters)
        retrieval_query = _retrieval_query(case.query, signal)
        hits = retriever.search(retrieval_query, top_k=top_k, filters=filters, explain=True)
        ranking = [hit.id for hit in hits]
        expected_rank = _first_rank(ranking, case.expected_ids)
        retrieval_stage = {
            "skipped": False,
            "query": retrieval_query,
            "ranking": ranking,
            "expected_retrieval": case.expect_retrieval,
            "expected_rank": expected_rank,
            "expected_found": expected_rank is not None,
            "signals": _merged_trace_signals(hits),
            "hits": [_hit_row(hit) for hit in hits],
        }
        candidates = [
            ContextCandidate(item=item_bodies[hit.id][0], body=item_bodies[hit.id][1], score=hit.score)
            for hit in hits
            if hit.id in item_bodies
        ]
        firewall_result = ContextFirewall().filter(
            candidates,
            query=case.query,
            query_signal=signal,
            max_items=top_k,
        )
        included = firewall_result.included
        included_ids = [decision.candidate.item.id for decision in included]
        excluded_ids = [decision.candidate.item.id for decision in firewall_result.excluded]
        expected_included = bool(set(case.expected_ids) & set(included_ids)) if case.expected_ids else False
        if not case.assert_firewall:
            firewall_outcome_ok = True
        else:
            firewall_outcome_ok = (
                expected_included
                if expected_firewall_include
                else bool(case.expected_ids) and not expected_included
            )
        firewall_stage = {
            "skipped": False,
            "expected_included": expected_included,
            "expected_include": expected_firewall_include,
            "asserted": case.assert_firewall,
            "expected_outcome_ok": firewall_outcome_ok,
            "included_ids": included_ids,
            "excluded_ids": excluded_ids,
            "reasons": sorted({reason for decision in firewall_result.decisions for reason in decision.reasons}),
        }
        packed = pack_decisions(included, requested="auto", budget_tokens=max(120, top_k * 80))
        packed_ids = [entry.pack.item_id for entry in packed.included]
        expected_reversible = bool(set(case.expected_ids) & set(packed_ids)) and all(
            entry.pack.reversible for entry in packed.included if entry.pack.item_id in case.expected_ids
        )
        context_stage = {
            "skipped": False,
            "expected_reversible": expected_reversible,
            "expected_pack_reversible": expected_pack_reversible,
            "skipped_expected_exclusion": (
                case.assert_firewall
                and bool(case.expected_ids)
                and expected_firewall_include is False
                and not expected_included
            ),
            "packed_ids": packed_ids,
            "packed_tokens": packed.used_tokens,
            "full_tokens": packed.full_tokens,
            "items": [entry.pack.to_dict() for entry in packed.included],
        }

    return {
        "name": case.name,
        "category": case.category,
        "query": case.query,
        "expected_decision": case.expected_decision,
        "expected_ids": list(case.expected_ids),
        "expect_retrieval": case.expect_retrieval,
        "passed": _case_passed(
            case,
            expected_decision_ok=expected_decision_ok,
            retrieval_found=bool(retrieval_stage["expected_found"]),
            firewall_outcome_ok=firewall_outcome_ok,
            pack_reversible=expected_reversible,
            expected_firewall_include=expected_firewall_include,
            expected_pack_reversible=expected_pack_reversible,
        ),
        "stages": {
            "query_signal": _signal_row(signal, expected_decision_ok),
            "retrieval": retrieval_stage,
            "firewall": firewall_stage,
            "context_pack": context_stage,
        },
    }


def _search_filter(payload: dict[str, object]) -> SearchFilter:
    from agent_brain.memory.recall.retrieval import SearchFilter

    return SearchFilter(
        type=_optional_str(payload.get("type")),
        project=_optional_str(payload.get("project")),
        tags=[str(value) for value in payload.get("tags", []) or []],
        exclude_tags=[str(value) for value in payload.get("exclude_tags", []) or []],
        include_superseded=bool(payload.get("include_superseded", False)),
        include_stale_state=bool(payload.get("include_stale_state", False)),
    )


def _aggregate_metrics(
    rows: list[dict[str, object]],
    *,
    items_indexed: int,
    index_build_time_s: float,
    total_time_s: float,
    top_k: int,
) -> dict[str, object]:
    block_rows = [row for row in rows if row["expected_decision"] == "block"]
    inject_rows = [row for row in rows if row["expected_decision"] != "block"]
    retrieval_rows = [
        row for row in inject_rows
        if row["expected_ids"] and not row["stages"]["retrieval"]["skipped"]  # type: ignore[index]
        and row["stages"]["retrieval"]["expected_retrieval"]  # type: ignore[index]
    ]
    expected_include_rows = [
        row for row in retrieval_rows
        if row["stages"]["firewall"]["asserted"]  # type: ignore[index]
        and row["stages"]["firewall"]["expected_include"]  # type: ignore[index]
    ]
    expected_exclude_rows = [
        row for row in retrieval_rows
        if row["stages"]["firewall"]["asserted"]  # type: ignore[index]
        and not row["stages"]["firewall"]["expected_include"]  # type: ignore[index]
    ]
    unasserted_context_rows = [
        row for row in retrieval_rows
        if not row["stages"]["firewall"]["asserted"]  # type: ignore[index]
    ]
    found_rows = [
        row for row in retrieval_rows
        if row["stages"]["retrieval"]["expected_found"]  # type: ignore[index]
    ]
    firewall_rows = [
        row for row in expected_include_rows
        if row["stages"]["firewall"]["expected_included"]  # type: ignore[index]
    ]
    firewall_exclude_rows = [
        row for row in expected_exclude_rows
        if row["stages"]["firewall"]["expected_outcome_ok"]  # type: ignore[index]
    ]
    pack_rows = [
        row for row in expected_include_rows
        if row["stages"]["context_pack"]["expected_reversible"]  # type: ignore[index]
    ]
    reciprocal_sum = 0.0
    for row in retrieval_rows:
        rank = row["stages"]["retrieval"]["expected_rank"]  # type: ignore[index]
        if isinstance(rank, int) and rank > 0:
            reciprocal_sum += 1.0 / rank

    return {
        "case_count": len(rows),
        "items_indexed": items_indexed,
        "embedding_mode": "hashing_offline",
        "index_build_time_s": round(index_build_time_s, 6),
        "total_time_s": round(total_time_s, 6),
        "top_k": top_k,
        "query_gate": {
            "weak_block_cases": len(block_rows),
            "inject_cases": len(inject_rows),
            "block_accuracy": _ratio(
                [
                    row["stages"]["query_signal"]["expected_ok"]  # type: ignore[index]
                    for row in block_rows
                ]
            ),
            "inject_accuracy": _ratio(
                [
                    row["stages"]["query_signal"]["expected_ok"]  # type: ignore[index]
                    for row in inject_rows
                ]
            ),
        },
        "retrieval": {
            "retrieval_cases": len(retrieval_rows),
            "recall_at_k": _ratio([row in found_rows for row in retrieval_rows]),
            "mrr": round(reciprocal_sum / max(1, len(retrieval_rows)), 6),
        },
        "context": {
            "firewall_cases": len(retrieval_rows),
            "firewall_include_expected_cases": len(expected_include_rows),
            "firewall_exclude_expected_cases": len(expected_exclude_rows),
            "firewall_unasserted_cases": len(unasserted_context_rows),
            "firewall_include_rate": _ratio([row in firewall_rows for row in expected_include_rows]),
            "firewall_exclude_rate": _ratio([row in firewall_exclude_rows for row in expected_exclude_rows]),
            "packed_cases": len(expected_include_rows),
            "pack_reversible_rate": _ratio([row in pack_rows for row in expected_include_rows]),
        },
    }


def _gate_failures(
    metrics: dict[str, object],
    *,
    min_block_accuracy: float,
    min_inject_accuracy: float,
    min_recall_at_k: float,
    min_firewall_include_rate: float,
    min_pack_reversible_rate: float,
) -> list[str]:
    query_gate = metrics["query_gate"]
    retrieval = metrics["retrieval"]
    context = metrics["context"]
    failures = []
    checks = [
        ("block_accuracy", query_gate["block_accuracy"], min_block_accuracy),
        ("inject_accuracy", query_gate["inject_accuracy"], min_inject_accuracy),
        ("recall_at_k", retrieval["recall_at_k"], min_recall_at_k),
        ("firewall_include_rate", context["firewall_include_rate"], min_firewall_include_rate),
        ("pack_reversible_rate", context["pack_reversible_rate"], min_pack_reversible_rate),
    ]
    for name, actual, threshold in checks:
        if float(actual) < threshold:
            failures.append(f"{name} {float(actual):.4f} < {threshold:.4f}")
    return failures


def _case_failures(rows: list[dict[str, object]]) -> list[str]:
    failures = []
    for row in rows:
        if row["passed"]:
            continue
        retrieval = row["stages"]["retrieval"]  # type: ignore[index]
        firewall = row["stages"]["firewall"]  # type: ignore[index]
        context_pack = row["stages"]["context_pack"]  # type: ignore[index]
        failures.append(
            f"{row['name']}: decision={row['stages']['query_signal']['decision']} "  # type: ignore[index]
            f"rank={retrieval['expected_rank']} "
            f"firewall_expected={firewall['expected_include']} "
            f"firewall_ok={firewall['expected_outcome_ok']} "
            f"pack_ok={context_pack['expected_reversible']} "
            f"query={row['query']!r}"
        )
    return failures[:50]


def _case_passed(
    case: SystemBenchmarkCase,
    *,
    expected_decision_ok: bool,
    retrieval_found: bool,
    firewall_outcome_ok: bool,
    pack_reversible: bool,
    expected_firewall_include: bool,
    expected_pack_reversible: bool,
) -> bool:
    if not expected_decision_ok:
        return False
    if case.expected_decision == "block":
        return True
    if not case.expected_ids:
        return True
    if not case.expect_retrieval:
        return True
    if not retrieval_found:
        return False
    if not firewall_outcome_ok:
        return False
    if not case.assert_firewall:
        return True
    if not expected_firewall_include:
        return True
    if not expected_pack_reversible:
        return True
    return pack_reversible


def _expected_decision_ok(expected: str, signal: QuerySignal) -> bool:
    if expected == "block":
        return not signal.injectable
    if expected == "search_only":
        return signal.injectable and signal.decision == "search_only"
    if expected == "inject":
        return signal.injectable and signal.decision == "inject_allowed"
    return signal.injectable


def _signal_row(signal: QuerySignal, expected_ok: bool) -> dict[str, object]:
    return {
        "decision": "inject" if signal.decision == "inject_allowed" else signal.decision,
        "injectable": signal.injectable,
        "reason": signal.reason,
        "specificity": round(signal.specificity, 6),
        "terms": list(signal.terms),
        "strong_terms": list(signal.strong_terms),
        "weak_terms": list(signal.weak_terms),
        "anchors": list(signal.anchors),
        "trace": list(signal.trace),
        "expected_ok": expected_ok,
    }


def _retrieval_query(original_query: str, signal: QuerySignal) -> str:
    if signal.terms:
        return f"{original_query} {' '.join(signal.terms)}"
    return original_query


def _first_rank(ranking: list[str], expected_ids: tuple[str, ...]) -> int | None:
    expected = set(expected_ids)
    for index, item_id in enumerate(ranking, start=1):
        if item_id in expected:
            return index
    return None


def _merged_trace_signals(hits) -> list[str]:
    signals: list[str] = []
    seen: set[str] = set()
    for hit in hits:
        if hit.trace is None:
            continue
        for signal in hit.trace.signals:
            if signal not in seen:
                seen.add(signal)
                signals.append(signal)
    return signals


def _hit_row(hit) -> dict[str, object]:
    return {
        "id": hit.id,
        "score": round(hit.score, 8),
        "bm25_rank": hit.bm25_rank,
        "vector_rank": hit.vector_rank,
        "trace": hit.trace.to_dict() if hit.trace is not None else None,
    }


def _ratio(values: list[bool]) -> float:
    if not values:
        return 1.0
    return round(sum(1 for value in values if value) / len(values), 6)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _short_id(item_id: str) -> str:
    return item_id.replace("mem-", "")[-16:]


def _expected_firewall_include(item: MemoryItem) -> bool:
    return _firewall_expectation(item) == "include"


def _expected_retrieval(item: MemoryItem, body: str = "") -> bool:
    from agent_brain.memory.context.context_firewall import ContextFirewall
    from agent_brain.memory.governance.temporal_state import TemporalStateGate

    if item.superseded_by:
        return False
    item_type = _field_value(item.type)
    now = datetime.now(timezone.utc)
    config = ContextFirewall().config
    if item_type == "signal" and age_days(item.created_at, now) > config.stale_signal_days:
        return False
    if item_type == "handoff" and age_days(item.created_at, now) > config.stale_handoff_days:
        return False
    temporal = TemporalStateGate(now=now).evaluate(item, body)
    if temporal.status in {"scope_mismatch", "stale"}:
        return False
    return True


def _firewall_expectation(item: MemoryItem) -> str:
    """Mirror obvious firewall exclusions for generated real-data cases."""
    from agent_brain.memory.context.context_firewall import ContextFirewall

    sensitivity = _field_value(item.sensitivity)
    if sensitivity not in {"public", "internal"}:
        return "exclude"
    if item.superseded_by:
        return "exclude"
    tags = {tag.lower() for tag in item.tags}
    if REVIEW_REQUIRED_TAGS & tags:
        return "exclude"
    if item.confidence < 0.2:
        return "exclude"
    if has_strong_negative_feedback(item, ContextFirewall().config):
        return "exclude"
    item_type = _field_value(item.type)
    if item_type in SOURCE_REQUIRED_TYPES and not has_source_refs(item):
        return "exclude"
    now = datetime.now(timezone.utc)
    if item_type == "signal" and age_days(item.created_at, now) > ContextFirewall().config.stale_signal_days:
        return "exclude"
    if item_type == "handoff" and age_days(item.created_at, now) > ContextFirewall().config.stale_handoff_days:
        return "exclude"
    if _field_value(getattr(item, "abstraction", "")) == "L0":
        return "unasserted"
    if is_l0_evidence_only(item):
        return "unasserted"
    return "include"


def _field_value(value: object) -> str:
    return str(getattr(value, "value", value))


__all__ = [
    "DEFAULT_WEAK_PROMPTS",
    "SystemBenchmarkCase",
    "SystemBenchmarkReport",
    "build_synthetic_system_cases",
    "load_cases",
    "load_items",
    "run_system_benchmark",
    "run_system_benchmark_on_items",
    "write_report",
]
