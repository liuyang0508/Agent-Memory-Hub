from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_brain.contracts.memory_item import MemoryItem, Source
from agent_brain.memory.store import pending as pending_module
from agent_brain.memory.store.item_markdown import render_item_markdown
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.store.pending import PendingQueue, enqueue_write_record


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


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


def _payload_sha256(record: dict[str, object]) -> str:
    payload = json.dumps(
        record["item"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stable_item_id(record: dict[str, object]) -> str:
    item = record["item"]
    assert isinstance(item, dict)
    created_at = datetime.fromisoformat(str(record["original_created_at"]))
    title = str(item["title"])
    slug = re.sub(r"[/\\]+", "-", "-".join(title.lower().split()))[:30].strip("-")
    stable = hashlib.sha256(str(record["record_id"]).encode("utf-8")).hexdigest()[:8]
    return f"mem-{created_at:%Y%m%d-%H%M%S}-{slug or 'pending'}-{stable}"


def _freeze_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "agent_brain.memory.store.pending._utc_now", lambda: NOW, raising=False
    )


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


def test_enqueue_then_replay_writes_item(tmp_brain: Path) -> None:
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
    stats = q.replay()
    assert stats.written == 1
    assert not path.exists()
    assert q.depth() == 0


def test_replay_is_idempotent_on_empty(tmp_brain: Path) -> None:
    assert PendingQueue().replay().written == 0


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
    assert first.age_seconds == int((NOW - datetime(2026, 7, 1, 10, tzinfo=timezone.utc)).total_seconds())
    assert first.classification == "ready"
    assert first.reason == "READY"


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


def test_oversized_pending_record_fails_closed_without_reading_it(
    tmp_brain: Path,
) -> None:
    pending = tmp_brain / "pending"
    pending.mkdir()
    path = pending / "oversized.jsonl"
    path.write_bytes(b"{" + b" " * (1024 * 1024) + b"}\n")

    preview = PendingQueue().preview(limit=10).records[0]

    assert preview.classification == "malformed"
    assert preview.reason == "PENDING_RECORD_TOO_LARGE"


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
    monkeypatch.setattr(pending_module, "secure_dir_fd_io_supported", lambda: False)

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
