"""Apply controlled summary rewrite candidates."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from agent_brain.memory.governance.summary_rewrite import preview_summary_rewrite
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.history import BrainHistory

ROLLBACK_MARKER = ".summary-rewrite-rollback"


@dataclass(frozen=True)
class SummaryRewriteChange:
    """One summary rewrite candidate or applied change."""

    item_id: str
    title: str
    current_summary: str
    current_length: int
    candidate_summary: str
    candidate_length: int
    target_length: int
    strategy: str
    applied: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SummaryRewriteApplyResult:
    """Summary rewrite apply/dry-run result."""

    scanned_items: int
    candidate_count: int
    returned_count: int
    applied_count: int
    dry_run: bool
    target_length: int
    snapshot_sha: str | None = None
    marker_path: str | None = None
    changes: list[SummaryRewriteChange] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "scanned_items": self.scanned_items,
            "candidate_count": self.candidate_count,
            "returned_count": self.returned_count,
            "applied_count": self.applied_count,
            "dry_run": self.dry_run,
            "target_length": self.target_length,
            "snapshot_sha": self.snapshot_sha,
            "marker_path": self.marker_path,
            "changes": [change.to_dict() for change in self.changes],
        }


def apply_summary_rewrites(
    *,
    brain_dir: Path,
    items_store: ItemsStore,
    limit: int = 10,
    target_length: int = 200,
    dry_run: bool = False,
    snapshot: bool = True,
) -> SummaryRewriteApplyResult:
    """Preview or apply deterministic summary rewrite candidates."""
    brain = Path(brain_dir)
    items = list(items_store.iter_all())
    candidates = [
        _candidate(item, target_length=target_length)
        for item, _body in items
        if len(item.summary) > target_length
    ]
    visible = candidates[:max(0, limit)]

    snapshot_sha: str | None = None
    marker_path: str | None = None
    if visible and not dry_run and snapshot:
        history = BrainHistory(brain)
        snapshot_sha = history.snapshot(
            f"pre-summary-rewrite snapshot ({len(visible)} item(s))"
        )
        if snapshot_sha is None:
            log = history.log(limit=1)
            snapshot_sha = log[0]["sha"] if log else None
        if snapshot_sha:
            marker = brain / ROLLBACK_MARKER
            marker.write_text(snapshot_sha, encoding="utf-8")
            marker_path = str(marker)

    applied_changes: list[SummaryRewriteChange] = []
    for change in visible:
        if dry_run:
            applied_changes.append(change)
            continue
        items_store.update_frontmatter(change.item_id, summary=change.candidate_summary)
        applied_changes.append(_with_applied(change))

    return SummaryRewriteApplyResult(
        scanned_items=len(items),
        candidate_count=len(candidates),
        returned_count=len(visible),
        applied_count=0 if dry_run else len(applied_changes),
        dry_run=dry_run,
        target_length=target_length,
        snapshot_sha=snapshot_sha,
        marker_path=marker_path,
        changes=applied_changes,
    )


def rollback_summary_rewrites(*, brain_dir: Path) -> str:
    """Restore the brain pool to the last summary rewrite rollback marker."""
    brain = Path(brain_dir)
    marker = brain / ROLLBACK_MARKER
    if not marker.exists():
        raise FileNotFoundError(f"no summary rewrite rollback marker: {marker}")
    sha = marker.read_text(encoding="utf-8").strip()
    BrainHistory(brain).restore(sha)
    return sha


def _candidate(item, *, target_length: int) -> SummaryRewriteChange:
    preview = preview_summary_rewrite(item.summary, target_length=target_length)
    return SummaryRewriteChange(
        item_id=item.id,
        title=item.title,
        current_summary=preview.current_summary,
        current_length=preview.current_length,
        candidate_summary=preview.candidate_summary,
        candidate_length=preview.candidate_length,
        target_length=preview.target_length,
        strategy=preview.strategy,
        applied=False,
    )


def _with_applied(change: SummaryRewriteChange) -> SummaryRewriteChange:
    return SummaryRewriteChange(
        item_id=change.item_id,
        title=change.title,
        current_summary=change.current_summary,
        current_length=change.current_length,
        candidate_summary=change.candidate_summary,
        candidate_length=change.candidate_length,
        target_length=change.target_length,
        strategy=change.strategy,
        applied=True,
    )


__all__ = [
    "ROLLBACK_MARKER",
    "SummaryRewriteApplyResult",
    "SummaryRewriteChange",
    "apply_summary_rewrites",
    "rollback_summary_rewrites",
]
