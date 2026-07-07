"""Helpers for excluding mechanical runtime signals from knowledge governance."""

from __future__ import annotations

from typing import Any


def is_session_active_signal(item: Any) -> bool:
    """Return true for hook-created session liveness signals.

    These records are operational breadcrumbs, not durable knowledge assertions.
    They can be useful in review queues, but they should not dominate duplicate
    and drift-cluster health metrics.
    """
    item_type = str(getattr(item, "type", "") or "")
    tags = {str(tag).strip().lower() for tag in getattr(item, "tags", ()) or ()}
    title = str(getattr(item, "title", "") or "").strip().lower()
    summary = str(getattr(item, "summary", "") or "").strip().lower()
    return (
        item_type.endswith("signal")
        and "session-active" in tags
        and "auto-captured" in tags
        and title.startswith("session ")
        and "第一次 turn" in summary
    )


def is_governance_noise(item: Any) -> bool:
    """Return true for items that should not count as knowledge-health issues."""
    return is_session_active_signal(item)


__all__ = ["is_governance_noise", "is_session_active_signal"]
