"""Storage axis: hot / warm / cold tier classification.

Tier is a *derived* index (capability-building plan §2.5.3 axis B): it is computed
from an item's age (last access, falling back to creation), its confidence, and
whether it has been archived. Tier is deliberately **not** stored in the md
frontmatter — the md file stays flat and is the source of truth; tier lives only
in the sqlite shadow index, written by ``rebalance``.

cold-archiving (physically moving md files to ``items/archived/``) is the existing
``batch-archive`` command; an item under that directory classifies cold. Moving
files automatically during rebalance is deferred to a later version per the plan
(6-month target is the hot/warm split plus automatic re-classification).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.contracts.memory_item import MemoryItem


class Tier(str, Enum):
    hot = "hot"
    warm = "warm"
    cold = "cold"


@dataclass(frozen=True)
class TierThresholds:
    hot_days: int = 30
    cold_days: int = 180
    hot_confidence: float = 0.8
    cold_confidence: float = 0.3


DEFAULT_THRESHOLDS = TierThresholds()


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def effective_age_days(
    last_accessed: Optional[datetime], created_at: datetime, now: datetime
) -> float:
    """Days since the item was last touched (last_accessed, else created_at)."""
    ref = _aware(last_accessed or created_at)
    return (_aware(now) - ref).total_seconds() / 86400.0


def classify_tier(
    *,
    confidence: float,
    last_accessed: Optional[datetime],
    created_at: datetime,
    now: datetime,
    archived: bool = False,
    thresholds: Optional[TierThresholds] = None,
) -> Tier:
    """Deterministic tier from age + confidence + archived flag.

    Precedence (first match wins): archived → low-confidence → stale → recent →
    high-confidence → warm. Staleness (not touched in ``cold_days``) dominates a
    high confidence: an item nobody has read in 180 days is cold regardless.
    """
    th = thresholds or DEFAULT_THRESHOLDS
    if archived:
        return Tier.cold
    if confidence < th.cold_confidence:
        return Tier.cold
    age = effective_age_days(last_accessed, created_at, now)
    if age >= th.cold_days:
        return Tier.cold
    if age < th.hot_days:
        return Tier.hot
    if confidence >= th.hot_confidence:
        return Tier.hot
    return Tier.warm


def tier_for_item(
    item: MemoryItem,
    now: datetime,
    *,
    archived: bool = False,
    thresholds: Optional[TierThresholds] = None,
) -> Tier:
    last = item.retention.last_accessed if item.retention else None
    return classify_tier(
        confidence=item.confidence,
        last_accessed=last,
        created_at=item.created_at,
        now=now,
        archived=archived,
        thresholds=thresholds,
    )


@dataclass
class RebalanceReport:
    distribution: dict[Tier, int]
    applied: int = 0
    changes: list[tuple[str, Tier]] = field(default_factory=list)


def rebalance(
    store: ItemsStore,
    *,
    index: Optional[object] = None,
    apply: bool = False,
    now: Optional[datetime] = None,
    thresholds: Optional[TierThresholds] = None,
) -> RebalanceReport:
    """Recompute tiers for every item; when ``apply``, persist them to the index.

    Offline by default — the distribution is computed straight from the md store
    and needs no sqlite. ``apply`` writes the derived tier into ``items_meta.tier``
    so retrieval / observability can read it (the weekly rebalance step).
    """
    now = now or datetime.now(timezone.utc)
    pairs = list(scan_tiers(store.items_dir, now=now, thresholds=thresholds))
    dist = tier_distribution(t for _, t in pairs)
    applied = 0
    if apply and index is not None:
        for item, tier in pairs:
            try:
                index.update_tier(item.id, tier.value)
                applied += 1
            except Exception:
                pass
    return RebalanceReport(
        distribution=dist,
        applied=applied,
        changes=[(item.id, tier) for item, tier in pairs],
    )


from agent_brain.memory.governance.tier_scan import scan_tiers, tier_distribution  # noqa: E402
