"""Answerability checks for memory candidates before context injection."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

from agent_brain.memory.context.context_firewall_rules import (
    candidate_haystack,
    covered_query_terms,
    matches_query,
)
from agent_brain.memory.context.context_firewall_types import ContextCandidate
from agent_brain.memory.context.query_signal import QuerySignal

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class CandidateAnswerability:
    """Whether a candidate is allowed to answer a structured recall query."""

    answerable: bool
    required_terms: tuple[str, ...]
    covered_terms: tuple[str, ...]
    missing_terms: tuple[str, ...]
    query_intent: str = "general"
    reason: str = "ok"
    semantic_score: float | None = None
    semantic_reason: str | None = None


@dataclass(frozen=True)
class SemanticAnswerabilityDecision:
    """Verifier-level decision for semantic answerability."""

    answerable: bool
    score: float | None = None
    reason: str = "semantic_answerability"


class AnswerabilityVerifier(Protocol):
    """Optional second-stage semantic verifier for context candidates."""

    def verify(
        self,
        *,
        query: str,
        candidate: ContextCandidate,
        signal: QuerySignal,
        deterministic: CandidateAnswerability,
    ) -> SemanticAnswerabilityDecision | None:
        """Return a semantic decision, or None when the verifier is unavailable."""


class CrossEncoderAnswerabilityVerifier:
    """Use a cross-encoder to verify query/candidate answerability."""

    def __init__(
        self,
        *,
        cross_encoder_factory: Callable[[], object] | None = None,
        threshold: float = 0.65,
        max_candidate_chars: int = 4000,
    ) -> None:
        self.cross_encoder_factory = cross_encoder_factory
        self.threshold = threshold
        self.max_candidate_chars = max_candidate_chars

    def verify(
        self,
        *,
        query: str,
        candidate: ContextCandidate,
        signal: QuerySignal,
        deterministic: CandidateAnswerability,
    ) -> SemanticAnswerabilityDecision | None:
        del signal, deterministic
        text = _candidate_verifier_text(candidate, self.max_candidate_chars)
        if not query.strip() or not text:
            return None

        from agent_brain.memory.recall.retrieval_rerank import _get_cross_encoder, _sigmoid

        factory = self.cross_encoder_factory or _get_cross_encoder
        model = factory()
        raw_score = _first_score(model.predict([(query, text)]))
        score = _sigmoid(float(raw_score))
        answerable = score >= self.threshold
        reason = "cross_encoder_answerability"
        if not answerable:
            reason = "cross_encoder_below_threshold"
        return SemanticAnswerabilityDecision(
            answerable=answerable,
            score=score,
            reason=reason,
        )


class LLMAnswerabilityVerifier:
    """Use an injected LLM/judge callable for semantic answerability."""

    def __init__(
        self,
        *,
        judge: Callable[[dict[str, Any]], Any] | None = None,
        threshold: float = 0.65,
        max_candidate_chars: int = 4000,
        model: str | None = None,
    ) -> None:
        self.judge = judge
        self.threshold = threshold
        self.max_candidate_chars = max_candidate_chars
        self.model = model

    def verify(
        self,
        *,
        query: str,
        candidate: ContextCandidate,
        signal: QuerySignal,
        deterministic: CandidateAnswerability,
    ) -> SemanticAnswerabilityDecision | None:
        payload = {
            "query": query,
            "query_intent": deterministic.query_intent,
            "required_terms": list(deterministic.required_terms),
            "covered_terms": list(deterministic.covered_terms),
            "candidate_id": candidate.item.id,
            "candidate_type": str(candidate.item.type),
            "candidate_title": candidate.item.title,
            "candidate_summary": candidate.item.summary,
            "candidate_text": _candidate_verifier_text(candidate, self.max_candidate_chars),
            "signal_terms": list(signal.terms),
            "signal_strong_terms": list(signal.strong_terms),
        }
        raw = self.judge(payload) if self.judge else self._call_litellm(payload)
        return _semantic_decision_from_raw(raw, threshold=self.threshold, default_reason="llm_answerability")

    def _call_litellm(self, payload: dict[str, Any]) -> Any | None:
        try:
            import litellm
        except ImportError:
            _LOG.debug("litellm not installed; LLM answerability verifier unavailable")
            return None

        messages = [
            {"role": "system", "content": _LLM_ANSWERABILITY_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        kwargs: dict[str, Any] = {
            "model": self.model or _answerability_llm_model(),
            "messages": messages,
            "temperature": 0,
            "max_tokens": 256,
        }
        base_url = os.environ.get("MEMORY_HUB_LLM_BASE_URL")
        if base_url:
            kwargs["api_base"] = base_url
        api_key = os.environ.get("MEMORY_HUB_LLM_API_KEY")
        if api_key:
            kwargs["api_key"] = api_key

        try:
            response = litellm.completion(**kwargs)
            text = response.choices[0].message.content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            return json.loads(text)
        except Exception as exc:
            _LOG.warning("LLM answerability verifier failed: %s", exc)
            return None


def verify_candidate_answerability(
    candidate: ContextCandidate,
    signal: QuerySignal,
    *,
    query: str | None = None,
    verifier: AnswerabilityVerifier | None = None,
) -> CandidateAnswerability:
    """Return whether a candidate has the primary anchor needed for injection.

    Retrieval can rank by any overlapping term.  Injection is stricter: when a
    query contains multiple strong terms, the first strong term is treated as the
    primary topic anchor, while later terms may still help cohort coverage and
    ranking.  This fail-closed rule prevents scope-only hits from entering the
    prompt.
    """
    if not signal.injectable:
        return CandidateAnswerability(
            answerable=False,
            required_terms=(),
            covered_terms=(),
            missing_terms=(),
            query_intent="blocked",
            reason="query_not_injectable",
        )

    query_intent = answerability_intent(query or "")
    required_terms = primary_answerability_terms(signal)
    if not required_terms:
        if matches_query(candidate, signal):
            answerability = CandidateAnswerability(True, (), (), (), query_intent, "ok")
            return _apply_semantic_verifier(
                answerability,
                candidate=candidate,
                signal=signal,
                query=query,
                verifier=verifier,
            )
        return CandidateAnswerability(False, (), (), (), query_intent, "query_mismatch")

    covered = tuple(
        term for term in required_terms
        if term in covered_query_terms(candidate, required_terms)
    )
    missing = tuple(term for term in required_terms if term not in covered)
    if missing:
        reason = "answerability_mismatch"
        if not matches_query(candidate, signal):
            reason = "query_mismatch"
        return CandidateAnswerability(
            answerable=False,
            required_terms=required_terms,
            covered_terms=covered,
            missing_terms=missing,
            query_intent=query_intent,
            reason=reason,
        )

    if query_intent == "resolution" and not _has_resolution_evidence(candidate):
        return CandidateAnswerability(
            answerable=False,
            required_terms=required_terms,
            covered_terms=covered,
            missing_terms=(),
            query_intent=query_intent,
            reason="answerability_mismatch",
        )

    answerability = CandidateAnswerability(
        answerable=True,
        required_terms=required_terms,
        covered_terms=covered,
        missing_terms=(),
        query_intent=query_intent,
        reason="ok",
    )
    return _apply_semantic_verifier(
        answerability,
        candidate=candidate,
        signal=signal,
        query=query,
        verifier=verifier,
    )


def primary_answerability_terms(signal: QuerySignal) -> tuple[str, ...]:
    """Return the topic term a candidate must cover to be injected."""
    terms = signal.strong_terms or signal.terms
    if not terms:
        return ()
    return (terms[0],)


_RESOLUTION_QUERY_RE = re.compile(
    r"怎么处理|怎么修|如何处理|如何修|修复|解决|错乱|报错|失败|故障|"
    r"\bfix(?:ed|es|ing)?\b|\bresolve(?:d|s|ing)?\b|\bhandle(?:d|s|ing)?\b|"
    r"\bfail(?:ed|s|ure|ures|ing)?\b|\berror(?:s)?\b",
    re.IGNORECASE,
)
_RESOLUTION_EVIDENCE_RE = re.compile(
    r"修复|解决|处理|验证|回归|通过|原因|根因|失败|检查|契约|必须|"
    r"\bfix(?:ed|es|ing)?\b|\bresolve(?:d|s|ing)?\b|\baddress(?:ed|es|ing)?\b|"
    r"\bverified\b|\bverification\b|\bpassed\b|\btest(?:s|ed|ing)?\b|"
    r"\bfail(?:ed|s|ure|ures|ing)?\b|\bpytest\b|\bgate\b|\bregression\b|"
    r"\broot cause\b|\bdue to\b",
    re.IGNORECASE,
)


def answerability_intent(query: str) -> str:
    """Classify the answer shape requested by the raw user query."""
    if _RESOLUTION_QUERY_RE.search(query):
        return "resolution"
    return "general"


def _has_resolution_evidence(candidate: ContextCandidate) -> bool:
    return bool(_RESOLUTION_EVIDENCE_RE.search(candidate_haystack(candidate)))


def _apply_semantic_verifier(
    deterministic: CandidateAnswerability,
    *,
    candidate: ContextCandidate,
    signal: QuerySignal,
    query: str | None,
    verifier: AnswerabilityVerifier | None,
) -> CandidateAnswerability:
    if not deterministic.answerable or verifier is None or not (query or "").strip():
        return deterministic

    try:
        semantic = verifier.verify(
            query=query or "",
            candidate=candidate,
            signal=signal,
            deterministic=deterministic,
        )
    except Exception as exc:
        _LOG.warning("semantic answerability verifier failed; falling back: %s", exc)
        return deterministic

    if semantic is None:
        return deterministic

    if semantic.answerable:
        return replace(
            deterministic,
            semantic_score=semantic.score,
            semantic_reason=semantic.reason,
        )

    return CandidateAnswerability(
        answerable=False,
        required_terms=deterministic.required_terms,
        covered_terms=deterministic.covered_terms,
        missing_terms=(),
        query_intent=deterministic.query_intent,
        reason="semantic_answerability_mismatch",
        semantic_score=semantic.score,
        semantic_reason=semantic.reason,
    )


def answerability_verifier_from_env() -> AnswerabilityVerifier | None:
    """Build an optional semantic verifier from explicit environment config."""
    mode = os.environ.get("MEMORY_HUB_ANSWERABILITY_VERIFIER", "").strip().lower()
    if mode in ("", "0", "false", "no", "off", "none"):
        return None

    threshold = _env_float("MEMORY_HUB_ANSWERABILITY_THRESHOLD", 0.65)
    max_chars = _env_int("MEMORY_HUB_ANSWERABILITY_MAX_CHARS", 4000)
    if mode in ("cross-encoder", "cross_encoder", "ce"):
        return CrossEncoderAnswerabilityVerifier(
            threshold=threshold,
            max_candidate_chars=max_chars,
        )
    if mode in ("llm", "llm-judge", "llm_judge", "llm-verifier", "llm_verifier"):
        return LLMAnswerabilityVerifier(
            threshold=threshold,
            max_candidate_chars=max_chars,
        )

    _LOG.warning("unknown MEMORY_HUB_ANSWERABILITY_VERIFIER mode: %s", mode)
    return None


def _candidate_verifier_text(candidate: ContextCandidate, max_chars: int) -> str:
    text = candidate_haystack(candidate)
    if max_chars <= 0:
        return text
    return text[:max_chars]


def _first_score(scores: Any) -> Any:
    try:
        return scores[0]
    except TypeError:
        return scores


def _semantic_decision_from_raw(
    raw: Any,
    *,
    threshold: float,
    default_reason: str,
) -> SemanticAnswerabilityDecision | None:
    if raw is None:
        return None
    if isinstance(raw, SemanticAnswerabilityDecision):
        return raw
    if isinstance(raw, bool):
        return SemanticAnswerabilityDecision(
            answerable=raw,
            score=1.0 if raw else 0.0,
            reason=default_reason,
        )
    if not isinstance(raw, Mapping):
        return None

    answerable = _coerce_answerable(raw.get("answerable", raw.get("decision", False)))
    score = _coerce_score(raw.get("score"), default=1.0 if answerable else 0.0)
    reason = str(raw.get("reason") or default_reason)
    return SemanticAnswerabilityDecision(
        answerable=answerable and score >= threshold,
        score=score,
        reason=reason,
    )


def _coerce_answerable(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
            "answerable",
            "allow",
            "allowed",
            "include",
            "pass",
            "passed",
            "ok",
        }
    return bool(value)


def _coerce_score(value: Any, *, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, ""))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, ""))
    except ValueError:
        return default


def _answerability_llm_model() -> str:
    return (
        os.environ.get("MEMORY_HUB_ANSWERABILITY_LLM_MODEL")
        or os.environ.get("MEMORY_HUB_LLM_MODEL")
        or "gpt-4o-mini"
    )


_LLM_ANSWERABILITY_SYSTEM_PROMPT = """\
You judge whether a memory candidate can answer the current user query.
Return only JSON:
{"answerable": true|false, "score": 0.0-1.0, "reason": "short reason"}
Reject topic-only, scope-only, stale-status, or background candidates that mention
the same words but do not answer the user's requested question shape.
"""


__all__ = [
    "AnswerabilityVerifier",
    "CandidateAnswerability",
    "CrossEncoderAnswerabilityVerifier",
    "LLMAnswerabilityVerifier",
    "SemanticAnswerabilityDecision",
    "answerability_intent",
    "answerability_verifier_from_env",
    "primary_answerability_terms",
    "verify_candidate_answerability",
]
