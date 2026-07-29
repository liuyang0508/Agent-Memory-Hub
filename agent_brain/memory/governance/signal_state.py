"""Deterministic lifecycle-state checks for active signal memories."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from agent_brain.contracts.memory_enums import memory_enum_value
from agent_brain.contracts.memory_item import MemoryItem


SignalState = Literal["open", "resolved", "ambiguous"]

_ACTIVE_TAGS = frozenset(
    {
        "blocked",
        "in-progress",
        "in_progress",
        "needs-action",
        "open",
        "pending",
        "waiting",
    }
)
_RESOLVED_TAGS = frozenset({"closed", "completed", "done", "resolved"})
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


def assess_signal_state(item: MemoryItem) -> SignalStateAssessment:
    """Derive one bounded state and report contradictory lifecycle evidence."""

    if memory_enum_value(item.type) != "signal" or item.superseded_by:
        return SignalStateAssessment("open")
    tags = frozenset(
        tag.strip().casefold()
        for tag in item.tags
        if isinstance(tag, str) and tag.strip()
    )
    active = tags & _ACTIVE_TAGS
    resolved = tags & _RESOLVED_TAGS
    closure_text = bool(_CLOSURE_RE.search(f"{item.title}\n{item.summary}".casefold()))
    issues: list[str] = []
    if active and resolved:
        issues.append("terminal_and_active_tags")
    if active and closure_text:
        issues.append("closure_text_with_active_tags")
    if closure_text and not resolved:
        issues.append("closure_text_without_terminal_tag")
    state: SignalState
    if issues:
        state = "ambiguous"
    elif resolved:
        state = "resolved"
    else:
        state = "open"
    return SignalStateAssessment(state, tuple(issues))


__all__ = ["SignalState", "SignalStateAssessment", "assess_signal_state"]
