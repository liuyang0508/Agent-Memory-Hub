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

import hashlib
import json
import logging
import mimetypes
import os
import stat
from collections.abc import Callable
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
    sha256_text,
    validate_extraction_id,
    validate_resource_id,
)
from agent_brain.platform.bounded_json import open_bounded_json_directory
from agent_brain.platform.secure_io import (
    close_descriptor,
    open_directory_path_without_symlinks,
    open_regular_file_at,
    secure_dir_fd_io_supported,
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
_MAX_WRITE_EVIDENCE_SYMLINK_BYTES = 4096
_GENERATED_EVIDENCE_CLOCK_SKEW_SECONDS = 5
_MAX_LEGACY_EVIDENCE_OFFSET_SECONDS = 14 * 60 * 60
_LEGACY_EVIDENCE_OFFSET_GRANULARITY_SECONDS = 15 * 60
_GENERATED_EVIDENCE_ID_RE = re.compile(
    r"^(?P<prefix>res|ext)-(?P<date>\d{8})-(?P<time>\d{6})-"
)


@dataclass(frozen=True)
class _WriteEvidenceFile:
    ref_file: str
    path: Path
    sha256: str | None = None
    size_bytes: int | None = None
    text: str | None = None


@dataclass(frozen=True)
class _WriteEvidenceSpec:
    id_title: str
    resource: ResourceRecord
    extraction: ExtractionRecord | None = None


class _WriteEvidenceBoundaryError(RuntimeError):
    pass


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
        evidence = _prepare_write_evidence(item, body)
        item = self._attach_evidence_sidecar(item, evidence)
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

    def _attach_evidence_sidecar(
        self,
        item: MemoryItem,
        evidence: tuple[_WriteEvidenceSpec, ...],
    ) -> MemoryItem:
        """Attach resource/extraction evidence produced by the write boundary.

        Explicit refs stay authoritative. Existing file refs are mirrored into
        ResourceStore sidecars when the local file is readable. If no extraction
        evidence exists after that, plain text write input is captured as a
        small evidence sidecar so fact/decision items do not become untraceable
        bare assertions. Multimodal placeholders are deliberately excluded:
        ``[Image #1]`` requires a real resource/extraction, not a text echo.
        """
        if not evidence:
            return item
        resource_store = ResourceStore(
            self._brain_dir or self._store.items_dir.parent
        )
        refs = item.refs.model_dump(mode="json")
        resources = list(refs.get("resources") or [])
        extractions = list(refs.get("extractions") or [])

        for spec in evidence:
            resource_id, extraction_id = _materialize_evidence_spec(
                resource_store,
                spec,
            )
            resources.append(resource_id)
            if extraction_id:
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


def _prepare_write_evidence(
    item: MemoryItem,
    body: str,
) -> tuple[_WriteEvidenceSpec, ...]:
    if len(item.refs.files) > _MAX_WRITE_EVIDENCE_FILES:
        raise _WriteEvidenceBoundaryError("WRITE_EVIDENCE_FILE_COUNT_EXCEEDED")
    if (
        len(item.refs.resources) > _MAX_WRITE_EVIDENCE_REFS
        or len(item.refs.extractions) > _MAX_WRITE_EVIDENCE_REFS
    ):
        raise _WriteEvidenceBoundaryError("WRITE_EVIDENCE_REF_COUNT_EXCEEDED")

    def is_safe_regular(opened: object) -> bool:
        attributes = int(getattr(opened, "st_file_attributes", 0) or 0)
        return stat.S_ISREG(int(getattr(opened, "st_mode", 0))) and not (
            attributes & 0x0400
        )

    files: list[_WriteEvidenceFile] = []
    for ref_file in item.refs.files:
        path = Path(ref_file).expanduser()
        try:
            before = os.lstat(path)
        except FileNotFoundError:
            files.append(_WriteEvidenceFile(ref_file=ref_file, path=path))
            continue
        except OSError as exc:
            raise _WriteEvidenceBoundaryError(
                "WRITE_EVIDENCE_FILE_UNSAFE"
            ) from exc

        if stat.S_ISLNK(before.st_mode):
            try:
                target = os.readlink(path)
                if (
                    not target
                    or len(os.fsencode(target)) > _MAX_WRITE_EVIDENCE_SYMLINK_BYTES
                ):
                    raise OSError
                target_path = Path(target)
                if not target_path.is_absolute():
                    target_path = path.parent / target_path
                try:
                    target_stat = os.lstat(target_path)
                except FileNotFoundError:
                    target_stat = None
                after = os.lstat(path)
                if not stat.S_ISLNK(after.st_mode) or not os.path.samestat(
                    before, after
                ):
                    raise OSError
            except OSError as exc:
                raise _WriteEvidenceBoundaryError(
                    "WRITE_EVIDENCE_FILE_UNSAFE"
                ) from exc
            if target_stat is None or (
                not stat.S_ISLNK(target_stat.st_mode)
                and not is_safe_regular(target_stat)
            ):
                files.append(_WriteEvidenceFile(ref_file=ref_file, path=path))
                continue
            raise _WriteEvidenceBoundaryError("WRITE_EVIDENCE_FILE_UNSAFE")

        if not is_safe_regular(before):
            files.append(_WriteEvidenceFile(ref_file=ref_file, path=path))
            continue
        if before.st_size > _MAX_WRITE_EVIDENCE_FILE_BYTES:
            raise _WriteEvidenceBoundaryError("WRITE_EVIDENCE_FILE_TOO_LARGE")

        directory_descriptor: int | None = None
        descriptor: int | None = None
        try:
            if secure_dir_fd_io_supported():
                directory_descriptor = open_directory_path_without_symlinks(
                    path.parent
                )
                descriptor = open_regular_file_at(
                    directory_descriptor,
                    path.name,
                )
            else:
                flags = (
                    os.O_RDONLY
                    | getattr(os, "O_BINARY", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NONBLOCK", 0)
                    | getattr(os, "O_NOFOLLOW", 0)
                )
                descriptor = os.open(path, flags)
            opened = os.fstat(descriptor)
            if (
                not is_safe_regular(opened)
                or not os.path.samestat(before, opened)
                or opened.st_size > _MAX_WRITE_EVIDENCE_FILE_BYTES
            ):
                raise OSError
            chunks: list[bytes] = []
            remaining = _MAX_WRITE_EVIDENCE_FILE_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            after = os.fstat(descriptor)
            path_after = os.lstat(path)
            if (
                len(content) > _MAX_WRITE_EVIDENCE_FILE_BYTES
                or not os.path.samestat(opened, after)
                or not os.path.samestat(after, path_after)
                or opened.st_size != after.st_size
                or opened.st_mtime_ns != after.st_mtime_ns
                or len(content) != after.st_size
            ):
                raise OSError
        except OSError as exc:
            raise _WriteEvidenceBoundaryError(
                "WRITE_EVIDENCE_FILE_UNSAFE"
            ) from exc
        finally:
            if descriptor is not None:
                close_descriptor(descriptor)
            if directory_descriptor is not None:
                close_descriptor(directory_descriptor)
        try:
            text = (
                content.decode("utf-8")
                if len(content) <= _TEXT_EXTRACTION_MAX_BYTES
                else None
            )
        except UnicodeDecodeError:
            text = None
        files.append(
            _WriteEvidenceFile(
                ref_file=ref_file,
                path=path,
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                text=text,
            )
        )

    evidence = [
        _file_ref_spec(item, file)
        for file in files
        if file.sha256 is not None and file.size_bytes is not None
    ]
    extraction_count = len(item.refs.extractions) + sum(
        spec.extraction is not None for spec in evidence
    )
    if extraction_count == 0 and _should_capture_write_input(body):
        evidence.append(_write_input_spec(item, body))
    generated_extractions = sum(spec.extraction is not None for spec in evidence)
    if (
        len(item.refs.resources) + len(evidence) > _MAX_WRITE_EVIDENCE_REFS
        or len(item.refs.extractions) + generated_extractions
        > _MAX_WRITE_EVIDENCE_REFS
    ):
        raise _WriteEvidenceBoundaryError("WRITE_EVIDENCE_REF_COUNT_EXCEEDED")
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
    if file.sha256 is None or file.size_bytes is None:
        raise ValueError("skipped file has no evidence spec")
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
    created_at = datetime.now(timezone.utc)
    resource = spec.resource.model_copy(
        update={
            "id": make_resource_id(spec.id_title, when=created_at),
            "created_at": created_at,
        }
    )
    resource_store.write_resource(resource)
    if spec.extraction is None:
        return resource.id, None
    extraction = spec.extraction.model_copy(
        update={
            "id": make_extraction_id(spec.id_title, when=created_at),
            "resource_id": resource.id,
            "created_at": created_at,
        }
    )
    resource_store.write_extraction(extraction)
    return resource.id, extraction.id


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


def _matches_existing_write(
    item: MemoryItem,
    body: str,
    existing: MemoryItem,
    existing_body: str,
    *,
    brain: Path,
    now: datetime,
) -> bool:
    if (
        body.rstrip() != existing_body.rstrip()
        or len(existing.refs.resources) > _MAX_WRITE_EVIDENCE_REFS
        or len(existing.refs.extractions) > _MAX_WRITE_EVIDENCE_REFS
    ):
        return False
    expected, _warning = _mark_boundary_review_candidate(item)
    expected = enrich_memory_item(expected)
    try:
        specs = _prepare_write_evidence(expected, body)
    except _WriteEvidenceBoundaryError:
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

    resources: dict[str, ResourceRecord] = {}
    if generated_resource_ids:
        with open_bounded_json_directory(brain / "resources") as reader:
            if reader is None:
                return False
            try:
                for resource_id in generated_resource_ids:
                    validate_resource_id(resource_id)
                    payload = reader.read_object(
                        f"{resource_id}.json",
                        max_bytes=_MAX_WRITE_EVIDENCE_RECORD_BYTES,
                    )
                    if payload is None:
                        return False
                    resources[resource_id] = ResourceRecord.model_validate(payload)
            except (TypeError, ValueError):
                return False
    extractions: dict[str, ExtractionRecord] = {}
    if generated_extraction_ids:
        with open_bounded_json_directory(brain / "extractions") as reader:
            if reader is None:
                return False
            try:
                for extraction_id in generated_extraction_ids:
                    validate_extraction_id(extraction_id)
                    payload = reader.read_object(
                        f"{extraction_id}.json",
                        max_bytes=_MAX_WRITE_EVIDENCE_RECORD_BYTES,
                    )
                    if payload is None:
                        return False
                    extractions[extraction_id] = ExtractionRecord.model_validate(
                        payload
                    )
            except (TypeError, ValueError):
                return False

    last_created_at: datetime | None = None
    generated_offset: int | None = None
    extraction_index = 0
    for spec, resource_id in zip(specs, generated_resource_ids, strict=True):
        resource = resources.get(resource_id)
        if resource is None:
            return False
        resource_offset = _generated_evidence_offset(
            resource,
            spec.resource,
            prefix="res",
            item_created_at=expected.created_at,
            now=now,
        )
        if (
            resource_offset is None
            or (
                generated_offset is not None
                and resource_offset != generated_offset
            )
            or (
                last_created_at is not None
                and resource.created_at < last_created_at
            )
        ):
            return False
        generated_offset = resource_offset
        last_created_at = resource.created_at
        if spec.extraction is None:
            continue
        extraction_id = generated_extraction_ids[extraction_index]
        extraction_index += 1
        extraction = extractions.get(extraction_id)
        if extraction is None:
            return False
        extraction_offset = _generated_evidence_offset(
            extraction,
            spec.extraction,
            prefix="ext",
            exclude={"resource_id"},
            item_created_at=expected.created_at,
            now=now,
        )
        if (
            extraction.resource_id != resource.id
            or extraction_offset is None
            or extraction_offset != generated_offset
            or extraction.created_at < resource.created_at
        ):
            return False
        generated_offset = extraction_offset
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


def _generated_evidence_offset(
    actual: ResourceRecord | ExtractionRecord,
    expected: ResourceRecord | ExtractionRecord,
    *,
    prefix: str,
    exclude: set[str] | None = None,
    item_created_at: datetime,
    now: datetime,
) -> int | None:
    match = _GENERATED_EVIDENCE_ID_RE.match(actual.id)
    if match is None or match.group("prefix") != prefix:
        return None
    try:
        id_local = datetime.strptime(
            match.group("date") + match.group("time"),
            "%Y%m%d%H%M%S",
        )
    except ValueError:
        return None
    created_utc = actual.created_at.astimezone(timezone.utc)
    lower = item_created_at.astimezone(timezone.utc)
    upper = now.astimezone(timezone.utc) + timedelta(
        seconds=_GENERATED_EVIDENCE_CLOCK_SKEW_SECONDS
    )
    created_wall_utc = created_utc.replace(tzinfo=None)
    raw_offset = (id_local - created_wall_utc).total_seconds()
    offset = round(
        raw_offset / _LEGACY_EVIDENCE_OFFSET_GRANULARITY_SECONDS
    ) * _LEGACY_EVIDENCE_OFFSET_GRANULARITY_SECONDS
    ignored = {"id", "created_at", *(exclude or set())}
    if (
        not lower <= created_utc <= upper
        or abs(offset) > _MAX_LEGACY_EVIDENCE_OFFSET_SECONDS
        or abs(raw_offset - offset) > _GENERATED_EVIDENCE_CLOCK_SKEW_SECONDS
        or actual.model_dump(
            mode="json",
            exclude_none=False,
            exclude=ignored,
        )
        != expected.model_dump(
            mode="json",
            exclude_none=False,
            exclude=ignored,
        )
    ):
        return None
    return offset


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
