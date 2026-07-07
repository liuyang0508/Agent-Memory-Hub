from agent_brain.platform.doctor import run_doctor


def test_doctor_offline_reports_writable_and_overall_ok(tmp_brain):
    rep = run_doctor(offline=True)
    assert rep.checks["core.md_store.writable"] is True
    assert rep.overall in ("OK", "DEGRADED")
    assert rep.exit_code in (0, 1)


def test_doctor_reports_pending_depth(tmp_brain):
    from agent_brain.memory.store.pending import enqueue_write_record
    enqueue_write_record({"op": "write", "item": {"title": "x", "summary": "s", "body": "b"}})
    rep = run_doctor(offline=True)
    assert rep.checks["pending.depth"] == 1


def test_doctor_reports_malformed_item_count(tmp_brain):
    bad = tmp_brain / "items" / "bad.md"
    bad.write_text("missing frontmatter\n", encoding="utf-8")

    rep = run_doctor(offline=True)

    assert rep.checks["core.items.skipped"] == 1
    assert rep.overall == "DEGRADED"
    assert rep.exit_code == 1


def test_doctor_reports_bounded_malformed_item_details(tmp_brain):
    bad = tmp_brain / "items" / "bad.md"
    bad.write_text("missing frontmatter\n", encoding="utf-8")

    rep = run_doctor(offline=True)

    assert rep.details["core.items.skipped"][0]["path"].endswith("bad.md")
    assert rep.details["core.items.skipped"][0]["reason"]


def test_malformed_repair_dry_run_does_not_move_file(tmp_brain):
    from agent_brain.memory.store.malformed_repair import quarantine_malformed_items

    bad = tmp_brain / "items" / "bad.md"
    bad.write_text("missing frontmatter\n", encoding="utf-8")

    report = quarantine_malformed_items(tmp_brain / "items", apply=False)

    assert report.found == 1
    assert report.moved == 0
    assert bad.exists()
    assert report.actions[0].destination.name == "bad.md"


def test_malformed_repair_apply_moves_file_and_records_reason(tmp_brain):
    from agent_brain.memory.store.malformed_repair import quarantine_malformed_items

    bad = tmp_brain / "items" / "bad.md"
    bad.write_text("missing frontmatter\n", encoding="utf-8")

    report = quarantine_malformed_items(tmp_brain / "items", apply=True)

    assert report.found == 1
    assert report.moved == 1
    assert not bad.exists()
    moved = tmp_brain / "items" / "archived" / "malformed" / "bad.md"
    assert moved.read_text(encoding="utf-8") == "missing frontmatter\n"
    assert (moved.with_suffix(moved.suffix + ".reason.txt")).exists()

    repaired = run_doctor(offline=True)
    assert repaired.checks["core.items.skipped"] == 0


def test_malformed_restore_dry_run_does_not_move_valid_file(tmp_brain):
    from datetime import datetime, timezone

    from agent_brain.memory.store.items_store import ItemsStore
    from agent_brain.memory.store.malformed_repair import restore_malformed_item
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType

    item = MemoryItem(
        id="mem-20260609-120000-restored",
        type=MemoryType.fact,
        created_at=datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc),
        title="Restored memory",
        summary="A manually repaired quarantined item",
        tags=["restore"],
    )
    archive_store = ItemsStore(tmp_brain / "items" / "archived" / "malformed")
    archived = archive_store.write(item, "restored body")

    report = restore_malformed_item(tmp_brain / "items", archived.name, apply=False)

    assert report.found == 1
    assert report.restored == 0
    assert archived.exists()
    assert not (tmp_brain / "items" / "mem-20260609-120000-restored.md").exists()
    assert report.actions[0].valid is True


def test_malformed_restore_apply_moves_valid_file_back_to_active_items(tmp_brain):
    from datetime import datetime, timezone

    from agent_brain.memory.store.items_store import ItemsStore
    from agent_brain.memory.store.malformed_repair import restore_malformed_item
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType

    item = MemoryItem(
        id="mem-20260609-120001-restored",
        type=MemoryType.fact,
        created_at=datetime(2026, 6, 9, 12, 1, tzinfo=timezone.utc),
        title="Restored memory",
        summary="A manually repaired quarantined item",
        tags=["restore"],
    )
    archive_store = ItemsStore(tmp_brain / "items" / "archived" / "malformed")
    archived = archive_store.write(item, "restored body")
    archived.with_suffix(archived.suffix + ".reason.txt").write_text("fixed manually\n", encoding="utf-8")

    report = restore_malformed_item(tmp_brain / "items", archived.name, apply=True)

    active = tmp_brain / "items" / "mem-20260609-120001-restored.md"
    assert report.found == 1
    assert report.restored == 1
    assert not archived.exists()
    assert active.exists()
    assert "restored body" in active.read_text(encoding="utf-8")
    assert not archived.with_suffix(archived.suffix + ".reason.txt").exists()


def test_malformed_restore_rejects_still_invalid_file(tmp_brain):
    from agent_brain.memory.store.malformed_repair import restore_malformed_item

    archive = tmp_brain / "items" / "archived" / "malformed" / "bad.md"
    archive.parent.mkdir(parents=True)
    archive.write_text("still missing frontmatter\n", encoding="utf-8")

    report = restore_malformed_item(tmp_brain / "items", archive.name, apply=True)

    assert report.found == 1
    assert report.restored == 0
    assert archive.exists()
    assert report.actions[0].valid is False
    assert "ValueError" in report.actions[0].reason


def test_malformed_restore_does_not_overwrite_active_item(tmp_brain):
    from datetime import datetime, timezone

    from agent_brain.memory.store.items_store import ItemsStore
    from agent_brain.memory.store.malformed_repair import restore_malformed_item
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType

    item = MemoryItem(
        id="mem-20260609-120002-duplicate",
        type=MemoryType.fact,
        created_at=datetime(2026, 6, 9, 12, 2, tzinfo=timezone.utc),
        title="Duplicate memory",
        summary="A manually repaired quarantined item",
        tags=["restore"],
    )
    active_store = ItemsStore(tmp_brain / "items")
    active = active_store.write(item, "active body")
    archive_store = ItemsStore(tmp_brain / "items" / "archived" / "malformed")
    archived = archive_store.write(item, "archived body")

    report = restore_malformed_item(tmp_brain / "items", archived.name, apply=True)

    assert report.found == 1
    assert report.restored == 0
    assert archived.exists()
    assert "active item already exists" in report.actions[0].reason
    assert "active body" in active.read_text(encoding="utf-8")


def test_malformed_restore_rejects_path_traversal(tmp_brain):
    import pytest

    from agent_brain.memory.store.malformed_repair import restore_malformed_item

    with pytest.raises(ValueError, match="archived_name must stay under"):
        restore_malformed_item(tmp_brain / "items", "../bad.md")
