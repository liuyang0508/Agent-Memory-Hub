"""Pure operational-truth model for the derived memory index."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Collection, Literal

from agent_brain.memory.store.pending import DirtyIndexMarker

IndexHealthStatus = Literal["clean", "repair_required", "corrupt", "unavailable"]
IndexHealthReason = Literal[
    "SOURCE_SCAN_UNTRUSTED",
    "DIRTY_MARKER_CORRUPT",
    "DIRTY_MARKER_UNAVAILABLE",
    "INDEX_PROJECTION_UNAVAILABLE",
    "INDEX_PROJECTION_NOT_AVAILABLE",
    "INDEX_REPAIR_REQUIRED",
]
IndexProjectionStatus = Literal["available", "not_available", "unavailable"]


@dataclass(frozen=True)
class IndexHealthReport:
    """Complete internal health evidence with a low-sensitivity public summary."""

    status: IndexHealthStatus
    reason: IndexHealthReason | None
    source_scan_trusted: bool
    md_count: int
    index_count: int
    missing_ids: frozenset[str]
    orphan_ids: frozenset[str]
    dirty_status: str
    dirty_entries: tuple[str, ...]
    dirty_entry_count: int
    dirty_unique_count: int
    active_dirty_ids: frozenset[str]
    orphan_dirty_ids: frozenset[str]
    retired_dirty_ids: frozenset[str]
    duplicate_dirty_entries: int
    graph_status: IndexProjectionStatus
    expected_supersedes: frozenset[tuple[str, str]]
    indexed_supersedes: frozenset[tuple[str, str]]
    frontmatter_only_edges: frozenset[tuple[str, str]]
    graph_only_edges: frozenset[tuple[str, str]]

    @property
    def repair_required(self) -> bool:
        return self.status == "repair_required"

    def to_summary_dict(self) -> dict[str, object]:
        """Return counts and stable reasons without IDs, paths, or memory content."""

        return {
            "schema_version": 1,
            "status": self.status,
            "reason": self.reason,
            "repair_required": self.repair_required,
            "source_scan_trusted": self.source_scan_trusted,
            "items": {
                "md": self.md_count,
                "index": self.index_count,
                "missing": len(self.missing_ids),
                "orphan": len(self.orphan_ids),
            },
            "dirty_marker": {
                "status": self.dirty_status,
                "entries": self.dirty_entry_count,
                "unique": self.dirty_unique_count,
                "active": len(self.active_dirty_ids),
                "orphan": len(self.orphan_dirty_ids),
                "retired": len(self.retired_dirty_ids),
                "duplicates": self.duplicate_dirty_entries,
            },
            "supersession": {
                "status": self.graph_status,
                "expected": len(self.expected_supersedes),
                "indexed": len(self.indexed_supersedes),
                "frontmatter_only": len(self.frontmatter_only_edges),
                "graph_only": len(self.graph_only_edges),
            },
        }


def build_index_health(
    *,
    md_ids: Collection[str],
    index_ids: Collection[str],
    expected_supersedes: Collection[tuple[str, str]],
    indexed_supersedes: Collection[tuple[str, str]],
    source_scan_trusted: bool,
    graph_status: IndexProjectionStatus,
    dirty_marker: DirtyIndexMarker,
) -> IndexHealthReport:
    """Classify source, projection, marker, and supersession drift deterministically."""

    md = frozenset(md_ids)
    indexed = frozenset(index_ids)
    expected_edges = frozenset(expected_supersedes)
    indexed_edges = frozenset(indexed_supersedes)
    dirty_ids = dirty_marker.item_ids
    dirty_entries = dirty_marker.entries

    missing_ids = md - indexed
    orphan_ids = indexed - md
    active_dirty_ids = dirty_ids & md
    orphan_dirty_ids = dirty_ids & (indexed - md)
    retired_dirty_ids = dirty_ids - (md | indexed)
    frontmatter_only_edges = expected_edges - indexed_edges
    graph_only_edges = indexed_edges - expected_edges

    status: IndexHealthStatus
    reason: IndexHealthReason | None
    if not source_scan_trusted:
        status, reason = "unavailable", "SOURCE_SCAN_UNTRUSTED"
    elif graph_status == "unavailable":
        status, reason = "unavailable", "INDEX_PROJECTION_UNAVAILABLE"
    elif graph_status == "not_available":
        status, reason = "unavailable", "INDEX_PROJECTION_NOT_AVAILABLE"
    elif dirty_marker.status == "unavailable":
        status, reason = "unavailable", "DIRTY_MARKER_UNAVAILABLE"
    elif dirty_marker.status == "corrupt":
        status, reason = "corrupt", "DIRTY_MARKER_CORRUPT"
    elif (
        missing_ids
        or orphan_ids
        or dirty_marker.status == "repair_required"
        or frontmatter_only_edges
        or graph_only_edges
    ):
        status, reason = "repair_required", "INDEX_REPAIR_REQUIRED"
    else:
        status, reason = "clean", None

    return IndexHealthReport(
        status=status,
        reason=reason,
        source_scan_trusted=source_scan_trusted,
        md_count=len(md),
        index_count=len(indexed),
        missing_ids=missing_ids,
        orphan_ids=orphan_ids,
        dirty_status=dirty_marker.status,
        dirty_entries=dirty_entries,
        dirty_entry_count=len(dirty_entries),
        dirty_unique_count=len(dirty_ids),
        active_dirty_ids=active_dirty_ids,
        orphan_dirty_ids=orphan_dirty_ids,
        retired_dirty_ids=retired_dirty_ids,
        duplicate_dirty_entries=max(0, len(dirty_entries) - len(dirty_ids)),
        graph_status=graph_status,
        expected_supersedes=expected_edges,
        indexed_supersedes=indexed_edges,
        frontmatter_only_edges=frontmatter_only_edges,
        graph_only_edges=graph_only_edges,
    )
