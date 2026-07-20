"""Read-only readiness report for the next governance pass."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
from typing import Any

from agent_brain.contracts.memory_enums import memory_enum_value
from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.context.query_signal import diagnose_injection_query
from agent_brain.memory.governance.auto_governance import lifecycle_review_due
from agent_brain.memory.governance.lifecycle_ledger import active_lifecycle_deferrals
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.store.pending import (
    MAX_PENDING_QUEUE_ENTRIES,
    PendingQueue,
)


Status = str


@dataclass(frozen=True)
class ReadinessCheck:
    id: str
    status: Status
    title: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReadinessLane:
    id: str
    title: str
    status: Status
    metrics: dict[str, Any]
    checks: list[ReadinessCheck]
    next_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "metrics": self.metrics,
            "checks": [check.to_dict() for check in self.checks],
            "next_actions": self.next_actions,
        }


@dataclass(frozen=True)
class GovernanceReadinessReport:
    generated_at: str
    overall_status: Status
    lanes: list[ReadinessLane]
    next_actions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "overall_status": self.overall_status,
            "lanes": [lane.to_dict() for lane in self.lanes],
            "next_actions": self.next_actions,
        }


QUERY_SIGNAL_AUDIT_CASES_PATH = Path(__file__).with_name("query_signal_adversarial_cases.json")


def load_query_signal_audit_cases() -> tuple[dict[str, Any], ...]:
    """Load adversarial query-signal readiness cases shipped with the package."""
    try:
        raw = json.loads(QUERY_SIGNAL_AUDIT_CASES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot load query signal audit cases: {exc}") from exc
    if not isinstance(raw, list):
        raise RuntimeError("query signal audit cases must be a JSON list")
    cases: list[dict[str, Any]] = []
    for index, case in enumerate(raw):
        if not isinstance(case, dict):
            raise RuntimeError(f"query signal audit case #{index} must be an object")
        cases.append(case)
    return tuple(cases)


def build_governance_readiness_report(
    brain_dir: Path,
    *,
    repo_root: Path,
) -> GovernanceReadinessReport:
    lanes = [
        _release_lane(repo_root),
        _query_signal_lane(brain_dir),
        _memory_lifecycle_lane(brain_dir),
    ]
    next_actions = _unique(
        action for lane in lanes for action in lane.next_actions
    )
    return GovernanceReadinessReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        overall_status=_worst_status(lane.status for lane in lanes),
        lanes=lanes,
        next_actions=next_actions,
    )


def render_governance_readiness_markdown(report: GovernanceReadinessReport) -> str:
    lines = [
        "# Governance Readiness",
        "",
        f"**Overall status**: `{report.overall_status}`",
        f"**Generated at**: `{report.generated_at}`",
        "",
    ]
    for lane in report.lanes:
        lines.extend([
            f"## {lane.title}",
            "",
            f"**Status**: `{lane.status}`",
            "",
            "| Check | Status | Detail |",
            "|---|---|---|",
        ])
        for check in lane.checks:
            lines.append(f"| {check.title} | `{check.status}` | {check.detail} |")
        if lane.metrics:
            lines.extend(["", "**Metrics**:", ""])
            for key, value in lane.metrics.items():
                lines.append(f"- `{key}`: {value}")
        if lane.next_actions:
            lines.extend(["", "**Next actions**:", ""])
            for action in lane.next_actions:
                lines.append(f"- `{action}`")
        lines.append("")
    if report.next_actions:
        lines.extend(["## Suggested Command Queue", ""])
        for action in report.next_actions:
            lines.append(f"- `{action}`")
    return "\n".join(lines).rstrip() + "\n"


def _release_lane(repo_root: Path) -> ReadinessLane:
    local_files = {
        "install_sh": repo_root / "install.sh",
        "install_ps1": repo_root / "install.ps1",
        "homebrew_cask": repo_root / "Casks" / "agent-memory-hub.rb",
        "npm_package_json": repo_root / "packaging" / "npm" / "package.json",
        "release_publishing_doc": repo_root / "docs" / "release-publishing.md",
    }
    checks = [
        _file_check("install_sh", "install.sh asset", local_files["install_sh"]),
        _file_check("install_ps1", "install.ps1 asset", local_files["install_ps1"]),
        _file_check("homebrew_cask", "Homebrew cask", local_files["homebrew_cask"]),
        _file_check(
            "npm_package_json",
            "npm package metadata",
            local_files["npm_package_json"],
            missing_status="warn",
        ),
        _file_check(
            "release_publishing_doc",
            "release publishing doc",
            local_files["release_publishing_doc"],
        ),
        _public_hygiene_check(repo_root),
    ]
    next_actions = []
    if not local_files["npm_package_json"].exists():
        next_actions.append("prepare packaging/npm/package.json before npm publish")
    if any(check.status == "fail" for check in checks):
        next_actions.append("fix release readiness failures before tagging a release")
    return ReadinessLane(
        id="release",
        title="发布可用性",
        status=_worst_status(check.status for check in checks),
        metrics={
            "checked_files": len(local_files),
            "missing_or_warn_files": sum(
                1 for path in local_files.values() if not path.exists()
            ),
        },
        checks=checks,
        next_actions=next_actions,
    )


def _query_signal_lane(brain_dir: Path) -> ReadinessLane:
    cases = load_query_signal_audit_cases()
    checks: list[ReadinessCheck] = []
    under_extracted = 0
    injectable = 0
    blocked = 0
    category_counts: dict[str, int] = {}
    for case in cases:
        category = str(case.get("category") or "uncategorized")
        category_counts[category] = category_counts.get(category, 0) + 1
        # Readiness is a probe, not a cache builder. The adversarial manifest is
        # intentionally evaluated against the deterministic static extractor so
        # running this report never creates the query-signal metadata cache.
        diagnostic = diagnose_injection_query(case["prompt"], brain_dir=None)
        terms = tuple(str(term) for term in diagnostic.terms)
        lower_terms = tuple(term.lower() for term in terms)
        expected_terms = tuple(str(term).lower() for term in case.get("expected_terms", ()))
        missing_terms = [
            term for term in expected_terms
            if not any(term in candidate or candidate in term for candidate in lower_terms)
        ]
        if diagnostic.injectable:
            injectable += 1
        else:
            blocked += 1
        expected_injectable = bool(case["expected_injectable"])
        expected_reason = case.get("expected_reason")
        reason_mismatch = (
            expected_reason is not None
            and str(diagnostic.reason) != str(expected_reason)
        )
        is_under_extracted = (
            diagnostic.injectable != expected_injectable
            or len(terms) < int(case["min_terms"])
            or bool(missing_terms)
            or reason_mismatch
        )
        if is_under_extracted:
            under_extracted += 1
        status = "warn" if is_under_extracted else "pass"
        checks.append(ReadinessCheck(
            id=str(case["id"]),
            status=status,
            title=str(case["id"]).replace("_", " "),
            detail=(
                f"category={category}, "
                f"decision={diagnostic.decision}, "
                f"terms={list(terms)}, missing={missing_terms}, "
                f"reason={diagnostic.reason}"
            ),
            evidence={
                **diagnostic.to_dict(),
                "category": category,
                "expected_injectable": expected_injectable,
                "expected_reason": expected_reason,
                "reason_mismatch": reason_mismatch,
            },
        ))
    return ReadinessLane(
        id="query_signal",
        title="长任务召回入口",
        status="warn" if under_extracted else "pass",
        metrics={
            "case_count": len(cases),
            "category_counts": dict(sorted(category_counts.items())),
            "injectable_cases": injectable,
            "blocked_cases": blocked,
            "under_extracted_cases": under_extracted,
        },
        checks=checks,
        next_actions=(
            ["add or tune query_signal cases before changing retrieval ranking"]
            if under_extracted
            else []
        ),
    )


_PENDING_CLASSIFICATIONS = (
    "ready",
    "already_written",
    "stale_requires_review",
    "duplicate_candidate",
    "conflict",
    "unsupported_type",
    "malformed",
    "audit_blocked",
)
_PENDING_REVIEW_CLASSIFICATIONS = frozenset({
    "stale_requires_review",
    "duplicate_candidate",
    "unsupported_type",
})
_PENDING_BLOCKER_CLASSIFICATIONS = frozenset({
    "conflict",
    "malformed",
    "audit_blocked",
})


@dataclass(frozen=True)
class _IndexGraphTruth:
    status: str
    edges: frozenset[tuple[str, str]]


@dataclass(frozen=True)
class _IndexComponentState:
    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int


class _UnsafeIndexSnapshot(OSError):
    """The source index cannot be copied into one stable bounded snapshot."""


_INDEX_COMPONENT_LIMITS = {
    "": 512 * 1024 * 1024,
    "-wal": 512 * 1024 * 1024,
    "-shm": 64 * 1024 * 1024,
    "-journal": 512 * 1024 * 1024,
}
_MAX_INDEX_SNAPSHOT_BYTES = 2 * 1024 * 1024 * 1024
_SQLITE_SHARED_FIRST_BYTE = 0x40000002


def build_memory_lifecycle_readiness(brain_dir: Path) -> ReadinessLane:
    """Aggregate lifecycle health without exposing item or queue content."""
    return _memory_lifecycle_lane(brain_dir)


def _memory_lifecycle_lane(brain_dir: Path) -> ReadinessLane:
    brain = Path(brain_dir)
    now = datetime.now(timezone.utc)
    items, archived_count, malformed_item_count, item_scan_unavailable = (
        _read_items_readonly(brain / "items")
    )
    active_ids = {item.id for item in items}
    by_type: dict[str, int] = {}
    stale_items: list[tuple[MemoryItem, int]] = []
    low_confidence_count = 0
    untagged_count = 0
    raw_count = 0
    private_or_secret_count = 0
    for item in items:
        item_type = str(memory_enum_value(item.type))
        by_type[item_type] = by_type.get(item_type, 0) + 1
        try:
            if lifecycle_review_due(item, now=now):
                observed_at = item.validity.observed_at or item.created_at
                age_seconds = _age_seconds(now, observed_at)
                if age_seconds is None:
                    item_scan_unavailable = True
                else:
                    stale_items.append((item, age_seconds))
        except (OverflowError, TypeError, ValueError):
            item_scan_unavailable = True
        if item.confidence < 0.5:
            low_confidence_count += 1
        if not item.tags:
            untagged_count += 1
        if (
            str(memory_enum_value(item.abstraction)) == "L0"
            or str(memory_enum_value(item.maturity)) == "raw"
        ):
            raw_count += 1
        if str(memory_enum_value(item.sensitivity)) in {"private", "secret"}:
            private_or_secret_count += 1

    superseded_items = [item for item in items if item.superseded_by]
    active_count = len(items) - len(superseded_items)
    broken_superseded_count = sum(
        1
        for item in superseded_items
        if item.superseded_by not in active_ids
    )
    deferrals = active_lifecycle_deferrals(brain, now=now)
    review_items = [
        (item, age_seconds)
        for item, age_seconds in stale_items
        if item.id not in deferrals
    ]

    pending_metrics = _pending_truth_readonly(brain)
    graph_truth = _read_supersedes_graph_readonly(brain / "index.db")
    frontmatter_edges = frozenset(
        (str(item.superseded_by), item.id)
        for item in superseded_items
        if item.superseded_by in active_ids
    )
    if graph_truth.status == "available":
        comparable_graph_edges = frozenset(
            (source_id, target_id)
            for source_id, target_id in graph_truth.edges
            if source_id in active_ids and target_id in active_ids
        )
        supersession_drift_count: int | None = len(
            frontmatter_edges.symmetric_difference(comparable_graph_edges)
        )
    else:
        supersession_drift_count = None

    index_dirty_status = _index_dirty_status(brain / ".index-dirty")
    index_repair_required = (
        graph_truth.status != "available"
        or bool(supersession_drift_count)
        or index_dirty_status != "clean"
    )
    metrics = {
        "total_items": len(items),
        "by_type": dict(sorted(by_type.items())),
        "active_count": active_count,
        "stale_count": len(stale_items),
        # Backward-compatible alias retained for existing report consumers.
        "stale_signal_count": len(stale_items),
        "superseded_count": len(superseded_items),
        "archived_count": archived_count,
        "broken_superseded_count": broken_superseded_count,
        "review_queue_count": len(review_items),
        "review_queue_oldest_age_seconds": (
            max(age for _item, age in review_items) if review_items else None
        ),
        "low_confidence_count": low_confidence_count,
        "untagged_count": untagged_count,
        "raw_count": raw_count,
        "private_or_secret_count": private_or_secret_count,
        "malformed_item_count": malformed_item_count,
        "item_scan_unavailable": item_scan_unavailable,
        **pending_metrics,
        "supersession_graph_status": graph_truth.status,
        "supersession_drift_count": supersession_drift_count,
        "index_dirty_status": index_dirty_status,
        "index_repair_required": index_repair_required,
    }
    checks = [
        _threshold_check(
            "review_queue_count",
            "lifecycle review backlog",
            len(review_items),
            "memory govern plan --category lifecycle",
        ),
        _threshold_check(
            "low_confidence_count",
            "low confidence",
            low_confidence_count,
            "memory govern maturity --format table",
        ),
        _threshold_check(
            "untagged_count",
            "untagged items",
            untagged_count,
            "memory govern run --format markdown",
        ),
        _threshold_check(
            "private_or_secret_count",
            "private / secret items",
            private_or_secret_count,
            "memory search --context-firewall",
        ),
        _aggregate_check(
            "broken_superseded_count",
            "broken supersession chains",
            status="fail" if broken_superseded_count else "pass",
            detail=f"{broken_superseded_count} broken chain(s)",
            count=broken_superseded_count,
        ),
        _pending_integrity_check(pending_metrics),
        _pending_age_check(pending_metrics["pending_oldest_age_seconds"]),
        _aggregate_check(
            "item_scan",
            "memory item scan",
            status=(
                "fail"
                if item_scan_unavailable or malformed_item_count
                else "pass"
            ),
            detail=(
                "scan unavailable or malformed item(s) present"
                if item_scan_unavailable or malformed_item_count
                else "source tree scanned"
            ),
            count=malformed_item_count,
        ),
        _supersession_graph_check(
            graph_status=graph_truth.status,
            drift_count=supersession_drift_count,
        ),
        _aggregate_check(
            "index_dirty",
            "index repair marker",
            status=(
                "pass"
                if index_dirty_status == "clean"
                else "fail" if index_dirty_status == "unavailable" else "warn"
            ),
            detail=f"index dirty status={index_dirty_status}",
        ),
    ]
    status = _worst_status(check.status for check in checks)
    next_actions: list[str] = []
    if review_items or broken_superseded_count or supersession_drift_count:
        next_actions.append(
            "memory govern plan --category lifecycle --format markdown"
        )
    if (
        pending_metrics["pending_total"]
        or pending_metrics["pending_dead_count"]
        or pending_metrics["pending_scan_unavailable"]
    ):
        next_actions.append("memory sync-pending --format json")
    if index_repair_required:
        next_actions.append("memory verify")
    return ReadinessLane(
        id="memory_lifecycle",
        title="记忆生命周期",
        status=status,
        metrics=metrics,
        checks=checks,
        next_actions=next_actions,
    )


def _read_items_readonly(
    items_dir: Path,
) -> tuple[list[MemoryItem], int, int, bool]:
    try:
        opened = os.lstat(items_dir)
    except FileNotFoundError:
        return [], 0, 0, False
    except OSError:
        return [], 0, 0, True
    if not os.path.isdir(items_dir) or os.path.islink(items_dir):
        return [], 0, 0, True
    if not opened.st_ino or not opened.st_dev:
        return [], 0, 0, True

    store = ItemsStore(items_dir)
    active_items = [item for item, _body in store.iter_all()]
    active_skipped = store.last_scan.skipped_count
    all_items = [item for item, _body in store.iter_all(include_archived=True)]
    all_skipped = store.last_scan.skipped_count
    return (
        active_items,
        max(0, len(all_items) - len(active_items)),
        max(active_skipped, all_skipped),
        False,
    )


def _pending_truth_readonly(brain_dir: Path) -> dict[str, Any]:
    queue = PendingQueue(brain=brain_dir)
    depth_failed = False
    try:
        depth = queue.depth()
    except (OSError, RuntimeError, ValueError):
        depth = 0
        depth_failed = True
    try:
        preview = queue.preview(limit=min(depth, MAX_PENDING_QUEUE_ENTRIES))
    except (OSError, RuntimeError, ValueError):
        preview = None

    counts = Counter({name: 0 for name in _PENDING_CLASSIFICATIONS})
    if preview is not None:
        counts.update(record.classification for record in preview.records)
    dead_count, dead_scan_unavailable = _count_dead_pending_readonly(
        brain_dir / "pending" / "dead"
    )
    pending_scan_unavailable = (
        depth_failed
        or preview is None
        or bool(preview.scan_unavailable if preview is not None else True)
        or dead_scan_unavailable
    )
    total = preview.total if preview is not None else depth
    returned = preview.returned if preview is not None else 0
    truncated = bool(preview.truncated if preview is not None else total)
    oldest = max(
        (
            record.age_seconds
            for record in (preview.records if preview is not None else [])
            if record.age_seconds is not None
        ),
        default=None,
    )
    classifications = {name: counts[name] for name in _PENDING_CLASSIFICATIONS}
    return {
        "pending_total": total,
        "pending_returned": returned,
        "pending_truncated": truncated,
        "pending_scan_unavailable": pending_scan_unavailable,
        "pending_oldest_age_seconds": oldest,
        "pending_classifications": classifications,
        "pending_dead_count": dead_count,
        "pending_groups": {
            "ready": counts["ready"] + counts["already_written"],
            "review": sum(counts[name] for name in _PENDING_REVIEW_CLASSIFICATIONS),
            "blocker": (
                sum(counts[name] for name in _PENDING_BLOCKER_CLASSIFICATIONS)
                + dead_count
            ),
        },
    }


def _count_dead_pending_readonly(dead_dir: Path) -> tuple[int, bool]:
    try:
        opened = os.lstat(dead_dir)
    except FileNotFoundError:
        return 0, False
    except OSError:
        return 0, True
    if not os.path.isdir(dead_dir) or os.path.islink(dead_dir):
        return 0, True
    if not opened.st_ino or not opened.st_dev:
        return 0, True
    count = 0
    try:
        with os.scandir(dead_dir) as entries:
            for entry in entries:
                try:
                    if entry.is_symlink():
                        return count, True
                    if not entry.name.endswith(".jsonl"):
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        return count, True
                    count += 1
                except OSError:
                    return count, True
    except OSError:
        return 0, True
    return count, False


def _read_supersedes_graph_readonly(db_path: Path) -> _IndexGraphTruth:
    try:
        primary = _index_component_state(
            db_path,
            limit=_INDEX_COMPONENT_LIMITS[""],
        )
    except FileNotFoundError:
        return _IndexGraphTruth("not_available", frozenset())
    except (OSError, ValueError):
        return _IndexGraphTruth("unavailable", frozenset())

    try:
        rows, table_available = _query_supersedes_from_external_snapshot(
            db_path,
            primary=primary,
        )
    except (OSError, sqlite3.Error, subprocess.SubprocessError, ValueError):
        return _IndexGraphTruth("unavailable", frozenset())
    if not table_available:
        return _IndexGraphTruth("not_available", frozenset())
    return _IndexGraphTruth(
        "available",
        frozenset((str(source), str(target)) for source, target in rows),
    )


def _query_supersedes_from_external_snapshot(
    db_path: Path,
    *,
    primary: _IndexComponentState,
) -> tuple[list[tuple[object, object]], bool]:
    source_paths = {
        suffix: Path(f"{db_path}{suffix}")
        for suffix in _INDEX_COMPONENT_LIMITS
    }
    states: dict[str, _IndexComponentState | None] = {"": primary}
    for suffix in _INDEX_COMPONENT_LIMITS:
        if not suffix:
            continue
        try:
            states[suffix] = _index_component_state(
                source_paths[suffix],
                limit=_INDEX_COMPONENT_LIMITS[suffix],
            )
        except FileNotFoundError:
            states[suffix] = None
    if sum(state.size for state in states.values() if state is not None) > (
        _MAX_INDEX_SNAPSHOT_BYTES
    ):
        raise _UnsafeIndexSnapshot("INDEX_SNAPSHOT_TOO_LARGE")

    with tempfile.TemporaryDirectory(prefix="amh-readiness-index-") as temporary:
        temp_dir = Path(temporary)
        os.chmod(temp_dir, 0o700)
        for suffix, state in states.items():
            if state is None:
                continue
            _copy_index_component(
                source_paths[suffix],
                temp_dir / f"index.db{suffix}",
                expected=state,
                check_sqlite_lock=(suffix == ""),
            )
        for suffix, expected in states.items():
            source = source_paths[suffix]
            if expected is None:
                try:
                    os.lstat(source)
                except FileNotFoundError:
                    continue
                raise _UnsafeIndexSnapshot("INDEX_COMPONENT_APPEARED")
            current = _index_component_state(
                source,
                limit=_INDEX_COMPONENT_LIMITS[suffix],
            )
            if current != expected:
                raise _UnsafeIndexSnapshot("INDEX_COMPONENT_CHANGED")

        temp_database = temp_dir / "index.db"
        with sqlite3.connect(str(temp_database), timeout=0.1) as connection:
            connection.execute("PRAGMA busy_timeout=100")
            connection.execute("PRAGMA query_only=ON")
            table = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'refs_graph'"
            ).fetchone()
            if table is None:
                return [], False
            rows = connection.execute(
                "SELECT source_id, target_id FROM refs_graph WHERE relation = ?",
                ("supersedes",),
            ).fetchall()
            return rows, True


def _index_component_state(path: Path, *, limit: int) -> _IndexComponentState:
    opened = os.lstat(path)
    if not stat.S_ISREG(opened.st_mode) or stat.S_ISLNK(opened.st_mode):
        raise _UnsafeIndexSnapshot("INDEX_COMPONENT_NOT_REGULAR")
    if not opened.st_dev or not opened.st_ino:
        raise _UnsafeIndexSnapshot("INDEX_COMPONENT_IDENTITY_UNAVAILABLE")
    if opened.st_size < 0 or opened.st_size > limit:
        raise _UnsafeIndexSnapshot("INDEX_COMPONENT_TOO_LARGE")
    return _IndexComponentState(
        device=int(opened.st_dev),
        inode=int(opened.st_ino),
        mode=int(opened.st_mode),
        size=int(opened.st_size),
        mtime_ns=int(opened.st_mtime_ns),
        ctime_ns=int(opened.st_ctime_ns),
    )


def _copy_index_component(
    source: Path,
    destination: Path,
    *,
    expected: _IndexComponentState,
    check_sqlite_lock: bool,
) -> None:
    source_fd = -1
    destination_fd = -1
    try:
        source_fd = os.open(
            source,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        if _state_from_fstat(os.fstat(source_fd)) != expected:
            raise _UnsafeIndexSnapshot("INDEX_COMPONENT_CHANGED")
        if check_sqlite_lock and not _sqlite_shared_lock_available(source_fd):
            raise _UnsafeIndexSnapshot("INDEX_DATABASE_LOCKED")
        destination_fd = os.open(
            destination,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.fchmod(destination_fd, 0o600)
        remaining = expected.size
        while remaining:
            chunk = os.read(source_fd, min(1024 * 1024, remaining))
            if not chunk:
                raise _UnsafeIndexSnapshot("INDEX_COMPONENT_SHORT_READ")
            _write_all_fd(destination_fd, chunk)
            remaining -= len(chunk)
        if os.read(source_fd, 1):
            raise _UnsafeIndexSnapshot("INDEX_COMPONENT_GREW")
        if _state_from_fstat(os.fstat(source_fd)) != expected:
            raise _UnsafeIndexSnapshot("INDEX_COMPONENT_CHANGED")
        os.fsync(destination_fd)
    finally:
        if destination_fd >= 0:
            os.close(destination_fd)
        if source_fd >= 0:
            os.close(source_fd)


def _state_from_fstat(opened: os.stat_result) -> _IndexComponentState:
    if not stat.S_ISREG(opened.st_mode):
        raise _UnsafeIndexSnapshot("INDEX_COMPONENT_NOT_REGULAR")
    return _IndexComponentState(
        device=int(opened.st_dev),
        inode=int(opened.st_ino),
        mode=int(opened.st_mode),
        size=int(opened.st_size),
        mtime_ns=int(opened.st_mtime_ns),
        ctime_ns=int(opened.st_ctime_ns),
    )


def _write_all_fd(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise _UnsafeIndexSnapshot("INDEX_SNAPSHOT_WRITE_FAILED")
        remaining = remaining[written:]


def _sqlite_shared_lock_available(descriptor: int) -> bool:
    if os.name == "nt":
        import msvcrt

        locking = getattr(msvcrt, "locking")
        lock_nonblocking = getattr(msvcrt, "LK_NBLCK")
        unlock = getattr(msvcrt, "LK_UNLCK")
        try:
            os.lseek(descriptor, _SQLITE_SHARED_FIRST_BYTE, os.SEEK_SET)
            locking(descriptor, lock_nonblocking, 1)
        except OSError:
            return False
        finally:
            os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            os.lseek(descriptor, _SQLITE_SHARED_FIRST_BYTE, os.SEEK_SET)
            locking(descriptor, unlock, 1)
        finally:
            os.lseek(descriptor, 0, os.SEEK_SET)
        return True
    if os.name != "posix":
        return False
    script = (
        "import fcntl,os,sys\n"
        "fd=int(sys.argv[1])\n"
        "try:\n"
        f" fcntl.lockf(fd,fcntl.LOCK_SH|fcntl.LOCK_NB,1,{_SQLITE_SHARED_FIRST_BYTE},os.SEEK_SET)\n"
        "except OSError:\n"
        " raise SystemExit(1)\n"
        "fcntl.lockf(fd,fcntl.LOCK_UN,1,"
        f"{_SQLITE_SHARED_FIRST_BYTE},os.SEEK_SET)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(descriptor)],
        pass_fds=(descriptor,),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=0.5,
        check=False,
    )
    return completed.returncode == 0


def _index_dirty_status(path: Path) -> str:
    try:
        opened = os.lstat(path)
    except FileNotFoundError:
        return "clean"
    except OSError:
        return "unavailable"
    if not os.path.isfile(path) or os.path.islink(path):
        return "unavailable"
    return "repair_required" if opened.st_size else "clean"


def _age_seconds(now: datetime, observed_at: datetime) -> int | None:
    try:
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        return max(0, int((now - observed_at).total_seconds()))
    except (OverflowError, TypeError, ValueError):
        return None


def _aggregate_check(
    check_id: str,
    title: str,
    *,
    status: Status,
    detail: str,
    count: int | None = None,
) -> ReadinessCheck:
    evidence = {} if count is None else {"count": count}
    return ReadinessCheck(check_id, status, title, detail, evidence=evidence)


def _pending_integrity_check(metrics: dict[str, Any]) -> ReadinessCheck:
    classifications = metrics["pending_classifications"]
    blocker_count = int(metrics["pending_groups"]["blocker"])
    unavailable = bool(metrics["pending_scan_unavailable"])
    truncated = bool(metrics["pending_truncated"])
    status = "fail" if unavailable or truncated or blocker_count else "pass"
    return ReadinessCheck(
        "pending_integrity",
        status,
        "pending queue integrity",
        (
            "scan unavailable or incomplete"
            if unavailable or truncated
            else f"{blocker_count} blocker(s)"
        ),
        evidence={
            "blocker_count": blocker_count,
            "conflict_count": classifications["conflict"],
            "malformed_count": classifications["malformed"],
            "audit_blocked_count": classifications["audit_blocked"],
            "dead_count": metrics["pending_dead_count"],
        },
    )


def _pending_age_check(oldest_age_seconds: int | None) -> ReadinessCheck:
    if oldest_age_seconds is None:
        return ReadinessCheck("pending_age", "pass", "pending oldest age", "empty")
    if oldest_age_seconds > 7 * 86400:
        status = "fail"
    elif oldest_age_seconds > 24 * 3600:
        status = "warn"
    else:
        status = "pass"
    return ReadinessCheck(
        "pending_age",
        status,
        "pending oldest age",
        f"oldest_age_seconds={oldest_age_seconds}",
        evidence={"oldest_age_seconds": oldest_age_seconds},
    )


def _supersession_graph_check(
    *,
    graph_status: str,
    drift_count: int | None,
) -> ReadinessCheck:
    if graph_status == "unavailable":
        status = "fail"
    elif graph_status == "not_available":
        status = "warn"
    else:
        status = "fail" if drift_count else "pass"
    return ReadinessCheck(
        "supersession_graph",
        status,
        "supersession graph projection",
        f"status={graph_status}, drift_count={drift_count}",
        evidence={"graph_status": graph_status, "drift_count": drift_count},
    )


def _file_check(
    check_id: str,
    title: str,
    path: Path,
    *,
    missing_status: Status = "fail",
) -> ReadinessCheck:
    if path.exists():
        return ReadinessCheck(check_id, "pass", title, f"found: {path.as_posix()}")
    return ReadinessCheck(check_id, missing_status, title, f"missing: {path.as_posix()}")


def _public_hygiene_check(repo_root: Path) -> ReadinessCheck:
    try:
        from agent_brain.evaluation.public_hygiene import (
            format_findings,
            scan_git_public_surface,
        )

        findings = scan_git_public_surface(repo_root)
    except Exception as exc:  # noqa: BLE001 - readiness should not crash on non-git trees
        return ReadinessCheck(
            "public_hygiene",
            "warn",
            "public hygiene",
            f"scan unavailable: {exc}",
        )
    if findings:
        return ReadinessCheck(
            "public_hygiene",
            "fail",
            "public hygiene",
            format_findings(findings[:5]),
            evidence={"finding_count": len(findings)},
        )
    return ReadinessCheck("public_hygiene", "pass", "public hygiene", "no tracked findings")


def _threshold_check(check_id: str, title: str, count: int, command: str) -> ReadinessCheck:
    if count == 0:
        return ReadinessCheck(check_id, "pass", title, "0 items")
    return ReadinessCheck(
        check_id,
        "warn",
        title,
        f"{count} item(s); inspect with `{command}`",
        evidence={"count": count, "command": command},
    )


def _worst_status(statuses: Any) -> Status:
    order = {"pass": 0, "ok": 0, "warn": 1, "fail": 2, "error": 2}
    worst = "pass"
    for status in statuses:
        if order.get(str(status), 1) > order.get(worst, 0):
            worst = "fail" if str(status) == "error" else str(status)
    return worst


def _unique(values: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value)
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out


__all__ = [
    "GovernanceReadinessReport",
    "QUERY_SIGNAL_AUDIT_CASES_PATH",
    "ReadinessCheck",
    "ReadinessLane",
    "build_governance_readiness_report",
    "load_query_signal_audit_cases",
    "render_governance_readiness_markdown",
]
