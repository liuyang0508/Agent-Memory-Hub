"""Structured prompt admission frame for automatic memory injection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agent_brain.memory.context.prompt_normalization import normalize_hook_prompt_for_recall
from agent_brain.memory.context.query_signal import QuerySignal
from agent_brain.memory.recall.admission import build_routed_query_signal


IntentKind = Literal[
    "control",
    "status_output",
    "task_question",
    "artifact_request",
    "resume",
    "unknown",
]
RetrievalMode = Literal["block", "candidate_search", "explicit_search"]
InjectionPolicy = Literal["never", "needs_answerability", "allow_if_scope_matches"]


@dataclass(frozen=True)
class PromptFrame:
    """A stable, policy-facing view of a user prompt.

    ``QuerySignal`` remains the term extractor.  ``PromptFrame`` explains how
    the automatic injection path should treat that signal: whether candidate
    search is allowed, which anchors are topical versus scoping, and why a
    prompt was blocked.
    """

    raw_prompt: str
    normalized_prompt: str
    intent_kind: IntentKind
    topic_anchors: tuple[str, ...]
    scope_anchors: tuple[str, ...]
    risk_flags: tuple[str, ...]
    retrieval_mode: RetrievalMode
    injection_policy: InjectionPolicy
    query_terms: tuple[str, ...]
    strong_terms: tuple[str, ...]
    reason: str
    trace: tuple[str, ...] = ()

    def evidence(self) -> tuple[str, ...]:
        """Return bounded diagnostics safe for runtime gap records."""
        rows = [
            f"prompt_frame.intent_kind={self.intent_kind}",
            f"prompt_frame.retrieval_mode={self.retrieval_mode}",
            f"prompt_frame.injection_policy={self.injection_policy}",
            f"prompt_frame.reason={self.reason}",
        ]
        if self.topic_anchors:
            rows.append("prompt_frame.topic_anchors=" + "|".join(self.topic_anchors))
        if self.scope_anchors:
            rows.append("prompt_frame.scope_anchors=" + "|".join(self.scope_anchors))
        if self.risk_flags:
            rows.append("prompt_frame.risk_flags=" + "|".join(self.risk_flags))
        return tuple(rows)


def analyze_prompt_frame(
    prompt: str,
    *,
    brain_dir: Path | str | None = None,
    enable_technical_anchors: bool = True,
) -> PromptFrame:
    """Classify a prompt for pre-injection recall admission."""
    normalized = normalize_hook_prompt_for_recall(prompt)
    brain_path = Path(brain_dir) if brain_dir is not None else None
    signal = build_routed_query_signal(
        normalized,
        enable_technical_anchors=enable_technical_anchors,
        brain_dir=brain_path,
    )
    risk_flags = _risk_flags(signal)
    retrieval_mode: RetrievalMode = "candidate_search" if signal.injectable else "block"
    injection_policy: InjectionPolicy = (
        "needs_answerability" if retrieval_mode == "candidate_search" else "never"
    )
    scope_anchors = _scope_anchors(signal)
    topic_anchors = _topic_anchors(signal, scope_anchors)
    intent_kind = _intent_kind(normalized, signal, risk_flags, topic_anchors, scope_anchors)
    return PromptFrame(
        raw_prompt=prompt,
        normalized_prompt=normalized,
        intent_kind=intent_kind,
        topic_anchors=topic_anchors,
        scope_anchors=scope_anchors,
        risk_flags=risk_flags,
        retrieval_mode=retrieval_mode,
        injection_policy=injection_policy,
        query_terms=signal.terms,
        strong_terms=signal.strong_terms,
        reason=signal.reason,
        trace=signal.trace,
    )


def _risk_flags(signal: QuerySignal) -> tuple[str, ...]:
    flags: list[str] = []
    if signal.reason == "test_status_without_topic":
        flags.append("status_only")
    if signal.reason in {"single_unanchored_ascii", "generic_format_without_topic"}:
        flags.append("generic_singleton")
    if signal.reason == "unanchored_mixed_scope":
        flags.append("generic_singleton")
        flags.append("unanchored_mixed_scope")
    if signal.reason == "too_weak" and not signal.terms:
        flags.append("weak_control")
    if signal.reason == "weak_intent_without_anchor":
        flags.append("weak_without_anchor")
    if signal.reason == "unanchored_cjk_clause":
        flags.append("unanchored_clause")
    return tuple(dict.fromkeys(flags))


def _scope_anchors(signal: QuerySignal) -> tuple[str, ...]:
    if "file_or_module" not in signal.anchors:
        return ()
    return tuple(
        term for term in signal.terms
        if "." in term or "/" in term
    )


def _topic_anchors(signal: QuerySignal, scope_anchors: tuple[str, ...]) -> tuple[str, ...]:
    if not signal.injectable:
        return ()
    if "keyphrase" not in signal.anchors and "metadata_phrase" not in signal.anchors and "metadata_entity" not in signal.anchors:
        return ()
    scoped = set(scope_anchors)
    return tuple(term for term in signal.strong_terms if term not in scoped)


def _intent_kind(
    normalized_prompt: str,
    signal: QuerySignal,
    risk_flags: tuple[str, ...],
    topic_anchors: tuple[str, ...],
    scope_anchors: tuple[str, ...],
) -> IntentKind:
    if "status_only" in risk_flags:
        return "status_output"
    if "weak_control" in risk_flags:
        return "control"
    if not signal.injectable:
        return "unknown"
    lower = normalized_prompt.lower()
    if scope_anchors and any(token in lower for token in ("继续", "resume", "接着")):
        return "resume"
    if any(token in lower for token in ("生成", "转成", "导出", "预览", "report", "preview", "export")):
        return "artifact_request"
    if topic_anchors:
        return "task_question"
    if any(token in lower for token in ("为什么", "怎么", "如何", "什么", "?", "？", "why", "how", "what")):
        return "task_question"
    return "unknown"


__all__ = [
    "InjectionPolicy",
    "IntentKind",
    "PromptFrame",
    "RetrievalMode",
    "analyze_prompt_frame",
]
