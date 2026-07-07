"""Temporal state classification for before-inject memory safety.

This module treats runtime state, failures, permissions, test/build results,
and availability observations as time-bounded. Old state observations can be
useful history, but they should not be injected as current facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Mapping

from agent_brain.contracts.memory_item import MemoryItem

TemporalCategory = Literal["stable", "current_state", "negative_state", "positive_state"]
TemporalStatus = Literal["current", "stale", "scope_mismatch"]


@dataclass(frozen=True)
class TemporalStateConfig:
    negative_state_ttl_days: int = 2
    positive_state_ttl_days: int = 2
    current_state_ttl_days: int = 2


@dataclass(frozen=True)
class TemporalStateSignal:
    category: TemporalCategory
    status: TemporalStatus
    reasons: tuple[str, ...]
    age_days: int
    ttl_days: int | None = None


class TemporalStateGate:
    """Classify whether a memory item is a stale runtime-state observation."""

    def __init__(
        self,
        config: TemporalStateConfig | None = None,
        *,
        now: datetime | None = None,
    ) -> None:
        self.config = config or TemporalStateConfig()
        self.now = _aware(now or datetime.now(timezone.utc))

    def evaluate(
        self,
        item: MemoryItem,
        body: str = "",
        *,
        current_scope: Mapping[str, str] | None = None,
    ) -> TemporalStateSignal:
        age_days = _age_days(item.created_at, self.now)
        category, reasons = _classify(item, body)
        ttl_days = _ttl_days(category, self.config)
        scope_mismatches = _scope_mismatches(item, current_scope) if category != "stable" else ()
        if scope_mismatches:
            return TemporalStateSignal(
                category=category,
                status="scope_mismatch",
                reasons=tuple([*reasons, *(f"scope_mismatch:{field}" for field in scope_mismatches)]),
                age_days=age_days,
                ttl_days=ttl_days,
            )
        if ttl_days is not None and age_days > ttl_days:
            return TemporalStateSignal(
                category=category,
                status="stale",
                reasons=tuple([*reasons, f"{category}_ttl_expired"]),
                age_days=age_days,
                ttl_days=ttl_days,
            )
        return TemporalStateSignal(
            category=category,
            status="current",
            reasons=tuple(reasons),
            age_days=age_days,
            ttl_days=ttl_days,
        )


_STRONG_STATE_TAGS = {
    "state",
    "current-state",
    "runtime",
    "permission",
    "verification",
    "status",
}
_WEAK_STATE_TAGS = {
    "test",
    "build",
    "release",
    "install",
    "browser",
}

_STATE_TERMS = (
    "当前",
    "状态",
    "权限",
    "浏览器",
    "测试",
    "验证",
    "构建",
    "安装",
    "发布",
    "current",
    "status",
    "runtime",
    "browser",
    "permission",
    "test passed",
    "tests passed",
    "test failed",
    "tests failed",
    "pytest",
    "build",
    "install",
    "release",
)

_NEGATIVE_TERMS = (
    "失败",
    "受限",
    "不可用",
    "无权限",
    "报错",
    "无法",
    "不能",
    "operation not permitted",
    "not permitted",
    "permission denied",
    "failed",
    "blocked",
    "unavailable",
    "error",
)

_POSITIVE_TERMS = (
    "通过",
    "成功",
    "可用",
    "已修复",
    "恢复",
    "passed",
    "available",
    "fixed",
    "restored",
    "success",
)

_SCOPE_FIELDS = ("cwd", "repo", "branch", "os", "adapter")


def _classify(item: MemoryItem, body: str) -> tuple[TemporalCategory, list[str]]:
    if str(item.type) == "artifact":
        return "stable", []

    text = _haystack(item, body)
    tags = {tag.lower() for tag in item.tags}
    reasons: list[str] = []

    has_strong_state_tag = bool(tags & _STRONG_STATE_TAGS)
    has_weak_state_tag = bool(tags & _WEAK_STATE_TAGS)
    has_state_term = _contains_any(text, _STATE_TERMS)
    has_negative = _contains_any(text, _NEGATIVE_TERMS)
    has_positive = _contains_any(text, _POSITIVE_TERMS)
    is_signal = str(item.type) == "signal"
    has_state_anchor = (
        has_strong_state_tag
        or has_state_term
        or is_signal
        or (has_weak_state_tag and (has_negative or has_positive))
    )

    if has_strong_state_tag or (has_weak_state_tag and has_state_anchor):
        reasons.append("state_tag")
    if has_state_term:
        reasons.append("state_term")
    if is_signal:
        reasons.append("signal_type")
    if has_negative and has_state_anchor:
        reasons.append("negative_state_term")
        return "negative_state", reasons
    if has_positive and has_state_anchor:
        reasons.append("positive_state_term")
        return "positive_state", reasons
    if has_strong_state_tag or has_state_term or is_signal:
        return "current_state", reasons
    return "stable", []


def _ttl_days(category: TemporalCategory, config: TemporalStateConfig) -> int | None:
    if category == "negative_state":
        return config.negative_state_ttl_days
    if category == "positive_state":
        return config.positive_state_ttl_days
    if category == "current_state":
        return config.current_state_ttl_days
    return None


def _scope_mismatches(item: MemoryItem, current_scope: Mapping[str, str] | None) -> tuple[str, ...]:
    if not current_scope:
        return ()
    validity = getattr(item, "validity", None)
    if validity is None:
        return ()

    mismatches: list[str] = []
    for field in _SCOPE_FIELDS:
        item_value = getattr(validity, field, None)
        current_value = current_scope.get(field)
        if item_value and current_value and str(item_value) != str(current_value):
            mismatches.append(field)
    return tuple(mismatches)


def _haystack(item: MemoryItem, body: str) -> str:
    return " ".join([
        item.title,
        item.summary,
        " ".join(item.tags),
        body,
    ]).lower()


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _age_days(created_at: datetime, now: datetime) -> int:
    return max(0, (_aware(now) - _aware(created_at)).days)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


__all__ = [
    "TemporalCategory",
    "TemporalStateConfig",
    "TemporalStateGate",
    "TemporalStateSignal",
    "TemporalStatus",
]
