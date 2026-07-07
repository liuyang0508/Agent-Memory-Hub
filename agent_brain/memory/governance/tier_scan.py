from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.governance.tiering import Tier, TierThresholds, tier_for_item
from agent_brain.contracts.memory_item import MemoryItem


def scan_tiers(
    items_dir: Path,
    *,
    now: Optional[datetime] = None,
    thresholds: Optional[TierThresholds] = None,
) -> Iterator[tuple[MemoryItem, Tier]]:
    """Yield (item, tier) for every md file under ``items_dir`` recursively."""
    now = now or datetime.now(timezone.utc)
    for md_path in sorted(items_dir.rglob("*.md")):
        try:
            item, _body = ItemsStore._read_one(md_path)
        except Exception:
            continue
        archived = "archived" in md_path.relative_to(items_dir).parts
        yield item, tier_for_item(item, now, archived=archived, thresholds=thresholds)


def tier_distribution(tiers: Iterable[Tier]) -> dict[Tier, int]:
    counts = Counter(tiers)
    return {tier: counts.get(tier, 0) for tier in Tier}
