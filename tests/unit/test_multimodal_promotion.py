from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

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
from agent_brain.memory.store.items_store import ItemsStore


runner = CliRunner()


def _seed_image_extraction(brain_dir: Path) -> tuple[ResourceRecord, ExtractionRecord]:
    store = ResourceStore(brain_dir)
    resource = ResourceRecord(
        id=make_resource_id("Runtime Screenshot"),
        kind=ResourceKind.image,
        uri="file:///tmp/runtime-screenshot.png",
        title="Runtime Screenshot",
        mime_type="image/png",
        sha256="a" * 64,
        tags=["runtime", "screenshot"],
        project="agent-memory-hub",
    )
    extraction_text = "截图显示链路追踪模块按 Agent、行为面和时间范围筛选记忆事件。"
    extraction = ExtractionRecord(
        id=make_extraction_id("Runtime Screenshot Caption"),
        resource_id=resource.id,
        kind=ExtractionKind.vlm_caption,
        extractor="openviking-vlm",
        extractor_version="1",
        content_text=extraction_text,
        content_sha256=sha256_text(extraction_text),
        confidence=0.82,
        source_locator="image://runtime-screenshot#caption",
    )
    store.write_resource(resource)
    store.write_extraction(extraction)
    return resource, extraction


def test_promote_extraction_to_memory_preserves_multimodal_evidence(tmp_brain_dir: Path, monkeypatch) -> None:
    from agent_brain.memory.evidence.extraction_promotion import promote_extraction_to_memory

    monkeypatch.setenv("MEMORY_HUB_EMBEDDING_OFFLINE", "1")
    resource, extraction = _seed_image_extraction(tmp_brain_dir)

    result = promote_extraction_to_memory(
        brain_dir=tmp_brain_dir,
        extraction_id=extraction.id,
        title="链路追踪截图事实",
        summary="截图显示链路追踪模块支持按 Agent、行为面和时间范围筛选。",
        agent="codex",
        project="agent-memory-hub",
        tags=["trace-ui"],
    )

    assert result.status == "written"
    assert result.item_id
    item, body = ItemsStore(tmp_brain_dir / "items").get(result.item_id)
    assert item.refs.resources == [resource.id]
    assert item.refs.extractions == [extraction.id]
    assert item.source.kind == "multimodal-extraction"
    assert item.source.extractor == "openviking-vlm"
    assert item.confidence == extraction.confidence
    assert {
        "source:multimodal",
        "modality:image",
        "evidence:resource",
        "evidence:extraction",
        "extraction:vlm_caption",
        "extractor:openviking-vlm",
        "trace-ui",
    }.issubset(set(item.tags))
    assert "截图显示链路追踪模块" in body
    assert resource.id in body
    assert extraction.id in body
    assert not any("multimodal placeholder" in warning for warning in result.warnings)
    assert (tmp_brain_dir / "sources" / "writes" / f"{item.id}.json").exists()


def test_resource_promote_extraction_cli_writes_json_result(tmp_brain_dir: Path, monkeypatch) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    monkeypatch.setenv("MEMORY_HUB_EMBEDDING_OFFLINE", "1")
    resource, extraction = _seed_image_extraction(tmp_brain_dir)

    result = runner.invoke(app, [
        "resource",
        "promote-extraction",
        extraction.id,
        "--title",
        "CLI 多模态事实",
        "--summary",
        "CLI 将图片 caption 提升为可检索记忆。",
        "--tag",
        "cli",
        "--format",
        "json",
    ])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "written"
    assert payload["refs"]["resources"] == [resource.id]
    assert payload["refs"]["extractions"] == [extraction.id]
    assert "modality:image" in payload["tags"]
    item, body = ItemsStore(tmp_brain_dir / "items").get(payload["item_id"])
    assert item.refs.resources == [resource.id]
    assert item.refs.extractions == [extraction.id]
    assert "CLI 将图片 caption" in item.summary
    assert "截图显示链路追踪模块" in body
