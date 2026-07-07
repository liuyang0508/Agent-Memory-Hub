"""Abstraction axis: L0 → L1 consolidation.

Mechanically merges ≥N raw (L0) facts that share a (project, tag) into a single
L1 "consolidated" fact. Non-destructive: source items are never deleted — they
remain the source of truth, and the new L1 item references them via refs.mems.

Offline-first (§4.2 of the capability-building plan): the default body is a
template merge that needs no LLM. An optional ``summarizer`` callable lets a
caller opt into LLM summarisation while a provenance footer always preserves the
source IDs.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.governance.consolidation_builder import build_consolidated_item
from agent_brain.memory.governance.consolidation_types import (
    ConsolidationGroup,
    ConsolidationReport,
    ItemBody,
    Summarizer,
)
from agent_brain.contracts.memory_item import (
    AbstractionLayer,
    MemoryType,
)


def find_consolidation_groups(
    items: list[ItemBody],
    *,
    min_group: int = 3,
    min_confidence: float = 0.6,
    project: Optional[str] = None,
    tag: Optional[str] = None,
) -> list[ConsolidationGroup]:
    """Group eligible L0 facts by (project, tag).

    Eligibility: type == fact, abstraction == L0, has a project, confidence >=
    ``min_confidence``. Optional ``project`` / ``tag`` narrow the scan.

    An item that carries several tags could match several buckets; greedy dedupe
    assigns it to exactly one group (largest bucket first) so it is never
    consolidated twice in a single run.
    """
    buckets: dict[tuple[str, str], list[ItemBody]] = defaultdict(list)
    for item, body in items:
        if item.type != MemoryType.fact:
            continue
        if item.abstraction != AbstractionLayer.L0:
            continue
        if not item.project:
            continue
        if item.confidence < min_confidence:
            continue
        if project is not None and item.project != project:
            continue
        for t in item.tags:
            if tag is not None and t != tag:
                continue
            buckets[(item.project, t)].append((item, body))

    # Largest buckets first (then deterministic by project, tag) so a shared
    # item lands in its biggest group.
    ordered_keys = sorted(buckets, key=lambda k: (-len(buckets[k]), k[0], k[1]))

    used: set[str] = set()
    groups: list[ConsolidationGroup] = []
    for proj, t in ordered_keys:
        remaining = [(it, b) for it, b in buckets[(proj, t)] if it.id not in used]
        if len(remaining) < min_group:
            continue
        for it, _ in remaining:
            used.add(it.id)
        groups.append(ConsolidationGroup(project=proj, tag=t, sources=remaining))
    return groups


def consolidate(
    store: ItemsStore,
    *,
    min_group: int = 3,
    min_confidence: float = 0.6,
    project: Optional[str] = None,
    tag: Optional[str] = None,
    apply: bool = False,
    now: Optional[datetime] = None,
    summarizer: Optional[Summarizer] = None,
) -> ConsolidationReport:
    """Scan ``store`` for consolidation groups; write L1 items only when ``apply``."""
    items = list(store.iter_all())
    groups = find_consolidation_groups(
        items,
        min_group=min_group,
        min_confidence=min_confidence,
        project=project,
        tag=tag,
    )
    report = ConsolidationReport(scanned=len(items), groups=groups, applied=apply)
    if not apply:
        return report

    for group in groups:
        item, body = build_consolidated_item(group, now=now, summarizer=summarizer)
        store.write(item, body)
        report.created.append(item)
    return report
