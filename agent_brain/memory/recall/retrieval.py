from __future__ import annotations

import logging
import operator
import re
import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Callable, SupportsIndex, cast

from agent_brain.platform.embedding import Embedder
from agent_brain.platform.indexing.index_types import Hit
from agent_brain.memory.recall.query_expansion import (
    _expand_with_synonyms as _expand_with_synonyms,
    _extract_words as _extract_words,
    _tokenize_mixed as _tokenize_mixed,
    expand_query,
)
from agent_brain.memory.recall.retrieval_access import RetrievalAccessRecorder
from agent_brain.memory.recall.retrieval_budget import (
    estimate_tokens as estimate_tokens,
    pack_within_budget as pack_within_budget,
)
from agent_brain.memory.recall.retrieval_decay import RetrievalDecay, retention_factor as retention_factor
from agent_brain.memory.recall.retrieval_fusion import rrf_fusion
from agent_brain.memory.recall.retrieval_graph import expand_via_graph
from agent_brain.memory.recall.retrieval_hopfield import expand_via_hopfield
from agent_brain.memory.recall.retrieval_mmr import _cosine_sim as _cosine_sim, mmr_rerank
from agent_brain.memory.recall import retrieval_rerank as retrieval_rerank
from agent_brain.memory.recall.retrieval_rerank import (
    CrossEncoderReranker,
    _get_cross_encoder,
    _sigmoid as _sigmoid,
    rerank_enabled,
)
from agent_brain.memory.recall.retrieval_runtime import apply_adapter_runtime_evidence_boost
from agent_brain.memory.recall.retrieval_status import (
    apply_status_handoff_boost,
    supplement_status_handoff_candidates,
)
from agent_brain.memory.recall.retrieval_supersession import filter_md_superseded_candidates
from agent_brain.memory.recall.retrieval_tags import suggest_tags as suggest_tags
from agent_brain.memory.recall.retrieval_temporal import filter_stale_temporal_state
from agent_brain.memory.recall.retrieval_trace import RetrievalStageTrace, RetrievalTrace
from agent_brain.memory.recall.retrieval_types import RetrievedItem
from agent_brain.memory.recall.retrieval_value import apply_feedback_value_weight
from agent_brain.memory.recall.routed_fusion import fuse_routes
from agent_brain.memory.recall.routed_types import (
    RecallRequest,
    RoutedSearchResult,
    RouteTrace,
)

logger = logging.getLogger(__name__)
_METADATA_TOKEN_RE = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]", re.IGNORECASE)
_RAW_FALLBACK_RUN_RE = re.compile(r"[A-Za-z0-9_+.-]+|[\u3400-\u9fff]+")
_RAW_FALLBACK_FRAGMENT_LIMIT = 64

_CandidateStage = Callable[[list[RetrievedItem]], list[RetrievedItem]]

if TYPE_CHECKING:
    from agent_brain.platform.indexing.index import HubIndex


def _normalize_routed_top_k(value: object) -> int:
    if isinstance(value, bool):
        raise TypeError("top_k must be an integer")
    try:
        top_k = operator.index(cast(SupportsIndex, value))
    except TypeError as exc:
        raise TypeError("top_k must be an integer") from exc
    if top_k < 0:
        raise ValueError("top_k must be non-negative")
    return top_k


@dataclass(frozen=True)
class _NamedCandidateStage:
    name: str
    apply: _CandidateStage


@dataclass(frozen=True)
class SearchFilter:
    """Pre-retrieval filter applied at the SQL level on items_meta."""
    type: str | None = None
    project: str | None = None
    tags: list[str] = field(default_factory=list)
    exclude_tags: list[str] = field(default_factory=list)
    since_days: int | None = None
    tenant_id: str | None = None
    include_superseded: bool = False
    include_stale_state: bool = False

    @property
    def is_empty(self) -> bool:
        """Return whether the filter has no metadata constraints."""
        return (
            self.type is None and self.project is None and not self.tags
            and not self.exclude_tags
            and self.since_days is None and self.tenant_id is None
        )


@dataclass(frozen=True)
class _SearchPipelineOptions:
    query: str
    top_k: int
    allowed_ids: set[str] | None
    include_superseded: bool
    include_stale_state: bool


@dataclass(frozen=True)
class _RoutedFilterPlan:
    allowed_ids: set[str] | None
    excluded_ids: set[str]


class Retriever:
    """BM25 + vector retrieval fused via Reciprocal Rank Fusion."""

    def __init__(
        self,
        index: HubIndex,
        embedder: Embedder,
        rrf_k: int = 60,
        bm25_top: int = 50,
        vector_top: int = 50,
        bm25_weight: float = 1.0,
        vector_weight: float = 1.0,
        query_expansion: bool = True,
        rerank: bool | None = None,
        rerank_top: int = 20,
        apply_decay: bool = True,
        record_access: bool = True,
        graph_expand: bool = False,
        graph_depth: int = 1,
        hopfield_expand: bool = False,
        hopfield_top: int = 20,
        hopfield_beta: float = 8.0,
        mmr_lambda: float | None = None,
    ) -> None:
        self.index = index
        self.embedder = embedder
        self.rrf_k = rrf_k
        self.bm25_top = bm25_top
        self.vector_top = vector_top
        self.bm25_weight = bm25_weight
        self.vector_weight = vector_weight
        self.query_expansion = query_expansion
        self.rerank = rerank if rerank is not None else rerank_enabled()
        self.rerank_top = rerank_top
        self.apply_decay = apply_decay
        self.record_access = record_access
        self.graph_expand = graph_expand
        self.graph_depth = graph_depth
        self.hopfield_expand = hopfield_expand
        self.hopfield_top = hopfield_top
        self.hopfield_beta = hopfield_beta
        self.mmr_lambda = mmr_lambda
        self.decay = RetrievalDecay(index)
        self.cross_encoder_reranker = CrossEncoderReranker(
            index,
            cross_encoder_factory=lambda: _get_cross_encoder(),
        )

    def _rrf_fusion(
        self, query: str, allowed_ids: set[str] | None = None,
    ) -> list[RetrievedItem]:
        fts_query = expand_query(query, use_or=self.query_expansion)
        bm25_hits = []
        if self.bm25_weight > 0 and self.bm25_top > 0:
            bm25_hits = self.index.bm25_search(fts_query, top_k=self.bm25_top)
        if self.vector_weight <= 0 or self.vector_top <= 0:
            vec_hits = []
        elif getattr(self.embedder, "degraded", False):
            # Prod embedder fell back to HashingEmbedder (no real semantic model):
            # its vectors are meaningless, so go BM25-only rather than fuse in
            # garbage neighbours. BM25 is local and needs no model.
            vec_hits = []
        else:
            vec_hits = self.index.vector_search(
                self.embedder.embed(query), top_k=self.vector_top
            )
        if allowed_ids is not None:
            bm25_hits = [h for h in bm25_hits if h.id in allowed_ids]
            vec_hits = [h for h in vec_hits if h.id in allowed_ids]
        return rrf_fusion(
            bm25_hits,
            vec_hits,
            rrf_k=self.rrf_k,
            bm25_weight=self.bm25_weight,
            vector_weight=self.vector_weight,
        )

    def _rerank(self, query: str, candidates: list[RetrievedItem]) -> list[RetrievedItem]:
        return self.cross_encoder_reranker.rerank(query, candidates)

    def _apply_decay(self, candidates: list[RetrievedItem]) -> list[RetrievedItem]:
        return self.decay.apply(candidates)

    def _apply_value_weight(self, candidates: list[RetrievedItem]) -> list[RetrievedItem]:
        return apply_feedback_value_weight(self.index, candidates)

    def _apply_metadata_phrase_boost(
        self,
        query: str,
        candidates: list[RetrievedItem],
        *,
        allowed_ids: set[str] | None = None,
    ) -> list[RetrievedItem]:
        phrase_hits = _metadata_phrase_hits(
            self.index,
            query,
            allowed_ids=allowed_ids,
            limit=max(self.bm25_top, self.vector_top, 20),
        )
        if not phrase_hits:
            return candidates

        by_id = {candidate.id: candidate for candidate in candidates}
        for phrase_hit in phrase_hits:
            existing = by_id.get(phrase_hit.id)
            if existing is None:
                by_id[phrase_hit.id] = phrase_hit
                continue
            by_id[phrase_hit.id] = replace(
                existing,
                score=max(existing.score, phrase_hit.score),
                bm25_rank=existing.bm25_rank if existing.bm25_rank is not None else phrase_hit.bm25_rank,
            )
        return sorted(by_id.values(), key=lambda candidate: candidate.score, reverse=True)

    def _mmr_rerank(
        self, query: str, candidates: list[RetrievedItem], top_k: int,
    ) -> list[RetrievedItem]:
        """Maximal Marginal Relevance re-ranking for result diversity."""
        ids = [c.id for c in candidates]
        embeddings = self.index.get_embeddings(ids)
        return mmr_rerank(
            candidates,
            embeddings,
            top_k=top_k,
            lambda_=self.mmr_lambda,
        )

    def _expand_via_graph(
        self,
        candidates: list[RetrievedItem],
        top_k: int,
        *,
        allowed_ids: set[str] | None = None,
    ) -> list[RetrievedItem]:
        return expand_via_graph(
            self.index,
            candidates,
            top_k=top_k,
            graph_depth=self.graph_depth,
            allowed_ids=allowed_ids,
        )

    def _expand_via_hopfield(
        self,
        candidates: list[RetrievedItem],
        top_k: int,
        *,
        allowed_ids: set[str] | None = None,
    ) -> list[RetrievedItem]:
        return expand_via_hopfield(
            self.index,
            candidates,
            top_k=top_k,
            hopfield_top=self.hopfield_top,
            beta=self.hopfield_beta,
            allowed_ids=allowed_ids,
        )

    def _allowed_ids_for_filter(self, filters: SearchFilter) -> set[str] | None:
        if filters.is_empty and filters.include_superseded:
            return None
        return self.index.filter_ids(
            type=filters.type,
            project=filters.project,
            tags=filters.tags or None,
            exclude_tags=filters.exclude_tags or None,
            since_days=filters.since_days,
            tenant_id=filters.tenant_id,
            include_superseded=filters.include_superseded,
        )

    def _rerank_pool(
        self,
        query: str,
        candidates: list[RetrievedItem],
    ) -> list[RetrievedItem]:
        rerank_pool = candidates[: self.rerank_top]
        rest = candidates[self.rerank_top :]
        return [*self._rerank(query, rerank_pool), *rest]

    def _candidate_stages(
        self,
        options: _SearchPipelineOptions,
    ) -> tuple[_NamedCandidateStage, ...]:
        stages: list[_NamedCandidateStage] = [
            _NamedCandidateStage(
                "metadata_phrase",
                lambda candidates: self._apply_metadata_phrase_boost(
                    options.query,
                    candidates,
                    allowed_ids=options.allowed_ids,
                ),
            ),
            _NamedCandidateStage(
                "status_handoff_supplement",
                lambda candidates: supplement_status_handoff_candidates(
                    self.index,
                    options.query,
                    candidates,
                    allowed_ids=options.allowed_ids,
                ),
            ),
        ]
        if self.rerank:
            stages.append(
                _NamedCandidateStage(
                    "cross_encoder_rerank",
                    lambda candidates: self._rerank_pool(options.query, candidates),
                )
            )
        if self.apply_decay:
            stages.append(_NamedCandidateStage("decay", self._apply_decay))
        stages.extend(
            (
                _NamedCandidateStage("feedback_value", self._apply_value_weight),
                _NamedCandidateStage(
                    "status_handoff_boost",
                    lambda candidates: apply_status_handoff_boost(
                        self.index,
                        options.query,
                        candidates,
                    ),
                ),
                _NamedCandidateStage(
                    "runtime_evidence",
                    lambda candidates: apply_adapter_runtime_evidence_boost(
                        self.index,
                        options.query,
                        candidates,
                    ),
                ),
                _NamedCandidateStage(
                    "temporal_state_filter",
                    lambda candidates: filter_stale_temporal_state(
                        self.index,
                        candidates,
                        include_stale_state=options.include_stale_state,
                    ),
                ),
                _NamedCandidateStage(
                    "supersession_filter",
                    lambda candidates: filter_md_superseded_candidates(
                        self.index,
                        candidates,
                        include_superseded=options.include_superseded,
                    ),
                ),
            )
        )
        if self.mmr_lambda is not None:
            stages.append(
                _NamedCandidateStage(
                    "mmr",
                    lambda candidates: self._mmr_rerank(
                        options.query,
                        candidates,
                        options.top_k * 2,
                    ),
                )
            )
        if self.hopfield_expand:
            stages.append(
                _NamedCandidateStage(
                    "hopfield_expand",
                    lambda candidates: self._expand_via_hopfield(
                        candidates,
                        options.top_k,
                        allowed_ids=options.allowed_ids,
                    ),
                )
            )
        if self.graph_expand:
            stages.append(
                _NamedCandidateStage(
                    "graph_expand",
                    lambda candidates: self._expand_via_graph(
                        candidates,
                        options.top_k,
                        allowed_ids=options.allowed_ids,
                    ),
                )
            )
        return tuple(stages)

    def _run_candidate_pipeline(
        self,
        candidates: list[RetrievedItem],
        options: _SearchPipelineOptions,
    ) -> list[RetrievedItem]:
        for stage in self._candidate_stages(options):
            if not candidates:
                break
            candidates = stage.apply(candidates)
        return candidates

    def _run_candidate_pipeline_with_trace(
        self,
        candidates: list[RetrievedItem],
        options: _SearchPipelineOptions,
    ) -> tuple[list[RetrievedItem], dict[str, list[RetrievalStageTrace]]]:
        traces: dict[str, list[RetrievalStageTrace]] = {}
        for stage in self._candidate_stages(options):
            if not candidates:
                break
            before = _snapshot(candidates)
            candidates = stage.apply(candidates)
            after = _snapshot(candidates)
            for item_id in set(before) | set(after):
                effect = _stage_effect(before.get(item_id), after.get(item_id))
                if effect == "filtered":
                    continue
                traces.setdefault(item_id, []).append(
                    RetrievalStageTrace(
                        name=stage.name,
                        before_rank=before.get(item_id, (None, None))[0],
                        after_rank=after.get(item_id, (None, None))[0],
                        before_score=before.get(item_id, (None, None))[1],
                        after_score=after.get(item_id, (None, None))[1],
                        effect=effect,
                    )
                )
        return candidates, traces

    def _record_access(
        self,
        results: list[RetrievedItem],
        *,
        enabled: bool | None = None,
    ) -> None:
        should_record = self.record_access if enabled is None else enabled
        if not should_record or not results:
            return
        RetrievalAccessRecorder(
            index=self.index,
            reinforce_confidence=getattr(self, "reinforce_confidence", False),
            confidence_reward=getattr(self, "confidence_reward", 0.01),
        ).record(results)

    def record_accesses(self, results: list[RetrievedItem]) -> None:
        """Record final caller-approved hits using the instance access policy."""
        self._record_access(results)

    def search(
        self,
        query: str,
        top_k: int = 10,
        filters: SearchFilter | None = None,
        *,
        explain: bool = False,
        record_access: bool | None = None,
    ) -> list[RetrievedItem]:
        """Return the top matching memory IDs after retrieval policy stages.

        The query first goes through BM25/vector fusion, then through a fixed
        candidate pipeline for handoff supplementation, optional reranking,
        decay, value weighting, runtime/status boosts, stale-state filtering,
        supersession filtering, optional MMR, and optional graph expansion.
        ``record_access`` overrides the instance setting for this call without
        mutating shared retriever state.
        """
        filters = filters or SearchFilter()
        allowed_ids = self._allowed_ids_for_filter(filters)
        if allowed_ids is not None and not allowed_ids:
            return []

        candidates = self._rrf_fusion(query, allowed_ids=allowed_ids)
        initial = {candidate.id: candidate for candidate in candidates}
        options = _SearchPipelineOptions(
            query=query,
            top_k=top_k,
            allowed_ids=allowed_ids,
            include_superseded=filters.include_superseded,
            include_stale_state=filters.include_stale_state,
        )
        stage_traces: dict[str, list[RetrievalStageTrace]] = {}
        if explain:
            candidates, stage_traces = self._run_candidate_pipeline_with_trace(
                candidates,
                options,
            )
        else:
            candidates = self._run_candidate_pipeline(candidates, options)
        final = candidates[:top_k]
        if explain:
            final = [
                replace(
                    candidate,
                    trace=RetrievalTrace(
                        initial_bm25_rank=initial.get(candidate.id, candidate).bm25_rank,
                        initial_vector_rank=initial.get(candidate.id, candidate).vector_rank,
                        initial_score=initial.get(candidate.id, candidate).score,
                        final_rank=rank,
                        final_score=candidate.score,
                        stages=tuple(stage_traces.get(candidate.id, ())),
                        signals=_signals_for_trace(
                            initial.get(candidate.id, candidate),
                            stage_traces.get(candidate.id, ()),
                        ),
                    ),
                )
                for rank, candidate in enumerate(final, start=1)
            ]
        self._record_access(final, enabled=record_access)
        return final

    def search_routed(
        self,
        request: RecallRequest,
        *,
        top_k: int = 10,
        filters: SearchFilter | None = None,
        explain: bool = False,
        record_access: bool | None = None,
    ) -> RoutedSearchResult:
        """Generate and fuse independent lexical and semantic recall routes."""
        top_k = _normalize_routed_top_k(top_k)
        if top_k == 0:
            return RoutedSearchResult([], (), request.admission, {})
        if not request.admission.allowed:
            routes = tuple(
                RouteTrace(route, "skipped", 0.0, 0, "admission_rejected")
                for route in (
                    "lexical_terms",
                    "semantic_raw",
                    "lexical_raw_fallback",
                )
            )
            return RoutedSearchResult([], routes, request.admission, {})

        filters = filters or SearchFilter()
        scope = request.project_scope
        filter_plan = self._routed_filter_plan(request, filters)
        allowed_ids = filter_plan.allowed_ids
        excluded_ids = filter_plan.excluded_ids
        if allowed_ids is not None and not allowed_ids:
            return RoutedSearchResult([], (), request.admission, {})

        route_traces: list[RouteTrace] = []
        lexical_terms_hits: list[Hit] = []
        semantic_hits: list[Hit] = []
        lexical_raw_hits: list[Hit] = []
        semantic_similarities: dict[str, float] = {}

        if request.lexical_terms:
            started = time.perf_counter()
            try:
                terms_query = expand_query(
                    " ".join(request.lexical_terms),
                    use_or=self.query_expansion,
                )
                lexical_terms_hits = list(
                    self.index.bm25_search(
                        terms_query,
                        top_k=self.bm25_top,
                        allowed_ids=allowed_ids,
                        excluded_ids=excluded_ids or None,
                    )
                )
                lexical_terms_hits = _filter_route_hits(
                    lexical_terms_hits,
                    allowed_ids,
                    excluded_ids,
                )
                route_traces.append(
                    _completed_route_trace(
                        "lexical_terms",
                        started,
                        len(lexical_terms_hits),
                    )
                )
            except TimeoutError:
                lexical_terms_hits = []
                route_traces.append(
                    _failed_route_trace(
                        "lexical_terms",
                        started,
                        timeout=True,
                    )
                )
            except Exception:
                lexical_terms_hits = []
                route_traces.append(
                    _failed_route_trace(
                        "lexical_terms",
                        started,
                        timeout=False,
                    )
                )
        else:
            route_traces.append(
                RouteTrace(
                    "lexical_terms",
                    "skipped",
                    0.0,
                    0,
                    "lexical_terms_empty",
                )
            )

        semantic_available = (
            self.embedder is not None
            and not getattr(self.embedder, "degraded", False)
            and self.vector_weight > 0
            and self.vector_top > 0
        )
        semantic_unavailable = not semantic_available
        if not semantic_available:
            route_traces.append(
                RouteTrace(
                    "semantic_raw",
                    "skipped",
                    0.0,
                    0,
                    "semantic_not_ready",
                )
            )
        else:
            started = time.perf_counter()
            try:
                query_embedding = self.embedder.embed(request.normalized_query)
                semantic_hits = list(
                    self.index.vector_search(
                        query_embedding,
                        top_k=self.vector_top,
                        allowed_ids=allowed_ids,
                        excluded_ids=excluded_ids or None,
                    )
                )
                semantic_hits = _filter_route_hits(
                    semantic_hits,
                    allowed_ids,
                    excluded_ids,
                )
                result_embeddings = self.index.get_embeddings(
                    [str(hit.id) for hit in semantic_hits]
                )
                semantic_hits = [
                    hit for hit in semantic_hits if str(hit.id) in result_embeddings
                ]
                semantic_similarities = {
                    str(hit.id): _cosine_sim(query_embedding, item_embedding)
                    for hit in semantic_hits
                    if (item_embedding := result_embeddings.get(str(hit.id))) is not None
                }
                route_traces.append(
                    _completed_route_trace(
                        "semantic_raw",
                        started,
                        len(semantic_hits),
                    )
                )
            except TimeoutError:
                semantic_hits = []
                semantic_similarities = {}
                semantic_unavailable = True
                route_traces.append(
                    _failed_route_trace(
                        "semantic_raw",
                        started,
                        timeout=True,
                    )
                )
            except Exception:
                semantic_hits = []
                semantic_similarities = {}
                semantic_unavailable = True
                route_traces.append(
                    _failed_route_trace(
                        "semantic_raw",
                        started,
                        timeout=False,
                    )
                )

        if semantic_unavailable:
            started = time.perf_counter()
            try:
                raw_query = _raw_fallback_bm25_query(
                    request.normalized_query,
                    use_or=self.query_expansion,
                )
                lexical_raw_hits = list(
                    self.index.bm25_search(
                        raw_query,
                        top_k=self.bm25_top,
                        allowed_ids=allowed_ids,
                        excluded_ids=excluded_ids or None,
                    )
                )
                lexical_raw_hits = _filter_route_hits(
                    lexical_raw_hits,
                    allowed_ids,
                    excluded_ids,
                )
                route_traces.append(
                    _completed_route_trace(
                        "lexical_raw_fallback",
                        started,
                        len(lexical_raw_hits),
                    )
                )
            except TimeoutError:
                lexical_raw_hits = []
                route_traces.append(
                    _failed_route_trace(
                        "lexical_raw_fallback",
                        started,
                        timeout=True,
                    )
                )
            except Exception:
                lexical_raw_hits = []
                route_traces.append(
                    _failed_route_trace(
                        "lexical_raw_fallback",
                        started,
                        timeout=False,
                    )
                )

        candidates, evidence_by_id = fuse_routes(
            lexical_terms_hits=lexical_terms_hits,
            semantic_hits=semantic_hits,
            lexical_raw_hits=lexical_raw_hits,
            semantic_similarities=semantic_similarities,
            rrf_k=self.rrf_k,
        )
        if scope is not None and not scope.hard_filter and candidates:
            projects = self.index.get_projects([candidate.id for candidate in candidates])
            candidates = [
                replace(candidate, score=candidate.score * 1.05)
                if projects.get(candidate.id) == scope.value
                else candidate
                for candidate in candidates
            ]
            candidates.sort(key=lambda candidate: candidate.score, reverse=True)

        initial = {candidate.id: candidate for candidate in candidates}
        options = _SearchPipelineOptions(
            query=request.normalized_query,
            top_k=top_k,
            allowed_ids=allowed_ids,
            include_superseded=filters.include_superseded,
            include_stale_state=filters.include_stale_state,
        )
        stage_traces: dict[str, list[RetrievalStageTrace]] = {}
        if explain:
            candidates, stage_traces = self._run_candidate_pipeline_with_trace(
                candidates,
                options,
            )
        else:
            candidates = self._run_candidate_pipeline(candidates, options)
        if excluded_ids:
            candidates = [
                candidate for candidate in candidates if candidate.id not in excluded_ids
            ]
        final = candidates[:top_k]
        if explain:
            final = [
                replace(
                    candidate,
                    trace=RetrievalTrace(
                        initial_bm25_rank=initial.get(candidate.id, candidate).bm25_rank,
                        initial_vector_rank=initial.get(candidate.id, candidate).vector_rank,
                        initial_score=initial.get(candidate.id, candidate).score,
                        final_rank=rank,
                        final_score=candidate.score,
                        stages=tuple(stage_traces.get(candidate.id, [])),
                        signals=_signals_for_trace(
                            initial.get(candidate.id, candidate),
                            stage_traces.get(candidate.id, []),
                        ),
                    ),
                )
                for rank, candidate in enumerate(final, start=1)
            ]
        self._record_access(final, enabled=record_access)
        final_evidence = {
            candidate.id: evidence_by_id[candidate.id]
            for candidate in final
            if candidate.id in evidence_by_id
        }
        return RoutedSearchResult(
            final,
            tuple(route_traces),
            request.admission,
            final_evidence,
        )

    def _routed_filter_plan(
        self,
        request: RecallRequest,
        filters: SearchFilter,
    ) -> _RoutedFilterPlan:
        scope = request.project_scope
        if (
            scope is not None
            and scope.hard_filter
            and filters.project is not None
            and filters.project != scope.value
        ):
            return _RoutedFilterPlan(set(), set())
        effective_filters = filters
        if scope is not None and scope.hard_filter:
            effective_filters = replace(filters, project=scope.value)
        if effective_filters.is_empty:
            excluded_ids = (
                set()
                if effective_filters.include_superseded
                else self.index.get_superseded_ids()
            )
            return _RoutedFilterPlan(None, excluded_ids)
        return _RoutedFilterPlan(
            self._allowed_ids_for_filter(effective_filters),
            set(),
        )


def _snapshot(candidates: list[RetrievedItem]) -> dict[str, tuple[int, float]]:
    return {
        candidate.id: (rank, candidate.score)
        for rank, candidate in enumerate(candidates, start=1)
    }


def _filter_route_hits(
    hits: list[Hit],
    allowed_ids: set[str] | None,
    excluded_ids: set[str],
) -> list[Hit]:
    return [
        hit
        for hit in hits
        if (allowed_ids is None or str(getattr(hit, "id")) in allowed_ids)
        and str(getattr(hit, "id")) not in excluded_ids
    ]


def _raw_fallback_bm25_query(query: str, *, use_or: bool) -> str:
    """Build a bounded raw fallback query without treating long CJK as one phrase."""
    fragments: list[str] = []
    for run in _RAW_FALLBACK_RUN_RE.findall(query):
        if run.isascii() or len(run) <= 3:
            fragments.append(run)
            continue
        fragments.extend(run[index:index + 3] for index in range(len(run) - 2))
    fragments = list(dict.fromkeys(fragments))
    if len(fragments) > _RAW_FALLBACK_FRAGMENT_LIMIT:
        last = len(fragments) - 1
        fragments = [
            fragments[round(index * last / (_RAW_FALLBACK_FRAGMENT_LIMIT - 1))]
            for index in range(_RAW_FALLBACK_FRAGMENT_LIMIT)
        ]
    return expand_query("|".join(fragments), use_or=use_or)


def _completed_route_trace(
    route: str,
    started: float,
    candidate_count: int,
) -> RouteTrace:
    return RouteTrace(
        route,
        "ok",
        (time.perf_counter() - started) * 1000,
        candidate_count,
        "route_completed",
    )


def _failed_route_trace(
    route: str,
    started: float,
    *,
    timeout: bool,
) -> RouteTrace:
    return RouteTrace(
        route,
        "timeout" if timeout else "error",
        (time.perf_counter() - started) * 1000,
        0,
        "route_timeout" if timeout else "route_error",
    )


def _stage_effect(
    before: tuple[int, float] | None,
    after: tuple[int, float] | None,
) -> str:
    if before is None and after is not None:
        return "added"
    if before is not None and after is None:
        return "filtered"
    if before is None or after is None:
        return "kept"
    before_rank, before_score = before
    after_rank, after_score = after
    if after_score > before_score:
        return "boosted"
    if after_score < before_score:
        return "demoted"
    if after_rank != before_rank:
        return "reranked"
    return "kept"


def _signals_for_trace(
    initial: RetrievedItem,
    stages: list[RetrievalStageTrace],
) -> tuple[str, ...]:
    signals: list[str] = []
    if initial.bm25_rank is not None:
        signals.append("bm25")
    if initial.vector_rank is not None:
        signals.append("vector")
    for stage in stages:
        if stage.effect == "kept":
            continue
        signals.append(f"{stage.name}:{stage.effect}")
    return tuple(signals)


def _metadata_phrase_hits(
    index: HubIndex,
    query: str,
    *,
    allowed_ids: set[str] | None,
    limit: int,
) -> list[RetrievedItem]:
    connection = getattr(index, "connection", None)
    if connection is None:
        return []
    rows = connection.execute(
        "SELECT id, title, summary FROM items_meta",
    ).fetchall()
    query_norm = _normalize_metadata_phrase(query)
    query_tokens = set(_metadata_tokens(query_norm))
    if not query_tokens:
        return []
    hits: list[RetrievedItem] = []
    for item_id, title, summary in rows:
        if allowed_ids is not None and item_id not in allowed_ids:
            continue
        score = _metadata_phrase_score(
            query_norm,
            query_tokens,
            str(title or ""),
            str(summary or ""),
        )
        if score <= 0:
            continue
        # RRF scores are usually ~0.01-0.05. A metadata phrase hit is a
        # deterministic exact/near-exact metadata signal, so it should survive
        # the candidate pool before policy stages re-apply decay and filters.
        hits.append(RetrievedItem(id=item_id, score=1.0 + score, bm25_rank=0, vector_rank=None))
    hits.sort(key=lambda hit: hit.score, reverse=True)
    return hits[:limit]


def _metadata_phrase_score(
    query_norm: str,
    query_tokens: set[str],
    title: str,
    summary: str,
) -> float:
    title_norm = _normalize_metadata_phrase(title)
    summary_norm = _normalize_metadata_phrase(summary)
    if title_norm and (title_norm == query_norm or title_norm in query_norm):
        return 6.0
    if summary_norm and (summary_norm == query_norm or summary_norm in query_norm):
        return 5.0
    title_score = _token_coverage_score(query_tokens, set(_metadata_tokens(title_norm)), weight=3.0)
    summary_score = _token_coverage_score(query_tokens, set(_metadata_tokens(summary_norm)), weight=2.0)
    return max(title_score, summary_score)


def _token_coverage_score(
    query_tokens: set[str],
    metadata_tokens: set[str],
    *,
    weight: float,
) -> float:
    if len(metadata_tokens) < 3:
        return 0.0
    overlap = metadata_tokens & query_tokens
    coverage = len(overlap) / len(metadata_tokens)
    if len(overlap) < 3 or coverage < 0.78:
        return 0.0
    return weight * coverage


def _normalize_metadata_phrase(text: str) -> str:
    tokens = _metadata_tokens(text.lower().replace("-", " "))
    return " ".join(tokens)


def _metadata_tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in _METADATA_TOKEN_RE.finditer(text)]
