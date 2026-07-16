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
from agent_brain.memory.context.injection_gateway import build_injection_context
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
    values = [case["query"] for case in _cases()]
    values.extend(
        _searchable_item_text(case["brain_item"])
        for case in _cases()
        if case.get("brain_item") is not None
    )
    return tuple(dict.fromkeys(values))


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


def _seed_fixture_brain(
    tmp_path: Path,
) -> tuple[HubIndex, dict[str, tuple[MemoryItem, str]], _PrecomputedSemanticEmbedder]:
    embedder = _PrecomputedSemanticEmbedder()
    index = HubIndex(tmp_path / "fixture-index.db", embedding_dim=embedder.dim)
    items: dict[str, tuple[MemoryItem, str]] = {}
    for case in _cases():
        raw = case.get("brain_item")
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


def _gateway_ids(
    hits: list[Any],
    *,
    items: dict[str, tuple[MemoryItem, str]],
    request: Any,
    evidence: dict[str, RouteEvidence],
) -> frozenset[str]:
    candidates = [
        ContextCandidate(items[hit.id][0], body=items[hit.id][1], score=hit.score)
        for hit in hits
        if hit.id in items
    ]
    result = build_injection_context(
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
    return frozenset(entry.decision.candidate.item.id for entry in result.included)


def _legacy_outcome(
    retriever: Retriever,
    case: dict[str, Any],
    items: dict[str, tuple[MemoryItem, str]],
) -> _Outcome:
    request = build_recall_request(case["query"], adapter="codex", cwd="/repo/current")
    if not request.query_signal.injectable or not request.lexical_terms:
        return _Outcome(frozenset(), frozenset(), ())
    hits = retriever.search(
        "|".join(request.lexical_terms),
        top_k=10,
        filters=SearchFilter(),
        record_access=False,
    )
    evidence = {
        hit.id: RouteEvidence(("lexical_terms",), None, None, rank, None)
        for rank, hit in enumerate(hits, start=1)
    }
    return _Outcome(
        frozenset(hit.id for hit in hits),
        _gateway_ids(hits, items=items, request=request, evidence=evidence),
        ("lexical_terms:ok",),
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
    if case.get("simulate_gateway_exception"):
        return _Outcome(
            frozenset(hit.id for hit in result.hits),
            frozenset(),
            tuple(
                f"{trace.route}:{trace.status}:{trace.reason}:{trace.candidate_count}"
                for trace in result.routes
            ),
        )
    return _Outcome(
        frozenset(hit.id for hit in result.hits),
        _gateway_ids(
            result.hits,
            items=items,
            request=request,
            evidence=dict(result.evidence_by_id),
        ),
        tuple(
            f"{trace.route}:{trace.status}:{trace.reason}:{trace.candidate_count}"
            for trace in result.routes
        ),
    )


def test_dual_route_candidate_and_injection_governance_matrix(tmp_path: Path) -> None:
    cases = _cases()
    index, items, embedder = _seed_fixture_brain(tmp_path)
    legacy = Retriever(
        index,
        embedder,
        vector_weight=0.0,
        rerank=False,
        apply_decay=False,
        record_access=False,
    )
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
            request = build_recall_request(case["query"], adapter="codex")
            assert request.admission.allowed is case["expect_admission"], case["id"]
            rows.append(
                (case, _legacy_outcome(legacy, case, items), _routed_outcome(routed, case, items))
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
    prohibited = [
        (case["id"], sorted(set(case["prohibited_item_ids"]) & new.injected))
        for case, _old, new in rows
        if set(case["prohibited_item_ids"]) & new.injected
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
            hit = RetrievedItem(item.id, 1.0, bm25_rank=1)
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
