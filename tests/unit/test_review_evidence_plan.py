from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_brain.contracts.memory_enums import MemoryType
from agent_brain.contracts.memory_item import MemoryItem, Refs, Validity
from agent_brain.contracts.resource import (
    ExtractionKind,
    ExtractionRecord,
    ResourceKind,
    ResourceRecord,
    make_extraction_id,
    make_resource_id,
    sha256_text,
)
from agent_brain.interfaces.cli import app
from agent_brain.memory.evidence.resource_store import ResourceStore
from agent_brain.memory.governance.contradiction_cases import ContradictionCase
from agent_brain.memory.governance.review_evidence import (
    REVIEW_EVIDENCE_SCHEMA_VERSION,
    build_review_evidence_plan,
)
from agent_brain.memory.store.items_store import ItemsStore


runner = CliRunner()


def _review_item(
    suffix: str,
    *,
    refs: Refs | None = None,
    tags: list[str] | None = None,
    validity: Validity | None = None,
    sensitivity: str = "internal",
) -> MemoryItem:
    return MemoryItem(
        id=f"mem-20260730-180000-{suffix}",
        type=MemoryType.decision,
        created_at=datetime.now(timezone.utc),
        project="evidence-project",
        title=f"Review evidence {suffix}",
        summary=f"Review evidence summary {suffix}",
        refs=refs or Refs(),
        tags=tags or ["needs-review"],
        confidence=0.35,
        validity=validity or Validity(),
        sensitivity=sensitivity,
    )


def test_evidence_plan_binds_item_and_deduplicates_one_provenance_root(
    tmp_brain: Path,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source_path = repo / "decision.md"
    source_text = "authoritative decision source"
    source_path.write_text(source_text, encoding="utf-8")
    source_sha256 = hashlib.sha256(source_text.encode("utf-8")).hexdigest()

    evidence = ResourceStore(tmp_brain)
    resource = ResourceRecord(
        id=make_resource_id("decision source"),
        kind=ResourceKind.file,
        uri=str(source_path),
        title="decision source",
        sha256=source_sha256,
        size_bytes=len(source_text.encode("utf-8")),
        project="evidence-project",
    )
    extraction = ExtractionRecord(
        id=make_extraction_id("decision source exact"),
        resource_id=resource.id,
        kind=ExtractionKind.text,
        extractor="test",
        content_text=source_text,
        content_sha256=sha256_text(source_text),
    )
    evidence.write_resource(resource)
    evidence.write_extraction(extraction)

    item = _review_item(
        "backed",
        refs=Refs(
            files=["decision.md"],
            resources=[resource.id],
            extractions=[extraction.id],
        ),
        validity=Validity(repo=str(repo)),
    )
    store = ItemsStore(tmp_brain / "items")
    store.write(item, "PRIVATE_ITEM_BODY_CANARY")

    plan = build_review_evidence_plan(
        tmp_brain,
        store=store,
        contradiction_cases=(),
    )
    row = plan.items[0]

    assert plan.schema_version == REVIEW_EVIDENCE_SCHEMA_VERSION
    assert plan.mutates_memory is False
    assert plan.mutates_confidence is False
    assert plan.item_scan_unavailable_count == 0
    assert plan.evidence_available_count == 1
    assert plan.source_gap_count == 0
    assert plan.provenance_recoverable_count == 0
    assert plan.unresolved_source_gap_count == 0
    assert row.evidence_status == "evidence_available"
    assert row.verified_source_count == 3
    assert row.supporting_verified_source_count == 3
    assert row.independent_verified_source_count == 1
    assert row.item_sha256 == hashlib.sha256(
        store.read_bytes_nofollow(item.id)
    ).hexdigest()
    assert [source.availability for source in row.sources] == [
        "verified",
        "verified",
        "verified",
    ]


def test_evidence_plan_distinguishes_explicit_gap_from_available_provenance(
    tmp_brain: Path,
) -> None:
    evidence = ResourceStore(tmp_brain)
    resource = ResourceRecord(
        id=make_resource_id("write input"),
        kind=ResourceKind.document,
        uri="memory://write-input",
        title="write input",
        project="evidence-project",
    )
    extraction = ExtractionRecord(
        id=make_extraction_id("write input exact"),
        resource_id=resource.id,
        kind=ExtractionKind.text,
        extractor="write-service",
        content_text="captured write input",
        content_sha256=sha256_text("captured write input"),
    )
    evidence.write_resource(resource)
    evidence.write_extraction(extraction)
    item = _review_item(
        "provenance-only",
        refs=Refs(
            resources=[resource.id],
            extractions=[extraction.id],
        ),
    )
    store = ItemsStore(tmp_brain / "items")
    store.write(item, "candidate body")

    plan = build_review_evidence_plan(
        tmp_brain,
        store=store,
        contradiction_cases=(),
    )
    row = plan.items[0]

    assert plan.source_gap_count == 1
    assert plan.provenance_recoverable_count == 1
    assert plan.unresolved_source_gap_count == 0
    assert plan.evidence_available_count == 0
    assert row.review_reason == "source_gap"
    assert row.evidence_status == "provenance_available"
    assert row.recommended_action == (
        "inspect_provenance_then_attach_source_or_reject"
    )


def test_write_input_sidecar_is_traceability_not_independent_support(
    tmp_brain: Path,
) -> None:
    item = _review_item("write-input-only")
    evidence = ResourceStore(tmp_brain)
    resource = ResourceRecord(
        id=make_resource_id("write input self evidence"),
        kind=ResourceKind.document,
        uri=f"memory://items/{item.id}/write-input",
        title="write input self evidence",
        project="evidence-project",
        metadata={
            "evidence_role": "write_input",
            "memory_item_id": item.id,
        },
    )
    extraction = ExtractionRecord(
        id=make_extraction_id("write input self evidence"),
        resource_id=resource.id,
        kind=ExtractionKind.text,
        extractor="write-service",
        content_text="the same assertion submitted for memory write",
        content_sha256=sha256_text(
            "the same assertion submitted for memory write"
        ),
    )
    evidence.write_resource(resource)
    evidence.write_extraction(extraction)
    item = item.model_copy(
        update={
            "refs": Refs(
                resources=[resource.id],
                extractions=[extraction.id],
            )
        }
    )
    store = ItemsStore(tmp_brain / "items")
    store.write(item, "candidate body")

    plan = build_review_evidence_plan(
        tmp_brain,
        store=store,
        contradiction_cases=(),
    )
    row = plan.items[0]

    assert plan.source_gap_count == 1
    assert plan.provenance_recoverable_count == 0
    assert plan.unresolved_source_gap_count == 1
    assert plan.traceability_only_count == 1
    assert row.evidence_status == "traceability_only"
    assert row.verified_source_count == 2
    assert row.supporting_verified_source_count == 0
    assert row.independent_verified_source_count == 0
    assert row.recommended_action == "attach_independent_source_or_reject"
    assert {source.evidence_role for source in row.sources} == {"write_input"}
    assert all(source.supports_truth is False for source in row.sources)


def test_public_evidence_plan_omits_locators_and_content(
    tmp_brain: Path,
) -> None:
    item = _review_item("private", sensitivity="private")
    store = ItemsStore(tmp_brain / "items")
    store.write(item, "PRIVATE_EVIDENCE_BODY_CANARY")

    payload = build_review_evidence_plan(
        tmp_brain,
        store=store,
        contradiction_cases=(),
    ).to_dict(include_locators=False)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["source_gap_count"] == 1
    assert payload["unresolved_source_gap_count"] == 1
    assert "locator" not in serialized
    assert "PRIVATE_EVIDENCE_BODY_CANARY" not in serialized
    assert "Review evidence summary private" not in serialized


def test_evidence_plan_fails_closed_on_symlinked_sidecar_directory(
    tmp_brain: Path,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside-resources"
    outside.mkdir()
    resource = ResourceRecord(
        id=make_resource_id("outside resource"),
        kind=ResourceKind.document,
        uri="memory://outside",
        title="OUTSIDE_RESOURCE_TITLE_CANARY",
        project="evidence-project",
    )
    (outside / f"{resource.id}.json").write_text(
        json.dumps(resource.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        (tmp_brain / "resources").symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("directory symlinks unavailable")
    item = _review_item(
        "symlinked-resource",
        refs=Refs(resources=[resource.id]),
    )
    store = ItemsStore(tmp_brain / "items")
    store.write(item, "candidate")

    plan = build_review_evidence_plan(
        tmp_brain,
        store=store,
        contradiction_cases=(),
    )
    source = plan.items[0].sources[0]

    assert plan.status == "warn"
    assert source.availability == "invalid"
    assert source.reason == "RESOURCE_INVALID_OR_UNREADABLE"
    assert source.content_digest is None


def test_contested_items_route_to_case_or_unpaired_without_mutation(
    tmp_brain: Path,
) -> None:
    store = ItemsStore(tmp_brain / "items")
    paired = _review_item("paired", tags=["contested", "needs-review"])
    counterpart = _review_item("counterpart")
    unpaired = _review_item("unpaired", tags=["contested", "needs-review"])
    for item in (paired, counterpart, unpaired):
        store.write(item, f"body for {item.id}")
    case = ContradictionCase(
        case_id="contradiction-0123456789abcdef",
        item_ids=(paired.id, counterpart.id),
        pair_count=1,
        confidence=0.8,
        evidence=("test contradiction",),
    )
    before = {
        item.id: store.read_bytes_nofollow(item.id)
        for item in (paired, counterpart, unpaired)
    }

    plan = build_review_evidence_plan(
        tmp_brain,
        store=store,
        contradiction_cases=(case,),
    )
    by_id = {row.item_id: row for row in plan.items}

    assert plan.contested_count == 2
    assert plan.contested_case_count == 1
    assert plan.contested_unpaired_count == 1
    assert by_id[paired.id].contestation_route == "contradiction_case"
    assert by_id[paired.id].contradiction_case_ids == (case.case_id,)
    assert by_id[unpaired.id].contestation_route == "contested_unpaired"
    assert {
        item.id: store.read_bytes_nofollow(item.id)
        for item in (paired, counterpart, unpaired)
    } == before


def test_review_evidence_plan_cli_includes_locator_only_in_operator_output(
    tmp_brain: Path,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "cli-repo"
    repo.mkdir()
    (repo / "source.md").write_text("cli evidence", encoding="utf-8")
    item = _review_item(
        "cli",
        refs=Refs(files=["source.md"]),
        validity=Validity(repo=str(repo)),
    )
    ItemsStore(tmp_brain / "items").write(item, "candidate")

    result = runner.invoke(
        app,
        ["review", "evidence-plan", item.id, "--format", "json"],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert payload["schema_version"] == REVIEW_EVIDENCE_SCHEMA_VERSION
    assert payload["mutates_memory"] is False
    assert payload["items"][0]["sources"][0]["locator"] == "source.md"
    assert payload["items"][0]["sources"][0]["availability"] == "verified"
