from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agent_brain.contracts.memory_enums import MemoryType
from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.governance.contradiction_cases import (
    build_contradiction_cases,
)
from agent_brain.memory.governance.contradiction_containment import (
    contain_contradiction_case,
    read_containment_receipt_health,
    recover_containment_transaction,
)
from agent_brain.memory.governance.contradiction_resolution import (
    build_contradiction_case_inventory,
    read_contradiction_receipt_health,
    recover_contradiction_case_transaction,
    resolve_contradiction_case,
)
from agent_brain.memory.governance.auto_governance import AutoGovernanceCycle
from agent_brain.memory.governance.drift_types import DriftFinding, DriftType
from agent_brain.memory.governance.review_transactions import (
    read_review_receipt_health,
    resolve_review_candidate,
)
from agent_brain.memory.store.items_store import ItemsStore


def _item(suffix: str) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260729-150000-{suffix}",
        type=MemoryType.decision,
        created_at=datetime.now(timezone.utc),
        title=f"Review transaction {suffix}",
        summary=f"Review transaction candidate {suffix}",
        tags=["needs-review", suffix],
        confidence=0.35,
    )


def _finding(left: str, right: str, *, confidence: float = 0.5) -> DriftFinding:
    return DriftFinding(
        drift_type=DriftType.CONTRADICTION,
        item_ids=[left, right],
        confidence=confidence,
        description="contradiction",
        evidence=f"{left} versus {right}",
    )


def _decision(suffix: str, *, title: str) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260729-160000-{suffix}",
        type=MemoryType.decision,
        created_at=datetime.now(timezone.utc),
        project="contradiction-case-test",
        title=title,
        summary=f"Decision candidate {suffix}",
        tags=["frontend-choice"],
        confidence=0.7,
    )


def _contradiction_store(
    tmp_path: Path,
) -> tuple[Path, ItemsStore, MemoryItem, MemoryItem]:
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    first = _decision("react", title="Frontend Framework Choice")
    second = _decision("vue", title="Frontend Framework Choice Updated")
    store.write(first, "We decided to use React for the frontend framework.")
    store.write(second, "After evaluation, we chose Vue instead of React.")
    return brain, store, first, second


def test_contradiction_cases_collapse_connected_pairs_stably() -> None:
    findings = [
        _finding("a", "b"),
        _finding("b", "c", confidence=0.8),
        _finding("d", "e"),
    ]

    first = build_contradiction_cases(findings)
    second = build_contradiction_cases(reversed(findings))

    assert first == second
    assert len(first) == 2
    connected = next(case for case in first if case.item_ids == ("a", "b", "c"))
    assert connected.pair_count == 2
    assert connected.confidence == 0.8
    assert connected.case_id.startswith("contradiction-")


def test_review_resolution_is_preview_first_digest_bound_and_receipted(
    tmp_path,
) -> None:
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    item = _item("approve")
    store.write(item, "candidate body")

    preview = resolve_review_candidate(
        brain_dir=brain,
        store=store,
        item_id=item.id,
        action="approve",
        confidence=0.82,
    )
    unchanged, _body = store.get(item.id)

    assert preview.status == "ready"
    assert preview.dry_run is True
    assert unchanged.confidence == 0.35

    applied = resolve_review_candidate(
        brain_dir=brain,
        store=store,
        item_id=item.id,
        action="approve",
        confidence=0.82,
        apply=True,
        expected_sha256=preview.expected_sha256,
    )

    assert applied.status == "applied"
    updated, _body = store.get(item.id)
    assert updated.confidence == 0.82
    assert "review-approved" in updated.tags
    records = [
        json.loads(line)
        for line in (
            brain / "runtime" / "review-resolution-receipts.jsonl"
        ).read_text().splitlines()
    ]
    assert [record["state"] for record in records] == ["prepared", "completed"]
    assert records[0]["transaction_id"] == records[1]["transaction_id"]
    assert records[1]["after_sha256"]
    assert read_review_receipt_health(brain).status == "healthy"


def test_review_resolution_blocks_changed_preview_without_receipt(tmp_path) -> None:
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    item = _item("changed")
    store.write(item, "candidate body")
    preview = resolve_review_candidate(
        brain_dir=brain,
        store=store,
        item_id=item.id,
        action="reject",
        confidence=0.1,
    )
    store.update_frontmatter(item.id, summary="Changed after preview")

    result = resolve_review_candidate(
        brain_dir=brain,
        store=store,
        item_id=item.id,
        action="reject",
        confidence=0.1,
        apply=True,
        expected_sha256=preview.expected_sha256,
    )

    assert result.status == "blocked"
    assert result.reason == "REVIEW_RESOLUTION_CHANGED"
    assert not (brain / "runtime" / "review-resolution-receipts.jsonl").exists()


def test_contradiction_case_coexist_is_digest_bound_and_reopens_on_change(
    tmp_path,
) -> None:
    brain, store, first, second = _contradiction_store(tmp_path)
    inventory = build_contradiction_case_inventory(brain_dir=brain, store=store)
    case = inventory.cases[0]

    preview = resolve_contradiction_case(
        brain_dir=brain,
        store=store,
        case_id=case.case_id,
        action="coexist",
    )
    applied = resolve_contradiction_case(
        brain_dir=brain,
        store=store,
        case_id=case.case_id,
        action="coexist",
        apply=True,
        expected_intent_sha256=preview.expected_intent_sha256,
    )

    assert preview.status == "ready"
    assert applied.status == "applied"
    assert read_contradiction_receipt_health(brain).completed_count == 1
    resolved = build_contradiction_case_inventory(brain_dir=brain, store=store)
    assert resolved.cases[0].status == "resolved"
    plan = AutoGovernanceCycle(
        brain_dir=brain,
        items_store=store,
        include_index=False,
        include_conversations=False,
        include_evolve=False,
    ).run()
    assert not any(
        action.action == "review_contradiction_case"
        for action in plan.actions
    )
    for item_id in (first.id, second.id):
        item, _body = store.get(item_id)
        assert "contradiction-coexists" in item.tags

    store.update_frontmatter(first.id, summary="Changed after the coexist decision")

    reopened = build_contradiction_case_inventory(brain_dir=brain, store=store)
    assert reopened.cases[0].status == "open"


def test_dismiss_restores_exact_containment_baseline_and_recall(
    tmp_path,
) -> None:
    from agent_brain.memory.context.context_firewall import (
        ContextCandidate,
        ContextFirewall,
    )
    from agent_brain.memory.context.context_firewall_types import (
        ContextFirewallConfig,
    )

    brain, store, first, second = _contradiction_store(tmp_path)
    original = {
        item_id: store.get(item_id)[0]
        for item_id in (first.id, second.id)
    }
    case = build_contradiction_case_inventory(
        brain_dir=brain,
        store=store,
    ).cases[0]
    detected = build_contradiction_cases([
        _finding(first.id, second.id),
    ])[0]

    contained = contain_contradiction_case(
        brain_dir=brain,
        store=store,
        case=detected,
        apply=True,
    )

    assert contained.status == "applied"
    assert read_containment_receipt_health(brain).status == "healthy"
    quarantined, body = store.get(first.id)
    assert {"contested", "needs-review"}.issubset(quarantined.tags)
    assert quarantined.confidence == original[first.id].confidence - 0.15
    firewall = ContextFirewall(
        ContextFirewallConfig(require_source_for_fact_decision=False)
    )
    blocked = firewall.filter([
        ContextCandidate(quarantined, body, score=1.0),
    ])
    assert blocked.included == []
    assert "requires_review" in blocked.excluded[0].reasons

    preview = resolve_contradiction_case(
        brain_dir=brain,
        store=store,
        case_id=case.case_id,
        action="dismiss",
    )
    applied = resolve_contradiction_case(
        brain_dir=brain,
        store=store,
        case_id=case.case_id,
        action="dismiss",
        apply=True,
        expected_intent_sha256=preview.expected_intent_sha256,
    )

    assert preview.status == "ready"
    assert preview.containment_restored_count == 2
    assert applied.status == "applied"
    assert applied.containment_restored_count == 2
    for item_id in (first.id, second.id):
        restored, restored_body = store.get(item_id)
        assert restored.confidence == original[item_id].confidence
        assert "contested" not in restored.tags
        assert "needs-review" not in restored.tags
        assert "contradiction-dismissed" in restored.tags
        recalled = firewall.filter([
            ContextCandidate(restored, restored_body, score=1.0),
        ])
        assert recalled.included
    resolved = build_contradiction_case_inventory(
        brain_dir=brain,
        store=store,
    )
    assert resolved.cases[0].resolution_action == "dismiss"


def test_case_resolution_fails_closed_for_legacy_containment_without_receipt(
    tmp_path,
) -> None:
    brain, store, first, second = _contradiction_store(tmp_path)
    for item_id in (first.id, second.id):
        item, _body = store.get(item_id)
        store.update_frontmatter(
            item_id,
            tags=sorted({*item.tags, "contested", "needs-review"}),
            confidence=item.confidence - 0.15,
        )
    case = build_contradiction_case_inventory(
        brain_dir=brain,
        store=store,
    ).cases[0]

    result = resolve_contradiction_case(
        brain_dir=brain,
        store=store,
        case_id=case.case_id,
        action="dismiss",
    )

    assert result.status == "blocked"
    assert result.reason == "CONTAINMENT_PROVENANCE_MISSING"


def test_incomplete_containment_transaction_is_recoverable(
    tmp_path,
    monkeypatch,
) -> None:
    from agent_brain.memory.governance import contradiction_containment

    brain, store, first, second = _contradiction_store(tmp_path)
    before = {
        item_id: (store.items_dir / f"{item_id}.md").read_bytes()
        for item_id in (first.id, second.id)
    }
    case = build_contradiction_cases([
        _finding(first.id, second.id),
    ])[0]
    append = contradiction_containment._append_receipt
    append_terminal = contradiction_containment._append_receipt_from_prepared

    def interrupt_completed(*args, **kwargs):
        if kwargs["state"] == "completed":
            raise OSError("simulated containment interruption")
        return append(*args, **kwargs)

    def interrupt_rollback(*args, **kwargs):
        raise OSError("simulated containment rollback interruption")

    monkeypatch.setattr(
        contradiction_containment,
        "_append_receipt",
        interrupt_completed,
    )
    monkeypatch.setattr(
        contradiction_containment,
        "_append_receipt_from_prepared",
        interrupt_rollback,
    )
    interrupted = contain_contradiction_case(
        brain_dir=brain,
        store=store,
        case=case,
        apply=True,
    )
    monkeypatch.setattr(
        contradiction_containment,
        "_append_receipt",
        append,
    )
    monkeypatch.setattr(
        contradiction_containment,
        "_append_receipt_from_prepared",
        append_terminal,
    )

    assert interrupted.reason == "CONTAINMENT_ROLLBACK_FAILED"
    assert interrupted.transaction_id
    assert read_containment_receipt_health(brain).status == "incomplete"
    recovery = recover_containment_transaction(
        brain_dir=brain,
        store=store,
        transaction_id=interrupted.transaction_id,
        apply=True,
    )

    assert recovery.status == "recovered"
    assert read_containment_receipt_health(brain).status == "healthy"
    for item_id, raw in before.items():
        assert (store.items_dir / f"{item_id}.md").read_bytes() == raw


def test_corrupt_containment_ledger_blocks_new_containment(tmp_path) -> None:
    brain, store, first, second = _contradiction_store(tmp_path)
    runtime = brain / "runtime"
    runtime.mkdir(exist_ok=True)
    (runtime / "contradiction-containment-receipts.jsonl").write_text(
        "{not-json}\n",
        encoding="utf-8",
    )
    case = build_contradiction_cases([
        _finding(first.id, second.id),
    ])[0]

    result = contain_contradiction_case(
        brain_dir=brain,
        store=store,
        case=case,
        apply=True,
    )

    assert result.status == "blocked"
    assert result.reason == "CONTAINMENT_LEDGER_UNAVAILABLE"
    assert read_containment_receipt_health(brain).status == "corrupt"


def test_contradiction_case_select_authority_supersedes_case_atomically(
    tmp_path,
) -> None:
    brain, store, first, second = _contradiction_store(tmp_path)
    case = build_contradiction_case_inventory(
        brain_dir=brain,
        store=store,
    ).cases[0]
    preview = resolve_contradiction_case(
        brain_dir=brain,
        store=store,
        case_id=case.case_id,
        action="select_authority",
        target_item_id=second.id,
    )
    result = resolve_contradiction_case(
        brain_dir=brain,
        store=store,
        case_id=case.case_id,
        action="select_authority",
        target_item_id=second.id,
        apply=True,
        expected_intent_sha256=preview.expected_intent_sha256,
    )

    assert result.status == "applied"
    obsolete, _body = store.get(first.id)
    authority, _body = store.get(second.id)
    assert obsolete.superseded_by == second.id
    assert "contradiction-resolved" in obsolete.tags
    assert "contradiction-authority" in authority.tags
    assert first.id in authority.refs.mems
    assert result.snapshot


def test_contradiction_case_merge_uses_reviewed_item_outside_case(
    tmp_path,
) -> None:
    brain, store, first, second = _contradiction_store(tmp_path)
    merged = _decision("merged", title="Architecture ADR 42")
    store.write(merged, "Canonical synthesis with reviewed trade-off evidence.")
    case = build_contradiction_case_inventory(
        brain_dir=brain,
        store=store,
    ).cases[0]

    preview = resolve_contradiction_case(
        brain_dir=brain,
        store=store,
        case_id=case.case_id,
        action="merge",
        target_item_id=merged.id,
    )
    result = resolve_contradiction_case(
        brain_dir=brain,
        store=store,
        case_id=case.case_id,
        action="merge",
        target_item_id=merged.id,
        apply=True,
        expected_intent_sha256=preview.expected_intent_sha256,
    )

    assert preview.status == "ready"
    assert result.status == "applied"
    canonical, _body = store.get(merged.id)
    assert "contradiction-merged" in canonical.tags
    assert set(canonical.refs.mems) >= {first.id, second.id}
    for item_id in (first.id, second.id):
        obsolete, _body = store.get(item_id)
        assert obsolete.superseded_by == merged.id


def test_contradiction_case_defer_is_digest_bound_without_item_mutation(
    tmp_path,
) -> None:
    brain, store, first, second = _contradiction_store(tmp_path)
    before = {
        item_id: (store.items_dir / f"{item_id}.md").read_bytes()
        for item_id in (first.id, second.id)
    }
    case = build_contradiction_case_inventory(
        brain_dir=brain,
        store=store,
    ).cases[0]
    preview = resolve_contradiction_case(
        brain_dir=brain,
        store=store,
        case_id=case.case_id,
        action="defer",
        defer_days=7,
    )
    result = resolve_contradiction_case(
        brain_dir=brain,
        store=store,
        case_id=case.case_id,
        action="defer",
        defer_days=7,
        apply=True,
        expected_intent_sha256=preview.expected_intent_sha256,
    )

    assert result.status == "deferred"
    assert result.deferred_until
    inventory = build_contradiction_case_inventory(
        brain_dir=brain,
        store=store,
    )
    assert inventory.cases[0].status == "deferred"
    for item_id, raw in before.items():
        assert (store.items_dir / f"{item_id}.md").read_bytes() == raw


def test_contradiction_case_changed_preview_writes_no_receipt(tmp_path) -> None:
    brain, store, first, _second = _contradiction_store(tmp_path)
    case = build_contradiction_case_inventory(
        brain_dir=brain,
        store=store,
    ).cases[0]
    preview = resolve_contradiction_case(
        brain_dir=brain,
        store=store,
        case_id=case.case_id,
        action="coexist",
    )
    store.update_frontmatter(first.id, confidence=0.61)

    result = resolve_contradiction_case(
        brain_dir=brain,
        store=store,
        case_id=case.case_id,
        action="coexist",
        apply=True,
        expected_intent_sha256=preview.expected_intent_sha256,
    )

    assert result.status == "blocked"
    assert result.reason == "CASE_RESOLUTION_CHANGED"
    assert not (
        brain / "runtime" / "contradiction-case-receipts.jsonl"
    ).exists()


def test_contradiction_case_blocks_when_receipt_ledger_is_corrupt(
    tmp_path,
) -> None:
    brain, store, _first, _second = _contradiction_store(tmp_path)
    case = build_contradiction_case_inventory(
        brain_dir=brain,
        store=store,
    ).cases[0]
    runtime = brain / "runtime"
    runtime.mkdir(exist_ok=True)
    (runtime / "contradiction-case-receipts.jsonl").write_text(
        "{invalid-json}\n",
        encoding="utf-8",
    )

    result = resolve_contradiction_case(
        brain_dir=brain,
        store=store,
        case_id=case.case_id,
        action="coexist",
    )

    assert result.status == "blocked"
    assert result.reason == "CASE_LEDGER_UNAVAILABLE"
    assert read_contradiction_receipt_health(brain).status == "corrupt"


def test_incomplete_contradiction_case_transaction_is_recoverable(
    tmp_path,
    monkeypatch,
) -> None:
    from agent_brain.memory.governance import contradiction_resolution

    brain, store, first, second = _contradiction_store(tmp_path)
    before = {
        item_id: (store.items_dir / f"{item_id}.md").read_bytes()
        for item_id in (first.id, second.id)
    }
    case = build_contradiction_case_inventory(
        brain_dir=brain,
        store=store,
    ).cases[0]
    preview = resolve_contradiction_case(
        brain_dir=brain,
        store=store,
        case_id=case.case_id,
        action="coexist",
    )
    append = contradiction_resolution._append_case_receipt

    def interrupt_terminal(*args, **kwargs):
        if kwargs["state"] != "prepared":
            raise OSError("simulated process interruption")
        return append(*args, **kwargs)

    monkeypatch.setattr(
        contradiction_resolution,
        "_append_case_receipt",
        interrupt_terminal,
    )
    interrupted = resolve_contradiction_case(
        brain_dir=brain,
        store=store,
        case_id=case.case_id,
        action="coexist",
        apply=True,
        expected_intent_sha256=preview.expected_intent_sha256,
    )
    monkeypatch.setattr(
        contradiction_resolution,
        "_append_case_receipt",
        append,
    )

    assert interrupted.reason == "CASE_ROLLBACK_FAILED"
    assert interrupted.transaction_id
    assert read_contradiction_receipt_health(brain).status == "incomplete"
    blocked = resolve_contradiction_case(
        brain_dir=brain,
        store=store,
        case_id=case.case_id,
        action="coexist",
    )
    assert blocked.reason == "CASE_LEDGER_UNAVAILABLE"
    recovery = recover_contradiction_case_transaction(
        brain_dir=brain,
        store=store,
        transaction_id=interrupted.transaction_id,
        apply=True,
    )

    assert recovery.status == "recovered"
    assert read_contradiction_receipt_health(brain).status == "healthy"
    for item_id, raw in before.items():
        assert (store.items_dir / f"{item_id}.md").read_bytes() == raw
