from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from agent_brain.contracts.conversation import ConversationMessageRecord, ConversationTier


@dataclass(frozen=True)
class ConversationTierThresholds:
    hot_days: int = 14
    cold_days: int = 180
    frozen_days: int = 365
    cold_score: float = 0.2
    hot_score: float = 0.7


DEFAULT_THRESHOLDS = ConversationTierThresholds()


@dataclass
class ConversationRebalanceReport:
    scanned: int = 0
    updated: int = 0
    distribution: dict[str, int] = field(
        default_factory=lambda: {tier.value: 0 for tier in ConversationTier}
    )


@dataclass(frozen=True)
class ForgettingCurveScore:
    """Explainable score for raw conversation evidence retention."""

    score: float
    days_since_reference: float
    half_life_days: int
    components: dict[str, float]


def forgetting_curve_score(
    message: ConversationMessageRecord,
    *,
    now: datetime | None = None,
) -> ForgettingCurveScore:
    """Return a multi-axis forgetting-curve score for raw evidence.

    Raw transcript messages are not ranked as MemoryItem knowledge, but their
    storage tier needs a defensible retention model. Time decay is the base;
    access, importance, support, and gain reinforce useful evidence, while
    contradiction and noise metadata cool it down.
    """
    now = _aware(now or datetime.now(timezone.utc))
    ref = _aware(message.retention.last_accessed or message.observed_at)
    days = max(0.0, (now - ref).total_seconds() / 86400.0)
    half_life_days = max(1, int(message.retention.half_life_days))
    time_retention = math.pow(0.5, days / half_life_days)

    support_count = _metadata_int(message, "support_count")
    contradict_count = _metadata_int(message, "contradict_count")
    gain_score = _metadata_float(message, "gain_score")
    noise_score = max(0.0, min(1.0, _metadata_float(message, "noise_score")))

    access_reinforcement = min(0.35, math.log1p(max(0, message.retention.access_count)) * 0.1)
    importance_reinforcement = min(0.2, max(0.0, message.retention.importance) * 0.2)
    evidence_reinforcement = min(
        0.25,
        max(0, support_count) * 0.03 + max(0.0, gain_score) * 0.10,
    )
    contradiction_penalty = min(
        0.4,
        max(0, contradict_count) * 0.08 + abs(min(0.0, gain_score)) * 0.10,
    )
    noise_penalty = min(0.3, noise_score * 0.3)
    score = (
        time_retention
        + access_reinforcement
        + importance_reinforcement
        + evidence_reinforcement
        - contradiction_penalty
        - noise_penalty
    )
    components = {
        "time_retention": time_retention,
        "access_reinforcement": access_reinforcement,
        "importance_reinforcement": importance_reinforcement,
        "evidence_reinforcement": evidence_reinforcement,
        "contradiction_penalty": contradiction_penalty,
        "noise_penalty": noise_penalty,
    }
    return ForgettingCurveScore(
        score=min(1.0, max(0.0, score)),
        days_since_reference=days,
        half_life_days=half_life_days,
        components=components,
    )


def retention_score(message: ConversationMessageRecord, *, now: datetime | None = None) -> float:
    """Ebbinghaus-style retention score for raw conversation evidence.

    The score is intentionally explainable and multi-axis. It is used for
    storage-tier governance, not semantic ranking.
    """
    return forgetting_curve_score(message, now=now).score


def classify_tier(
    message: ConversationMessageRecord,
    *,
    now: datetime | None = None,
    thresholds: ConversationTierThresholds | None = None,
) -> ConversationTier:
    now = _aware(now or datetime.now(timezone.utc))
    thresholds = thresholds or DEFAULT_THRESHOLDS
    ref = _aware(message.retention.last_accessed or message.observed_at)
    age_days = max(0.0, (now - ref).total_seconds() / 86400.0)
    score = retention_score(message, now=now)

    if age_days >= thresholds.frozen_days:
        return ConversationTier.frozen
    if age_days >= thresholds.cold_days or score < thresholds.cold_score:
        return ConversationTier.cold
    if age_days <= thresholds.hot_days or score >= thresholds.hot_score:
        return ConversationTier.hot
    return ConversationTier.warm


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _metadata_int(message: ConversationMessageRecord, key: str) -> int:
    try:
        return int(message.metadata.get(key, 0))
    except (TypeError, ValueError):
        return 0


def _metadata_float(message: ConversationMessageRecord, key: str) -> float:
    try:
        return float(message.metadata.get(key, 0.0))
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "ConversationRebalanceReport",
    "ConversationTierThresholds",
    "DEFAULT_THRESHOLDS",
    "ForgettingCurveScore",
    "classify_tier",
    "forgetting_curve_score",
    "retention_score",
]
