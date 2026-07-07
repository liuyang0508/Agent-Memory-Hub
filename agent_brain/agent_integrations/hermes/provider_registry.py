"""Hermes provider tool registry."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


HERMES_TOOL_NAMES = (
    "hub_search",
    "hub_remember",
    "hub_profile",
    "hub_context",
    "hub_graph",
    "hub_drift",
    "hub_evolve",
    "hub_batch_confirm",
    "hub_update",
    "hub_stats",
    "hub_link",
    "hub_unlink",
    "hub_read",
    "hub_delete",
    "hub_list",
    "hub_govern",
    "hub_conclude",
    "hub_tag_suggest",
    "hub_import",
    "hub_obsidian_export",
    "hub_obsidian_import",
    "hub_gc",
)


def _lookup_tool(namespace: Mapping[str, Any] | Any, name: str) -> Any:
    if isinstance(namespace, Mapping):
        return namespace[name]
    return getattr(namespace, name)


def build_hermes_tools(namespace: Mapping[str, Any] | Any | None = None) -> tuple[Any, ...]:
    """Build the canonical Hermes tool tuple from a provider namespace."""
    if namespace is None:
        from agent_brain.agent_integrations.hermes import provider

        existing = getattr(provider, "HERMES_TOOLS", None)
        if existing is not None:
            return existing
        namespace = provider
    return tuple(_lookup_tool(namespace, name) for name in HERMES_TOOL_NAMES)


__all__ = ["HERMES_TOOL_NAMES", "build_hermes_tools"]
