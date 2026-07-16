"""Fail-closed routed recall orchestration for CLI prompt surfaces."""

from __future__ import annotations

import hashlib
import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping, cast

from agent_brain.memory.context.context_firewall_types import ContextCandidate
from agent_brain.memory.context.context_loading import ContextVerbosity
from agent_brain.memory.context.injection_gateway import (
    HYDRATE_ERROR_REASON,
    InjectionResult,
    _record_injection_diagnostic,
    build_injection_context,
    injection_exclusion_reason_counts,
    injection_retrieval_top_k,
    surface_injection_metrics,
)
from agent_brain.memory.context.injection_query_context import InjectionQueryContext
from agent_brain.memory.recall.admission import build_recall_request
from agent_brain.memory.recall.retrieval import SearchFilter
from agent_brain.memory.recall.retrieval_types import RetrievedItem
from agent_brain.memory.recall.routed_types import (
    ProjectScope,
    RecallRequest,
    RouteEvidence,
    RoutedSearchResult,
    RouteTrace,
)

logger = logging.getLogger(__name__)

HookStatus = Literal["injected", "empty", "timeout", "error"]
_HOOK_STATUSES = frozenset({"injected", "empty", "timeout", "error"})
_ROUTE_STATUSES = frozenset({"ok", "skipped", "timeout", "error"})
_ROUTE_NAMES = frozenset({"lexical_terms", "semantic_raw", "lexical_raw_fallback"})
_ROUTE_REASONS_BY_STATUS = {
    "ok": frozenset({"route_completed"}),
    "skipped": frozenset({"admission_rejected", "lexical_terms_empty", "semantic_not_ready"}),
    "timeout": frozenset({"route_timeout"}),
    "error": frozenset({"route_error"}),
}


@dataclass(frozen=True)
class HookSearchPayload:
    """Stable, privacy-bounded protocol consumed by short-lived hooks."""

    status: HookStatus
    reason: str
    context: str
    routes: tuple[Mapping[str, object], ...]

    def __post_init__(self) -> None:
        if self.status not in _HOOK_STATUSES:
            raise ValueError("unsupported hook status")
        if self.status == "injected" and not self.context:
            raise ValueError("injected hook payload requires context")
        if self.status != "injected" and self.context:
            raise ValueError("non-injected hook payload must not contain context")
        object.__setattr__(
            self,
            "routes",
            tuple(MappingProxyType(dict(route)) for route in self.routes),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "context": self.context,
            "routes": [dict(route) for route in self.routes],
        }


def execute_routed_query(
    *,
    raw_query: str,
    store: Any,
    retriever: Any,
    top_k: int,
    filters: SearchFilter,
    requested: ContextVerbosity,
    project: str | None,
    adapter: str,
    session_id: str | None,
    cwd: str | None,
    brain_dir: Path | None = None,
    prefer_type: list[str] | None = None,
    record_injection_cohort: bool = False,
    record_recall_gap: bool = False,
) -> HookSearchPayload:
    """Retrieve, govern, pack, account, and render one routed CLI query.

    Every candidate generator, including the rollback generator, converges on
    the same Gateway. Exceptions fail closed and never expose raw hits.
    """

    try:
        scope = ProjectScope(project, "explicit", hard_filter=True) if project is not None else None
        request = build_recall_request(
            raw_query,
            adapter=adapter,
            project_scope=scope,
            cwd=cwd,
            session_id=session_id,
        )
        routed = _generate_candidates(
            request=request,
            retriever=retriever,
            top_k=injection_retrieval_top_k(top_k),
            filters=filters,
            use_routed=os.environ.get("AGENT_MEMORY_HUB_ROUTED_RECALL") != "0",
        )
        if routed.admission != request.admission:
            raise ValueError("conflicting routed admission")
        routes = _serialize_routes(routed.routes)
        if not request.admission.allowed:
            _maybe_record_gap(
                enabled=record_recall_gap,
                brain_dir=brain_dir,
                raw_query=raw_query,
                reason="query_not_injectable",
                adapter=adapter,
                session_id=session_id,
                cwd=cwd,
                retrieved_count=len(routed.hits),
                injection=None,
                hydrate_error_count=0,
            )
            return HookSearchPayload("empty", "admission_rejected", "", routes)
        if not routed.hits:
            _maybe_record_gap(
                enabled=record_recall_gap,
                brain_dir=brain_dir,
                raw_query=raw_query,
                reason="empty_recall",
                adapter=adapter,
                session_id=session_id,
                cwd=cwd,
                retrieved_count=0,
                injection=None,
                hydrate_error_count=0,
            )
            return HookSearchPayload("empty", "no_candidates", "", routes)

        items_by_id = {item.id: (item, body) for item, body in store.iter_all()}
        hydrate_error_count = sum(1 for hit in routed.hits if hit.id not in items_by_id)
        _record_injection_diagnostic(
            surface="cli-routed-search",
            reason=HYDRATE_ERROR_REASON,
            count=hydrate_error_count,
        )
        type_order = prefer_type or []
        candidates = [
            ContextCandidate(
                item=items_by_id[hit.id][0],
                body=items_by_id[hit.id][1],
                score=_candidate_score(
                    hit.score,
                    str(items_by_id[hit.id][0].type),
                    type_order,
                ),
                source="cli-routed-search",
            )
            for hit in routed.hits
            if hit.id in items_by_id
        ]
        query_context = InjectionQueryContext(
            raw_query=request.raw_query,
            admission=request.admission,
            query_signal=request.query_signal,
            evidence_by_id=routed.evidence_by_id,
        )
        current_scope: dict[str, str] = {}
        if cwd:
            current_scope["cwd"] = cwd
        if adapter != "unknown":
            current_scope["adapter"] = adapter
        injection = build_injection_context(
            candidates,
            query_context=query_context,
            requested=requested,
            max_items=top_k,
            current_scope=current_scope or None,
        )
        hit_by_id = {hit.id: hit for hit in routed.hits}
        included_hits = [
            hit_by_id[entry.decision.candidate.item.id]
            for entry in injection.included
            if entry.decision.candidate.item.id in hit_by_id
        ]
        retriever.record_accesses(included_hits)

        if not injection.included:
            _maybe_record_gap(
                enabled=record_recall_gap,
                brain_dir=brain_dir,
                raw_query=raw_query,
                reason="all_candidates_rejected",
                adapter=adapter,
                session_id=session_id,
                cwd=cwd,
                retrieved_count=len(routed.hits),
                injection=injection,
                hydrate_error_count=hydrate_error_count,
            )
            return HookSearchPayload("empty", "all_rejected", "", routes)

        metrics = cast(
            dict[str, object],
            surface_injection_metrics(
                injection,
                raw_candidate_count=len(routed.hits),
                hydrate_error_count=hydrate_error_count,
            ),
        )
        _maybe_record_cohort(
            enabled=record_injection_cohort,
            brain_dir=brain_dir,
            item_ids=[hit.id for hit in included_hits],
            adapter=adapter,
            session_id=session_id,
            cwd=cwd,
            raw_query=raw_query,
            query_terms=list(request.lexical_terms),
            pack_metrics=metrics,
        )
        if record_recall_gap and (injection.excluded or hydrate_error_count):
            _maybe_record_gap(
                enabled=True,
                brain_dir=brain_dir,
                raw_query=raw_query,
                reason="partial_candidates_rejected",
                adapter=adapter,
                session_id=session_id,
                cwd=cwd,
                retrieved_count=len(routed.hits),
                injection=injection,
                hydrate_error_count=hydrate_error_count,
            )
        context = _render_included_context(injection)
        if not context:
            raise RuntimeError("empty packed context")
        return HookSearchPayload("injected", "included", context, routes)
    except TimeoutError:
        logger.warning("routed CLI query timed out")
        return HookSearchPayload("timeout", "overall_timeout", "", ())
    except Exception:  # noqa: BLE001 - hook protocol must fail closed
        logger.warning("routed CLI query failed")
        return HookSearchPayload("error", "internal_error", "", ())


def _generate_candidates(
    *,
    request: RecallRequest,
    retriever: Any,
    top_k: int,
    filters: SearchFilter,
    use_routed: bool,
) -> RoutedSearchResult:
    if use_routed:
        return retriever.search_routed(
            request,
            top_k=top_k,
            filters=filters,
            explain=False,
            record_access=False,
        )

    effective_query = request.raw_query
    route_name = "lexical_raw_fallback"
    if request.query_signal.injectable and request.lexical_terms:
        effective_query = "|".join(request.lexical_terms)
        route_name = "lexical_terms"
    hits = retriever.search(
        effective_query,
        top_k=top_k,
        filters=filters,
        explain=False,
        record_access=False,
    )
    evidence = {
        hit.id: RouteEvidence(
            routes=(route_name,),
            semantic_similarity=None,
            semantic_rank=None,
            lexical_terms_rank=rank if route_name == "lexical_terms" else None,
            lexical_raw_rank=rank if route_name == "lexical_raw_fallback" else None,
        )
        for rank, hit in enumerate(hits, start=1)
    }
    trace = RouteTrace(
        route_name,
        "ok",
        0.0,
        len(hits),
        "route_completed",
    )
    return RoutedSearchResult(hits, (trace,), request.admission, evidence)


def _serialize_routes(
    traces: tuple[RouteTrace, ...],
) -> tuple[Mapping[str, object], ...]:
    rows: list[Mapping[str, object]] = []
    for trace in traces:
        if trace.status not in _ROUTE_STATUSES:
            raise ValueError("malformed route status")
        if trace.route not in _ROUTE_NAMES:
            raise ValueError("malformed route name")
        if trace.reason not in _ROUTE_REASONS_BY_STATUS[trace.status]:
            raise ValueError("malformed route reason")
        if type(trace.candidate_count) is not int or trace.candidate_count < 0:
            raise ValueError("malformed route candidate count")
        if (
            isinstance(trace.latency_ms, bool)
            or not isinstance(trace.latency_ms, (int, float))
            or not math.isfinite(trace.latency_ms)
            or trace.latency_ms < 0
        ):
            raise ValueError("malformed route latency")
        rows.append(
            {
                "route": trace.route,
                "status": trace.status,
                "candidate_count": trace.candidate_count,
                "reason": trace.reason,
            }
        )
    return tuple(rows)


def _render_included_context(injection: InjectionResult) -> str:
    blocks: list[str] = []
    for entry in injection.included:
        item = entry.decision.candidate.item
        pack = entry.pack
        confidence = (
            f" conf:{item.confidence:.1f}"
            if item.confidence is not None and item.confidence < 1.0
            else ""
        )
        lines = [
            f"[{item.type}] **{item.title}** (id:{item.id}{confidence})",
            "  "
            f"view={pack.selected_view} "
            f"packed={pack.packed_tokens}/{pack.full_tokens}t "
            f'retrieve="{pack.cli_retrieve_hint}"',
        ]
        lines.extend(f"  {line}" for line in pack.text.splitlines())
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _candidate_score(score: float, item_type: str, type_order: list[str]) -> float:
    try:
        priority = type_order.index(item_type)
    except ValueError:
        return score
    return score + float(len(type_order) - priority)


def _maybe_record_cohort(
    *,
    enabled: bool,
    brain_dir: Path | None,
    item_ids: list[str],
    adapter: str,
    session_id: str | None,
    cwd: str | None,
    raw_query: str,
    query_terms: list[str],
    pack_metrics: dict[str, object],
) -> None:
    if not enabled or not item_ids or brain_dir is None:
        return
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort

    record_injection_cohort(
        brain_dir,
        item_ids=item_ids,
        adapter=adapter,
        session_id=session_id,
        cwd=cwd,
        query=raw_query,
        query_terms=query_terms,
        pack_metrics=pack_metrics,
    )


def _maybe_record_gap(
    *,
    enabled: bool,
    brain_dir: Path | None,
    raw_query: str,
    reason: str,
    adapter: str,
    session_id: str | None,
    cwd: str | None,
    retrieved_count: int,
    injection: InjectionResult | None,
    hydrate_error_count: int,
) -> None:
    if not enabled or brain_dir is None:
        return
    from agent_brain.memory.governance.recall_events import record_gap

    excluded = injection.excluded if injection is not None else []
    included_count = len(injection.included) if injection is not None else 0
    evidence = [
        f"retrieved_count={retrieved_count}",
        f"included_count={included_count}",
        f"hydrate_error_count={hydrate_error_count}",
        f"excluded_count={len(excluded) + hydrate_error_count}",
    ]
    reason_counts = injection_exclusion_reason_counts(
        excluded,
        hydrate_error_count=hydrate_error_count,
    )
    evidence.extend(f"excluded_reason.{key}={count}" for key, count in reason_counts.items())
    record_gap(
        brain_dir,
        query="sha256:" + hashlib.sha256(raw_query.encode("utf-8")).hexdigest(),
        reason=reason,
        injected_ids=[],
        rejected_ids=[],
        evidence=evidence,
        adapter=adapter,
        session_id=session_id,
        cwd=cwd,
    )


__all__ = [
    "HookSearchPayload",
    "HookStatus",
    "execute_routed_query",
]
