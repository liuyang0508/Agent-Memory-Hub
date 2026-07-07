"""Review queue helpers for unverified memory candidates."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.contracts.memory_item import MemoryItem

ACTIVE_REVIEW_TAGS = {"needs-review", "requires-review", "unverified-boundary"}
TERMINAL_REVIEW_TAGS = {"review-approved", "review-rejected"}
APPROVED_TAG = "review-approved"
REJECTED_TAG = "review-rejected"


@dataclass(frozen=True)
class ReviewCandidate:
    id: str
    type: str
    title: str
    summary: str
    tags: tuple[str, ...]
    confidence: float
    created_at: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["tags"] = list(self.tags)
        return data


@dataclass(frozen=True)
class ReviewQueueReport:
    candidates: tuple[ReviewCandidate, ...]

    @property
    def total(self) -> int:
        return len(self.candidates)

    def to_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "items": [candidate.to_dict() for candidate in self.candidates],
        }


def list_review_candidates(store: ItemsStore) -> ReviewQueueReport:
    candidates = [
        _candidate(item)
        for item, _body in store.iter_all()
        if _is_active_review_candidate(item)
    ]
    candidates.sort(key=lambda candidate: (candidate.created_at, candidate.id))
    return ReviewQueueReport(candidates=tuple(candidates))


def approve_review_candidate(
    store: ItemsStore,
    item_id: str,
    *,
    confidence: float = 0.7,
) -> MemoryItem:
    item, _body = store.get(item_id)
    tags = _without_review_tags(item.tags)
    tags = sorted({*tags, APPROVED_TAG})
    return store.update_frontmatter(
        item_id,
        tags=tags,
        confidence=_clamp(confidence),
    )


def reject_review_candidate(
    store: ItemsStore,
    item_id: str,
    *,
    confidence: float = 0.1,
) -> MemoryItem:
    item, _body = store.get(item_id)
    tags = _without_review_tags(item.tags)
    tags = sorted({*tags, REJECTED_TAG})
    return store.update_frontmatter(
        item_id,
        tags=tags,
        confidence=_clamp(confidence),
        contradict_count=item.contradict_count + 1,
        gain_score=min(item.gain_score, -0.2),
    )


def _candidate(item: MemoryItem) -> ReviewCandidate:
    return ReviewCandidate(
        id=item.id,
        type=str(item.type),
        title=item.title,
        summary=item.summary,
        tags=tuple(item.tags),
        confidence=item.confidence,
        created_at=item.created_at.isoformat(),
    )


def _is_active_review_candidate(item: MemoryItem) -> bool:
    tags = {tag.lower() for tag in item.tags}
    return bool(tags & ACTIVE_REVIEW_TAGS) and not bool(tags & TERMINAL_REVIEW_TAGS)


def _without_review_tags(tags: list[str]) -> set[str]:
    blocked = ACTIVE_REVIEW_TAGS | TERMINAL_REVIEW_TAGS
    return {tag for tag in tags if tag.lower() not in blocked}


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


__all__ = [
    "ACTIVE_REVIEW_TAGS",
    "APPROVED_TAG",
    "REJECTED_TAG",
    "ReviewCandidate",
    "ReviewQueueReport",
    "approve_review_candidate",
    "list_review_candidates",
    "reject_review_candidate",
]
