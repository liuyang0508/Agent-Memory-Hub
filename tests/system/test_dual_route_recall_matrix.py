from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.util
import inspect
import json
import math
from pathlib import Path
from typing import Any

import pytest

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.context.context_firewall_types import ContextCandidate
from agent_brain.memory.context.injection_gateway import InjectionResult, build_injection_context
from agent_brain.memory.context.injection_query_context import InjectionQueryContext
from agent_brain.memory.recall.admission import build_recall_request
from agent_brain.memory.recall.retrieval import Retriever, SearchFilter
from agent_brain.memory.recall.routed_types import RouteEvidence
from agent_brain.platform.indexing.index import HubIndex


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "dual_route_recall_cases.json"
PRECOMPUTED_EMBEDDING_PATH = (
    Path(__file__).parents[1] / "fixtures" / "dual_route_precomputed_embeddings.json"
)
GENERATOR_PATH = Path(__file__).parents[2] / "scripts" / "generate-dual-route-embedding-fixture.py"
CATEGORIES = {
    "semantic_paraphrase",
    "multilingual",
    "keyword_extraction_error",
    "exact_entity",
    "weak_or_no_value",
}


def _cases() -> list[dict[str, Any]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_dual_route_fixture_schema_and_distribution() -> None:
    cases = _cases()
    required = {
        "id",
        "category",
        "query",
        "expected_item_ids",
        "expect_admission",
        "expect_injection",
        "legacy_false_negative",
        "prohibited_item_ids",
    }
    counts = Counter(case["category"] for case in cases)

    assert len(cases) >= 40
    assert len({case["id"] for case in cases}) == len(cases)
    assert all(required <= case.keys() for case in cases)
    assert all(isinstance(case["query"], str) and case["query"].strip() for case in cases)
    assert all(isinstance(case["expected_item_ids"], list) for case in cases)
    assert all(isinstance(case["prohibited_item_ids"], list) for case in cases)
    assert all(type(case["expect_admission"]) is bool for case in cases)
    assert all(type(case["expect_injection"]) is bool for case in cases)
    assert all(type(case["legacy_false_negative"]) is bool for case in cases)
    assert set(counts) == CATEGORIES
    assert all(counts[category] >= 8 for category in CATEGORIES)
    assert sum(bool(case["legacy_false_negative"]) for case in cases) >= 3
    assert {
        "safety-private",
        "safety-secret",
        "safety-review",
        "safety-superseded",
        "safety-scope",
        "safety-gateway-error",
    } <= {case["id"] for case in cases}
    hard_negative_ids = {
        item["id"]
        for case in cases
        for item in case.get("hard_negative_items", [])
    }
    assert len(hard_negative_ids) >= 3
    gateway_case = next(case for case in cases if case["id"] == "safety-gateway-error")
    assert gateway_case["gateway_exception_test"] == (
        "test_gateway_exception_never_exposes_raw_candidate"
    )


def _searchable_item_text(raw: dict[str, Any]) -> str:
    return " ".join(
        (
            str(raw.get("title", "")),
            str(raw.get("summary", "")),
            str(raw.get("body", "")),
            *(str(tag) for tag in raw.get("tags", [])),
        )
    )


def _fixture_embedding_texts() -> tuple[str, ...]:
    return _load_embedding_generator().extract_case_texts(_cases())


def _load_embedding_generator():
    spec = importlib.util.spec_from_file_location("dual_route_embedding_generator", GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_precomputed_embedding_fixture_has_provenance_and_no_label_leakage() -> None:
    generator = _load_embedding_generator()
    signature = inspect.signature(generator.generate_precomputed_embeddings)
    assert tuple(signature.parameters) == ("texts", "encode", "provenance")
    generator_source = GENERATOR_PATH.read_text(encoding="utf-8")
    assert all(
        field not in generator_source
        for field in ("expected_item_ids", "legacy_false_negative", "prohibited_item_ids")
    )
    assert not (
        PRECOMPUTED_EMBEDDING_PATH.parent / "dual_route_semantic_lexicon.json"
    ).exists()

    payload = json.loads(PRECOMPUTED_EMBEDDING_PATH.read_text(encoding="utf-8"))
    texts = _fixture_embedding_texts()
    expected_hashes = {hashlib.sha256(text.encode("utf-8")).hexdigest() for text in texts}
    assert set(payload["embeddings"]) == expected_hashes
    assert all(
        len(content_hash) == 64
        and all(character in "0123456789abcdef" for character in content_hash)
        for content_hash in payload["embeddings"]
    )
    assert payload["content_hash"] == "sha256:utf-8"
    assert payload["model"] == {
        "id": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "revision": "e8f8c211226b894fcb81acc59f3b34ba3efd5f42",
        "dimension": 384,
        "normalized": True,
    }
    assert payload["generator"] == {
        "path": "scripts/generate-dual-route-embedding-fixture.py",
        "version": 1,
        "encoder": "sentence-transformers==3.4.1",
        "float_round_digits": 8,
    }
    assert all(len(vector) == 384 for vector in payload["embeddings"].values())
    assert all(
        math.sqrt(sum(value * value for value in vector)) == pytest.approx(1.0, abs=1e-5)
        for vector in payload["embeddings"].values()
    )
    with pytest.raises(ValueError, match="provenance"):
        generator.generate_precomputed_embeddings(
            ["raw text only"],
            lambda _texts: [[1.0, *([0.0] * 383)]],
            {
                **payload["model"],
                "expected_item_ids": ["forbidden-label-channel"],
            },
        )


def test_precomputed_provider_resolves_only_by_content_hash() -> None:
    provider = _PrecomputedSemanticEmbedder()
    text = _fixture_embedding_texts()[0]
    payload = json.loads(PRECOMPUTED_EMBEDDING_PATH.read_text(encoding="utf-8"))
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    assert provider.embed(text) == payload["embeddings"][content_hash]
    with pytest.raises(KeyError):
        provider.embed("unseen text is not mapped to any label")


class _PrecomputedSemanticEmbedder:
    """Offline CI provider keyed only by searchable-text SHA-256."""

    degraded = False

    def __init__(self) -> None:
        payload = json.loads(PRECOMPUTED_EMBEDDING_PATH.read_text(encoding="utf-8"))
        self.dim = int(payload["model"]["dimension"])
        self._embeddings = payload["embeddings"]

    def embed(self, text: str) -> list[float]:
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return list(self._embeddings[content_hash])


def _memory_item(data: dict[str, Any]) -> tuple[MemoryItem, str]:
    payload = dict(data)
    body = str(payload.pop("body", ""))
    payload.setdefault("created_at", "2026-07-15T12:00:00+00:00")
    payload.setdefault("confidence", 0.9)
    payload.setdefault("sensitivity", "internal")
    payload.setdefault("tags", [])
    payload.setdefault("project", None)
    return MemoryItem.model_validate(payload), body


@dataclass(frozen=True)
class _Outcome:
    candidates: frozenset[str]
    injected: frozenset[str]
    routes: tuple[str, ...]
    semantic_similarities: tuple[tuple[str, float], ...] = ()
    exclusion_reasons: tuple[tuple[str, tuple[str, ...]], ...] = ()


def _seed_fixture_brain(
    tmp_path: Path,
) -> tuple[HubIndex, dict[str, tuple[MemoryItem, str]], _PrecomputedSemanticEmbedder]:
    embedder = _PrecomputedSemanticEmbedder()
    index = HubIndex(tmp_path / "fixture-index.db", embedding_dim=embedder.dim)
    items: dict[str, tuple[MemoryItem, str]] = {}
    for case in _cases():
        raw_items = [case.get("brain_item"), *case.get("hard_negative_items", [])]
        for raw in raw_items:
            if raw is None:
                continue
            item, body = _memory_item(raw)
            previous = items.get(item.id)
            assert previous is None or previous == (item, body), item.id
            if previous is None:
                items[item.id] = (item, body)
                index.upsert(
                    item,
                    body,
                    embedding=embedder.embed(_searchable_item_text(raw)),
                )
    return index, items, embedder


def _gateway_result(
    hits: list[Any],
    *,
    items: dict[str, tuple[MemoryItem, str]],
    request: Any,
    evidence: dict[str, RouteEvidence],
) -> InjectionResult:
    candidates = [
        ContextCandidate(items[hit.id][0], body=items[hit.id][1], score=hit.score)
        for hit in hits
        if hit.id in items
    ]
    return build_injection_context(
        candidates,
        query_context=InjectionQueryContext(
            raw_query=request.raw_query,
            admission=request.admission,
            query_signal=request.query_signal,
            evidence_by_id=evidence,
        ),
        current_scope={"cwd": "/repo/current", "adapter": "codex"},
        max_items=10,
    )


def _gateway_ids(result: InjectionResult) -> frozenset[str]:
    return frozenset(entry.decision.candidate.item.id for entry in result.included)


def _legacy_outcome(
    retriever: Retriever,
    case: dict[str, Any],
    items: dict[str, tuple[MemoryItem, str]],
) -> _Outcome:
    from agent_brain.interfaces.cli.routed_query import _generate_candidates

    request = build_recall_request(case["query"], adapter="codex", cwd="/repo/current")
    result = _generate_candidates(
        request=request,
        retriever=retriever,
        top_k=10,
        filters=SearchFilter(),
        use_routed=False,
    )
    gateway = _gateway_result(
        result.hits,
        items=items,
        request=request,
        evidence=dict(result.evidence_by_id),
    )
    return _Outcome(
        frozenset(hit.id for hit in result.hits),
        _gateway_ids(gateway),
        tuple(
            f"{trace.route}:{trace.status}:{trace.reason}:{trace.candidate_count}"
            for trace in result.routes
        ),
        tuple(
            (item_id, evidence.semantic_similarity)
            for item_id, evidence in result.evidence_by_id.items()
            if evidence.semantic_similarity is not None
        ),
        tuple(
            (decision.candidate.item.id, decision.reasons)
            for decision in gateway.excluded
        ),
    )


def _routed_outcome(
    retriever: Retriever,
    case: dict[str, Any],
    items: dict[str, tuple[MemoryItem, str]],
) -> _Outcome:
    request = build_recall_request(case["query"], adapter="codex", cwd="/repo/current")
    result = retriever.search_routed(
        request,
        top_k=10,
        filters=SearchFilter(),
        record_access=False,
    )
    gateway = _gateway_result(
        result.hits,
        items=items,
        request=request,
        evidence=dict(result.evidence_by_id),
    )
    return _Outcome(
        frozenset(hit.id for hit in result.hits),
        _gateway_ids(gateway),
        tuple(
            f"{trace.route}:{trace.status}:{trace.reason}:{trace.candidate_count}"
            for trace in result.routes
        ),
        tuple(
            (item_id, evidence.semantic_similarity)
            for item_id, evidence in result.evidence_by_id.items()
            if evidence.semantic_similarity is not None
        ),
        tuple(
            (decision.candidate.item.id, decision.reasons)
            for decision in gateway.excluded
        ),
    )


def test_dual_route_candidate_and_injection_governance_matrix(tmp_path: Path) -> None:
    cases = _cases()
    index, items, embedder = _seed_fixture_brain(tmp_path)
    routed = Retriever(
        index,
        embedder,
        rerank=False,
        apply_decay=False,
        record_access=False,
    )
    rows: list[tuple[dict[str, Any], _Outcome, _Outcome]] = []
    try:
        for case in cases:
            if case.get("gateway_exception_test"):
                continue
            request = build_recall_request(case["query"], adapter="codex")
            assert request.admission.allowed is case["expect_admission"], case["id"]
            rows.append(
                (case, _legacy_outcome(routed, case, items), _routed_outcome(routed, case, items))
            )
    finally:
        index.close()

    positives = [(case, old, new) for case, old, new in rows if case["expected_item_ids"]]
    legacy_hits = sum(
        bool(set(case["expected_item_ids"]) & old.candidates) for case, old, _new in positives
    )
    routed_hits = sum(
        bool(set(case["expected_item_ids"]) & new.candidates) for case, _old, new in positives
    )
    fixed = [
        case["id"]
        for case, old, new in rows
        if case["legacy_false_negative"]
        and not (set(case["expected_item_ids"]) & old.injected)
        and bool(set(case["expected_item_ids"]) & new.injected)
    ]
    new_false_negatives = [
        case["id"]
        for case, old, new in rows
        if not case["legacy_false_negative"]
        and case["expect_injection"]
        and bool(set(case["expected_item_ids"]) & old.injected)
        and not bool(set(case["expected_item_ids"]) & new.injected)
    ]
    hard_negative_ids = {
        item["id"]
        for case in cases
        for item in case.get("hard_negative_items", [])
    }
    prohibited = [
        (
            case["id"],
            sorted((set(case["prohibited_item_ids"]) | hard_negative_ids) & new.injected),
        )
        for case, _old, new in rows
        if (set(case["prohibited_item_ids"]) | hard_negative_ids) & new.injected
    ]
    expected_misses = [
        {
            "id": case["id"],
            "routes": new.routes,
            "candidate_ids": sorted(new.candidates),
        }
        for case, _old, new in rows
        if case["expect_injection"] and not (set(case["expected_item_ids"]) & new.injected)
    ]

    assert routed_hits / len(positives) >= legacy_hits / len(positives), expected_misses
    assert len(fixed) >= 3, {"fixed": fixed, "misses": expected_misses}
    assert new_false_negatives == [], new_false_negatives
    assert prohibited == [], prohibited
    assert expected_misses == [], expected_misses

    expected_targets = {
        item_id
        for case in cases
        for item_id in case["expected_item_ids"]
    }
    target_clusters = Counter(
        tuple(case["expected_item_ids"])
        for case in cases
        if case["expected_item_ids"]
    )
    assert len(expected_targets) >= 11
    assert len(target_clusters) >= 11

    positive_similarities = [
        similarity
        for case, _old, new in rows
        for item_id, similarity in new.semantic_similarities
        if item_id in set(case["expected_item_ids"])
    ]
    hard_negative_similarities = [
        similarity
        for _case, _old, new in rows
        for item_id, similarity in new.semantic_similarities
        if item_id in hard_negative_ids
    ]
    assert len(positive_similarities) == len(positives)
    assert hard_negative_similarities
    threshold_only_positive_similarities = [
        similarity
        for case, _old, new in rows
        if not build_recall_request(case["query"], adapter="codex").query_signal.injectable
        for item_id, similarity in new.semantic_similarities
        if item_id in set(case["expected_item_ids"])
    ]
    threshold_only_hard_negative_similarities = [
        similarity
        for case, _old, new in rows
        if not build_recall_request(case["query"], adapter="codex").query_signal.injectable
        for item_id, similarity in new.semantic_similarities
        if item_id in hard_negative_ids
    ]
    distribution = {
        "positive_min": min(positive_similarities),
        "positive_max": max(positive_similarities),
        "hard_negative_min": min(hard_negative_similarities),
        "hard_negative_max": max(hard_negative_similarities),
    }
    assert min(threshold_only_positive_similarities) >= 0.25, distribution
    assert max(threshold_only_hard_negative_similarities) < 0.25, distribution

    overlapping_negatives = []
    for case, _old, new in rows:
        signal = build_recall_request(case["query"], adapter="codex").query_signal
        reasons_by_id = dict(new.exclusion_reasons)
        for item_id, similarity in new.semantic_similarities:
            if item_id not in hard_negative_ids or similarity < 0.25:
                continue
            overlapping_negatives.append(
                (case["id"], item_id, similarity, reasons_by_id.get(item_id, ()))
            )
            assert signal.injectable, overlapping_negatives
            assert {
                "query_mismatch",
                "answerability_mismatch",
            } <= set(reasons_by_id.get(item_id, ())), overlapping_negatives
    assert overlapping_negatives, distribution


class _FakeDeadline:
    def __init__(self, current: float = 0.0) -> None:
        self.current = current

    def now(self) -> float:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += seconds

    def expired(self, deadline: float) -> bool:
        return self.now() >= deadline


def test_fake_deadline_semantic_timeout_preserves_completed_lexical_route(
) -> None:
    from tests.unit.test_routed_retrieval import _Embedder, _Index, _request, _retriever
    from agent_brain.platform.indexing.index_types import Hit

    clock = _FakeDeadline()

    class AdvancingEmbedder(_Embedder):
        def embed(self, query: str) -> list[float]:
            clock.advance(1.1)
            return super().embed(query)

    index = _Index()
    index.bm25_hits["rule_term"] = [Hit("term-hit", 2.0)]
    index.bm25_hits["raw"] = [Hit("raw-hit", 1.0)]

    result = _retriever(index, AdvancingEmbedder()).search_routed(
        _request(),
        top_k=10,
        clock=clock.now,
        semantic_deadline=1.0,
    )

    assert {hit.id for hit in result.hits} == {"term-hit", "raw-hit"}
    traces = {trace.route: trace for trace in result.routes}
    assert traces["lexical_terms"].status == "ok"
    assert traces["semantic_raw"].status == "timeout"
    assert traces["lexical_raw_fallback"].status == "ok"


def test_fake_overall_deadline_fails_closed_without_wall_clock() -> None:
    from agent_brain.interfaces.cli.routed_query import execute_routed_query
    from tests.unit.test_routed_retrieval import _Embedder, _Index, _retriever

    clock = _FakeDeadline()
    index = _Index()
    original_bm25 = index.bm25_search

    def advancing_bm25(*args: Any, **kwargs: Any) -> list[Any]:
        clock.advance(1.1)
        return original_bm25(*args, **kwargs)

    index.bm25_search = advancing_bm25  # type: ignore[method-assign]
    embedder = _Embedder()
    embedder.degraded = True

    payload = execute_routed_query(
        raw_query="meaningful overall deadline probe",
        store=object(),
        retriever=_retriever(index, embedder),
        top_k=10,
        filters=SearchFilter(),
        requested="auto",
        project=None,
        adapter="codex",
        session_id="deadline",
        cwd="/repo/current",
        clock=clock.now,
        overall_deadline=1.0,
    )

    assert payload.to_dict() == {
        "status": "timeout",
        "reason": "overall_timeout",
        "context": "",
        "routes": [],
    }


def test_deadline_expiry_after_render_has_zero_durable_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agent_brain.interfaces.cli import routed_query
    from agent_brain.memory.recall.retrieval_types import RetrievedItem
    from agent_brain.memory.recall.routed_types import RoutedSearchResult, RouteTrace

    clock = _FakeDeadline()
    item, body = _memory_item(
        next(case["brain_item"] for case in _cases() if case["id"] == "entity-01")
    )

    class Store:
        def iter_all(self):
            return iter([(item, body)])

    class RetrieverStub:
        def __init__(self) -> None:
            self.accesses: list[str] = []

        def search_routed(self, request: Any, **_kwargs: Any) -> Any:
            hit = RetrievedItem(item.id, 1.0, bm25_rank=1, vector_rank=None)
            return RoutedSearchResult(
                [hit],
                (RouteTrace("lexical_terms", "ok", 0.0, 1, "route_completed"),),
                request.admission,
                {item.id: RouteEvidence(("lexical_terms",), None, None, 1, None)},
            )

        def record_accesses(self, hits: list[Any]) -> None:
            self.accesses.extend(hit.id for hit in hits)

    retriever = RetrieverStub()
    original_render = routed_query._render_included_context

    def render_then_expire(injection: Any) -> str:
        rendered = original_render(injection)
        clock.advance(1.1)
        return rendered

    cohorts: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    monkeypatch.setattr(routed_query, "_render_included_context", render_then_expire)
    monkeypatch.setattr(
        routed_query,
        "_maybe_record_cohort",
        lambda **kwargs: cohorts.append(kwargs),
    )
    monkeypatch.setattr(
        routed_query,
        "_maybe_record_gap",
        lambda **kwargs: gaps.append(kwargs),
    )

    payload = routed_query.execute_routed_query(
        raw_query="E0583",
        store=Store(),
        retriever=retriever,
        top_k=10,
        filters=SearchFilter(),
        requested="auto",
        project=None,
        adapter="codex",
        session_id="deadline-after-render",
        cwd="/repo/current",
        brain_dir=tmp_path,
        record_injection_cohort=True,
        record_recall_gap=True,
        clock=clock.now,
        overall_deadline=1.0,
    )

    assert payload.to_dict() == {
        "status": "timeout",
        "reason": "overall_timeout",
        "context": "",
        "routes": [],
    }
    assert retriever.accesses == []
    assert cohorts == []
    assert gaps == []


@pytest.mark.parametrize(
    ("clock_value", "deadline"),
    [
        (float("nan"), 1.0),
        (float("inf"), 1.0),
        (0.0, float("nan")),
        (0.0, float("inf")),
    ],
)
def test_non_finite_deadline_inputs_fail_closed(
    clock_value: float,
    deadline: float,
) -> None:
    from agent_brain.interfaces.cli.routed_query import execute_routed_query

    payload = execute_routed_query(
        raw_query="deadline validation probe",
        store=object(),
        retriever=object(),
        top_k=10,
        filters=SearchFilter(),
        requested="auto",
        project=None,
        adapter="codex",
        session_id="deadline-validation",
        cwd="/repo/current",
        clock=lambda: clock_value,
        overall_deadline=deadline,
    )

    assert payload.to_dict() == {
        "status": "error",
        "reason": "internal_error",
        "context": "",
        "routes": [],
    }


def test_gateway_exception_never_exposes_raw_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_brain.interfaces.cli import routed_query
    from agent_brain.memory.recall.retrieval_types import RetrievedItem
    from agent_brain.memory.recall.routed_types import RoutedSearchResult, RouteTrace

    item, body = _memory_item(
        next(case["brain_item"] for case in _cases() if case["id"] == "safety-gateway-error")
    )

    class Store:
        def iter_all(self):
            return iter([(item, body)])

    class RetrieverStub:
        def search_routed(self, request: Any, **_kwargs: Any) -> Any:
            hit = RetrievedItem(item.id, 1.0, bm25_rank=1, vector_rank=None)
            return RoutedSearchResult(
                [hit],
                (RouteTrace("lexical_terms", "ok", 0.0, 1, "route_completed"),),
                request.admission,
                {item.id: RouteEvidence(("lexical_terms",), None, None, 1, None)},
            )

    def explode(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("gateway verifier failed")

    monkeypatch.setattr(routed_query, "build_injection_context", explode)
    payload = routed_query.execute_routed_query(
        raw_query="gateway failure safety probe",
        store=Store(),
        retriever=RetrieverStub(),
        top_k=10,
        filters=SearchFilter(),
        requested="auto",
        project=None,
        adapter="codex",
        session_id=None,
        cwd="/repo/current",
    )

    assert payload.status == "error"
    assert payload.context == ""
    assert item.id not in json.dumps(payload.to_dict())
