"""Hopfield-inspired capacity governance for the brain pool.

Classical Hopfield networks have a capacity limit of ~0.14N patterns (N = network
size / embedding dimensions). Beyond this, retrieval degrades catastrophically
(spurious states). While our architecture isn't a literal Hopfield net, the
principle translates: too many hot-tier items degrades retrieval quality.

This module enforces a soft capacity limit on the hot tier. When exceeded,
items with the lowest effective score (confidence × retention × gain) are
demoted to warm or archived, keeping the active working set crisp.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.governance.tiering import (
    Tier,
    TierThresholds,
    scan_tiers,
    tier_for_item,
)
from agent_brain.contracts.memory_item import MemoryItem

_log = logging.getLogger(__name__)

HOPFIELD_RATIO = 0.14
DEFAULT_EMBEDDING_DIM = 384


@dataclass
class CapacityReport:
    embedding_dim: int
    capacity_limit: int
    hot_count: int
    warm_count: int
    cold_count: int
    overflow: int
    demoted: list[str] = field(default_factory=list)

    @property
    def is_over_capacity(self) -> bool:
        return self.hot_count > self.capacity_limit

    @property
    def utilization(self) -> float:
        return self.hot_count / self.capacity_limit if self.capacity_limit > 0 else 0.0


def compute_capacity_limit(embedding_dim: int = DEFAULT_EMBEDDING_DIM) -> int:
    """Compute the soft capacity limit for hot tier based on Hopfield theory.

    For 384-dim embeddings: 0.14 × 384 ≈ 53 items in hot tier.
    For 768-dim: 0.14 × 768 ≈ 107 items.
    For 1536-dim (OpenAI): 0.14 × 1536 ≈ 215 items.
    """
    return max(20, int(HOPFIELD_RATIO * embedding_dim))


def _effective_score(item: MemoryItem) -> float:
    """Combined score for capacity-based ranking: confidence × (1 + gain) × recency_boost."""
    gain_factor = max(0.1, 1.0 + item.gain_score)
    support_factor = min(2.0, 1.0 + item.support_count * 0.05)
    return item.confidence * gain_factor * support_factor


def check_capacity(
    store: ItemsStore,
    *,
    embedding_dim: int = DEFAULT_EMBEDDING_DIM,
    thresholds: Optional[TierThresholds] = None,
) -> CapacityReport:
    """Check current hot-tier utilization against capacity limit."""
    limit = compute_capacity_limit(embedding_dim)
    now = datetime.now(timezone.utc)

    hot, warm, cold = 0, 0, 0
    for _, tier in scan_tiers(store.items_dir, now=now, thresholds=thresholds):
        if tier == Tier.hot:
            hot += 1
        elif tier == Tier.warm:
            warm += 1
        else:
            cold += 1

    return CapacityReport(
        embedding_dim=embedding_dim,
        capacity_limit=limit,
        hot_count=hot,
        warm_count=warm,
        cold_count=cold,
        overflow=max(0, hot - limit),
    )


def enforce_capacity(
    store: ItemsStore,
    *,
    embedding_dim: int = DEFAULT_EMBEDDING_DIM,
    dry_run: bool = True,
    thresholds: Optional[TierThresholds] = None,
) -> CapacityReport:
    """Demote lowest-scoring hot items when over capacity.

    Items are ranked by effective_score; the bottom N (overflow count) get their
    confidence reduced to push them into warm tier on next rebalance.
    """
    limit = compute_capacity_limit(embedding_dim)
    now = datetime.now(timezone.utc)

    hot_items: list[tuple[MemoryItem, float]] = []
    warm_count = 0
    cold_count = 0

    for item, tier in scan_tiers(store.items_dir, now=now, thresholds=thresholds):
        if tier == Tier.hot:
            hot_items.append((item, _effective_score(item)))
        elif tier == Tier.warm:
            warm_count += 1
        else:
            cold_count += 1

    overflow = max(0, len(hot_items) - limit)
    demoted: list[str] = []

    if overflow > 0 and not dry_run:
        hot_items.sort(key=lambda x: x[1])
        to_demote = hot_items[:overflow]

        for item, score in to_demote:
            try:
                new_conf = min(item.confidence, 0.6)
                store.update_frontmatter(item.id, confidence=new_conf)
                demoted.append(item.id)
                _log.info(
                    "capacity demote %s (score=%.3f, conf %.2f→%.2f)",
                    item.id, score, item.confidence, new_conf,
                )
            except Exception as e:
                _log.warning("capacity demote failed for %s: %s", item.id, e)

    return CapacityReport(
        embedding_dim=embedding_dim,
        capacity_limit=limit,
        hot_count=len(hot_items),
        warm_count=warm_count,
        cold_count=cold_count,
        overflow=overflow,
        demoted=demoted,
    )
