from __future__ import annotations

from types import MethodType, SimpleNamespace
from typing import Any

import pytest

from agent_brain.memory.context.query_signal import QuerySignal
from agent_brain.memory.recall.admission import RecallAdmission
from agent_brain.memory.recall.retrieval import Retriever, SearchFilter
from agent_brain.memory.recall.retrieval_types import RetrievedItem
from agent_brain.memory.recall.routed_types import ProjectScope, RecallRequest


def _request(
    *,
    normalized_query: str = "raw semantic question",
    lexical_terms: tuple[str, ...] = ("rule_term",),
    allowed: bool = True,
    project_scope: ProjectScope | None = None,
) -> RecallRequest:
    return RecallRequest(
        raw_query=f"RAW:{normalized_query}",
        normalized_query=normalized_query,
        lexical_terms=lexical_terms,
        admission=RecallAdmission(
            allowed,
            "meaningful_query" if allowed else "weak_confirmation",
        ),
        query_signal=QuerySignal(
            terms=lexical_terms,
            strong_terms=lexical_terms,
            weak_terms=(),
            injectable=allowed,
            reason="strong_term" if allowed else "too_weak",
            specificity=1.0 if allowed else 0.0,
        ),
        project_scope=project_scope,
        cwd="/workspace/project",
        adapter="codex",
        session_id="session-1",
    )


class _Embedder:
    degraded = False

    def __init__(self, embedding: list[float] | None = None) -> None:
        self.embedding = embedding or [1.0, 0.0]
        self.queries: list[str] = []
        self.error: BaseException | None = None

    def embed(self, query: str) -> list[float]:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return self.embedding


class _Index:
    def __init__(self) -> None:
        self.bm25_hits: dict[str, list[Any]] = {}
        self.vector_hits: list[Any] = []
        self.embeddings: dict[str, list[float]] = {}
        self.projects: dict[str, str | None] = {}
        self.bm25_queries: list[str] = []
        self.vector_queries: list[list[float]] = []
        self.filter_calls: list[dict[str, Any]] = []
        self.get_projects_calls: list[list[str]] = []
        self.bm25_error_for: dict[str, BaseException] = {}
        self.vector_error: BaseException | None = None

    def bm25_search(
        self,
        query: str,
        top_k: int = 10,
        *,
        allowed_ids: set[str] | None = None,
    ) -> list[Any]:
        self.bm25_queries.append(query)
        for marker, error in self.bm25_error_for.items():
            if marker in query:
                raise error
        for marker, hits in self.bm25_hits.items():
            if marker in query:
                eligible = hits
                if allowed_ids is not None:
                    eligible = [hit for hit in hits if hit.id in allowed_ids]
                return eligible[:top_k]
        return []

    def vector_search(
        self,
        embedding: list[float],
        top_k: int = 10,
        *,
        allowed_ids: set[str] | None = None,
    ) -> list[Any]:
        self.vector_queries.append(embedding)
        if self.vector_error is not None:
            raise self.vector_error
        eligible = self.vector_hits
        if allowed_ids is not None:
            eligible = [hit for hit in self.vector_hits if hit.id in allowed_ids]
        return eligible[:top_k]

    def get_embeddings(self, item_ids: list[str]) -> dict[str, list[float]]:
        return {
            item_id: self.embeddings[item_id] for item_id in item_ids if item_id in self.embeddings
        }

    def filter_ids(self, **kwargs: Any) -> set[str] | None:
        self.filter_calls.append(kwargs)
        project = kwargs.get("project")
        if project is not None:
            return {item_id for item_id, value in self.projects.items() if value == project}
        return None

    def get_projects(self, item_ids: list[str]) -> dict[str, str | None]:
        self.get_projects_calls.append(list(item_ids))
        return {item_id: self.projects.get(item_id) for item_id in item_ids}

    def get_feedback_data(self, item_ids: list[str]) -> dict[str, tuple[int, int, float]]:
        return {}

    def get_search_metadata(self, item_ids: list[str]) -> dict[str, dict[str, object]]:
        return {}

    def get_texts(self, item_ids: list[str]) -> dict[str, str]:
        return {}


def _retriever(index: _Index, embedder: _Embedder, **kwargs: Any) -> Retriever:
    options: dict[str, Any] = {
        "rerank": False,
        "apply_decay": False,
        "record_access": False,
    }
    options.update(kwargs)
    return Retriever(
        index=index,  # type: ignore[arg-type]
        embedder=embedder,  # type: ignore[arg-type]
        **options,
    )


def test_fuse_routes_accumulates_shared_hits_and_preserves_stable_evidence() -> None:
    from agent_brain.memory.recall.routed_fusion import fuse_routes

    hits, evidence = fuse_routes(
        lexical_terms_hits=[SimpleNamespace(id="terms-first"), SimpleNamespace(id="shared")],
        semantic_hits=[SimpleNamespace(id="shared"), SimpleNamespace(id="semantic-only")],
        lexical_raw_hits=[SimpleNamespace(id="raw-only")],
        semantic_similarities={"shared": 0.8, "semantic-only": 0.6},
        rrf_k=60,
    )

    assert [hit.id for hit in hits] == [
        "shared",
        "terms-first",
        "raw-only",
        "semantic-only",
    ]
    by_id = {hit.id: hit for hit in hits}
    assert by_id["shared"].score == pytest.approx(1 / 62 + 1 / 61)
    assert by_id["shared"].bm25_rank == 2
    assert by_id["shared"].vector_rank == 1
    assert evidence["shared"].routes == ("lexical_terms", "semantic_raw")
    assert evidence["shared"].semantic_similarity == pytest.approx(0.8)
    assert evidence["shared"].semantic_rank == 1
    assert evidence["shared"].lexical_terms_rank == 2
    assert evidence["shared"].lexical_raw_rank is None
    assert evidence["raw-only"].routes == ("lexical_raw_fallback",)
    assert evidence["raw-only"].lexical_raw_rank == 1
    assert by_id["shared"].score != evidence["shared"].semantic_similarity


def test_routed_queries_keep_rule_terms_and_normalized_semantic_query_separate() -> None:
    index = _Index()
    index.bm25_hits = {"rule_term": [SimpleNamespace(id="shared", score=99.0)]}
    index.vector_hits = [SimpleNamespace(id="shared", score=-123.0)]
    index.embeddings = {"shared": [4.0, 3.0]}
    embedder = _Embedder([2.0, 0.0])
    request = _request(
        normalized_query="raw semantic sentence unique_raw",
        lexical_terms=("rule_term",),
    )

    result = _retriever(index, embedder).search_routed(
        request,
        top_k=5,
        filters=SearchFilter(include_superseded=True, include_stale_state=True),
    )

    assert embedder.queries == [request.normalized_query]
    assert len(index.bm25_queries) == 1
    assert "rule_term" in index.bm25_queries[0]
    assert "unique_raw" not in index.bm25_queries[0]
    assert result.evidence_by_id["shared"].semantic_similarity == pytest.approx(0.8)
    assert result.evidence_by_id["shared"].semantic_rank == 1
    assert result.evidence_by_id["shared"].lexical_terms_rank == 1
    assert result.hits[0].score != pytest.approx(0.8)


def test_degraded_semantic_runs_term_and_full_raw_bm25_routes() -> None:
    index = _Index()
    index.bm25_hits = {
        "rule_term": [SimpleNamespace(id="term-hit", score=1.0)],
        "unique_raw": [SimpleNamespace(id="raw-hit", score=1.0)],
    }
    embedder = _Embedder()
    embedder.degraded = True
    request = _request(
        normalized_query="full unique_raw question",
        lexical_terms=("rule_term",),
    )

    result = _retriever(index, embedder).search_routed(
        request,
        filters=SearchFilter(include_superseded=True, include_stale_state=True),
    )

    assert embedder.queries == []
    assert len(index.bm25_queries) == 2
    assert "rule_term" in index.bm25_queries[0]
    assert "unique_raw" in index.bm25_queries[1]
    assert {hit.id for hit in result.hits} == {"term-hit", "raw-hit"}
    assert [(trace.route, trace.status, trace.reason) for trace in result.routes] == [
        ("lexical_terms", "ok", "route_completed"),
        ("semantic_raw", "skipped", "semantic_not_ready"),
        ("lexical_raw_fallback", "ok", "route_completed"),
    ]


@pytest.mark.parametrize(
    ("error", "status", "reason"),
    [
        (TimeoutError("private query must not leak"), "timeout", "route_timeout"),
        (RuntimeError("private item must not leak"), "error", "route_error"),
    ],
)
def test_semantic_failure_preserves_term_hits_and_uses_bounded_trace(
    error: BaseException,
    status: str,
    reason: str,
) -> None:
    index = _Index()
    index.bm25_hits = {
        "rule_term": [SimpleNamespace(id="term-hit", score=1.0)],
        "unique_raw": [SimpleNamespace(id="raw-hit", score=1.0)],
    }
    index.vector_error = error
    request = _request(
        normalized_query="private unique_raw question",
        lexical_terms=("rule_term",),
    )

    result = _retriever(index, _Embedder()).search_routed(
        request,
        filters=SearchFilter(include_superseded=True, include_stale_state=True),
    )

    assert {hit.id for hit in result.hits} == {"term-hit", "raw-hit"}
    semantic_trace = next(trace for trace in result.routes if trace.route == "semantic_raw")
    assert semantic_trace.status == status
    assert semantic_trace.reason == reason
    assert "private" not in repr(result.routes)


def test_empty_terms_skip_only_term_route_while_semantic_still_runs() -> None:
    index = _Index()
    index.vector_hits = [SimpleNamespace(id="semantic", score=-2.0)]
    index.embeddings = {"semantic": [1.0, 0.0]}

    result = _retriever(index, _Embedder()).search_routed(
        _request(lexical_terms=()),
        filters=SearchFilter(include_superseded=True, include_stale_state=True),
    )

    assert [hit.id for hit in result.hits] == ["semantic"]
    assert index.bm25_queries == []
    assert result.routes[0].route == "lexical_terms"
    assert result.routes[0].status == "skipped"
    assert result.routes[0].reason == "lexical_terms_empty"


def test_empty_terms_with_degraded_semantic_still_runs_raw_fallback() -> None:
    index = _Index()
    index.bm25_hits = {"unique_raw": [SimpleNamespace(id="raw-hit", score=1.0)]}
    embedder = _Embedder()
    embedder.degraded = True

    result = _retriever(index, embedder).search_routed(
        _request(normalized_query="full unique_raw question", lexical_terms=()),
        filters=SearchFilter(include_superseded=True, include_stale_state=True),
    )

    assert [hit.id for hit in result.hits] == ["raw-hit"]
    assert len(index.bm25_queries) == 1
    assert "unique_raw" in index.bm25_queries[0]


def test_ready_semantic_zero_hits_does_not_trigger_raw_fallback() -> None:
    index = _Index()
    index.bm25_hits = {
        "rule_term": [SimpleNamespace(id="term-hit", score=1.0)],
        "unique_raw": [SimpleNamespace(id="must-not-run", score=1.0)],
    }

    result = _retriever(index, _Embedder()).search_routed(
        _request(
            normalized_query="full unique_raw question",
            lexical_terms=("rule_term",),
        ),
        filters=SearchFilter(include_superseded=True, include_stale_state=True),
    )

    assert [hit.id for hit in result.hits] == ["term-hit"]
    assert len(index.bm25_queries) == 1
    semantic_trace = next(trace for trace in result.routes if trace.route == "semantic_raw")
    assert semantic_trace.status == "ok"
    assert semantic_trace.candidate_count == 0
    assert all(trace.route != "lexical_raw_fallback" for trace in result.routes)


def test_admission_rejection_touches_no_index_or_embedder() -> None:
    class ExplodingIndex:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"index must not be touched: {name}")

    embedder = _Embedder()

    result = _retriever(ExplodingIndex(), embedder).search_routed(  # type: ignore[arg-type]
        _request(allowed=False),
    )

    assert result.hits == []
    assert result.evidence_by_id == {}
    assert embedder.queries == []
    assert result.routes
    assert all(trace.status == "skipped" for trace in result.routes)
    assert all(trace.reason == "admission_rejected" for trace in result.routes)


def test_explicit_project_scope_is_a_hard_filter() -> None:
    index = _Index()
    index.projects = {"in": "project-a", "out": "project-b"}
    index.bm25_hits = {
        "rule_term": [
            SimpleNamespace(id="out", score=2.0),
            SimpleNamespace(id="in", score=1.0),
        ]
    }

    result = _retriever(index, _Embedder(), vector_weight=0.0).search_routed(
        _request(
            lexical_terms=("rule_term",),
            project_scope=ProjectScope("project-a", "explicit", hard_filter=True),
        ),
        filters=SearchFilter(include_superseded=True, include_stale_state=True),
    )

    assert [hit.id for hit in result.hits] == ["in"]
    assert any(call.get("project") == "project-a" for call in index.filter_calls)


def test_lexical_terms_hard_filter_applies_before_real_bm25_limit(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.platform.indexing.index import HubIndex

    index = HubIndex(tmp_path / "index.db", embedding_dim=2)
    try:
        blocked = MemoryItem(
            id="mem-20260715-110000-blocked-bm25",
            type=MemoryType.fact,
            created_at="2026-07-15T11:00:00+08:00",
            title="blocked item",
            summary="blocked summary " + "needle " * 40,
            project="project-b",
        )
        allowed = MemoryItem(
            id="mem-20260715-110001-allowed-bm25",
            type=MemoryType.fact,
            created_at="2026-07-15T11:00:01+08:00",
            title="allowed item",
            summary="allowed summary needle",
            project="project-a",
        )
        index.upsert(blocked, "needle " * 40, embedding=None)
        index.upsert(allowed, "needle", embedding=None)
        assert index.bm25_search('"needle"', top_k=1)[0].id == blocked.id

        embedder = _Embedder()
        embedder.degraded = True
        result = Retriever(
            index,
            embedder,  # type: ignore[arg-type]
            bm25_top=1,
            rerank=False,
            apply_decay=False,
            record_access=False,
        ).search_routed(
            _request(
                normalized_query="needle",
                lexical_terms=("needle",),
                project_scope=ProjectScope("project-a", "explicit", hard_filter=True),
            ),
            filters=SearchFilter(include_superseded=True, include_stale_state=True),
        )

        assert [hit.id for hit in result.hits] == [allowed.id]
        assert result.evidence_by_id[allowed.id].lexical_terms_rank == 1
    finally:
        index.close()


def test_semantic_hard_filter_applies_before_vector_limit() -> None:
    index = _Index()
    index.projects = {"blocked": "project-b", "allowed": "project-a"}
    index.vector_hits = [
        SimpleNamespace(id="blocked", score=2.0),
        SimpleNamespace(id="allowed", score=1.0),
    ]
    index.embeddings = {"blocked": [1.0, 0.0], "allowed": [0.9, 0.1]}

    result = _retriever(index, _Embedder(), vector_top=1).search_routed(
        _request(
            lexical_terms=(),
            project_scope=ProjectScope("project-a", "explicit", hard_filter=True),
        ),
        filters=SearchFilter(include_superseded=True, include_stale_state=True),
    )

    assert [hit.id for hit in result.hits] == ["allowed"]
    assert result.evidence_by_id["allowed"].semantic_rank == 1


def test_semantic_hard_filter_applies_before_real_vector_limit(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.platform.indexing.index import HubIndex

    index = HubIndex(tmp_path / "vector-index.db", embedding_dim=2)
    try:
        blocked = MemoryItem(
            id="mem-20260715-111000-blocked-vector",
            type=MemoryType.fact,
            created_at="2026-07-15T11:10:00+08:00",
            title="blocked vector item",
            summary="blocked vector summary",
            project="project-b",
        )
        allowed = MemoryItem(
            id="mem-20260715-111001-allowed-vector",
            type=MemoryType.fact,
            created_at="2026-07-15T11:10:01+08:00",
            title="allowed vector item",
            summary="allowed vector summary",
            project="project-a",
        )
        index.upsert(blocked, "blocked", embedding=[1.0, 0.0])
        index.upsert(allowed, "allowed", embedding=[0.9, 0.1])
        assert index.vector_search([1.0, 0.0], top_k=1)[0].id == blocked.id

        result = Retriever(
            index,
            _Embedder([1.0, 0.0]),  # type: ignore[arg-type]
            vector_top=1,
            rerank=False,
            apply_decay=False,
            record_access=False,
        ).search_routed(
            _request(
                normalized_query="semantic route query",
                lexical_terms=(),
                project_scope=ProjectScope("project-a", "explicit", hard_filter=True),
            ),
            filters=SearchFilter(include_superseded=True, include_stale_state=True),
        )

        assert [hit.id for hit in result.hits] == [allowed.id]
        assert result.evidence_by_id[allowed.id].semantic_rank == 1
    finally:
        index.close()


def test_lexical_raw_fallback_hard_filter_applies_before_bm25_limit() -> None:
    index = _Index()
    index.projects = {"blocked": "project-b", "allowed": "project-a"}
    index.bm25_hits = {
        "unique_raw": [
            SimpleNamespace(id="blocked", score=2.0),
            SimpleNamespace(id="allowed", score=1.0),
        ]
    }

    result = _retriever(index, _Embedder(), vector_weight=0.0, bm25_top=1).search_routed(
        _request(
            normalized_query="full unique_raw question",
            lexical_terms=(),
            project_scope=ProjectScope("project-a", "explicit", hard_filter=True),
        ),
        filters=SearchFilter(include_superseded=True, include_stale_state=True),
    )

    assert [hit.id for hit in result.hits] == ["allowed"]
    assert result.evidence_by_id["allowed"].lexical_raw_rank == 1


def test_semantic_hit_without_embedding_is_not_fused_or_fallbacked() -> None:
    index = _Index()
    index.vector_hits = [SimpleNamespace(id="missing-embedding", score=-1.0)]

    result = _retriever(index, _Embedder()).search_routed(
        _request(lexical_terms=()),
        filters=SearchFilter(include_superseded=True, include_stale_state=True),
    )

    assert result.hits == []
    assert result.evidence_by_id == {}
    semantic_trace = next(trace for trace in result.routes if trace.route == "semantic_raw")
    assert semantic_trace.status == "ok"
    assert semantic_trace.candidate_count == 0
    assert all(trace.route != "lexical_raw_fallback" for trace in result.routes)


@pytest.mark.parametrize("source", ["cwd", "agent_inferred"])
def test_inferred_project_scope_only_soft_boosts_without_hard_filter(source: str) -> None:
    index = _Index()
    index.projects = {"cross-project": "project-b", "scope-match": "project-a", "none": None}
    index.bm25_hits = {
        "rule_term": [
            SimpleNamespace(id="cross-project", score=3.0),
            SimpleNamespace(id="scope-match", score=2.0),
            SimpleNamespace(id="none", score=1.0),
        ]
    }

    result = _retriever(index, _Embedder(), vector_weight=0.0).search_routed(
        _request(
            lexical_terms=("rule_term",),
            project_scope=ProjectScope("project-a", source, hard_filter=False),  # type: ignore[arg-type]
        ),
        filters=SearchFilter(include_superseded=True, include_stale_state=True),
    )

    assert {hit.id for hit in result.hits} == {"cross-project", "scope-match", "none"}
    assert result.hits[0].id == "scope-match"
    assert index.filter_calls == []
    assert index.get_projects_calls == [["cross-project", "scope-match", "none"]]


def test_routed_pipeline_uses_normalized_query_for_every_query_aware_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_brain.memory.recall import retrieval as retrieval_module

    index = _Index()
    index.bm25_hits = {"rule_term": [SimpleNamespace(id="hit", score=1.0)]}
    request = _request(
        normalized_query="normalized complete raw question",
        lexical_terms=("rule_term",),
    )
    retriever = _retriever(index, _Embedder(), vector_weight=0.0, rerank=True, mmr_lambda=0.5)
    seen: list[str] = []

    def capture_method(
        self: Retriever, query: str, candidates: list[RetrievedItem], *args: Any, **kwargs: Any
    ):
        seen.append(query)
        return candidates

    def capture_function(
        index: Any, query: str, candidates: list[RetrievedItem], *args: Any, **kwargs: Any
    ):
        seen.append(query)
        return candidates

    monkeypatch.setattr(
        retriever, "_apply_metadata_phrase_boost", MethodType(capture_method, retriever)
    )
    monkeypatch.setattr(retriever, "_rerank", MethodType(capture_method, retriever))
    monkeypatch.setattr(retriever, "_mmr_rerank", MethodType(capture_method, retriever))
    monkeypatch.setattr(retrieval_module, "supplement_status_handoff_candidates", capture_function)
    monkeypatch.setattr(retrieval_module, "apply_status_handoff_boost", capture_function)
    monkeypatch.setattr(retrieval_module, "apply_adapter_runtime_evidence_boost", capture_function)

    retriever.search_routed(
        request,
        filters=SearchFilter(include_superseded=True, include_stale_state=True),
    )

    assert seen
    assert set(seen) == {request.normalized_query}


def test_final_evidence_mapping_only_contains_final_routed_hits() -> None:
    index = _Index()
    index.bm25_hits = {
        "rule_term": [
            SimpleNamespace(id="keep", score=2.0),
            SimpleNamespace(id="drop", score=1.0),
        ]
    }
    retriever = _retriever(index, _Embedder(), vector_weight=0.0)

    def replace_pipeline(
        self: Retriever,
        candidates: list[RetrievedItem],
        options: Any,
    ) -> list[RetrievedItem]:
        return [
            RetrievedItem("supplement", 2.0, None, None),
            next(candidate for candidate in candidates if candidate.id == "keep"),
        ]

    retriever._run_candidate_pipeline = MethodType(replace_pipeline, retriever)

    result = retriever.search_routed(
        _request(lexical_terms=("rule_term",)),
        top_k=2,
        filters=SearchFilter(include_superseded=True, include_stale_state=True),
    )

    assert [hit.id for hit in result.hits] == ["supplement", "keep"]
    assert set(result.evidence_by_id) == {"keep"}


def test_metadata_index_get_projects_and_hub_facade(tmp_path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.platform.indexing.index import HubIndex

    index = HubIndex(tmp_path / "index.db", embedding_dim=2)
    try:
        index.upsert(
            MemoryItem(
                id="mem-20260715-100000-project",
                type=MemoryType.fact,
                created_at="2026-07-15T10:00:00+08:00",
                title="project item",
                summary="project summary",
                project="project-a",
            ),
            "body",
            embedding=[1.0, 0.0],
        )
        index.upsert(
            MemoryItem(
                id="mem-20260715-100001-project-none",
                type=MemoryType.fact,
                created_at="2026-07-15T10:00:01+08:00",
                title="projectless item",
                summary="projectless summary",
            ),
            "body",
            embedding=[0.0, 1.0],
        )

        assert index.get_projects(
            ["mem-20260715-100000-project", "mem-20260715-100001-project-none", "missing"]
        ) == {
            "mem-20260715-100000-project": "project-a",
            "mem-20260715-100001-project-none": None,
        }
    finally:
        index.close()
