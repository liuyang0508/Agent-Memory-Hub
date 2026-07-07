"""Conflict Auto-Resolution — extends drift detection with resolution strategies.

When DriftDetector finds contradictions, this module applies one of several
strategies to resolve them automatically:

Strategies:
  - KEEP_NEWER: Supersede the older item, keep the newer decision
  - KEEP_HIGHER_CONFIDENCE: Supersede the lower-confidence item
  - MARK_CONTESTED: Mark both items as contested (add tag, lower confidence)
  - MERGE_RESOLUTION: Create a new resolution item summarizing the conflict

Selection logic:
  - confidence >= 0.8 contradiction → KEEP_NEWER (clear temporal winner)
  - confidence >= 0.6 + both items high confidence → MARK_CONTESTED (genuine tension)
  - confidence < 0.6 → MARK_CONTESTED (uncertain, flag for human review)
  - If one item's confidence is 0.3+ higher → KEEP_HIGHER_CONFIDENCE
"""
from __future__ import annotations

import logging
from typing import Any

from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.governance.conflict_actions import (
    resolve_keep_higher_confidence as _resolve_keep_higher_confidence,
    resolve_keep_newer as _resolve_keep_newer,
    resolve_mark_contested as _resolve_mark_contested,
    resolve_merge as _resolve_merge,
)
from agent_brain.memory.governance.conflict_strategy import select_strategy as _select_strategy
from agent_brain.memory.governance.drift import DriftDetector, DriftType
from agent_brain.memory.governance.conflict_types import ConflictReport, Resolution, ResolutionStrategy
from agent_brain.contracts.memory_item import MemoryItem

_log = logging.getLogger(__name__)


def resolve_conflicts(
    store: ItemsStore,
    *,
    dry_run: bool = True,
    strategy_override: ResolutionStrategy | None = None,
    embedder: Any = None,
) -> ConflictReport:
    """Detect contradictions and apply resolution strategies.

    Args:
        store: The items store to scan and modify
        dry_run: If True, report what would happen without modifying items
        strategy_override: Force a specific strategy for all contradictions
        embedder: Optional embedder for semantic contradiction detection
    """
    detector = DriftDetector(items_store=store, embedder=embedder)
    drift_report = detector.detect()

    contradictions = [
        f for f in drift_report.findings
        if f.drift_type == DriftType.CONTRADICTION
    ]

    report = ConflictReport(contradictions_found=len(contradictions))

    items_cache: dict[str, tuple[MemoryItem, str]] = {}
    for item, body in store.iter_all():
        items_cache[item.id] = (item, body)

    for finding in contradictions:
        if len(finding.item_ids) < 2:
            continue

        id_a, id_b = finding.item_ids[0], finding.item_ids[1]
        if id_a not in items_cache or id_b not in items_cache:
            continue

        item_a, body_a = items_cache[id_a]
        item_b, body_b = items_cache[id_b]

        if strategy_override:
            strategy = strategy_override
            reason = f"forced strategy: {strategy.value}"
        else:
            strategy, reason = _select_strategy(finding, item_a, item_b)

        resolution = Resolution(
            finding=finding,
            strategy=strategy,
            reason=reason,
        )

        if strategy == ResolutionStrategy.KEEP_NEWER:
            winner, loser = _resolve_keep_newer(store, item_a, item_b, dry_run)
            resolution.winner_id = winner
            resolution.loser_id = loser
            resolution.applied = not dry_run

        elif strategy == ResolutionStrategy.KEEP_HIGHER_CONFIDENCE:
            winner, loser = _resolve_keep_higher_confidence(store, item_a, item_b, dry_run)
            resolution.winner_id = winner
            resolution.loser_id = loser
            resolution.applied = not dry_run

        elif strategy == ResolutionStrategy.MARK_CONTESTED:
            _resolve_mark_contested(store, item_a, item_b, dry_run)
            resolution.applied = not dry_run

        elif strategy == ResolutionStrategy.MERGE_RESOLUTION:
            rid = _resolve_merge(store, item_a, item_b, body_a, body_b, dry_run)
            resolution.resolution_item_id = rid
            resolution.applied = not dry_run

        report.resolutions.append(resolution)
        if resolution.applied:
            _log.info(
                "resolved conflict [%s vs %s] via %s: %s",
                id_a, id_b, strategy.value, reason,
            )

    return report
