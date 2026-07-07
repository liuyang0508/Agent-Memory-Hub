"""Build L1 consolidated memory items from consolidation groups."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Optional

from agent_brain.memory.governance.consolidation_types import ConsolidationGroup, ItemBody, Summarizer
from agent_brain.contracts.memory_item import (
    AbstractionLayer,
    MemoryItem,
    MemoryType,
    Refs,
    Sensitivity,
)


_SENSITIVITY_ORDER = [
    Sensitivity.public,
    Sensitivity.internal,
    Sensitivity.private,
    Sensitivity.secret,
]

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(tag: str) -> str:
    s = _SLUG_RE.sub("-", tag.lower()).strip("-")
    return s or "tag"


def _most_restrictive(sources: list[ItemBody]) -> Sensitivity:
    worst = Sensitivity.public
    for item, _ in sources:
        sv = Sensitivity(item.sensitivity)
        if _SENSITIVITY_ORDER.index(sv) > _SENSITIVITY_ORDER.index(worst):
            worst = sv
    return worst


def _template_body(group: ConsolidationGroup) -> str:
    lines = [
        f"本条由 {len(group.sources)} 条 L0 raw fact 自动聚合"
        f"（project={group.project}, tag={group.tag}）。",
        "",
        "源 items：",
    ]
    for item, _ in group.sources:
        lines.append(f"- {item.title} ({item.id}, confidence={item.confidence})")
        lines.append(f"  {item.summary}")
    return "\n".join(lines)


def _provenance_footer(group: ConsolidationGroup) -> str:
    ids = ", ".join(group.source_ids)
    return (
        "\n\n> abstraction=L1 consolidated · 源 item 未删除，仍为 source of truth。\n"
        f"> 源 IDs: {ids}"
    )


def build_consolidated_item(
    group: ConsolidationGroup,
    *,
    now: Optional[datetime] = None,
    summarizer: Optional[Summarizer] = None,
) -> ItemBody:
    """Build (but do not persist) the L1 fact that summarises ``group``."""
    now = now or datetime.now(timezone.utc)
    confidences = [item.confidence for item, _ in group.sources]
    mean_conf = round(sum(confidences) / len(confidences), 4)

    common_tags = set.intersection(
        *[set(item.tags) for item, _ in group.sources]
    ) if group.sources else set()
    tags = sorted({group.tag, "consolidated"} | common_tags)

    short = hashlib.sha256("\n".join(sorted(group.source_ids)).encode()).hexdigest()[:8]
    item_id = f"mem-{now.strftime('%Y%m%d-%H%M%S')}-consolidated-{_slug(group.tag)}-{short}"

    if summarizer is not None:
        body = summarizer(group) + _provenance_footer(group)
    else:
        body = _template_body(group)

    item = MemoryItem(
        id=item_id,
        type=MemoryType.fact,
        created_at=now,
        agent="consolidation-engine",
        session=None,
        project=group.project,
        tags=tags,
        sensitivity=_most_restrictive(group.sources),
        title=f"[L1] {group.tag}: {len(group.sources)} 条 raw fact 聚合",
        summary=f"{len(group.sources)} 条 L0 fact 聚合（project={group.project}, tag={group.tag}）",
        refs=Refs(mems=sorted(group.source_ids)),
        confidence=mean_conf,
        abstraction=AbstractionLayer.L1,
    )
    return item, body


__all__ = ["build_consolidated_item"]
