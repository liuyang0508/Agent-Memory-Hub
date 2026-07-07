"""Drift cluster finding assembly for governance drift detection."""

from __future__ import annotations

from typing import Any

from agent_brain.memory.governance.drift_types import DriftFinding, DriftType
from agent_brain.memory.governance.runtime_noise import is_governance_noise

_MIN_CLUSTER_ITEMS = 4

_BROAD_TAGS = {
    "agent-memory-hub",
    "memory",
    "decision",
    "fact",
    "artifact",
    "episode",
    "signal",
    "handoff",
    "architecture",
    "refactor",
    "verification",
    "test",
    "tests",
    "needs-review",
    "auto-captured",
}


def detect_drift_clusters(items: list[Any]) -> list[DriftFinding]:
    """Find same-topic item clusters that warrant consolidation review."""
    findings: list[DriftFinding] = []

    buckets: dict[tuple[str, str], list[Any]] = {}
    topic_groups: dict[tuple[str, str], dict[str, list[Any]]] = {}
    for item in items:
        if is_governance_noise(item):
            continue
        project = item.project or "unknown"
        item_type = str(getattr(item, "type", "") or "unknown")
        bucket_key = (project, item_type)
        buckets.setdefault(bucket_key, []).append(item)
        for tag in _meaningful_tags(item):
            topic_groups.setdefault(bucket_key, {}).setdefault(tag, []).append(item)

    for (project, item_type), topics in sorted(topic_groups.items()):
        eligible_topics = [
            (topic, group)
            for topic, group in topics.items()
            if len(group) >= _MIN_CLUSTER_ITEMS
        ]
        if not eligible_topics:
            continue

        eligible_topics.sort(key=lambda entry: (-len(entry[1]), entry[0]))
        cluster_ids = {item.id for _, group in eligible_topics for item in group}
        item_ids = [item.id for item in buckets[(project, item_type)] if item.id in cluster_ids]
        topic_summary = ", ".join(
            f"{topic}:{len(group)}"
            for topic, group in eligible_topics[:5]
        )
        findings.append(DriftFinding(
            drift_type=DriftType.DRIFT_CLUSTER,
            item_ids=item_ids,
            confidence=0.5,
            description=(
                f"Project '{project}' has {len(item_ids)} {item_type} items "
                f"across {len(eligible_topics)} recurring topic tags that may "
                f"need consolidation"
            ),
            evidence=(
                f"Topics: {topic_summary}; "
                f"Items: {', '.join(item_ids[:5])}"
            ),
        ))

    return findings


def _meaningful_tags(item: Any) -> set[str]:
    tags = set()
    for tag in getattr(item, "tags", ()) or ():
        value = str(tag).strip().lower()
        if not value or value in _BROAD_TAGS or value.startswith("session-"):
            continue
        tags.add(value)
    return tags


__all__ = ["detect_drift_clusters"]
