from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.evaluation.recall_quality_corpus import (
    RecallQualityCase,
    load_recall_quality_corpus,
)
from agent_brain.memory.context.context_firewall_types import ContextCandidate
from agent_brain.memory.context.injection_gateway import build_injection_context
from agent_brain.memory.context.injection_query_context import InjectionQueryContext
from agent_brain.memory.governance.temporal_state import TemporalStateGate
from agent_brain.memory.recall.admission import build_recall_request
from agent_brain.memory.recall.retrieval import Retriever, SearchFilter
from agent_brain.memory.recall.routed_types import ProjectScope
from agent_brain.platform.indexing.index import HubIndex


FIXTURE = Path("tests/fixtures/recall_quality_production_replay_v1.json")
EVALUATION_NOW = datetime(2026, 7, 19, 2, 0, tzinfo=timezone.utc)


class _NoModelEmbedder:
    dim = 8
    degraded = True

    def embed(self, text: str) -> list[float]:
        raise AssertionError(f"no-model replay attempted semantic embedding: {text!r}")


def _item(raw: dict) -> tuple[MemoryItem, str]:
    payload = dict(raw)
    body = str(payload.pop("body", ""))
    return MemoryItem.model_validate(payload), body


def _scope(case: RecallQualityCase) -> ProjectScope | None:
    if case.project_scope is None:
        return None
    return ProjectScope(
        value=str(case.project_scope["value"]),
        source=str(case.project_scope["source"]),  # type: ignore[arg-type]
        hard_filter=bool(case.project_scope["hard_filter"]),
    )


def test_production_replay_exercises_admission_retrieval_temporal_and_injection(
    tmp_path: Path,
) -> None:
    corpus = load_recall_quality_corpus(FIXTURE)
    index = HubIndex(tmp_path / "replay.db", embedding_dim=_NoModelEmbedder.dim)
    items: dict[str, tuple[MemoryItem, str]] = {}
    for case in corpus.cases:
        for raw in case.memory_items:
            value, body = _item(raw)
            previous = items.get(value.id)
            assert previous is None or previous == (value, body), value.id
            if previous is None:
                items[value.id] = (value, body)
                index.upsert(value, body, embedding=[0.0] * _NoModelEmbedder.dim)

    retriever = Retriever(
        index,
        _NoModelEmbedder(),  # type: ignore[arg-type]
        rerank=False,
        apply_decay=False,
        record_access=False,
        temporal_now=EVALUATION_NOW,
    )
    failures: list[dict[str, object]] = []
    try:
        for case in corpus.cases:
            request = build_recall_request(
                case.query,
                adapter="codex",
                project_scope=_scope(case),
                cwd="/sanitized/replay",
            )
            result = retriever.search_routed(
                request,
                top_k=10,
                filters=SearchFilter(),
                record_access=False,
            )
            candidates = [
                ContextCandidate(items[hit.id][0], items[hit.id][1], score=hit.score)
                for hit in result.hits
                if hit.id in items
            ]
            gateway = build_injection_context(
                candidates,
                query_context=InjectionQueryContext(
                    raw_query=request.raw_query,
                    admission=request.admission,
                    query_signal=request.query_signal,
                    evidence_by_id=dict(result.evidence_by_id),
                ),
                current_scope={"cwd": "/sanitized/replay", "adapter": "codex"},
                max_items=10,
                now=EVALUATION_NOW,
            )
            injected = {
                entry.decision.candidate.item.id for entry in gateway.included
            }
            expected = set(case.expected_item_ids)
            prohibited = set(case.prohibited_item_ids)
            temporal = _temporal_expectation(case, items)
            answerability = (
                "not_applicable"
                if not request.admission.allowed
                else "supported"
                if expected and expected <= injected
                else "insufficient"
            )
            injection_ok = (
                expected <= injected
                if case.expected_injection
                else not injected
            )
            mismatch = {
                "admission": request.admission.allowed != case.expected_admission,
                "injection": not injection_ok,
                "unexpected_injected": bool(injected - expected),
                "prohibited": bool(injected & prohibited),
                "answerability": answerability != case.expected_answerability,
                "temporal": temporal != case.expected_temporal,
                "abstention": (not injected) != case.expected_abstention,
            }
            if any(mismatch.values()):
                failures.append({
                    "id": case.id,
                    "mismatch": mismatch,
                    "terms": list(request.lexical_terms),
                    "candidate_ids": [hit.id for hit in result.hits],
                    "injected_ids": sorted(injected),
                    "excluded": [
                        (decision.candidate.item.id, decision.reasons)
                        for decision in gateway.excluded
                    ],
                    "routes": [
                        (trace.route, trace.status, trace.reason, trace.candidate_count)
                        for trace in result.routes
                    ],
                })
    finally:
        index.close()

    assert failures == [], json.dumps(failures, ensure_ascii=False, indent=2)


def _temporal_expectation(
    case: RecallQualityCase,
    items: dict[str, tuple[MemoryItem, str]],
) -> str:
    relevant = [
        item_id
        for item_id in (*case.expected_item_ids, *case.prohibited_item_ids)
        if item_id in items
    ]
    if not relevant:
        return "not_applicable"
    signals = [
        TemporalStateGate(now=EVALUATION_NOW).evaluate(
            items[item_id][0],
            items[item_id][1],
        )
        for item_id in relevant
    ]
    if any(signal.status == "stale" for signal in signals):
        return "stale"
    if all(signal.category == "stable" for signal in signals):
        return "stable"
    return "current"
