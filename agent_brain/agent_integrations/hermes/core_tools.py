"""Hermes core memory-provider tool implementations."""

from __future__ import annotations

from collections import Counter
from typing import Any, Callable

from agent_brain.memory.recall.retrieval import Retriever, SearchFilter
from agent_brain.agent_integrations.hermes.context import build_active_recall_payload
from agent_brain.agent_integrations.hermes.profile import enrich_profile_with_preferences
from agent_brain.agent_integrations.hermes.remember import hub_remember_impl
from agent_brain.agent_integrations.hermes.search import format_search_hits
from agent_brain.memory.scope import (
    ProjectScopeResolver,
    ScopeContext,
    ScopeResolution,
    filter_items_for_scope,
    related_item_ids_from_graph,
)


ComponentsFactory = Callable[[], tuple[Any, Any, Any]]
EmbedderFactory = Callable[[], Any]
RememberFunc = Callable[..., dict[str, Any]]


def hub_search_impl(
    components: ComponentsFactory,
    embedder_factory: EmbedderFactory,
    query: str,
    top_k: int = 10,
    type: str | None = None,
    project: str | None = None,
    tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    since_days: int | None = None,
    graph_expand: bool = False,
    tenant_id: str | None = None,
    mmr_lambda: float | None = None,
) -> list[dict[str, Any]]:
    """Search the brain pool for relevant memories."""
    store, idx, _ = components()
    embedder = embedder_factory()
    retriever = Retriever(
        index=idx,
        embedder=embedder,
        graph_expand=graph_expand,
        graph_depth=1,
        mmr_lambda=mmr_lambda,
    )
    items_by_id: dict[str, Any] = {}
    bodies_by_id: dict[str, str] = {}
    for it, body in store.iter_all():
        items_by_id[it.id] = it
        bodies_by_id[it.id] = body
    sf = SearchFilter(
        type=type,
        project=project,
        tags=tags or [],
        exclude_tags=exclude_tags or [],
        since_days=since_days,
        tenant_id=tenant_id,
    )
    hits = retriever.search(query, top_k=top_k, filters=sf)
    return format_search_hits(hits=hits, items_by_id=items_by_id, bodies_by_id=bodies_by_id)


def hub_profile_impl(
    components: ComponentsFactory,
    *,
    project: str | None = None,
    tenant_id: str | None = None,
    scope_item_ids: list[str] | None = None,
    include_related: bool = True,
    cwd: str | None = None,
    repo: str | None = None,
    session_id: str | None = None,
    auto_resolve_project: bool = True,
) -> dict[str, Any]:
    """Return a profile summary of the brain pool."""
    store, idx, _ = components()
    seed_item_ids = tuple(scope_item_ids or ())
    resolution: ScopeResolution | None = None
    if auto_resolve_project and (
        project is not None
        or cwd is not None
        or repo is not None
        or session_id is not None
        or seed_item_ids
    ):
        resolution = ProjectScopeResolver(store).resolve(
            explicit_project=project,
            cwd=cwd,
            repo=repo,
            session_id=session_id,
            seed_item_ids=seed_item_ids,
        )
        if resolution.status == "resolved":
            project = resolution.project
    related_item_ids: tuple[str, ...] = ()
    if include_related and seed_item_ids:
        related_item_ids = tuple(sorted(related_item_ids_from_graph(idx, seed_item_ids=seed_item_ids, depth=1)))
    scope_context = ScopeContext(
        project=project,
        tenant_id=tenant_id,
        seed_item_ids=seed_item_ids,
        related_item_ids=related_item_ids,
        include_related=include_related,
    )
    items = [(scoped.item, scoped.body) for scoped in filter_items_for_scope(store.iter_all(), scope_context)]
    if not items:
        result: dict[str, Any] = {"summary": "Empty brain pool", "total_items": 0}
        if project is not None or tenant_id is not None or seed_item_ids:
            result["scope_filter"] = _scope_filter_payload(scope_context)
        if resolution is not None:
            result["scope_resolution"] = _scope_resolution_payload(resolution)
        return result

    type_counts = Counter(str(it.type) for it, _ in items)
    project_counts = Counter(it.project for it, _ in items if it.project)
    agent_counts = Counter(it.agent for it, _ in items if it.agent)

    sorted_items = sorted(items, key=lambda p: p[0].created_at, reverse=True)
    recent_titles = [it.title for it, _ in sorted_items[:5]]

    top_projects = [p for p, _ in project_counts.most_common(5)]
    summary = (
        f"Brain pool with {len(items)} items across {len(project_counts)} projects. "
        f"Types: {', '.join(f'{t}({c})' for t, c in type_counts.most_common())}. "
        f"Most active: {', '.join(top_projects[:3]) if top_projects else 'no project tags'}."
    )

    result = {
        "summary": summary,
        "total_items": len(items),
        "type_counts": dict(type_counts),
        "project_counts": dict(project_counts.most_common(10)),
        "agent_counts": dict(agent_counts),
        "recent_titles": recent_titles,
    }
    if project is not None or tenant_id is not None or seed_item_ids:
        result["scope_filter"] = _scope_filter_payload(scope_context)
    if resolution is not None:
        result["scope_resolution"] = _scope_resolution_payload(resolution)

    return enrich_profile_with_preferences(result, store, scope_context=scope_context)


def _scope_filter_payload(scope: ScopeContext) -> dict[str, Any]:
    return {
        "project": scope.project,
        "tenant_id": scope.tenant_id,
        "seed_item_ids": list(scope.seed_item_ids),
        "related_item_ids": list(scope.related_item_ids),
        "include_related": scope.include_related,
        "include_global": scope.include_global,
    }


def _scope_resolution_payload(resolution: ScopeResolution) -> dict[str, Any]:
    return {
        "project": resolution.project,
        "confidence": resolution.confidence,
        "status": resolution.status,
        "evidence": [
            {
                "source": evidence.source,
                "project": evidence.project,
                "confidence": evidence.confidence,
            }
            for evidence in resolution.evidence
        ],
        "candidates": [
            {
                "project": candidate.project,
                "confidence": candidate.confidence,
                "evidence_sources": sorted({evidence.source for evidence in candidate.evidence}),
            }
            for candidate in resolution.candidates
        ],
    }


def hub_context_impl(
    components: ComponentsFactory,
    project: str | None = None,
    limit: int = 20,
    task_hint: str | None = None,
    cwd: str | None = None,
    repo: str | None = None,
    session_id: str | None = None,
    auto_resolve_project: bool = True,
) -> dict[str, Any]:
    """Return contextual memories for the current working session."""
    store, _idx, retriever = components()
    resolution: ScopeResolution | None = None
    if auto_resolve_project and (project is not None or cwd is not None or repo is not None or session_id is not None):
        resolution = ProjectScopeResolver(store).resolve(
            explicit_project=project,
            cwd=cwd,
            repo=repo,
            session_id=session_id,
        )
        if resolution.status == "resolved":
            project = resolution.project
    items = list(store.iter_all())
    if project:
        items = [(it, b) for it, b in items if it.project == project]

    items.sort(key=lambda p: p[0].created_at, reverse=True)
    items = items[:limit]

    result = {
        "project_filter": project,
        "count": len(items),
        "items": [
            {
                "id": it.id,
                "type": str(it.type),
                "title": it.title,
                "summary": it.summary,
                "confidence": it.confidence,
                "created_at": it.created_at.isoformat(),
                "tags": it.tags,
            }
            for it, _ in items
        ],
    }
    if resolution is not None:
        result["scope_resolution"] = _scope_resolution_payload(resolution)

    if task_hint and retriever is not None:
        result["active_recall"] = build_active_recall_payload(
            retriever=retriever,
            task_hint=task_hint,
            project=project,
        )

    return result


def hub_conclude_impl(
    remember_func: RememberFunc,
    session_summary: str,
    key_decisions: list[str] | None = None,
    agent: str | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Record a session conclusion / handoff into the brain pool."""
    body_parts = [f"**Session Summary**: {session_summary}"]
    if key_decisions:
        body_parts.append("\n**Key Decisions**:")
        for decision in key_decisions:
            body_parts.append(f"- {decision}")

    return remember_func(
        content="\n".join(body_parts),
        title=f"Session conclusion: {session_summary[:60]}",
        type="handoff",
        tags=["session-conclude", "hermes"],
        project=project,
        agent=agent,
    )
