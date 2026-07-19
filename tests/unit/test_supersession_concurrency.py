import threading
from datetime import datetime, timezone
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.memory.governance.supersession import SupersessionService
from agent_brain.memory.store.items_store import ItemsStore


OLD_ID = "mem-20260719-100000-concurrency-old"
NEW_ID = "mem-20260719-110000-concurrency-new"


def _item(item_id: str) -> MemoryItem:
    return MemoryItem(
        id=item_id,
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc),
        title=item_id,
        summary=f"summary {item_id}",
        project="agent-memory-hub",
        tenant_id="tenant-a",
        tags=["lifecycle"],
    )


def _seed(brain_dir: Path) -> tuple[ItemsStore, MemoryItem, MemoryItem]:
    store = ItemsStore(brain_dir / "items")
    old = _item(OLD_ID)
    new = _item(NEW_ID)
    store.write(old, "old body")
    store.write(new, "new body")
    return store, old, new


def test_apply_revalidates_incompatible_managed_update_after_snapshot(
    tmp_brain_dir: Path, monkeypatch
) -> None:
    store, old, new = _seed(tmp_brain_dir)
    service = SupersessionService(tmp_brain_dir, store)
    real_snapshot = service._snapshot

    def snapshot_after_tenant_change(*args):
        store.update_frontmatter(old.id, tenant_id="tenant-b")
        return real_snapshot(*args)

    monkeypatch.setattr(service, "_snapshot", snapshot_after_tenant_change)

    result = service.apply(new.id, old.id, apply=True)

    assert result.status == "blocked"
    assert result.reason == "CONCURRENT_MODIFICATION"
    assert result.dry_run is False
    assert store.get(old.id)[0].tenant_id == "tenant-b"
    assert store.get(old.id)[0].superseded_by is None
    assert old.id not in store.get(new.id)[0].refs.mems


def test_apply_revalidates_same_scope_summary_update_after_snapshot(
    tmp_brain_dir: Path, monkeypatch
) -> None:
    store, old, new = _seed(tmp_brain_dir)
    service = SupersessionService(tmp_brain_dir, store)
    real_snapshot = service._snapshot

    def snapshot_after_summary_change(*args):
        snapshot = real_snapshot(*args)
        store.update_frontmatter(old.id, summary="managed concurrent summary")
        return snapshot

    monkeypatch.setattr(service, "_snapshot", snapshot_after_summary_change)

    result = service.apply(new.id, old.id, apply=True)

    assert result.status == "blocked"
    assert result.reason == "CONCURRENT_MODIFICATION"
    assert store.get(old.id)[0].summary == "managed concurrent summary"
    assert store.get(old.id)[0].superseded_by is None


def test_revert_revalidates_managed_update_after_snapshot(
    tmp_brain_dir: Path, monkeypatch
) -> None:
    store, old, new = _seed(tmp_brain_dir)
    service = SupersessionService(tmp_brain_dir, store)
    assert service.apply(new.id, old.id, apply=True).status == "applied"
    real_snapshot = service._snapshot

    def snapshot_after_summary_change(*args):
        snapshot = real_snapshot(*args)
        store.update_frontmatter(new.id, summary="revert concurrent summary")
        return snapshot

    monkeypatch.setattr(service, "_snapshot", snapshot_after_summary_change)

    result = service.revert(new.id, old.id, apply=True)

    assert result.status == "blocked"
    assert result.reason == "CONCURRENT_MODIFICATION"
    assert store.get(new.id)[0].summary == "revert concurrent summary"
    assert store.get(old.id)[0].superseded_by == new.id
    assert old.id in store.get(new.id)[0].refs.mems


def test_managed_writer_waits_for_apply_then_preserves_both_updates(
    tmp_brain_dir: Path, monkeypatch
) -> None:
    store, old, new = _seed(tmp_brain_dir)
    service = SupersessionService(tmp_brain_dir, store)
    real_snapshot = service._snapshot
    snapshot_entered = threading.Event()
    release_snapshot = threading.Event()
    writer_started = threading.Event()
    writer_done = threading.Event()
    results = {}

    def blocked_snapshot(*args):
        snapshot_entered.set()
        assert release_snapshot.wait(5)
        return real_snapshot(*args)

    def apply_worker():
        results["apply"] = service.apply(new.id, old.id, apply=True)

    def writer_worker():
        assert snapshot_entered.wait(5)
        writer_started.set()
        store.update_frontmatter(old.id, summary="writer after transaction")
        writer_done.set()

    monkeypatch.setattr(service, "_snapshot", blocked_snapshot)
    apply_thread = threading.Thread(target=apply_worker)
    writer_thread = threading.Thread(target=writer_worker)
    apply_thread.start()
    writer_thread.start()
    assert writer_started.wait(5)
    assert writer_done.wait(0.2) is False
    release_snapshot.set()
    apply_thread.join(10)
    writer_thread.join(10)

    assert not apply_thread.is_alive()
    assert not writer_thread.is_alive()
    assert results["apply"].status == "applied"
    updated = store.get(old.id)[0]
    assert updated.superseded_by == new.id
    assert updated.summary == "writer after transaction"


def test_apply_rollback_does_not_overwrite_waiting_managed_writer(
    tmp_brain_dir: Path, monkeypatch
) -> None:
    store, old, new = _seed(tmp_brain_dir)
    service = SupersessionService(tmp_brain_dir, store)
    second_write_entered = threading.Event()
    release_failure = threading.Event()
    writer_done = threading.Event()
    results = {}

    def fail_link(_source_id, _target_id):
        second_write_entered.set()
        assert release_failure.wait(5)
        raise OSError("second write failed")

    def apply_worker():
        results["apply"] = service.apply(new.id, old.id, apply=True)

    def writer_worker():
        assert second_write_entered.wait(5)
        store.update_frontmatter(old.id, summary="writer survived rollback")
        writer_done.set()

    monkeypatch.setattr(store, "link_mem", fail_link)
    apply_thread = threading.Thread(target=apply_worker)
    writer_thread = threading.Thread(target=writer_worker)
    apply_thread.start()
    writer_thread.start()
    assert second_write_entered.wait(5)
    assert writer_done.wait(0.2) is False
    release_failure.set()
    apply_thread.join(10)
    writer_thread.join(10)

    assert results["apply"].reason == "MARKDOWN_UPDATE_FAILED"
    updated = store.get(old.id)[0]
    assert updated.superseded_by is None
    assert updated.summary == "writer survived rollback"


def test_apply_rollback_refuses_to_overwrite_reentrant_managed_update(
    tmp_brain_dir: Path, monkeypatch
) -> None:
    store, old, new = _seed(tmp_brain_dir)
    service = SupersessionService(tmp_brain_dir, store)

    def mutate_then_fail(_source_id, _target_id):
        store.update_frontmatter(old.id, summary="third-party reentrant update")
        raise OSError("second write failed")

    monkeypatch.setattr(store, "link_mem", mutate_then_fail)

    result = service.apply(new.id, old.id, apply=True)

    assert result.status == "blocked"
    assert result.reason == "ROLLBACK_FAILED"
    updated = store.get(old.id)[0]
    assert updated.summary == "third-party reentrant update"
    assert updated.superseded_by == new.id


def test_snapshot_durability_failure_precedes_markdown_ledger_and_index(
    tmp_brain_dir: Path, monkeypatch
) -> None:
    store, old, new = _seed(tmp_brain_dir)

    class TrackingIndex:
        def __init__(self) -> None:
            self.calls = []

        def upsert(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    index = TrackingIndex()
    service = SupersessionService(tmp_brain_dir, store, index=index)
    old_before = (store.items_dir / f"{old.id}.md").read_bytes()
    new_before = (store.items_dir / f"{new.id}.md").read_bytes()

    def fail_barrier(_repo):
        raise OSError("injected nested fsync failure")

    monkeypatch.setattr(service.snapshot_store, "_fsync_repository", fail_barrier)

    result = service.apply(new.id, old.id, apply=True)

    assert result.status == "blocked"
    assert result.reason == "SNAPSHOT_FAILED"
    assert (store.items_dir / f"{old.id}.md").read_bytes() == old_before
    assert (store.items_dir / f"{new.id}.md").read_bytes() == new_before
    assert not (tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl").exists()
    assert index.calls == []
