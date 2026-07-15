"""Immutable contracts shared by routed recall implementations."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Literal, get_args

from agent_brain.memory.context.query_signal import QuerySignal
from agent_brain.memory.recall.admission import RecallAdmission
from agent_brain.memory.recall.retrieval_types import RetrievedItem

ProjectScopeSource = Literal["explicit", "cwd", "agent_inferred"]
RouteStatus = Literal["ok", "skipped", "timeout", "error"]
RouteReason = Literal[
    "route_completed",
    "admission_rejected",
    "lexical_terms_empty",
    "semantic_not_ready",
    "route_timeout",
    "route_error",
]

_PROJECT_SCOPE_SOURCES = frozenset(get_args(ProjectScopeSource))
_ROUTE_STATUSES = frozenset(get_args(RouteStatus))
_ROUTE_REASONS = frozenset(get_args(RouteReason))


@dataclass(frozen=True)
class ProjectScope:
    """Project provenance and whether it is safe to use as a hard filter."""

    value: str
    source: ProjectScopeSource
    hard_filter: bool = False

    def __post_init__(self) -> None:
        if self.source not in _PROJECT_SCOPE_SOURCES:
            raise ValueError(f"unsupported project scope source: {self.source!r}")
        if self.hard_filter and self.source != "explicit":
            raise ValueError("hard project filtering requires an explicit scope")


@dataclass(frozen=True)
class RecallRequest:
    """Complete routed-recall request with raw evidence preserved."""

    raw_query: str
    normalized_query: str
    lexical_terms: tuple[str, ...]
    admission: RecallAdmission
    query_signal: QuerySignal
    project_scope: ProjectScope | None
    cwd: str | None
    adapter: str
    session_id: str | None


@dataclass(frozen=True)
class RouteTrace:
    """Bounded route diagnostics that never contain user prompt text."""

    route: str
    status: RouteStatus
    latency_ms: float
    candidate_count: int
    reason: RouteReason

    def __post_init__(self) -> None:
        if self.status not in _ROUTE_STATUSES:
            raise ValueError(f"unsupported route status: {self.status!r}")
        if self.reason not in _ROUTE_REASONS:
            raise ValueError(f"unsupported route reason: {self.reason!r}")


@dataclass(frozen=True)
class RouteEvidence:
    """Independent retrieval evidence for one result.

    ``semantic_similarity`` is cosine evidence from the semantic route. It is
    deliberately separate from backend ``Hit.score`` and fused RRF scores.
    """

    routes: tuple[str, ...]
    semantic_similarity: float | None
    semantic_rank: int | None
    lexical_terms_rank: int | None
    lexical_raw_rank: int | None


@dataclass(frozen=True)
class RoutedSearchResult:
    """Routed hits and their independent per-id evidence."""

    hits: list[RetrievedItem]
    routes: tuple[RouteTrace, ...]
    admission: RecallAdmission
    evidence_by_id: Mapping[str, RouteEvidence]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "evidence_by_id",
            MappingProxyType(dict(self.evidence_by_id)),
        )


__all__ = [
    "ProjectScope",
    "ProjectScopeSource",
    "RecallRequest",
    "RouteEvidence",
    "RouteReason",
    "RoutedSearchResult",
    "RouteStatus",
    "RouteTrace",
]
