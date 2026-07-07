"""Archive proposal analysis for the evolve engine."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from agent_brain.memory.governance.evolve.engine import EvolveAction, EvolveProposal
from agent_brain.memory.governance.evolve.proposal_previews import generate_archive_preview
from agent_brain.contracts.memory_item import MemoryItem, MemoryType


ArchivePreviewFactory = Callable[[MemoryItem], str]


def _created_at_timestamp(item: MemoryItem) -> float:
    return item.created_at.timestamp() if isinstance(item.created_at, datetime) else item.created_at


def find_archive_candidates(
    items: list[tuple[MemoryItem, str]],
    *,
    index: Any = None,
    decay_archive_threshold: float = 0.1,
    now: datetime | None = None,
    archive_preview: ArchivePreviewFactory = generate_archive_preview,
) -> list[EvolveProposal]:
    """Find expired signals, very old artifacts, and decay-dead items."""
    now = now or datetime.now(timezone.utc)
    thirty_days_ago_ts = now.timestamp() - (30 * 24 * 60 * 60)
    proposals: list[EvolveProposal] = []
    decay_checked: set[str] = set()

    for item, _body in items:
        item_ts = _created_at_timestamp(item)
        if item.type == MemoryType.signal and item_ts < thirty_days_ago_ts:
            days_old = int((now.timestamp() - item_ts) / (24 * 60 * 60))
            proposals.append(EvolveProposal(
                action=EvolveAction.ARCHIVE,
                item_ids=[item.id],
                title=f"Archive expired signal '{item.title}'",
                description=f"Move this signal to archive as it's {days_old} days old",
                rationale=f"Signal is {days_old} days old (>30 days threshold). Archived signals remain accessible but don't clutter active memory.",
                confidence=0.85,
                output_preview=archive_preview(item),
            ))
            decay_checked.add(item.id)

    if index is not None:
        from agent_brain.memory.recall.retrieval import retention_factor

        all_ids = [item.id for item, _ in items if item.id not in decay_checked]
        if all_ids:
            conf_data = index.get_confidence_data(all_ids)
            for item, _body in items:
                if item.id in decay_checked:
                    continue
                data = conf_data.get(item.id)
                if data is None:
                    continue
                confidence, decay_cls, last_acc_iso = data
                if last_acc_iso:
                    try:
                        last_acc = datetime.fromisoformat(last_acc_iso)
                        if last_acc.tzinfo is None:
                            last_acc = last_acc.replace(tzinfo=timezone.utc)
                        days = (now - last_acc).total_seconds() / 86400
                    except (ValueError, TypeError):
                        days = 0.0
                else:
                    days = 0.0
                rf = retention_factor(decay_cls, days)
                effective = confidence * rf
                if effective < decay_archive_threshold:
                    proposals.append(EvolveProposal(
                        action=EvolveAction.ARCHIVE,
                        item_ids=[item.id],
                        title=f"Archive decayed item '{item.title}'",
                        description=f"Effective score {effective:.3f} below threshold {decay_archive_threshold}",
                        rationale=f"confidence={confidence:.2f} × retention={rf:.3f} = {effective:.3f}. Item has decayed below usefulness threshold.",
                        confidence=0.8,
                        output_preview=archive_preview(item),
                    ))

    return proposals


__all__ = ["find_archive_candidates"]
