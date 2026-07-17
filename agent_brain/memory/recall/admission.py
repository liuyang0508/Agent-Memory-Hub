"""Conservative admission for automatic memory recall attempts."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal, get_args

from agent_brain.memory.context.prompt_normalization import normalize_hook_prompt_for_recall
from agent_brain.memory.context.query_signal import QuerySignal, analyze_injection_query
from agent_brain.memory.recall.technical_anchors import (
    TECHNICAL_ALIAS_SET_ID,
    technical_query_anchors,
)

if TYPE_CHECKING:
    from agent_brain.memory.recall.routed_types import ProjectScope, RecallRequest

AdmissionReason = Literal[
    "meaningful_query",
    "empty_query",
    "punctuation_only",
    "adapter_control_command",
    "weak_confirmation",
]

_ADMISSION_REASONS = frozenset(get_args(AdmissionReason))
_ADAPTER_CONTROL_COMMANDS = ("remember", "goal", "compact", "clear")
_ADAPTER_CONTROL_COMMAND_RE = re.compile(
    r"^\s*/(?:" + "|".join(_ADAPTER_CONTROL_COMMANDS) + r")(?=\s|$)[^\n]*$",
    re.IGNORECASE,
)
_WEAK_CONFIRMATIONS = frozenset({"是", "确认", "继续", "ok", "okay", "1"})


@dataclass(frozen=True)
class RecallAdmission:
    """Whether a normalized prompt is worth attempting to recall against."""

    allowed: bool
    reason: AdmissionReason

    def __post_init__(self) -> None:
        if self.reason not in _ADMISSION_REASONS:
            raise ValueError(f"unsupported admission reason: {self.reason!r}")
        if self.allowed != (self.reason == "meaningful_query"):
            raise ValueError("allowed admission must use the meaningful_query reason")


def analyze_recall_admission(raw_query: str) -> RecallAdmission:
    """Classify recall admission without consulting lexical extraction results."""

    normalized = normalize_hook_prompt_for_recall(raw_query)
    if not normalized:
        return RecallAdmission(False, "empty_query")
    if _is_punctuation_only(normalized):
        return RecallAdmission(False, "punctuation_only")
    if _ADAPTER_CONTROL_COMMAND_RE.fullmatch(normalized):
        return RecallAdmission(False, "adapter_control_command")
    weak_candidate = _trim_unicode_edges(normalized).casefold()
    if weak_candidate in _WEAK_CONFIRMATIONS:
        return RecallAdmission(False, "weak_confirmation")
    return RecallAdmission(True, "meaningful_query")


def build_recall_request(
    raw_query: str,
    *,
    adapter: str,
    enable_technical_anchors: bool = True,
    project_scope: ProjectScope | None = None,
    cwd: str | None = None,
    session_id: str | None = None,
) -> RecallRequest:
    """Build the immutable input contract used by routed recall."""

    from agent_brain.memory.recall.routed_types import ProjectScope, RecallRequest

    if project_scope is not None and not isinstance(project_scope, ProjectScope):
        raise TypeError("project_scope must be a ProjectScope or None")
    normalized_query = normalize_hook_prompt_for_recall(raw_query)
    signal = build_routed_query_signal(
        normalized_query,
        enable_technical_anchors=enable_technical_anchors,
    )
    admission = analyze_recall_admission(raw_query)
    return RecallRequest(
        raw_query=raw_query,
        normalized_query=normalized_query,
        lexical_terms=tuple(signal.terms[:6]),
        admission=admission,
        query_signal=signal,
        project_scope=project_scope,
        cwd=cwd,
        adapter=adapter,
        session_id=session_id,
    )


def build_routed_query_signal(
    raw_query: str,
    *,
    enable_technical_anchors: bool = True,
    brain_dir: Path | None = None,
) -> QuerySignal:
    """Build the query signal used by routed retrieval and diagnostics."""

    normalized_query = normalize_hook_prompt_for_recall(raw_query)
    if brain_dir is None:
        signal = analyze_injection_query(normalized_query)
    else:
        signal = analyze_injection_query(normalized_query, brain_dir=brain_dir)
    if not enable_technical_anchors:
        return signal
    return _with_technical_aliases(signal, normalized_query)


def _with_technical_aliases(signal: QuerySignal, query: str) -> QuerySignal:
    anchors = technical_query_anchors(query)
    if not anchors:
        return signal
    terms = tuple(dict.fromkeys((*anchors, *signal.terms)))
    strong_terms = tuple(dict.fromkeys((*anchors, *signal.strong_terms)))
    trace = tuple(entry for entry in signal.trace if not entry.startswith("block:"))
    specificity = len(strong_terms) + max(0, len(terms) - len(strong_terms)) * 0.35
    return replace(
        signal,
        terms=terms,
        strong_terms=strong_terms,
        injectable=True,
        reason="ok",
        specificity=specificity,
        decision="inject_allowed",
        anchors=tuple(dict.fromkeys(("technical_alias", *signal.anchors))),
        trace=tuple(
            (
                *trace,
                "technical_alias_set=" + TECHNICAL_ALIAS_SET_ID,
                "technical_alias_terms=" + "|".join(anchors),
                "decision:inject_allowed",
            )
        ),
    )


def _is_punctuation_only(value: str) -> bool:
    visible = tuple(character for character in value if not character.isspace())
    return bool(visible) and all(
        unicodedata.category(character).startswith("P")
        for character in visible
    )


def _trim_unicode_edges(value: str) -> str:
    start = 0
    end = len(value)
    while start < end and _is_unicode_edge(value[start]):
        start += 1
    while end > start and _is_unicode_edge(value[end - 1]):
        end -= 1
    return value[start:end]


def _is_unicode_edge(character: str) -> bool:
    return character.isspace() or unicodedata.category(character).startswith(("P", "S"))


__all__ = [
    "AdmissionReason",
    "RecallAdmission",
    "analyze_recall_admission",
    "build_recall_request",
    "build_routed_query_signal",
]
