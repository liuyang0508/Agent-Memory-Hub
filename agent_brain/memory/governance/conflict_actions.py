"""Resolution action implementations for conflict auto-resolution."""

from __future__ import annotations

from datetime import datetime, timezone

from agent_brain.memory.store.items_store import ItemsStore, make_item_id
from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs


def resolve_keep_newer(
    store: ItemsStore,
    item_a: MemoryItem,
    item_b: MemoryItem,
    dry_run: bool,
) -> tuple[str, str]:
    """Supersede the older item with the newer one."""
    if item_a.created_at >= item_b.created_at:
        winner, loser = item_a, item_b
    else:
        winner, loser = item_b, item_a

    if not dry_run:
        store.update_frontmatter(loser.id, superseded_by=winner.id)
    return winner.id, loser.id


def resolve_keep_higher_confidence(
    store: ItemsStore,
    item_a: MemoryItem,
    item_b: MemoryItem,
    dry_run: bool,
) -> tuple[str, str]:
    """Supersede the lower-confidence item."""
    if item_a.confidence >= item_b.confidence:
        winner, loser = item_a, item_b
    else:
        winner, loser = item_b, item_a

    if not dry_run:
        store.update_frontmatter(loser.id, superseded_by=winner.id)
    return winner.id, loser.id


def resolve_mark_contested(
    store: ItemsStore,
    item_a: MemoryItem,
    item_b: MemoryItem,
    dry_run: bool,
) -> None:
    """Mark both items as contested: lower confidence and add the contested tag."""
    if dry_run:
        return

    for item in (item_a, item_b):
        tags = list(item.tags)
        if "contested" not in tags:
            tags.append("contested")
        new_conf = max(0.3, item.confidence - 0.15)
        store.update_frontmatter(item.id, tags=tags, confidence=new_conf)


def resolve_merge(
    store: ItemsStore,
    item_a: MemoryItem,
    item_b: MemoryItem,
    body_a: str,
    body_b: str,
    dry_run: bool,
) -> str | None:
    """Create a resolution item that references both conflicting items."""
    if dry_run:
        return None

    now = datetime.now(timezone.utc)
    title = f"[resolution] {item_a.title} vs {item_b.title}"
    if len(title) > 200:
        title = title[:197] + "..."

    body = (
        f"**冲突来源**\n\n"
        f"- A: {item_a.title} (id={item_a.id}, confidence={item_a.confidence})\n"
        f"- B: {item_b.title} (id={item_b.id}, confidence={item_b.confidence})\n\n"
        f"**A 的观点**\n{body_a[:500]}\n\n"
        f"**B 的观点**\n{body_b[:500]}\n\n"
        f"**待定**: 需要人工审阅确认哪个决策为准。"
    )

    resolution_item = MemoryItem(
        id=make_item_id(title, when=now),
        type=MemoryType.decision,
        created_at=now,
        project=item_a.project or item_b.project,
        tags=sorted(set(item_a.tags + item_b.tags + ["conflict-resolution"])),
        title=title,
        summary=f"Unresolved conflict between {item_a.id} and {item_b.id}",
        refs=Refs(mems=[item_a.id, item_b.id]),
        confidence=0.5,
    )
    store.write(resolution_item, body)

    for item in (item_a, item_b):
        store.update_frontmatter(item.id, superseded_by=resolution_item.id)

    return resolution_item.id


__all__ = [
    "resolve_keep_higher_confidence",
    "resolve_keep_newer",
    "resolve_mark_contested",
    "resolve_merge",
]
