"""Shared staged-recall policy for Agent-facing search surfaces."""
from __future__ import annotations

MAX_STAGED_DETAIL_ITEMS = 3
BROAD_EXPLICIT_DETAIL_WARNING = (
    "explicit detail search with top_k>3 bypasses staged recall; "
    "prefer locator/overview search followed by read_memory for 1-3 selected items"
)


def search_governance_warnings(*, verbosity: str, top_k: int) -> tuple[str, ...]:
    """Return non-blocking guidance for broad explicit-detail searches."""
    if verbosity.strip().lower() == "detail" and top_k > MAX_STAGED_DETAIL_ITEMS:
        return (BROAD_EXPLICIT_DETAIL_WARNING,)
    return ()


__all__ = [
    "BROAD_EXPLICIT_DETAIL_WARNING",
    "MAX_STAGED_DETAIL_ITEMS",
    "search_governance_warnings",
]
