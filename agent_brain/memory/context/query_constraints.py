"""Deterministic, evidence-preserving query constraint extraction.

This module deliberately extracts only constraints that are explicit in the
user's text.  It does not paraphrase a prompt or infer unstated intent.  The
original substring is retained as ``evidence`` so downstream retrieval can
audit every parsed constraint without another model call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ConstraintKind = Literal["prohibition", "absence", "requirement"]


@dataclass(frozen=True)
class QueryConstraint:
    """One explicit boolean constraint bound to its source text."""

    predicate: str
    polarity: bool
    evidence: str
    kind: ConstraintKind
    confidence: float = 0.9

    def __post_init__(self) -> None:
        if not self.predicate.strip():
            raise ValueError("constraint predicate must be non-empty")
        if not self.evidence.strip():
            raise ValueError("constraint evidence must be non-empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("constraint confidence must be between 0 and 1")

    def to_dict(self) -> dict[str, object]:
        return {
            "predicate": self.predicate,
            "polarity": self.polarity,
            "evidence": self.evidence,
            "kind": self.kind,
            "confidence": self.confidence,
        }


_CJK_NEGATION_MARKERS = (
    "不应该",
    "不需要",
    "不建议",
    "尚未",
    "还未",
    "并未",
    "从未",
    "未能",
    "没有",
    "无需",
    "不必",
    "不要",
    "不应",
    "不能",
    "不可",
    "禁止",
    "避免",
    "绝不",
    "不用",
    "勿",
    "没",
    "未",
)
_CJK_NEGATION_RE = re.compile(
    r"(?P<marker>" + "|".join(map(re.escape, _CJK_NEGATION_MARKERS)) + r")"
    r"(?P<target>[A-Za-z0-9_+./\-\u4e00-\u9fff]{1,24})",
    re.IGNORECASE,
)
_CJK_DOUBLE_NEGATION_RE = re.compile(
    r"(?:" + "|".join(map(re.escape, _CJK_NEGATION_MARKERS)) + r")[不没未无]"
)
_CJK_TARGET_STOPS = (
    "怎么",
    "如何",
    "为什么",
    "为何",
    "是否",
    "能否",
    "请问",
    "而是",
    "但是",
    "然后",
    "并且",
    "同时",
    "或者",
    "以及",
)
_CJK_ABSENCE_MARKERS = frozenset({"尚未", "还未", "并未", "从未", "未能", "没有", "没", "未"})
_CJK_QUESTION_PREFIXES = ("为什么", "为何", "怎么", "是否", "能否", "是不是", "有没有")
_CJK_EPISTEMIC_PREFIXES = (
    "看到",
    "看见",
    "看懂",
    "找到",
    "收到",
    "听到",
    "理解",
    "明白",
    "知道",
    "发现",
    "注意",
    "想起",
)

_EN_NEGATION_RE = re.compile(
    r"\b(?P<marker>do\s+not|does\s+not|did\s+not|must\s+not|should\s+not|"
    r"cannot|can't|never|without|avoid(?:s|ed|ing)?|not)\s+"
    r"(?P<target>[a-z0-9_+./-]+(?:\s+[a-z0-9_+./-]+){0,7})",
    re.IGNORECASE,
)
_EN_DOUBLE_NEGATION_RE = re.compile(
    r"\b(?:do\s+not|does\s+not|did\s+not|must\s+not|should\s+not|"
    r"cannot|can't|never|without|not)\s+(?:not|never|without)\b",
    re.IGNORECASE,
)
_EN_TARGET_STOPS = frozenset(
    {"and", "but", "because", "how", "why", "whether", "then", "while", "when"}
)


def extract_query_constraints(prompt: str) -> tuple[QueryConstraint, ...]:
    """Extract explicit negative constraints without generative rewriting."""

    constraints: list[QueryConstraint] = []
    seen: set[tuple[str, bool]] = set()

    for match in _CJK_NEGATION_RE.finditer(prompt):
        marker = match.group("marker")
        raw_target = match.group("target")
        if _is_cjk_nonconstraint_context(prompt, match.start(), marker, raw_target):
            continue
        predicate = _trim_cjk_target(raw_target)
        if not predicate or _starts_with_negation(predicate):
            continue
        evidence = marker + raw_target[: len(predicate)]
        constraint = QueryConstraint(
            predicate=predicate,
            polarity=False,
            evidence=evidence,
            kind="absence" if marker in _CJK_ABSENCE_MARKERS else "prohibition",
            confidence=0.92,
        )
        _append_constraint(constraints, seen, constraint)

    for match in _EN_NEGATION_RE.finditer(prompt):
        marker = " ".join(match.group("marker").lower().split())
        raw_target = match.group("target")
        prefix = prompt[max(0, match.start() - 16):match.start()].casefold().rstrip()
        if re.search(r"\b(?:why|how|whether)\s*$", prefix):
            continue
        predicate = _trim_english_target(raw_target)
        if not predicate or predicate.startswith(("not ", "never ")):
            continue
        evidence = f"{match.group('marker')} {predicate}"
        constraint = QueryConstraint(
            predicate=predicate,
            polarity=False,
            evidence=evidence,
            kind="absence" if marker in {"never", "not"} else "prohibition",
            confidence=0.9,
        )
        _append_constraint(constraints, seen, constraint)

    return tuple(constraints)


def constraint_parse_confidence(
    prompt: str,
    constraints: tuple[QueryConstraint, ...],
    *,
    dropped_spans: tuple[str, ...] = (),
) -> float:
    """Return a bounded confidence used to route future model fallbacks."""

    confidence = 1.0
    if _CJK_DOUBLE_NEGATION_RE.search(prompt) or _EN_DOUBLE_NEGATION_RE.search(prompt):
        confidence = 0.5
    elif len(constraints) > 2:
        confidence = 0.8
    if dropped_spans:
        confidence = min(confidence, 0.82)
    return confidence


def _trim_cjk_target(value: str) -> str:
    end = len(value)
    for stop in _CJK_TARGET_STOPS:
        position = value.find(stop)
        if position >= 0:
            end = min(end, position)
    target = value[:end].strip("的了呢吗吧啊呀")
    while target.startswith("再") and len(target) > 1:
        target = target[1:]
    return target[:16]


def _trim_english_target(value: str) -> str:
    words = value.strip().split()
    for index, word in enumerate(words):
        if word.lower() in _EN_TARGET_STOPS:
            words = words[:index]
            break
    return " ".join(words).strip(" .?!,;:")


def _starts_with_negation(value: str) -> bool:
    return value.startswith(("不", "没", "未", "无")) or any(
        value.startswith(marker) for marker in _CJK_NEGATION_MARKERS
    )


def _is_cjk_nonconstraint_context(
    prompt: str,
    start: int,
    marker: str,
    raw_target: str,
) -> bool:
    prefix = prompt[max(0, start - 12):start].rstrip()
    if any(prefix.endswith(question) for question in _CJK_QUESTION_PREFIXES):
        return True
    if raw_target.endswith(("吗", "么")):
        return True
    if marker in _CJK_ABSENCE_MARKERS:
        if raw_target.startswith("在"):
            return True
        if raw_target.startswith(_CJK_EPISTEMIC_PREFIXES):
            return True
    return False


def _append_constraint(
    constraints: list[QueryConstraint],
    seen: set[tuple[str, bool]],
    constraint: QueryConstraint,
) -> None:
    key = (constraint.predicate.casefold(), constraint.polarity)
    if key in seen:
        return
    seen.add(key)
    constraints.append(constraint)


__all__ = [
    "ConstraintKind",
    "QueryConstraint",
    "constraint_parse_confidence",
    "extract_query_constraints",
]
