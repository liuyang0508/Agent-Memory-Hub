from __future__ import annotations

import json
from datetime import datetime, timezone

from agent_brain.contracts.memory_enums import MemoryType
from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.governance.contradiction_cases import (
    build_contradiction_cases,
)
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
