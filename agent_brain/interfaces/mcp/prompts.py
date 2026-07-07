"""MCP prompts exposed by amh.

These prompts are the *primary* mechanism we use to teach connecting LLM
agents how/when to use the brain. MCP clients (Claude Desktop, Cursor,
Qoder, etc.) surface them either as slash commands or by injecting them
into the system prompt — either way, the agent sees an actionable workflow
guide instead of having to discover behaviour from tool descriptions alone.

Design intent:
- ``agent_workflow_guide`` is the canonical "how to be a good citizen of
  the brain" doc. Any client that supports prompts will pick this up.
- Scenario prompts (``before_answering`` / ``after_producing_artifact`` /
  ``end_of_task``) are short, single-purpose triggers an agent can re-read
  at the right moment of its loop.
- Identical text is mirrored in ``resources.py`` (guide:// URIs) and in
  ``onboarding.py`` (``get_usage_guide`` tool) so EVERY MCP client surface
  has a path to the same content. See ``onboarding._USAGE_GUIDE`` for the
  single source of truth.
"""
from __future__ import annotations

from agent_brain.interfaces.mcp.onboarding import (
    AFTER_PRODUCING_ARTIFACT,
    BEFORE_ANSWERING,
    END_OF_TASK,
    USAGE_GUIDE,
)


def register(mcp) -> None:
    """Attach 4 prompts to the FastMCP instance."""

    @mcp.prompt(
        name="agent_workflow_guide",
        description=(
            "Canonical 'how to use agent-memory-hub correctly' guide. "
            "Read this once at the start of any session that has access to "
            "the brain — it teaches when to search before answering, when to "
            "write_memory proactively, and how to chain link_memories."
        ),
    )
    def agent_workflow_guide():
        return USAGE_GUIDE

    @mcp.prompt(
        name="before_answering",
        description=(
            "Re-read this at the moment you receive a user question, BEFORE "
            "you start reasoning. It enumerates the triggers that mean "
            "'search the brain first'."
        ),
    )
    def before_answering():
        return BEFORE_ANSWERING

    @mcp.prompt(
        name="after_producing_artifact",
        description=(
            "Re-read this immediately after you finish producing any reusable "
            "artifact (prompt template, decision framework, checklist, recipe, "
            "code snippet). It tells you exactly what to write_memory + link."
        ),
    )
    def after_producing_artifact():
        return AFTER_PRODUCING_ARTIFACT

    @mcp.prompt(
        name="end_of_task",
        description=(
            "Re-read this when a task is wrapping up. Lists the lightweight "
            "hygiene calls (brain_stats / drift_check / gc_memory) you should "
            "consider before signing off."
        ),
    )
    def end_of_task():
        return END_OF_TASK


__all__ = ["register"]
