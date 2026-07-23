"""Single write funnel for the brain pool.

Every write entry point (MCP, CLI, hook shim, pending replay, harvester) goes
through ``WriteService``. Invariant: ``ItemsStore.write`` (the markdown append) is
the ONLY thing that defines "written"; embedding + index upsert are best-effort and
their failure degrades — never blocks — the write. Before persisting, an audit gate
fail-closes on critical/high findings unless the caller passes ``allow_unsafe=True``.

Usage::

    svc = WriteService.for_brain()           # brain dir from $BRAIN_DIR
    svc = WriteService.for_brain(brain_dir)   # or an explicit dir (tests)
    res = svc.write(item=item, body=body)     # -> WriteResult

Depends on: ``ItemsStore`` (md source of truth, required), ``HubIndex`` +
``get_default_embedder`` (derived index, optional/lazy), ``audit_memory_text``
(write gate). When the index/embedder can't be built (offline, missing model),
the service still writes md and records the item id in the index-dirty log so a
later ``sync-pending``/reindex can repair the derived index.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

from agent_brain.memory.governance.audit.scanner import audit_memory_text
from agent_brain.memory.recall.embedding_text import embedding_text_for_item
from agent_brain.memory.evidence.resource_store import ResourceStore
from agent_brain.memory.store.field_enrichment import enrich_memory_item
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.store.quality import quality_warnings_for
from agent_brain.memory.store.write_types import WriteResult
from agent_brain.platform.embedding import Embedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.contracts.memory_item import MemoryItem, Refs
from agent_brain.contracts.resource import (
    ExtractionKind,
    ExtractionRecord,
    ResourceKind,
    ResourceRecord,
    make_extraction_id,
    make_resource_id,
    sha256_file,
    sha256_text,
)

logger = logging.getLogger(__name__)

# Severities that fail-close the write gate. Medium/low are advisory and do not
# block (mirrors AuditReport.passed, which is False only on critical/high).
_BLOCKING_SEVERITIES = ("critical", "high")
_REVIEW_SOURCE_KINDS = {"harvested", "pending-replay", "remember"}
_REVIEW_SOURCE_TAGS = {"harvested", "conversation", "transcript"}
_REVIEW_TAGS = ("needs-review", "unverified-boundary")
_REVIEW_CONFIDENCE_CEILING = 0.35
_REVIEW_WARNING = "memory item lacks explicit validity/source boundary; marked needs-review"
_MULTIMODAL_PLACEHOLDER_RE = re.compile(
    r"\[(?:Image|Audio|Video|PDF|Document)\s+#\d+\]",
    re.IGNORECASE,
)
_TEXT_EXTRACTION_MAX_BYTES = 256 * 1024
_MAX_WRITE_EVIDENCE_FILES = 64
_MAX_WRITE_EVIDENCE_REFS = 256
_MAX_WRITE_EVIDENCE_FILE_BYTES = 64 * 1024 * 1024
_MAX_WRITE_EVIDENCE_RECORD_BYTES = 2 * 1024 * 1024
_GENERATED_EVIDENCE_CLOCK_SKEW_SECONDS = 5
_GENERATED_EVIDENCE_ID_RE = re.compile(
    r"^(?P<prefix>res|ext)-(?P<date>\d{8})-(?P<time>\d{6})-"
)


@dataclass(frozen=True)
class _WriteEvidenceFile:
    ref_file: str
    path: Path
    sha256: str
    size_bytes: int
    text: str | None


@dataclass(frozen=True)
class _WriteEvidenceSpec:
    id_title: str
    resource: ResourceRecord
    extraction: ExtractionRecord | None = None


def _brain_dir() -> Path:
    """Resolve the on-disk brain root, honoring ``$BRAIN_DIR``.

    Matches the resolution used by every other entry point (mcp_server,
    core.pending) so a single ``BRAIN_DIR`` controls the whole system.
    """
    return Path(os.environ.get("BRAIN_DIR", os.path.expanduser("~/.agent-memory-hub")))


class WriteService:
    """The one and only write path into the brain pool."""

    def __init__(
        self,
        store: ItemsStore,
        index: HubIndex | Callable[[], HubIndex] | None = None,
        embedder: Embedder | Callable[[], Embedder] | None = None,
        brain_dir: Path | None = None,
        owns_index: bool = False,
    ) -> None:
        self._store = store
        self._index = index
        self._embedder = embedder
        self._brain_dir = brain_dir
        self._owns_index = owns_index

    @classmethod
    def for_brain(cls, brain_dir: Path | None = None) -> "WriteService":
        """Build a service against the on-disk brain; index/embedder are lazy & optional.

        The md store is always constructed (it is the source of truth, so a write
        cannot proceed without it). The index + embedder are built inside a guard:
        any failure (offline, model unavailable, locked sqlite) leaves them unset
        so writes still land in md and merely degrade the derived index.
        """
        brain = brain_dir if brain_dir is not None else _brain_dir()
        store = ItemsStore(items_dir=brain / "items")
        index = None
        embedder = None
        try:
            from agent_brain.platform.embedding import get_default_embedder
            from agent_brain.platform.indexing.index import HubIndex

            embedder = get_default_embedder()
            index = HubIndex(db_path=brain / "index.db", embedding_dim=embedder.dim)
        except Exception:
            # Degraded: the write still works; the index is repaired later.
            index = None
            embedder = None
        return cls(
            store,
            index,
            embedder,
            brain_dir=brain,
            owns_index=index is not None,
        )

    def __enter__(self) -> "WriteService":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._owns_index:
            return
        index = self._index
        if index is not None and not callable(index):
            index.close()
        self._owns_index = False

    def write(
        self,
        *,
        item: MemoryItem,
        body: str,
        allow_unsafe: bool = False,
        overview: str | None = None,
    ) -> WriteResult:
        """Funnel a single item+body into the pool.

        1. Audit gate (unless ``allow_unsafe``): refuse on critical/high findings.
        2. md append — the ONLY verdict for "written".
        3. index upsert — best-effort; failure degrades, never blocks.
        """
        if overview is not None:
            item = _with_overview(item, overview)
        if not allow_unsafe:
            report = audit_memory_text(
                f"{item.title}\n{item.summary}\n{item.context_views.overview}\n{body}"
            )
            if not report.passed:
                return WriteResult(
                    status="blocked",
                    findings=[
                        {"rule_id": f.rule_id, "severity": f.severity, "description": f.description}
                        for f in report.findings
                        if f.severity in _BLOCKING_SEVERITIES
                    ],
                )
        else:
            logger.warning(
                "allow_unsafe audit bypass for memory item %s type=%s source=%s",
                item.id,
                item.type,
                getattr(item.source, "kind", None),
            )
        item, review_warning = _mark_boundary_review_candidate(item)
        item = enrich_memory_item(item)
        item = self._attach_evidence_sidecar(item, body)
        # md append — the source of truth. If this raises, the write genuinely
        # failed and the exception propagates to the caller (entry points decide
        # whether to buffer to the pending queue).
        warnings = quality_warnings_for(item, body, brain_dir=self._brain_dir)
        if review_warning:
            warnings.append(review_warning)
        path = self._store.write(item, body)
        source_ledger_degraded = False
        try:
            _write_source_record(
                brain_dir=self._brain_dir or self._store.items_dir.parent,
                item=item,
                item_path=path,
                body=body,
            )
        except Exception:
            source_ledger_degraded = True
            warnings.append("SOURCE_LEDGER_REPAIR_REQUIRED")
        result = WriteResult(status="written", item_id=item.id, path=str(path), warnings=warnings)
        if source_ledger_degraded:
            result.degraded.append("source-ledger")
        # index — best-effort; a failure here must never undo "written".
        try:
            self._index_item(item, body)
            result.indexed = True
        except Exception:
            result.indexed = False
            result.degraded.append("index")
            self._mark_dirty(item.id)
        return result

    def reconcile_existing(self, *, item: MemoryItem, body: str) -> WriteResult:
        """Finish durable side effects for an item whose markdown already exists.

        Pending replay can observe the narrow crash window after the source of
        truth was created but before the source ledger or derived index landed.
        Reconciliation deliberately uses the stored item/body verbatim: it does
        not re-audit, enrich, or rewrite markdown.
        """

        path = self._store.items_dir / f"{item.id}.md"
        warnings: list[str] = []
        result = WriteResult(status="written", item_id=item.id, path=str(path), warnings=warnings)
        try:
            _write_source_record(
                brain_dir=self._brain_dir or self._store.items_dir.parent,
                item=item,
                item_path=path,
                body=body,
            )
        except Exception:
            result.degraded.append("source-ledger")
            warnings.append("SOURCE_LEDGER_REPAIR_REQUIRED")

        try:
            self._index_item(item, body)
            result.indexed = True
        except Exception:
            result.degraded.append("index")
            self._mark_dirty(item.id)
        return result

    def _index_item(self, item: MemoryItem, body: str) -> None:
        """Embed and upsert into the derived index. Raises when unavailable."""
        index = self._index() if callable(self._index) else self._index
        embedder = self._embedder() if callable(self._embedder) else self._embedder
        self._index = index
        self._embedder = embedder
        if index is None or embedder is None:
            raise RuntimeError("index/embedder unavailable")
        embedding = embedder.embed(embedding_text_for_item(item))
        index.upsert(item, body, embedding=embedding)

    def _mark_dirty(self, item_id: str) -> None:
        """Record that ``item_id`` has md but a stale/missing index row.

        Best-effort: the markdown is already the source of truth, so if the
        pending module isn't present yet or the dirty-log can't be written, we
        swallow the error rather than fail an otherwise-successful write. A later
        reindex/``sync-pending`` consumes this log to repair the derived index.
        """
        try:
            from agent_brain.memory.store.pending import append_dirty_index_marker

            append_dirty_index_marker(
                self._brain_dir or self._store.items_dir.parent,
                item_id,
            )
        except Exception:
            pass

    def _attach_evidence_sidecar(self, item: MemoryItem, body: str) -> MemoryItem:
        """Attach resource/extraction evidence produced by the write boundary.

        Explicit refs stay authoritative. Existing file refs are mirrored into
        ResourceStore sidecars when the local file is readable. If no extraction
        evidence exists after that, plain text write input is captured as a
        small evidence sidecar so fact/decision items do not become untraceable
        bare assertions. Multimodal placeholders are deliberately excluded:
        ``[Image #1]`` requires a real resource/extraction, not a text echo.
        """
        brain = self._brain_dir or self._store.items_dir.parent
        resource_store = ResourceStore(brain)
        refs = item.refs.model_dump(mode="json")
        resources = list(refs.get("resources") or [])
        extractions = list(refs.get("extractions") or [])

        for ref_file in refs.get("files") or []:
            resource_id, extraction_id = _write_file_ref_sidecar(
                resource_store=resource_store,
                item=item,
                ref_file=ref_file,
            )
            if resource_id:
                resources.append(resource_id)
            if extraction_id:
                extractions.append(extraction_id)

        if not extractions and _should_capture_write_input(body):
            resource_id, extraction_id = _write_input_sidecar(
                resource_store=resource_store,
                item=item,
                body=body,
            )
            resources.append(resource_id)
            extractions.append(extraction_id)

        if resources == item.refs.resources and extractions == item.refs.extractions:
            return item

        refs["resources"] = _dedupe(resources)
        refs["extractions"] = _dedupe(extractions)
        return item.model_copy(update={"refs": Refs.model_validate(refs)})


def get_write_service() -> WriteService:
    """Convenience factory: a WriteService bound to the configured brain dir."""
    return WriteService.for_brain()


def _mark_boundary_review_candidate(item: MemoryItem) -> tuple[MemoryItem, str | None]:
    if not _needs_boundary_review(item):
        return item, None
    tags = sorted({*item.tags, *_REVIEW_TAGS})
    confidence = min(item.confidence, _REVIEW_CONFIDENCE_CEILING)
    return item.model_copy(update={"tags": tags, "confidence": confidence}), _REVIEW_WARNING


def _needs_boundary_review(item: MemoryItem) -> bool:
    tags = {tag.strip().lower() for tag in item.tags}
    if tags & set(_REVIEW_TAGS):
        return False
    source_kind = str(getattr(item.source, "kind", "") or "").strip().lower()
    if source_kind not in _REVIEW_SOURCE_KINDS and not (tags & _REVIEW_SOURCE_TAGS):
        return False
    return not _has_explicit_boundary(item)


def _has_explicit_boundary(item: MemoryItem) -> bool:
    refs = item.refs
    if (
        refs.files
        or refs.urls
        or refs.mems
        or refs.commits
        or refs.resources
        or refs.extractions
    ):
        return True
    validity = item.validity
    return bool(
        validity.ttl_hours is not None
        or validity.cwd
        or validity.repo
        or validity.branch
        or validity.os
        or validity.adapter
    )


def _with_overview(item: MemoryItem, overview: str) -> MemoryItem:
    data = item.model_dump(mode="json")
    context_views = dict(data.get("context_views") or {})
    context_views["overview"] = overview
    data["context_views"] = context_views
    return MemoryItem.model_validate(data)


def _prepare_evidence_plan(
    item: MemoryItem,
    body: str,
    files: Sequence[_WriteEvidenceFile],
) -> tuple[_WriteEvidenceSpec, ...]:
    evidence = [_file_ref_spec(item, file) for file in files]
    extraction_count = len(item.refs.extractions) + sum(
        spec.extraction is not None for spec in evidence
    )
    if extraction_count == 0 and _should_capture_write_input(body):
        evidence.append(_write_input_spec(item, body))
    return tuple(evidence)


def _write_input_spec(item: MemoryItem, body: str) -> _WriteEvidenceSpec:
    content = body if body.strip() else f"{item.title}\n{item.summary}"
    resource = ResourceRecord(
        id="res-19700101-000000-write-input-template",
        kind=ResourceKind.document,
        uri=f"memory://items/{item.id}/write-input",
        title=f"Write input for {item.title}",
        mime_type="text/markdown",
        sha256=sha256_text(content),
        size_bytes=len(content.encode("utf-8")),
        project=item.project,
        tenant_id=item.tenant_id,
        tags=item.tags,
        sensitivity=item.sensitivity,
        created_at=item.created_at,
        metadata={
            "memory_item_id": item.id,
            "source_kind": getattr(item.source, "kind", None),
            "evidence_role": "write_input",
        },
    )
    extraction = ExtractionRecord(
        id="ext-19700101-000000-write-input-template",
        resource_id=resource.id,
        kind=ExtractionKind.text,
        extractor="amh.write-service.write-input",
        extractor_version="1",
        content_text=content,
        content_sha256=sha256_text(content),
        source_locator=f"memory://items/{item.id}/body",
        confidence=item.confidence,
        created_at=item.created_at,
        metadata={"memory_item_id": item.id, "evidence_role": "write_input"},
    )
    return _WriteEvidenceSpec(
        id_title=f"{item.title} write input",
        resource=resource,
        extraction=extraction,
    )


def _file_ref_spec(
    item: MemoryItem,
    file: _WriteEvidenceFile,
) -> _WriteEvidenceSpec:
    mime_type, _encoding = mimetypes.guess_type(str(file.path))
    resource = ResourceRecord(
        id="res-19700101-000000-file-ref-template",
        kind=_resource_kind_for_file(file.path, mime_type),
        uri=_file_uri(file.path),
        title=file.path.name,
        mime_type=mime_type,
        sha256=file.sha256,
        size_bytes=file.size_bytes,
        created_at=item.created_at,
        project=item.project,
        tenant_id=item.tenant_id,
        tags=item.tags,
        sensitivity=item.sensitivity,
        metadata={
            "memory_item_id": item.id,
            "ref_file": file.ref_file,
            "evidence_role": "ref_file",
        },
    )
    extraction = (
        ExtractionRecord(
            id="ext-19700101-000000-file-ref-template",
            resource_id=resource.id,
            kind=ExtractionKind.text,
            extractor="amh.write-service.file-ref",
            extractor_version="1",
            content_text=file.text,
            content_sha256=sha256_text(file.text),
            created_at=item.created_at,
            source_locator=_file_uri(file.path),
            confidence=item.confidence,
            metadata={"memory_item_id": item.id, "ref_file": file.ref_file},
        )
        if file.text is not None
        else None
    )
    return _WriteEvidenceSpec(
        id_title=file.path.name,
        resource=resource,
        extraction=extraction,
    )


def _materialize_evidence_spec(
    resource_store: ResourceStore,
    spec: _WriteEvidenceSpec,
) -> tuple[str, str | None]:
    resource = spec.resource.model_copy(
        update={
            "id": make_resource_id(spec.id_title),
            "created_at": datetime.now(timezone.utc),
        }
    )
    resource_store.write_resource(resource)
    if spec.extraction is None:
        return resource.id, None
    extraction = spec.extraction.model_copy(
        update={
            "id": make_extraction_id(spec.id_title),
            "resource_id": resource.id,
            "created_at": datetime.now(timezone.utc),
        }
    )
    resource_store.write_extraction(extraction)
    return resource.id, extraction.id


def _write_input_sidecar(
    *,
    resource_store: ResourceStore,
    item: MemoryItem,
    body: str,
) -> tuple[str, str]:
    resource_id, extraction_id = _materialize_evidence_spec(
        resource_store,
        _write_input_spec(item, body),
    )
    assert extraction_id is not None
    return resource_id, extraction_id


def _write_source_record(
    *,
    brain_dir: Path,
    item: MemoryItem,
    item_path: Path,
    body: str,
) -> Path:
    path = Path(brain_dir) / "sources" / "writes" / f"{item.id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    refs = item.refs.model_dump(mode="json")
    record = {
        "v": 1,
        "source_kind": "write_input",
        "writer": "WriteService",
        "item_id": item.id,
        "item_path": str(item_path),
        "memory_item_uri": f"memory://items/{item.id}/body",
        "title": item.title,
        "type": item.type,
        "summary": item.summary,
        "created_at": item.created_at.isoformat(),
        "agent": item.agent,
        "session": item.session,
        "project": item.project,
        "tenant_id": item.tenant_id,
        "sensitivity": item.sensitivity,
        "source": item.source.model_dump(mode="json"),
        "validity": item.validity.model_dump(mode="json"),
        "refs": refs,
        "body_sha256": sha256_text(body),
        "body_size_bytes": len(body.encode("utf-8")),
    }
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _write_file_ref_sidecar(
    *,
    resource_store: ResourceStore,
    item: MemoryItem,
    ref_file: str,
) -> tuple[str | None, str | None]:
    path = Path(ref_file).expanduser()
    if not path.exists() or not path.is_file():
        return None, None
    opened = path.stat()
    return _materialize_evidence_spec(
        resource_store,
        _file_ref_spec(
            item,
            _WriteEvidenceFile(
                ref_file=ref_file,
                path=path,
                sha256=sha256_file(path),
                size_bytes=opened.st_size,
                text=_read_text_file(path),
            ),
        ),
    )


def _matches_existing_write(
    item: MemoryItem,
    body: str,
    existing: MemoryItem,
    existing_body: str,
    *,
    files: Sequence[_WriteEvidenceFile],
    resources: Mapping[str, ResourceRecord],
    extractions: Mapping[str, ExtractionRecord],
    now: datetime,
) -> bool:
    if (
        body.rstrip() != existing_body.rstrip()
        or len(item.refs.files) > _MAX_WRITE_EVIDENCE_FILES
        or len(item.refs.resources) > _MAX_WRITE_EVIDENCE_REFS
        or len(item.refs.extractions) > _MAX_WRITE_EVIDENCE_REFS
        or len(existing.refs.resources) > _MAX_WRITE_EVIDENCE_REFS
        or len(existing.refs.extractions) > _MAX_WRITE_EVIDENCE_REFS
        or [file.ref_file for file in files] != item.refs.files
    ):
        return False
    expected, _warning = _mark_boundary_review_candidate(item)
    expected = enrich_memory_item(expected)
    specs = _prepare_evidence_plan(expected, body, files)
    if len(specs) + len(expected.refs.resources) > _MAX_WRITE_EVIDENCE_REFS:
        return False
    explicit_resources = (
        _dedupe(list(expected.refs.resources))
        if specs
        else list(expected.refs.resources)
    )
    explicit_extractions = (
        _dedupe(list(expected.refs.extractions))
        if specs
        else list(expected.refs.extractions)
    )
    generated_resource_ids = existing.refs.resources[len(explicit_resources) :]
    generated_extraction_ids = existing.refs.extractions[
        len(explicit_extractions) :
    ]
    if (
        existing.refs.resources[: len(explicit_resources)] != explicit_resources
        or existing.refs.extractions[: len(explicit_extractions)]
        != explicit_extractions
        or len(generated_resource_ids) != len(specs)
        or len(generated_extraction_ids)
        != sum(spec.extraction is not None for spec in specs)
        or existing.refs.resources
        != _dedupe([*expected.refs.resources, *generated_resource_ids])
        or existing.refs.extractions
        != _dedupe([*expected.refs.extractions, *generated_extraction_ids])
    ):
        return False

    last_created_at: datetime | None = None
    extraction_index = 0
    for spec, resource_id in zip(specs, generated_resource_ids, strict=True):
        resource = resources.get(resource_id)
        if (
            resource is None
            or not _matches_generated_evidence(
                resource,
                spec.resource,
                prefix="res",
                item_created_at=expected.created_at,
                now=now,
            )
            or (
                last_created_at is not None
                and resource.created_at < last_created_at
            )
        ):
            return False
        last_created_at = resource.created_at
        if spec.extraction is None:
            continue
        extraction_id = generated_extraction_ids[extraction_index]
        extraction_index += 1
        extraction = extractions.get(extraction_id)
        if (
            extraction is None
            or extraction.resource_id != resource.id
            or not _matches_generated_evidence(
                extraction,
                spec.extraction,
                prefix="ext",
                exclude={"resource_id"},
                item_created_at=expected.created_at,
                now=now,
            )
            or extraction.created_at < resource.created_at
        ):
            return False
        last_created_at = extraction.created_at

    expected = expected.model_copy(
        update={
            "refs": expected.refs.model_copy(
                update={
                    "resources": list(existing.refs.resources),
                    "extractions": list(existing.refs.extractions),
                }
            )
        }
    )
    return existing == expected


def _matches_generated_evidence(
    actual: ResourceRecord | ExtractionRecord,
    expected: ResourceRecord | ExtractionRecord,
    *,
    prefix: str,
    exclude: set[str] | None = None,
    item_created_at: datetime,
    now: datetime,
) -> bool:
    match = _GENERATED_EVIDENCE_ID_RE.match(actual.id)
    if match is None or match.group("prefix") != prefix:
        return False
    try:
        id_local = datetime.strptime(
            match.group("date") + match.group("time"),
            "%Y%m%d%H%M%S",
        )
    except ValueError:
        return False
    created_utc = actual.created_at.astimezone(timezone.utc)
    lower = item_created_at.astimezone(timezone.utc)
    upper = now.astimezone(timezone.utc) + timedelta(
        seconds=_GENERATED_EVIDENCE_CLOCK_SKEW_SECONDS
    )
    created_local = actual.created_at.astimezone().replace(tzinfo=None)
    ignored = {"id", "created_at", *(exclude or set())}
    return (
        lower <= created_utc <= upper
        and abs((created_local - id_local).total_seconds())
        <= _GENERATED_EVIDENCE_CLOCK_SKEW_SECONDS
        and actual.model_dump(
            mode="json",
            exclude_none=False,
            exclude=ignored,
        )
        == expected.model_dump(
            mode="json",
            exclude_none=False,
            exclude=ignored,
        )
    )


def _should_capture_write_input(body: str) -> bool:
    return not _MULTIMODAL_PLACEHOLDER_RE.search(body or "")


def _resource_kind_for_file(path: Path, mime_type: str | None) -> ResourceKind:
    suffix = path.suffix.lower()
    if suffix == ".pdf" or mime_type == "application/pdf":
        return ResourceKind.pdf
    if mime_type and mime_type.startswith("image/"):
        return ResourceKind.image
    if mime_type and mime_type.startswith("audio/"):
        return ResourceKind.audio
    if mime_type and mime_type.startswith("video/"):
        return ResourceKind.video
    if suffix in {".md", ".markdown", ".txt", ".rst", ".json", ".yaml", ".yml"}:
        return ResourceKind.document
    return ResourceKind.file


def _read_text_file(path: Path) -> str | None:
    try:
        if path.stat().st_size > _TEXT_EXTRACTION_MAX_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    except OSError:
        return None


def _file_uri(path: Path) -> str:
    try:
        return path.resolve(strict=False).as_uri()
    except ValueError:
        return str(path)


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            out.append(value)
            seen.add(value)
    return out
