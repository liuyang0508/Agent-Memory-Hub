from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs
from agent_brain.platform.indexing import index_writer as index_writer_module
from agent_brain.platform.indexing.index import HubIndex

META_INDEX = "idx_items_meta_superseded_by"
GRAPH_INDEX = "idx_refs_graph_target_relation"


def _item(suffix: str, *, refs: list[str] | None = None) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260720-120000-{suffix}",
        type=MemoryType.fact,
        created_at=datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc),
        title=f"Schema {suffix}",
        summary=f"Schema summary {suffix}",
        refs=Refs(mems=refs or []),
    )


def _index_names(index: HubIndex) -> set[str]:
    return {
        row[0]
        for row in index.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        ).fetchall()
    }


def test_supersession_query_indexes_are_created_for_new_and_existing_db(
    tmp_brain_dir: Path,
):
    db_path = tmp_brain_dir / "index.db"
    index = HubIndex(db_path, embedding_dim=8)
    assert {META_INDEX, GRAPH_INDEX} <= _index_names(index)
    index.connection.execute(f"DROP INDEX {META_INDEX}")
    index.connection.execute(f"DROP INDEX {GRAPH_INDEX}")
    index.connection.commit()
    index.close()

    reopened = HubIndex(db_path, embedding_dim=8)

    assert {META_INDEX, GRAPH_INDEX} <= _index_names(reopened)


def test_supersession_queries_use_schema_indexes(tmp_brain_dir: Path):
    index = HubIndex(tmp_brain_dir / "index.db", embedding_dim=8)

    meta_plan = index.connection.execute(
        "EXPLAIN QUERY PLAN SELECT id FROM items_meta WHERE superseded_by = ?",
        ("mem-20260720-120000-replacement",),
    ).fetchall()
    graph_plan = index.connection.execute(
        "EXPLAIN QUERY PLAN DELETE FROM refs_graph "
        "WHERE target_id = ? AND relation = 'supersedes'",
        ("mem-20260720-120000-obsolete",),
    ).fetchall()

    meta_detail = " ".join(str(row[3]) for row in meta_plan)
    graph_detail = " ".join(str(row[3]) for row in graph_plan)
    assert "SCAN items_meta" not in meta_detail
    assert META_INDEX in meta_detail
    assert "SCAN refs_graph" not in graph_detail
    assert GRAPH_INDEX in graph_detail


def test_target_metadata_is_loaded_in_bounded_batches(
    tmp_brain_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    index = HubIndex(tmp_brain_dir / "index.db", embedding_dim=8)
    targets = [_item(f"batch-target-{number}") for number in range(5)]
    for target in targets:
        index.upsert(target, target.summary, embedding=None)
    source = _item("batch-source", refs=[target.id for target in targets])
    monkeypatch.setattr(index_writer_module, "_sqlite_variable_limit", lambda _conn: 2)
    statements: list[str] = []
    index.connection.set_trace_callback(statements.append)

    index.upsert(source, source.summary, embedding=None)

    index.connection.set_trace_callback(None)
    metadata_queries = [
        statement
        for statement in statements
        if statement.startswith("SELECT id, superseded_by FROM items_meta")
    ]
    assert len(metadata_queries) == 3
    assert all(" WHERE id IN (" in statement for statement in metadata_queries)
    assert not any(
        statement.startswith("SELECT superseded_by FROM items_meta WHERE id =")
        for statement in statements
    )
