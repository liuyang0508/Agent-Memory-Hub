from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from agent_brain.contracts.memory_enums import MemoryType, Sensitivity
from agent_brain.contracts.memory_item import MemoryItem, Refs, Source
from agent_brain.contracts.resource import ExtractionRecord, ResourceRecord
from agent_brain.memory.evidence.resource_store import ResourceStore
from agent_brain.memory.store.items_store import make_item_id
from agent_brain.memory.store.write_service import WriteService
from agent_brain.memory.store.write_types import WriteResult


_TAG_TOKEN_RE = re.compile(r"[^a-zA-Z0-9_.:-]+")


def promote_extraction_to_memory(
    *,
    brain_dir: Path,
    extraction_id: str,
    memory_type: MemoryType | str = MemoryType.fact,
    title: str | None = None,
    summary: str | None = None,
    body: str | None = None,
    tags: list[str] | None = None,
    agent: str | None = None,
    session: str | None = None,
    project: str | None = None,
    tenant_id: str | None = None,
    sensitivity: Sensitivity | str | None = None,
    confidence: float | None = None,
    allow_unsafe: bool = False,
) -> WriteResult:
    """Promote a resource extraction into the normal MemoryItem write funnel.

    Resource/Extraction records remain evidence. The promoted MemoryItem is the
    searchable, governed knowledge unit and keeps refs back to both evidence
    sidecars so recall can move from locator -> overview -> exact source.
    """
    resource_store = ResourceStore(brain_dir)
    extraction = resource_store.get_extraction(extraction_id)
    resource = resource_store.get_resource(extraction.resource_id)
    now = datetime.now(timezone.utc).astimezone()
    item_title = title or _default_title(resource, extraction)
    item_summary = summary or _summary_from_extraction(extraction)
    item_tags = _promotion_tags(resource, extraction, extra=tags or [])
    mem_type = MemoryType(memory_type)
    mem_sensitivity = Sensitivity(sensitivity or resource.sensitivity)
    item = MemoryItem(
        id=make_item_id(item_title, when=now),
        type=mem_type,
        created_at=now,
        agent=agent,
        session=session,
        project=project or resource.project,
        tenant_id=tenant_id or resource.tenant_id,
        tags=item_tags,
        sensitivity=mem_sensitivity,
        title=item_title,
        summary=item_summary,
        refs=Refs(resources=[resource.id], extractions=[extraction.id]),
        confidence=confidence if confidence is not None else extraction.confidence,
        source=Source(
            kind="multimodal-extraction",
            extractor=extraction.extractor,
        ),
        context_views={
            "locator": item_summary,
            "overview": _overview_from_extraction(resource, extraction),
        },
    )
    return WriteService.for_brain(brain_dir).write(
        item=item,
        body=body or _default_body(resource, extraction, memory_type=mem_type),
        allow_unsafe=allow_unsafe,
        overview=item.context_views.overview,
    )


def _default_title(resource: ResourceRecord, extraction: ExtractionRecord) -> str:
    return f"{resource.title} {extraction.kind} memory"


def _summary_from_extraction(extraction: ExtractionRecord, *, max_chars: int = 160) -> str:
    text = " ".join(extraction.content_text.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _overview_from_extraction(resource: ResourceRecord, extraction: ExtractionRecord) -> str:
    locator = extraction.source_locator or resource.uri
    return (
        f"多模态抽取证据：resource={resource.id} kind={resource.kind}; "
        f"extraction={extraction.id} kind={extraction.kind}; locator={locator}"
    )


def _default_body(
    resource: ResourceRecord,
    extraction: ExtractionRecord,
    *,
    memory_type: MemoryType,
) -> str:
    if memory_type == MemoryType.fact:
        return (
            "**事实**\n"
            f"{extraction.content_text}\n\n"
            "**来源**\n"
            f"- resource: {resource.id} ({resource.kind}, {resource.uri})\n"
            f"- extraction: {extraction.id} ({extraction.kind}, extractor={extraction.extractor}, "
            f"confidence={extraction.confidence})\n"
            f"- locator: {extraction.source_locator or resource.uri}\n\n"
            "**有效期**\n"
            "由原始资源和抽取证据决定；使用前按 refs.resources / refs.extractions 回查。"
        )
    return (
        f"{extraction.content_text}\n\n"
        "## Evidence\n"
        f"- resource: {resource.id} ({resource.kind}, {resource.uri})\n"
        f"- extraction: {extraction.id} ({extraction.kind}, extractor={extraction.extractor})\n"
        f"- locator: {extraction.source_locator or resource.uri}"
    )


def _promotion_tags(
    resource: ResourceRecord,
    extraction: ExtractionRecord,
    *,
    extra: list[str],
) -> list[str]:
    tags = [
        *resource.tags,
        *extra,
        "source:multimodal",
        "evidence:resource",
        "evidence:extraction",
        f"modality:{_tag_token(str(resource.kind))}",
        f"extraction:{_tag_token(str(extraction.kind))}",
    ]
    if extraction.extractor:
        tags.append(f"extractor:{_tag_token(extraction.extractor)}")
    return _dedupe(tags)


def _tag_token(value: str) -> str:
    token = _TAG_TOKEN_RE.sub("-", value.strip().lower()).strip("-")
    return token or "unknown"


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        v = value.strip()
        if v and v not in seen:
            out.append(v)
            seen.add(v)
    return out


__all__ = ["promote_extraction_to_memory"]
