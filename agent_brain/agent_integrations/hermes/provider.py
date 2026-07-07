"""Hermes memory provider — maps our brain pool to Hermes's 5 standard tools.

Hermes (Nous Research) defines a standard memory-provider interface with
five tools: ``hub_search``, ``hub_remember``, ``hub_profile``,
``hub_context``, ``hub_conclude``.  This module implements all five by
delegating to our existing core modules.

Usage as a standalone MCP server::

    python -m agent_brain.agent_integrations.hermes.provider

Or import and register on an existing FastMCP instance::

    from agent_brain.agent_integrations.hermes.provider import register_hermes_tools
    register_hermes_tools(mcp_instance)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agent_brain._version import __version__
from agent_brain.platform.embedding import get_default_embedder
from agent_brain.memory.store.items_store import make_item_id
from agent_brain.agent_integrations.hermes.components import build_components, build_store
from agent_brain.agent_integrations.hermes.core_tools import (
    hub_conclude_impl,
    hub_context_impl,
    hub_profile_impl,
    hub_remember_impl,
    hub_search_impl,
)
from agent_brain.agent_integrations.hermes.governance_tools import (
    hub_drift_impl,
    hub_evolve_impl,
    hub_govern_impl,
    hub_stats_impl,
    hub_tag_suggest_impl,
)
from agent_brain.agent_integrations.hermes.import_export_tools import (
    hub_gc_impl,
    hub_import_impl,
    hub_obsidian_export_impl,
    hub_obsidian_import_impl,
)
from agent_brain.agent_integrations.hermes.item_tools import (
    hub_batch_confirm_impl,
    hub_delete_impl,
    hub_graph_impl,
    hub_link_impl,
    hub_list_impl,
    hub_read_impl,
    hub_unlink_impl,
    hub_update_impl,
)
from agent_brain.agent_integrations.hermes.provider_registry import build_hermes_tools
from agent_brain.agent_integrations.hermes.provider_server import register_tools, run_provider


def _brain_dir() -> Path:
    return Path(os.environ.get("BRAIN_DIR", os.path.expanduser("~/.agent-memory-hub")))


def _components():
    return build_components(_brain_dir(), get_default_embedder)


def _store():
    return build_store(_brain_dir())


def hub_search(
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
    """Search the brain pool for relevant memories.

    Hermes standard: ``hub_search(query) → list[{id, title, type, score, summary}]``

    When graph_expand=True, search results are expanded with knowledge-graph
    neighbors (items linked via refs.mems). tenant_id isolates to a single tenant.
    mmr_lambda (0.0-1.0) enables MMR diversity re-ranking.
    """
    return hub_search_impl(
        _components,
        get_default_embedder,
        query=query,
        top_k=top_k,
        type=type,
        project=project,
        tags=tags,
        exclude_tags=exclude_tags,
        since_days=since_days,
        graph_expand=graph_expand,
        tenant_id=tenant_id,
        mmr_lambda=mmr_lambda,
    )


def hub_remember(
    content: str,
    title: str,
    type: str = "fact",
    tags: list[str] | None = None,
    refs: dict | None = None,
    project: str | None = None,
    agent: str | None = None,
    confidence: float = 0.7,
    tenant_id: str | None = None,
    allow_unsafe: bool = False,
) -> dict[str, Any]:
    """Store a new memory in the brain pool.

    Hermes standard: ``hub_remember(content, metadata) → {id, stored}``

    Content is audited (防进 gate) before it lands; a critical/high finding
    blocks the write unless ``allow_unsafe=True``.
    """
    return hub_remember_impl(
        _components,
        get_default_embedder,
        make_item_id,
        content=content,
        title=title,
        type=type,
        tags=tags,
        refs=refs,
        project=project,
        agent=agent,
        confidence=confidence,
        tenant_id=tenant_id,
        allow_unsafe=allow_unsafe,
        store_factory=_store,
    )


def hub_profile(
    project: str | None = None,
    tenant_id: str | None = None,
    scope_item_ids: list[str] | None = None,
    include_related: bool = True,
    cwd: str | None = None,
    repo: str | None = None,
    session_id: str | None = None,
    auto_resolve_project: bool = True,
) -> dict[str, Any]:
    """Return a profile summary of the brain pool.

    Hermes standard: ``hub_profile() → {summary, stats}``
    Aggregates memory types, projects, agents, and recent activity.
    Optional scope parameters bound derived preferences and recent summaries to
    runtime evidence instead of the whole memory pool.
    """
    return hub_profile_impl(
        _components,
        project=project,
        tenant_id=tenant_id,
        scope_item_ids=scope_item_ids,
        include_related=include_related,
        cwd=cwd,
        repo=repo,
        session_id=session_id,
        auto_resolve_project=auto_resolve_project,
    )


def hub_context(
    project: str | None = None,
    limit: int = 20,
    task_hint: str | None = None,
    cwd: str | None = None,
    repo: str | None = None,
    session_id: str | None = None,
    auto_resolve_project: bool = True,
) -> dict[str, Any]:
    """Return contextual memories for the current working session.

    Hermes standard: ``hub_context(scope) → {items, summary}``
    Returns recent and relevant items, optionally scoped to a project.
    When task_hint is provided, also performs Active Recall to surface
    the most relevant policies/skills for the upcoming work.
    """
    return hub_context_impl(
        _components,
        project=project,
        limit=limit,
        task_hint=task_hint,
        cwd=cwd,
        repo=repo,
        session_id=session_id,
        auto_resolve_project=auto_resolve_project,
    )


def hub_graph(item_id: str, depth: int = 1) -> dict[str, Any]:
    """Query knowledge-graph connections for a memory item.

    Returns edges (from refs.mems) and all reachable neighbors within
    depth hops (bidirectional traversal).
    """
    return hub_graph_impl(_components, item_id, depth=depth)


def hub_link(
    source_id: str,
    target_id: str,
    relation: str = "refs",
) -> dict[str, Any]:
    """Create a knowledge-graph link between two memory items."""
    return hub_link_impl(_components, source_id, target_id, relation=relation)


def hub_unlink(
    source_id: str,
    target_id: str,
) -> dict[str, Any]:
    """Remove a knowledge-graph link between two memory items."""
    return hub_unlink_impl(_components, source_id, target_id)


def hub_read(item_id: str) -> dict[str, Any]:
    """Read full content of one memory item.

    Returns frontmatter fields plus the body text.
    """
    return hub_read_impl(_components, item_id)


def hub_delete(item_id: str) -> dict[str, Any]:
    """Delete a memory item by id.

    Removes both the md file and the sqlite index entry. Use sparingly —
    prefer archiving or superseding over hard delete.
    """
    return hub_delete_impl(_components, item_id)


def hub_list(
    n: int = 10,
    type: str | None = None,
    project: str | None = None,
) -> list[dict[str, Any]]:
    """List recent memory items, optionally filtered by type or project.

    Returns the n most recent items sorted by creation date descending.
    """
    return hub_list_impl(_components, n=n, type=type, project=project)


def hub_conclude(
    session_summary: str,
    key_decisions: list[str] | None = None,
    agent: str | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Record a session conclusion / handoff into the brain pool.

    Hermes standard: ``hub_conclude(summary, learnings) → {id, stored}``
    Writes a handoff-type memory capturing session-end state.
    """
    return hub_conclude_impl(
        hub_remember,
        session_summary=session_summary,
        key_decisions=key_decisions,
        agent=agent,
        project=project,
    )


def hub_drift(staleness_days: int = 180) -> dict[str, Any]:
    """Run drift detection on the brain pool.

    Checks for contradictions, staleness, citation rot, and drift clusters.
    Returns a summary for the agent to act on.
    """
    return hub_drift_impl(_components, staleness_days=staleness_days)


def hub_evolve(apply: bool = False) -> dict[str, Any]:
    """Run self-evolve engine on the brain pool.

    Proposes consolidation, promotion, archiving, skill generation.
    When apply=True, approved proposals are executed.
    """
    return hub_evolve_impl(_components, apply=apply)


def hub_batch_confirm(
    item_ids: list[str],
    confidence: float = 0.9,
) -> dict[str, Any]:
    """Confirm multiple memory items at once, setting their confidence."""
    return hub_batch_confirm_impl(_components, item_ids, confidence=confidence)


def hub_update(
    item_id: str,
    title: str | None = None,
    summary: str | None = None,
    tags: list[str] | None = None,
    type: str | None = None,
    confidence: float | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    """Update fields of an existing memory item.

    Only provided fields are updated; others remain unchanged.
    """
    return hub_update_impl(
        _components,
        get_default_embedder,
        item_id=item_id,
        title=title,
        summary=summary,
        tags=tags,
        type=type,
        confidence=confidence,
        project=project,
    )


def hub_stats(project: str | None = None) -> dict[str, Any]:
    """Return statistics and health score for the brain pool.

    Includes item counts, tag distribution, weekly trend, and health grade.
    """
    return hub_stats_impl(_components, project=project)


def hub_govern(ttl_days: int = 90) -> dict[str, Any]:
    """Run governance pipeline on the brain pool.

    Checks for duplicates, noise, expired items, and quality issues.
    Returns a summary for the agent to act on.
    """
    return hub_govern_impl(_components, ttl_days=ttl_days)


def hub_tag_suggest(
    text: str,
    max_tags: int = 5,
) -> dict[str, Any]:
    """Suggest tags for content based on similar existing items."""
    from agent_brain.memory.recall.retrieval import suggest_tags as _suggest_tags

    return hub_tag_suggest_impl(
        _components,
        get_default_embedder,
        _suggest_tags,
        text=text,
        max_tags=max_tags,
    )


def hub_import(
    data: str,
    format: str = "jsonl",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Import memory items from JSON or JSONL string.

    Each record should have 'frontmatter' (dict) and 'body' (str).
    """
    return hub_import_impl(
        _components,
        get_default_embedder,
        data=data,
        format=format,
        overwrite=overwrite,
    )


def hub_obsidian_export(
    vault_dir: str,
    project: str | None = None,
    type: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Export brain pool items to an Obsidian vault as markdown files.

    Each item becomes a .md with YAML frontmatter and [[wikilinks]].
    """
    return hub_obsidian_export_impl(
        _components,
        vault_dir=vault_dir,
        project=project,
        type=type,
        overwrite=overwrite,
    )


def hub_obsidian_import(
    vault_dir: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Import Obsidian markdown files back into brain pool.

    Only imports files with frontmatter containing a valid mem-* ID.
    """
    return hub_obsidian_import_impl(
        _components,
        get_default_embedder,
        vault_dir=vault_dir,
        overwrite=overwrite,
    )


def hub_gc(
    max_age_days: int = 7,
    tags: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Garbage-collect stale auto-captured items (session-end signals, etc.).

    Deletes items older than max_age_days that have ANY of the specified tags.
    Default tags: session-end, auto-captured, needs-review.
    """
    return hub_gc_impl(
        _components,
        max_age_days=max_age_days,
        tags=tags,
        dry_run=dry_run,
    )


HERMES_TOOLS = build_hermes_tools(globals())


def register_hermes_tools(mcp_instance: Any) -> None:
    """Register all Hermes tools on an existing FastMCP instance."""
    register_tools(mcp_instance, HERMES_TOOLS)


def run() -> None:
    """Standalone Hermes provider MCP server."""
    run_provider(__version__, register_hermes_tools)


if __name__ == "__main__":
    run()
