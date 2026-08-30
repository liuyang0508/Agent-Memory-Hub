from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.memory.governance.lifecycle_ledger import (
    LifecycleLedgerRecord,
    append_lifecycle_record,
)
from agent_brain.memory.governance.lifecycle_archive import (
    ArchiveTransactionResult,
    archive_reviewed_item,
)
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


def test_lifecycle_surfaces_skip_fifo_without_blocking_or_writing_it(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    regular = _stale_item("mem-20260101-180026-fifo-plan-regular", now=now)
    fifo_id = "mem-20260101-180027-fifo-lifecycle"
    store.write(regular, "body")
    fifo = store.items_dir / f"{fifo_id}.md"
    os.mkfifo(fifo)
    script = (
        "import json; from pathlib import Path; "
        "from agent_brain.memory.store.items_store import ItemsStore; "
        "from agent_brain.memory.governance.lifecycle_review import "
        "LifecycleReviewAction,apply_lifecycle_review_actions,build_lifecycle_review_plan; "
        f"brain=Path({str(brain)!r}); store=ItemsStore(brain/'items'); "
        "plan=build_lifecycle_review_plan(brain_dir=brain,items_store=store); "
        f"archive=apply_lifecycle_review_actions(brain_dir=brain,items_store=store,actions=[LifecycleReviewAction('archive',{fifo_id!r})],apply=False,index_repair=False); "
        f"keep=apply_lifecycle_review_actions(brain_dir=brain,items_store=store,actions=[LifecycleReviewAction('keep-active',{fifo_id!r})],apply=False,index_repair=False); "
        f"defer=apply_lifecycle_review_actions(brain_dir=brain,items_store=store,actions=[LifecycleReviewAction('defer',{fifo_id!r},defer_days=7)],apply=False,index_repair=False); "
        "print(json.dumps({'queue':[x.item_id for x in plan.review_queue],"
        "'archive':archive['results'][0], 'keep':keep['results'][0], "
        "'defer':defer['results'][0]}))"
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        text=True,
        capture_output=True,
        timeout=3,
        check=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["queue"] == [regular.id]
    assert payload["archive"]["reason"] == "NOT_IN_LIFECYCLE_REVIEW_QUEUE"
    assert payload["keep"]["reason"] == "ITEM_INVALID"
    assert payload["defer"]["reason"] == "ITEM_INVALID"
    assert stat.S_ISFIFO(fifo.stat().st_mode)
    assert not (brain / "runtime" / "lifecycle-actions.jsonl").exists()


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


def test_keep_active_supersedes_long_defer_and_item_reenters_queue_after_32_days(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    item = _stale_item("mem-20260101-180014-keep-overrides-defer", now=now)
    store.write(item, "body")
    append_lifecycle_record(
        brain,
        LifecycleLedgerRecord(
            action="defer",
            timestamp=(now - timedelta(minutes=1)).isoformat(),
            status="deferred",
            reason="OK",
            obsolete_id=item.id,
            replacement_id=None,
            snapshot=None,
            replacement_ref_preexisted=False,
            deferred_until=(now + timedelta(days=365)).isoformat(),
        ),
    )

    payload = apply_lifecycle_review_actions(
        brain_dir=brain,
        items_store=store,
        actions=[LifecycleReviewAction("keep-active", item.id)],
        apply=True,
        index_repair=False,
    )

    kept, _ = store.get(item.id)
    assert payload["results"][0]["status"] == "applied"
    assert kept.validity.observed_at is not None
    plan = build_lifecycle_review_plan(
        brain_dir=brain,
        items_store=store,
        now=kept.validity.observed_at + timedelta(days=32),
    )
    assert [row.item_id for row in plan.review_queue] == [item.id]
    ledger_rows = [
        json.loads(line)
        for line in (brain / "runtime" / "lifecycle-actions.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert ledger_rows[-1]["action"] == "keep-active"
    assert ledger_rows[-1]["status"] == "applied"


def test_keep_active_rolls_back_markdown_when_ledger_append_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_brain.memory.governance import lifecycle_review

    now = datetime.now(timezone.utc)
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    item = _stale_item("mem-20260101-180015-keep-ledger-failure", now=now)
    source = store.write(item, "body")
    original = source.read_bytes()
    monkeypatch.setattr(
        lifecycle_review,
        "append_lifecycle_record",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("ledger failed")),
    )

    payload = apply_lifecycle_review_actions(
        brain_dir=brain,
        items_store=store,
        actions=[LifecycleReviewAction("keep-active", item.id)],
        apply=True,
        index_repair=False,
    )

    assert payload["results"][0]["status"] == "blocked"
    assert payload["results"][0]["reason"] == "LEDGER_WRITE_FAILED"
    assert source.read_bytes() == original


def test_keep_active_ledger_failure_never_overwrites_concurrent_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_brain.memory.governance import lifecycle_review

    now = datetime.now(timezone.utc)
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    item = _stale_item("mem-20260101-180028-keep-concurrent-ledger", now=now)
    source = store.write(item, "original")
    replacement_store = ItemsStore(tmp_path / "keep-concurrent-replacement")
    fresh_bytes = replacement_store.write(item, "concurrent fresh").read_bytes()

    def replace_then_fail(*_args, **_kwargs) -> None:
        source.unlink()
        source.write_bytes(fresh_bytes)
        raise OSError("ledger failed after external replacement")

    monkeypatch.setattr(
        lifecycle_review,
        "append_lifecycle_record",
        replace_then_fail,
    )

    payload = apply_lifecycle_review_actions(
        brain_dir=brain,
        items_store=store,
        actions=[LifecycleReviewAction("keep-active", item.id)],
        apply=True,
        index_repair=False,
    )

    result = payload["results"][0]
    assert result["status"] == "partial"
    assert result["reason"] == "CONCURRENT_MODIFICATION"
    assert result["index_repair_required"] is True
    assert source.read_bytes() == fresh_bytes


def test_lifecycle_queue_corrupt_ledger_fails_safe_without_writing(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    item = _stale_item("mem-20260101-180005-defer-corrupt", now=now)
    store.write(item, "body")
    runtime = brain / "runtime"
    runtime.mkdir(exist_ok=True)
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
    assert not (brain / "runtime" / "lifecycle-actions.jsonl").exists()


@pytest.mark.parametrize("action_name", ["supersede", "revert-supersession"])
def test_self_relation_blocks_whole_batch_before_preceding_keep_active(
    tmp_path: Path,
    action_name: str,
) -> None:
    now = datetime.now(timezone.utc)
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    kept = _stale_item("mem-20260101-180016-preflight-kept", now=now)
    related = _stale_item("mem-20260101-180017-preflight-self", now=now)
    kept_path = store.write(kept, "kept")
    store.write(related, "related")
    original = kept_path.read_bytes()

    payload = apply_lifecycle_review_actions(
        brain_dir=brain,
        items_store=store,
        actions=[
            LifecycleReviewAction("keep-active", kept.id),
            LifecycleReviewAction(action_name, related.id, related.id),
        ],
        apply=True,
        index_repair=False,
    )

    assert [row["reason"] for row in payload["results"]] == [
        "BATCH_VALIDATION_FAILED",
        "SELF_SUPERSESSION",
    ]
    assert kept_path.read_bytes() == original
    assert not (brain / "runtime" / "lifecycle-actions.jsonl").exists()


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


def test_supersession_fifo_source_is_nonblocking_structured_item_invalid(
    tmp_path: Path,
) -> None:
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    old_id = "mem-20260101-180018-fifo-old"
    new = _stale_item(
        "mem-20260701-180019-fifo-replacement",
        now=datetime.now(timezone.utc),
    )
    store.write(new, "new")
    os.mkfifo(store.items_dir / f"{old_id}.md")
    script = (
        "import json; from pathlib import Path; "
        "from agent_brain.memory.store.items_store import ItemsStore; "
        "from agent_brain.memory.governance.supersession import SupersessionService; "
        f"brain=Path({str(brain)!r}); "
        f"result=SupersessionService(brain, ItemsStore(brain/'items')).preview({new.id!r}, {old_id!r}); "
        "print(json.dumps(result.to_dict()))"
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        text=True,
        capture_output=True,
        timeout=2,
        check=True,
    )
    result = json.loads(completed.stdout)

    assert result["status"] == "blocked"
    assert result["reason"] == "ITEM_INVALID"


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
    from agent_brain.memory.store.durable_fs import SecureDirectory

    original_open_file = SecureDirectory.open_file
    state = {"replaced": False}

    def replace_before_target(
        directory,
        name,
        flags,
        mode=0o600,
        *,
        exclusive=False,
    ):
        if name == source.name and exclusive and not state["replaced"]:
            state["replaced"] = True
            source.rename(displaced)
            source.write_bytes(replacement_bytes)
        return original_open_file(
            directory, name, flags, mode, exclusive=exclusive
        )

    monkeypatch.setattr(SecureDirectory, "open_file", replace_before_target)

    result = archive_reviewed_item(
        brain_dir=brain,
        items_store=store,
        item_id=item.id,
        eligible=lambda _item: True,
    )

    assert result.status == "partial"
    assert result.reason == "ARCHIVE_SOURCE_REPLACED"
    assert source.read_bytes() == replacement_bytes
    assert displaced.read_bytes() == original_bytes
    assert (store.items_dir / "archived" / source.name).read_bytes() == original_bytes


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
    from agent_brain.memory.store.durable_fs import SecureDirectory

    original_open_file = SecureDirectory.open_file
    state = {"published": False}

    def create_target_before_exclusive_open(
        directory,
        name,
        flags,
        mode=0o600,
        *,
        exclusive=False,
    ):
        if name == source.name and exclusive and not state["published"]:
            state["published"] = True
            descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=directory.fd,
            )
            try:
                os.write(descriptor, concurrent_bytes)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return original_open_file(
            directory, name, flags, mode, exclusive=exclusive
        )

    monkeypatch.setattr(
        SecureDirectory,
        "open_file",
        create_target_before_exclusive_open,
    )

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


def test_archive_rejects_same_inode_content_change_without_archiving_new_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_brain.memory.governance import lifecycle_archive

    now = datetime.now(timezone.utc)
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    item = _stale_item("mem-20260101-180020-archive-inode-write", now=now)
    source = store.write(item, "original body")
    replacement_store = ItemsStore(tmp_path / "replacement-inode")
    replacement = replacement_store.write(item, "same inode changed body").read_bytes()
    from agent_brain.memory.store.durable_fs import SecureDirectory

    original_bytes = source.read_bytes()
    original_open_file = SecureDirectory.open_file
    changed = False

    def mutate_before_target(
        directory,
        name,
        flags,
        mode=0o600,
        *,
        exclusive=False,
    ):
        nonlocal changed
        if name == source.name and exclusive and not changed:
            changed = True
            descriptor = os.open(source, os.O_WRONLY | os.O_TRUNC)
            try:
                os.write(descriptor, replacement)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return original_open_file(
            directory, name, flags, mode, exclusive=exclusive
        )

    monkeypatch.setattr(SecureDirectory, "open_file", mutate_before_target)

    result = archive_reviewed_item(
        brain_dir=brain,
        items_store=store,
        item_id=item.id,
        eligible=lambda _item: True,
    )

    assert result.status == "partial"
    assert result.reason == "ARCHIVE_SOURCE_REPLACED"
    assert source.read_bytes() == replacement
    assert (store.items_dir / "archived" / source.name).read_bytes() == original_bytes


def test_archive_recovers_linked_half_transaction_without_stage_directory(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    item = _stale_item("mem-20260101-180021-archive-linked-half", now=now)
    source = store.write(item, "body")
    archive = store.items_dir / "archived"
    archive.mkdir()
    target = archive / source.name
    target.write_bytes(source.read_bytes())
    target.chmod(stat.S_IMODE(source.stat().st_mode))

    result = archive_reviewed_item(
        brain_dir=brain,
        items_store=store,
        item_id=item.id,
        eligible=lambda _item: True,
    )

    assert result.status == "applied"
    assert result.reason == "OK"
    assert not source.exists()
    assert target.exists()
    assert not (store.items_dir / ".amh-lifecycle-stage").exists()


def test_archive_recognizes_completed_target_when_source_is_already_absent(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    item = _stale_item("mem-20260101-180023-archive-completed", now=now)
    source = store.write(item, "body")
    archive = store.items_dir / "archived"
    archive.mkdir()
    target = archive / source.name
    target.write_bytes(source.read_bytes())
    target.chmod(stat.S_IMODE(source.stat().st_mode))
    source.unlink()

    result = archive_reviewed_item(
        brain_dir=brain,
        items_store=store,
        item_id=item.id,
        eligible=lambda _item: True,
    )

    assert result.status == "already_applied"
    assert result.reason == "ALREADY_ARCHIVED"
    assert target.exists()


def test_archive_keeps_fresh_source_when_replaced_after_exclusive_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_brain.memory.governance import lifecycle_archive

    now = datetime.now(timezone.utc)
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    item = _stale_item("mem-20260101-180022-archive-post-link-swap", now=now)
    source = store.write(item, "old body")
    old_bytes = source.read_bytes()
    replacement_store = ItemsStore(tmp_path / "replacement-post-link")
    fresh_bytes = replacement_store.write(item, "fresh body").read_bytes()
    displaced = tmp_path / "linked-old.md"
    original_publish = lifecycle_archive._publish_independent_target
    swapped = False

    def replace_source_after_publish(**kwargs):
        nonlocal swapped
        result = original_publish(**kwargs)
        if not swapped:
            swapped = True
            source.rename(displaced)
            source.write_bytes(fresh_bytes)
        return result

    monkeypatch.setattr(
        lifecycle_archive,
        "_publish_independent_target",
        replace_source_after_publish,
    )

    result = archive_reviewed_item(
        brain_dir=brain,
        items_store=store,
        item_id=item.id,
        eligible=lambda _item: True,
    )

    target = store.items_dir / "archived" / source.name
    assert result.status == "partial"
    assert result.reason == "ARCHIVE_SOURCE_REPLACED"
    assert source.read_bytes() == fresh_bytes
    assert target.read_bytes() == old_bytes
    assert displaced.read_bytes() == old_bytes
    assert not (store.items_dir / ".amh-lifecycle-stage").exists()


def test_archive_keeps_fifo_that_replaces_source_after_target_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_brain.memory.governance import lifecycle_archive

    now = datetime.now(timezone.utc)
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    item = _stale_item("mem-20260101-180030-archive-post-publish-fifo", now=now)
    source = store.write(item, "old body")
    old_bytes = source.read_bytes()
    displaced = tmp_path / "post-publish-old.md"
    original_publish = lifecycle_archive._publish_independent_target

    def replace_source_with_fifo(**kwargs):
        result = original_publish(**kwargs)
        source.rename(displaced)
        os.mkfifo(source)
        return result

    monkeypatch.setattr(
        lifecycle_archive,
        "_publish_independent_target",
        replace_source_with_fifo,
    )

    result = archive_reviewed_item(
        brain_dir=brain,
        items_store=store,
        item_id=item.id,
        eligible=lambda _item: True,
    )

    target = store.items_dir / "archived" / source.name
    assert result.status == "partial"
    assert result.reason == "ARCHIVE_SOURCE_REPLACED"
    assert stat.S_ISFIFO(source.stat().st_mode)
    assert target.read_bytes() == old_bytes
    assert displaced.read_bytes() == old_bytes


def test_lifecycle_payload_reports_partial_archive_in_legacy_failed_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_brain.memory.governance import lifecycle_review

    now = datetime.now(timezone.utc)
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    item = _stale_item("mem-20260101-180025-archive-partial-payload", now=now)
    store.write(item, "body")
    monkeypatch.setattr(
        lifecycle_review,
        "archive_reviewed_item",
        lambda **_kwargs: ArchiveTransactionResult(
            "partial", "ARCHIVE_SOURCE_REPLACED"
        ),
    )

    payload = apply_lifecycle_review_actions(
        brain_dir=brain,
        items_store=store,
        actions=[LifecycleReviewAction("archive", item.id)],
        apply=True,
        index_repair=False,
    )

    assert payload["results"][0]["status"] == "partial"
    assert payload["failed"] == [
        {"id": item.id, "reason": "ARCHIVE_SOURCE_REPLACED"}
    ]


def test_archive_does_not_misreport_applied_when_fifo_appears_after_unlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_brain.memory.governance import lifecycle_archive

    now = datetime.now(timezone.utc)
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    item = _stale_item("mem-20260101-180024-archive-post-unlink-fifo", now=now)
    source = store.write(item, "body")
    original_unlink = os.unlink
    inserted = False

    def insert_fifo_after_unlink(path, *args, **kwargs):
        nonlocal inserted
        result = original_unlink(path, *args, **kwargs)
        if path == source.name and not inserted:
            inserted = True
            os.mkfifo(source)
        return result

    monkeypatch.setattr(lifecycle_archive.os, "unlink", insert_fifo_after_unlink)
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

    assert result.status == "partial"
    assert result.reason == "ARCHIVE_SOURCE_REPLACED"
    assert stat.S_ISFIFO(source.stat().st_mode)


def test_archive_target_is_independent_from_writer_fd_to_unlinked_source(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    item = _stale_item("mem-20260101-180029-archive-independent-copy", now=now)
    source = store.write(item, "original archive payload")
    original = source.read_bytes()
    writer = os.open(source, os.O_WRONLY)
    try:
        result = archive_reviewed_item(
            brain_dir=brain,
            items_store=store,
            item_id=item.id,
            eligible=lambda _item: True,
        )
        os.lseek(writer, 0, os.SEEK_SET)
        os.write(writer, b"CORRUPTED OLD INODE")
        os.fsync(writer)
    finally:
        os.close(writer)

    target = store.items_dir / "archived" / source.name
    assert result.status == "applied"
    assert not source.exists()
    assert target.read_bytes() == original
