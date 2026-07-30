"""Deterministic lifecycle-state checks for active signal memories."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from agent_brain.contracts.memory_enums import memory_enum_value
from agent_brain.contracts.memory_item import MemoryItem


SignalState = Literal["open", "resolved", "obsolete", "deferred", "ambiguous"]

ACTIVE_SIGNAL_TAGS = frozenset(
    {
        "blocked",
        "in-progress",
        "in_progress",
        "needs-action",
        "open",
        "pending",
        "signal-open",
        "waiting",
    }
)
RESOLVED_SIGNAL_TAGS = frozenset(
    {"closed", "completed", "done", "resolved", "signal-resolved"}
)
OBSOLETE_SIGNAL_TAGS = frozenset({"obsolete", "signal-obsolete"})
DEFERRED_SIGNAL_TAGS = frozenset({"deferred", "signal-deferred"})
LIFECYCLE_SIGNAL_TAGS = (
    ACTIVE_SIGNAL_TAGS
    | RESOLVED_SIGNAL_TAGS
    | OBSOLETE_SIGNAL_TAGS
    | DEFERRED_SIGNAL_TAGS
)
_CLOSURE_RE = re.compile(
    r"(?:\b(?:closed|completed|done|fixed|obsolete|resolved)\b"
    r"|不再适用|修复完成|问题已解决|已上线|已关闭|已完成|已接通|已解决|已修复|阻塞已解除)"
)


@dataclass(frozen=True)
class SignalStateAssessment:
    state: SignalState
    issues: tuple[str, ...] = ()

    @property
    def consistent(self) -> bool:
        return not self.issues


def assess_signal_state(
    item: MemoryItem,
    *,
    now: datetime | None = None,
) -> SignalStateAssessment:
    """Derive one bounded state and report contradictory lifecycle evidence."""

    if memory_enum_value(item.type) != "signal" or item.superseded_by:
        return SignalStateAssessment("open")
    tags = frozenset(
        tag.strip().casefold()
        for tag in item.tags
        if isinstance(tag, str) and tag.strip()
    )
    active = tags & ACTIVE_SIGNAL_TAGS
    resolved = tags & RESOLVED_SIGNAL_TAGS
    obsolete = tags & OBSOLETE_SIGNAL_TAGS
    deferred = tags & DEFERRED_SIGNAL_TAGS
    closure_text = bool(_CLOSURE_RE.search(f"{item.title}\n{item.summary}".casefold()))
    issues: list[str] = []
    terminal_groups = sum(bool(group) for group in (resolved, obsolete, deferred))
    if active and (resolved or obsolete):
        issues.append("terminal_and_active_tags")
    if terminal_groups > 1:
        issues.append("multiple_terminal_state_tags")
    if active and closure_text and item.signal_state is None:
        issues.append("closure_text_with_active_tags")
    if closure_text and not (resolved or obsolete) and item.signal_state is None:
        issues.append("closure_text_without_terminal_tag")

    explicit = item.signal_state
    if explicit is not None:
        status = explicit.status
        if status == "open" and (resolved or obsolete or deferred):
            issues.append("explicit_open_with_non_open_tags")
        elif status == "resolved" and not resolved:
            issues.append("explicit_resolved_without_resolved_tag")
        elif status == "obsolete" and not obsolete:
            issues.append("explicit_obsolete_without_obsolete_tag")
        elif status == "deferred" and not deferred:
            issues.append("explicit_deferred_without_deferred_tag")
        if status == "resolved" and (active or obsolete or deferred):
            issues.append("explicit_resolved_with_conflicting_tags")
        elif status == "obsolete" and (active or resolved or deferred):
            issues.append("explicit_obsolete_with_conflicting_tags")
        elif status == "deferred" and (resolved or obsolete):
            issues.append("explicit_deferred_with_terminal_tags")
        if status == "deferred":
            current = now or datetime.now(timezone.utc)
            deadline = explicit.deferred_until
            if deadline is not None and deadline <= current:
                issues.append("expired_deferral")
                return SignalStateAssessment("open", tuple(dict.fromkeys(issues)))
        if issues:
            return SignalStateAssessment("ambiguous", tuple(dict.fromkeys(issues)))
        return SignalStateAssessment(status)

    if deferred:
        issues.append("deferred_without_explicit_deadline")
    state: SignalState
    if issues:
        state = "ambiguous"
    elif resolved:
        state = "resolved"
    elif obsolete:
        state = "obsolete"
    else:
        state = "open"
    return SignalStateAssessment(state, tuple(issues))


__all__ = [
    "ACTIVE_SIGNAL_TAGS",
    "DEFERRED_SIGNAL_TAGS",
    "LIFECYCLE_SIGNAL_TAGS",
    "OBSOLETE_SIGNAL_TAGS",
    "RESOLVED_SIGNAL_TAGS",
    "SignalState",
    "SignalStateAssessment",
    "assess_signal_state",
]
