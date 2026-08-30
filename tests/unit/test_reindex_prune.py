"""P3-2: reindex --prune drops orphan index rows; verify diffs md vs index.

Before the fix `reindex` only upserts md items and never removes index rows
whose md file is gone, so deleted/archived items linger as ghost search hits
and there is no reconcile/verify capability.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_brain.interfaces.cli import app
from agent_brain.interfaces.cli.commands import maintenance
from agent_brain.interfaces.cli.commands.index_maintenance import IndexRepairResult
from agent_brain.memory.governance.index_health import build_index_health
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.store.pending import DirtyIndexMarker, dirty_index_path
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

runner = CliRunner()

LIVE_ID = "mem-20260528-100000-live"
GHOST_ID = "mem-20260528-100000-ghost"


def _make_item(item_id: str, title: str) -> tuple[MemoryItem, str]:
    item = MemoryItem(
        id=item_id,
        type=MemoryType.fact,
        created_at=datetime(2026, 5, 28, tzinfo=timezone.utc),
        title=title,
        summary=f"{title} summary mypy",
    )
    return item, f"{title} body"


@pytest.fixture
def brain_with_ghost(tmp_brain_dir: Path, monkeypatch):
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")
    store = ItemsStore(tmp_brain_dir / "items")
    live, live_body = _make_item(LIVE_ID, "live one")
    store.write(live, live_body)
    idx = HubIndex(db_path=tmp_brain_dir / "index.db")
    idx.upsert(live, live_body, embedding=None)
    ghost, ghost_body = _make_item(GHOST_ID, "ghost one")
    idx.upsert(ghost, ghost_body, embedding=None)
    idx.connection.close()
    return tmp_brain_dir


def _all_ids(brain: Path) -> set[str]:
    idx = HubIndex(db_path=brain / "index.db")
    try:
        return idx.all_ids()
    finally:
        idx.connection.close()


def test_index_maintenance_helpers_are_split():
    from agent_brain.interfaces.cli.commands import maintenance
    from agent_brain.interfaces.cli.commands.index_maintenance import inspect_index_drift

    assert maintenance.inspect_index_drift is inspect_index_drift


def test_reindex_without_prune_keeps_ghost(brain_with_ghost: Path):
    result = runner.invoke(app, ["reindex"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "reindexed 1 items"
    assert GHOST_ID in _all_ids(brain_with_ghost)


def test_reindex_prune_drops_ghost(brain_with_ghost: Path):
    result = runner.invoke(app, ["reindex", "--prune"])
    assert result.exit_code == 0, result.output
    assert "pruned 1" in result.output
    ids = _all_ids(brain_with_ghost)
    assert GHOST_ID not in ids
    assert LIVE_ID in ids


def test_verify_reports_orphan_and_exits_nonzero(brain_with_ghost: Path):
    result = runner.invoke(app, ["verify"])
    assert result.exit_code == 1
    assert "orphan index rows: 1" in result.output
    assert GHOST_ID in result.output


def test_verify_repair_then_clean(brain_with_ghost: Path):
    repair = runner.invoke(app, ["verify", "--repair"])
    assert repair.exit_code == 0, repair.output
    assert "pruned 1 orphans" in repair.output
    check = runner.invoke(app, ["verify"])
    assert check.exit_code == 0, check.output
    assert "index in sync" in check.output


def test_verify_fails_when_ids_match_but_dirty_marker_remains(
    tmp_brain_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    item, body = _make_item(LIVE_ID, "dirty live")
    store = ItemsStore(tmp_brain_dir / "items")
    store.write(item, body)
    index = HubIndex(tmp_brain_dir / "index.db")
    index.upsert(item, body, embedding=None)
    index.close()
    dirty_index_path(tmp_brain_dir).write_text(f"{item.id}\n", encoding="utf-8")

    result = runner.invoke(app, ["verify"])

    assert result.exit_code == 1
    assert "index in sync" not in result.output
    assert "dirty marker: repair_required" in result.output


def test_verify_fails_for_graph_only_supersession_without_printing_edge_ids(
    tmp_brain_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = ItemsStore(tmp_brain_dir / "items")
    source, source_body = _make_item(
        "mem-20260720-140003-graph-source", "graph source"
    )
    target, target_body = _make_item(
        "mem-20260720-140003-graph-target", "graph target"
    )
    store.write(source, source_body)
    store.write(target, target_body)
    index = HubIndex(tmp_brain_dir / "index.db")
    index.upsert(source, source_body, embedding=None)
    index.upsert(target, target_body, embedding=None)
    index.add_ref(source.id, target.id, "supersedes")
    index.close()

    result = runner.invoke(app, ["verify"])

    assert result.exit_code == 1
    assert "graph-only: 1" in result.output
    assert source.id not in result.output
    assert target.id not in result.output


def test_verify_json_is_readonly_and_low_sensitivity(
    tmp_brain_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = ItemsStore(tmp_brain_dir / "items")
    item, body = _make_item(
        "mem-20260720-140004-json-private", "private json source"
    )
    store.write(item, body)
    index = HubIndex(tmp_brain_dir / "index.db")
    index.upsert(item, body, embedding=None)
    index.close()
    monkeypatch.setattr(
        maintenance._cli,
        "_managed_components",
        lambda: (_ for _ in ()).throw(AssertionError("managed write open")),
    )

    result = runner.invoke(app, ["verify", "--format", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert set(payload) == {
        "schema_version",
        "status",
        "reason",
        "repair_required",
        "source_scan_trusted",
        "items",
        "dirty_marker",
        "supersession",
    }
    assert "mem-" not in result.output


def test_verify_rejects_unknown_format_without_opening_index(
    tmp_brain_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    monkeypatch.setattr(
        maintenance,
        "collect_index_health_readonly",
        lambda _brain: (_ for _ in ()).throw(AssertionError("collector opened")),
        raising=False,
    )

    result = runner.invoke(app, ["verify", "--format", "yaml"])

    assert result.exit_code == 2
    assert "format must be text or json" in result.output


def test_verify_repair_exits_nonzero_when_after_report_is_not_clean(
    tmp_brain_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    before = build_index_health(
        md_ids={"mem-20260720-140020-after-missing"},
        index_ids=set(),
        expected_supersedes=set(),
        indexed_supersedes=set(),
        source_scan_trusted=True,
        graph_status="available",
        dirty_marker=DirtyIndexMarker("clean"),
    )
    reports = iter((before, before))
    monkeypatch.setattr(
        maintenance,
        "collect_index_health_readonly",
        lambda _brain: next(reports),
    )
    monkeypatch.setattr(
        maintenance,
        "repair_index_health",
        lambda *_args, **_kwargs: IndexRepairResult(
            upserted=1,
            pruned=0,
            supersedes_deleted=0,
            supersedes_inserted=0,
            marker_entries_cleared=0,
        ),
        raising=False,
    )

    result = runner.invoke(app, ["verify", "--repair", "--format", "json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert set(payload) == {"schema_version", "before", "repair", "after"}
    assert payload["before"]["status"] == "repair_required"
    assert payload["after"]["status"] == "repair_required"


def test_verify_repair_clean_noop_does_not_open_write_components(
    tmp_brain_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    clean = build_index_health(
        md_ids=set(),
        index_ids=set(),
        expected_supersedes=set(),
        indexed_supersedes=set(),
        source_scan_trusted=True,
        graph_status="available",
        dirty_marker=DirtyIndexMarker("clean"),
    )
    monkeypatch.setattr(
        maintenance,
        "collect_index_health_readonly",
        lambda _brain: clean,
    )
    monkeypatch.setattr(
        maintenance._cli,
        "_managed_components",
        lambda: (_ for _ in ()).throw(AssertionError("write open")),
    )

    result = runner.invoke(app, ["verify", "--repair"])

    assert result.exit_code == 0
    assert "index in sync" in result.output


def test_verify_repair_retired_marker_json_is_lazy_and_idempotent(
    tmp_brain_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    index = HubIndex(tmp_brain_dir / "index.db")
    index.close()
    retired = "mem-20260720-140021-cli-retired"
    dirty_index_path(tmp_brain_dir).write_text(
        f"{retired}\n{retired}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        maintenance._cli,
        "get_default_embedder",
        lambda: (_ for _ in ()).throw(AssertionError("embedder opened")),
    )
    monkeypatch.setattr(
        maintenance,
        "HubIndex",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("index opened")),
    )

    first = runner.invoke(app, ["verify", "--repair", "--format", "json"])
    second = runner.invoke(app, ["verify", "--repair", "--format", "json"])

    assert first.exit_code == 0, first.output
    first_payload = json.loads(first.output)
    assert first_payload["repair"] == {
        "upserted": 0,
        "pruned": 0,
        "supersedes_deleted": 0,
        "supersedes_inserted": 0,
        "marker_entries_cleared": 2,
    }
    assert first_payload["after"]["status"] == "clean"
    assert second.exit_code == 0, second.output
    second_payload = json.loads(second.output)
    assert second_payload["before"]["status"] == "clean"
    assert all(value == 0 for value in second_payload["repair"].values())
    assert second_payload["after"]["status"] == "clean"


def test_verify_repair_untrusted_preflight_is_zero_write(
    tmp_brain_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    unavailable = build_index_health(
        md_ids=set(),
        index_ids=set(),
        expected_supersedes=set(),
        indexed_supersedes=set(),
        source_scan_trusted=True,
        graph_status="unavailable",
        dirty_marker=DirtyIndexMarker("clean"),
    )
    monkeypatch.setattr(
        maintenance,
        "collect_index_health_readonly",
        lambda _brain: unavailable,
    )
    monkeypatch.setattr(
        maintenance,
        "HubIndex",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("write open")),
    )

    result = runner.invoke(app, ["verify", "--repair", "--format", "json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["before"]["status"] == "unavailable"
    assert payload["repair"] is None
    assert payload["after"] is None


def test_verify_repair_graph_only_uses_narrow_sqlite_scope(
    tmp_brain_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = ItemsStore(tmp_brain_dir / "items")
    source, source_body = _make_item(
        "mem-20260720-140022-narrow-source", "narrow source"
    )
    target, target_body = _make_item(
        "mem-20260720-140022-narrow-target", "narrow target"
    )
    store.write(source, source_body)
    store.write(target, target_body)
    index = HubIndex(tmp_brain_dir / "index.db")
    index.upsert(source, source_body, embedding=None)
    index.upsert(target, target_body, embedding=None)
    index.add_ref(source.id, target.id, "supersedes")
    index.close()
    monkeypatch.setattr(
        maintenance,
        "HubIndex",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("broad index opened")),
    )
    monkeypatch.setattr(
        maintenance._cli,
        "get_default_embedder",
        lambda: (_ for _ in ()).throw(AssertionError("embedder opened")),
    )

    result = runner.invoke(app, ["verify", "--repair", "--format", "json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["repair"]["supersedes_deleted"] == 1
    assert payload["repair"]["supersedes_inserted"] == 0
    assert payload["after"]["status"] == "clean"
