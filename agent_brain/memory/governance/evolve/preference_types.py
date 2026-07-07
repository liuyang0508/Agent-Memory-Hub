"""Shared value objects for inferred preference profiles."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class PreferenceSignal:
    """A single inferred preference."""

    dimension: str
    preference: str
    anti_preference: str | None
    confidence: float
    evidence_count: int
    tags: list[str] = field(default_factory=list)
    scope_match: str = "exact"
    source_item_ids: list[str] = field(default_factory=list)


@dataclass
class PreferenceProfile:
    """Aggregated user preference profile."""

    generated_at: datetime
    signals: list[PreferenceSignal] = field(default_factory=list)
    top_projects: list[tuple[str, int]] = field(default_factory=list)
    top_tags: list[tuple[str, int]] = field(default_factory=list)
    decision_patterns: list[str] = field(default_factory=list)
    scope: dict[str, object] = field(default_factory=dict)


__all__ = ["PreferenceProfile", "PreferenceSignal"]
