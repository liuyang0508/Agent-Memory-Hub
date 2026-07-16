"""Answerability checks for memory candidates before context injection."""

from __future__ import annotations

import json
import logging
import math
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
from agent_brain.memory.context.context_firewall_types import (
    ContextCandidate,
    ContextFirewallConfig,
)
from agent_brain.memory.context.injection_query_context import InjectionQueryContext
from agent_brain.memory.context.prompt_normalization import normalize_hook_prompt_for_recall
from agent_brain.memory.context.query_signal import QuerySignal
from agent_brain.memory.recall.query_tokens import _tokenize_mixed

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
    execution_failed: bool = False


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
        except Exception:
            _LOG.warning("LLM answerability verifier failed")
            return SemanticAnswerabilityDecision(
                answerable=False,
                reason="llm_answerability_execution_failed",
                execution_failed=True,
            )


def verify_candidate_answerability(
    candidate: ContextCandidate,
    signal: QuerySignal,
    *,
    query: str | None = None,
    verifier: AnswerabilityVerifier | None = None,
    fail_closed_on_verifier_error: bool = False,
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
                fail_closed_on_error=fail_closed_on_verifier_error,
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
        fail_closed_on_error=fail_closed_on_verifier_error,
    )


def verify_routed_candidate_answerability(
    candidate: ContextCandidate,
    query_context: InjectionQueryContext,
    config: ContextFirewallConfig,
    *,
    verifier: AnswerabilityVerifier | None = None,
) -> CandidateAnswerability:
    """Verify one routed candidate from admission and independent route evidence."""
    signal = query_context.query_signal
    query = query_context.raw_query
    query_intent = answerability_intent(query)
    if not query_context.admission.allowed:
        return _route_answerability_failure(query_intent)

    evidence = query_context.evidence_by_id.get(candidate.item.id)
    if evidence is None or not _has_recognized_route_provenance(evidence.routes):
        return _route_answerability_failure(query_intent)

    routes = set(evidence.routes)
    semantic_failure: CandidateAnswerability | None = None
    if "semantic_raw" in routes:
        similarity = evidence.semantic_similarity
        if (
            similarity is not None
            and not isinstance(similarity, bool)
            and isinstance(similarity, (int, float))
            and math.isfinite(similarity)
            and -1.0 <= similarity <= 1.0
            and similarity >= config.semantic_route_min_similarity
        ):
            semantic_answerable = True
            covered: tuple[str, ...] = ()
            if signal.strong_terms:
                coverage, phrases = raw_query_candidate_coverage(query, candidate)
                if not phrases or coverage < config.raw_route_min_coverage:
                    term_answerability = _verify_term_answerability(
                        candidate,
                        signal,
                        query=query,
                        verifier=verifier,
                    )
                    if term_answerability.answerable:
                        return term_answerability
                    semantic_answerable = False
                    semantic_failure = term_answerability
                else:
                    covered = phrases
            if semantic_answerable:
                deterministic = CandidateAnswerability(
                    True,
                    (),
                    covered,
                    (),
                    query_intent,
                    "ok",
                )
                verified = _apply_semantic_verifier(
                    deterministic,
                    candidate=candidate,
                    signal=signal,
                    query=query,
                    verifier=verifier,
                    fail_closed_on_error=True,
                )
                if verified.answerable:
                    return verified
                semantic_failure = verified

    if "lexical_raw_fallback" in routes:
        coverage, covered = raw_query_candidate_coverage(query, candidate)
        if covered and coverage >= config.raw_route_min_coverage:
            deterministic = CandidateAnswerability(
                True,
                (),
                covered,
                (),
                query_intent,
                "ok",
            )
            return _apply_semantic_verifier(
                deterministic,
                candidate=candidate,
                signal=signal,
                query=query,
                verifier=verifier,
                fail_closed_on_error=True,
            )

    if "lexical_terms" in routes and signal.terms:
        return _verify_term_answerability(
            candidate,
            signal,
            query=query,
            verifier=verifier,
        )

    return semantic_failure or _route_answerability_failure(query_intent)


def preselect_routed_candidate_ids(
    candidates: list[ContextCandidate],
    query_context: InjectionQueryContext,
    config: ContextFirewallConfig,
) -> frozenset[str] | None:
    """Choose the bounded routed cohort allowed to reach item policy gates.

    ``None`` means no semantic cohort was available and preserves the existing
    lexical-only policy. An empty set means semantic evidence existed but was
    too ambiguous, so the route fails closed.
    """
    semantic_candidates: list[tuple[ContextCandidate, float, int]] = []
    fully_anchored_lexical: list[ContextCandidate] = []
    for candidate in candidates:
        evidence = query_context.evidence_by_id.get(candidate.item.id)
        if evidence is None:
            continue
        if (
            "lexical_terms" in evidence.routes
            and query_context.query_signal.strong_terms
            and set(query_context.query_signal.strong_terms)
            <= covered_query_terms(
                candidate,
                query_context.query_signal.strong_terms,
            )
        ):
            fully_anchored_lexical.append(candidate)
        similarity = evidence.semantic_similarity
        rank = evidence.semantic_rank
        if (
            "semantic_raw" in evidence.routes
            and rank is not None
            and rank > 0
            and similarity is not None
            and not isinstance(similarity, bool)
            and isinstance(similarity, (int, float))
            and math.isfinite(similarity)
            and -1.0 <= similarity <= 1.0
        ):
            semantic_candidates.append((candidate, float(similarity), rank))

    if not semantic_candidates:
        return None
    if fully_anchored_lexical:
        return frozenset(candidate.item.id for candidate in fully_anchored_lexical)

    semantic_candidates.sort(key=lambda row: (row[2], -row[1], row[0].item.id))
    rank_one = next((row for row in semantic_candidates if row[2] == 1), None)
    if rank_one is None:
        return frozenset()

    anchored = []
    for candidate, similarity, rank in semantic_candidates:
        if rank > 2 or similarity < config.semantic_route_anchor_rescue_min_similarity:
            continue
        coverage, phrases = raw_query_candidate_coverage(
            query_context.raw_query,
            candidate,
        )
        if phrases:
            anchored.append((candidate, coverage, similarity, rank))
    if len(anchored) == 1:
        return frozenset({anchored[0][0].item.id})

    direct = [
        row
        for row in semantic_candidates
        if row[1] >= config.semantic_route_direct_min_similarity
    ]
    if direct:
        selected = [direct[0]]
        covered_units = set(
            raw_query_candidate_covered_units(
                query_context.raw_query,
                direct[0][0],
            )
        )
        for row in direct[1:]:
            if len(selected) >= config.semantic_route_direct_max_items:
                break
            units = set(
                raw_query_candidate_covered_units(
                    query_context.raw_query,
                    row[0],
                )
            )
            if not units - covered_units:
                continue
            selected.append(row)
            covered_units.update(units)
        return frozenset(candidate.item.id for candidate, _similarity, _rank in selected)

    winner, winner_similarity, _rank = rank_one
    second_similarity = max(
        (similarity for _candidate, similarity, rank in semantic_candidates if rank > 1),
        default=-1.0,
    )
    margin = winner_similarity - second_similarity
    if (
        winner_similarity >= config.semantic_route_min_similarity
        and margin >= config.semantic_route_min_margin
    ):
        return frozenset({winner.item.id})
    return frozenset()


def _verify_term_answerability(
    candidate: ContextCandidate,
    signal: QuerySignal,
    *,
    query: str,
    verifier: AnswerabilityVerifier | None,
) -> CandidateAnswerability:
    """Apply legacy anchor rules without consulting signal injectability."""
    allowed_signal = replace(signal, injectable=True)
    return verify_candidate_answerability(
        candidate,
        allowed_signal,
        query=query,
        verifier=verifier,
        fail_closed_on_verifier_error=True,
    )


def _route_answerability_failure(query_intent: str) -> CandidateAnswerability:
    return CandidateAnswerability(
        False,
        (),
        (),
        (),
        query_intent,
        "route_answerability_insufficient",
    )


_ROUTED_ANSWERABILITY_ROUTES = frozenset({
    "semantic_raw",
    "lexical_terms",
    "lexical_raw_fallback",
})


def _has_recognized_route_provenance(routes: tuple[str, ...]) -> bool:
    route_set = set(routes)
    return bool(route_set) and route_set <= _ROUTED_ANSWERABILITY_ROUTES


_RAW_QUERY_ASCII_NOISE = frozenset({
    "a",
    "an",
    "and",
    "are",
    "at",
    "be",
    "changed",
    "did",
    "do",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "please",
    "the",
    "to",
    "what",
    "was",
    "when",
    "where",
    "were",
    "which",
    "why",
    "with",
})
_RAW_QUERY_CJK_NOISE = frozenset("的了吗呢吧啊呀我你他她它请把给在和与及是有")
_CJK_RUN_RE = re.compile(r"[\u3400-\u9fff]+")
_RAW_QUERY_CJK_NOISE_PHRASE_RE = re.compile(
    r"都做了什么|告诉我|帮我|麻烦|继续|接着|排查|处理|修复|优化|修改|"
    r"分析|说明|看看|一下|怎么|如何|为什么|什么|哪些|是否|请"
)


def raw_query_candidate_coverage(
    raw_query: str,
    candidate: ContextCandidate,
) -> tuple[float, tuple[str, ...]]:
    """Return non-noise raw-query token coverage and matched substantive phrases."""
    normalized = normalize_hook_prompt_for_recall(raw_query).casefold()
    substantive_text = _RAW_QUERY_CJK_NOISE_PHRASE_RE.sub(" ", normalized)
    mixed_tokens = _tokenize_mixed(substantive_text)
    ascii_sequence = tuple(
        token.casefold()
        for token in mixed_tokens
        if token.isascii() and _is_substantive_raw_token(token)
    )
    ascii_tokens = tuple(dict.fromkeys(ascii_sequence))
    cjk_units = tuple(dict.fromkeys(
        unit
        for run in _substantive_cjk_runs(substantive_text)
        for unit in _cjk_coverage_units(run)
    ))
    units = (*ascii_tokens, *cjk_units)
    if not units:
        return 0.0, ()

    haystack = candidate_haystack(candidate)
    covered_ascii = tuple(
        token for token in ascii_tokens
        if _ascii_token_matches(token, haystack)
    )
    covered_cjk = tuple(unit for unit in cjk_units if unit in haystack)
    coverage = (len(covered_ascii) + len(covered_cjk)) / len(units)
    covered_ascii_phrases = _covered_ascii_phrases(ascii_sequence, haystack)
    if len(ascii_sequence) >= 2 and not covered_ascii_phrases:
        return coverage, ()
    covered_phrases = tuple(dict.fromkeys([
        *covered_ascii_phrases,
        *covered_cjk,
    ]))
    return coverage, covered_phrases


def raw_query_candidate_covered_units(
    raw_query: str,
    candidate: ContextCandidate,
) -> tuple[str, ...]:
    """Return substantive query units independently covered by one candidate."""
    normalized = normalize_hook_prompt_for_recall(raw_query).casefold()
    substantive_text = _RAW_QUERY_CJK_NOISE_PHRASE_RE.sub(" ", normalized)
    mixed_tokens = _tokenize_mixed(substantive_text)
    ascii_tokens = tuple(dict.fromkeys(
        token.casefold()
        for token in mixed_tokens
        if token.isascii() and _is_substantive_raw_token(token)
    ))
    cjk_units = tuple(dict.fromkeys(
        unit
        for run in _substantive_cjk_runs(substantive_text)
        for unit in _cjk_coverage_units(run)
    ))
    haystack = candidate_haystack(candidate)
    return tuple([
        *(token for token in ascii_tokens if _ascii_token_matches(token, haystack)),
        *(unit for unit in cjk_units if unit in haystack),
    ])


def _substantive_cjk_runs(text: str) -> tuple[str, ...]:
    runs: list[str] = []
    for raw_run in _CJK_RUN_RE.findall(text):
        chunks = re.split(
            f"[{re.escape(''.join(sorted(_RAW_QUERY_CJK_NOISE)))}]+",
            raw_run,
        )
        runs.extend(chunk for chunk in chunks if chunk)
    return tuple(runs)


def _cjk_coverage_units(run: str) -> tuple[str, ...]:
    if len(run) >= 3:
        return tuple(run[index:index + 3] for index in range(len(run) - 2))
    if len(run) == 2:
        return (run,)
    return ()


def _ascii_token_matches(token: str, haystack: str) -> bool:
    return re.search(
        rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])",
        haystack,
        re.IGNORECASE,
    ) is not None


def _covered_ascii_phrases(
    tokens: tuple[str, ...],
    haystack: str,
) -> tuple[str, ...]:
    if len(tokens) == 1:
        return tokens if _ascii_token_matches(tokens[0], haystack) else ()
    phrases: list[str] = []
    for left, right in zip(tokens, tokens[1:], strict=False):
        pattern = (
            rf"(?<![A-Za-z0-9_]){re.escape(left)}"
            rf"[^A-Za-z0-9_]+{re.escape(right)}(?![A-Za-z0-9_])"
        )
        if re.search(pattern, haystack, re.IGNORECASE):
            phrases.append(f"{left} {right}")
    return tuple(phrases)


def _is_substantive_raw_token(token: str) -> bool:
    lowered = token.casefold().strip("._-+")
    if not lowered:
        return False
    if lowered.isascii():
        return len(lowered) >= 2 and lowered not in _RAW_QUERY_ASCII_NOISE
    return lowered not in _RAW_QUERY_CJK_NOISE


def primary_answerability_terms(signal: QuerySignal) -> tuple[str, ...]:
    """Return the topic term a candidate must cover to be injected."""
    terms = substantive_answerability_terms(signal.strong_terms or signal.terms)
    if not terms:
        return ()
    return (terms[0],)


def substantive_answerability_terms(terms: tuple[str, ...]) -> tuple[str, ...]:
    """Remove conversational noise before applying item/cohort anchor gates."""
    return tuple(term for term in terms if _is_substantive_raw_token(term))


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
    fail_closed_on_error: bool = False,
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
    except Exception:
        _LOG.warning("semantic answerability verifier failed")
        if fail_closed_on_error:
            return CandidateAnswerability(
                answerable=False,
                required_terms=deterministic.required_terms,
                covered_terms=deterministic.covered_terms,
                missing_terms=(),
                query_intent=deterministic.query_intent,
                reason="semantic_answerability_mismatch",
            )
        return deterministic

    if semantic is None:
        return deterministic

    if semantic.execution_failed:
        if not fail_closed_on_error:
            return deterministic
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
        execution_failed=bool(raw.get("execution_failed", False)),
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
    "raw_query_candidate_covered_units",
    "substantive_answerability_terms",
    "verify_candidate_answerability",
]
