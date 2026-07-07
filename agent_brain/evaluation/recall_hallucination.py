"""Recall-hallucination gate for pre-injection memory context.

This gate uses synthetic, public-safe fixtures to exercise the same deterministic
path that protects user prompts from irrelevant memory injection:
normalization, query signal, retrieval, context firewall, and packing.
"""

from __future__ import annotations

import json
import tempfile
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall
from agent_brain.memory.context.context_packing import pack_decisions
from agent_brain.memory.context.prompt_normalization import normalize_hook_prompt_for_recall
from agent_brain.memory.context.query_signal import QuerySignal, analyze_injection_query
from agent_brain.memory.recall.embedding_text import embedding_text_for_item
from agent_brain.memory.recall.retrieval import Retriever
from agent_brain.memory.recall.retrieval_types import RetrievedItem
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex


NOW = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class RecallHallucinationCase:
    """One end-to-end case for detecting false context injection."""

    name: str
    query: str
    expected_ids: tuple[str, ...] = ()
    forbidden_ids: tuple[str, ...] = ()
    category: str = "synthetic"
    filters: dict[str, object] = field(default_factory=dict)

    @property
    def is_positive(self) -> bool:
        return bool(self.expected_ids)


@dataclass(frozen=True)
class RecallHallucinationReport:
    passed: bool
    metrics: dict[str, int | float]
    cases: list[dict[str, object]]
    failures: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "metrics": self.metrics,
            "cases": self.cases,
            "failures": self.failures,
        }


def run_recall_hallucination_gate(
    *,
    brain_dir: Path | None = None,
    items: list[tuple[MemoryItem, str]] | None = None,
    cases: list[RecallHallucinationCase] | None = None,
    top_k: int = 8,
    max_false_injection_rate: float = 0.0,
    min_positive_recall_rate: float = 1.0,
) -> RecallHallucinationReport:
    """Run the public-safe recall-hallucination gate.

    ``brain_dir`` is only used for the synthetic fixture store and query
    metadata anchors. When omitted, a temporary brain is created and removed.
    """

    loaded_items = items or load_builtin_items()
    loaded_cases = cases or load_builtin_cases()
    with _brain_context(brain_dir) as run_brain_dir:
        rows = _run_cases(run_brain_dir, loaded_items, loaded_cases, top_k=top_k)
    metrics = _metrics(rows, loaded_cases)
    failures = _failures(
        rows,
        metrics,
        max_false_injection_rate=max_false_injection_rate,
        min_positive_recall_rate=min_positive_recall_rate,
    )
    return RecallHallucinationReport(
        passed=not failures,
        metrics=metrics,
        cases=rows,
        failures=failures,
    )


def load_builtin_items() -> list[tuple[MemoryItem, str]]:
    """Return synthetic fixture items with no real personal data."""

    created_at = datetime.now(timezone.utc)
    return [
        _item(
            "eval-alpha-capsule",
            MemoryType.artifact,
            "Alpha project memory capsule",
            "Alpha project onboarding plan with task matrix and module routing.",
            body="Alpha project memory capsule with task matrix, module routing, and public synthetic details.",
            tags=["alpha-project", "task-matrix", "module-routing"],
            created_at=created_at,
        ),
        _item(
            "eval-beta-runtime",
            MemoryType.fact,
            "Beta runtime environment setup",
            "Beta runtime toolchain and module cache verification.",
            body="Beta runtime setup verified with toolchain, workspace cache, and module cache checks.",
            tags=["beta-runtime", "toolchain", "module-cache"],
            created_at=created_at,
        ),
        _item(
            "eval-generic-labels",
            MemoryType.artifact,
            "通用短标签讨论",
            "甲类、乙类、通过这类短标签的讨论样例。",
            body="只包含甲类、乙类、通过、选择等低信息量标签，不代表任何真实业务方案。",
            tags=["甲类", "乙类", "泛词"],
            created_at=created_at,
        ),
        _item(
            "eval-generic-install",
            MemoryType.artifact,
            "Generic installation checklist",
            "Install and configure checklist without a concrete toolchain anchor.",
            body="Generic install configure verify checklist for broad operational notes.",
            tags=["install", "configure", "generic"],
            created_at=created_at,
        ),
        _item(
            "eval-attachment-placeholder",
            MemoryType.episode,
            "Attachment placeholder capture note",
            "Attachment placeholder handling without extracted text.",
            body="A placeholder-only attachment prompt should not become recall evidence without extraction provenance.",
            tags=["attachment", "placeholder", "multimodal"],
            created_at=created_at,
        ),
        _item(
            "eval-recall-domain",
            MemoryType.artifact,
            "Recall pipeline architecture note",
            "Query signal, retrieval, firewall, and packing are the pre-injection stages.",
            body="Recall pipeline architecture: query signal, retrieval, firewall, context packing.",
            tags=["recall", "firewall", "context"],
            created_at=created_at,
        ),
        _item(
            "eval-agent-memory-metrics",
            MemoryType.artifact,
            "AMH agent-memory metrics evaluation report",
            "Agent memory metrics and benchmark readiness evaluation.",
            body="Synthetic agent memory metrics note that must not be injected for unrelated mixed-language topics.",
            tags=["agent", "memory", "metrics"],
            created_at=created_at,
        ),
    ]


def load_builtin_cases() -> list[RecallHallucinationCase]:
    """Return public-safe cases that model false-injection failure modes."""

    alpha_id = _item_id("eval-alpha-capsule")
    beta_id = _item_id("eval-beta-runtime")
    generic_label_id = _item_id("eval-generic-labels")
    generic_install_id = _item_id("eval-generic-install")
    attachment_id = _item_id("eval-attachment-placeholder")
    recall_id = _item_id("eval-recall-domain")
    agent_metrics_id = _item_id("eval-agent-memory-metrics")
    return [
        RecallHallucinationCase(
            name="weak-followup",
            query="...",
            forbidden_ids=(alpha_id, beta_id, generic_label_id, generic_install_id, attachment_id, recall_id),
            category="negative_weak_intent",
        ),
        RecallHallucinationCase(
            name="generic-cjk-noise",
            query=(
                "我想调整几个条目，并按顺序考虑若干选项，然后补齐能力"
            ),
            forbidden_ids=(generic_label_id, generic_install_id, attachment_id, recall_id),
            category="negative_generic_terms",
        ),
        RecallHallucinationCase(
            name="attachment-placeholder-noise",
            query='<attachment name="[Attachment #1]" path="/tmp/synthetic-placeholder.bin">placeholder preview</attachment>',
            forbidden_ids=(attachment_id, generic_install_id, generic_label_id),
            category="negative_multimodal_placeholder",
        ),
        RecallHallucinationCase(
            name="explicit-ascii-without-anchor",
            query="standalone",
            forbidden_ids=(alpha_id, beta_id, generic_label_id, generic_install_id, attachment_id, recall_id),
            category="negative_missing_metadata_anchor",
        ),
        RecallHallucinationCase(
            name="recall-domain-question-without-exact-artifact",
            query="甲乙丙丁戊己庚辛壬癸",
            forbidden_ids=(alpha_id, beta_id, generic_label_id, generic_install_id, attachment_id),
            category="negative_domain_question",
        ),
        RecallHallucinationCase(
            name="mixed-agent-shared-brain-not-agent-metrics",
            query="多Agent共享第二大脑",
            forbidden_ids=(agent_metrics_id, recall_id, generic_install_id, generic_label_id),
            category="negative_mixed_generic_singleton",
        ),
        RecallHallucinationCase(
            name="short-mixed-agent-singleton-blocked",
            query="多Agent",
            forbidden_ids=(agent_metrics_id, recall_id, generic_install_id, generic_label_id),
            category="negative_mixed_generic_singleton",
        ),
        RecallHallucinationCase(
            name="metadata-backed-alpha-capsule",
            query="Alpha project memory capsule task matrix",
            expected_ids=(alpha_id,),
            forbidden_ids=(generic_label_id, generic_install_id, attachment_id),
            category="positive_metadata_anchor",
        ),
        RecallHallucinationCase(
            name="metadata-backed-beta-runtime",
            query="Beta runtime environment setup module cache",
            expected_ids=(beta_id,),
            forbidden_ids=(generic_install_id, generic_label_id, attachment_id),
            category="positive_metadata_anchor",
        ),
    ]


def write_report(path: Path, report: RecallHallucinationReport) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _run_cases(
    brain_dir: Path,
    items: list[tuple[MemoryItem, str]],
    cases: list[RecallHallucinationCase],
    *,
    top_k: int,
) -> list[dict[str, object]]:
    item_bodies = {item.id: (item, body) for item, body in items}
    _seed_store(brain_dir, items)
    embedder = HashingEmbedder()
    index = HubIndex(brain_dir / "recall-hallucination-index.db", embedding_dim=embedder.dim)
    try:
        for item, body in items:
            index.upsert(item, body, embedding=embedder.embed(embedding_text_for_item(item)))
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
        return [
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


def _run_case(
    brain_dir: Path,
    case: RecallHallucinationCase,
    retriever: Retriever,
    item_bodies: dict[str, tuple[MemoryItem, str]],
    *,
    top_k: int,
) -> dict[str, object]:
    normalized_query = normalize_hook_prompt_for_recall(case.query)
    signal = analyze_injection_query(normalized_query, brain_dir=brain_dir)
    retrieval_query = _retrieval_query(normalized_query, signal)
    hits: list[RetrievedItem] = []
    included_ids: list[str] = []
    excluded_ids: list[str] = []
    firewall_reasons: list[str] = []
    packed_ids: list[str] = []
    if signal.injectable:
        hits = retriever.search(retrieval_query, top_k=top_k, explain=True)
        candidates = [
            ContextCandidate(item=item_bodies[hit.id][0], body=item_bodies[hit.id][1], score=hit.score)
            for hit in hits
            if hit.id in item_bodies
        ]
        firewall_result = ContextFirewall().filter(
            candidates,
            query=normalized_query,
            query_signal=signal,
            max_items=top_k,
        )
        included_ids = [decision.candidate.item.id for decision in firewall_result.included]
        excluded_ids = [decision.candidate.item.id for decision in firewall_result.excluded]
        firewall_reasons = sorted(
            {reason for decision in firewall_result.decisions for reason in decision.reasons}
            | set(firewall_result.cohort_reasons)
        )
        packed = pack_decisions(firewall_result.included, requested="auto", budget_tokens=max(160, top_k * 80))
        packed_ids = [entry.pack.item_id for entry in packed.included]

    expected_ids = set(case.expected_ids)
    forbidden_ids = set(case.forbidden_ids)
    included_set = set(included_ids)
    expected_ids_included = expected_ids.issubset(included_set) if expected_ids else False
    forbidden_included_ids = sorted(forbidden_ids & included_set)
    negative_context_injected = not case.is_positive and bool(included_ids)
    false_context_injected = negative_context_injected or bool(forbidden_included_ids)
    passed = (
        not false_context_injected
        and (expected_ids_included if case.is_positive else not included_ids)
    )
    return {
        "name": case.name,
        "category": case.category,
        "passed": passed,
        "query": case.query,
        "normalized_query": normalized_query,
        "signal": _signal_row(signal),
        "retrieval_query": retrieval_query,
        "ranking": [hit.id for hit in hits],
        "included_ids": included_ids,
        "excluded_ids": excluded_ids,
        "packed_ids": packed_ids,
        "firewall_reasons": firewall_reasons,
        "expected_ids": list(case.expected_ids),
        "expected_ids_included": expected_ids_included,
        "forbidden_ids": list(case.forbidden_ids),
        "forbidden_included_ids": forbidden_included_ids,
        "false_context_injected": false_context_injected,
    }


def _metrics(
    rows: list[dict[str, object]],
    cases: list[RecallHallucinationCase],
) -> dict[str, int | float]:
    negative_names = {case.name for case in cases if not case.is_positive}
    positive_names = {case.name for case in cases if case.is_positive}
    false_injections = [
        row
        for row in rows
        if bool(row["false_context_injected"])
    ]
    positive_hits = [
        row
        for row in rows
        if row["name"] in positive_names and bool(row["expected_ids_included"])
    ]
    negative_clean = [
        row
        for row in rows
        if row["name"] in negative_names and not row["included_ids"]
    ]
    negative_count = len(negative_names)
    positive_count = len(positive_names)
    false_injection_rate = len(false_injections) / max(1, negative_count + positive_count)
    negative_clean_rate = len(negative_clean) / max(1, negative_count)
    positive_recall_rate = len(positive_hits) / max(1, positive_count)
    return {
        "case_count": len(rows),
        "negative_cases": negative_count,
        "positive_cases": positive_count,
        "false_injection_count": len(false_injections),
        "false_injection_rate": round(false_injection_rate, 6),
        "negative_clean_rate": round(negative_clean_rate, 6),
        "positive_recall_rate": round(positive_recall_rate, 6),
    }


def _failures(
    rows: list[dict[str, object]],
    metrics: dict[str, int | float],
    *,
    max_false_injection_rate: float,
    min_positive_recall_rate: float,
) -> list[str]:
    failures: list[str] = []
    if float(metrics["false_injection_rate"]) > max_false_injection_rate:
        failures.append(
            "false_injection_rate "
            f"{float(metrics['false_injection_rate']):.4f} > {max_false_injection_rate:.4f}"
        )
    if float(metrics["positive_recall_rate"]) < min_positive_recall_rate:
        failures.append(
            "positive_recall_rate "
            f"{float(metrics['positive_recall_rate']):.4f} < {min_positive_recall_rate:.4f}"
        )
    for row in rows:
        if not row["passed"]:
            failures.append(f"{row['name']}: recall hallucination gate failed")
    return failures


def _seed_store(brain_dir: Path, items: list[tuple[MemoryItem, str]]) -> None:
    (brain_dir / "items").mkdir(parents=True, exist_ok=True)
    store = ItemsStore(brain_dir / "items")
    for item, body in items:
        store.write(item, body)


@contextmanager
def _brain_context(brain_dir: Path | None) -> Iterator[Path]:
    if brain_dir is not None:
        brain_dir.mkdir(parents=True, exist_ok=True)
        (brain_dir / "items").mkdir(parents=True, exist_ok=True)
        with nullcontext(brain_dir) as path:
            yield path
        return
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        (path / "items").mkdir(parents=True, exist_ok=True)
        yield path


def _item(
    suffix: str,
    type_: MemoryType,
    title: str,
    summary: str,
    *,
    body: str,
    tags: list[str],
    created_at: datetime | None = None,
) -> tuple[MemoryItem, str]:
    item_id = _item_id(suffix)
    item = MemoryItem.model_validate({
        "id": item_id,
        "type": type_.value,
        "created_at": (created_at or NOW).isoformat(),
        "title": title,
        "summary": summary,
        "project": "agent-memory-hub",
        "tags": tags,
        "confidence": 0.86,
        "abstraction": "L1",
        "support_count": 2,
        "gain_score": 0.2,
        "refs": {"urls": [f"https://example.test/recall-hallucination/{suffix}"]},
        "context_views": {
            "locator": summary,
            "overview": f"{title}\n{summary}",
            "detail_uri": f"memory://items/{item_id}/body",
        },
    })
    return item, body


def _item_id(suffix: str) -> str:
    return f"mem-20260702-120000-{suffix}"


def _retrieval_query(query: str, signal: QuerySignal) -> str:
    if signal.terms:
        return f"{query} {' '.join(signal.terms)}".strip()
    return query


def _signal_row(signal: QuerySignal) -> dict[str, object]:
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
    }


__all__ = [
    "RecallHallucinationCase",
    "RecallHallucinationReport",
    "load_builtin_cases",
    "load_builtin_items",
    "run_recall_hallucination_gate",
    "write_report",
]
