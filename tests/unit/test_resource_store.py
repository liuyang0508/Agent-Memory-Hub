from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

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


def test_resource_and_extraction_ids_use_portable_ascii_slugs() -> None:
    from agent_brain.contracts.resource import make_extraction_id, make_resource_id

    forbidden = '<>:"/\\|?*\x00'
    for title in ("Café Memory", "CON", '<>:"/\\|?*\x00 中文'):
        resource_id = make_resource_id(title)
        extraction_id = make_extraction_id(title)
        assert re.fullmatch(r"res-\d{8}-\d{6}-[a-z0-9-]+", resource_id)
        assert re.fullmatch(r"ext-\d{8}-\d{6}-[a-z0-9-]+", extraction_id)
        assert not any(char in resource_id for char in forbidden)
        assert not any(char in extraction_id for char in forbidden)


def test_legacy_unicode_evidence_ids_validate_and_load_without_rewrite(
    fixtures_dir: Path,
) -> None:
    from agent_brain.contracts.resource import (
        ExtractionRecord,
        ResourceRecord,
        validate_resource_id,
    )
    from agent_brain.memory.evidence.resource_store import ResourceStore

    root = fixtures_dir / "legacy_evidence"
    resource_id = "res-20250101-010203-历史.证据-abcdef12"
    extraction_id = "ext-20250101-010204-摘要_一-12345678"
    resource_payload = json.loads(
        (root / "resources" / f"{resource_id}.json").read_text(encoding="utf-8")
    )
    extraction_payload = json.loads(
        (root / "extractions" / f"{extraction_id}.json").read_text(encoding="utf-8")
    )

    assert ResourceRecord.model_validate(resource_payload).id == resource_id
    assert ExtractionRecord.model_validate(extraction_payload).id == extraction_id
    store = ResourceStore(root)
    assert store.get_resource(resource_id).id == resource_id
    assert store.get_extraction(extraction_id).resource_id == resource_id
    assert (
        validate_resource_id("res-20250101-010203-Cafe\u0301_证据.١-abcdef12")
        == "res-20250101-010203-Cafe\u0301_证据.١-abcdef12"
    )


def test_structural_utc_v1_sentinel_is_portable_for_evidence_ids() -> None:
    from agent_brain.contracts.resource import (
        validate_extraction_id,
        validate_resource_id,
    )

    resource_id = "res-20260724-120000-~utc-v1~portable-abcdef12"
    extraction_id = "ext-20260724-120000-~utc-v1~portable-12345678"

    assert validate_resource_id(resource_id) == resource_id
    assert validate_extraction_id(extraction_id) == extraction_id


def test_legacy_punctuation_evidence_fixtures_load_without_rewrite(
    fixtures_dir: Path,
) -> None:
    from agent_brain.memory.evidence.resource_store import ResourceStore

    root = fixtures_dir / "legacy_evidence"
    resource_id = "res-20250102-010203-旧版#A+官网@README，回填、审核！-abcdef12"
    extraction_id = "ext-20250102-010204-摘要#A+@中文（旧版）！-12345678"

    store = ResourceStore(root)

    assert store.get_resource(resource_id).id == resource_id
    assert store.get_extraction(extraction_id).id == extraction_id


def test_resource_iteration_skips_one_unsafe_record_and_keeps_valid_records(
    tmp_path: Path,
) -> None:
    from agent_brain.contracts.resource import ResourceKind, ResourceRecord, make_resource_id
    from agent_brain.memory.evidence.resource_store import ResourceStore

    store = ResourceStore(tmp_path)
    valid = ResourceRecord(
        id=make_resource_id("valid"),
        kind=ResourceKind.document,
        uri="memory://valid",
        title="valid",
    )
    store.write_resource(valid)
    (store.resources_dir / "000-unsafe.json").write_text(
        json.dumps(
            {
                "id": "res-20250102-010203-unsafe:colon-abcdef12",
                "kind": "document",
                "uri": "memory://unsafe",
                "title": "unsafe",
            }
        ),
        encoding="utf-8",
    )

    assert [record.id for record in store.iter_resources()] == [valid.id]
    assert store.last_scan.skipped_count == 1
    assert store.last_scan.skipped[0].path.name == "000-unsafe.json"


def test_extraction_iteration_skips_one_corrupt_record_and_keeps_valid_records(
    tmp_path: Path,
) -> None:
    from agent_brain.contracts.resource import (
        ExtractionKind,
        ExtractionRecord,
        ResourceKind,
        ResourceRecord,
        make_extraction_id,
        make_resource_id,
        sha256_text,
    )
    from agent_brain.memory.evidence.resource_store import ResourceStore

    store = ResourceStore(tmp_path)
    resource = ResourceRecord(
        id=make_resource_id("valid resource"),
        kind=ResourceKind.document,
        uri="memory://valid",
        title="valid",
    )
    extraction = ExtractionRecord(
        id=make_extraction_id("valid extraction"),
        resource_id=resource.id,
        kind=ExtractionKind.text,
        extractor="test",
        content_text="valid",
        content_sha256=sha256_text("valid"),
    )
    store.write_resource(resource)
    store.write_extraction(extraction)
    (store.extractions_dir / "000-corrupt.json").write_text(
        "{not-json\n", encoding="utf-8"
    )

    assert [record.id for record in store.iter_extractions()] == [extraction.id]
    assert store.last_scan.skipped_count == 1
    assert store.last_scan.skipped[0].path.name == "000-corrupt.json"


@pytest.mark.parametrize(
    "unsafe_id",
    [
        "res-20250101-010203-../escape",
        "res-20250101-010203-back\\slash",
        "res-20250101-010203-colon:name",
        "res-20250101-010203-less<than",
        "res-20250101-010203-greater>than",
        'res-20250101-010203-double"quote',
        "res-20250101-010203-pipe|name",
        "res-20250101-010203-question?name",
        "res-20250101-010203-star*name",
        "res-20250101-010203-white space",
        "res-20250101-010203-nonbreaking\u00a0space",
        "res-20250101-010203-zero\u200bwidth",
        "res-20250101-010203-bidi\u202ename",
        "res-20250101-010203-tab\tname",
        "res-20250101-010203-control\x00name",
        "res-20250101-010203-",
        "res-20250101-010203-..",
        "res-20250101-010203-trailing.",
    ],
)
def test_legacy_evidence_id_validator_rejects_path_and_platform_hazards(
    unsafe_id: str,
) -> None:
    from agent_brain.contracts.resource import ResourceKind, ResourceRecord

    with pytest.raises(ValueError):
        ResourceRecord(
            id=unsafe_id,
            kind=ResourceKind.document,
            uri="memory://unsafe",
            title="unsafe",
        )


def test_resource_store_rejects_unsafe_lookup_before_path_access(tmp_path: Path) -> None:
    from agent_brain.memory.evidence.resource_store import ResourceStore

    store = ResourceStore(tmp_path / "brain")
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="INVALID_RESOURCE_ID"):
        store.get_resource("../outside")


def test_resource_id_validation_rejects_controls_without_echoing_input() -> None:
    from pydantic import ValidationError

    from agent_brain.contracts.resource import ResourceKind, ResourceRecord

    secret_controlled_id = "res-20260720-120000-secret\x00token"
    with pytest.raises(ValidationError) as error:
        ResourceRecord(
            id=secret_controlled_id,
            kind=ResourceKind.file,
            uri="file:///tmp/a",
            title="safe",
        )

    assert secret_controlled_id not in str(error.value)

    from agent_brain.contracts.resource import (
        ExtractionKind,
        ExtractionRecord,
        make_resource_id,
        sha256_text,
    )

    unsafe_extraction_id = "ext-20260720-120000-private\x00detail"
    with pytest.raises(ValidationError) as extraction_error:
        ExtractionRecord(
            id=unsafe_extraction_id,
            resource_id=make_resource_id("safe"),
            kind=ExtractionKind.text,
            extractor="test",
            content_text="body",
            content_sha256=sha256_text("body"),
        )
    assert unsafe_extraction_id not in str(extraction_error.value)


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


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS system alias")
@pytest.mark.parametrize("temp_root", [None, "/tmp"])
def test_resource_store_accepts_trusted_macos_root_aliases(temp_root: str | None) -> None:
    from agent_brain.contracts.resource import (
        ResourceKind,
        ResourceRecord,
        make_resource_id,
    )
    from agent_brain.memory.evidence.resource_store import ResourceStore

    if temp_root is not None and not Path(temp_root).is_symlink():
        pytest.skip(f"{temp_root} is not a system symlink")
    with tempfile.TemporaryDirectory(dir=temp_root) as temporary:
        assert str(temporary).startswith("/var/" if temp_root is None else "/tmp/")
        store = ResourceStore(Path(temporary) / "brain")
        resource = ResourceRecord(
            id=make_resource_id("macOS root alias"),
            kind=ResourceKind.document,
            uri="memory://macos-alias",
            title="macOS root alias",
        )

        store.write_resource(resource)

        assert store.get_resource(resource.id) == resource


def test_resource_store_unsupported_platform_fallback_is_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from agent_brain.contracts.resource import (
        ResourceKind,
        ResourceRecord,
        make_resource_id,
    )
    from agent_brain.memory.evidence import resource_store as resource_store_module

    monkeypatch.setattr(
        resource_store_module,
        "secure_dir_fd_mutation_supported",
        lambda: False,
    )
    monkeypatch.setattr(resource_store_module, "_STRICT_SECURE_MUTATION", False)
    resource = ResourceRecord(
        id=make_resource_id("fallback"),
        kind=ResourceKind.document,
        uri="memory://fallback",
        title="fallback",
    )

    with caplog.at_level(
        logging.WARNING,
        logger="agent_brain.memory.evidence.resource_store",
    ):
        store = resource_store_module.ResourceStore(tmp_path / "brain")
        store.write_resource(resource)

    assert store.get_resource(resource.id) == resource
    assert "RESOURCE_STORE_SECURE_IO_UNAVAILABLE" in caplog.text


def test_resource_store_posix_without_secure_mutation_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_brain.memory.evidence import resource_store as resource_store_module

    monkeypatch.setattr(
        resource_store_module,
        "secure_dir_fd_mutation_supported",
        lambda: False,
    )

    with pytest.raises(OSError, match="SECURE_RESOURCE_STORE_UNAVAILABLE"):
        resource_store_module.ResourceStore(tmp_path / "brain")


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


@pytest.mark.parametrize("directory_name", ["resources", "extractions"])
def test_resource_store_rejects_symlinked_storage_directory_without_external_write(
    tmp_path: Path,
    directory_name: str,
) -> None:
    from agent_brain.contracts.resource import (
        ExtractionKind,
        ExtractionRecord,
        ResourceKind,
        ResourceRecord,
        make_extraction_id,
        make_resource_id,
        sha256_text,
    )
    from agent_brain.memory.evidence.resource_store import ResourceStore

    brain = tmp_path / "brain"
    outside = tmp_path / "outside"
    brain.mkdir()
    outside.mkdir()
    (brain / directory_name).symlink_to(outside, target_is_directory=True)
    other = "extractions" if directory_name == "resources" else "resources"
    (brain / other).mkdir()
    resource = ResourceRecord(
        id=make_resource_id(f"{directory_name} escape"),
        kind=ResourceKind.document,
        uri="memory://escape",
        title="escape",
    )
    extraction = ExtractionRecord(
        id=make_extraction_id(f"{directory_name} escape"),
        resource_id=resource.id,
        kind=ExtractionKind.text,
        extractor="test",
        content_text="outside canary",
        content_sha256=sha256_text("outside canary"),
    )

    with pytest.raises(OSError):
        store = ResourceStore(brain)
        store.write_resource(resource)
        store.write_extraction(extraction)

    assert list(outside.iterdir()) == []


def test_resource_store_rejects_symlinked_root_ancestor_without_external_creation(
    tmp_path: Path,
) -> None:
    from agent_brain.memory.evidence.resource_store import ResourceStore

    outside = tmp_path / "outside"
    outside.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError):
        ResourceStore(alias / "brain")

    assert not (outside / "brain").exists()


def test_resource_store_rejects_broken_target_symlink_without_external_write(
    tmp_path: Path,
) -> None:
    from agent_brain.contracts.resource import (
        ResourceKind,
        ResourceRecord,
        make_resource_id,
    )
    from agent_brain.memory.evidence.resource_store import ResourceStore

    store = ResourceStore(tmp_path / "brain")
    outside = tmp_path / "outside.json"
    resource = ResourceRecord(
        id=make_resource_id("broken target escape"),
        kind=ResourceKind.document,
        uri="memory://escape",
        title="escape",
    )
    (store.resources_dir / f"{resource.id}.json").symlink_to(outside)

    with pytest.raises(OSError):
        store.write_resource(resource)

    assert not outside.exists()


def test_resource_store_create_race_cannot_replace_target_with_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_brain.contracts.resource import (
        ResourceKind,
        ResourceRecord,
        make_resource_id,
    )
    from agent_brain.memory.evidence.resource_store import ResourceStore
    from agent_brain.memory.store.durable_fs import SecureDirectory

    store = ResourceStore(tmp_path / "brain")
    outside = tmp_path / "outside.json"
    resource = ResourceRecord(
        id=make_resource_id("create race"),
        kind=ResourceKind.document,
        uri="memory://race",
        title="race",
    )
    real_create = SecureDirectory.atomic_create

    def swap_then_create(
        directory: SecureDirectory,
        name: str,
        data: bytes,
        *,
        mode: int = 0o600,
    ) -> None:
        (store.resources_dir / name).symlink_to(outside)
        real_create(directory, name, data, mode=mode)

    monkeypatch.setattr(SecureDirectory, "atomic_create", swap_then_create)

    with pytest.raises(OSError):
        store.write_resource(resource)

    assert not outside.exists()


def test_resource_store_get_and_iter_do_not_follow_record_symlinks(
    tmp_path: Path,
) -> None:
    from agent_brain.contracts.resource import (
        ResourceKind,
        ResourceRecord,
        make_resource_id,
    )
    from agent_brain.memory.evidence.resource_store import ResourceStore

    store = ResourceStore(tmp_path / "brain")
    resource = ResourceRecord(
        id=make_resource_id("outside record"),
        kind=ResourceKind.document,
        uri="memory://outside",
        title="outside",
    )
    outside = tmp_path / "outside.json"
    outside.write_text(
        json.dumps(resource.model_dump(mode="json"), sort_keys=True),
        encoding="utf-8",
    )
    (store.resources_dir / f"{resource.id}.json").symlink_to(outside)

    with pytest.raises(OSError):
        store.get_resource(resource.id)
    assert list(store.iter_resources()) == []
    assert store.last_scan.skipped_count == 1


def test_resource_store_get_and_iter_reject_fifo_without_blocking(
    tmp_path: Path,
) -> None:
    from agent_brain.contracts.resource import make_resource_id
    from agent_brain.memory.evidence.resource_store import ResourceStore

    store = ResourceStore(tmp_path / "brain")
    resource_id = make_resource_id("fifo record")
    fifo = store.resources_dir / f"{resource_id}.json"
    os.mkfifo(fifo)

    with pytest.raises(OSError):
        store.get_resource(resource_id)
    assert list(store.iter_resources()) == []
    assert store.last_scan.skipped_count == 1


def test_resource_store_secure_read_preserves_large_record_semantics(
    tmp_path: Path,
) -> None:
    from agent_brain.contracts.resource import (
        ExtractionKind,
        ExtractionRecord,
        ResourceKind,
        ResourceRecord,
        make_extraction_id,
        make_resource_id,
        sha256_text,
    )
    from agent_brain.memory.evidence.resource_store import ResourceStore

    store = ResourceStore(tmp_path / "brain")
    resource = ResourceRecord(
        id=make_resource_id("large extraction"),
        kind=ResourceKind.document,
        uri="memory://large",
        title="large",
    )
    text = "x" * (300 * 1024)
    extraction = ExtractionRecord(
        id=make_extraction_id("large extraction"),
        resource_id=resource.id,
        kind=ExtractionKind.text,
        extractor="test",
        content_text=text,
        content_sha256=sha256_text(text),
    )

    store.write_resource(resource)
    store.write_extraction(extraction)

    assert store.get_extraction(extraction.id).content_text == text
    assert [record.id for record in store.iter_extractions()] == [extraction.id]


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
