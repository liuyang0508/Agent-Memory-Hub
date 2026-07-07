"""MemoryClient — the simple Python SDK for agent-memory-hub.

Designed for non-MCP agents (plain Python scripts, cron jobs, notebooks)
that want to read/write the shared brain pool without running an MCP server.

All operations are local-first (direct filesystem + sqlite), zero network calls.

Example:
    from agent_brain.interfaces.sdk import MemoryClient

    client = MemoryClient(brain_dir="~/.agent-memory-hub")
    client.write(
        type="decision",
        title="Chose SSE over WebSocket",
        summary="SSE is simpler for our uni-directional push case",
        body="**决策** SSE\\n**理由** uni-directional\\n**改回去的代价** low",
        tags=["api", "sse"],
        project="my-project",
    )

    results = client.search("real-time push", top_k=5)
    for r in results:
        print(f"{r.title} (score={r.score:.3f})")

    client.reaffirm(results[0].id)
    client.reject(results[1].id)

    stats = client.stats()
    print(f"Brain health: {stats['health_grade']}")
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_brain.interfaces.sdk.config import resolve_brain_dir
from agent_brain.interfaces.sdk.components import ClientComponents
from agent_brain.interfaces.sdk.feedback import (
    apply_confirm,
    apply_injection_feedback,
    apply_reaffirm,
    apply_reject,
    apply_task_outcome_feedback,
)
from agent_brain.interfaces.sdk.query import (
    SearchResult,
    build_brief_payload,
    list_recent_items,
    read_item,
    search_items,
)
from agent_brain.interfaces.sdk.write import write_item


class MemoryClient:
    """Lightweight Python SDK for agent-memory-hub.

    All operations are local (filesystem + sqlite). No network, no MCP server needed.
    """

    def __init__(
        self,
        brain_dir: str | Path | None = None,
        agent: str | None = None,
        session: str | None = None,
        project: str | None = None,
    ):
        """Initialize the client.

        Args:
            brain_dir: Path to brain directory (default: $BRAIN_DIR or ~/.agent-memory-hub)
            agent: Default agent name for writes (e.g. "my-script")
            session: Default session ID for writes
            project: Default project for writes and searches
        """
        self._brain_dir = resolve_brain_dir(brain_dir)
        self._default_agent = agent
        self._default_session = session
        self._default_project = project

        self._components = ClientComponents(self._brain_dir)

    @property
    def brain_dir(self) -> Path:
        return self._brain_dir

    def write(
        self,
        type: str,
        title: str,
        summary: str,
        body: str = "",
        *,
        overview: str | None = None,
        tags: list[str] | None = None,
        refs: dict[str, object] | None = None,
        project: str | None = None,
        agent: str | None = None,
        session: str | None = None,
        confidence: float = 0.7,
        sensitivity: str = "internal",
        validity: dict[str, object] | None = None,
        allow_unsafe: bool = False,
    ) -> str:
        """Write a new memory item. Returns the item ID.

        Args:
            type: Memory type (fact, episode, decision, artifact, signal, handoff)
            title: Concise title
            summary: 1-2 sentence summary
            body: Full body content
            overview: Optional structured context view
            tags: Categorization tags
            refs: Optional source refs: files/urls/mems/commits/resources/extractions
            project: Project scope
            agent: Agent name (defaults to client default)
            session: Session ID (defaults to client default)
            confidence: Initial confidence (0.0-1.0)
            sensitivity: Sensitivity level (public, internal, private, secret)
            validity: Optional scope where this observation is valid
            allow_unsafe: Bypass the before-write audit gate
        """
        return write_item(
            store=self._components.get_store(),
            index_getter=self._components.get_index,
            embedder_getter=self._components.get_embedder,
            type=type,
            title=title,
            summary=summary,
            body=body,
            overview=overview,
            tags=tags,
            refs=refs,
            project=project or self._default_project,
            agent=agent or self._default_agent,
            session=session or self._default_session,
            confidence=confidence,
            sensitivity=sensitivity,
            validity=validity,
            allow_unsafe=allow_unsafe,
        )

    def search(
        self,
        query: str,
        *,
        top_k: int = 10,
        type: str | None = None,
        project: str | None = None,
        tags: list[str] | None = None,
        verbosity: str = "locator",
        include_trace: bool = False,
        context_firewall: bool = False,
        include_resources: bool = False,
    ) -> list[SearchResult]:
        """Search the brain pool. Returns ranked results.

        Args:
            query: Free-text search query
            top_k: Maximum results to return
            type: Filter by memory type
            project: Filter by project (defaults to client default)
            tags: Filter by tags (all must match)
            verbosity: Context view to pack: locator, overview, detail, or auto
            include_trace: Include retrieval trace diagnostics
            context_firewall: Run before-inject firewall over retrieved candidates
            include_resources: Include resource sidecar context referenced by hits
        """
        return search_items(
            query=query,
            top_k=top_k,
            type=type,
            project=project,
            tags=tags,
            default_project=self._default_project,
            retriever=self._components.get_retriever(),
            store=self._components.get_store(),
            brain_dir=self._brain_dir,
            verbosity=verbosity,
            include_trace=include_trace,
            context_firewall=context_firewall,
            include_resources=include_resources,
        )

    def read(self, item_id: str) -> dict[str, Any] | None:
        """Read a single item by ID. Returns dict with 'item' and 'body' or None."""
        return read_item(self._components.get_store(), item_id)

    def reaffirm(self, item_id: str) -> None:
        """Signal that a retrieved item was useful (support_count+1, gain+0.1)."""
        apply_reaffirm(self._components.get_feedback(), item_id)

    def reject(self, item_id: str) -> None:
        """Signal that a retrieved item was wrong/unhelpful (contradict_count+1, gain-0.2)."""
        apply_reject(self._components.get_feedback(), item_id)

    def injection_feedback(
        self,
        *,
        injected_ids: list[str],
        adopted_ids: list[str] | None = None,
        rejected_ids: list[str] | None = None,
    ) -> dict[str, list[str]]:
        """Apply feedback for an injected cohort.

        Adopted items are reinforced, rejected items are penalized, and
        injected-but-unmentioned items are left unchanged.
        """
        return apply_injection_feedback(
            self._components.get_store(),
            self._components.get_index(),
            injected_ids=injected_ids,
            adopted_ids=adopted_ids,
            rejected_ids=rejected_ids,
        )

    def apply_task_outcome_feedback(self, *, force: bool = False) -> dict[str, Any]:
        """Apply explicit task outcome adopted/rejected ids to memory feedback."""
        return apply_task_outcome_feedback(
            self._brain_dir,
            self._components.get_store(),
            self._components.get_index(),
            force=force,
        )

    def confirm(self, item_id: str, confidence: float = 0.9) -> None:
        """Confirm an item is still accurate (updates confidence)."""
        apply_confirm(
            self._components.get_store(),
            self._components.get_index(),
            item_id,
            confidence,
        )

    def stats(self) -> dict[str, Any]:
        """Get brain pool statistics and health grade."""
        from agent_brain.interfaces.sdk.stats import build_client_stats

        return build_client_stats(self._components.get_store())

    def list_recent(self, n: int = 10, type: str | None = None) -> list[dict[str, Any]]:
        """List recent items, optionally filtered by type."""
        return list_recent_items(self._components.get_store(), n=n, type=type)

    def brief(self, budget_tokens: int = 1500) -> dict[str, Any]:
        """Get a token-budgeted briefing of the brain pool."""
        return build_brief_payload(
            self._components.get_store(),
            project=self._default_project,
            budget_tokens=budget_tokens,
        )


__all__ = ["MemoryClient", "SearchResult"]
