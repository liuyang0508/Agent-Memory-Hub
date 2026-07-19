import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Sensitivity
from agent_brain.memory.governance import lifecycle_ledger as lifecycle_ledger_module
from agent_brain.memory.governance.lifecycle_ledger import (
    LifecycleLedgerRecord,
    append_lifecycle_record,
)
from agent_brain.memory.governance.lifecycle_snapshot import LifecycleSnapshotError
from agent_brain.memory.governance.supersession import SupersessionService
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.history import BrainHistory


def _item(
    item_id: str,
    *,
    project: str = "agent-memory-hub",
    tenant: str | None = None,
    tags: list[str] | None = None,
    sensitivity: Sensitivity | str | None = None,
    superseded_by: str | None = None,
) -> MemoryItem:
    fields = {
        "id": item_id,
        "type": MemoryType.signal,
        "created_at": datetime.now(timezone.utc),
        "title": item_id,
        "summary": f"summary {item_id}",
        "project": project,
        "tenant_id": tenant,
        "tags": tags if tags is not None else ["lifecycle"],
        "superseded_by": superseded_by,
    }
    if sensitivity is not None:
        fields["sensitivity"] = sensitivity
    return MemoryItem.model_validate(fields)


def _write_legacy_without_sensitivity(
    store: ItemsStore, item: MemoryItem, body: str
) -> None:
    path = store.write(item, body)
    text = path.read_text(encoding="utf-8")
    marker = "sensitivity: internal\n"
    assert marker in text
    path.write_text(text.replace(marker, "", 1), encoding="utf-8")


def _write_malformed_item(store: ItemsStore, item_id: str) -> None:
    (store.items_dir / f"{item_id}.md").write_text(
        "not valid memory frontmatter\n", encoding="utf-8"
    )


def _write_invalid_utf8_item(store: ItemsStore, item_id: str) -> None:
    (store.items_dir / f"{item_id}.md").write_bytes(b"\xff\xfe\xfa")


def _write_item_with_mismatched_id(
    store: ItemsStore, requested_id: str, actual_id: str
) -> None:
    written = store.write(_item(actual_id), "mismatched")
    written.replace(store.items_dir / f"{requested_id}.md")


def _seed_pair(brain_dir):
    store = ItemsStore(brain_dir / "items")
    old = _item("mem-20260719-100000-transaction-old")
    new = _item("mem-20260719-110000-transaction-new")
    store.write(old, "old body")
    store.write(new, "new body")
    return store, old, new


def test_preview_accepts_replacement_supersedes_obsolete(tmp_brain_dir):
    store = ItemsStore(tmp_brain_dir / "items")
    old = _item("mem-20260719-100000-old-signal")
    new = _item("mem-20260719-110000-new-signal")
    store.write(old, "old")
    store.write(new, "new")

    result = SupersessionService(tmp_brain_dir, store).preview(
        replacement_id=new.id,
        obsolete_id=old.id,
    )

    assert result.status == "ready"
    assert result.reason == "OK"
    assert result.replacement_id == new.id
    assert result.obsolete_id == old.id


def test_preview_accepts_legacy_items_with_default_sensitivity(tmp_brain_dir):
    store = ItemsStore(tmp_brain_dir / "items")
    obsolete = _item("mem-20260719-100000-default-obsolete")
    replacement = _item("mem-20260719-110000-default-replacement")
    assert obsolete.sensitivity is Sensitivity.internal
    assert replacement.sensitivity is Sensitivity.internal
    _write_legacy_without_sensitivity(store, obsolete, "obsolete")
    _write_legacy_without_sensitivity(store, replacement, "replacement")

    result = SupersessionService(tmp_brain_dir, store).preview(
        replacement.id, obsolete.id
    )

    assert result.status == "ready"
    assert result.reason == "OK"


def test_preview_compares_default_enum_sensitivity_without_error(tmp_brain_dir):
    store = ItemsStore(tmp_brain_dir / "items")
    obsolete = _item(
        "mem-20260719-100000-explicit-public",
        sensitivity=Sensitivity.public,
    )
    replacement = _item("mem-20260719-110000-default-internal")
    assert replacement.sensitivity is Sensitivity.internal
    store.write(obsolete, "obsolete")
    _write_legacy_without_sensitivity(store, replacement, "replacement")

    result = SupersessionService(tmp_brain_dir, store).preview(
        replacement.id, obsolete.id
    )

    assert result.status == "blocked"
    assert result.reason == "VISIBILITY_REDUCTION"


def test_preview_rejects_self_cycle_cross_tenant_and_cross_project(tmp_brain_dir):
    store = ItemsStore(tmp_brain_dir / "items")
    left = _item("mem-20260719-100000-left", tenant="tenant-a")
    right = _item("mem-20260719-110000-right", tenant="tenant-b")
    other = _item("mem-20260719-120000-other", project="other", tenant="tenant-a")
    for item in (left, right, other):
        store.write(item, item.title)

    service = SupersessionService(tmp_brain_dir, store)
    assert service.preview(left.id, left.id).reason == "SELF_SUPERSESSION"
    assert service.preview(right.id, left.id).reason == "TENANT_MISMATCH"
    assert service.preview(other.id, left.id).reason == "PROJECT_MISMATCH"


def test_preview_rejects_cycle_through_replacement_chain(tmp_brain_dir):
    store = ItemsStore(tmp_brain_dir / "items")
    obsolete = _item("mem-20260719-100000-cycle-obsolete")
    replacement = _item(
        "mem-20260719-110000-cycle-replacement",
        superseded_by=obsolete.id,
    )
    store.write(obsolete, "obsolete")
    store.write(replacement, "replacement")

    result = SupersessionService(tmp_brain_dir, store).preview(
        replacement.id, obsolete.id
    )

    assert result.status == "blocked"
    assert result.reason == "SUPERSESSION_CYCLE"


def test_preview_rejects_missing_item(tmp_brain_dir):
    store = ItemsStore(tmp_brain_dir / "items")
    obsolete = _item("mem-20260719-100000-existing")
    store.write(obsolete, "obsolete")

    result = SupersessionService(tmp_brain_dir, store).preview(
        "mem-20260719-110000-missing", obsolete.id
    )

    assert result.status == "blocked"
    assert result.reason == "ITEM_MISSING"


def test_preview_rejects_path_ids_without_reading_outside_store(
    tmp_brain_dir, monkeypatch
):
    store = ItemsStore(tmp_brain_dir / "items")
    valid = _item("mem-20260719-100000-valid-boundary")
    store.write(valid, "valid")
    absolute_base = tmp_brain_dir / "absolute-outside"
    cases = [
        ("../outside", tmp_brain_dir / "outside.md"),
        (str(absolute_base), Path(f"{absolute_base}.md")),
        ("nested/outside", store.items_dir / "nested" / "outside.md"),
    ]
    for _, sentinel in cases:
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("sentinel must not be read\n", encoding="utf-8")

    original_read_text = Path.read_text
    read_paths: list[Path] = []

    def tracking_read_text(path: Path, *args, **kwargs):
        read_paths.append(path.resolve())
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracking_read_text)
    service = SupersessionService(tmp_brain_dir, store)

    for invalid_id, _ in cases:
        read_paths.clear()
        result = service.preview(invalid_id, valid.id)
        assert result.status == "blocked"
        assert result.reason == "INVALID_ITEM_ID"
        assert read_paths == []

        result = service.preview(valid.id, invalid_id)
        assert result.status == "blocked"
        assert result.reason == "INVALID_ITEM_ID"
        assert read_paths == []


@pytest.mark.parametrize("malformed_role", ["replacement", "obsolete"])
def test_preview_rejects_malformed_primary_item(tmp_brain_dir, malformed_role):
    store = ItemsStore(tmp_brain_dir / "items")
    replacement_id = "mem-20260719-110000-malformed-replacement"
    obsolete_id = "mem-20260719-100000-malformed-obsolete"
    if malformed_role == "replacement":
        _write_malformed_item(store, replacement_id)
        store.write(_item(obsolete_id), "obsolete")
    else:
        store.write(_item(replacement_id), "replacement")
        _write_malformed_item(store, obsolete_id)

    result = SupersessionService(tmp_brain_dir, store).preview(
        replacement_id, obsolete_id
    )

    assert result.status == "blocked"
    assert result.reason == "ITEM_INVALID"


def test_preview_rejects_primary_item_with_invalid_utf8(tmp_brain_dir):
    store = ItemsStore(tmp_brain_dir / "items")
    replacement_id = "mem-20260719-110000-invalid-utf8-replacement"
    obsolete = _item("mem-20260719-100000-invalid-utf8-obsolete")
    _write_invalid_utf8_item(store, replacement_id)
    store.write(obsolete, "obsolete")

    result = SupersessionService(tmp_brain_dir, store).preview(
        replacement_id, obsolete.id
    )

    assert result.status == "blocked"
    assert result.reason == "ITEM_INVALID"


@pytest.mark.parametrize(
    "error_type",
    [TypeError, RuntimeError, RecursionError],
)
def test_preview_propagates_programming_errors(
    tmp_brain_dir, monkeypatch, error_type
):
    store = ItemsStore(tmp_brain_dir / "items")

    def raising_get(_item_id):
        raise error_type("programming failure")

    monkeypatch.setattr(store, "get", raising_get)
    service = SupersessionService(tmp_brain_dir, store)

    with pytest.raises(error_type, match="programming failure"):
        service.preview(
            "mem-20260719-110000-error-replacement",
            "mem-20260719-100000-error-obsolete",
        )


@pytest.mark.parametrize("mismatched_role", ["replacement", "obsolete"])
def test_preview_rejects_primary_frontmatter_id_mismatch(
    tmp_brain_dir, mismatched_role
):
    store = ItemsStore(tmp_brain_dir / "items")
    replacement_id = "mem-20260719-110000-requested-replacement"
    obsolete_id = "mem-20260719-100000-requested-obsolete"
    if mismatched_role == "replacement":
        _write_item_with_mismatched_id(
            store,
            replacement_id,
            "mem-20260719-110001-actual-replacement",
        )
        store.write(_item(obsolete_id), "obsolete")
    else:
        store.write(_item(replacement_id), "replacement")
        _write_item_with_mismatched_id(
            store,
            obsolete_id,
            "mem-20260719-100001-actual-obsolete",
        )

    result = SupersessionService(tmp_brain_dir, store).preview(
        replacement_id, obsolete_id
    )

    assert result.status == "blocked"
    assert result.reason == "ITEM_INVALID"


@pytest.mark.parametrize(
    "review_tag",
    [
        "needs-review",
        "NEEDS-REVIEW",
        "requires-review",
        "REQUIRES-REVIEW",
        "review-rejected",
        "REVIEW-REJECTED",
        "unverified-boundary",
        "UNVERIFIED-BOUNDARY",
    ],
)
def test_preview_rejects_replacement_with_review_required_tag(
    tmp_brain_dir, review_tag
):
    store = ItemsStore(tmp_brain_dir / "items")
    obsolete = _item("mem-20260719-100000-reviewed")
    replacement = _item(
        "mem-20260719-110000-needs-review",
        tags=["lifecycle", review_tag],
    )
    store.write(obsolete, "obsolete")
    store.write(replacement, "replacement")

    result = SupersessionService(tmp_brain_dir, store).preview(
        replacement.id, obsolete.id
    )

    assert result.status == "blocked"
    assert result.reason == "REPLACEMENT_REQUIRES_REVIEW"


def test_preview_rejects_visibility_reduction(tmp_brain_dir):
    store = ItemsStore(tmp_brain_dir / "items")
    obsolete = _item(
        "mem-20260719-100000-public", sensitivity=Sensitivity.public
    )
    replacement = _item(
        "mem-20260719-110000-internal", sensitivity=Sensitivity.internal
    )
    store.write(obsolete, "obsolete")
    store.write(replacement, "replacement")

    result = SupersessionService(tmp_brain_dir, store).preview(
        replacement.id, obsolete.id
    )

    assert result.status == "blocked"
    assert result.reason == "VISIBILITY_REDUCTION"


def test_preview_accepts_visibility_expansion(tmp_brain_dir):
    store = ItemsStore(tmp_brain_dir / "items")
    obsolete = _item(
        "mem-20260719-100000-secret", sensitivity=Sensitivity.secret
    )
    replacement = _item(
        "mem-20260719-110000-public-replacement",
        sensitivity=Sensitivity.public,
    )
    store.write(obsolete, "obsolete")
    store.write(replacement, "replacement")

    result = SupersessionService(tmp_brain_dir, store).preview(
        replacement.id, obsolete.id
    )

    assert result.status == "ready"
    assert result.reason == "OK"


def test_preview_reports_already_applied(tmp_brain_dir):
    store = ItemsStore(tmp_brain_dir / "items")
    replacement = _item("mem-20260719-110000-applied-replacement")
    obsolete = _item(
        "mem-20260719-100000-applied-obsolete",
        superseded_by=replacement.id,
    )
    store.write(obsolete, "obsolete")
    store.write(replacement, "replacement")

    result = SupersessionService(tmp_brain_dir, store).preview(
        replacement.id, obsolete.id
    )

    assert result.to_dict() == {
        "status": "already_applied",
        "reason": "ALREADY_APPLIED",
        "replacement_id": replacement.id,
        "obsolete_id": obsolete.id,
        "dry_run": True,
        "snapshot": None,
        "index_repair_required": False,
    }


def test_preview_rejects_obsolete_that_is_already_superseded(tmp_brain_dir):
    store = ItemsStore(tmp_brain_dir / "items")
    obsolete = _item(
        "mem-20260719-100000-previously-obsolete",
        superseded_by="mem-20260719-105000-existing-replacement",
    )
    replacement = _item("mem-20260719-110000-different-replacement")
    store.write(obsolete, "obsolete")
    store.write(replacement, "replacement")

    result = SupersessionService(tmp_brain_dir, store).preview(
        replacement.id, obsolete.id
    )

    assert result.status == "blocked"
    assert result.reason == "OBSOLETE_ALREADY_SUPERSEDED"


def test_preview_rejects_broken_replacement_chain(tmp_brain_dir):
    store = ItemsStore(tmp_brain_dir / "items")
    obsolete = _item("mem-20260719-100000-broken-chain-obsolete")
    replacement = _item(
        "mem-20260719-110000-broken-chain-replacement",
        superseded_by="mem-20260719-105000-missing-chain-link",
    )
    store.write(obsolete, "obsolete")
    store.write(replacement, "replacement")

    result = SupersessionService(tmp_brain_dir, store).preview(
        replacement.id, obsolete.id
    )

    assert result.status == "blocked"
    assert result.reason == "BROKEN_REPLACEMENT_CHAIN"


def test_preview_rejects_invalid_chain_pointer_without_reading_outside_store(
    tmp_brain_dir, monkeypatch
):
    store = ItemsStore(tmp_brain_dir / "items")
    obsolete = _item("mem-20260719-100000-invalid-chain-obsolete")
    replacement = _item(
        "mem-20260719-110000-invalid-chain-replacement",
        superseded_by="../outside-chain",
    )
    store.write(obsolete, "obsolete")
    store.write(replacement, "replacement")
    sentinel = tmp_brain_dir / "outside-chain.md"
    sentinel.write_text("sentinel must not be read\n", encoding="utf-8")
    original_read_text = Path.read_text
    read_paths: list[Path] = []

    def tracking_read_text(path: Path, *args, **kwargs):
        read_paths.append(path.resolve())
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracking_read_text)

    result = SupersessionService(tmp_brain_dir, store).preview(
        replacement.id, obsolete.id
    )

    assert result.status == "blocked"
    assert result.reason == "BROKEN_REPLACEMENT_CHAIN"
    assert sentinel.resolve() not in read_paths


def test_preview_rejects_malformed_replacement_chain_item(tmp_brain_dir):
    store = ItemsStore(tmp_brain_dir / "items")
    chain_id = "mem-20260719-105000-malformed-chain"
    obsolete = _item("mem-20260719-100000-malformed-chain-obsolete")
    replacement = _item(
        "mem-20260719-110000-malformed-chain-replacement",
        superseded_by=chain_id,
    )
    store.write(obsolete, "obsolete")
    store.write(replacement, "replacement")
    _write_malformed_item(store, chain_id)

    result = SupersessionService(tmp_brain_dir, store).preview(
        replacement.id, obsolete.id
    )

    assert result.status == "blocked"
    assert result.reason == "BROKEN_REPLACEMENT_CHAIN"


def test_preview_rejects_replacement_chain_item_with_invalid_utf8(tmp_brain_dir):
    store = ItemsStore(tmp_brain_dir / "items")
    chain_id = "mem-20260719-105000-invalid-utf8-chain"
    obsolete = _item("mem-20260719-100000-invalid-utf8-chain-obsolete")
    replacement = _item(
        "mem-20260719-110000-invalid-utf8-chain-replacement",
        superseded_by=chain_id,
    )
    store.write(obsolete, "obsolete")
    store.write(replacement, "replacement")
    _write_invalid_utf8_item(store, chain_id)

    result = SupersessionService(tmp_brain_dir, store).preview(
        replacement.id, obsolete.id
    )

    assert result.status == "blocked"
    assert result.reason == "BROKEN_REPLACEMENT_CHAIN"


def test_preview_rejects_replacement_chain_frontmatter_id_mismatch(tmp_brain_dir):
    store = ItemsStore(tmp_brain_dir / "items")
    chain_id = "mem-20260719-105000-requested-chain"
    obsolete = _item("mem-20260719-100000-mismatched-chain-obsolete")
    replacement = _item(
        "mem-20260719-110000-mismatched-chain-replacement",
        superseded_by=chain_id,
    )
    store.write(obsolete, "obsolete")
    store.write(replacement, "replacement")
    _write_item_with_mismatched_id(
        store,
        chain_id,
        "mem-20260719-105001-actual-chain",
    )

    result = SupersessionService(tmp_brain_dir, store).preview(
        replacement.id, obsolete.id
    )

    assert result.status == "blocked"
    assert result.reason == "BROKEN_REPLACEMENT_CHAIN"


def test_preview_rejects_multihop_cycle_through_obsolete(tmp_brain_dir):
    store = ItemsStore(tmp_brain_dir / "items")
    obsolete = _item("mem-20260719-100000-multihop-obsolete")
    middle = _item(
        "mem-20260719-105000-multihop-middle",
        superseded_by=obsolete.id,
    )
    replacement = _item(
        "mem-20260719-110000-multihop-replacement",
        superseded_by=middle.id,
    )
    for item in (obsolete, middle, replacement):
        store.write(item, item.title)

    result = SupersessionService(tmp_brain_dir, store).preview(
        replacement.id, obsolete.id
    )

    assert result.status == "blocked"
    assert result.reason == "SUPERSESSION_CYCLE"


def test_preview_rejects_replacement_chain_self_cycle(tmp_brain_dir):
    store = ItemsStore(tmp_brain_dir / "items")
    obsolete = _item("mem-20260719-100000-self-chain-obsolete")
    middle_id = "mem-20260719-105000-self-chain-middle"
    middle = _item(middle_id, superseded_by=middle_id)
    replacement = _item(
        "mem-20260719-110000-self-chain-replacement",
        superseded_by=middle.id,
    )
    for item in (obsolete, middle, replacement):
        store.write(item, item.title)

    result = SupersessionService(tmp_brain_dir, store).preview(
        replacement.id, obsolete.id
    )

    assert result.status == "blocked"
    assert result.reason == "SUPERSESSION_CYCLE"


def test_preview_does_not_modify_brain_files(tmp_brain_dir):
    store = ItemsStore(tmp_brain_dir / "items")
    obsolete = _item("mem-20260719-100000-read-only-obsolete")
    replacement = _item("mem-20260719-110000-read-only-replacement")
    store.write(obsolete, "obsolete")
    store.write(replacement, "replacement")
    before = {
        path.relative_to(tmp_brain_dir): path.read_bytes()
        for path in tmp_brain_dir.rglob("*")
        if path.is_file()
    }

    SupersessionService(tmp_brain_dir, store).preview(replacement.id, obsolete.id)

    after = {
        path.relative_to(tmp_brain_dir): path.read_bytes()
        for path in tmp_brain_dir.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_apply_updates_both_markdown_items_and_writes_private_safe_ledger(
    tmp_brain_dir,
):
    store, old, new = _seed_pair(tmp_brain_dir)

    result = SupersessionService(tmp_brain_dir, store).apply(
        new.id, old.id, apply=True
    )

    old_after, _ = store.get(old.id)
    new_after, _ = store.get(new.id)
    assert result.status == "applied"
    assert result.dry_run is False
    assert old_after.superseded_by == new.id
    assert old.id in new_after.refs.mems
    ledger_path = tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl"
    ledger = ledger_path.read_text(encoding="utf-8")
    assert old.id in ledger and new.id in ledger
    assert "old body" not in ledger and "new body" not in ledger
    record = json.loads(ledger)
    assert set(record) == {
        "action",
        "timestamp",
        "status",
        "reason",
        "obsolete_id",
        "replacement_id",
        "snapshot",
        "replacement_ref_preexisted",
    }
    assert record["replacement_ref_preexisted"] is False
    assert ledger_path.stat().st_mode & 0o777 == 0o600


def test_apply_is_idempotent_and_revert_restores_only_transaction_added_ref(
    tmp_brain_dir,
):
    store, old, new = _seed_pair(tmp_brain_dir)
    service = SupersessionService(tmp_brain_dir, store)

    first = service.apply(new.id, old.id, apply=True)
    ledger_after_first = (
        tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl"
    ).read_bytes()
    second = service.apply(new.id, old.id, apply=True)
    reverted = service.revert(new.id, old.id, apply=True)

    assert first.status == "applied"
    assert second.status == "already_applied"
    assert second.dry_run is False
    assert reverted.status == "reverted"
    assert store.get(old.id)[0].superseded_by is None
    assert old.id not in store.get(new.id)[0].refs.mems
    ledger_lines = (
        tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl"
    ).read_bytes().splitlines()
    assert ledger_lines[0] + b"\n" == ledger_after_first
    assert len(ledger_lines) == 2


def test_lifecycle_snapshot_does_not_create_or_use_outer_brain_history(
    tmp_brain_dir,
):
    store, old, new = _seed_pair(tmp_brain_dir)
    service = SupersessionService(tmp_brain_dir, store)

    applied = service.apply(new.id, old.id, apply=True)
    assert applied.snapshot is not None
    assert service.revert(new.id, old.id, apply=True).status == "reverted"
    ledger_path = tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl"
    assert ledger_path.read_bytes()
    assert not (tmp_brain_dir / ".git").exists()
    assert (tmp_brain_dir / "runtime" / "lifecycle-history.git").is_dir()


def test_revert_preserves_reference_that_preexisted_supersession(tmp_brain_dir):
    store, old, new = _seed_pair(tmp_brain_dir)
    store.link_mem(new.id, old.id)
    service = SupersessionService(tmp_brain_dir, store)

    assert service.apply(new.id, old.id, apply=True).status == "applied"
    result = service.revert(new.id, old.id, apply=True)

    assert result.status == "reverted"
    assert store.get(old.id)[0].superseded_by is None
    assert old.id in store.get(new.id)[0].refs.mems


def test_revert_preserves_reference_when_matching_ledger_boolean_is_malformed(
    tmp_brain_dir,
):
    store, old, new = _seed_pair(tmp_brain_dir)
    store.link_mem(new.id, old.id)
    service = SupersessionService(tmp_brain_dir, store)
    assert service.apply(new.id, old.id, apply=True).status == "applied"
    ledger_path = tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl"
    record = json.loads(ledger_path.read_text(encoding="utf-8"))
    record["replacement_ref_preexisted"] = 0
    ledger_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    result = service.revert(new.id, old.id, apply=True)

    assert result.status == "reverted"
    assert old.id in store.get(new.id)[0].refs.mems


def test_revert_does_not_reuse_apply_record_before_later_successful_revert(
    tmp_brain_dir,
):
    store, old, new = _seed_pair(tmp_brain_dir)
    service = SupersessionService(tmp_brain_dir, store)
    assert service.apply(new.id, old.id, apply=True).status == "applied"
    assert service.revert(new.id, old.id, apply=True).status == "reverted"
    store.link_mem(new.id, old.id)
    store.update_frontmatter(old.id, superseded_by=new.id)

    result = service.revert(new.id, old.id, apply=True)

    assert result.status == "reverted"
    assert old.id in store.get(new.id)[0].refs.mems


def test_revert_does_not_skip_malformed_newer_matching_transaction(
    tmp_brain_dir,
):
    store, old, new = _seed_pair(tmp_brain_dir)
    service = SupersessionService(tmp_brain_dir, store)
    assert service.apply(new.id, old.id, apply=True).status == "applied"
    ledger_path = tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl"
    malformed_newer = json.loads(ledger_path.read_text(encoding="utf-8"))
    malformed_newer["timestamp"] = "2026-07-19T23:59:59+00:00"
    malformed_newer["replacement_ref_preexisted"] = 0
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(malformed_newer) + "\n")

    result = service.revert(new.id, old.id, apply=True)

    assert result.status == "reverted"
    assert old.id in store.get(new.id)[0].refs.mems


@pytest.mark.parametrize(
    "malformed_line",
    [
        "{truncated",
        "[]",
        json.dumps(
            {
                "obsolete_id": "mem-20260719-100000-transaction-old",
                "replacement_id": "mem-20260719-110000-transaction-new",
            }
        ),
        json.dumps(
            {
                "action": "supersede",
                "timestamp": "2026-07-19T23:59:59+00:00",
                "status": "applied",
                "reason": "OK",
                "obsolete_id": "mem-20260719-100000-transaction-old",
                "replacement_id": "mem-20260719-110000-transaction-new",
                "snapshot": None,
                "replacement_ref_preexisted": 0,
            }
        ),
    ],
)
def test_revert_treats_any_malformed_ledger_tail_as_conservative_barrier(
    tmp_brain_dir, malformed_line
):
    store, old, new = _seed_pair(tmp_brain_dir)
    service = SupersessionService(tmp_brain_dir, store)
    assert service.apply(new.id, old.id, apply=True).status == "applied"
    ledger_path = tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl"
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(malformed_line + "\n")

    result = service.revert(new.id, old.id, apply=True)

    assert result.status == "reverted"
    assert old.id in store.get(new.id)[0].refs.mems


def test_apply_false_is_preview_only_without_snapshot_or_ledger(tmp_brain_dir):
    store, old, new = _seed_pair(tmp_brain_dir)
    before = {
        path.relative_to(tmp_brain_dir): path.read_bytes()
        for path in tmp_brain_dir.rglob("*")
        if path.is_file()
    }

    result = SupersessionService(tmp_brain_dir, store).apply(new.id, old.id)

    after = {
        path.relative_to(tmp_brain_dir): path.read_bytes()
        for path in tmp_brain_dir.rglob("*")
        if path.is_file()
    }
    assert result.status == "ready"
    assert result.dry_run is True
    assert before == after
    assert not (tmp_brain_dir / ".git").exists()
    assert not (tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl").exists()


def test_platform_unsupported_apply_is_side_effect_free(tmp_brain_dir, monkeypatch):
    store, old, new = _seed_pair(tmp_brain_dir)
    before = {
        path.relative_to(tmp_brain_dir): path.read_bytes()
        for path in tmp_brain_dir.rglob("*")
        if path.is_file()
    }
    monkeypatch.setattr(
        "agent_brain.memory.governance.supersession.lifecycle_mutation_capability",
        lambda: False,
    )

    result = SupersessionService(tmp_brain_dir, store).apply(new.id, old.id, apply=True)

    after = {
        path.relative_to(tmp_brain_dir): path.read_bytes()
        for path in tmp_brain_dir.rglob("*")
        if path.is_file()
    }
    assert result.status == "blocked"
    assert result.reason == "PLATFORM_UNSUPPORTED"
    assert result.dry_run is False
    assert after == before
    assert not (tmp_brain_dir / "runtime").exists()


def test_missing_git_apply_is_blocked_before_runtime_creation(
    tmp_brain_dir, monkeypatch
):
    store, old, new = _seed_pair(tmp_brain_dir)
    monkeypatch.setenv("PATH", "")

    service = SupersessionService(tmp_brain_dir, store)
    preview = service.apply(new.id, old.id, apply=False)
    result = service.apply(new.id, old.id, apply=True)

    assert preview.status == "ready"
    assert result.status == "blocked"
    assert result.reason == "PLATFORM_UNSUPPORTED"
    assert result.dry_run is False
    assert not (tmp_brain_dir / "runtime").exists()


def test_platform_unsupported_revert_is_side_effect_free(tmp_brain_dir, monkeypatch):
    store, old, new = _seed_pair(tmp_brain_dir)
    store.update_frontmatter(old.id, superseded_by=new.id)
    store.link_mem(new.id, old.id)
    before = {
        path.relative_to(tmp_brain_dir): path.read_bytes()
        for path in tmp_brain_dir.rglob("*")
        if path.is_file()
    }
    monkeypatch.setattr(
        "agent_brain.memory.governance.supersession.lifecycle_mutation_capability",
        lambda: False,
    )

    result = SupersessionService(tmp_brain_dir, store).revert(
        new.id, old.id, apply=True
    )

    after = {
        path.relative_to(tmp_brain_dir): path.read_bytes()
        for path in tmp_brain_dir.rglob("*")
        if path.is_file()
    }
    assert result.status == "blocked"
    assert result.reason == "PLATFORM_UNSUPPORTED"
    assert result.dry_run is False
    assert after == before
    assert not (tmp_brain_dir / "runtime").exists()


def test_apply_revalidates_current_markdown_after_earlier_preview(
    tmp_brain_dir, monkeypatch
):
    store, old, new = _seed_pair(tmp_brain_dir)
    service = SupersessionService(tmp_brain_dir, store)
    original_preview = service.preview

    def stale_preview(replacement_id, obsolete_id):
        result = original_preview(replacement_id, obsolete_id)
        store.update_frontmatter(replacement_id, project="different-project")
        return result

    monkeypatch.setattr(service, "preview", stale_preview)

    result = service.apply(new.id, old.id, apply=True)

    assert result.status == "blocked"
    assert result.reason == "PROJECT_MISMATCH"
    assert result.dry_run is False
    assert store.get(old.id)[0].superseded_by is None
    assert not (tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl").exists()


def test_apply_snapshot_failure_is_closed_before_markdown_ledger_or_index(
    tmp_brain_dir, monkeypatch
):
    store, old, new = _seed_pair(tmp_brain_dir)
    old_path = store.items_dir / f"{old.id}.md"
    new_path = store.items_dir / f"{new.id}.md"
    before = (old_path.read_bytes(), new_path.read_bytes())

    class RecordingIndex:
        calls = []

        def upsert(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    service = SupersessionService(tmp_brain_dir, store, index=RecordingIndex())
    monkeypatch.setattr(
        service.snapshot_store,
        "snapshot_pair",
        lambda *_args: (_ for _ in ()).throw(LifecycleSnapshotError("SNAPSHOT_FAILED")),
    )

    result = service.apply(new.id, old.id, apply=True)

    assert result.status == "blocked"
    assert result.reason == "SNAPSHOT_FAILED"
    assert result.dry_run is False
    assert (old_path.read_bytes(), new_path.read_bytes()) == before
    assert not (tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl").exists()
    assert service.index.calls == []


def test_apply_snapshot_rejects_store_outside_canonical_brain_items(
    tmp_brain_dir,
):
    external_items = tmp_brain_dir / "other-items"
    store = ItemsStore(external_items)
    old = _item("mem-20260719-100000-external-store-old")
    new = _item("mem-20260719-110000-external-store-new")
    store.write(old, "old body")
    store.write(new, "new body")
    before = {
        path.name: path.read_bytes() for path in external_items.glob("*.md")
    }

    result = SupersessionService(tmp_brain_dir, store).apply(
        new.id, old.id, apply=True
    )

    assert result.status == "blocked"
    assert result.reason == "SNAPSHOT_FAILED"
    assert {
        path.name: path.read_bytes() for path in external_items.glob("*.md")
    } == before
    assert not (tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl").exists()


def test_revert_snapshot_failure_is_closed_before_markdown_ledger_or_index(
    tmp_brain_dir, monkeypatch
):
    store, old, new = _seed_pair(tmp_brain_dir)
    service = SupersessionService(tmp_brain_dir, store)
    assert service.apply(new.id, old.id, apply=True).status == "applied"
    old_path = store.items_dir / f"{old.id}.md"
    new_path = store.items_dir / f"{new.id}.md"
    ledger_path = tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl"
    before = (old_path.read_bytes(), new_path.read_bytes(), ledger_path.read_bytes())
    monkeypatch.setattr(
        service.snapshot_store,
        "snapshot_pair",
        lambda *_args: (_ for _ in ()).throw(LifecycleSnapshotError("SNAPSHOT_FAILED")),
    )

    result = service.revert(new.id, old.id, apply=True)

    assert result.status == "blocked"
    assert result.reason == "SNAPSHOT_FAILED"
    assert result.dry_run is False
    assert (
        old_path.read_bytes(),
        new_path.read_bytes(),
        ledger_path.read_bytes(),
    ) == before


def test_apply_rolls_back_both_markdown_files_when_second_update_fails(
    tmp_brain_dir, monkeypatch
):
    store, old, new = _seed_pair(tmp_brain_dir)
    old_path = store.items_dir / f"{old.id}.md"
    new_path = store.items_dir / f"{new.id}.md"
    old_before = old_path.read_bytes()
    new_before = new_path.read_bytes()

    def fail_link(_source_id, _target_id):
        raise OSError("injected second markdown failure")

    monkeypatch.setattr(store, "link_mem", fail_link)

    result = SupersessionService(tmp_brain_dir, store).apply(
        new.id, old.id, apply=True
    )

    assert result.status == "blocked"
    assert result.reason == "MARKDOWN_UPDATE_FAILED"
    assert result.dry_run is False
    assert old_path.read_bytes() == old_before
    assert new_path.read_bytes() == new_before
    ledger = (tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl").read_text(
        encoding="utf-8"
    )
    assert "MARKDOWN_UPDATE_FAILED" in ledger
    assert store.get(old.id)[0].superseded_by is None


def test_apply_rolls_back_markdown_before_reraising_keyboard_interrupt(
    tmp_brain_dir, monkeypatch
):
    store, old, new = _seed_pair(tmp_brain_dir)
    old_path = store.items_dir / f"{old.id}.md"
    new_path = store.items_dir / f"{new.id}.md"
    old_before = old_path.read_bytes()
    new_before = new_path.read_bytes()

    def interrupt_link(_source_id, _target_id):
        raise KeyboardInterrupt

    monkeypatch.setattr(store, "link_mem", interrupt_link)

    with pytest.raises(KeyboardInterrupt):
        SupersessionService(tmp_brain_dir, store).apply(
            new.id, old.id, apply=True
        )

    assert old_path.read_bytes() == old_before
    assert new_path.read_bytes() == new_before
    ledger = (tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl").read_text(
        encoding="utf-8"
    )
    assert json.loads(ledger.splitlines()[-1])["reason"] == "MARKDOWN_UPDATE_FAILED"


def test_apply_uses_snapshot_fallback_when_raw_pair_restore_fails(
    tmp_brain_dir, monkeypatch
):
    store, old, new = _seed_pair(tmp_brain_dir)
    old_path = store.items_dir / f"{old.id}.md"
    new_path = store.items_dir / f"{new.id}.md"
    old_before = old_path.read_bytes()
    new_before = new_path.read_bytes()
    service = SupersessionService(tmp_brain_dir, store)
    real_snapshot_restore = service._restore_snapshot_pair
    snapshot_restores = []

    def fail_link(_source_id, _target_id):
        raise OSError("injected markdown failure")

    def fail_raw_restore(_item_id, _data):
        raise OSError("injected raw rollback failure")

    def tracking_snapshot_restore(ref, obsolete_id, replacement_id):
        snapshot_restores.append(ref)
        return real_snapshot_restore(ref, obsolete_id, replacement_id)

    monkeypatch.setattr(store, "link_mem", fail_link)
    monkeypatch.setattr(store, "restore_raw", fail_raw_restore)
    monkeypatch.setattr(service, "_restore_snapshot_pair", tracking_snapshot_restore)

    result = service.apply(new.id, old.id, apply=True)

    assert result.status == "blocked"
    assert result.reason == "MARKDOWN_UPDATE_FAILED"
    assert len(snapshot_restores) == 1
    assert old_path.read_bytes() == old_before
    assert new_path.read_bytes() == new_before


def test_apply_private_snapshot_does_not_reuse_or_move_existing_outer_head(
    tmp_brain_dir, monkeypatch
):
    store, old, new = _seed_pair(tmp_brain_dir)
    baseline = BrainHistory(tmp_brain_dir).snapshot("baseline")
    assert baseline is not None
    outer_diff_before = subprocess.run(
        ["git", "-C", str(tmp_brain_dir), "diff", "--binary"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    old_path = store.items_dir / f"{old.id}.md"
    new_path = store.items_dir / f"{new.id}.md"
    old_before = old_path.read_bytes()
    new_before = new_path.read_bytes()

    def fail_link(_source_id, _target_id):
        raise OSError("injected markdown failure")

    def fail_raw_restore(_item_id, _data):
        raise OSError("injected raw rollback failure")

    monkeypatch.setattr(store, "link_mem", fail_link)
    monkeypatch.setattr(store, "restore_raw", fail_raw_restore)

    result = SupersessionService(tmp_brain_dir, store).apply(
        new.id, old.id, apply=True
    )

    assert result.status == "blocked"
    assert result.reason == "MARKDOWN_UPDATE_FAILED"
    assert result.snapshot is not None
    assert BrainHistory(tmp_brain_dir).log(limit=1)[0]["sha"] == baseline
    outer_diff_after = subprocess.run(
        ["git", "-C", str(tmp_brain_dir), "diff", "--binary"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert outer_diff_after == outer_diff_before
    assert old_path.read_bytes() == old_before
    assert new_path.read_bytes() == new_before


def test_exact_raw_rollback_bytes_skip_full_history_fallback_even_if_restore_raises(
    tmp_brain_dir, monkeypatch
):
    store, old, new = _seed_pair(tmp_brain_dir)
    old_path = store.items_dir / f"{old.id}.md"
    new_path = store.items_dir / f"{new.id}.md"
    old_before = old_path.read_bytes()
    new_before = new_path.read_bytes()
    real_restore_raw = store.restore_raw
    service = SupersessionService(tmp_brain_dir, store)
    snapshot_restore_calls = []

    def fail_link(_source_id, _target_id):
        raise OSError("injected markdown failure")

    def restore_then_raise(item_id, data):
        real_restore_raw(item_id, data)
        raise OSError("injected post-restore housekeeping failure")

    def tracking_snapshot_restore(ref, _obsolete_id, _replacement_id):
        snapshot_restore_calls.append(ref)

    monkeypatch.setattr(store, "link_mem", fail_link)
    monkeypatch.setattr(store, "restore_raw", restore_then_raise)
    monkeypatch.setattr(service, "_restore_snapshot_pair", tracking_snapshot_restore)

    result = service.apply(new.id, old.id, apply=True)

    assert result.status == "blocked"
    assert result.reason == "MARKDOWN_UPDATE_FAILED"
    assert snapshot_restore_calls == []
    assert old_path.read_bytes() == old_before
    assert new_path.read_bytes() == new_before


def test_apply_snapshot_fallback_reraises_original_keyboard_interrupt(
    tmp_brain_dir, monkeypatch
):
    store, old, new = _seed_pair(tmp_brain_dir)
    old_path = store.items_dir / f"{old.id}.md"
    new_path = store.items_dir / f"{new.id}.md"
    old_before = old_path.read_bytes()
    new_before = new_path.read_bytes()

    def interrupt_link(_source_id, _target_id):
        raise KeyboardInterrupt

    def fail_raw_restore(_item_id, _data):
        raise OSError("injected raw rollback failure")

    monkeypatch.setattr(store, "link_mem", interrupt_link)
    monkeypatch.setattr(store, "restore_raw", fail_raw_restore)

    with pytest.raises(KeyboardInterrupt):
        SupersessionService(tmp_brain_dir, store).apply(
            new.id, old.id, apply=True
        )

    assert old_path.read_bytes() == old_before
    assert new_path.read_bytes() == new_before


def test_apply_reports_rollback_failed_when_raw_and_snapshot_restore_fail(
    tmp_brain_dir, monkeypatch
):
    store, old, new = _seed_pair(tmp_brain_dir)

    def fail_link(_source_id, _target_id):
        raise OSError("injected markdown failure")

    def fail_raw_restore(_item_id, _data):
        raise OSError("injected raw rollback failure")

    service = SupersessionService(tmp_brain_dir, store)

    def fail_snapshot_restore(_ref, _obsolete_id, _replacement_id):
        raise OSError("injected history rollback failure")

    monkeypatch.setattr(store, "link_mem", fail_link)
    monkeypatch.setattr(store, "restore_raw", fail_raw_restore)
    monkeypatch.setattr(service, "_restore_snapshot_pair", fail_snapshot_restore)

    result = service.apply(new.id, old.id, apply=True)

    assert result.status == "blocked"
    assert result.reason == "ROLLBACK_FAILED"
    assert result.dry_run is False
    ledger = (tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl").read_text(
        encoding="utf-8"
    )
    assert "ROLLBACK_FAILED" in ledger
    assert "injected" not in ledger


def test_blocked_audit_best_effort_does_not_swallow_control_exception(
    tmp_brain_dir, monkeypatch
):
    store, old, new = _seed_pair(tmp_brain_dir)
    service = SupersessionService(tmp_brain_dir, store)

    def interrupt_record(*_args, **_kwargs):
        raise KeyboardInterrupt("control flow")

    monkeypatch.setattr(service, "_record", interrupt_record)

    with pytest.raises(KeyboardInterrupt, match="control flow"):
        service._record_blocked_best_effort(
            "supersede",
            "ROLLBACK_FAILED",
            old.id,
            new.id,
            "a" * 40,
            False,
        )


def test_apply_markdown_control_exception_survives_failed_rollback(
    tmp_brain_dir, monkeypatch
):
    store, old, new = _seed_pair(tmp_brain_dir)
    service = SupersessionService(tmp_brain_dir, store)

    def interrupt_link(_source_id, _target_id):
        raise KeyboardInterrupt("sensitive apply markdown control flow")

    def fail_raw_restore(_item_id, _data):
        raise OSError("sensitive raw rollback failure")

    def fail_snapshot_restore(_ref, _obsolete_id, _replacement_id):
        raise OSError("sensitive snapshot rollback failure")

    monkeypatch.setattr(store, "link_mem", interrupt_link)
    monkeypatch.setattr(store, "restore_raw", fail_raw_restore)
    monkeypatch.setattr(service, "_restore_snapshot_pair", fail_snapshot_restore)

    with pytest.raises(KeyboardInterrupt, match="sensitive apply markdown"):
        service.apply(new.id, old.id, apply=True)

    ledger_path = tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl"
    ledger = ledger_path.read_text(encoding="utf-8")
    record = json.loads(ledger.splitlines()[-1])
    assert record["status"] == "blocked"
    assert record["reason"] == "ROLLBACK_FAILED"
    assert '"status":"applied"' not in ledger
    assert "sensitive" not in ledger


def test_apply_ledger_control_exception_survives_failed_rollback(
    tmp_brain_dir, monkeypatch
):
    store, old, new = _seed_pair(tmp_brain_dir)
    service = SupersessionService(tmp_brain_dir, store)

    def interrupt_applied_record(brain_dir, record):
        if record.status == "applied":
            raise SystemExit("sensitive apply ledger control flow")
        append_lifecycle_record(brain_dir, record)

    def fail_raw_restore(_item_id, _data):
        raise OSError("sensitive raw rollback failure")

    def fail_snapshot_restore(_ref, _obsolete_id, _replacement_id):
        raise OSError("sensitive snapshot rollback failure")

    monkeypatch.setattr(
        "agent_brain.memory.governance.supersession.append_lifecycle_record",
        interrupt_applied_record,
    )
    monkeypatch.setattr(store, "restore_raw", fail_raw_restore)
    monkeypatch.setattr(service, "_restore_snapshot_pair", fail_snapshot_restore)

    with pytest.raises(SystemExit, match="sensitive apply ledger"):
        service.apply(new.id, old.id, apply=True)

    ledger_path = tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl"
    ledger = ledger_path.read_text(encoding="utf-8")
    record = json.loads(ledger.splitlines()[-1])
    assert record["status"] == "blocked"
    assert record["reason"] == "ROLLBACK_FAILED"
    assert '"status":"applied"' not in ledger
    assert "sensitive" not in ledger


def test_selective_snapshot_fallback_preserves_third_item_and_unrelated_runtime(
    tmp_brain_dir, monkeypatch
):
    store, old, new = _seed_pair(tmp_brain_dir)
    third = _item("mem-20260719-120000-rollback-third-item")
    store.write(third, "third body")
    runtime_path = tmp_brain_dir / "runtime" / "unrelated.jsonl"
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.write_text("before\n", encoding="utf-8")
    baseline = BrainHistory(tmp_brain_dir).snapshot("baseline with unrelated files")
    assert baseline is not None
    old_path = store.items_dir / f"{old.id}.md"
    new_path = store.items_dir / f"{new.id}.md"
    old_before = old_path.read_bytes()
    new_before = new_path.read_bytes()

    def fail_after_external_changes(_source_id, _target_id):
        store.update_frontmatter(third.id, summary="externally updated third")
        runtime_path.write_text("after\n", encoding="utf-8")
        raise OSError("injected markdown failure")

    def fail_raw_restore(_item_id, _data):
        raise OSError("injected raw rollback failure")

    monkeypatch.setattr(store, "link_mem", fail_after_external_changes)
    monkeypatch.setattr(store, "restore_raw", fail_raw_restore)

    result = SupersessionService(tmp_brain_dir, store).apply(
        new.id, old.id, apply=True
    )

    assert result.status == "blocked"
    assert result.reason == "MARKDOWN_UPDATE_FAILED"
    assert old_path.read_bytes() == old_before
    assert new_path.read_bytes() == new_before
    assert store.get(third.id)[0].summary == "externally updated third"
    assert runtime_path.read_text(encoding="utf-8") == "after\n"


def test_selective_snapshot_restore_treats_canonical_id_as_literal_pathspec(
    tmp_brain_dir, monkeypatch
):
    store = ItemsStore(tmp_brain_dir / "items")
    old = _item("mem-20260719-100000-rollback-*")
    new = _item("mem-20260719-110000-rollback-new")
    third = _item("mem-20260719-100000-rollback-third")
    store.write(old, "old body")
    store.write(new, "new body")
    store.write(third, "third body")
    old_path = store.items_dir / f"{old.id}.md"
    def fail_after_third_item_update(_source_id, _target_id):
        store.update_frontmatter(third.id, summary="third must remain updated")
        raise OSError("injected markdown failure")

    def fail_raw_restore(item_id, _data):
        raise OSError("injected raw rollback failure")

    monkeypatch.setattr(store, "link_mem", fail_after_third_item_update)
    monkeypatch.setattr(store, "restore_raw", fail_raw_restore)

    result = SupersessionService(tmp_brain_dir, store).apply(
        new.id, old.id, apply=True
    )

    assert result.status == "blocked"
    assert result.reason == "MARKDOWN_UPDATE_FAILED"
    assert store.get(third.id)[0].summary == "third must remain updated"
    assert store.get(old.id)[0].superseded_by is None


def test_append_lifecycle_record_restores_original_bytes_when_fsync_fails(
    tmp_brain_dir, monkeypatch
):
    ledger_path = tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_bytes(b'{"existing":true}\n')
    before = ledger_path.read_bytes()
    real_fsync = os.fsync
    calls = 0

    def fail_once(fd):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_once)
    record = LifecycleLedgerRecord(
        action="supersede",
        timestamp="2026-07-19T12:00:00+00:00",
        status="applied",
        reason="OK",
        obsolete_id="mem-20260719-100000-ledger-old",
        replacement_id="mem-20260719-110000-ledger-new",
        snapshot=None,
        replacement_ref_preexisted=False,
    )

    with pytest.raises(OSError, match="injected fsync failure"):
        append_lifecycle_record(tmp_brain_dir, record)

    assert ledger_path.read_bytes() == before


def test_post_fsync_unlock_failure_keeps_durable_ledger_success(
    tmp_brain_dir, monkeypatch, caplog
):
    store, old, new = _seed_pair(tmp_brain_dir)

    def fail_unlock(_handle):
        raise OSError("sensitive unlock detail")

    monkeypatch.setattr(lifecycle_ledger_module, "_unlock", fail_unlock)

    with caplog.at_level("WARNING"):
        result = SupersessionService(tmp_brain_dir, store).apply(
            new.id, old.id, apply=True
        )

    ledger = (tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl").read_text(
        encoding="utf-8"
    )
    assert result.status == "applied"
    assert store.get(old.id)[0].superseded_by == new.id
    assert old.id in store.get(new.id)[0].refs.mems
    assert '"status":"applied"' in ledger
    assert "sensitive unlock detail" not in caplog.text
    assert "LIFECYCLE_LOCK_HOUSEKEEPING_FAILED" in caplog.text


def test_post_fsync_close_failure_keeps_durable_ledger_success(
    tmp_brain_dir, monkeypatch, caplog
):
    store, old, new = _seed_pair(tmp_brain_dir)
    service = SupersessionService(tmp_brain_dir, store)
    real_record = service._record
    real_close = os.close

    def close_then_fail(descriptor):
        real_close(descriptor)
        raise OSError("sensitive close detail")

    def record_with_close_failure(*args, **kwargs):
        with monkeypatch.context() as scoped:
            scoped.setattr(os, "close", close_then_fail)
            return real_record(*args, **kwargs)

    monkeypatch.setattr(service, "_record", record_with_close_failure)

    with caplog.at_level("WARNING"):
        result = service.apply(new.id, old.id, apply=True)

    ledger = (tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl").read_text(
        encoding="utf-8"
    )
    assert result.status == "applied"
    assert store.get(old.id)[0].superseded_by == new.id
    assert old.id in store.get(new.id)[0].refs.mems
    assert '"status":"applied"' in ledger
    assert "sensitive close detail" not in caplog.text
    assert "LIFECYCLE_LEDGER_HOUSEKEEPING_FAILED" in caplog.text


def test_lifecycle_ledger_rejects_subclass_with_sensitive_extra_fields(
    tmp_brain_dir,
):
    @dataclass(frozen=True)
    class LeakyLedgerRecord(LifecycleLedgerRecord):
        body: str = "private body"
        title: str = "private title"
        secret: str = "private secret"

    record = LeakyLedgerRecord(
        action="supersede",
        timestamp="2026-07-19T12:00:00+00:00",
        status="applied",
        reason="OK",
        obsolete_id="mem-20260719-100000-ledger-old",
        replacement_id="mem-20260719-110000-ledger-new",
        snapshot=None,
        replacement_ref_preexisted=False,
    )

    with pytest.raises(TypeError, match="INVALID_LIFECYCLE_LEDGER_RECORD"):
        append_lifecycle_record(tmp_brain_dir, record)

    ledger_path = tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl"
    assert not ledger_path.exists()


def test_ledger_failure_rolls_back_markdown_and_skips_index(
    tmp_brain_dir, monkeypatch
):
    store, old, new = _seed_pair(tmp_brain_dir)
    old_path = store.items_dir / f"{old.id}.md"
    new_path = store.items_dir / f"{new.id}.md"
    old_before = old_path.read_bytes()
    new_before = new_path.read_bytes()

    class RecordingIndex:
        def __init__(self):
            self.calls = []

        def upsert(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    index = RecordingIndex()

    def fail_ledger(*_args, **_kwargs):
        raise OSError("injected ledger failure")

    monkeypatch.setattr(
        "agent_brain.memory.governance.supersession.append_lifecycle_record",
        fail_ledger,
    )

    result = SupersessionService(tmp_brain_dir, store, index=index).apply(
        new.id, old.id, apply=True
    )

    assert result.status == "blocked"
    assert result.reason == "LEDGER_WRITE_FAILED"
    assert result.dry_run is False
    assert result.snapshot is not None
    assert old_path.read_bytes() == old_before
    assert new_path.read_bytes() == new_before
    assert index.calls == []
    ledger_path = tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl"
    assert not ledger_path.exists() or b'"status":"applied"' not in ledger_path.read_bytes()


def test_ledger_failure_uses_snapshot_fallback_when_raw_restore_fails(
    tmp_brain_dir, monkeypatch
):
    store, old, new = _seed_pair(tmp_brain_dir)
    old_path = store.items_dir / f"{old.id}.md"
    new_path = store.items_dir / f"{new.id}.md"
    old_before = old_path.read_bytes()
    new_before = new_path.read_bytes()

    def fail_ledger(*_args, **_kwargs):
        raise OSError("injected ledger failure")

    def fail_raw_restore(_item_id, _data):
        raise OSError("injected raw rollback failure")

    monkeypatch.setattr(
        "agent_brain.memory.governance.supersession.append_lifecycle_record",
        fail_ledger,
    )
    monkeypatch.setattr(store, "restore_raw", fail_raw_restore)

    result = SupersessionService(tmp_brain_dir, store).apply(
        new.id, old.id, apply=True
    )

    assert result.status == "blocked"
    assert result.reason == "LEDGER_WRITE_FAILED"
    assert old_path.read_bytes() == old_before
    assert new_path.read_bytes() == new_before


def test_ledger_keyboard_interrupt_rolls_back_markdown_and_skips_index(
    tmp_brain_dir, monkeypatch
):
    store, old, new = _seed_pair(tmp_brain_dir)
    old_path = store.items_dir / f"{old.id}.md"
    new_path = store.items_dir / f"{new.id}.md"
    old_before = old_path.read_bytes()
    new_before = new_path.read_bytes()

    class RecordingIndex:
        def __init__(self):
            self.calls = []

        def upsert(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    index = RecordingIndex()

    def interrupt_ledger(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "agent_brain.memory.governance.supersession.append_lifecycle_record",
        interrupt_ledger,
    )

    with pytest.raises(KeyboardInterrupt):
        SupersessionService(tmp_brain_dir, store, index=index).apply(
            new.id, old.id, apply=True
        )

    assert old_path.read_bytes() == old_before
    assert new_path.read_bytes() == new_before
    assert index.calls == []


def test_index_failure_keeps_applied_markdown_and_ledger(tmp_brain_dir):
    store, old, new = _seed_pair(tmp_brain_dir)

    class FailingIndex:
        def upsert(self, *_args, **_kwargs):
            raise RuntimeError("injected index failure")

    result = SupersessionService(tmp_brain_dir, store, index=FailingIndex()).apply(
        new.id, old.id, apply=True
    )

    assert result.status == "applied"
    assert result.index_repair_required is True
    assert store.get(old.id)[0].superseded_by == new.id
    assert old.id in store.get(new.id)[0].refs.mems
    ledger = (tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl").read_text(
        encoding="utf-8"
    )
    assert '"status":"applied"' in ledger


def test_revert_false_is_preview_only_and_requires_exact_current_pointer(
    tmp_brain_dir,
):
    store, old, new = _seed_pair(tmp_brain_dir)
    service = SupersessionService(tmp_brain_dir, store)

    not_applied = service.revert(new.id, old.id)
    assert not_applied.status == "blocked"
    assert not_applied.reason == "SUPERSESSION_NOT_APPLIED"

    assert service.apply(new.id, old.id, apply=True).status == "applied"
    before = {
        path.relative_to(tmp_brain_dir): path.read_bytes()
        for path in tmp_brain_dir.rglob("*")
        if path.is_file()
    }
    preview = service.revert(new.id, old.id)
    after = {
        path.relative_to(tmp_brain_dir): path.read_bytes()
        for path in tmp_brain_dir.rglob("*")
        if path.is_file()
    }
    assert preview.status == "ready"
    assert preview.dry_run is True
    assert after == before


def test_apply_true_early_returns_are_explicit_execution_results(tmp_brain_dir):
    store, old, new = _seed_pair(tmp_brain_dir)
    service = SupersessionService(tmp_brain_dir, store)
    mismatched = _item(
        "mem-20260719-120000-dry-run-project-mismatch",
        project="different-project",
    )
    store.write(mismatched, "mismatch")

    invalid = service.apply("../outside", old.id, apply=True)
    missing = service.apply(
        "mem-20260719-130000-dry-run-missing", old.id, apply=True
    )
    blocked = service.apply(mismatched.id, old.id, apply=True)
    first = service.apply(new.id, old.id, apply=True)
    already = service.apply(new.id, old.id, apply=True)

    for result in (invalid, missing, blocked):
        assert result.status == "blocked"
        assert result.dry_run is False
    assert first.status == "applied"
    assert first.dry_run is False
    assert already.status == "already_applied"
    assert already.dry_run is False


def test_revert_true_early_returns_are_explicit_execution_results(tmp_brain_dir):
    store, old, new = _seed_pair(tmp_brain_dir)
    service = SupersessionService(tmp_brain_dir, store)

    invalid = service.revert("../outside", old.id, apply=True)
    missing = service.revert(
        "mem-20260719-130000-dry-run-missing", old.id, apply=True
    )
    not_applied = service.revert(new.id, old.id, apply=True)
    other = _item("mem-20260719-120000-dry-run-other-replacement")
    store.write(other, "other")
    store.update_frontmatter(old.id, superseded_by=other.id)
    mismatch = service.revert(new.id, old.id, apply=True)

    for result in (invalid, missing, not_applied, mismatch):
        assert result.status == "blocked"
        assert result.dry_run is False
    assert not_applied.reason == "SUPERSESSION_NOT_APPLIED"
    assert mismatch.reason == "SUPERSESSION_MISMATCH"


def test_revert_rolls_back_both_markdown_files_when_unlink_fails(
    tmp_brain_dir, monkeypatch
):
    store, old, new = _seed_pair(tmp_brain_dir)
    service = SupersessionService(tmp_brain_dir, store)
    assert service.apply(new.id, old.id, apply=True).status == "applied"
    old_path = store.items_dir / f"{old.id}.md"
    new_path = store.items_dir / f"{new.id}.md"
    old_before = old_path.read_bytes()
    new_before = new_path.read_bytes()

    def fail_unlink(_source_id, _target_id):
        raise OSError("injected second markdown failure")

    monkeypatch.setattr(store, "unlink_mem", fail_unlink)

    result = service.revert(new.id, old.id, apply=True)

    assert result.status == "blocked"
    assert result.reason == "MARKDOWN_UPDATE_FAILED"
    assert old_path.read_bytes() == old_before
    assert new_path.read_bytes() == new_before
    ledger_lines = (
        tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    assert json.loads(ledger_lines[-1])["reason"] == "MARKDOWN_UPDATE_FAILED"


def test_revert_uses_snapshot_fallback_when_raw_pair_restore_fails(
    tmp_brain_dir, monkeypatch
):
    store, old, new = _seed_pair(tmp_brain_dir)
    service = SupersessionService(tmp_brain_dir, store)
    assert service.apply(new.id, old.id, apply=True).status == "applied"
    old_path = store.items_dir / f"{old.id}.md"
    new_path = store.items_dir / f"{new.id}.md"
    old_before = old_path.read_bytes()
    new_before = new_path.read_bytes()
    ledger_path = tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl"
    ledger_before = ledger_path.read_bytes()

    def fail_unlink(_source_id, _target_id):
        raise OSError("injected unlink failure")

    def fail_raw_restore(_item_id, _data):
        raise OSError("injected raw rollback failure")

    monkeypatch.setattr(store, "unlink_mem", fail_unlink)
    monkeypatch.setattr(store, "restore_raw", fail_raw_restore)

    result = service.revert(new.id, old.id, apply=True)

    assert result.status == "blocked"
    assert result.reason == "MARKDOWN_UPDATE_FAILED"
    assert old_path.read_bytes() == old_before
    assert new_path.read_bytes() == new_before
    assert ledger_path.read_bytes().startswith(ledger_before)


def test_revert_markdown_control_exception_survives_failed_rollback(
    tmp_brain_dir, monkeypatch
):
    store, old, new = _seed_pair(tmp_brain_dir)
    service = SupersessionService(tmp_brain_dir, store)
    assert service.apply(new.id, old.id, apply=True).status == "applied"

    def interrupt_unlink(_source_id, _target_id):
        raise SystemExit("sensitive revert markdown control flow")

    def fail_raw_restore(_item_id, _data):
        raise OSError("sensitive raw rollback failure")

    def fail_snapshot_restore(_ref, _obsolete_id, _replacement_id):
        raise OSError("sensitive snapshot rollback failure")

    monkeypatch.setattr(store, "unlink_mem", interrupt_unlink)
    monkeypatch.setattr(store, "restore_raw", fail_raw_restore)
    monkeypatch.setattr(service, "_restore_snapshot_pair", fail_snapshot_restore)

    with pytest.raises(SystemExit, match="sensitive revert markdown"):
        service.revert(new.id, old.id, apply=True)

    ledger = (tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl").read_text(
        encoding="utf-8"
    )
    records = [json.loads(line) for line in ledger.splitlines()]
    assert records[-1]["status"] == "blocked"
    assert records[-1]["reason"] == "ROLLBACK_FAILED"
    assert not any(record["status"] == "reverted" for record in records)
    assert "sensitive" not in ledger


def test_revert_ledger_control_exception_survives_failed_rollback(
    tmp_brain_dir, monkeypatch
):
    store, old, new = _seed_pair(tmp_brain_dir)
    service = SupersessionService(tmp_brain_dir, store)
    assert service.apply(new.id, old.id, apply=True).status == "applied"

    def interrupt_reverted_record(brain_dir, record):
        if record.status == "reverted":
            raise KeyboardInterrupt("sensitive revert ledger control flow")
        append_lifecycle_record(brain_dir, record)

    def fail_raw_restore(_item_id, _data):
        raise OSError("sensitive raw rollback failure")

    def fail_snapshot_restore(_ref, _obsolete_id, _replacement_id):
        raise OSError("sensitive snapshot rollback failure")

    monkeypatch.setattr(
        "agent_brain.memory.governance.supersession.append_lifecycle_record",
        interrupt_reverted_record,
    )
    monkeypatch.setattr(store, "restore_raw", fail_raw_restore)
    monkeypatch.setattr(service, "_restore_snapshot_pair", fail_snapshot_restore)

    with pytest.raises(KeyboardInterrupt, match="sensitive revert ledger"):
        service.revert(new.id, old.id, apply=True)

    ledger = (tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl").read_text(
        encoding="utf-8"
    )
    records = [json.loads(line) for line in ledger.splitlines()]
    assert records[-1]["status"] == "blocked"
    assert records[-1]["reason"] == "ROLLBACK_FAILED"
    assert not any(record["status"] == "reverted" for record in records)
    assert "sensitive" not in ledger


def test_restore_raw_rejects_noncanonical_id_without_touching_outside_file(
    tmp_brain_dir,
):
    store = ItemsStore(tmp_brain_dir / "items")
    outside = tmp_brain_dir / "outside.md"
    outside.write_bytes(b"sentinel")

    with pytest.raises(ValueError, match="invalid memory item id"):
        store.restore_raw("../outside", b"attacker")

    assert outside.read_bytes() == b"sentinel"
