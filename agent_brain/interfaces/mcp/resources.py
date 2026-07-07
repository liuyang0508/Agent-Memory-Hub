"""MCP resources exposed by amh.

Resources are the third MCP surface (alongside tools and prompts). Clients
that support them can fetch read-only documents by URI — useful for clients
that don't auto-inject prompts but DO let the agent enumerate ``resources/``
on demand (e.g. via tool-use loops or explorers).

We mirror the same four onboarding documents available through prompts and
the ``get_usage_guide`` tool, addressed under the ``guide://`` scheme:

- ``guide://agent-workflow``            → full canonical workflow guide
- ``guide://before-answering``          → trigger checklist for incoming Qs
- ``guide://after-producing-artifact``  → capture checklist for artifacts
- ``guide://end-of-task``               → wrap-up hygiene checklist

The single source of truth is ``agent_brain.interfaces.mcp.onboarding``; this module
only registers FastMCP resource handlers that return those constants.
"""
from __future__ import annotations

from agent_brain.interfaces.mcp.onboarding import (
    AFTER_PRODUCING_ARTIFACT,
    BEFORE_ANSWERING,
    END_OF_TASK,
    USAGE_GUIDE,
)


def register(mcp) -> None:
    """Attach 4 read-only guide:// resources to the FastMCP instance."""

    @mcp.resource(
        "guide://agent-workflow",
        name="agent_workflow_guide",
        description=(
            "Canonical guide for any LLM agent connected to agent-memory-hub. "
            "Covers the three core habits (search before answering, write "
            "proactively, link after writing), the session lifecycle, the 7 "
            "canonical type values, and hard rules."
        ),
        mime_type="text/markdown",
    )
    def agent_workflow_resource() -> str:
        return USAGE_GUIDE

    @mcp.resource(
        "guide://before-answering",
        name="before_answering",
        description=(
            "Trigger checklist: re-read at the moment a user question arrives, "
            "BEFORE reasoning. Enumerates the signals that mean 'search the "
            "brain first'."
        ),
        mime_type="text/markdown",
    )
    def before_answering_resource() -> str:
        return BEFORE_ANSWERING

    @mcp.resource(
        "guide://after-producing-artifact",
        name="after_producing_artifact",
        description=(
            "Capture checklist: re-read immediately after producing any "
            "reusable artifact (prompt, recipe, checklist, decision framework, "
            "code snippet). Tells the agent exactly what to write_memory + link."
        ),
        mime_type="text/markdown",
    )
    def after_producing_artifact_resource() -> str:
        return AFTER_PRODUCING_ARTIFACT

    @mcp.resource(
        "guide://end-of-task",
        name="end_of_task",
        description=(
            "Wrap-up hygiene checklist: lightweight brain_stats / drift_check "
            "/ gc_memory calls to consider before signing off on a task."
        ),
        mime_type="text/markdown",
    )
    def end_of_task_resource() -> str:
        return END_OF_TASK


__all__ = ["register"]
