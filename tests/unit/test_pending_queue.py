from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import sys
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from agent_brain.contracts.memory_item import MemoryItem, Source
from agent_brain.memory.store import pending as pending_module
from agent_brain.memory.store.item_markdown import render_item_markdown
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.store.pending import (
    PendingEnqueueError,
    PendingQueue,
    PendingResolutionAction,
    enqueue_write_record,
)
from agent_brain.platform.embedding import HashingEmbedder


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


class UnhashableStr(str):
    __hash__ = None  # type: ignore[assignment]


class UnhashablePendingResolutionAction(PendingResolutionAction):
    __hash__ = None  # type: ignore[assignment]


class _GuardedInfiniteActions:
    def __init__(self) -> None:
        self.reads = 0

    def __iter__(self) -> _GuardedInfiniteActions:
        return self

    def __next__(self) -> object:
        self.reads += 1
        if self.reads > 4:
            raise AssertionError("resolution request was not bounded")
        return object()


class _StatProxy:
    def __init__(self, original: os.stat_result, **overrides: object) -> None:
        self._original = original
        self._overrides = overrides

    def __getattr__(self, name: str) -> object:
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._original, name)


class _DirEntryProxy:
    def __init__(
        self,
        original: os.DirEntry[str],
        *,
        zero_identity: bool = False,
    ) -> None:
        self._original = original
        self.name = original.name
        self._zero_identity = zero_identity

    def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
        opened = self._original.stat(follow_symlinks=follow_symlinks)
        if self._zero_identity:
            return _StatProxy(opened, st_dev=0, st_ino=0)  # type: ignore[return-value]
        return opened

    def is_symlink(self) -> bool:
        return self._original.is_symlink()

    def is_file(self, *, follow_symlinks: bool = True) -> bool:
        return self._original.is_file(follow_symlinks=follow_symlinks)


class _ScandirProxy:
    def __init__(
        self,
        original: os.ScandirIterator[str],
        *,
        zero_identity: bool = False,
    ) -> None:
        self._original = original
        self._zero_identity = zero_identity

    def __iter__(self) -> _ScandirProxy:
        return self

    def __next__(self) -> _DirEntryProxy:
        entry = next(self._original)
        return _DirEntryProxy(
            entry,
            zero_identity=self._zero_identity,
        )

    def __enter__(self) -> _ScandirProxy:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self._original.close()


def _v2_record(
    *,
    record_id: str = "pending-test-fact-0001",
    type_: str = "fact",
    original_created_at: str = "2026-07-01T10:00:00+00:00",
    project: str | None = "amh",
    tenant_id: str | None = "tenant-a",
) -> dict[str, object]:
    item: dict[str, object] = {
        "type": type_,
        "title": "queued fact",
        "summary": "queued fact summary",
        "body": "queued fact body",
        "tags": ["pending"],
        "sensitivity": "internal",
        "confidence": 0.7,
    }
    if project is not None:
        item["project"] = project
    if tenant_id is not None:
        item["tenant_id"] = tenant_id
    return {
        "v": 2,
        "op": "write",
        "origin": "hook",
        "record_id": record_id,
        "enqueued_at": "2026-07-01T11:00:00+00:00",
        "original_created_at": original_created_at,
        "item": item,
    }


def _legacy_feedback_record() -> dict[str, object]:
    return {
        "v": 1,
        "op": "write",
        "origin": "hook",
        "ts": "2026-07-01T11:00:00+00:00",
        "item": {
            "type": "feedback",
            "title": "legacy feedback",
            "summary": "legacy feedback summary",
            "body": "legacy feedback body",
            "tags": ["pending"],
            "sensitivity": "internal",
        },
    }


def _enqueue_audit_resolution_action(record_id: str) -> PendingResolutionAction:
    record = _v2_record(record_id=record_id)
    item = record["item"]
    assert isinstance(item, dict)
    item["body"] = "curl https://example.invalid/order-review"
    enqueue_write_record(record)
    return PendingResolutionAction("approve_audit", record_id)


def _payload_sha256(record: dict[str, object]) -> str:
    payload = json.dumps(record["item"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stable_item_id(record: dict[str, object]) -> str:
    item = record["item"]
    assert isinstance(item, dict)
    created_at = datetime.fromisoformat(str(record["original_created_at"])).astimezone(timezone.utc)
    title = str(item["title"])
    slug = re.sub(r"[/\\]+", "-", "-".join(title.lower().split()))[:30].strip("-")
    stable = hashlib.sha256(str(record["record_id"]).encode("utf-8")).hexdigest()[:24]
    return f"mem-{created_at:%Y%m%d-%H%M%S}-{slug or 'pending'}-{stable}"


def _expected_domain_digest(domain: str, value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(domain.encode("utf-8") + b"\0" + encoded).hexdigest()


def _freeze_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent_brain.memory.store.pending._utc_now", lambda: NOW, raising=False)


def test_pending_readiness_preview_fails_closed_on_total_byte_budget(
    tmp_brain,
):
    enqueue_write_record(_v2_record(record_id="readiness-budget-record"))

    preview = PendingQueue(brain=tmp_brain).preview_for_readiness(
        limit=10,
        max_total_bytes=8,
        deadline_seconds=1.0,
    )

    assert preview.scan_unavailable is True
    assert preview.reason == "PENDING_READINESS_BUDGET_EXCEEDED"


def test_pending_readiness_uses_supplied_item_catalog_without_rescanning(
    tmp_brain,
    monkeypatch,
):
    enqueue_write_record(_v2_record(record_id="shared-catalog-record"))

    def forbidden_scan(*_args, **_kwargs):
        raise AssertionError("readiness must not rescan item metadata")

    monkeypatch.setattr(pending_module, "_scan_existing_item_metadata", forbidden_scan)
    catalog = pending_module.PendingItemCatalogSnapshot(
        items={},
        trusted=True,
        entry_count=0,
        metadata_bytes=0,
    )

    preview = PendingQueue(brain=tmp_brain).preview_for_readiness(
        limit=10,
        max_total_bytes=1024 * 1024,
        deadline_seconds=1.0,
        item_catalog=catalog,
    )

    assert preview.scan_unavailable is False
    assert preview.returned == 1
    assert preview.records[0].classification == "ready"


def test_pending_readiness_rejects_untrusted_supplied_item_catalog(
    tmp_brain,
    monkeypatch,
):
    enqueue_write_record(_v2_record(record_id="untrusted-catalog-record"))

    def forbidden_scan(*_args, **_kwargs):
        raise AssertionError("untrusted catalog must fail closed without a rescan")

    monkeypatch.setattr(pending_module, "_scan_existing_item_metadata", forbidden_scan)
    catalog = pending_module.PendingItemCatalogSnapshot(
        items={},
        trusted=False,
        reason="ITEM_SCAN_UNAVAILABLE",
        entry_count=1,
        metadata_bytes=128,
    )

    preview = PendingQueue(brain=tmp_brain).preview_for_readiness(
        limit=10,
        max_total_bytes=1024 * 1024,
        deadline_seconds=1.0,
        item_catalog=catalog,
    )

    assert preview.scan_unavailable is True
    assert preview.returned == 0
    assert preview.reason == "PENDING_ITEM_SNAPSHOT_UNTRUSTED"


def test_pending_summary_contains_counts_without_record_content(tmp_brain):
    record = _v2_record(record_id="PRIVATE_RECORD_ID_CANARY")
    item = record["item"]
    assert isinstance(item, dict)
    item["title"] = "PRIVATE_TITLE_CANARY"
    item["summary"] = "PRIVATE_SUMMARY_CANARY"
    enqueue_write_record(record)

    summary = PendingQueue(brain=tmp_brain).preview(limit=10).to_summary_dict()
    encoded = json.dumps(summary, sort_keys=True)

    assert summary["schema_version"] == 1
    assert summary["classification_counts"] == {"ready": 1}
    assert summary["reason_counts"] == {"READY": 1}
    assert summary["groups"] == {"ready": 1, "review": 0, "blocker": 0}
    assert "PRIVATE_RECORD_ID_CANARY" not in encoded
    assert "PRIVATE_TITLE_CANARY" not in encoded
    assert "PRIVATE_SUMMARY_CANARY" not in encoded
    assert "record_id" not in encoded


def test_pending_summary_normalizes_unknown_reason_without_leaking_it(tmp_brain):
    enqueue_write_record(_v2_record(record_id="unknown-summary-reason"))
    preview = PendingQueue(brain=tmp_brain).preview(limit=10)
    record = pending_module.replace(
        preview.records[0],
        reason="SECRET_BACKEND_EXCEPTION_CANARY",
    )
    unknown = pending_module.PendingPreview(
        total=1,
        returned=1,
        limit=10,
        truncated=False,
        records=[record],
    )

    summary = unknown.to_summary_dict()
    encoded = json.dumps(summary, sort_keys=True)

    assert summary["reason_counts"] == {"UNKNOWN_PENDING_REASON": 1}
    assert "SECRET_BACKEND_EXCEPTION_CANARY" not in encoded


def test_pending_apply_summary_omits_per_record_identifiers():
    stats = pending_module.PendingApplyStats()
    stats.add(
        pending_module.PendingApplyResult(
            record_id="PRIVATE_APPLY_RECORD_CANARY",
            item_id="mem-20260720-120000-private-apply-canary",
            classification="ready",
            status="written",
            reason="WRITTEN",
        )
    )

    summary = stats.to_summary_dict()
    encoded = json.dumps(summary, sort_keys=True)

    assert summary["schema_version"] == 1
    assert summary["status_counts"] == {"written": 1}
    assert summary["classification_counts"] == {"ready": 1}
    assert summary["reason_counts"] == {"WRITTEN": 1}
    assert "results" not in summary
    assert "PRIVATE_APPLY_RECORD_CANARY" not in encoded
    assert "private-apply-canary" not in encoded


def test_pending_readiness_preview_uses_injectable_deadline_clock(
    tmp_brain,
    monkeypatch,
):
    import agent_brain.memory.store.pending as pending_module

    enqueue_write_record(_v2_record(record_id="readiness-deadline-record"))
    ticks = iter((0.0, 2.0))
    monkeypatch.setattr(pending_module, "_monotonic", lambda: next(ticks, 2.0))

    preview = PendingQueue(brain=tmp_brain).preview_for_readiness(
        limit=10,
        max_total_bytes=1024 * 1024,
        deadline_seconds=1.0,
    )

    assert preview.scan_unavailable is True
    assert preview.reason == "PENDING_READINESS_BUDGET_EXCEEDED"


def test_pending_path_scan_stops_at_injected_deadline(
    tmp_brain,
    monkeypatch,
):
    import agent_brain.memory.store.pending as pending_module

    for index in range(20):
        enqueue_write_record(_v2_record(record_id=f"deadline-path-{index:02d}"))
    ticks = iter((0.0, 0.0, 0.0, 2.0))
    monkeypatch.setattr(pending_module, "_monotonic", lambda: next(ticks, 2.0))

    snapshot = pending_module._pending_record_paths(
        tmp_brain / "pending",
        deadline_at=1.0,
        entry_cap=20_000,
    )

    assert snapshot.scan_unavailable is True
    assert snapshot.reason == "PENDING_READINESS_BUDGET_EXCEEDED"
    assert snapshot.total <= 2


def test_pending_record_deadline_after_open_closes_fallback_descriptor(
    tmp_brain,
    monkeypatch,
) -> None:
    import agent_brain.memory.store.pending as pending_module

    path = enqueue_write_record(_v2_record(record_id="deadline-after-open"))
    real_close = pending_module.close_descriptor
    closed: list[int] = []

    def tracked_close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    ticks = iter((0.0, 0.0, 0.0, 2.0))
    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)
    monkeypatch.setattr(pending_module, "close_descriptor", tracked_close)
    monkeypatch.setattr(pending_module, "_monotonic", lambda: next(ticks, 2.0))

    with pytest.raises(pending_module._PendingReadError) as caught:
        pending_module._read_pending_record_snapshot(path, deadline_at=1.0)

    assert caught.value.reason == "PENDING_READINESS_BUDGET_EXCEEDED"
    assert len(closed) == 1


def test_metadata_deadline_after_root_open_closes_directory_descriptor(
    tmp_brain,
    monkeypatch,
) -> None:
    import agent_brain.memory.store.pending as pending_module

    real_close = pending_module.close_descriptor
    closed: list[int] = []

    def tracked_close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    ticks = iter((0.0, 2.0))
    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: True)
    monkeypatch.setattr(pending_module, "close_descriptor", tracked_close)
    monkeypatch.setattr(pending_module, "_monotonic", lambda: next(ticks, 2.0))

    snapshot = pending_module._scan_existing_item_metadata(
        tmp_brain / "items",
        deadline_at=1.0,
    )

    assert snapshot.trusted is False
    assert snapshot.reason == "PENDING_READINESS_BUDGET_EXCEEDED"
    assert len(closed) == 1


def test_item_catalog_snapshot_reports_scan_bounds(tmp_brain):
    record = _v2_record(record_id="catalog-bounds-record")
    _write_existing_item(
        tmp_brain,
        record,
        item_id=_stable_item_id(record),
        span_hash=_payload_sha256(record),
    )

    snapshot = pending_module._scan_existing_item_metadata(tmp_brain / "items")

    assert snapshot.trusted is True
    assert snapshot.entry_count >= 1
    assert snapshot.metadata_bytes > 0


def test_pending_path_scan_allows_exact_cap_and_rejects_cap_plus_one(
    tmp_brain,
) -> None:
    import agent_brain.memory.store.pending as pending_module

    for index in range(3):
        enqueue_write_record(_v2_record(record_id=f"entry-cap-{index}"))

    exact = pending_module._pending_record_paths(
        tmp_brain / "pending",
        entry_cap=3,
    )

    assert exact.scan_unavailable is False
    assert exact.total == 3

    enqueue_write_record(_v2_record(record_id="entry-cap-overflow"))
    overflow = pending_module._pending_record_paths(
        tmp_brain / "pending",
        entry_cap=3,
    )

    assert overflow.scan_unavailable is True
    assert overflow.reason == "PENDING_READINESS_BUDGET_EXCEEDED"
    assert overflow.total == 3


def _write_existing_item(
    tmp_brain: Path,
    record: dict[str, object],
    *,
    item_id: str,
    span_hash: str | None,
    project: str | None = "amh",
    tenant_id: str | None = "tenant-a",
    corrupt_body: bool = False,
) -> None:
    queued = record["item"]
    assert isinstance(queued, dict)
    item = MemoryItem(
        id=item_id,
        type=str(queued["type"]),
        created_at=datetime.fromisoformat(str(record["original_created_at"])),
        title=str(queued["title"]),
        summary=str(queued["summary"]),
        tags=list(queued.get("tags") or []),
        sensitivity=str(queued.get("sensitivity") or "internal"),
        project=project,
        tenant_id=tenant_id,
        source=Source(kind="pending-replay", span_hash=span_hash),
    )
    path = ItemsStore(tmp_brain / "items").write(item, "existing body must not be read")
    if corrupt_body:
        frontmatter = render_item_markdown(item, "").encode("utf-8")
        path.write_bytes(frontmatter + b"\xff\xfe\xfd")


def _tree_snapshot(root: Path) -> list[tuple[str, bytes | None]]:
    return [
        (
            path.relative_to(root).as_posix(),
            None if path.is_dir() else path.read_bytes(),
        )
        for path in sorted(root.rglob("*"))
    ]


def test_enqueue_then_explicit_safe_replay_writes_item(tmp_brain: Path) -> None:
    rec = {
        "v": 1,
        "op": "write",
        "origin": "test",
        "item": {
            "type": "fact",
            "title": "queued fact",
            "summary": "s",
            "body": "b",
            "tags": [],
            "sensitivity": "internal",
            "confidence": 0.7,
            "allow_unsafe": True,
        },
    }
    path = enqueue_write_record(rec)
    assert path.exists()
    q = PendingQueue()
    stats = q.replay(safe_only=True)
    assert stats.written == 1
    assert not path.exists()
    assert q.depth() == 0


def test_replay_is_idempotent_on_empty(tmp_brain: Path) -> None:
    assert PendingQueue().replay().written == 0


def test_replay_without_an_explicit_selection_never_writes(tmp_brain: Path) -> None:
    path = enqueue_write_record(_v2_record())

    stats = PendingQueue().replay()

    assert stats.written == 0
    assert path.exists()
    assert list((tmp_brain / "items").glob("*.md")) == []


def test_apply_preserves_original_created_at_and_pending_source(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    path = enqueue_write_record(record)

    result = PendingQueue().apply(safe_only=True)

    assert result.written == 1
    assert not path.exists()
    item, _body = next(ItemsStore(tmp_brain / "items").iter_all())
    assert item.id == _stable_item_id(record)
    assert item.created_at.isoformat() == "2026-07-01T10:00:00+00:00"
    assert item.source.kind == "pending-replay"
    assert item.source.span_hash == _payload_sha256(record)


@pytest.mark.parametrize(
    ("title", "expected_slug"),
    [
        ("Café Memory", "cafe-memory"),
        ("CON", "pending"),
        ('<>:"/\\|?*\x00 中文', "pending"),
    ],
)
def test_pending_item_id_uses_portable_ascii_slug(title: str, expected_slug: str) -> None:
    item_id = pending_module._pending_item_id(title, NOW, "portable-record")

    slug = item_id.removeprefix("mem-20260720-120000-").rsplit("-", 1)[0]
    assert slug == expected_slug
    assert unicodedata.normalize("NFKD", slug) == slug
    assert re.fullmatch(r"[a-z0-9-]+", slug)
    assert not any(char in item_id for char in '<>:"/\\|?*\x00')


def test_pending_unsafe_title_previews_and_applies_without_changing_content(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    original_title = '<>:"/\\|?*\x00 中文'
    record = _v2_record(record_id="unsafe-title-record")
    item = record["item"]
    assert isinstance(item, dict)
    item["title"] = original_title
    path = enqueue_write_record(record)

    preview = PendingQueue().preview(limit=1)
    result = PendingQueue().apply(record_ids=["unsafe-title-record"])

    assert preview.records[0].classification == "ready"
    assert result.written == 1
    assert not path.exists()
    stored, _body = next(ItemsStore(tmp_brain / "items").iter_all())
    assert stored.title == original_title
    assert not any(char in stored.id for char in '<>:"/\\|?*\x00')


def test_apply_requires_explicit_ids_or_safe_only(tmp_brain: Path) -> None:
    path = enqueue_write_record(_v2_record())

    result = PendingQueue().apply()

    assert result.written == 0
    assert result.skipped == 0
    assert result.results == []
    assert path.exists()


def test_apply_explicitly_selected_ready_record_only(tmp_brain: Path) -> None:
    selected = enqueue_write_record(_v2_record(record_id="selected-record"))
    unselected = enqueue_write_record(_v2_record(record_id="unselected-record"))

    result = PendingQueue().apply(record_ids=["selected-record"])

    assert result.written == 1
    assert result.results[0].record_id == "selected-record"
    assert result.results[0].status == "written"
    assert not selected.exists()
    assert unselected.exists()


def test_apply_aborts_before_mutation_when_prepared_receipt_fails(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = enqueue_write_record(_v2_record(record_id="receipt-prepare-failure"))

    def fail_append(*_args, **_kwargs):
        raise OSError("simulated prepared receipt failure")

    monkeypatch.setattr(
        pending_module,
        "append_pending_receipt",
        fail_append,
        raising=False,
    )

    result = PendingQueue().apply(record_ids=["receipt-prepare-failure"])

    assert result.written == 0
    assert result.failed == 1
    assert result.governance_reason == "PENDING_RECEIPT_PREPARE_FAILED"
    assert path.exists()
    assert list((tmp_brain / "items").glob("*.md")) == []


def test_apply_persists_prepared_and_completed_receipt(tmp_brain: Path) -> None:
    from agent_brain.memory.governance.pending_receipts import (
        read_pending_receipt_ledger_health,
    )

    enqueue_write_record(_v2_record(record_id="receipt-success"))

    result = PendingQueue().apply(record_ids=["receipt-success"])
    health = read_pending_receipt_ledger_health(tmp_brain)

    assert result.written == 1
    assert result.governance_reason is None
    assert result.receipt is not None
    assert result.receipt.state == "completed"
    assert result.receipt.status_counts == {"written": 1}
    assert health.status == "healthy"
    assert health.record_count == 2
    assert health.incomplete_count == 0


def test_apply_reports_incomplete_receipt_when_completion_append_fails(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_brain.memory.governance.pending_receipts import (
        append_pending_receipt as real_append,
        read_pending_receipt_ledger_health,
    )

    path = enqueue_write_record(_v2_record(record_id="receipt-completion-failure"))
    calls = 0

    def fail_second_append(brain, receipt):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated completed receipt failure")
        real_append(brain, receipt)

    monkeypatch.setattr(
        pending_module,
        "append_pending_receipt",
        fail_second_append,
        raising=False,
    )

    result = PendingQueue().apply(record_ids=["receipt-completion-failure"])
    health = read_pending_receipt_ledger_health(tmp_brain)

    assert result.written == 1
    assert result.governance_reason == "PENDING_RECEIPT_COMPLETION_FAILED"
    assert result.receipt is not None
    assert result.receipt.state == "incomplete"
    assert not path.exists()
    assert len(list((tmp_brain / "items").glob("*.md"))) == 1
    assert health.status == "healthy"
    assert health.record_count == 1
    assert health.incomplete_count == 1


def test_apply_collects_applied_record_lock_and_preserves_live_lock(
    tmp_brain: Path,
) -> None:
    from agent_brain.memory.governance.pending_lock_gc import pending_record_lock_name

    selected = enqueue_write_record(_v2_record(record_id="lock-gc-selected"))
    unselected = enqueue_write_record(_v2_record(record_id="lock-gc-unselected"))
    locks = tmp_brain / "pending" / ".amh-record-locks"
    locks.mkdir()
    live_lock = locks / pending_record_lock_name(unselected.name)
    live_lock.write_bytes(b"")
    os.chmod(live_lock, 0o600)

    result = PendingQueue().apply(record_ids=["lock-gc-selected"])

    assert result.written == 1
    assert result.lock_gc_report is not None
    assert result.lock_gc_report.total == 2
    assert result.lock_gc_report.orphan == 1
    assert result.lock_gc_report.deleted == 1
    assert result.lock_gc_report.preserved == 1
    assert not selected.exists()
    assert live_lock.exists()
    assert result.receipt is not None
    assert result.receipt.warning_counts == {}


def test_apply_records_lock_gc_warning_in_completed_receipt(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_brain.memory.governance.pending_lock_gc import PendingLockGcReport

    enqueue_write_record(_v2_record(record_id="lock-gc-warning"))

    def truncated_gc(*_args, **_kwargs):
        return PendingLockGcReport(
            total=1,
            orphan=1,
            preserved=1,
            truncated=True,
            reason="PENDING_LOCK_GC_TRUNCATED",
        )

    monkeypatch.setattr(
        pending_module,
        "collect_pending_record_locks",
        truncated_gc,
        raising=False,
    )

    result = PendingQueue().apply(record_ids=["lock-gc-warning"])

    assert result.written == 1
    assert result.lock_gc_report is not None
    assert result.lock_gc_report.truncated is True
    assert result.receipt is not None
    assert result.receipt.state == "completed"
    assert result.receipt.warning_counts == {"PENDING_LOCK_GC_TRUNCATED": 1}


def test_apply_safe_only_never_writes_review_classifications(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    ready = enqueue_write_record(_v2_record(record_id="ready-record"))
    stale_record = _v2_record(
        record_id="stale-record",
        type_="signal",
        original_created_at="2026-01-01T10:00:00+00:00",
    )
    stale = enqueue_write_record(stale_record)

    result = PendingQueue().apply(safe_only=True)

    assert result.written == 1
    assert not ready.exists()
    assert stale.exists()
    assert [row.record_id for row in result.results] == ["ready-record"]


def test_apply_explicit_non_ready_record_reports_review_without_writing(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    path = enqueue_write_record(
        _v2_record(
            record_id="stale-record",
            type_="handoff",
            original_created_at="2026-01-01T10:00:00+00:00",
        )
    )

    result = PendingQueue().apply(record_ids=["stale-record"])

    assert result.review_required == 1
    assert result.results[0].classification == "stale_requires_review"
    assert result.results[0].status == "review_required"
    assert result.results[0].reason == "STALE_EPHEMERAL_MEMORY"
    assert path.exists()
    assert list((tmp_brain / "items").glob("*.md")) == []


def test_apply_reports_missing_and_duplicate_explicit_ids_honestly(tmp_brain: Path) -> None:
    enqueue_write_record(_v2_record(record_id="present-record"))

    result = PendingQueue().apply(
        record_ids=["missing-record", "missing-record", "present-record", "present-record"]
    )

    assert result.written == 1
    assert result.skipped == 3
    assert [(row.record_id, row.status, row.reason) for row in result.results] == [
        ("missing-record", "skipped", "RECORD_ID_NOT_FOUND"),
        ("missing-record", "skipped", "DUPLICATE_RECORD_ID_SELECTION"),
        ("present-record", "written", "WRITTEN"),
        ("present-record", "skipped", "DUPLICATE_RECORD_ID_SELECTION"),
    ]


def test_crash_after_write_before_unlink_becomes_already_written(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    path = enqueue_write_record(_v2_record())
    record_id = PendingQueue().preview(limit=1).records[0].record_id
    original_unlink = pending_module._unlink_pending_record
    failed = False

    def fail_once(
        candidate: Path,
        expected_sha256: str,
        expected_identity: tuple[int, int] | None = None,
    ) -> tuple[str, ...]:
        nonlocal failed
        if candidate == path and not failed:
            failed = True
            raise OSError("simulated unlink failure")
        return original_unlink(candidate, expected_sha256, expected_identity)

    monkeypatch.setattr(pending_module, "_unlink_pending_record", fail_once)

    first = PendingQueue().apply(record_ids=[record_id])
    second = PendingQueue().apply(record_ids=[record_id])

    assert first.failed == 1
    assert first.results[0].reason == "PENDING_UNLINK_FAILED"
    assert second.already_written == 1
    assert second.results[0].status == "already_written"
    assert not path.exists()
    assert len(list(ItemsStore(tmp_brain / "items").iter_all())) == 1


def test_already_written_reconciles_missing_source_ledger_before_consuming_queue(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record(record_id="reconcile-ledger-record")
    path = enqueue_write_record(record)
    item_id = _stable_item_id(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id=item_id,
        span_hash=_payload_sha256(record),
    )

    result = PendingQueue().apply(record_ids=["reconcile-ledger-record"])

    assert result.already_written == 1
    assert not path.exists()
    assert (tmp_brain / "sources" / "writes" / f"{item_id}.json").exists()


def test_already_written_index_repair_is_honest_and_marks_dirty(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_brain.memory.store.write_service import WriteService

    _freeze_now(monkeypatch)
    record = _v2_record(record_id="reconcile-index-record")
    path = enqueue_write_record(record)
    item_id = _stable_item_id(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id=item_id,
        span_hash=_payload_sha256(record),
    )
    service = WriteService.for_brain(tmp_brain)
    monkeypatch.setattr(
        service,
        "_index_item",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    monkeypatch.setattr(WriteService, "for_brain", classmethod(lambda cls, *_args: service))

    result = PendingQueue().apply(record_ids=["reconcile-index-record"])

    assert result.already_written == 1
    assert result.results[0].index_repair_required is True
    assert result.results[0].reason == "STABLE_ITEM_ALREADY_WRITTEN_INDEX_REPAIR_REQUIRED"
    assert item_id in (tmp_brain / ".index-dirty").read_text(encoding="utf-8")
    assert not path.exists()


def test_already_written_keeps_queue_when_source_ledger_repair_fails(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_brain.memory.store import write_service as write_service_module

    _freeze_now(monkeypatch)
    record = _v2_record(record_id="reconcile-source-failure")
    path = enqueue_write_record(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id=_stable_item_id(record),
        span_hash=_payload_sha256(record),
    )
    monkeypatch.setattr(
        write_service_module,
        "_write_source_record",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("private filesystem detail")),
    )

    result = PendingQueue().apply(record_ids=["reconcile-source-failure"])

    assert result.failed == 1
    assert result.results[0].reason == "SOURCE_LEDGER_REPAIR_REQUIRED"
    assert path.exists()


def test_unlink_directory_fsync_failure_keeps_written_result_with_fixed_warning(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    path = enqueue_write_record(_v2_record(record_id="unlink-fsync-record"))
    pending_identity = os.stat(path.parent)
    real_fsync = pending_module.SecureDirectory.fsync

    def fail_post_unlink_pending_fsync(directory: object) -> None:
        opened = os.fstat(directory.fd)  # type: ignore[attr-defined]
        if (
            not path.exists()
            and opened.st_dev == pending_identity.st_dev
            and opened.st_ino == pending_identity.st_ino
        ):
            raise OSError("sensitive mount detail")
        real_fsync(directory)  # type: ignore[arg-type]

    monkeypatch.setattr(pending_module.SecureDirectory, "fsync", fail_post_unlink_pending_fsync)

    result = PendingQueue().apply(record_ids=["unlink-fsync-record"])

    assert result.written == 1
    assert result.results[0].warnings == ("PENDING_DIRECTORY_FSYNC_UNAVAILABLE",)
    assert not path.exists()


def test_concurrent_apply_of_same_record_creates_one_item(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    enqueue_write_record(_v2_record(record_id="concurrent-record"))
    barrier = threading.Barrier(8)
    outcomes: list[object] = []
    errors: list[BaseException] = []

    def apply_record() -> None:
        try:
            barrier.wait()
            outcomes.append(PendingQueue().apply(record_ids=["concurrent-record"]))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=apply_record) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert len(outcomes) == 8
    assert sum(getattr(outcome, "written") for outcome in outcomes) == 1
    assert len(list(ItemsStore(tmp_brain / "items").iter_all())) == 1


def test_items_store_write_never_follows_dangling_item_symlink(tmp_brain: Path) -> None:
    record = _v2_record(record_id="dangling-target-record")
    item_payload = record["item"]
    assert isinstance(item_payload, dict)
    original_created_at = datetime.fromisoformat(str(record["original_created_at"]))
    item, reason = pending_module._validate_pending_item(
        item=item_payload,
        version=2,
        stable_item_id=_stable_item_id(record),
        original_created_at=original_created_at,
        payload_sha256=_payload_sha256(record),
    )
    assert reason is None and item is not None
    outside = tmp_brain.parent / "outside-item.md"
    target = tmp_brain / "items" / f"{item.id}.md"
    target.symlink_to(outside)

    with pytest.raises((FileExistsError, OSError)):
        ItemsStore(tmp_brain / "items").write(item, "must stay inside")

    assert not outside.exists()


def test_apply_stable_id_payload_conflict_fails_closed(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record(record_id="conflicting-record")
    path = enqueue_write_record(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id=_stable_item_id(record),
        span_hash="different-payload-hash",
    )

    result = PendingQueue().apply(record_ids=["conflicting-record"])

    assert result.review_required == 1
    assert result.results[0].classification == "conflict"
    assert result.results[0].reason == "STABLE_ITEM_PAYLOAD_CONFLICT"
    assert path.exists()
    assert len(list(ItemsStore(tmp_brain / "items").iter_all())) == 1


def test_apply_fails_closed_when_pending_changes_after_preview(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    path = enqueue_write_record(_v2_record(record_id="changed-record"))
    queue = PendingQueue()
    original_preview = queue.preview

    def preview_then_change(*, limit: int = 20):
        preview = original_preview(limit=limit)
        persisted = json.loads(path.read_text(encoding="utf-8"))
        item = persisted["item"]
        assert isinstance(item, dict)
        item["body"] = "changed after preview"
        path.write_text(json.dumps(persisted, ensure_ascii=False) + "\n", encoding="utf-8")
        return preview

    monkeypatch.setattr(queue, "preview", preview_then_change)

    result = queue.apply(record_ids=["changed-record"])

    assert result.failed == 1
    assert result.results[0].reason == "PENDING_RECORD_CHANGED"
    assert path.exists()
    assert list((tmp_brain / "items").glob("*.md")) == []


def test_apply_rejects_same_bytes_replaced_inode_after_preview(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    path = enqueue_write_record(_v2_record(record_id="inode-swap-record"))
    queue = PendingQueue()
    stale_preview = queue.preview(limit=10)
    replacement = path.with_suffix(".replacement")
    replacement.write_bytes(path.read_bytes())
    os.replace(replacement, path)
    monkeypatch.setattr(queue, "preview", lambda *, limit=20: stale_preview)

    result = queue.apply(record_ids=["inode-swap-record"])

    assert result.failed == 1
    assert result.results[0].reason == "CONCURRENT_MODIFICATION"
    assert path.exists()
    assert list((tmp_brain / "items").glob("*.md")) == []


def test_enqueue_waits_while_apply_holds_global_queue_lock(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    enqueue_write_record(_v2_record(record_id="apply-first"))
    queue = PendingQueue()
    original_preview = queue.preview
    preview_entered = threading.Event()
    release_preview = threading.Event()
    enqueue_finished = threading.Event()

    def paused_preview(*, limit: int = 20):
        result = original_preview(limit=limit)
        preview_entered.set()
        assert release_preview.wait(timeout=5)
        return result

    monkeypatch.setattr(queue, "preview", paused_preview)
    apply_thread = threading.Thread(
        target=lambda: queue.apply(record_ids=["apply-first"]), daemon=True
    )
    apply_thread.start()
    assert preview_entered.wait(timeout=5)

    def enqueue_second() -> None:
        enqueue_write_record(_v2_record(record_id="enqueue-second"))
        enqueue_finished.set()

    enqueue_thread = threading.Thread(target=enqueue_second, daemon=True)
    enqueue_thread.start()
    assert not enqueue_finished.wait(timeout=0.2)
    release_preview.set()
    apply_thread.join(timeout=5)
    enqueue_thread.join(timeout=5)

    assert not apply_thread.is_alive()
    assert not enqueue_thread.is_alive()
    assert enqueue_finished.is_set()
    assert PendingQueue().preview(limit=10).records[0].record_id == "enqueue-second"


def test_windows_queue_lock_uses_one_byte_msvcrt_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "queue.lock"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    events: list[tuple[int, int]] = []
    fake_msvcrt = SimpleNamespace(
        LK_LOCK=10,
        LK_UNLCK=11,
        locking=lambda _fd, operation, count: events.append((operation, count)),
    )
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(pending_module.os, "name", "nt")
    try:
        kind = pending_module._acquire_queue_file_lock(descriptor)
        pending_module._release_queue_file_lock(descriptor, kind)
        assert os.fstat(descriptor).st_size == 1
    finally:
        os.close(descriptor)

    assert kind == "msvcrt"
    assert events == [(10, 1), (11, 1)]


def test_apply_reports_platform_unsupported_before_any_mutation(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = enqueue_write_record(_v2_record(record_id="unsupported-platform-record"))
    monkeypatch.setattr(pending_module, "lifecycle_mutation_capability", lambda: False)

    result = PendingQueue().apply(record_ids=["unsupported-platform-record"])

    assert result.failed == 1
    assert result.results[0].reason == "PLATFORM_UNSUPPORTED"
    assert path.exists()
    assert list((tmp_brain / "items").glob("*.md")) == []


def test_apply_written_with_index_failure_marks_dirty_and_clears_queue(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_brain.memory.store.write_service import WriteService

    _freeze_now(monkeypatch)
    record = _v2_record(record_id="index-failure-record")
    path = enqueue_write_record(record)

    class _FailingIndex:
        def upsert(self, *_args: object, **_kwargs: object) -> None:
            raise OSError("simulated index failure")

    service = WriteService(
        ItemsStore(tmp_brain / "items"),
        index=_FailingIndex(),  # type: ignore[arg-type]
        embedder=HashingEmbedder(),
        brain_dir=tmp_brain,
    )
    monkeypatch.setattr(WriteService, "for_brain", classmethod(lambda cls, *_args: service))

    result = PendingQueue().apply(record_ids=["index-failure-record"])

    assert result.written == 1
    assert not path.exists()
    assert _stable_item_id(record) in (tmp_brain / ".index-dirty").read_text(encoding="utf-8")


def test_one_apply_failure_does_not_hide_or_block_other_selected_records(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_brain.memory.store.write_service import WriteService

    _freeze_now(monkeypatch)
    failing_record = _v2_record(record_id="failing-record")
    first = enqueue_write_record(failing_record)
    second = enqueue_write_record(_v2_record(record_id="succeeding-record"))
    failing_item_id = _stable_item_id(failing_record)
    original_write = WriteService.write

    def fail_one(self: WriteService, *, item: MemoryItem, **kwargs: object):
        if item.id == failing_item_id:
            raise OSError("simulated write failure")
        return original_write(self, item=item, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(WriteService, "write", fail_one)

    result = PendingQueue().apply(record_ids=["failing-record", "succeeding-record"])

    assert result.failed == 1
    assert result.written == 1
    assert [row.status for row in result.results] == ["failed", "written"]
    assert first.exists()
    assert not second.exists()


def test_private_apply_result_never_exposes_body_or_private_metadata(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record(record_id="private-record")
    item = record["item"]
    assert isinstance(item, dict)
    item.update(
        {
            "sensitivity": "private",
            "title": "private title sentinel",
            "summary": "private summary sentinel",
            "body": "private body sentinel",
            "project": "private project sentinel",
            "agent": "private agent sentinel",
            "session": "private session sentinel",
        }
    )
    enqueue_write_record(record)

    result = PendingQueue().apply(record_ids=["private-record"])
    encoded = json.dumps(result.to_dict(), ensure_ascii=False)

    assert result.written == 1
    assert "private title sentinel" not in encoded
    assert "private-title-sentinel" not in encoded
    assert "private summary sentinel" not in encoded
    assert "private body sentinel" not in encoded
    assert "private project sentinel" not in encoded
    assert "private agent sentinel" not in encoded
    assert "private session sentinel" not in encoded


def test_default_enqueue_writes_v2_envelope(tmp_brain: Path) -> None:
    path = enqueue_write_record(
        {
            "op": "write",
            "origin": "hook",
            "item": {
                "type": "fact",
                "title": "queued",
                "summary": "summary",
                "body": "body",
            },
        }
    )

    record = json.loads(path.read_text(encoding="utf-8"))

    assert record["v"] == 2
    assert record["op"] == "write"
    assert record["origin"] == "hook"
    assert record["record_id"]
    assert datetime.fromisoformat(record["enqueued_at"]).tzinfo is not None
    assert record["original_created_at"] == record["enqueued_at"]
    assert record["payload_sha256"] == _payload_sha256(record)


def test_enqueue_is_durable_and_private_under_umask_zero(tmp_brain: Path) -> None:
    previous = os.umask(0)
    try:
        path = enqueue_write_record(_v2_record())
    finally:
        os.umask(previous)

    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(path.parent.glob("*.tmp"))


def test_concurrent_same_enqueue_is_idempotent_without_overwrite(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    fixed = UUID("11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(pending_module.uuid, "uuid4", lambda: fixed)
    paths: list[Path] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def enqueue() -> None:
        try:
            barrier.wait()
            paths.append(enqueue_write_record(_v2_record(record_id=str(fixed))))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=enqueue) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert len(paths) == 8
    assert len(set(paths)) == 1
    assert len(list((tmp_brain / "pending").glob("*.jsonl"))) == 1


def test_fixed_filename_collision_with_different_bytes_fails_closed(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    fixed = UUID("22222222-2222-4222-8222-222222222222")
    monkeypatch.setattr(pending_module.uuid, "uuid4", lambda: fixed)
    enqueue_write_record(_v2_record(record_id=str(fixed)))
    changed = _v2_record(record_id=str(fixed))
    changed_item = changed["item"]
    assert isinstance(changed_item, dict)
    changed_item["body"] = "different body"

    with pytest.raises(PendingEnqueueError, match="PENDING_RECORD_FILENAME_CONFLICT"):
        enqueue_write_record(changed)

    files = list((tmp_brain / "pending").glob("*.jsonl"))
    assert len(files) == 1
    assert "different body" not in files[0].read_text(encoding="utf-8")


def test_enqueue_publish_failure_cleans_temp_and_leaves_no_record(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        pending_module.os,
        "link",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("publish failed")),
    )

    with pytest.raises(OSError, match="publish failed"):
        enqueue_write_record(_v2_record())

    pending = tmp_brain / "pending"
    assert list(pending.glob("*.tmp")) == []
    assert list(pending.glob("*.jsonl")) == []


def test_enqueue_write_all_handles_partial_os_writes(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_write = os.write

    def partial_write(descriptor: int, data: object) -> int:
        view = memoryview(data)  # type: ignore[arg-type]
        return original_write(descriptor, view[:7])

    monkeypatch.setattr(pending_module.os, "write", partial_write)

    path = enqueue_write_record(_v2_record())

    assert json.loads(path.read_text(encoding="utf-8"))["v"] == 2


def test_enqueue_fsync_failure_cleans_unpublished_temp(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_fsync = os.fsync

    def fail_regular_file_fsync(descriptor: int) -> None:
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("simulated file fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(pending_module.os, "fsync", fail_regular_file_fsync)

    with pytest.raises(OSError, match="simulated file fsync failure"):
        enqueue_write_record(_v2_record())

    pending = tmp_brain / "pending"
    assert list(pending.glob("*.tmp")) == []
    assert list(pending.glob("*.jsonl")) == []


def test_first_brain_creation_fsyncs_parent_before_publishing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brain = tmp_path / "new" / "deep" / "brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    original_fsync = os.fsync
    original_mkdir = os.mkdir
    directory_fsyncs = 0
    creation_events: list[str] = []

    def track_mkdir(path: object, *args: object, **kwargs: object) -> None:
        original_mkdir(path, *args, **kwargs)  # type: ignore[arg-type]
        creation_events.append("mkdir")

    def fail_first_directory_fsync(descriptor: int) -> None:
        nonlocal directory_fsyncs
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            directory_fsyncs += 1
            creation_events.append("fsync")
            if directory_fsyncs == 1:
                raise OSError("simulated parent fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(pending_module.os, "mkdir", track_mkdir)
    monkeypatch.setattr(pending_module.os, "fsync", fail_first_directory_fsync)

    with pytest.raises(OSError, match="simulated parent fsync failure"):
        enqueue_write_record(_v2_record())

    assert directory_fsyncs == 1
    assert creation_events[:2] == ["mkdir", "fsync"]
    assert list(brain.glob("pending/*.jsonl")) == []


def test_committed_record_survives_temp_cleanup_failure(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    original_unlink = os.unlink

    def fail_temp_cleanup(path: object, *args: object, **kwargs: object) -> None:
        if str(path).startswith(".amh-pending-"):
            raise OSError("simulated cleanup failure")
        original_unlink(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pending_module.os, "unlink", fail_temp_cleanup)

    path = enqueue_write_record(_v2_record())

    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8"))["v"] == 2
    assert list(path.parent.glob(".amh-pending-*.tmp"))
    assert "PENDING_TEMP_CLEANUP_FAILED" in caplog.text


def test_fallback_capability_enqueue_and_preview_are_functional(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)

    path = enqueue_write_record(_v2_record())
    preview = PendingQueue().preview(limit=1)

    assert path.is_file()
    assert preview.scan_unavailable is False
    assert preview.records[0].classification == "ready"


def test_fallback_existing_item_scan_remains_trusted(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    path = enqueue_write_record(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id=_stable_item_id(record),
        span_hash=_payload_sha256(record),
    )
    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)

    preview = PendingQueue().preview(limit=1)

    assert path.is_file()
    assert preview.records[0].classification == "already_written"


def test_fallback_nested_archived_item_ignores_zero_direntry_identity(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    enqueue_write_record(record)
    item_id = _stable_item_id(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id=item_id,
        span_hash=_payload_sha256(record),
    )
    nested = tmp_brain / "items" / "archived" / "nested"
    nested.mkdir(parents=True)
    (tmp_brain / "items" / f"{item_id}.md").rename(nested / f"{item_id}.md")
    original_scandir = os.scandir
    items_root = tmp_brain / "items"

    def zero_identity_scandir(path: object) -> object:
        opened = original_scandir(path)  # type: ignore[arg-type]
        candidate = Path(os.fspath(path))
        return _ScandirProxy(
            opened,  # type: ignore[arg-type]
            zero_identity=items_root == candidate or items_root in candidate.parents,
        )

    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)
    monkeypatch.setattr(pending_module.os, "scandir", zero_identity_scandir)

    preview = PendingQueue().preview(limit=1)

    assert preview.records[0].classification == "already_written"


def test_zero_explicit_file_identities_never_match_even_with_identical_metadata(
    tmp_path: Path,
) -> None:
    path = tmp_path / "identity"
    path.write_text("same", encoding="utf-8")
    opened = os.lstat(path)
    first = _StatProxy(opened, st_dev=0, st_ino=0)
    second = _StatProxy(opened, st_dev=0, st_ino=0)

    assert pending_module._same_file_identity(first, second) is False


def test_zero_explicit_nested_directory_identity_blocks_fallback_scan(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    enqueue_write_record(_v2_record())
    nested = tmp_brain / "items" / "nested"
    nested.mkdir()
    original_lstat = os.lstat

    def zero_lstat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        opened = original_lstat(path, *args, **kwargs)  # type: ignore[arg-type]
        if Path(os.fspath(path)) == nested:
            return _StatProxy(opened, st_dev=0, st_ino=0)  # type: ignore[return-value]
        return opened

    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)
    monkeypatch.setattr(pending_module.os, "lstat", zero_lstat)

    preview = PendingQueue().preview(limit=1)

    assert preview.records[0].classification == "audit_blocked"
    assert preview.records[0].reason == "EXISTING_ITEM_SCAN_UNAVAILABLE"


def test_fallback_rejects_windows_reparse_nested_items_directory(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    enqueue_write_record(_v2_record())
    nested = tmp_brain / "items" / "junction"
    nested.mkdir()
    original_lstat = os.lstat
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

    def reparse_lstat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        opened = original_lstat(path, *args, **kwargs)  # type: ignore[arg-type]
        if Path(os.fspath(path)) == nested:
            return _StatProxy(  # type: ignore[return-value]
                opened, st_file_attributes=reparse_flag
            )
        return opened

    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)
    monkeypatch.setattr(pending_module.os, "lstat", reparse_lstat)

    preview = PendingQueue().preview(limit=1)

    assert preview.records[0].classification == "audit_blocked"
    assert preview.records[0].reason == "EXISTING_ITEM_SCAN_UNAVAILABLE"


def test_fallback_rejects_pending_directory_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brain = tmp_path / "brain"
    outside = tmp_path / "outside"
    brain.mkdir()
    outside.mkdir()
    (brain / "pending").symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)

    with pytest.raises(PendingEnqueueError):
        enqueue_write_record(_v2_record())

    preview = PendingQueue().preview(limit=1)
    assert preview.scan_unavailable is True
    assert list(outside.iterdir()) == []


def test_fallback_rejects_windows_reparse_pending_directory(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pending = tmp_brain / "pending"
    pending.mkdir()
    original_lstat = os.lstat
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

    def reparse_lstat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        opened = original_lstat(path, *args, **kwargs)  # type: ignore[arg-type]
        if Path(os.fspath(path)) == pending:
            return _StatProxy(  # type: ignore[return-value]
                opened, st_file_attributes=reparse_flag
            )
        return opened

    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)
    monkeypatch.setattr(pending_module.os, "lstat", reparse_lstat)

    with pytest.raises(PendingEnqueueError, match="UNSAFE_PENDING_DIRECTORY"):
        enqueue_write_record(_v2_record())


def test_fallback_reparse_pending_file_marks_scan_unavailable(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = enqueue_write_record(_v2_record())
    original_lstat = os.lstat
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

    def reparse_lstat(candidate: object, *args: object, **kwargs: object) -> os.stat_result:
        opened = original_lstat(candidate, *args, **kwargs)  # type: ignore[arg-type]
        if Path(os.fspath(candidate)) == path:
            return _StatProxy(  # type: ignore[return-value]
                opened, st_file_attributes=reparse_flag
            )
        return opened

    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)
    monkeypatch.setattr(pending_module.os, "lstat", reparse_lstat)

    preview = PendingQueue().preview(limit=1)

    assert preview.scan_unavailable is True
    assert preview.reason == "PENDING_SCAN_UNAVAILABLE"


def test_fallback_rejects_fifo_pending_record(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation unavailable")
    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)
    fifo = tmp_brain / "pending" / "fifo.jsonl"
    fifo.parent.mkdir()
    os.mkfifo(fifo)

    with pytest.raises(pending_module._PendingReadError, match="NOT_REGULAR"):
        pending_module._read_pending_record(fifo)


def test_fallback_concurrent_same_enqueue_is_idempotent(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    fixed = UUID("33333333-3333-4333-8333-333333333333")
    monkeypatch.setattr(pending_module.uuid, "uuid4", lambda: fixed)
    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)
    paths: list[Path] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def enqueue() -> None:
        try:
            barrier.wait()
            paths.append(enqueue_write_record(_v2_record(record_id=str(fixed))))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=enqueue) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert len(paths) == 8
    assert len(set(paths)) == 1
    assert len(list((tmp_brain / "pending").glob("*.jsonl"))) == 1


def test_posix_fallback_fsyncs_each_new_parent_before_continuing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brain = tmp_path / "fallback" / "deep" / "brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)
    original_mkdir = os.mkdir
    original_fsync = os.fsync
    events: list[str] = []

    def track_mkdir(path: object, *args: object, **kwargs: object) -> None:
        original_mkdir(path, *args, **kwargs)  # type: ignore[arg-type]
        events.append("mkdir")

    def fail_first_directory_fsync(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            events.append("fsync")
            raise OSError("simulated fallback parent fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(pending_module.os, "mkdir", track_mkdir)
    monkeypatch.setattr(pending_module.os, "fsync", fail_first_directory_fsync)

    with pytest.raises(OSError, match="simulated fallback parent fsync failure"):
        enqueue_write_record(_v2_record())

    assert events[:2] == ["mkdir", "fsync"]
    assert list(brain.glob("pending/*.jsonl")) == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_payload_is_rejected_without_creating_files(tmp_brain: Path, bad: float) -> None:
    record = _v2_record()
    item = record["item"]
    assert isinstance(item, dict)
    item["confidence"] = bad

    with pytest.raises(PendingEnqueueError, match="NON_FINITE_PENDING_PAYLOAD"):
        enqueue_write_record(record)

    assert not (tmp_brain / "pending").exists()


def test_oversized_enqueue_is_rejected_before_file_creation(tmp_brain: Path) -> None:
    record = _v2_record()
    item = record["item"]
    assert isinstance(item, dict)
    item["body"] = "x" * (1024 * 1024)

    with pytest.raises(PendingEnqueueError, match="PENDING_RECORD_TOO_LARGE"):
        enqueue_write_record(record)

    assert not (tmp_brain / "pending").exists()


def test_explicit_v1_keeps_legacy_bytes_and_preview_is_read_only(
    tmp_brain: Path,
) -> None:
    record = _legacy_feedback_record()
    path = enqueue_write_record(record)
    before_bytes = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns
    before_names = sorted(entry.name for entry in path.parent.iterdir())

    first = PendingQueue().preview(limit=10)
    second = PendingQueue().preview(limit=10)

    persisted = json.loads(before_bytes)
    assert persisted["v"] == 1
    assert "record_id" not in persisted
    assert "payload_sha256" not in persisted
    assert first.records[0].record_id == second.records[0].record_id
    assert path.read_bytes() == before_bytes
    assert path.stat().st_mtime_ns == before_mtime
    assert sorted(entry.name for entry in path.parent.iterdir()) == before_names


def test_string_v1_stays_on_legacy_envelope(tmp_brain: Path) -> None:
    record = _legacy_feedback_record()
    record["v"] = "1"

    path = enqueue_write_record(record)
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert persisted["v"] == "1"
    assert "record_id" not in persisted
    assert PendingQueue().preview(limit=1).records[0].classification == "unsupported_type"


def test_legacy_identity_ignores_retry_bookkeeping(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _legacy_feedback_record()
    item = record["item"]
    assert isinstance(item, dict)
    item["type"] = "fact"
    path = enqueue_write_record(record)
    first = PendingQueue().preview(limit=1).records[0]
    persisted = json.loads(path.read_text(encoding="utf-8"))
    persisted.update(
        {
            "attempt": 4,
            "last_error_code": "TEMPORARY_FAILURE",
            "last_attempt_at": "2026-07-20T11:00:00+00:00",
            "status": "retrying",
        }
    )
    path.write_text(json.dumps(persisted, ensure_ascii=False) + "\n", encoding="utf-8")

    second = PendingQueue().preview(limit=1).records[0]

    assert second.record_id == first.record_id
    assert second.payload_sha256 == first.payload_sha256


def test_v2_preview_preserves_original_time_and_stable_identity(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    path = enqueue_write_record(record)

    first = PendingQueue().preview(limit=10).records[0]
    second = PendingQueue().preview(limit=10).records[0]

    assert path.exists()
    assert first.record_id == second.record_id == "pending-test-fact-0001"
    assert first.payload_sha256 == second.payload_sha256 == _payload_sha256(record)
    assert first.original_created_at == "2026-07-01T10:00:00+00:00"
    assert first.enqueued_at == "2026-07-01T11:00:00+00:00"
    assert first.age_seconds == int(
        (NOW - datetime(2026, 7, 1, 10, tzinfo=timezone.utc)).total_seconds()
    )
    assert first.classification == "ready"
    assert first.reason == "READY"


def test_stable_item_id_uses_utc_instant_and_96_bit_record_hash() -> None:
    first = pending_module._pending_item_id(
        "same title",
        datetime.fromisoformat("2026-07-01T18:00:00+08:00"),
        "same-record",
    )
    second = pending_module._pending_item_id(
        "same title",
        datetime.fromisoformat("2026-07-01T10:00:00+00:00"),
        "same-record",
    )

    assert first == second
    assert first.startswith("mem-20260701-100000-")
    assert re.search(r"-[0-9a-f]{24}$", first)


def test_stable_item_id_distinguishes_dst_fold_instants() -> None:
    zone = ZoneInfo("America/New_York")
    first_fold = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0)
    second_fold = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1)

    first = pending_module._pending_item_id("fold", first_fold, "same-record")
    second = pending_module._pending_item_id("fold", second_fold, "same-record")

    assert first != second
    assert "-053000-" in first
    assert "-063000-" in second


def test_legacy_feedback_is_unsupported_not_malformed(tmp_brain: Path) -> None:
    enqueue_write_record(_legacy_feedback_record())

    record = PendingQueue().preview(limit=10).records[0]

    assert record.malformed is False
    assert record.classification == "unsupported_type"
    assert record.reason == "UNSUPPORTED_MEMORY_TYPE"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("confidence", 1.1),
        ("sensitivity", "top-secret"),
        ("refs", ["not-a-mapping"]),
        ("validity", {"ttl_hours": "not-an-int"}),
        ("tags", "not-a-list"),
    ],
)
def test_invalid_item_schema_fails_closed_without_leaking_body(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    item = record["item"]
    assert isinstance(item, dict)
    item[field] = value
    item["body"] = "schema failure private body"
    enqueue_write_record(record)

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.classification == "malformed"
    assert preview.reason == "INVALID_ITEM_SCHEMA"
    assert "schema failure private body" not in json.dumps(preview.to_dict())


def test_unsupported_type_precedes_other_schema_failures(tmp_brain: Path) -> None:
    record = _legacy_feedback_record()
    item = record["item"]
    assert isinstance(item, dict)
    item["confidence"] = 2.0
    enqueue_write_record(record)

    preview = PendingQueue().preview(limit=1).records[0]

    assert preview.classification == "unsupported_type"
    assert preview.reason == "UNSUPPORTED_MEMORY_TYPE"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("refs", {"files": [], "filez": ["typo"]}),
        ("validity", {"ttl_hours": 24, "repoo": "typo"}),
        ("source", {"kind": "hook", "extractorr": "typo"}),
        ("source", {"kind": ["not-a-string"]}),
    ],
)
def test_nested_unknown_schema_fields_fail_closed(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    item = record["item"]
    assert isinstance(item, dict)
    item[field] = value
    enqueue_write_record(record)

    preview = PendingQueue().preview(limit=1).records[0]

    assert preview.classification == "malformed"
    assert preview.reason == "INVALID_ITEM_SCHEMA"


def test_v2_item_created_at_must_match_original_instant(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record(original_created_at="2026-07-01T10:00:00+00:00")
    item = record["item"]
    assert isinstance(item, dict)
    item["created_at"] = "2026-07-01T10:00:01+00:00"
    enqueue_write_record(record)

    preview = PendingQueue().preview(limit=1).records[0]

    assert preview.classification == "malformed"
    assert preview.reason == "ITEM_CREATED_AT_MISMATCH"


def test_v2_item_created_at_accepts_same_instant_with_different_offset(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record(original_created_at="2026-07-01T10:00:00+00:00")
    item = record["item"]
    assert isinstance(item, dict)
    item["created_at"] = "2026-07-01T18:00:00+08:00"
    enqueue_write_record(record)

    preview = PendingQueue().preview(limit=1).records[0]

    assert preview.classification == "ready"
    assert preview.original_created_at == "2026-07-01T10:00:00+00:00"


@pytest.mark.parametrize("type_", ["signal", "handoff"])
def test_old_signal_and_handoff_require_review(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch, type_: str
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record(type_=type_, original_created_at="2026-06-20T12:00:00+00:00")
    enqueue_write_record(record)

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.age_seconds == 30 * 24 * 60 * 60
    assert preview.classification == "stale_requires_review"
    assert preview.reason == "STALE_EPHEMERAL_MEMORY"


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("original_created_at", "2026-07-01T10:00:00", "NAIVE_ORIGINAL_CREATED_AT"),
        ("original_created_at", "2026-07-21T10:00:00+00:00", "FUTURE_ORIGINAL_CREATED_AT"),
        ("original_created_at", "0001-01-01T00:00:00+14:00", "INVALID_ORIGINAL_CREATED_AT"),
        ("enqueued_at", "not-a-time", "INVALID_ENQUEUED_AT"),
    ],
)
def test_invalid_pending_times_fail_closed(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
    reason: str,
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    record[field] = value
    enqueue_write_record(record)

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.malformed is True
    assert preview.classification == "malformed"
    assert preview.reason == reason


def test_v2_declared_hash_tamper_is_conflict(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    record["payload_sha256"] = "0" * 64
    enqueue_write_record(record)

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.payload_sha256 == _payload_sha256(record)
    assert preview.classification == "conflict"
    assert preview.reason == "PAYLOAD_HASH_MISMATCH"


def test_apply_accepts_canonical_hash_with_uppercase_hex(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record(record_id="uppercase-hash-record")
    record["payload_sha256"] = _payload_sha256(record).upper()
    enqueue_write_record(record)

    result = PendingQueue().apply(record_ids=["uppercase-hash-record"])

    assert result.written == 1
    assert result.failed == 0


def test_audit_blocked_payload_has_closed_classification(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    item = record["item"]
    assert isinstance(item, dict)
    marker = "-----BEGIN " + "RSA PRIVATE KEY-----"
    item["body"] = marker
    enqueue_write_record(record)

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.classification == "audit_blocked"
    assert preview.reason == "AUDIT_BLOCKED"
    assert marker not in json.dumps(preview.to_dict())


def test_explicit_apply_never_honors_queued_allow_unsafe_audit_bypass(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record(record_id="audit-blocked-record")
    item = record["item"]
    assert isinstance(item, dict)
    item["body"] = "-----BEGIN " + "RSA PRIVATE KEY-----"
    item["allow_unsafe"] = True
    path = enqueue_write_record(record)

    result = PendingQueue().apply(record_ids=["audit-blocked-record"])

    assert result.review_required == 1
    assert result.results[0].classification == "audit_blocked"
    assert result.results[0].reason == "AUDIT_BLOCKED"
    assert path.exists()
    assert list((tmp_brain / "items").glob("*.md")) == []


def test_malformed_record_does_not_block_other_records(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    first = enqueue_write_record(_v2_record(record_id="pending-valid-0001"))
    malformed = first.parent / "0000-malformed.jsonl"
    malformed.write_bytes(b"{not json\n")

    preview = PendingQueue().preview(limit=10)

    assert preview.total == 2
    assert [record.classification for record in preview.records] == ["malformed", "ready"]
    assert preview.records[0].reason == "MALFORMED_JSON"


def test_same_record_id_and_payload_marks_later_queue_record_duplicate(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    enqueue_write_record(_v2_record(record_id="shared-record"))
    enqueue_write_record(_v2_record(record_id="shared-record"))

    preview = PendingQueue().preview(limit=10)

    assert [record.classification for record in preview.records] == [
        "ready",
        "duplicate_candidate",
    ]
    assert preview.records[1].reason == "PENDING_RECORD_DUPLICATE"


def test_same_record_id_with_different_payload_marks_all_records_conflict(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    enqueue_write_record(_v2_record(record_id="shared-record"))
    changed = _v2_record(record_id="shared-record")
    item = changed["item"]
    assert isinstance(item, dict)
    item["summary"] = "different semantics"
    enqueue_write_record(changed)

    preview = PendingQueue().preview(limit=10)

    assert [record.classification for record in preview.records] == [
        "conflict",
        "conflict",
    ]
    assert {record.reason for record in preview.records} == {"PENDING_RECORD_ID_CONFLICT"}


def test_oversized_pending_record_fails_closed_without_reading_it(
    tmp_brain: Path,
) -> None:
    pending = tmp_brain / "pending"
    pending.mkdir()
    path = pending / "oversized.jsonl"
    path.write_bytes(b"{" + b" " * (1024 * 1024) + b"}\n")

    result = PendingQueue().preview(limit=10)
    preview = result.records[0]

    assert result.scan_unavailable is True
    assert preview.classification == "audit_blocked"
    assert preview.reason == "PENDING_SCAN_UNAVAILABLE"


def test_preview_ignores_symlinks_and_only_reads_regular_files(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    path = enqueue_write_record(_v2_record())
    symlink = path.parent / "0000-symlink.jsonl"
    try:
        symlink.symlink_to(path)
    except (NotImplementedError, OSError):
        pytest.skip("symlink creation unavailable")

    preview = PendingQueue().preview(limit=10)

    assert preview.total == 1
    assert [record.record_id for record in preview.records] == ["pending-test-fact-0001"]


def test_brain_dir_symlink_alias_converges_writer_and_scanner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "real-brain"
    (target / "items").mkdir(parents=True)
    alias = tmp_path / "brain-alias"
    try:
        alias.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlink unavailable")
    monkeypatch.setenv("BRAIN_DIR", str(alias))
    _freeze_now(monkeypatch)

    path = enqueue_write_record(_v2_record())
    preview = PendingQueue().preview(limit=1)

    assert path.parent == target.resolve() / "pending"
    assert preview.total == 1
    assert preview.records[0].classification == "ready"


def test_pending_directory_symlink_is_rejected_and_scan_truth_is_explicit(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_brain.parent / "outside-pending"
    outside.mkdir()
    try:
        (tmp_brain / "pending").symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlink unavailable")

    with pytest.raises((OSError, PendingEnqueueError)):
        enqueue_write_record(_v2_record())
    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)
    preview = PendingQueue().preview(limit=1)

    assert preview.scan_unavailable is True
    assert preview.reason == "PENDING_SCAN_UNAVAILABLE"
    with pytest.raises(PendingEnqueueError, match="PENDING_SCAN_UNAVAILABLE"):
        PendingQueue().depth()
    assert list(outside.iterdir()) == []


def test_existing_stable_item_with_same_hash_is_already_written_without_body_read(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    enqueue_write_record(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id=_stable_item_id(record),
        span_hash=_payload_sha256(record),
        corrupt_body=True,
    )

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.classification == "already_written"
    assert preview.reason == "STABLE_ITEM_ALREADY_WRITTEN"


def test_existing_private_stable_item_is_detected_but_content_stays_redacted(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    queued = record["item"]
    assert isinstance(queued, dict)
    queued["sensitivity"] = "private"
    enqueue_write_record(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id=_stable_item_id(record),
        span_hash=_payload_sha256(record),
        corrupt_body=True,
    )

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.classification == "already_written"
    assert preview.title is None
    assert preview.summary is None


def test_untrusted_existing_item_scan_blocks_ready_classification(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    enqueue_write_record(_v2_record())
    monkeypatch.setattr(
        pending_module,
        "_scan_existing_item_metadata",
        lambda *_args, **_kwargs: pending_module._ItemMetadataSnapshot(items={}, trusted=False),
    )

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.classification == "audit_blocked"
    assert preview.reason == "EXISTING_ITEM_SCAN_UNAVAILABLE"


def test_existing_stable_item_with_different_hash_is_conflict(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    enqueue_write_record(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id=_stable_item_id(record),
        span_hash="f" * 64,
    )

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.classification == "conflict"
    assert preview.reason == "STABLE_ITEM_PAYLOAD_CONFLICT"


@pytest.mark.parametrize(
    ("queued_project", "queued_tenant", "existing_project", "existing_tenant"),
    [
        ("amh", "tenant-a", "other-project", "tenant-a"),
        ("amh", "tenant-a", "amh", "tenant-b"),
    ],
)
def test_existing_stable_item_cross_scope_is_conflict_even_with_same_hash(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
    queued_project: str,
    queued_tenant: str,
    existing_project: str,
    existing_tenant: str,
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record(project=queued_project, tenant_id=queued_tenant)
    enqueue_write_record(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id=_stable_item_id(record),
        span_hash=_payload_sha256(record),
        project=existing_project,
        tenant_id=existing_tenant,
    )

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.classification == "conflict"
    assert preview.reason == "STABLE_ITEM_SCOPE_CONFLICT"


def test_none_and_empty_scope_are_canonically_equivalent(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record(project=None, tenant_id=None)
    enqueue_write_record(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id=_stable_item_id(record),
        span_hash=_payload_sha256(record),
        project="",
        tenant_id="",
    )

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.classification == "already_written"
    assert preview.reason == "STABLE_ITEM_ALREADY_WRITTEN"


def test_same_scope_existing_payload_is_duplicate_candidate(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    enqueue_write_record(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id="mem-20260701-100000-existing-duplicate",
        span_hash=_payload_sha256(record),
    )

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.classification == "duplicate_candidate"
    assert preview.reason == "SAME_SCOPE_PAYLOAD_DUPLICATE"


def test_apply_rescans_catalog_after_stale_preview_and_keeps_duplicate_queued(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record(record_id="catalog-rescan-record")
    path = enqueue_write_record(record)
    queue = PendingQueue()
    stale = queue.preview(limit=10)
    assert stale.records[0].classification == "ready"
    store = ItemsStore(tmp_brain / "items")
    apply_started = threading.Event()
    apply_finished = threading.Event()
    outcomes: list[object] = []

    def apply_pending() -> None:
        apply_started.set()
        outcomes.append(queue.apply(record_ids=["catalog-rescan-record"]))
        apply_finished.set()

    with store.locked_catalog():
        apply_thread = threading.Thread(target=apply_pending, daemon=True)
        apply_thread.start()
        assert apply_started.wait(timeout=5)
        assert not apply_finished.wait(timeout=0.2)
        _write_existing_item(
            tmp_brain,
            record,
            item_id="mem-20260701-100000-catalog-winner",
            span_hash=_payload_sha256(record),
        )
    apply_thread.join(timeout=5)
    assert not apply_thread.is_alive()
    result = outcomes[0]

    assert getattr(result, "review_required") == 1
    assert getattr(result, "results")[0].classification == "duplicate_candidate"
    assert getattr(result, "results")[0].reason == "SAME_SCOPE_PAYLOAD_DUPLICATE"
    assert path.exists()
    assert len(list(ItemsStore(tmp_brain / "items").iter_all())) == 1

    safe_only = queue.apply(safe_only=True)
    assert safe_only.review_required == 1
    assert safe_only.results[0].classification == "duplicate_candidate"
    assert path.exists()


def test_cataloged_metadata_update_reclassifies_pending_duplicate_without_second_item(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record(record_id="catalog-update-rescan")
    path = enqueue_write_record(record)
    existing_id = "mem-20260701-100000-catalog-update-winner"
    _write_existing_item(
        tmp_brain,
        record,
        item_id=existing_id,
        span_hash=None,
        project="different-project",
    )
    store = ItemsStore(tmp_brain / "items")
    queue = PendingQueue()
    assert queue.preview(limit=1).records[0].classification == "ready"
    apply_started = threading.Event()
    apply_finished = threading.Event()
    outcomes: list[object] = []

    def apply_pending() -> None:
        apply_started.set()
        outcomes.append(queue.apply(record_ids=["catalog-update-rescan"]))
        apply_finished.set()

    queued = record["item"]
    assert isinstance(queued, dict)
    with store.locked_catalog():
        thread = threading.Thread(target=apply_pending, daemon=True)
        thread.start()
        assert apply_started.wait(timeout=5)
        assert not apply_finished.wait(timeout=0.2)
        store.update_frontmatter(
            existing_id,
            title=queued["title"],
            summary=queued["summary"],
            project=queued["project"],
            tenant_id=queued["tenant_id"],
            type=queued["type"],
            refs={
                "files": [],
                "urls": [],
                "mems": [],
                "commits": [],
                "resources": [],
                "extractions": [],
            },
        )
    thread.join(timeout=5)

    assert not thread.is_alive()
    result = outcomes[0]
    assert getattr(result, "review_required") == 1
    assert getattr(result, "results")[0].classification == "duplicate_candidate"
    assert getattr(result, "results")[0].reason == "SAME_SCOPE_METADATA_DUPLICATE"
    assert path.exists()
    assert len(list(store.iter_all())) == 1


def test_same_scope_title_summary_identity_is_duplicate_candidate(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    enqueue_write_record(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id="mem-20260701-100000-existing-metadata-duplicate",
        span_hash=None,
    )

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.classification == "duplicate_candidate"
    assert preview.reason == "SAME_SCOPE_METADATA_DUPLICATE"


def test_different_scope_payload_is_not_a_duplicate_candidate(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    enqueue_write_record(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id="mem-20260701-100000-other-scope",
        span_hash=_payload_sha256(record),
        project="other-project",
    )

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.classification == "ready"


def test_private_preview_redacts_content_fields(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    item = record["item"]
    assert isinstance(item, dict)
    item.update(
        {
            "sensitivity": "private",
            "title": "private title",
            "summary": "private summary",
            "body": "private body",
            "project": "private project",
            "agent": "private agent",
            "session": "private session",
        }
    )
    enqueue_write_record(record)

    preview = PendingQueue().preview(limit=10).records[0]
    payload = preview.to_dict()

    assert preview.classification == "ready"
    assert payload["title"] is None
    assert payload["summary"] is None
    assert payload["project"] is None
    assert payload["agent"] is None
    assert payload["session"] is None
    assert "private body" not in json.dumps(payload)


@pytest.mark.parametrize("sensitivity", ["unknown-tier", 7, ["private"]])
def test_invalid_sensitivity_redacts_content_before_schema_validation(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
    sensitivity: object,
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record()
    item = record["item"]
    assert isinstance(item, dict)
    item.update(
        {
            "sensitivity": sensitivity,
            "title": "must redact title",
            "summary": "must redact summary",
            "body": "must redact body",
        }
    )
    enqueue_write_record(record)

    preview = PendingQueue().preview(limit=1).records[0]
    serialized = json.dumps(preview.to_dict())

    assert preview.classification == "malformed"
    assert preview.title is None
    assert preview.summary is None
    assert "must redact" not in serialized


def test_pending_preview_summarizes_records_without_replay(tmp_brain: Path) -> None:
    rec = {
        "v": 1,
        "op": "write",
        "origin": "hook",
        "attempt": 2,
        "ts": "2026-07-01T11:00:00+00:00",
        "item": {
            "type": "decision",
            "title": "queued decision",
            "summary": "queued summary",
            "body": "body",
            "tags": ["ops"],
            "sensitivity": "internal",
            "confidence": 0.7,
        },
    }
    path = enqueue_write_record(rec)

    preview = PendingQueue().preview(limit=10)

    assert path.exists()
    assert preview.total == 1
    assert preview.records[0].path == str(path)
    assert preview.records[0].title == "queued decision"
    assert preview.records[0].type == "decision"
    assert preview.records[0].attempt == 2
    assert PendingQueue().depth() == 1


def test_preview_limit_and_sort_are_deterministic(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    first = enqueue_write_record(_v2_record(record_id="pending-z"))
    second = enqueue_write_record(_v2_record(record_id="pending-a"))
    second.rename(first.parent / "0000-first.jsonl")
    first.rename(first.parent / "9999-last.jsonl")

    preview = PendingQueue().preview(limit=1)

    assert preview.total == 2
    assert preview.returned == 1
    assert preview.truncated is True
    assert preview.records[0].record_id == "pending-a"


def test_preview_limit_does_not_hide_record_id_conflict(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    first_record = _v2_record(record_id="shared-record-id")
    second_record = _v2_record(record_id="shared-record-id")
    second_item = second_record["item"]
    assert isinstance(second_item, dict)
    second_item["body"] = "conflicting body"
    first = enqueue_write_record(first_record)
    second = enqueue_write_record(second_record)
    first.rename(first.parent / "0000-first.jsonl")
    second.rename(second.parent / "9999-hidden.jsonl")

    preview = PendingQueue().preview(limit=1)

    assert preview.returned == 1
    assert preview.records[0].classification == "conflict"
    assert preview.records[0].reason == "PENDING_RECORD_ID_CONFLICT"


def test_preview_limit_does_not_hide_stable_id_conflict(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    monkeypatch.setattr(
        pending_module,
        "_pending_item_id",
        lambda *_args, **_kwargs: "mem-20260701-100000-forced-stable-id",
    )
    first_record = _v2_record(record_id="first-record-id")
    second_record = _v2_record(record_id="second-record-id")
    second_item = second_record["item"]
    assert isinstance(second_item, dict)
    second_item["body"] = "conflicting body"
    first = enqueue_write_record(first_record)
    second = enqueue_write_record(second_record)
    first.rename(first.parent / "0000-first.jsonl")
    second.rename(second.parent / "9999-hidden.jsonl")

    preview = PendingQueue().preview(limit=1)

    assert preview.returned == 1
    assert preview.records[0].classification == "conflict"
    assert preview.records[0].reason == "PENDING_STABLE_ID_CONFLICT"


def test_pending_entry_stat_failure_blocks_all_selected_records(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    first = enqueue_write_record(_v2_record(record_id="visible-record"))
    second = enqueue_write_record(_v2_record(record_id="unknown-record"))
    first.rename(first.parent / "0000-visible.jsonl")
    second.rename(second.parent / "9999-unknown.jsonl")
    hidden = tmp_brain / "pending" / "9999-unknown.jsonl"
    original_lstat = os.lstat

    def failing_lstat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        if Path(os.fspath(path)) == hidden:
            raise OSError("simulated entry stat failure")
        return original_lstat(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)
    monkeypatch.setattr(pending_module.os, "lstat", failing_lstat)

    preview = PendingQueue().preview(limit=1)

    assert preview.scan_unavailable is True
    assert preview.reason == "PENDING_SCAN_UNAVAILABLE"
    assert preview.records[0].classification == "audit_blocked"
    assert preview.records[0].reason == "PENDING_SCAN_UNAVAILABLE"


def test_pending_record_read_failure_blocks_all_selected_records(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    first = enqueue_write_record(_v2_record(record_id="readable-record"))
    second = enqueue_write_record(_v2_record(record_id="unreadable-record"))
    first.rename(first.parent / "0000-readable.jsonl")
    second.rename(second.parent / "9999-unreadable.jsonl")
    original_read = pending_module._read_pending_record_snapshot

    def fail_one_read(path: Path) -> tuple[bytes, tuple[int, int]]:
        if path.name == "9999-unreadable.jsonl":
            raise pending_module._PendingReadError("PENDING_RECORD_READ_FAILED")
        return original_read(path)

    monkeypatch.setattr(pending_module, "_read_pending_record_snapshot", fail_one_read)

    preview = PendingQueue().preview(limit=1)

    assert preview.scan_unavailable is True
    assert preview.reason == "PENDING_SCAN_UNAVAILABLE"
    assert preview.records[0].classification == "audit_blocked"
    assert preview.records[0].reason == "PENDING_SCAN_UNAVAILABLE"


def test_pending_scan_cap_keeps_cap_plus_one_and_reports_truncation(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    monkeypatch.setattr(pending_module, "MAX_PENDING_QUEUE_ENTRIES", 1)
    first = enqueue_write_record(_v2_record(record_id="pending-z"))
    second = enqueue_write_record(_v2_record(record_id="pending-a"))
    first.rename(first.parent / "z-last.jsonl")
    second.rename(second.parent / "a-first.jsonl")

    preview = PendingQueue().preview(limit=10)

    assert preview.total == 2
    assert preview.returned == 1
    assert preview.truncated is True
    assert preview.records[0].record_id == "pending-a"
    assert preview.records[0].classification == "audit_blocked"
    assert preview.records[0].reason == "PENDING_QUEUE_TRUNCATED"


def test_safe_only_apply_requires_a_complete_trusted_scan(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    monkeypatch.setattr(pending_module, "MAX_PENDING_QUEUE_ENTRIES", 1)
    enqueue_write_record(_v2_record(record_id="first-ready"))
    enqueue_write_record(_v2_record(record_id="second-ready"))

    result = PendingQueue().apply(safe_only=True)

    assert result.written == 0
    assert result.failed == 1
    assert result.results[0].reason == "PENDING_QUEUE_TRUNCATED"
    assert len(list((tmp_brain / "pending").glob("*.jsonl"))) == 2
    assert list((tmp_brain / "items").glob("*.md")) == []


def test_existing_item_scan_overflow_blocks_ready_classification(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    monkeypatch.setattr(pending_module, "MAX_ITEM_METADATA_ENTRIES", 1)
    record = _v2_record()
    enqueue_write_record(record)
    _write_existing_item(
        tmp_brain,
        record,
        item_id="mem-20260701-100000-first-existing",
        span_hash=None,
        project="other-one",
    )
    _write_existing_item(
        tmp_brain,
        record,
        item_id="mem-20260701-100001-second-existing",
        span_hash=None,
        project="other-two",
    )

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.classification == "audit_blocked"
    assert preview.reason == "EXISTING_ITEM_SCAN_UNAVAILABLE"


def test_legacy_item_lock_tree_does_not_consume_pending_metadata_budget(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    existing_record = _v2_record(record_id="existing-other-scope", project="other")
    _write_existing_item(
        tmp_brain,
        existing_record,
        item_id="mem-20260701-100000-existing-lock-budget",
        span_hash="existing",
        project="other",
    )
    legacy = tmp_brain / "items" / ".amh-item-locks"
    legacy.mkdir()
    for index in range(25):
        (legacy / f"legacy-{index}.lock").write_text("", encoding="utf-8")
    enqueue_write_record(_v2_record(record_id="ready-with-legacy-locks"))
    monkeypatch.setattr(pending_module, "MAX_ITEM_METADATA_ENTRIES", 1)

    preview = PendingQueue().preview(limit=1).records[0]

    assert preview.classification == "ready"


@pytest.mark.parametrize("secure_io", [True, False])
def test_existing_item_scan_stops_immediately_after_absolute_deadline(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
    secure_io: bool,
) -> None:
    for index in range(20):
        record = _v2_record(record_id=f"metadata-deadline-{index}", project="other")
        _write_existing_item(
            tmp_brain,
            record,
            item_id=f"mem-20260701-10{index:02d}00-metadata-deadline-{index}",
            span_hash=f"span-{index}",
            project="other",
        )
    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: secure_io)
    reader_name = "_read_item_frontmatter" if secure_io else "_read_item_frontmatter_fallback"
    real_reader = getattr(pending_module, reader_name)
    visits = 0

    def counted_reader(*args, **kwargs):
        nonlocal visits
        visits += 1
        return real_reader(*args, **kwargs)

    clock = -0.005

    def slow_clock() -> float:
        nonlocal clock
        clock += 0.005
        return clock

    monkeypatch.setattr(pending_module, reader_name, counted_reader)
    monkeypatch.setattr(pending_module, "_monotonic", slow_clock)

    snapshot = pending_module._scan_existing_item_metadata(
        tmp_brain / "items",
        deadline_at=0.001,
    )

    assert snapshot.trusted is False
    assert snapshot.reason == "PENDING_READINESS_BUDGET_EXCEEDED"
    assert visits <= 2


def test_secure_metadata_deadline_after_child_open_closes_descriptor_once(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nested = tmp_brain / "items" / "nested"
    nested.mkdir()
    real_open_child = pending_module.open_child_directory
    real_close = pending_module.close_descriptor
    opened_children: list[int] = []
    close_counts: dict[int, int] = {}
    expired = False

    def open_child_then_expire(parent: int, name: str) -> int:
        nonlocal expired
        child = real_open_child(parent, name)
        opened_children.append(child)
        expired = True
        return child

    def tracked_close(descriptor: int) -> None:
        close_counts[descriptor] = close_counts.get(descriptor, 0) + 1
        real_close(descriptor)

    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: True)
    monkeypatch.setattr(pending_module, "open_child_directory", open_child_then_expire)
    monkeypatch.setattr(pending_module, "close_descriptor", tracked_close)
    monkeypatch.setattr(pending_module, "_monotonic", lambda: 2.0 if expired else 0.0)

    snapshot = pending_module._scan_existing_item_metadata(
        tmp_brain / "items",
        deadline_at=1.0,
    )

    assert snapshot.trusted is False
    assert snapshot.reason == "PENDING_READINESS_BUDGET_EXCEEDED"
    assert len(opened_children) == 1
    child = opened_children[0]
    assert close_counts[child] == 1
    try:
        with pytest.raises(OSError) as caught:
            os.fstat(child)
        assert caught.value.errno == errno.EBADF
    finally:
        try:
            os.close(child)
        except OSError as error:
            assert error.errno == errno.EBADF


def test_malformed_existing_yaml_blocks_selected_preview_without_raising(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze_now(monkeypatch)
    enqueue_write_record(_v2_record())
    (tmp_brain / "items" / "mem-20260701-100000-bad-yaml.md").write_text(
        "---\nrefs: [unterminated\n---\nbody\n",
        encoding="utf-8",
    )

    preview = PendingQueue().preview(limit=1)

    assert preview.records[0].classification == "audit_blocked"
    assert preview.records[0].reason == "EXISTING_ITEM_SCAN_UNAVAILABLE"


def test_limit_zero_does_not_scan_existing_items(
    tmp_brain: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    enqueue_write_record(_v2_record())
    monkeypatch.setattr(
        pending_module,
        "_scan_existing_item_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not scan")),
    )

    preview = PendingQueue().preview(limit=0)

    assert preview.total == 1
    assert preview.returned == 0
    assert preview.truncated is True


def test_resolution_preview_validates_all_supported_actions_without_mutation(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_now(monkeypatch)
    audit = _v2_record(record_id="resolution-audit")
    audit_item = audit["item"]
    assert isinstance(audit_item, dict)
    audit_item["title"] = "audit-only record"
    audit_item["summary"] = "audit-only summary"
    audit_item["body"] = "curl https://example.invalid/review"
    enqueue_write_record(audit)

    duplicate = _v2_record(record_id="resolution-duplicate")
    target_id = "mem-20260701-100000-resolution-duplicate-target"
    _write_existing_item(
        tmp_brain,
        duplicate,
        item_id=target_id,
        span_hash="different-payload",
    )
    enqueue_write_record(duplicate)

    enqueue_write_record(_legacy_feedback_record())
    initial = PendingQueue().preview(limit=10)
    feedback = next(
        record for record in initial.records if record.classification == "unsupported_type"
    )
    before = _tree_snapshot(tmp_brain)
    selections: list[object] = []
    validate_resolution = PendingQueue._validate_resolution

    def capture_private_selection(*args, **kwargs):
        selection, result = validate_resolution(*args, **kwargs)
        selections.append(selection)
        return selection, result

    monkeypatch.setattr(
        PendingQueue,
        "_validate_resolution",
        capture_private_selection,
    )

    stats = PendingQueue().resolve(
        [
            PendingResolutionAction("approve_audit", "resolution-audit"),
            PendingResolutionAction(
                "accept_duplicate",
                "resolution-duplicate",
                target_id,
            ),
            PendingResolutionAction("convert_type", feedback.record_id, "decision"),
        ]
    )

    assert stats.dry_run is True
    assert [result.status for result in stats.results] == ["ready", "ready", "ready"]
    assert [result.reason for result in stats.results] == [
        "PENDING_RESOLUTION_READY",
        "PENDING_RESOLUTION_READY",
        "PENDING_RESOLUTION_READY",
    ]
    assert [result.classification for result in stats.results] == [
        "audit_blocked",
        "duplicate_candidate",
        "unsupported_type",
    ]
    assert all(result.item_id is not None for result in stats.results)
    assert stats.receipt is None
    assert stats.lock_gc_report is None
    assert len(selections) == 3
    assert all(selection is not None for selection in selections)
    private_digests = {
        digest
        for selection in selections
        for digest in (
            selection.audit_digest,  # type: ignore[union-attr]
            selection.target_digest,  # type: ignore[union-attr]
        )
        if digest is not None
    }
    assert len(private_digests) == 3
    assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in private_digests)
    target_item, _target_body = ItemsStore(tmp_brain / "items").get_nofollow(target_id)
    expected_target_digest = _expected_domain_digest(
        "amh.pending.resolution.target.v1",
        target_item.model_dump(mode="json", exclude_none=False),
    )
    expected_conversion_digest = _expected_domain_digest(
        "amh.pending.resolution.target.v1",
        "decision",
    )
    assert expected_target_digest in private_digests
    assert expected_conversion_digest in private_digests
    public_json = json.dumps(
        {
            "detail": stats.to_dict(),
            "summary": stats.to_summary_dict(),
        },
        sort_keys=True,
    )
    filesystem_text = b"\n".join(
        content
        for _path, content in _tree_snapshot(tmp_brain)
        if content is not None
    ).decode("utf-8", errors="replace")
    assert all(digest not in public_json for digest in private_digests)
    assert all(digest not in filesystem_text for digest in private_digests)
    assert _tree_snapshot(tmp_brain) == before


def test_resolution_conflict_blocks_whole_batch_without_mutation(
    tmp_brain: Path,
) -> None:
    enqueue_write_record(_v2_record(record_id="resolution-conflict"))
    before = _tree_snapshot(tmp_brain)

    stats = PendingQueue().resolve(
        [
            PendingResolutionAction("approve_audit", "resolution-conflict"),
            PendingResolutionAction("convert_type", "resolution-conflict", "decision"),
        ]
    )

    assert stats.governance_reason == "CONFLICTING_PENDING_RESOLUTIONS"
    assert len(stats.results) == 2
    assert {result.status for result in stats.results} == {"blocked"}
    assert {result.reason for result in stats.results} == {
        "CONFLICTING_PENDING_RESOLUTIONS"
    }
    assert _tree_snapshot(tmp_brain) == before


@pytest.mark.parametrize("sensitivity", ["private", "secret"])
def test_resolution_audit_approval_rejects_sensitive_records(
    tmp_brain: Path,
    sensitivity: str,
) -> None:
    record = _v2_record(record_id=f"resolution-sensitive-{sensitivity}")
    item = record["item"]
    assert isinstance(item, dict)
    item["sensitivity"] = sensitivity
    item["body"] = "curl https://example.invalid/private"
    enqueue_write_record(record)

    result = PendingQueue().resolve(
        [PendingResolutionAction("approve_audit", str(record["record_id"]))]
    ).results[0]

    assert result.status == "blocked"
    assert result.reason == "PENDING_AUDIT_APPROVAL_REQUIRED"


def test_resolution_audit_approval_never_bypasses_secret_findings(
    tmp_brain: Path,
) -> None:
    record = _v2_record(record_id="resolution-secret-finding")
    item = record["item"]
    assert isinstance(item, dict)
    item["body"] = (
        "api_"
        + 'key = "'
        + "abcdefghijklm"
        + "nopqrstuvwxyz"
        + "123456"
        + '"'
    )
    enqueue_write_record(record)

    result = PendingQueue().resolve(
        [PendingResolutionAction("approve_audit", "resolution-secret-finding")]
    ).results[0]

    assert result.status == "blocked"
    assert result.reason == "PENDING_AUDIT_SECRET_BLOCKED"


@pytest.mark.parametrize(
    ("case", "target_id", "project", "title"),
    [
        (
            "missing",
            "mem-20260701-100000-resolution-missing",
            "amh",
            "queued fact",
        ),
        (
            "scope",
            "mem-20260701-100000-resolution-scope",
            "other",
            "queued fact",
        ),
        (
            "metadata",
            "mem-20260701-100000-resolution-metadata",
            "amh",
            "different title",
        ),
    ],
)
def test_resolution_duplicate_rejects_wrong_target(
    tmp_brain: Path,
    case: str,
    target_id: str,
    project: str,
    title: str,
) -> None:
    record = _v2_record(record_id=f"resolution-duplicate-{case}")
    matching_id = f"mem-20260701-100000-resolution-matching-{case}"
    _write_existing_item(
        tmp_brain,
        record,
        item_id=matching_id,
        span_hash="different-payload",
    )
    if case != "missing":
        target_record = _v2_record(record_id=f"resolution-target-{case}", project=project)
        target_item = target_record["item"]
        assert isinstance(target_item, dict)
        target_item["title"] = title
        _write_existing_item(
            tmp_brain,
            target_record,
            item_id=target_id,
            span_hash="target-payload",
            project=project,
        )
    enqueue_write_record(record)

    result = PendingQueue().resolve(
        [PendingResolutionAction("accept_duplicate", str(record["record_id"]), target_id)]
    ).results[0]

    assert result.status == "blocked"
    assert result.reason == "PENDING_DUPLICATE_TARGET_MISMATCH"


@pytest.mark.parametrize(
    ("source_type", "target", "body", "expected"),
    [
        ("feedback", "fact", "legacy feedback body", "PENDING_CONVERSION_UNSUPPORTED"),
        ("obsolete", "decision", "legacy feedback body", "PENDING_CONVERSION_UNSUPPORTED"),
        ("feedback", "decision", ["not", "text"], "PENDING_CONVERSION_INVALID"),
        (
            "feedback",
            "decision",
            "curl https://example.invalid/converted",
            "PENDING_CONVERSION_INVALID",
        ),
    ],
)
def test_resolution_conversion_rejects_unsupported_or_unsafe_input(
    tmp_brain: Path,
    source_type: str,
    target: str,
    body: object,
    expected: str,
) -> None:
    record = _legacy_feedback_record()
    item = record["item"]
    assert isinstance(item, dict)
    item["type"] = source_type
    item["body"] = body
    enqueue_write_record(record)
    preview = PendingQueue().preview(limit=1).records[0]

    result = PendingQueue().resolve(
        [PendingResolutionAction("convert_type", preview.record_id, target)]
    ).results[0]

    assert result.status == "blocked"
    assert result.reason == expected


def test_resolution_rejects_record_drift_between_preview_and_validation(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _v2_record(record_id="resolution-drift")
    item = record["item"]
    assert isinstance(item, dict)
    item["body"] = "curl https://example.invalid/drift"
    enqueue_write_record(record)
    real_read = pending_module._read_pending_record_snapshot
    reads = 0

    def changed_second_read(*args, **kwargs):
        nonlocal reads
        raw, identity = real_read(*args, **kwargs)
        reads += 1
        return (raw + b" ", identity) if reads == 2 else (raw, identity)

    monkeypatch.setattr(
        pending_module,
        "_read_pending_record_snapshot",
        changed_second_read,
    )

    result = PendingQueue().resolve(
        [PendingResolutionAction("approve_audit", "resolution-drift")]
    ).results[0]

    assert result.status == "blocked"
    assert result.reason == "PENDING_RESOLUTION_CHANGED"


def test_resolution_summary_is_aggregate_only_and_preserves_public_reasons() -> None:
    stats = pending_module.PendingResolutionStats(dry_run=True)
    stats.results.extend(
        [
            pending_module.PendingResolutionResult(
                action="accept_duplicate",
                record_id="PRIVATE_RESOLUTION_RECORD_CANARY",
                target="mem-20260701-100000-private-target-canary",
                item_id="mem-20260701-100000-private-item-canary",
                classification="duplicate_candidate",
                status="blocked",
                reason="PENDING_DUPLICATE_TARGET_MISMATCH",
            ),
            pending_module.PendingResolutionResult(
                action="approve_audit",
                record_id="PRIVATE_SECOND_RECORD_CANARY",
                classification="audit_blocked",
                status="ready",
                reason="PENDING_RESOLUTION_READY",
            ),
        ]
    )

    summary = stats.to_summary_dict()
    encoded = json.dumps(summary, sort_keys=True)

    assert summary["schema_version"] == 1
    assert summary["dry_run"] is True
    assert summary["action_counts"] == {
        "accept_duplicate": 1,
        "approve_audit": 1,
    }
    assert summary["reason_counts"] == {
        "PENDING_DUPLICATE_TARGET_MISMATCH": 1,
        "PENDING_RESOLUTION_READY": 1,
    }
    for canary in (
        "PRIVATE_RESOLUTION_RECORD_CANARY",
        "PRIVATE_SECOND_RECORD_CANARY",
        "private-target-canary",
        "private-item-canary",
        "title",
        "summary",
        "body",
        "path",
    ):
        assert canary not in encoded


def test_resolution_deduplicates_exact_actions_and_empty_is_side_effect_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    brain = tmp_path / "not-created"
    empty = PendingQueue(brain=brain).resolve([])
    empty_apply = PendingQueue(brain=brain).resolve([], apply=True)

    assert empty.dry_run is True
    assert empty.results == []
    assert empty_apply.dry_run is True
    assert empty_apply.results == []
    assert not brain.exists()

    brain.mkdir()
    (brain / "items").mkdir()
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    record = _v2_record(record_id="resolution-idempotent")
    item = record["item"]
    assert isinstance(item, dict)
    item["body"] = "curl https://example.invalid/idempotent"
    enqueue_write_record(record)
    action = PendingResolutionAction("approve_audit", "resolution-idempotent")
    before = _tree_snapshot(brain)

    stats = PendingQueue(brain=brain).resolve([action, action])

    assert len(stats.results) == 1
    assert stats.results[0].reason == "PENDING_RESOLUTION_READY"
    assert _tree_snapshot(brain) == before


def test_resolution_apply_true_is_blocked_without_mutation(
    tmp_brain: Path,
) -> None:
    record = _v2_record(record_id="resolution-apply-not-yet")
    item = record["item"]
    assert isinstance(item, dict)
    item["body"] = "curl https://example.invalid/apply"
    enqueue_write_record(record)
    before = _tree_snapshot(tmp_brain)

    stats = PendingQueue().resolve(
        [PendingResolutionAction("approve_audit", "resolution-apply-not-yet")],
        apply=True,
    )

    assert stats.dry_run is False
    assert len(stats.results) == 1
    assert stats.results[0].status == "blocked"
    assert stats.results[0].reason == "PENDING_RESOLUTION_NOT_APPLICABLE"
    assert stats.receipt is None
    assert stats.lock_gc_report is None
    assert _tree_snapshot(tmp_brain) == before


def test_resolution_invalid_action_shapes_fail_closed_and_summarize_safely(
    tmp_brain: Path,
) -> None:
    malformed = [
        object(),
        PendingResolutionAction("delete", "resolution-invalid-action"),  # type: ignore[arg-type]
        PendingResolutionAction("approve_audit", "../invalid-record"),
        PendingResolutionAction(  # type: ignore[arg-type]
            {"not": "an action"},
            "resolution-invalid-action-type",
        ),
        PendingResolutionAction(  # type: ignore[arg-type]
            "accept_duplicate",
            "resolution-invalid-target",
            {"not": "a target"},
        ),
    ]
    before = _tree_snapshot(tmp_brain)

    stats = PendingQueue().resolve(malformed)  # type: ignore[arg-type]

    assert len(stats.results) == len(malformed)
    assert {result.status for result in stats.results} == {"blocked"}
    assert {result.reason for result in stats.results} == {
        "PENDING_DUPLICATE_TARGET_MISMATCH",
        "PENDING_RESOLUTION_NOT_APPLICABLE",
    }
    assert stats.to_summary_dict()["reason_counts"] == {
        "PENDING_DUPLICATE_TARGET_MISMATCH": 1,
        "PENDING_RESOLUTION_NOT_APPLICABLE": len(malformed) - 1,
    }
    assert _tree_snapshot(tmp_brain) == before


def test_resolution_preserves_existing_conflict_reason(
    tmp_brain: Path,
) -> None:
    record = _v2_record(record_id="resolution-existing-conflict")
    record["payload_sha256"] = "0" * 64
    enqueue_write_record(record)

    result = PendingQueue().resolve(
        [PendingResolutionAction("approve_audit", "resolution-existing-conflict")]
    ).results[0]

    assert result.status == "blocked"
    assert result.classification == "conflict"
    assert result.reason == "PAYLOAD_HASH_MISMATCH"


def test_resolution_rejects_unhashable_str_subclasses_without_side_effects(
    tmp_brain: Path,
) -> None:
    actions = [
        PendingResolutionAction(  # type: ignore[arg-type]
            UnhashableStr("approve_audit"),
            "resolution-unhashable-action",
        ),
        PendingResolutionAction(
            "approve_audit",
            UnhashableStr("resolution-unhashable-record"),
        ),
        PendingResolutionAction(
            "accept_duplicate",
            "resolution-unhashable-target",
            UnhashableStr("mem-20260701-100000-unhashable-target"),
        ),
    ]
    before = _tree_snapshot(tmp_brain)

    stats = PendingQueue().resolve(actions)

    assert len(stats.results) == 3
    assert {result.status for result in stats.results} == {"blocked"}
    assert [result.reason for result in stats.results] == [
        "PENDING_RESOLUTION_NOT_APPLICABLE",
        "PENDING_RESOLUTION_NOT_APPLICABLE",
        "PENDING_DUPLICATE_TARGET_MISMATCH",
    ]
    assert stats.results[0].action == "unknown"
    assert stats.results[1].record_id == ""
    assert stats.results[2].target is None
    assert _tree_snapshot(tmp_brain) == before


def test_resolution_closes_unknown_action_in_result_and_summary() -> None:
    canary = "PRIVATE_TITLE_CANARY"
    result = pending_module.PendingResolutionResult(
        action=canary,
        record_id="resolution-unknown-action",
        classification=None,
        status="blocked",
        reason="PENDING_RESOLUTION_NOT_APPLICABLE",
    )
    stats = pending_module.PendingResolutionStats(dry_run=True, results=[result])

    encoded = json.dumps(
        {"result": result.to_dict(), "summary": stats.to_summary_dict()},
        sort_keys=True,
    )

    assert result.action == "unknown"
    assert stats.to_summary_dict()["action_counts"] == {"unknown": 1}
    assert canary not in encoded


def test_resolution_rejects_identity_drift_with_original_hash(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _v2_record(record_id="resolution-identity-drift")
    item = record["item"]
    assert isinstance(item, dict)
    item["body"] = "curl https://example.invalid/identity-drift"
    enqueue_write_record(record)
    real_read = pending_module._read_pending_record_snapshot
    reads = 0

    def changed_identity_second_read(*args, **kwargs):
        nonlocal reads
        raw, identity = real_read(*args, **kwargs)
        reads += 1
        if reads == 2:
            return raw, (identity[0], identity[1] + 1)
        return raw, identity

    monkeypatch.setattr(
        pending_module,
        "_read_pending_record_snapshot",
        changed_identity_second_read,
    )

    result = PendingQueue().resolve(
        [PendingResolutionAction("approve_audit", "resolution-identity-drift")]
    ).results[0]

    assert result.status == "blocked"
    assert result.reason == "PENDING_RESOLUTION_CHANGED"


def test_resolution_rejects_oversized_requests_before_scanning(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pending_module, "MAX_PENDING_QUEUE_ENTRIES", 2)
    monkeypatch.setattr(
        pending_module,
        "_pending_record_paths",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not scan")),
    )
    monkeypatch.setattr(
        pending_module,
        "_scan_existing_item_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not scan")),
    )
    reads = 0

    def oversized():
        nonlocal reads
        for index in range(3):
            reads += 1
            yield PendingResolutionAction("approve_audit", f"resolution-large-{index}")

    stats = PendingQueue().resolve(oversized())

    assert reads == 3
    assert stats.governance_reason == "PENDING_RESOLUTION_REQUEST_TOO_LARGE"
    assert len(stats.results) == 2
    assert {result.status for result in stats.results} == {"blocked"}
    assert {result.reason for result in stats.results} == {
        "PENDING_RESOLUTION_REQUEST_TOO_LARGE"
    }


def test_resolution_bounds_infinite_invalid_generator_before_scanning(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pending_module, "MAX_PENDING_QUEUE_ENTRIES", 2)
    monkeypatch.setattr(
        pending_module,
        "_pending_record_paths",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not scan")),
    )
    monkeypatch.setattr(
        pending_module,
        "_scan_existing_item_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not scan")),
    )
    actions = _GuardedInfiniteActions()

    stats = PendingQueue().resolve(actions)  # type: ignore[arg-type]

    assert actions.reads == 3
    assert stats.governance_reason == "PENDING_RESOLUTION_REQUEST_TOO_LARGE"
    assert len(stats.results) == 2
    assert {result.reason for result in stats.results} == {
        "PENDING_RESOLUTION_REQUEST_TOO_LARGE"
    }


def test_resolution_invalid_only_request_returns_without_scanning(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pending_module,
        "_pending_record_paths",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not scan")),
    )
    monkeypatch.setattr(
        pending_module,
        "_scan_existing_item_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not scan")),
    )

    stats = PendingQueue().resolve(  # type: ignore[arg-type]
        [
            object(),
            PendingResolutionAction("PRIVATE_TITLE_CANARY", "resolution-invalid"),  # type: ignore[arg-type]
        ]
    )

    assert len(stats.results) == 2
    assert {result.reason for result in stats.results} == {
        "PENDING_RESOLUTION_NOT_APPLICABLE"
    }


@pytest.mark.parametrize(
    ("action", "expected_reason"),
    [
        pytest.param(
            PendingResolutionAction(
                "approve_audit",
                "resolution-audit-target",
                "decision",
            ),
            "PENDING_RESOLUTION_NOT_APPLICABLE",
            id="audit-target",
        ),
        pytest.param(
            PendingResolutionAction(
                "accept_duplicate",
                "resolution-duplicate-none",
            ),
            "PENDING_DUPLICATE_TARGET_MISMATCH",
            id="duplicate-none",
        ),
        pytest.param(
            PendingResolutionAction(
                "accept_duplicate",
                "resolution-duplicate-invalid",
                "not-a-memory-id",
            ),
            "PENDING_DUPLICATE_TARGET_MISMATCH",
            id="duplicate-invalid",
        ),
        pytest.param(
            PendingResolutionAction(
                "convert_type",
                "resolution-conversion-fact",
                "fact",
            ),
            "PENDING_CONVERSION_UNSUPPORTED",
            id="conversion-fact",
        ),
        pytest.param(
            PendingResolutionAction(
                "convert_type",
                "resolution-conversion-subclass",
                UnhashableStr("decision"),
            ),
            "PENDING_CONVERSION_UNSUPPORTED",
            id="conversion-str-subclass",
        ),
    ],
)
def test_resolution_rejects_invalid_action_targets_before_scanning(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: PendingResolutionAction,
    expected_reason: str,
) -> None:
    monkeypatch.setattr(
        pending_module,
        "_pending_record_paths",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not scan")),
    )
    monkeypatch.setattr(
        pending_module,
        "_scan_existing_item_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not scan")),
    )
    before = _tree_snapshot(tmp_brain)

    result = PendingQueue().resolve([action]).results[0]

    assert result.status == "blocked"
    assert result.reason == expected_reason
    assert _tree_snapshot(tmp_brain) == before


def test_resolution_rejects_unhashable_action_subclass_before_scanning(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pending_module,
        "_pending_record_paths",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not scan")),
    )
    monkeypatch.setattr(
        pending_module,
        "_scan_existing_item_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not scan")),
    )
    before = _tree_snapshot(tmp_brain)
    action = UnhashablePendingResolutionAction(
        "approve_audit",
        "resolution-action-subclass",
    )

    result = PendingQueue().resolve([action]).results[0]

    assert result.action == "unknown"
    assert result.status == "blocked"
    assert result.reason == "PENDING_RESOLUTION_NOT_APPLICABLE"
    assert _tree_snapshot(tmp_brain) == before


def test_resolution_preserves_valid_then_invalid_request_order(
    tmp_brain: Path,
) -> None:
    valid = _enqueue_audit_resolution_action("resolution-order-valid-first")
    invalid = PendingResolutionAction(
        "convert_type",
        "resolution-order-invalid-second",
        "fact",
    )

    stats = PendingQueue().resolve([valid, invalid])

    assert [result.record_id for result in stats.results] == [
        "resolution-order-valid-first",
        "resolution-order-invalid-second",
    ]
    assert [result.reason for result in stats.results] == [
        "PENDING_RESOLUTION_READY",
        "PENDING_CONVERSION_UNSUPPORTED",
    ]


def test_resolution_preserves_invalid_valid_invalid_request_order(
    tmp_brain: Path,
) -> None:
    valid = _enqueue_audit_resolution_action("resolution-order-valid-middle")
    actions = [
        PendingResolutionAction(
            "approve_audit",
            "resolution-order-invalid-first",
            "decision",
        ),
        valid,
        PendingResolutionAction(
            "convert_type",
            "resolution-order-invalid-last",
            "fact",
        ),
    ]

    stats = PendingQueue().resolve(actions)

    assert [result.record_id for result in stats.results] == [
        "resolution-order-invalid-first",
        "resolution-order-valid-middle",
        "resolution-order-invalid-last",
    ]
    assert [result.reason for result in stats.results] == [
        "PENDING_RESOLUTION_NOT_APPLICABLE",
        "PENDING_RESOLUTION_READY",
        "PENDING_CONVERSION_UNSUPPORTED",
    ]


def test_resolution_omits_interleaved_exact_duplicate_without_reordering(
    tmp_brain: Path,
) -> None:
    valid = _enqueue_audit_resolution_action("resolution-order-deduplicated")
    invalid = PendingResolutionAction(
        "convert_type",
        "resolution-order-between-duplicates",
        "fact",
    )

    stats = PendingQueue().resolve([valid, invalid, valid, invalid])

    assert [result.record_id for result in stats.results] == [
        "resolution-order-deduplicated",
        "resolution-order-between-duplicates",
    ]
    assert [result.reason for result in stats.results] == [
        "PENDING_RESOLUTION_READY",
        "PENDING_CONVERSION_UNSUPPORTED",
    ]


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(UnhashableStr("ready"), id="unhashable-str-subclass"),
        pytest.param("PRIVATE_MODEL_CANARY", id="unknown-canary"),
    ],
)
def test_resolution_public_model_closes_all_counted_fields(value: object) -> None:
    result = pending_module.PendingResolutionResult(
        action="approve_audit",
        record_id="resolution-adversarial-model",
        classification=value,  # type: ignore[arg-type]
        status=value,  # type: ignore[arg-type]
        reason=value,  # type: ignore[arg-type]
    )
    stats = pending_module.PendingResolutionStats(
        dry_run=True,
        results=[result],
        governance_reason=value,  # type: ignore[arg-type]
    )

    payload = {"result": result.to_dict(), "summary": stats.to_summary_dict()}
    encoded = json.dumps(payload, sort_keys=True)

    assert result.status == "unknown"
    assert result.classification == "unknown"
    assert result.reason == "UNKNOWN_PENDING_REASON"
    assert stats.governance_reason == "UNKNOWN_PENDING_REASON"
    assert payload["summary"]["status_counts"] == {"unknown": 1}  # type: ignore[index]
    assert payload["summary"]["classification_counts"] == {"unknown": 1}  # type: ignore[index]
    assert payload["summary"]["reason_counts"] == {"UNKNOWN_PENDING_REASON": 1}  # type: ignore[index]
    assert str(value) not in encoded


@pytest.mark.parametrize("sensitivity", ["private", "secret"])
@pytest.mark.parametrize("action_name", ["accept_duplicate", "convert_type"])
def test_resolution_redacts_sensitive_item_ids(
    tmp_brain: Path,
    sensitivity: str,
    action_name: str,
) -> None:
    if action_name == "accept_duplicate":
        record = _v2_record(record_id=f"resolution-redact-{sensitivity}")
        item = record["item"]
        assert isinstance(item, dict)
        item["title"] = "PRIVATE_DUPLICATE_TITLE_CANARY"
        item["summary"] = "private duplicate summary canary"
        item["sensitivity"] = sensitivity
        target_id = f"mem-20260701-100000-redaction-target-{sensitivity}"
        _write_existing_item(
            tmp_brain,
            record,
            item_id=target_id,
            span_hash="different-payload",
        )
        enqueue_write_record(record)
        preview = PendingQueue().preview(limit=1).records[0]
        action = PendingResolutionAction(
            "accept_duplicate",
            preview.record_id,
            target_id,
        )
        slug_canary = "private-duplicate-title-canary"
    else:
        record = _legacy_feedback_record()
        item = record["item"]
        assert isinstance(item, dict)
        item["title"] = "PRIVATE_CONVERSION_TITLE_CANARY"
        item["summary"] = "private conversion summary canary"
        item["sensitivity"] = sensitivity
        enqueue_write_record(record)
        preview = PendingQueue().preview(limit=1).records[0]
        action = PendingResolutionAction("convert_type", preview.record_id, "decision")
        slug_canary = "private-conversion-title-canary"

    stats = PendingQueue().resolve([action])
    result = stats.results[0]
    encoded = json.dumps(
        {
            "preview": preview.to_dict(),
            "result": result.to_dict(),
            "summary": stats.to_summary_dict(),
        },
        sort_keys=True,
    ).lower()

    assert result.status == "ready"
    assert result.item_id is None
    assert slug_canary not in encoded


@pytest.mark.parametrize("drift", ["deleted", "content", "scope"])
def test_resolution_duplicate_revalidates_target_snapshot(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
) -> None:
    record = _v2_record(record_id=f"resolution-target-drift-{drift}")
    target_id = f"mem-20260701-100000-resolution-target-drift-{drift}"
    _write_existing_item(
        tmp_brain,
        record,
        item_id=target_id,
        span_hash="different-payload",
    )
    pending_path = enqueue_write_record(record)
    pending_bytes = pending_path.read_bytes()
    target_path = tmp_brain / "items" / f"{target_id}.md"
    target_item, target_body = ItemsStore(tmp_brain / "items").get_nofollow(target_id)
    real_scan = pending_module._scan_existing_item_metadata
    scans = 0

    def drift_before_second_scan(*args, **kwargs):
        nonlocal scans
        scans += 1
        if scans == 2:
            if drift == "deleted":
                target_path.unlink()
            else:
                update = (
                    {"summary": "target content drifted"}
                    if drift == "content"
                    else {"project": "other-scope"}
                )
                target_path.write_text(
                    render_item_markdown(
                        target_item.model_copy(update=update),
                        target_body,
                    ),
                    encoding="utf-8",
                )
        return real_scan(*args, **kwargs)

    monkeypatch.setattr(
        pending_module,
        "_scan_existing_item_metadata",
        drift_before_second_scan,
    )

    result = PendingQueue().resolve(
        [PendingResolutionAction("accept_duplicate", str(record["record_id"]), target_id)]
    ).results[0]

    assert scans == 2
    assert result.status == "blocked"
    assert result.reason == "PENDING_RESOLUTION_CHANGED"
    assert pending_path.read_bytes() == pending_bytes


def test_resolution_duplicate_batch_uses_one_fresh_target_scan(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actions: list[PendingResolutionAction] = []
    for index in range(2):
        record = _v2_record(record_id=f"resolution-batch-duplicate-{index}")
        item = record["item"]
        assert isinstance(item, dict)
        item["title"] = f"batch duplicate title {index}"
        item["summary"] = f"batch duplicate summary {index}"
        target_id = f"mem-20260701-100000-resolution-batch-target-{index}"
        _write_existing_item(
            tmp_brain,
            record,
            item_id=target_id,
            span_hash=f"different-payload-{index}",
        )
        enqueue_write_record(record)
        actions.append(
            PendingResolutionAction(
                "accept_duplicate",
                str(record["record_id"]),
                target_id,
            )
        )
    real_scan = pending_module._scan_existing_item_metadata
    scans = 0

    def counted_scan(*args, **kwargs):
        nonlocal scans
        scans += 1
        return real_scan(*args, **kwargs)

    monkeypatch.setattr(pending_module, "_scan_existing_item_metadata", counted_scan)

    stats = PendingQueue().resolve(actions)

    assert scans == 2
    assert [result.status for result in stats.results] == ["ready", "ready"]
    assert [result.record_id for result in stats.results] == [
        "resolution-batch-duplicate-0",
        "resolution-batch-duplicate-1",
    ]


def test_resolution_batch_target_drift_replacement_preserves_request_slot(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    duplicate_actions: list[PendingResolutionAction] = []
    drift_path: Path | None = None
    drift_item: MemoryItem | None = None
    drift_body = ""
    for index in range(2):
        record = _v2_record(record_id=f"resolution-order-duplicate-{index}")
        item = record["item"]
        assert isinstance(item, dict)
        item["title"] = f"order duplicate title {index}"
        item["summary"] = f"order duplicate summary {index}"
        target_id = f"mem-20260701-100000-resolution-order-target-{index}"
        _write_existing_item(
            tmp_brain,
            record,
            item_id=target_id,
            span_hash=f"different-order-payload-{index}",
        )
        enqueue_write_record(record)
        duplicate_actions.append(
            PendingResolutionAction(
                "accept_duplicate",
                str(record["record_id"]),
                target_id,
            )
        )
        if index == 1:
            drift_path = tmp_brain / "items" / f"{target_id}.md"
            drift_item, drift_body = ItemsStore(tmp_brain / "items").get_nofollow(
                target_id
            )
    assert drift_path is not None
    assert drift_item is not None
    invalid = PendingResolutionAction(
        "convert_type",
        "resolution-order-invalid-middle",
        "fact",
    )
    real_scan = pending_module._scan_existing_item_metadata
    scans = 0

    def drift_on_batch_refresh(*args, **kwargs):
        nonlocal scans
        scans += 1
        if scans == 2:
            drift_path.write_text(
                render_item_markdown(
                    drift_item.model_copy(update={"summary": "fresh target drift"}),
                    drift_body,
                ),
                encoding="utf-8",
            )
        return real_scan(*args, **kwargs)

    monkeypatch.setattr(
        pending_module,
        "_scan_existing_item_metadata",
        drift_on_batch_refresh,
    )

    stats = PendingQueue().resolve(
        [duplicate_actions[0], invalid, duplicate_actions[1]]
    )

    assert scans == 2
    assert [result.record_id for result in stats.results] == [
        "resolution-order-duplicate-0",
        "resolution-order-invalid-middle",
        "resolution-order-duplicate-1",
    ]
    assert [result.reason for result in stats.results] == [
        "PENDING_RESOLUTION_READY",
        "PENDING_CONVERSION_UNSUPPORTED",
        "PENDING_RESOLUTION_CHANGED",
    ]


def test_resolution_audit_digest_is_deterministic_and_order_independent() -> None:
    first = SimpleNamespace(rule_id="NET-002", severity="high", category="network")
    second = SimpleNamespace(rule_id="CODE-001", severity="critical", category="code")
    forward = SimpleNamespace(findings=[first, second])
    reverse = SimpleNamespace(findings=[second, first])
    expected = _expected_domain_digest(
        "amh.pending.audit-findings.v1",
        [
            ("CODE-001", "critical", "code"),
            ("NET-002", "high", "network"),
        ],
    )

    assert pending_module._audit_findings_digest(forward) == expected
    assert pending_module._audit_findings_digest(reverse) == expected


def test_resolution_duplicate_blocks_when_fresh_target_digest_changes(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _v2_record(record_id="resolution-target-digest-drift")
    target_id = "mem-20260701-100000-resolution-target-digest-drift"
    _write_existing_item(
        tmp_brain,
        record,
        item_id=target_id,
        span_hash="different-payload",
    )
    enqueue_write_record(record)
    digests = iter(("a" * 64, "b" * 64))
    domains: list[str] = []

    def drifting_digest(domain: str, _value: object) -> str:
        domains.append(domain)
        return next(digests)

    monkeypatch.setattr(pending_module, "_domain_digest", drifting_digest)

    result = PendingQueue().resolve(
        [PendingResolutionAction("accept_duplicate", str(record["record_id"]), target_id)]
    ).results[0]

    assert domains == [
        "amh.pending.resolution.target.v1",
        "amh.pending.resolution.target.v1",
    ]
    assert result.status == "blocked"
    assert result.reason == "PENDING_RESOLUTION_CHANGED"
