"""Generic scope isolation helpers for memory-derived profiles and recall.

The core model is deliberately domain-agnostic: projects, tenants, tags, and
item graph edges are evidence dimensions supplied by adapters and memory items.
Product domains can add richer metadata upstream without changing this module.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.store.items_store import ItemsStore

PUBLIC_PROFILE_SENSITIVITIES = frozenset({"public", "internal"})
DEFAULT_PROJECT_CONFIDENCE_THRESHOLD = 0.70
DEFAULT_PROJECT_AMBIGUITY_DELTA = 0.05


@dataclass(frozen=True)
class ScopeContext:
    """Runtime evidence that bounds which memories may shape a profile."""

    project: str | None = None
    tenant_id: str | None = None
    tags: tuple[str, ...] = ()
    seed_item_ids: tuple[str, ...] = ()
    related_item_ids: tuple[str, ...] = ()
    include_related: bool = True
    include_global: bool = True
    allowed_sensitivities: frozenset[str] = field(default_factory=lambda: PUBLIC_PROFILE_SENSITIVITIES)


@dataclass(frozen=True)
class ScopedMemory:
    item: MemoryItem
    body: str
    scope_match: str


@dataclass(frozen=True)
class ProjectScopeEvidence:
    source: str
    project: str
    confidence: float
    detail: str = ""


@dataclass(frozen=True)
class ProjectScopeCandidate:
    project: str
    confidence: float
    evidence: tuple[ProjectScopeEvidence, ...] = ()


@dataclass(frozen=True)
class ScopeResolution:
    project: str | None
    confidence: float
    status: str
    evidence: tuple[ProjectScopeEvidence, ...] = ()
    candidates: tuple[ProjectScopeCandidate, ...] = ()

    def to_scope_context(self, *, tenant_id: str | None = None) -> ScopeContext:
        return ScopeContext(project=self.project, tenant_id=tenant_id)


class ProjectScopeResolver:
    """Resolve a current project scope from structural evidence only."""

    def __init__(
        self,
        store: ItemsStore,
        *,
        threshold: float = DEFAULT_PROJECT_CONFIDENCE_THRESHOLD,
        ambiguity_delta: float = DEFAULT_PROJECT_AMBIGUITY_DELTA,
    ) -> None:
        self.store = store
        self.threshold = threshold
        self.ambiguity_delta = ambiguity_delta

    def resolve(
        self,
        *,
        explicit_project: str | None = None,
        cwd: str | None = None,
        repo: str | None = None,
        session_id: str | None = None,
        seed_item_ids: Iterable[str] | None = None,
    ) -> ScopeResolution:
        explicit = _clean_project(explicit_project)
        if explicit:
            evidence = ProjectScopeEvidence("explicit_project", explicit, 1.0)
            candidate = ProjectScopeCandidate(explicit, 1.0, (evidence,))
            return ScopeResolution(
                project=explicit,
                confidence=1.0,
                status="resolved",
                evidence=(evidence,),
                candidates=(candidate,),
            )

        evidence = []
        evidence.extend(self._seed_item_evidence(seed_item_ids))
        evidence.extend(self._session_evidence(session_id))
        evidence.extend(self._validity_evidence(cwd=cwd, repo=repo))
        evidence.extend(self._git_root_evidence(cwd=cwd, repo=repo))
        candidates = _build_project_candidates(evidence)
        return _choose_project(candidates, threshold=self.threshold, ambiguity_delta=self.ambiguity_delta)

    def _seed_item_evidence(self, seed_item_ids: Iterable[str] | None) -> list[ProjectScopeEvidence]:
        seeds = {item_id for item_id in (seed_item_ids or ()) if item_id}
        if not seeds:
            return []
        rows = []
        for item, _ in self.store.iter_all():
            if item.id in seeds and item.project:
                rows.append(ProjectScopeEvidence("seed_item", item.project, 0.86, item.id))
        return rows

    def _session_evidence(self, session_id: str | None) -> list[ProjectScopeEvidence]:
        if not session_id:
            return []
        rows = []
        for item, _ in self.store.iter_all():
            if item.session == session_id and item.project:
                rows.append(ProjectScopeEvidence("session_history", item.project, 0.82, item.id))
        return rows

    def _validity_evidence(self, *, cwd: str | None, repo: str | None) -> list[ProjectScopeEvidence]:
        current_paths = [_normalize_path(value) for value in (cwd, repo) if value]
        if not current_paths:
            return []

        rows = []
        for item, _ in self.store.iter_all():
            if not item.project:
                continue
            validity = item.validity
            stored_paths = [_normalize_path(value) for value in (validity.cwd, validity.repo) if value]
            if any(_paths_related(current, stored) for current in current_paths for stored in stored_paths):
                rows.append(ProjectScopeEvidence("validity_scope", item.project, 0.76, item.id))
        return rows

    def _git_root_evidence(self, *, cwd: str | None, repo: str | None) -> list[ProjectScopeEvidence]:
        root = _find_git_root(repo or cwd)
        if root is None:
            return []
        project = _clean_project(root.name)
        if not project:
            return []
        return [ProjectScopeEvidence("git_root", project, 0.74, str(root))]


def filter_items_for_scope(
    items: Iterable[tuple[MemoryItem, str]],
    scope: ScopeContext | None,
) -> list[ScopedMemory]:
    """Return memories visible under a scope with their match class.

    Match classes are:
    - ``exact``: direct tenant/project/tag/seed match, or no active scope.
    - ``related``: explicit graph-related item supplied by the caller.
    - ``global``: item has no project and may be reused inside the tenant.
    """

    effective_scope = scope or ScopeContext()
    scoped: list[ScopedMemory] = []
    for item, body in items:
        match = match_item_scope(item, effective_scope)
        if match is not None:
            scoped.append(ScopedMemory(item=item, body=body, scope_match=match))
    return scoped


def match_item_scope(item: MemoryItem, scope: ScopeContext) -> str | None:
    if str(item.sensitivity) not in scope.allowed_sensitivities:
        return None

    if scope.tenant_id is not None and item.tenant_id not in (None, scope.tenant_id):
        return None

    active_project = scope.project is not None
    active_tags = bool(scope.tags)
    active_seed = bool(scope.seed_item_ids)
    active_related = scope.include_related and bool(scope.related_item_ids)
    has_active_scope = active_project or active_tags or active_seed or active_related

    if not has_active_scope:
        return "exact"

    if active_project and item.project == scope.project:
        return "exact"
    if active_tags and set(scope.tags).intersection(item.tags):
        return "exact"
    if active_seed and item.id in scope.seed_item_ids:
        return "exact"
    if active_related and item.id in scope.related_item_ids:
        return "related"
    if scope.include_global and item.project is None:
        return "global"
    return None


def related_item_ids_from_graph(
    graph: Any,
    *,
    seed_item_ids: Iterable[str],
    depth: int = 1,
    relation_filter: Callable[[str], bool] | None = None,
) -> set[str]:
    """Resolve graph-related memory IDs from explicit refs evidence.

    ``graph`` only needs a ``get_refs(item_id)`` method returning
    ``(source_id, target_id, relation)`` tuples. Relation semantics are provided
    by the graph data or an optional caller-supplied predicate, not by domain
    vocabulary embedded in this module.
    """

    max_depth = max(0, int(depth))
    seeds = {item_id for item_id in seed_item_ids if item_id}
    visited = set(seeds)
    frontier = set(seeds)
    related: set[str] = set()

    for _ in range(max_depth):
        if not frontier:
            break
        next_frontier: set[str] = set()
        for item_id in frontier:
            for source_id, target_id, relation in graph.get_refs(item_id):
                if relation_filter is not None and not relation_filter(relation):
                    continue
                if source_id == item_id:
                    neighbor = target_id
                elif target_id == item_id:
                    neighbor = source_id
                else:
                    continue
                if neighbor in visited:
                    continue
                related.add(neighbor)
                next_frontier.add(neighbor)
        visited.update(next_frontier)
        frontier = next_frontier

    return related - seeds


def _build_project_candidates(evidence: Iterable[ProjectScopeEvidence]) -> tuple[ProjectScopeCandidate, ...]:
    grouped: dict[str, list[ProjectScopeEvidence]] = defaultdict(list)
    for row in evidence:
        grouped[row.project].append(row)

    candidates = []
    for project, rows in grouped.items():
        confidence = max(row.confidence for row in rows)
        confidence = min(0.95, confidence + max(0, len(rows) - 1) * 0.08)
        candidates.append(ProjectScopeCandidate(project=project, confidence=confidence, evidence=tuple(rows)))

    candidates.sort(key=lambda candidate: (-candidate.confidence, candidate.project))
    return tuple(candidates)


def _choose_project(
    candidates: tuple[ProjectScopeCandidate, ...],
    *,
    threshold: float,
    ambiguity_delta: float,
) -> ScopeResolution:
    if not candidates:
        return ScopeResolution(project=None, confidence=0.0, status="unresolved")

    top = candidates[0]
    if top.confidence < threshold:
        return ScopeResolution(
            project=None,
            confidence=top.confidence,
            status="candidate",
            evidence=top.evidence,
            candidates=candidates,
        )

    if len(candidates) > 1 and candidates[1].confidence >= threshold:
        if top.confidence - candidates[1].confidence <= ambiguity_delta:
            return ScopeResolution(
                project=None,
                confidence=top.confidence,
                status="ambiguous",
                evidence=top.evidence,
                candidates=candidates,
            )

    return ScopeResolution(
        project=top.project,
        confidence=top.confidence,
        status="resolved",
        evidence=top.evidence,
        candidates=candidates,
    )


def _find_git_root(path: str | None) -> Path | None:
    if not path:
        return None
    current = _normalize_path(path)
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _normalize_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def _paths_related(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


def _clean_project(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip()
    if not stripped:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", stripped)
    cleaned = re.sub(r"-+", "-", cleaned)
    cleaned = re.sub(r"\.+", ".", cleaned)
    cleaned = cleaned.strip("-.")
    return cleaned or None


__all__ = [
    "PUBLIC_PROFILE_SENSITIVITIES",
    "ProjectScopeCandidate",
    "ProjectScopeEvidence",
    "ProjectScopeResolver",
    "ScopeContext",
    "ScopeResolution",
    "ScopedMemory",
    "filter_items_for_scope",
    "match_item_scope",
    "related_item_ids_from_graph",
]
