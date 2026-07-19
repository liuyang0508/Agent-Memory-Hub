from datetime import datetime, timezone

from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Sensitivity
from agent_brain.memory.governance.supersession import SupersessionService
from agent_brain.memory.store.items_store import ItemsStore


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


def test_preview_rejects_replacement_that_needs_review(tmp_brain_dir):
    store = ItemsStore(tmp_brain_dir / "items")
    obsolete = _item("mem-20260719-100000-reviewed")
    replacement = _item(
        "mem-20260719-110000-needs-review",
        tags=["lifecycle", "needs-review"],
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
