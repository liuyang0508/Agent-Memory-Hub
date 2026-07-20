"""Governance readiness report for release, recall admission, and memory lifecycle."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from typer.testing import CliRunner

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.interfaces.cli import app
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.store.pending import enqueue_write_record
from agent_brain.product.governance_readiness import (
    build_governance_readiness_report,
    build_memory_lifecycle_readiness,
)

runner = CliRunner()


def _write_supersedes_index(brain: Path, edges: list[tuple[str, str]]) -> None:
    with sqlite3.connect(brain / "index.db") as connection:
        connection.execute(
            "CREATE TABLE refs_graph ("
            "source_id TEXT NOT NULL, target_id TEXT NOT NULL, "
            "relation TEXT NOT NULL DEFAULT 'refs', "
            "PRIMARY KEY (source_id, target_id, relation))"
        )
        connection.executemany(
            "INSERT INTO refs_graph(source_id, target_id, relation) " "VALUES (?, ?, 'supersedes')",
            edges,
        )


def _tree_snapshot(root: Path) -> dict[str, tuple[bool, int, int]]:
    return {
        path.relative_to(root).as_posix(): (
            path.is_dir(),
            path.stat().st_mtime_ns,
            path.stat().st_size,
        )
        for path in sorted(root.rglob("*"))
    }


def _full_tree_snapshot(root: Path) -> dict[str, tuple[bool, int, bytes]]:
    paths = [root, *sorted(root.rglob("*"))]
    return {
        "." if path == root else path.relative_to(root).as_posix(): (
            path.is_dir(),
            path.stat().st_mtime_ns,
            b"" if path.is_dir() else path.read_bytes(),
        )
        for path in paths
    }


def _open_wal_index_without_shm(
    brain: Path,
    edge: tuple[str, str],
) -> sqlite3.Connection:
    database = brain / "index.db"
    connection = sqlite3.connect(database)
    assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute(
        "CREATE TABLE refs_graph ("
        "source_id TEXT NOT NULL, target_id TEXT NOT NULL, relation TEXT NOT NULL, "
        "PRIMARY KEY (source_id, target_id, relation))"
    )
    connection.commit()
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.execute(
        "INSERT INTO refs_graph VALUES (?, ?, 'supersedes')",
        edge,
    )
    connection.commit()
    Path(f"{database}-shm").unlink()
    assert Path(f"{database}-wal").exists()
    assert not Path(f"{database}-shm").exists()
    return connection


def _create_hot_rollback_journal(
    brain: Path,
    *,
    committed_edge: tuple[str, str],
    dirty_edge: tuple[str, str],
) -> Path:
    database = brain / "index.db"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute(
            "CREATE TABLE refs_graph ("
            "source_id TEXT NOT NULL, target_id TEXT NOT NULL, relation TEXT NOT NULL, "
            "PRIMARY KEY (source_id, target_id, relation))"
        )
        connection.execute("CREATE TABLE spill (sequence INTEGER PRIMARY KEY, payload BLOB)")
        connection.execute(
            "INSERT INTO refs_graph VALUES (?, ?, 'supersedes')",
            committed_edge,
        )
    script = """
import os
import sqlite3
import sys

database, dirty_source, dirty_target = sys.argv[1:]
connection = sqlite3.connect(database)
connection.execute("PRAGMA journal_mode=DELETE")
connection.execute("PRAGMA synchronous=FULL")
connection.execute("PRAGMA cache_size=1")
connection.execute("PRAGMA cache_spill=ON")
connection.execute("BEGIN IMMEDIATE")
connection.execute("DELETE FROM refs_graph")
connection.execute(
    "INSERT INTO refs_graph VALUES (?, ?, 'supersedes')",
    (dirty_source, dirty_target),
)
for _index in range(2000):
    connection.execute("INSERT INTO spill(payload) VALUES (?)", (b"x" * 4000,))
os._exit(0)
"""
    subprocess.run(
        [sys.executable, "-c", script, str(database), *dirty_edge],
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    journal = Path(f"{database}-journal")
    assert journal.exists()
    return database


def _write_item(
    store: ItemsStore,
    item_id: str,
    *,
    item_type: MemoryType,
    days_old: int,
    confidence: float,
    tags: list[str],
    title: str,
) -> None:
    item = MemoryItem(
        id=item_id,
        type=item_type,
        created_at=datetime.now(timezone.utc) - timedelta(days=days_old),
        title=title,
        summary=f"{title} summary",
        confidence=confidence,
        tags=tags,
        project="agent-memory-hub",
    )
    store.write(item, f"{title}\nbody")


def test_govern_readiness_json_reports_release_query_and_lifecycle_lanes(tmp_path, monkeypatch):
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    _write_item(
        store,
        "mem-20260101-000000-stale-signal-aaaa",
        item_type=MemoryType.signal,
        days_old=60,
        confidence=0.3,
        tags=[],
        title="stale hook warning",
    )
    _write_item(
        store,
        "mem-20260101-000000-good-decision-bbbb",
        item_type=MemoryType.decision,
        days_old=3,
        confidence=0.9,
        tags=["release", "doctor"],
        title="doctor fix release decision",
    )
    monkeypatch.setenv("BRAIN_DIR", str(brain))

    result = runner.invoke(app, ["govern", "readiness", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    by_lane = {lane["id"]: lane for lane in payload["lanes"]}
    assert set(by_lane) == {"release", "query_signal", "memory_lifecycle"}
    assert payload["overall_status"] in {"pass", "warn"}
    assert any(check["id"] == "install_sh" for check in by_lane["release"]["checks"])
    assert by_lane["query_signal"]["metrics"]["case_count"] >= 4
    assert by_lane["query_signal"]["metrics"]["under_extracted_cases"] == 0
    lifecycle = by_lane["memory_lifecycle"]["metrics"]
    assert lifecycle["total_items"] == 2
    assert lifecycle["stale_signal_count"] == 1
    assert lifecycle["low_confidence_count"] == 1
    assert lifecycle["untagged_count"] == 1
    assert any("memory govern plan" in action for action in payload["next_actions"])


def test_govern_readiness_query_signal_uses_adversarial_manifest(tmp_path, monkeypatch):
    brain = tmp_path / "brain"
    ItemsStore(brain / "items")
    monkeypatch.setenv("BRAIN_DIR", str(brain))

    result = runner.invoke(app, ["govern", "readiness", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    query_lane = next(lane for lane in payload["lanes"] if lane["id"] == "query_signal")
    metrics = query_lane["metrics"]
    assert metrics["case_count"] >= 10
    assert metrics["category_counts"]["cjk_long_task"] >= 2
    assert metrics["category_counts"]["json_config"] >= 1
    assert metrics["category_counts"]["multimodal"] >= 2
    assert metrics["category_counts"]["log_trace"] >= 1
    assert metrics["category_counts"]["code_snippet"] >= 1
    assert metrics["category_counts"]["weak_followup"] >= 2
    assert metrics["under_extracted_cases"] == 0
    assert any(check["id"] == "json_config_interface_reuse" for check in query_lane["checks"])
    assert any(check["id"] == "image_placeholder_without_ocr" for check in query_lane["checks"])


def test_govern_readiness_markdown_is_user_facing(tmp_path, monkeypatch):
    brain = tmp_path / "brain"
    ItemsStore(brain / "items")
    monkeypatch.setenv("BRAIN_DIR", str(brain))

    result = runner.invoke(app, ["govern", "readiness", "--format", "markdown"])

    assert result.exit_code == 0, result.output
    assert "# Governance Readiness" in result.output
    assert "发布可用性" in result.output
    assert "长任务召回入口" in result.output
    assert "记忆生命周期" in result.output


def test_lifecycle_readiness_reports_pending_and_broken_supersession(
    tmp_path,
    monkeypatch,
):
    brain = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    store = ItemsStore(brain / "items")
    broken = MemoryItem(
        id="mem-20260719-100000-broken-supersession",
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc) - timedelta(days=40),
        title="broken supersession",
        summary="broken supersession summary",
        tags=["lifecycle"],
        superseded_by="mem-20260719-110000-missing-target",
    )
    store.write(broken, "broken body")
    old_time = datetime.now(timezone.utc) - timedelta(days=8)
    enqueue_write_record(
        {
            "v": 2,
            "op": "write",
            "origin": "hook",
            "record_id": "pending-readiness-0001",
            "enqueued_at": old_time.isoformat(),
            "original_created_at": old_time.isoformat(),
            "item": {
                "type": "fact",
                "title": "pending readiness fact",
                "summary": "pending readiness summary",
                "body": "pending readiness body",
                "tags": ["pending"],
                "sensitivity": "internal",
            },
        }
    )

    result = runner.invoke(app, ["govern", "readiness", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    lane = next(row for row in payload["lanes"] if row["id"] == "memory_lifecycle")
    assert lane["status"] == "fail"
    assert lane["metrics"]["broken_superseded_count"] == 1
    assert lane["metrics"]["pending_total"] == 1
    assert lane["metrics"]["pending_oldest_age_seconds"] >= 7 * 86400
    assert lane["metrics"]["pending_classifications"]["ready"] == 1
    assert lane["metrics"]["pending_groups"] == {
        "ready": 1,
        "review": 0,
        "blocker": 0,
    }
    assert all("--apply" not in action for action in lane["next_actions"])


def test_lifecycle_readiness_fails_on_graph_only_supersession_drift(
    tmp_path,
    monkeypatch,
):
    brain = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    store = ItemsStore(brain / "items")
    old_id = "mem-20260719-100001-graph-only-old"
    new_id = "mem-20260719-110001-graph-only-new"
    _write_item(
        store,
        old_id,
        item_type=MemoryType.fact,
        days_old=2,
        confidence=0.9,
        tags=["lifecycle"],
        title="old graph fact",
    )
    _write_item(
        store,
        new_id,
        item_type=MemoryType.fact,
        days_old=1,
        confidence=0.9,
        tags=["lifecycle"],
        title="new graph fact",
    )
    _write_supersedes_index(brain, [(new_id, old_id)])

    result = runner.invoke(app, ["govern", "readiness", "--format", "json"])

    assert result.exit_code == 0, result.output
    lane = next(
        row for row in json.loads(result.output)["lanes"] if row["id"] == "memory_lifecycle"
    )
    assert lane["status"] == "fail"
    assert lane["metrics"]["supersession_graph_status"] == "available"
    assert lane["metrics"]["supersession_drift_count"] == 1


def test_lifecycle_readiness_fails_on_frontmatter_only_drift_and_ignores_custom_edges(
    tmp_path,
    monkeypatch,
):
    brain = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    store = ItemsStore(brain / "items")
    old_id = "mem-20260719-100002-frontmatter-old"
    new_id = "mem-20260719-110002-frontmatter-new"
    old = MemoryItem(
        id=old_id,
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc) - timedelta(days=2),
        title="frontmatter old",
        summary="frontmatter old summary",
        superseded_by=new_id,
    )
    store.write(old, "old body")
    _write_item(
        store,
        new_id,
        item_type=MemoryType.fact,
        days_old=1,
        confidence=0.9,
        tags=["lifecycle"],
        title="frontmatter new",
    )
    with sqlite3.connect(brain / "index.db") as connection:
        connection.execute(
            "CREATE TABLE refs_graph ("
            "source_id TEXT NOT NULL, target_id TEXT NOT NULL, relation TEXT NOT NULL, "
            "PRIMARY KEY (source_id, target_id, relation))"
        )
        connection.execute(
            "INSERT INTO refs_graph VALUES (?, ?, 'custom')",
            (new_id, old_id),
        )

    report = build_governance_readiness_report(brain, repo_root=Path.cwd())
    lane = next(row for row in report.lanes if row.id == "memory_lifecycle")

    assert lane.status == "fail"
    assert lane.metrics["supersession_drift_count"] == 1
    assert lane.metrics["broken_superseded_count"] == 0


def test_lifecycle_readiness_marks_missing_index_not_available_without_mutation(
    tmp_path,
    monkeypatch,
):
    brain = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    store = ItemsStore(brain / "items")
    _write_item(
        store,
        "mem-20260719-100003-no-index",
        item_type=MemoryType.fact,
        days_old=1,
        confidence=0.9,
        tags=["ready"],
        title="healthy memory",
    )
    before = _tree_snapshot(brain)

    report = build_governance_readiness_report(brain, repo_root=Path.cwd())
    lane = next(row for row in report.lanes if row.id == "memory_lifecycle")

    assert lane.metrics["supersession_graph_status"] == "not_available"
    assert lane.metrics["supersession_drift_count"] is None
    assert lane.metrics["index_repair_required"] is True
    assert lane.status == "warn"
    assert _tree_snapshot(brain) == before
    assert not (brain / "index.db").exists()


def test_lifecycle_readiness_fails_closed_when_pending_scan_is_unavailable(
    tmp_path,
    monkeypatch,
):
    from agent_brain.memory.store.pending import PendingEnqueueError, PendingQueue

    brain = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    (brain / "items").mkdir(parents=True)
    monkeypatch.setattr(
        PendingQueue,
        "preview_for_readiness",
        lambda self, **_kwargs: (_ for _ in ()).throw(
            PendingEnqueueError("PENDING_SCAN_UNAVAILABLE")
        ),
    )

    report = build_governance_readiness_report(brain, repo_root=Path.cwd())
    lane = next(row for row in report.lanes if row.id == "memory_lifecycle")

    assert lane.status == "fail"
    assert lane.metrics["pending_scan_unavailable"] is True


def test_lifecycle_readiness_never_leaks_private_item_or_pending_content(
    tmp_path,
    monkeypatch,
):
    brain = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    store = ItemsStore(brain / "items")
    secret_item_id = "mem-20260719-100004-private-secret"
    private = MemoryItem(
        id=secret_item_id,
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="PRIVATE_TITLE_CANARY",
        summary="PRIVATE_SUMMARY_CANARY",
        sensitivity="private",
    )
    store.write(private, "PRIVATE_BODY_CANARY")
    enqueue_write_record(
        {
            "v": 2,
            "op": "write",
            "origin": "hook",
            "record_id": "private-pending-record",
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
            "original_created_at": datetime.now(timezone.utc).isoformat(),
            "item": {
                "type": "fact",
                "title": "PENDING_TITLE_CANARY",
                "summary": "PENDING_SUMMARY_CANARY",
                "body": "PENDING_BODY_CANARY",
                "sensitivity": "private",
            },
        }
    )

    report = build_governance_readiness_report(brain, repo_root=Path.cwd())
    lane = next(row for row in report.lanes if row.id == "memory_lifecycle")
    serialized = json.dumps(lane.to_dict(), ensure_ascii=False)

    for canary in (
        secret_item_id,
        "PRIVATE_TITLE_CANARY",
        "PRIVATE_SUMMARY_CANARY",
        "PRIVATE_BODY_CANARY",
        "private-pending-record",
        "PENDING_TITLE_CANARY",
        "PENDING_SUMMARY_CANARY",
        "PENDING_BODY_CANARY",
        str(brain),
    ):
        assert canary not in serialized


def test_lifecycle_readiness_empty_brain_is_read_only_and_explicit(tmp_path, monkeypatch):
    brain = tmp_path / "absent-brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    assert not brain.exists()

    report = build_governance_readiness_report(brain, repo_root=Path.cwd())
    lane = next(row for row in report.lanes if row.id == "memory_lifecycle")

    assert lane.metrics["active_count"] == 0
    assert lane.metrics["archived_count"] == 0
    assert lane.metrics["pending_total"] == 0
    assert lane.metrics["supersession_graph_status"] == "not_available"
    assert not brain.exists()


def test_lifecycle_readiness_uses_observed_at_and_excludes_active_deferral(
    tmp_path,
    monkeypatch,
):
    from agent_brain.memory.governance.lifecycle_ledger import (
        LifecycleLedgerRecord,
        append_lifecycle_record,
    )

    brain = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    store = ItemsStore(brain / "items")
    now = datetime.now(timezone.utc)
    item = MemoryItem(
        id="mem-20260719-100005-deferred-signal",
        type=MemoryType.signal,
        created_at=now - timedelta(days=90),
        title="deferred signal",
        summary="deferred signal summary",
        validity={"observed_at": now - timedelta(days=40)},
    )
    store.write(item, "deferred body")
    append_lifecycle_record(
        brain,
        LifecycleLedgerRecord(
            action="defer",
            obsolete_id=item.id,
            replacement_id=None,
            status="deferred",
            reason="OK",
            timestamp=now.isoformat(),
            snapshot=None,
            replacement_ref_preexisted=False,
            deferred_until=(now + timedelta(days=3)).isoformat(),
        ),
    )

    report = build_governance_readiness_report(brain, repo_root=Path.cwd())
    lane = next(row for row in report.lanes if row.id == "memory_lifecycle")

    assert lane.metrics["stale_count"] == 1
    assert lane.metrics["review_queue_count"] == 0
    assert lane.metrics["review_queue_oldest_age_seconds"] is None


def test_lifecycle_readiness_counts_archived_replacement_as_broken(
    tmp_path,
    monkeypatch,
):
    brain = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    store = ItemsStore(brain / "items")
    old_id = "mem-20260719-100006-archived-target-old"
    target_id = "mem-20260719-110006-archived-target"
    old = MemoryItem(
        id=old_id,
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc) - timedelta(days=2),
        title="obsolete item",
        summary="obsolete summary",
        superseded_by=target_id,
    )
    target = MemoryItem(
        id=target_id,
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc) - timedelta(days=1),
        title="archived target",
        summary="archived target summary",
    )
    store.write(old, "old body")
    target_path = store.write(target, "target body")
    archived = store.items_dir / "archived"
    archived.mkdir()
    target_path.replace(archived / target_path.name)

    lane = build_memory_lifecycle_readiness(brain)

    assert lane.metrics["archived_count"] == 1
    assert lane.metrics["superseded_count"] == 1
    assert lane.metrics["broken_superseded_count"] == 1
    assert lane.status == "fail"


def test_lifecycle_readiness_ignores_dangling_and_non_supersedes_graph_edges(
    tmp_path,
    monkeypatch,
):
    brain = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    store = ItemsStore(brain / "items")
    active_id = "mem-20260719-100007-dangling-graph-active"
    _write_item(
        store,
        active_id,
        item_type=MemoryType.fact,
        days_old=1,
        confidence=0.9,
        tags=["graph"],
        title="active graph item",
    )
    missing_id = "mem-20260719-110007-dangling-graph-missing"
    with sqlite3.connect(brain / "index.db") as connection:
        connection.execute(
            "CREATE TABLE refs_graph ("
            "source_id TEXT NOT NULL, target_id TEXT NOT NULL, relation TEXT NOT NULL, "
            "PRIMARY KEY (source_id, target_id, relation))"
        )
        connection.executemany(
            "INSERT INTO refs_graph VALUES (?, ?, ?)",
            [
                (missing_id, active_id, "supersedes"),
                (active_id, active_id, "custom"),
            ],
        )

    lane = build_memory_lifecycle_readiness(brain)

    assert lane.metrics["supersession_graph_status"] == "available"
    assert lane.metrics["supersession_drift_count"] == 0


def test_lifecycle_readiness_fails_closed_on_malformed_item_and_corrupt_index(
    tmp_path,
    monkeypatch,
):
    brain = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    items = brain / "items"
    items.mkdir(parents=True)
    (items / "broken.md").write_text("not valid frontmatter", encoding="utf-8")
    (brain / "index.db").write_bytes(b"not a sqlite database")

    lane = build_memory_lifecycle_readiness(brain)

    assert lane.metrics["malformed_item_count"] == 1
    assert lane.metrics["supersession_graph_status"] == "unavailable"
    assert lane.metrics["supersession_drift_count"] is None
    assert lane.status == "fail"


def test_lifecycle_readiness_fails_fast_on_locked_index_without_writes(
    tmp_path,
    monkeypatch,
):
    brain = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    (brain / "items").mkdir(parents=True)
    _write_supersedes_index(brain, [])
    before = _tree_snapshot(brain)
    locker = sqlite3.connect(brain / "index.db")
    try:
        locker.execute("PRAGMA journal_mode=DELETE")
        locker.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()

        lane = build_memory_lifecycle_readiness(brain)

        assert time.monotonic() - started < 0.75
        assert lane.metrics["supersession_graph_status"] == "unavailable"
        assert lane.status == "fail"
        assert _tree_snapshot(brain) == before
    finally:
        locker.rollback()
        locker.close()


def test_lifecycle_readiness_pending_utc_age_warns_after_24_hours(
    tmp_path,
    monkeypatch,
):
    brain = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    (brain / "items").mkdir(parents=True)
    observed = datetime.now(timezone(timedelta(hours=8))) - timedelta(hours=25)
    enqueue_write_record(
        {
            "v": 2,
            "op": "write",
            "origin": "hook",
            "record_id": "pending-utc-warning",
            "enqueued_at": observed.isoformat(),
            "original_created_at": observed.isoformat(),
            "item": {
                "type": "fact",
                "title": "utc warning",
                "summary": "utc warning summary",
                "body": "utc warning body",
            },
        }
    )

    lane = build_memory_lifecycle_readiness(brain)

    age_check = next(check for check in lane.checks if check.id == "pending_age")
    assert age_check.status == "warn"
    assert lane.metrics["pending_oldest_age_seconds"] >= 24 * 3600
    assert lane.metrics["pending_oldest_age_seconds"] < 7 * 86400


def test_lifecycle_readiness_fails_closed_on_unsafe_dead_queue_entry(
    tmp_path,
    monkeypatch,
):
    brain = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    (brain / "items").mkdir(parents=True)
    dead = brain / "pending" / "dead"
    dead.mkdir(parents=True)
    outside = tmp_path / "outside.jsonl"
    outside.write_text("{}\n", encoding="utf-8")
    (dead / "unsafe.jsonl").symlink_to(outside)

    lane = build_memory_lifecycle_readiness(brain)

    assert lane.metrics["pending_scan_unavailable"] is True
    assert lane.status == "fail"


def test_lifecycle_readiness_reads_pending_from_explicit_brain_not_environment(
    tmp_path,
    monkeypatch,
):
    requested_brain = tmp_path / "requested-brain"
    other_brain = tmp_path / "other-brain"
    monkeypatch.setenv("BRAIN_DIR", str(requested_brain))
    (requested_brain / "items").mkdir(parents=True)
    enqueue_write_record(
        {
            "v": 2,
            "op": "write",
            "origin": "hook",
            "record_id": "explicit-brain-pending",
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
            "original_created_at": datetime.now(timezone.utc).isoformat(),
            "item": {
                "type": "fact",
                "title": "explicit brain item",
                "summary": "explicit brain summary",
                "body": "explicit brain body",
            },
        }
    )
    monkeypatch.setenv("BRAIN_DIR", str(other_brain))

    lane = build_memory_lifecycle_readiness(requested_brain)

    assert lane.metrics["pending_total"] == 1
    assert lane.metrics["pending_classifications"]["ready"] == 1
    assert not other_brain.exists()


def test_lifecycle_readiness_reads_wal_snapshot_only_from_external_temp_copy(
    tmp_path,
    monkeypatch,
):
    import agent_brain.product.governance_readiness as readiness_module

    brain = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    store = ItemsStore(brain / "items")
    old_id = "mem-20260720-100000-wal-snapshot-old"
    new_id = "mem-20260720-110000-wal-snapshot-new"
    old = MemoryItem(
        id=old_id,
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc) - timedelta(days=2),
        title="wal old",
        summary="wal old summary",
        superseded_by=new_id,
    )
    new = MemoryItem(
        id=new_id,
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc) - timedelta(days=1),
        title="wal new",
        summary="wal new summary",
    )
    store.write(old, "old body")
    store.write(new, "new body")
    writer = _open_wal_index_without_shm(brain, (new_id, old_id))
    before = _full_tree_snapshot(brain)
    real_connect = sqlite3.connect
    opened_databases: list[str] = []

    def track_connect(database, *args, **kwargs):
        opened_databases.append(str(database))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(readiness_module.sqlite3, "connect", track_connect)
    try:
        lane = build_memory_lifecycle_readiness(brain)
        after = _full_tree_snapshot(brain)
        assert lane.metrics["supersession_graph_status"] == "available"
        assert lane.metrics["supersession_drift_count"] == 0
        assert after == before
        assert opened_databases
        assert all(str(brain) not in database for database in opened_databases)
        for database in opened_databases:
            if database.startswith("file:"):
                continue
            assert not Path(database).parent.exists()
    finally:
        writer.close()


def test_lifecycle_readiness_rolls_back_hot_journal_only_in_temp_snapshot(
    tmp_path,
):
    import agent_brain.product.governance_readiness as readiness_module

    brain = tmp_path / "brain"
    brain.mkdir()
    committed_edge = ("committed-new", "committed-old")
    dirty_edge = ("dirty-new", "dirty-old")
    database = _create_hot_rollback_journal(
        brain,
        committed_edge=committed_edge,
        dirty_edge=dirty_edge,
    )
    raw_copy = tmp_path / "raw-without-journal.db"
    raw_copy.write_bytes(database.read_bytes())
    with sqlite3.connect(
        f"{raw_copy.as_uri()}?mode=ro&immutable=1",
        uri=True,
    ) as connection:
        assert connection.execute(
            "SELECT source_id, target_id FROM refs_graph WHERE relation = 'supersedes'"
        ).fetchall() == [dirty_edge]
    before = _full_tree_snapshot(brain)

    truth = readiness_module._read_supersedes_graph_readonly(database)

    assert truth.status == "available"
    assert truth.edges == frozenset({committed_edge})
    assert dirty_edge not in truth.edges
    assert _full_tree_snapshot(brain) == before


def test_lifecycle_readiness_ignores_stale_non_hot_journal_without_mutation(
    tmp_path,
):
    import agent_brain.product.governance_readiness as readiness_module

    brain = tmp_path / "brain"
    brain.mkdir()
    committed_edge = ("stale-journal-new", "stale-journal-old")
    _write_supersedes_index(brain, [committed_edge])
    journal = brain / "index.db-journal"
    journal.write_bytes(b"\0" * 1024)
    before = _full_tree_snapshot(brain)

    truth = readiness_module._read_supersedes_graph_readonly(brain / "index.db")

    assert truth.status == "available"
    assert truth.edges == frozenset({committed_edge})
    assert _full_tree_snapshot(brain) == before


def test_lifecycle_readiness_rejects_oversized_rollback_journal(
    tmp_path,
    monkeypatch,
):
    import agent_brain.product.governance_readiness as readiness_module

    brain = tmp_path / "brain"
    brain.mkdir()
    _write_supersedes_index(brain, [("bounded-new", "bounded-old")])
    journal = brain / "index.db-journal"
    journal.write_bytes(b"\0" * 1024)
    before = _full_tree_snapshot(brain)
    monkeypatch.setitem(readiness_module._INDEX_COMPONENT_LIMITS, "-journal", 512)

    truth = readiness_module._read_supersedes_graph_readonly(brain / "index.db")

    assert truth.status == "unavailable"
    assert _full_tree_snapshot(brain) == before


def test_lifecycle_readiness_rejects_journal_changed_during_snapshot(
    tmp_path,
    monkeypatch,
):
    import agent_brain.product.governance_readiness as readiness_module

    brain = tmp_path / "brain"
    brain.mkdir()
    _write_supersedes_index(brain, [("changing-new", "changing-old")])
    journal = brain / "index.db-journal"
    journal.write_bytes(b"\0" * 1024)
    real_copy = readiness_module._copy_index_component

    def copy_then_change(source, destination, **kwargs):
        real_copy(source, destination, **kwargs)
        if source.name == "index.db-journal":
            source.write_bytes(source.read_bytes() + b"changed")

    monkeypatch.setattr(
        readiness_module,
        "_copy_index_component",
        copy_then_change,
    )

    truth = readiness_module._read_supersedes_graph_readonly(brain / "index.db")

    assert truth.status == "unavailable"


def test_lifecycle_readiness_rejects_duplicate_item_id_across_active_and_archive(
    tmp_path,
    monkeypatch,
):
    brain = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    store = ItemsStore(brain / "items")
    item_id = "mem-20260720-120000-duplicate-tree-id"
    _write_item(
        store,
        item_id,
        item_type=MemoryType.fact,
        days_old=1,
        confidence=0.9,
        tags=["duplicate"],
        title="duplicate tree id",
    )
    archived = store.items_dir / "archived"
    archived.mkdir()
    source = store.items_dir / f"{item_id}.md"
    (archived / source.name).write_bytes(source.read_bytes())

    lane = build_memory_lifecycle_readiness(brain)

    assert lane.status == "fail"
    assert lane.metrics["item_scan_unavailable"] is True


def test_lifecycle_readiness_retries_concurrent_archive_as_one_generation(
    tmp_path,
    monkeypatch,
):
    import agent_brain.product.governance_readiness as readiness_module

    brain = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    store = ItemsStore(brain / "items")
    item_id = "mem-20260720-120001-concurrent-archive"
    _write_item(
        store,
        item_id,
        item_type=MemoryType.fact,
        days_old=1,
        confidence=0.9,
        tags=["archive"],
        title="concurrent archive",
    )
    real_scan = readiness_module._read_items_readonly
    moved = False

    def scan_then_archive(items_dir):
        nonlocal moved
        result = real_scan(items_dir)
        if not moved:
            moved = True
            archived = items_dir / "archived"
            archived.mkdir()
            source = items_dir / f"{item_id}.md"
            source.replace(archived / source.name)
        return result

    monkeypatch.setattr(readiness_module, "_read_items_readonly", scan_then_archive)

    lane = build_memory_lifecycle_readiness(brain)

    assert lane.metrics["snapshot_unstable"] is False
    assert lane.metrics["active_count"] == 0
    assert lane.metrics["archived_count"] == 1


def test_lifecycle_readiness_fails_after_repeated_generation_changes(
    tmp_path,
    monkeypatch,
):
    import agent_brain.product.governance_readiness as readiness_module

    brain = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    store = ItemsStore(brain / "items")
    item_id = "mem-20260720-120002-unstable-generation"
    _write_item(
        store,
        item_id,
        item_type=MemoryType.fact,
        days_old=1,
        confidence=0.9,
        tags=["unstable"],
        title="unstable generation",
    )
    source = store.items_dir / f"{item_id}.md"
    real_scan = readiness_module._read_items_readonly

    def scan_then_touch(items_dir):
        result = real_scan(items_dir)
        source.touch()
        return result

    monkeypatch.setattr(readiness_module, "_read_items_readonly", scan_then_touch)

    lane = build_memory_lifecycle_readiness(brain)

    assert lane.status == "fail"
    assert lane.metrics["snapshot_unstable"] is True


def test_lifecycle_readiness_preserves_legacy_stale_signal_check_schema(
    tmp_path,
    monkeypatch,
):
    brain = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    store = ItemsStore(brain / "items")
    _write_item(
        store,
        "mem-20260720-120003-legacy-stale-check",
        item_type=MemoryType.signal,
        days_old=40,
        confidence=0.9,
        tags=["legacy"],
        title="legacy stale check",
    )

    lane = build_memory_lifecycle_readiness(brain)
    checks = {check.id: check for check in lane.checks}

    assert lane.metrics["stale_signal_count"] == 1
    assert checks["stale_signal_count"].status == "warn"
    assert checks["stale_signal_count"].evidence["count"] == 1
    assert checks["review_queue_count"].status == "warn"


def test_lifecycle_readiness_rejects_duplicate_or_non_unique_graph_schema(
    tmp_path,
):
    import agent_brain.product.governance_readiness as readiness_module

    brain = tmp_path / "brain"
    brain.mkdir()
    database = brain / "index.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE refs_graph ("
            "source_id TEXT NOT NULL, target_id TEXT NOT NULL, relation TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO refs_graph VALUES (?, ?, 'supersedes')",
            [("duplicate-new", "duplicate-old")] * 2,
        )

    truth = readiness_module._read_supersedes_graph_readonly(database)

    assert truth.status == "unavailable"


def test_lifecycle_readiness_reports_corrupt_dirty_marker(
    tmp_path,
    monkeypatch,
):
    brain = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    (brain / "items").mkdir(parents=True)
    (brain / ".index-dirty").write_text("not-a-memory-id\n", encoding="utf-8")

    lane = build_memory_lifecycle_readiness(brain)

    assert lane.status == "fail"
    assert lane.metrics["index_dirty_status"] == "corrupt"


def test_lifecycle_generation_token_stops_streaming_at_deadline(
    tmp_path,
    monkeypatch,
):
    import agent_brain.product.governance_readiness as readiness_module

    brain = tmp_path / "brain"
    items = brain / "items"
    items.mkdir(parents=True)
    for index in range(20):
        (items / f"entry-{index:02d}.txt").write_text("x", encoding="utf-8")
    real_scandir = readiness_module.os.scandir
    visited = 0

    class CountingScandir:
        def __init__(self, target):
            self._inner = real_scandir(target)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self._inner.close()

        def __iter__(self):
            return self

        def __next__(self):
            nonlocal visited
            visited += 1
            return next(self._inner)

    ticks = iter((0.0, 0.0, 0.0, 3.0))
    monkeypatch.setattr(readiness_module, "_snapshot_monotonic", lambda: next(ticks, 3.0))
    monkeypatch.setattr(readiness_module.os, "scandir", CountingScandir)

    assert readiness_module._lifecycle_generation_token(brain) is None
    assert visited <= 3


def test_lifecycle_readiness_fails_closed_on_oversized_ledger(
    tmp_path,
):
    brain = tmp_path / "brain"
    (brain / "items").mkdir(parents=True)
    runtime = brain / "runtime"
    runtime.mkdir()
    ledger = runtime / "lifecycle-actions.jsonl"
    with ledger.open("wb") as handle:
        handle.truncate(16 * 1024 * 1024 + 1)

    lane = build_memory_lifecycle_readiness(brain)

    assert lane.status == "fail"
    assert lane.metrics["lifecycle_ledger_unavailable"] is True


def test_lifecycle_readiness_fails_closed_on_oversized_ledger_line(
    tmp_path,
):
    brain = tmp_path / "brain"
    (brain / "items").mkdir(parents=True)
    runtime = brain / "runtime"
    runtime.mkdir()
    (runtime / "lifecycle-actions.jsonl").write_bytes(b"x" * (1024 * 1024 + 1))

    lane = build_memory_lifecycle_readiness(brain)

    assert lane.status == "fail"
    assert lane.metrics["lifecycle_ledger_unavailable"] is True


def test_graph_budget_rejects_blob_before_payload_query(
    tmp_path,
    monkeypatch,
):
    import agent_brain.product.governance_readiness as readiness_module

    brain = tmp_path / "brain"
    brain.mkdir()
    database = brain / "index.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE refs_graph ("
            "source_id TEXT NOT NULL, target_id TEXT NOT NULL, relation TEXT NOT NULL, "
            "PRIMARY KEY (source_id, target_id, relation))"
        )
        connection.execute(
            "INSERT INTO refs_graph VALUES (zeroblob(1048576), 'target', 'supersedes')"
        )

    real_connect = sqlite3.connect
    payload_queries: list[str] = []

    class TrackingConnection:
        def __init__(self, path, *args, **kwargs):
            self._inner = real_connect(path, *args, **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self._inner.close()

        def execute(self, sql, parameters=()):
            if sql.lstrip().startswith("SELECT source_id, target_id, relation"):
                payload_queries.append(sql)
            return self._inner.execute(sql, parameters)

    monkeypatch.setattr(readiness_module.sqlite3, "connect", TrackingConnection)

    truth = readiness_module._read_supersedes_graph_readonly(database)

    assert truth.status == "unavailable"
    assert payload_queries == []


def test_graph_payload_rows_are_streamed_without_fetchall(
    tmp_path,
    monkeypatch,
):
    import agent_brain.product.governance_readiness as readiness_module

    brain = tmp_path / "brain"
    brain.mkdir()
    edge = ("stream-new", "stream-old")
    _write_supersedes_index(brain, [edge])
    real_connect = sqlite3.connect

    class PayloadCursor:
        def __init__(self, inner):
            self._inner = inner

        def fetchall(self):
            raise AssertionError("payload query must not fetchall")

        def fetchmany(self, size=None):
            return self._inner.fetchmany(size)

        def __iter__(self):
            return iter(self._inner)

    class TrackingConnection:
        def __init__(self, path, *args, **kwargs):
            self._inner = real_connect(path, *args, **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self._inner.close()

        def execute(self, sql, parameters=()):
            cursor = self._inner.execute(sql, parameters)
            if sql.lstrip().startswith("SELECT source_id, target_id, relation"):
                return PayloadCursor(cursor)
            return cursor

    monkeypatch.setattr(readiness_module.sqlite3, "connect", TrackingConnection)

    truth = readiness_module._read_supersedes_graph_readonly(brain / "index.db")

    assert truth.status == "available"
    assert truth.edges == frozenset({edge})
