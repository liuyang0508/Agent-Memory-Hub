from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.memory.governance.lifecycle_ledger import (
    LifecycleLedgerRecord,
    append_lifecycle_record,
)
from agent_brain.memory.governance.lifecycle_archive import archive_reviewed_item
from agent_brain.memory.governance.lifecycle_review import (
    LifecycleReviewAction,
    apply_lifecycle_review_actions,
    apply_lifecycle_review_items,
    build_lifecycle_review_plan,
)
from agent_brain.memory.governance.supersession import SupersessionService
from agent_brain.memory.store.items_store import ItemsStore


def _stale_item(item_id: str, *, now: datetime) -> MemoryItem:
    return MemoryItem(
        id=item_id,
        type=MemoryType.signal,
        created_at=now - timedelta(days=60),
        title=f"Stale {item_id}",
        summary=f"Stale {item_id}",
        tags=["runtime"],
    )


def _tree_snapshot(root: Path) -> dict[str, tuple[str, bytes | None]]:
    snapshot: dict[str, tuple[str, bytes | None]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = ("symlink", str(path.readlink()).encode())
        elif path.is_dir():
            snapshot[relative] = ("dir", None)
        else:
            snapshot[relative] = ("file", path.read_bytes())
    return snapshot


def test_lifecycle_preview_does_not_create_conversation_source_directories(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    item = _stale_item("mem-20260101-180001-preview-tree", now=now)
    store.write(item, "body")
    before = _tree_snapshot(brain)

    payload = apply_lifecycle_review_actions(
        brain_dir=brain,
        items_store=store,
        actions=[LifecycleReviewAction("archive", item.id)],
        apply=False,
        index_repair=True,
    )

    assert payload["results"][0]["status"] == "ready"
    assert _tree_snapshot(brain) == before


def test_lifecycle_queue_hides_future_defer_and_restores_at_deadline(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    item = _stale_item("mem-20260101-180002-defer-clock", now=now)
    store.write(item, "body")
    append_lifecycle_record(
        brain,
        LifecycleLedgerRecord(
            action="defer",
            timestamp=(now - timedelta(hours=1)).isoformat(),
            status="deferred",
            reason="OK",
            obsolete_id=item.id,
            replacement_id=None,
            snapshot=None,
            replacement_ref_preexisted=False,
            deferred_until=(now + timedelta(days=2)).isoformat(),
        ),
    )

    hidden = build_lifecycle_review_plan(
        brain_dir=brain,
        items_store=store,
        now=now,
    )
    restored = build_lifecycle_review_plan(
        brain_dir=brain,
        items_store=store,
        now=now + timedelta(days=2),
    )

    assert hidden.review_queue == []
    assert [row.item_id for row in restored.review_queue] == [item.id]


def test_lifecycle_queue_later_successful_review_action_supersedes_defer(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    item = _stale_item("mem-20260101-180003-defer-overridden", now=now)
    replacement = _stale_item("mem-20260102-180004-review-replacement", now=now)
    store.write(item, "body")
    store.write(replacement, "replacement")
    append_lifecycle_record(
        brain,
        LifecycleLedgerRecord(
            action="defer",
            timestamp=(now - timedelta(hours=2)).isoformat(),
            status="deferred",
            reason="OK",
            obsolete_id=item.id,
            replacement_id=None,
            snapshot=None,
            replacement_ref_preexisted=False,
            deferred_until=(now + timedelta(days=2)).isoformat(),
        ),
    )
    append_lifecycle_record(
        brain,
        LifecycleLedgerRecord(
            action="supersede",
            timestamp=(now - timedelta(hours=1)).isoformat(),
            status="applied",
            reason="OK",
            obsolete_id=item.id,
            replacement_id=replacement.id,
            snapshot=None,
            replacement_ref_preexisted=False,
        ),
    )

    plan = build_lifecycle_review_plan(
        brain_dir=brain,
        items_store=store,
        now=now,
    )

    assert item.id in {row.item_id for row in plan.review_queue}


def test_lifecycle_queue_corrupt_ledger_fails_safe_without_writing(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    item = _stale_item("mem-20260101-180005-defer-corrupt", now=now)
    store.write(item, "body")
    runtime = brain / "runtime"
    runtime.mkdir()
    ledger = runtime / "lifecycle-actions.jsonl"
    ledger.write_text('{"action":"defer"}\nnot-json\n', encoding="utf-8")
    before = _tree_snapshot(brain)

    plan = build_lifecycle_review_plan(
        brain_dir=brain,
        items_store=store,
        now=now,
    )

    assert [row.item_id for row in plan.review_queue] == [item.id]
    assert _tree_snapshot(brain) == before


def test_structural_invalid_action_blocks_entire_batch_before_mutation(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    item = _stale_item("mem-20260101-180006-batch-valid", now=now)
    store.write(item, "body")
    before = (store.items_dir / f"{item.id}.md").read_bytes()

    payload = apply_lifecycle_review_actions(
        brain_dir=brain,
        items_store=store,
        actions=[
            LifecycleReviewAction("keep-active", item.id),
            LifecycleReviewAction("archive", "../invalid"),
        ],
        apply=True,
        index_repair=False,
    )

    assert [row["reason"] for row in payload["results"]] == [
        "BATCH_VALIDATION_FAILED",
        "INVALID_ITEM_ID",
    ]
    assert (store.items_dir / f"{item.id}.md").read_bytes() == before
    assert not (brain / "runtime").exists()


def test_legacy_archive_reports_index_delete_failure_without_exception_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_brain.memory.governance import lifecycle_review

    now = datetime.now(timezone.utc)
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    item = _stale_item("mem-20260101-180007-index-failure", now=now)
    store.write(item, "body")

    class FailingIndex:
        def delete(self, _item_id: str) -> None:
            raise RuntimeError("SECRET_INDEX_EXCEPTION")

        def close(self) -> None:
            return None

    monkeypatch.setattr(lifecycle_review, "_open_index", lambda _brain: FailingIndex())

    payload = apply_lifecycle_review_items(
        brain_dir=brain,
        items_store=store,
        item_ids=[item.id],
        apply=True,
        index_repair=True,
    )

    assert payload["results"][0]["status"] == "applied"
    assert payload["results"][0]["index_repair_required"] is True
    assert payload["failed"] == [{"id": item.id, "reason": "INDEX_DELETE_FAILED"}]
    assert "SECRET_INDEX_EXCEPTION" not in json.dumps(payload)


def test_index_close_failure_does_not_taint_unrelated_blocked_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_brain.memory.governance import lifecycle_review

    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")

    class CloseFailingIndex:
        def close(self) -> None:
            raise OSError("close failed")

    monkeypatch.setattr(
        lifecycle_review,
        "_open_index",
        lambda _brain: CloseFailingIndex(),
    )

    payload = apply_lifecycle_review_actions(
        brain_dir=brain,
        items_store=store,
        actions=[
            LifecycleReviewAction(
                "keep-active",
                "mem-20260101-180008-missing-item",
            )
        ],
        apply=True,
        index_repair=True,
    )

    assert payload["results"][0]["reason"] == "ITEM_MISSING"
    assert payload["results"][0]["index_repair_required"] is False


def test_keep_active_updates_markdown_and_index_inside_lifecycle_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_brain.memory.governance import lifecycle_review

    now = datetime.now(timezone.utc)
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    item = _stale_item("mem-20260101-180013-keep-index-lock", now=now)
    store.write(item, "body")
    state = {"locked": False}
    indexed: list[tuple[MemoryItem, str]] = []

    @contextmanager
    def tracked_lock(_brain_dir: Path):
        assert state["locked"] is False
        state["locked"] = True
        try:
            yield
        finally:
            state["locked"] = False

    class RecordingIndex:
        def upsert(self, updated: MemoryItem, body: str, *, embedding) -> None:
            assert state["locked"] is True
            assert embedding is None
            indexed.append((updated, body))

        def close(self) -> None:
            return None

    monkeypatch.setattr(lifecycle_review, "lifecycle_transaction_lock", tracked_lock)
    monkeypatch.setattr(lifecycle_review, "_open_index", lambda _brain: RecordingIndex())

    payload = apply_lifecycle_review_actions(
        brain_dir=brain,
        items_store=store,
        actions=[LifecycleReviewAction("keep-active", item.id)],
        apply=True,
        index_repair=True,
    )

    persisted, persisted_body = store.get(item.id)
    assert payload["results"][0]["status"] == "applied"
    assert indexed == [(persisted, persisted_body)]
    assert persisted.validity.observed_at is not None


def test_supersession_symlink_source_is_structured_blocked_in_preview_and_apply(
    tmp_path: Path,
) -> None:
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    old = MemoryItem(
        id="mem-20260101-180009-symlink-old",
        type=MemoryType.signal,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        project="amh",
        title="Old",
        summary="Old",
    )
    new = MemoryItem(
        id="mem-20260701-180010-symlink-new",
        type=MemoryType.signal,
        created_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        project="amh",
        title="New",
        summary="New",
    )
    external_store = ItemsStore(tmp_path / "external")
    external = external_store.write(old, "EXTERNAL_SECRET")
    store.write(new, "new body")
    (store.items_dir / f"{old.id}.md").symlink_to(external)
    external_before = external.read_bytes()
    service = SupersessionService(brain, store)

    preview = service.preview(new.id, old.id)
    applied = service.apply(new.id, old.id, apply=True)

    assert preview.status == "blocked"
    assert applied.status == "blocked"
    assert preview.reason == applied.reason == "ITEM_INVALID"
    assert preview.dry_run is True
    assert applied.dry_run is False
    assert external.read_bytes() == external_before


def test_archive_does_not_archive_stale_descriptor_after_source_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_brain.memory.governance import lifecycle_archive

    now = datetime.now(timezone.utc)
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    item = _stale_item("mem-20260101-180011-archive-source-race", now=now)
    source = store.write(item, "original body")
    replacement_store = ItemsStore(tmp_path / "replacement")
    replacement_path = replacement_store.write(item, "concurrent replacement")
    replacement_bytes = replacement_path.read_bytes()
    original_bytes = source.read_bytes()
    displaced = tmp_path / "displaced-original.md"
    original_rename = os.rename
    state = {"replaced": False}

    def replace_before_stage(source_name, destination_name, *args, **kwargs):
        if source_name == f"{item.id}.md" and not state["replaced"]:
            state["replaced"] = True
            source_fd = kwargs["src_dir_fd"]
            original_rename(source_name, displaced, src_dir_fd=source_fd)
            descriptor = os.open(
                source_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=source_fd,
            )
            try:
                os.write(descriptor, replacement_bytes)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return original_rename(source_name, destination_name, *args, **kwargs)

    monkeypatch.setattr(lifecycle_archive.os, "rename", replace_before_stage)
    monkeypatch.setattr(
        lifecycle_archive.durable_fs,
        "lifecycle_mutation_capability",
        lambda: True,
    )

    result = archive_reviewed_item(
        brain_dir=brain,
        items_store=store,
        item_id=item.id,
        eligible=lambda _item: True,
    )

    assert result.status == "blocked"
    assert result.reason == "CONCURRENT_MODIFICATION"
    assert source.read_bytes() == replacement_bytes
    assert displaced.read_bytes() == original_bytes
    assert not (store.items_dir / "archived" / source.name).exists()


def test_archive_never_overwrites_target_created_during_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_brain.memory.governance import lifecycle_archive

    now = datetime.now(timezone.utc)
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    item = _stale_item("mem-20260101-180012-archive-target-race", now=now)
    source = store.write(item, "original body")
    original_bytes = source.read_bytes()
    concurrent_bytes = b"concurrent archive target"
    original_link = os.link
    state = {"published": False}

    def create_target_before_link(source_name, destination_name, *args, **kwargs):
        if not state["published"]:
            state["published"] = True
            target_fd = kwargs["dst_dir_fd"]
            descriptor = os.open(
                destination_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=target_fd,
            )
            try:
                os.write(descriptor, concurrent_bytes)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return original_link(source_name, destination_name, *args, **kwargs)

    monkeypatch.setattr(lifecycle_archive.os, "link", create_target_before_link)

    result = archive_reviewed_item(
        brain_dir=brain,
        items_store=store,
        item_id=item.id,
        eligible=lambda _item: True,
    )

    target = store.items_dir / "archived" / source.name
    assert result.status == "blocked"
    assert result.reason == "ARCHIVE_TARGET_EXISTS"
    assert source.read_bytes() == original_bytes
    assert target.read_bytes() == concurrent_bytes
