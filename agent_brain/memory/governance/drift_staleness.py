from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from agent_brain.memory.governance.drift_types import DriftFinding, DriftType


def detect_staleness(
    items: list[Any],
    staleness_days: int,
    now: datetime | None = None,
) -> list[DriftFinding]:
    current_time = now or datetime.now(timezone.utc)
    cutoff = current_time - timedelta(days=staleness_days)
    findings: list[DriftFinding] = []

    for item in items:
        if item.created_at < cutoff:
            age_days = (current_time - item.created_at).days
            findings.append(DriftFinding(
                drift_type=DriftType.STALENESS,
                item_ids=[item.id],
                confidence=0.9,
                description=f"Item is {age_days} days old (threshold: {staleness_days} days)",
                evidence=f"Created at: {item.created_at.isoformat()}",
            ))

    return findings


__all__ = ["detect_staleness"]
