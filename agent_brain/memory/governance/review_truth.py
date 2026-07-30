"""Canonical aggregate truth for review candidates and lifecycle-due items."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.governance.auto_governance import lifecycle_review_due
from agent_brain.memory.governance.lifecycle_ledger import (
    active_lifecycle_deferrals_readonly,
)
from agent_brain.memory.governance.review_queue import (
    list_review_candidates_from_items,
)
from agent_brain.memory.store.items_store import ItemsStore


REVIEW_TRUTH_SCHEMA_VERSION = "amh-review-truth/v1"
REVIEW_SLA_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class ReviewTruthSnapshot:
    """Content-free review metrics derived from one immutable item snapshot."""

    schema_version: str
    generated_at: str
    status: str
    consistency_status: str
    total_items: int
    active_review_candidate_count: int
    active_review_candidate_oldest_age_seconds: int | None
    active_review_candidate_sla_breach_count: int
    active_review_reason_counts: dict[str, int]
    active_review_type_counts: dict[str, int]
    active_review_contested_count: int
    active_review_contested_outside_low_confidence_count: int
    lifecycle_due_count: int
    lifecycle_due_oldest_age_seconds: int | None
    lifecycle_ledger_unavailable: bool
    item_scan_unavailable: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_review_truth_snapshot(
    items: Iterable[MemoryItem],
    *,
    brain_dir: Path | None = None,
    now: datetime | None = None,
    active_deferrals: set[str] | frozenset[str] | None = None,
    lifecycle_ledger_unavailable: bool | None = None,
    item_scan_unavailable: bool = False,
) -> ReviewTruthSnapshot:
    """Build aggregate truth without returning titles, summaries, IDs, or paths."""

    generated_at = _utc(now)
    item_snapshot = tuple(items)
    review = list_review_candidates_from_items(item_snapshot)

    ledger_unavailable = bool(lifecycle_ledger_unavailable)
    deferrals = frozenset(active_deferrals or ())
    if active_deferrals is None and brain_dir is not None:
        loaded_deferrals, loaded_unavailable = active_lifecycle_deferrals_readonly(
            Path(brain_dir),
            now=generated_at,
        )
        deferrals = frozenset(loaded_deferrals)
        ledger_unavailable = loaded_unavailable

    by_id = {item.id: item for item in item_snapshot}
    contested_review_count = sum(
        "contested" in {tag.casefold() for tag in item.tags}
        for candidate in review.candidates
        if (item := by_id.get(candidate.id)) is not None
    )
    review_ages = [
        age
        for candidate in review.candidates
        if (item := by_id.get(candidate.id)) is not None
        if (age := _age_seconds(generated_at, item.created_at)) is not None
    ]
    review_scan_unavailable = len(review_ages) != review.total

    lifecycle_ages: list[int] = []
    lifecycle_scan_unavailable = False
    for item in item_snapshot:
        if item.id in deferrals:
            continue
        try:
            if not lifecycle_review_due(item, now=generated_at):
                continue
            observed_at = item.validity.observed_at or item.created_at
            age = _age_seconds(generated_at, observed_at)
            if age is None:
                lifecycle_scan_unavailable = True
            else:
                lifecycle_ages.append(age)
        except (OverflowError, TypeError, ValueError):
            lifecycle_scan_unavailable = True

    reason_counts = Counter(candidate.review_reason for candidate in review.candidates)
    type_counts = Counter(candidate.type for candidate in review.candidates)
    scan_unavailable = (
        item_scan_unavailable
        or review_scan_unavailable
        or lifecycle_scan_unavailable
    )
    internally_consistent = (
        sum(reason_counts.values()) == review.total
        and sum(type_counts.values()) == review.total
    )
    consistency_status = (
        "unavailable"
        if scan_unavailable or ledger_unavailable
        else ("consistent" if internally_consistent else "inconsistent")
    )
    if consistency_status in {"unavailable", "inconsistent"}:
        status = "fail"
    elif review.total or lifecycle_ages:
        status = "warn"
    else:
        status = "pass"

    return ReviewTruthSnapshot(
        schema_version=REVIEW_TRUTH_SCHEMA_VERSION,
        generated_at=generated_at.isoformat(),
        status=status,
        consistency_status=consistency_status,
        total_items=len(item_snapshot),
        active_review_candidate_count=review.total,
        active_review_candidate_oldest_age_seconds=(
            max(review_ages) if review_ages else None
        ),
        active_review_candidate_sla_breach_count=sum(
            age > REVIEW_SLA_SECONDS for age in review_ages
        ),
        active_review_reason_counts=dict(sorted(reason_counts.items())),
        active_review_type_counts=dict(sorted(type_counts.items())),
        active_review_contested_count=contested_review_count,
        active_review_contested_outside_low_confidence_count=max(
            0,
            contested_review_count - reason_counts.get("contested", 0),
        ),
        lifecycle_due_count=len(lifecycle_ages),
        lifecycle_due_oldest_age_seconds=(
            max(lifecycle_ages) if lifecycle_ages else None
        ),
        lifecycle_ledger_unavailable=ledger_unavailable,
        item_scan_unavailable=scan_unavailable,
    )


def build_review_truth_from_brain(
    brain_dir: Path,
    *,
    now: datetime | None = None,
) -> ReviewTruthSnapshot:
    """Read one item snapshot and expose only canonical aggregate review truth."""

    root = Path(brain_dir)
    items = tuple(item for item, _body in ItemsStore(root / "items").iter_all())
    return build_review_truth_snapshot(items, brain_dir=root, now=now)


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _age_seconds(now: datetime, observed_at: datetime) -> int | None:
    try:
        current = observed_at
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return max(0, int((now - current.astimezone(timezone.utc)).total_seconds()))
    except (OverflowError, TypeError, ValueError):
        return None


__all__ = [
    "REVIEW_SLA_SECONDS",
    "REVIEW_TRUTH_SCHEMA_VERSION",
    "ReviewTruthSnapshot",
    "build_review_truth_from_brain",
    "build_review_truth_snapshot",
]
