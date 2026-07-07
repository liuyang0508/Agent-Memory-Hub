"""Observability metrics for Agent Memory Hub brain pool.

Provides stats collection and health scoring without requiring embedder
or network access (uses ItemsStore only).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from agent_brain.contracts.memory_item import MemoryItem


@dataclass
class BrainStats:
    """Raw counts and distributions for a brain pool."""

    total_items: int = 0
    type_counts: dict[str, int] = field(default_factory=dict)
    project_counts: dict[str, int] = field(default_factory=dict)
    agent_counts: dict[str, int] = field(default_factory=dict)
    sensitivity_counts: dict[str, int] = field(default_factory=dict)
    tag_counts: dict[str, int] = field(default_factory=dict)
    oldest: datetime | None = None
    newest: datetime | None = None
    avg_body_length: float = 0.0
    weekly_trend: list[tuple[str, int]] = field(default_factory=list)
    skipped_count: int = 0


def collect_stats(
    items: list[tuple[MemoryItem, str]],
    *,
    project_filter: str | None = None,
    skipped_count: int = 0,
) -> BrainStats:
    """Collect stats from a list of (MemoryItem, body) pairs."""
    if project_filter:
        items = [(it, body) for it, body in items if it.project == project_filter]

    stats = BrainStats(total_items=len(items), skipped_count=skipped_count)
    if not items:
        return stats

    type_c: Counter[str] = Counter()
    project_c: Counter[str] = Counter()
    agent_c: Counter[str] = Counter()
    sens_c: Counter[str] = Counter()
    tag_c: Counter[str] = Counter()
    total_body_len = 0
    dates: list[datetime] = []

    for item, body in items:
        type_c[item.type] += 1
        project_c[item.project or "(none)"] += 1
        agent_c[item.agent or "(none)"] += 1
        sens_c[item.sensitivity] += 1
        for tag in item.tags:
            tag_c[tag] += 1
        total_body_len += len(body)
        dates.append(item.created_at)

    stats.type_counts = dict(type_c.most_common())
    stats.project_counts = dict(project_c.most_common(10))
    stats.agent_counts = dict(agent_c.most_common())
    stats.sensitivity_counts = dict(sens_c.most_common())
    stats.tag_counts = dict(tag_c.most_common(15))
    stats.avg_body_length = total_body_len / len(items) if items else 0
    stats.oldest = min(dates)
    stats.newest = max(dates)

    week_counter: Counter[str] = Counter()
    for d in dates:
        week_label = d.strftime("%Y-W%W")
        week_counter[week_label] += 1
    recent_weeks = sorted(week_counter.items(), reverse=True)[:8]
    stats.weekly_trend = list(reversed(recent_weeks))

    return stats


@dataclass
class HealthScore:
    """Composite health assessment of a brain pool."""

    total_items: int = 0
    governance_issues: int = 0
    duplicates: int = 0
    noise: int = 0
    expired: int = 0
    low_quality: int = 0
    drift_findings: int = 0
    contradictions: int = 0
    stale: int = 0
    citation_rot: int = 0
    drift_clusters: int = 0
    skipped_items: int = 0

    items_with_issues: int = 0

    @property
    def issue_rate(self) -> float:
        if self.total_items == 0:
            return 0.0
        return self.items_with_issues / self.total_items

    @property
    def grade(self) -> str:
        rate = self.issue_rate
        drift = self.drift_findings
        if rate == 0 and drift == 0:
            return "A"
        if rate < 0.05 and drift <= 2:
            return "A-"
        if rate < 0.10 and drift <= 5:
            return "B+"
        if rate < 0.15 and drift <= 10:
            return "B"
        if rate < 0.25 and drift <= 20:
            return "C"
        return "D"

    @property
    def healthy(self) -> bool:
        return self.grade.startswith("A") or self.grade.startswith("B")
