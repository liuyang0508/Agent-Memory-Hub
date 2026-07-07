"""Context loading policy for locator/overview/detail views."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agent_brain.memory.recall.retrieval_budget import estimate_tokens
from agent_brain.contracts.memory_item import MemoryItem

ContextView = Literal["locator", "overview", "detail"]
ContextVerbosity = Literal["locator", "overview", "detail", "auto"]

_BOUNDARY_TYPES = {"fact", "decision"}
_STATEFUL_TYPES = {"signal", "handoff"}


@dataclass(frozen=True)
class ContextViewSelection:
    """Selected context view and diagnostics explaining why it was chosen."""

    view: ContextView
    reasons: tuple[str, ...]


def select_context_view(
    item: MemoryItem,
    body: str = "",
    *,
    requested: ContextVerbosity = "auto",
    firewall_decision=None,
    budget_tokens: int | None = None,
) -> ContextViewSelection:
    """Choose the smallest useful context view for an item.

    Explicit locator/overview/detail requests are honored. ``auto`` starts from
    locator and only upgrades when the memory's type, evidence shape, or
    firewall decision suggests the Agent needs boundary or source context.
    """
    if requested != "auto":
        return ContextViewSelection(requested, (f"explicit_{requested}",))

    view: ContextView = "locator"
    reasons: list[str] = ["default_locator"]

    if _raw_with_direct_evidence(item) and body:
        view = "detail"
        reasons.append("raw_direct_evidence")
    elif _should_load_overview(item, firewall_decision=firewall_decision):
        view = "overview"
        reasons.append(_overview_reason(item, firewall_decision=firewall_decision))

    selection = ContextViewSelection(view, tuple(_dedupe_reasons(reasons)))
    return _fit_budget(item, body, selection, budget_tokens=budget_tokens)


def render_context_view(item: MemoryItem, body: str, view: ContextView) -> str:
    """Render one of locator, overview, or detail for a memory item."""
    if view == "detail":
        return body
    if view == "overview":
        return item.context_views.overview or item.context_views.locator or item.summary or ""
    return item.context_views.locator or item.summary or ""


def _should_load_overview(item: MemoryItem, *, firewall_decision) -> bool:
    if not item.context_views.overview:
        return False
    item_type = str(item.type)
    if item_type in _BOUNDARY_TYPES or item_type in _STATEFUL_TYPES:
        return True
    if _has_source_refs(item) or _has_validity_boundary(item):
        return True
    return bool(firewall_decision is not None and getattr(firewall_decision, "action", "") == "demote")


def _overview_reason(item: MemoryItem, *, firewall_decision) -> str:
    item_type = str(item.type)
    if item_type in _BOUNDARY_TYPES:
        return "fact_or_decision_boundary"
    if item_type in _STATEFUL_TYPES:
        return "state_or_handoff_boundary"
    if firewall_decision is not None and getattr(firewall_decision, "action", "") == "demote":
        return "firewall_demoted"
    return "evidence_or_scope_navigation"


def _raw_with_direct_evidence(item: MemoryItem) -> bool:
    return (
        str(getattr(item, "maturity", "")) == "raw"
        and str(getattr(item, "abstraction", "")) == "L0"
        and _has_direct_evidence_refs(item)
    )


def _has_source_refs(item: MemoryItem) -> bool:
    refs = item.refs
    return bool(
        refs.files
        or refs.urls
        or refs.mems
        or refs.commits
        or refs.resources
        or refs.extractions
    )


def _has_direct_evidence_refs(item: MemoryItem) -> bool:
    refs = item.refs
    return bool(
        refs.files
        or refs.urls
        or refs.commits
        or refs.resources
        or refs.extractions
    )


def _has_validity_boundary(item: MemoryItem) -> bool:
    validity = getattr(item, "validity", None)
    if validity is None:
        return False
    return bool(
        validity.observed_at
        or validity.ttl_hours is not None
        or validity.cwd
        or validity.repo
        or validity.branch
        or validity.os
        or validity.adapter
    )


def _fit_budget(
    item: MemoryItem,
    body: str,
    selection: ContextViewSelection,
    *,
    budget_tokens: int | None,
) -> ContextViewSelection:
    if budget_tokens is None:
        return selection
    view: ContextView = selection.view
    reasons = list(selection.reasons)
    while view != "locator" and estimate_tokens(render_context_view(item, body, view)) > budget_tokens:
        view = "overview" if view == "detail" else "locator"
        reasons.append(f"budget_downgraded_to_{view}")
    return ContextViewSelection(view, tuple(_dedupe_reasons(reasons)))


def _dedupe_reasons(reasons: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for reason in reasons:
        if reason in seen:
            continue
        seen.add(reason)
        deduped.append(reason)
    return deduped


__all__ = [
    "ContextVerbosity",
    "ContextView",
    "ContextViewSelection",
    "render_context_view",
    "select_context_view",
]
