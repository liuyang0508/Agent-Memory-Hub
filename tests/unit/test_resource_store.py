from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_brain.contracts.memory_item import MemoryItem


def test_resource_record_validates_id_and_defaults() -> None:
    from agent_brain.contracts.resource import ResourceKind, ResourceRecord, make_resource_id

    rid = make_resource_id(
        "Demo PDF",
        when=datetime(2026, 6, 11, 1, 2, 3, tzinfo=timezone.utc),
    )
    record = ResourceRecord(
        id=rid,
        kind=ResourceKind.pdf,
        uri="/tmp/demo.pdf",
        title="Demo PDF",
        sha256="0" * 64,
    )

    assert record.id.startswith("res-20260611-010203-demo-pdf-")
    assert record.tags == []
    assert record.metadata == {}
    assert record.sensitivity == "internal"


def test_extraction_record_hashes_content() -> None:
    from agent_brain.contracts.resource import (
        ExtractionKind,
        ExtractionRecord,
        make_extraction_id,
        make_resource_id,
        sha256_text,
    )

    rid = make_resource_id("Demo")
    eid = make_extraction_id("Demo OCR")
    record = ExtractionRecord(
        id=eid,
        resource_id=rid,
        kind=ExtractionKind.ocr,
        extractor="manual-test",
        content_text="hello world",
        content_sha256=sha256_text("hello world"),
    )

    assert record.content_sha256 == sha256_text("hello world")
    assert record.confidence == 0.7


def test_extraction_record_rejects_hash_mismatch() -> None:
    from agent_brain.contracts.resource import (
        ExtractionKind,
        ExtractionRecord,
        make_extraction_id,
        make_resource_id,
    )

    with pytest.raises(ValueError):
        ExtractionRecord(
            id=make_extraction_id("Demo OCR"),
            resource_id=make_resource_id("Demo"),
            kind=ExtractionKind.ocr,
            extractor="manual-test",
            content_text="hello world",
            content_sha256="0" * 64,
        )


def test_resource_record_rejects_bad_hash() -> None:
    from agent_brain.contracts.resource import ResourceKind, ResourceRecord, make_resource_id

    with pytest.raises(ValueError):
        ResourceRecord(
            id=make_resource_id("bad"),
            kind=ResourceKind.file,
            uri="/tmp/bad",
            title="bad",
            sha256="not-a-sha",
        )


def test_resource_store_round_trips_resource_and_extraction(tmp_path) -> None:
    from agent_brain.memory.evidence.resource_store import ResourceStore
    from agent_brain.contracts.resource import (
        ExtractionKind,
        ExtractionRecord,
        ResourceKind,
        ResourceRecord,
        make_extraction_id,
        make_resource_id,
        sha256_text,
    )

    store = ResourceStore(tmp_path)
    resource = ResourceRecord(
        id=make_resource_id("Demo File"),
        kind=ResourceKind.file,
        uri="/tmp/demo.txt",
        title="Demo File",
        sha256="1" * 64,
    )
    extraction = ExtractionRecord(
        id=make_extraction_id("Demo Text"),
        resource_id=resource.id,
        kind=ExtractionKind.text,
        extractor="manual",
        content_text="derived text",
        content_sha256=sha256_text("derived text"),
    )

    store.write_resource(resource)
    store.write_extraction(extraction)

    assert store.get_resource(resource.id) == resource
    assert store.get_extraction(extraction.id) == extraction
    assert [r.id for r in store.iter_resources()] == [resource.id]
    assert [e.id for e in store.iter_extractions(resource_id=resource.id)] == [extraction.id]


def test_resource_store_rejects_duplicate_ids(tmp_path) -> None:
    from agent_brain.memory.evidence.resource_store import ResourceStore
    from agent_brain.contracts.resource import ResourceKind, ResourceRecord, make_resource_id

    store = ResourceStore(tmp_path)
    resource = ResourceRecord(
        id=make_resource_id("Duplicate"),
        kind=ResourceKind.file,
        uri="/tmp/dup.txt",
        title="Duplicate",
        sha256="2" * 64,
    )

    store.write_resource(resource)

    with pytest.raises(FileExistsError):
        store.write_resource(resource)


def test_resource_store_rejects_orphan_extraction(tmp_path) -> None:
    from agent_brain.memory.evidence.resource_store import ResourceStore
    from agent_brain.contracts.resource import (
        ExtractionKind,
        ExtractionRecord,
        make_extraction_id,
        make_resource_id,
        sha256_text,
    )

    store = ResourceStore(tmp_path)
    extraction = ExtractionRecord(
        id=make_extraction_id("Orphan"),
        resource_id=make_resource_id("Missing"),
        kind=ExtractionKind.text,
        extractor="manual",
        content_text="orphan",
        content_sha256=sha256_text("orphan"),
    )

    with pytest.raises(FileNotFoundError):
        store.write_extraction(extraction)


def test_memory_item_refs_preserve_resource_and_extraction_ids() -> None:
    item = MemoryItem.model_validate({
        "id": "mem-20260611-010203-demo",
        "type": "fact",
        "created_at": datetime(2026, 6, 11, tzinfo=timezone.utc).isoformat(),
        "title": "Demo",
        "summary": "Demo",
        "refs": {
            "resources": ["res-20260611-010203-demo-a1b2c3d4"],
            "extractions": ["ext-20260611-010204-demo-e5f6a7b8"],
        },
    })

    assert item.refs.resources == ["res-20260611-010203-demo-a1b2c3d4"]
    assert item.refs.extractions == ["ext-20260611-010204-demo-e5f6a7b8"]


def test_resource_reader_searches_resources_and_extractions(tmp_path) -> None:
    from agent_brain.memory.evidence.resource_reading import ResourceReader, ResourceSearchFilter
    from agent_brain.memory.evidence.resource_store import ResourceStore
    from agent_brain.contracts.resource import (
        ExtractionKind,
        ExtractionRecord,
        ResourceKind,
        ResourceRecord,
        make_extraction_id,
        make_resource_id,
        sha256_text,
    )

    store = ResourceStore(tmp_path)
    alpha = ResourceRecord(
        id=make_resource_id("Memory Isolation PDF"),
        kind=ResourceKind.pdf,
        uri="/tmp/memory-isolation.pdf",
        title="Memory Isolation PDF",
        sha256="a" * 64,
        project="agent-memory-hub",
        tags=["isolation", "pdf"],
    )
    beta = ResourceRecord(
        id=make_resource_id("Adapter Runtime Notes"),
        kind=ResourceKind.document,
        uri="/tmp/runtime.md",
        title="Adapter Runtime Notes",
        sha256="b" * 64,
        project="adapter",
        tags=["runtime"],
    )
    store.write_resource(alpha)
    store.write_resource(beta)
    text = "This extraction discusses progressive loading and memory isolation evidence."
    store.write_extraction(ExtractionRecord(
        id=make_extraction_id("Isolation summary"),
        resource_id=alpha.id,
        kind=ExtractionKind.summary,
        extractor="manual",
        content_text=text,
        content_sha256=sha256_text(text),
    ))

    reader = ResourceReader(store)

    hits = reader.search_resource("progressive evidence", top_k=5)
    assert [hit.resource.id for hit in hits] == [alpha.id]
    assert hits[0].score > 0

    filtered = reader.search_resource(
        "runtime",
        filters=ResourceSearchFilter(project="adapter", tags=["runtime"], kind=ResourceKind.document),
    )
    assert [hit.resource.id for hit in filtered] == [beta.id]


def test_resource_reader_applies_tenant_and_sensitivity_before_top_k(tmp_path) -> None:
    from agent_brain.contracts.resource import ResourceKind, ResourceRecord, make_resource_id
    from agent_brain.memory.evidence.resource_reading import (
        ResourceReader,
        ResourceSearchFilter,
    )
    from agent_brain.memory.evidence.resource_store import ResourceStore

    store = ResourceStore(tmp_path)
    other_tenant = ResourceRecord(
        id=make_resource_id("A boundary other tenant"),
        kind=ResourceKind.document,
        uri="/tmp/other.md",
        title="A web resource boundary",
        tenant_id="team-b",
        sensitivity="internal",
    )
    secret = ResourceRecord(
        id=make_resource_id("B boundary secret"),
        kind=ResourceKind.document,
        uri="/tmp/secret.md",
        title="B web resource boundary",
        tenant_id="team-a",
        sensitivity="secret",
    )
    allowed = ResourceRecord(
        id=make_resource_id("Z boundary allowed"),
        kind=ResourceKind.document,
        uri="/tmp/allowed.md",
        title="Z web resource boundary",
        tenant_id="team-a",
        sensitivity="internal",
    )
    for resource in (other_tenant, secret, allowed):
        store.write_resource(resource)

    hits = ResourceReader(store).search_resource(
        "web resource boundary",
        top_k=1,
        filters=ResourceSearchFilter(
            tenant_ids=(None, "team-a"),
            allowed_sensitivities=("public", "internal"),
        ),
    )

    assert [hit.resource.id for hit in hits] == [allowed.id]


def test_resource_reader_reads_summary_outline_segment_and_degraded(tmp_path) -> None:
    from agent_brain.memory.evidence.resource_reading import ResourceReader
    from agent_brain.memory.evidence.resource_store import ResourceStore
    from agent_brain.contracts.resource import (
        ExtractionKind,
        ExtractionRecord,
        ResourceKind,
        ResourceRecord,
        make_extraction_id,
        make_resource_id,
        sha256_text,
    )

    store = ResourceStore(tmp_path)
    resource = ResourceRecord(
        id=make_resource_id("Research PDF"),
        kind=ResourceKind.pdf,
        uri="/tmp/research.pdf",
        title="Research PDF",
        sha256="c" * 64,
    )
    store.write_resource(resource)
    for kind, text, locator in (
        (ExtractionKind.summary, "short summary", "summary"),
        (ExtractionKind.outline, "chapter outline", "outline"),
        (ExtractionKind.segment, "page twelve evidence", "page=12"),
    ):
        store.write_extraction(ExtractionRecord(
            id=make_extraction_id(f"{resource.title} {kind.value}"),
            resource_id=resource.id,
            kind=kind,
            extractor="manual",
            content_text=text,
            content_sha256=sha256_text(text),
            source_locator=locator,
        ))

    reader = ResourceReader(store)

    summary = reader.read_resource_summary(resource.id)
    outline = reader.read_resource_outline(resource.id)
    segment = reader.read_resource_segment(resource.id, locator="page=12")
    missing = reader.read_resource_segment(resource.id, locator="page=99")

    assert summary.status == "ok"
    assert summary.content_text == "short summary"
    assert outline.status == "ok"
    assert outline.content_text == "chapter outline"
    assert segment.status == "ok"
    assert segment.source_locator == "page=12"
    assert missing.status == "degraded"
    assert "No segment extraction" in missing.content_text


def test_resource_reader_packs_context_with_token_budget(tmp_path) -> None:
    from agent_brain.memory.evidence.resource_reading import ResourceReader
    from agent_brain.memory.evidence.resource_store import ResourceStore
    from agent_brain.contracts.resource import (
        ExtractionKind,
        ExtractionRecord,
        ResourceKind,
        ResourceRecord,
        make_extraction_id,
        make_resource_id,
        sha256_text,
    )

    store = ResourceStore(tmp_path)
    resource = ResourceRecord(
        id=make_resource_id("Budgeted PDF"),
        kind=ResourceKind.pdf,
        uri="/tmp/budgeted.pdf",
        title="Budgeted PDF",
        sha256="d" * 64,
    )
    store.write_resource(resource)
    for kind, text, locator in (
        (ExtractionKind.summary, "summary stays first", "summary"),
        (ExtractionKind.outline, "outline can fit when budget allows", "outline"),
        (ExtractionKind.segment, "segment one " * 12, "page=1"),
        (ExtractionKind.segment, "segment two " * 12, "page=2"),
    ):
        store.write_extraction(ExtractionRecord(
            id=make_extraction_id(f"{resource.title} {kind.value} {locator}"),
            resource_id=resource.id,
            kind=kind,
            extractor="manual",
            content_text=text,
            content_sha256=sha256_text(text),
            source_locator=locator,
        ))

    reader = ResourceReader(store)

    tight = reader.read_resource_context(resource.id, max_tokens=4)
    roomy = reader.read_resource_context(resource.id, max_tokens=80)

    assert [part.level for part in tight] == ["summary"]
    assert tight[0].content_text == "summary stays first"
    assert [part.level for part in roomy][:2] == ["summary", "outline"]
    assert "segment" in [part.level for part in roomy]


def test_resource_reading_module_functions_and_exact_read(tmp_path) -> None:
    from agent_brain.memory.evidence.resource_reading import (
        read_resource_context,
        read_resource_exact,
        read_resource_summary,
        search_resource,
    )
    from agent_brain.memory.evidence.resource_store import ResourceStore
    from agent_brain.contracts.resource import (
        ExtractionKind,
        ExtractionRecord,
        ResourceKind,
        ResourceRecord,
        make_extraction_id,
        make_resource_id,
        sha256_text,
    )

    store = ResourceStore(tmp_path)
    resource = ResourceRecord(
        id=make_resource_id("Exact Text"),
        kind=ResourceKind.document,
        uri="/tmp/exact.md",
        title="Exact Text",
        sha256="e" * 64,
        tags=["exact"],
    )
    store.write_resource(resource)
    for kind, text, locator in (
        (ExtractionKind.summary, "exact summary", "summary"),
        (ExtractionKind.text, "full extracted text", "body"),
    ):
        store.write_extraction(ExtractionRecord(
            id=make_extraction_id(f"{resource.title} {kind.value}"),
            resource_id=resource.id,
            kind=kind,
            extractor="manual",
            content_text=text,
            content_sha256=sha256_text(text),
            source_locator=locator,
        ))

    hits = search_resource(store, "exact", top_k=3)
    summary = read_resource_summary(store, resource.id)
    exact = read_resource_exact(store, resource.id, locator="body")
    context = read_resource_context(store, resource.id, max_tokens=20)

    assert [hit.resource.id for hit in hits] == [resource.id]
    assert summary.content_text == "exact summary"
    assert exact.status == "ok"
    assert exact.content_text == "full extracted text"
    assert [part.level for part in context] == ["summary"]


def test_quality_warns_for_unsourced_fact_and_multimodal_placeholder() -> None:
    from agent_brain.memory.store.quality import quality_warnings_for

    item = MemoryItem.model_validate({
        "id": "mem-20260611-020304-unsourced",
        "type": "fact",
        "created_at": datetime(2026, 6, 11, tzinfo=timezone.utc).isoformat(),
        "title": "Screenshot fact",
        "summary": "Screenshot fact",
    })

    warnings = quality_warnings_for(item, "**事实**\n[Image #1] shows the runtime status.")

    assert "fact body missing required sections: **来源**, **有效期**" in warnings
    assert "fact item has no source refs" in warnings
    assert "body contains multimodal placeholder without resource/extraction refs: [Image #1]" in warnings


def test_quality_accepts_resource_and_extraction_evidence_refs(tmp_path) -> None:
    from agent_brain.memory.store.quality import quality_warnings_for
    from agent_brain.memory.evidence.resource_store import ResourceStore
    from agent_brain.contracts.resource import (
        ExtractionKind,
        ExtractionRecord,
        ResourceKind,
        ResourceRecord,
        make_extraction_id,
        make_resource_id,
        sha256_text,
    )

    store = ResourceStore(tmp_path)
    resource = ResourceRecord(
        id=make_resource_id("Screenshot"),
        kind=ResourceKind.image,
        uri="/tmp/status.png",
        title="Screenshot",
        sha256="f" * 64,
    )
    store.write_resource(resource)
    extraction_text = "runtime status screenshot says 90 percent left"
    extraction = ExtractionRecord(
        id=make_extraction_id("Screenshot caption"),
        resource_id=resource.id,
        kind=ExtractionKind.vlm_caption,
        extractor="manual",
        content_text=extraction_text,
        content_sha256=sha256_text(extraction_text),
    )
    store.write_extraction(extraction)
    item = MemoryItem.model_validate({
        "id": "mem-20260611-020305-sourced",
        "type": "fact",
        "created_at": datetime(2026, 6, 11, tzinfo=timezone.utc).isoformat(),
        "title": "Screenshot fact",
        "summary": "Screenshot fact",
        "refs": {
            "resources": [resource.id],
            "extractions": [extraction.id],
        },
    })

    warnings = quality_warnings_for(
        item,
        "**事实**\n[Image #1] shows the runtime status.\n**来源**\ncaption\n**有效期**\ncurrent",
        brain_dir=tmp_path,
    )

    assert warnings == []


def test_quality_warns_for_missing_evidence_refs(tmp_path) -> None:
    from agent_brain.memory.store.quality import quality_warnings_for

    item = MemoryItem.model_validate({
        "id": "mem-20260611-020306-missing-evidence",
        "type": "fact",
        "created_at": datetime(2026, 6, 11, tzinfo=timezone.utc).isoformat(),
        "title": "Missing evidence",
        "summary": "Missing evidence",
        "refs": {
            "resources": ["res-20260611-020306-missing-a1b2c3d4"],
            "extractions": ["ext-20260611-020306-missing-e5f6a7b8"],
        },
    })

    warnings = quality_warnings_for(
        item,
        "**事实**\nEvidence claim.\n**来源**\nmissing evidence\n**有效期**\nunknown",
        brain_dir=tmp_path,
    )

    assert "refs.resources points to missing resource: res-20260611-020306-missing-a1b2c3d4" in warnings
    assert "refs.extractions points to missing extraction: ext-20260611-020306-missing-e5f6a7b8" in warnings
