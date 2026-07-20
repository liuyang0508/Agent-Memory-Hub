"""Operational truth for the derived memory index."""

from __future__ import annotations

import json

from agent_brain.memory.governance.index_health import build_index_health
from agent_brain.memory.store.pending import DirtyIndexMarker


def test_index_health_classifies_marker_and_graph_drift() -> None:
    report = build_index_health(
        md_ids={"mem-20260720-140000-active", "mem-20260720-140000-target"},
        index_ids={"mem-20260720-140000-active", "mem-20260720-140000-orphan"},
        expected_supersedes={
            ("mem-20260720-140000-active", "mem-20260720-140000-target")
        },
        indexed_supersedes={
            ("mem-20260720-140000-orphan", "mem-20260720-140000-active")
        },
        source_scan_trusted=True,
        graph_status="available",
        dirty_marker=DirtyIndexMarker(
            "repair_required",
            frozenset(
                {
                    "mem-20260720-140000-active",
                    "mem-20260720-140000-orphan",
                    "mem-20260720-140000-retired",
                }
            ),
            (
                "mem-20260720-140000-active",
                "mem-20260720-140000-active",
                "mem-20260720-140000-orphan",
                "mem-20260720-140000-retired",
            ),
        ),
    )

    assert report.status == "repair_required"
    assert report.reason == "INDEX_REPAIR_REQUIRED"
    assert report.missing_ids == frozenset({"mem-20260720-140000-target"})
    assert report.orphan_ids == frozenset({"mem-20260720-140000-orphan"})
    assert report.active_dirty_ids == frozenset({"mem-20260720-140000-active"})
    assert report.orphan_dirty_ids == frozenset({"mem-20260720-140000-orphan"})
    assert report.retired_dirty_ids == frozenset({"mem-20260720-140000-retired"})
    assert report.duplicate_dirty_entries == 1
    assert len(report.frontmatter_only_edges) == 1
    assert len(report.graph_only_edges) == 1


def test_index_health_summary_is_low_sensitivity() -> None:
    report = build_index_health(
        md_ids={"mem-20260720-140001-secret-source"},
        index_ids=set(),
        expected_supersedes=set(),
        indexed_supersedes=set(),
        source_scan_trusted=True,
        graph_status="available",
        dirty_marker=DirtyIndexMarker("clean"),
    )

    summary = report.to_summary_dict()
    rendered = json.dumps(summary, sort_keys=True)
    assert "mem-20260720-140001-secret-source" not in rendered
    assert set(summary) == {
        "schema_version",
        "status",
        "reason",
        "repair_required",
        "source_scan_trusted",
        "items",
        "dirty_marker",
        "supersession",
    }


def test_index_health_failure_precedence_is_closed_set() -> None:
    marker = DirtyIndexMarker("clean")
    source_untrusted = build_index_health(
        md_ids=set(),
        index_ids=set(),
        expected_supersedes=set(),
        indexed_supersedes=set(),
        source_scan_trusted=False,
        graph_status="available",
        dirty_marker=marker,
    )
    graph_unavailable = build_index_health(
        md_ids=set(),
        index_ids=set(),
        expected_supersedes=set(),
        indexed_supersedes=set(),
        source_scan_trusted=True,
        graph_status="unavailable",
        dirty_marker=marker,
    )
    marker_corrupt = build_index_health(
        md_ids=set(),
        index_ids=set(),
        expected_supersedes=set(),
        indexed_supersedes=set(),
        source_scan_trusted=True,
        graph_status="available",
        dirty_marker=DirtyIndexMarker("corrupt"),
    )

    assert (source_untrusted.status, source_untrusted.reason) == (
        "unavailable",
        "SOURCE_SCAN_UNTRUSTED",
    )
    assert (graph_unavailable.status, graph_unavailable.reason) == (
        "unavailable",
        "INDEX_PROJECTION_UNAVAILABLE",
    )
    assert (marker_corrupt.status, marker_corrupt.reason) == (
        "corrupt",
        "DIRTY_MARKER_CORRUPT",
    )
