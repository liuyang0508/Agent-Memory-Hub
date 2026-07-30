"""Digest-bound, content-free evidence plans for active review candidates."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.contracts.resource import (
    ExtractionRecord,
    ResourceRecord,
    validate_extraction_id,
    validate_resource_id,
)
from agent_brain.memory.governance.contradiction_cases import ContradictionCase
from agent_brain.memory.governance.review_queue import (
    list_review_candidates,
    list_review_candidates_from_items,
)
from agent_brain.memory.store.item_markdown import parse_item_markdown
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.secure_io import (
    close_descriptor,
    open_child_directory,
    open_directory_path_without_symlinks,
    open_regular_file_at,
)


REVIEW_EVIDENCE_SCHEMA_VERSION = "amh-review-evidence-plan/v1"
_MAX_EVIDENCE_FILE_BYTES = 64 * 1024 * 1024
_COMMIT_REF = re.compile(r"[0-9a-fA-F]{7,64}\Z")
EvidenceAvailability = Literal[
    "verified",
    "referenced",
    "missing",
    "invalid",
    "unavailable",
    "boundary_mismatch",
]
ContestationRoute = Literal[
    "not_contested",
    "contradiction_case",
    "contested_unpaired",
    "routing_unavailable",
]


@dataclass(frozen=True)
class ReviewEvidenceSource:
    """One source reference without source content."""

    kind: str
    ordinal: int
    availability: EvidenceAvailability
    boundary_status: str
    reference_sha256: str
    content_digest: str | None
    digest_algorithm: str | None
    evidence_role: str
    supports_truth: bool
    locator: str
    reason: str
    independence_key: str

    def to_dict(self, *, include_locator: bool) -> dict[str, object]:
        data = asdict(self)
        data.pop("independence_key", None)
        if not include_locator:
            data.pop("locator", None)
        return data


@dataclass(frozen=True)
class ReviewEvidenceItem:
    item_id: str
    item_sha256: str
    review_reason: str
    evidence_status: str
    explicit_source_count: int
    provenance_source_count: int
    verified_source_count: int
    supporting_verified_source_count: int
    independent_verified_source_count: int
    unavailable_source_count: int
    boundary_mismatch_count: int
    contestation_route: ContestationRoute
    contradiction_case_ids: tuple[str, ...]
    recommended_action: str
    sources: tuple[ReviewEvidenceSource, ...]

    def to_dict(self, *, include_locators: bool) -> dict[str, object]:
        data = asdict(self)
        data["contradiction_case_ids"] = list(self.contradiction_case_ids)
        data["sources"] = [
            source.to_dict(include_locator=include_locators)
            for source in self.sources
        ]
        return data


@dataclass(frozen=True)
class ReviewEvidencePlan:
    schema_version: str
    generated_at: str
    status: str
    routing_status: str
    total: int
    evidence_available_count: int
    source_gap_count: int
    provenance_recoverable_count: int
    unresolved_source_gap_count: int
    traceability_only_count: int
    evidence_unavailable_count: int
    boundary_blocked_count: int
    contested_count: int
    contested_case_count: int
    contested_unpaired_count: int
    item_scan_unavailable_count: int
    mutates_memory: bool
    mutates_confidence: bool
    items: tuple[ReviewEvidenceItem, ...]

    def to_dict(self, *, include_locators: bool = True) -> dict[str, object]:
        data = asdict(self)
        data["items"] = [
            item.to_dict(include_locators=include_locators)
            for item in self.items
        ]
        return data


def build_review_evidence_plan(
    brain_dir: Path,
    *,
    store: ItemsStore | None = None,
    item_ids: Iterable[str] | None = None,
    limit: int = 100,
    now: datetime | None = None,
    contradiction_cases: Iterable[ContradictionCase] | None = None,
    routing_status: str | None = None,
) -> ReviewEvidencePlan:
    """Inspect review evidence without fetching URLs or mutating durable state."""

    root = Path(brain_dir)
    items_store = store or ItemsStore(root / "items")
    queue = list_review_candidates(items_store)
    selected_ids = None if item_ids is None else frozenset(item_ids)
    candidates = [
        candidate
        for candidate in queue.candidates
        if selected_ids is None or candidate.id in selected_ids
    ][: max(0, min(int(limit), 500))]

    cases: tuple[ContradictionCase, ...]
    case_status = routing_status or "healthy"
    if contradiction_cases is not None:
        cases = tuple(contradiction_cases)
    else:
        try:
            from agent_brain.memory.governance.contradiction_resolution import (
                build_contradiction_case_inventory,
            )

            inventory = build_contradiction_case_inventory(
                brain_dir=root,
                store=items_store,
            )
            case_status = inventory.status
            cases = tuple(
                ContradictionCase(
                    case_id=view.case_id,
                    item_ids=view.item_ids,
                    pair_count=view.pair_count,
                    confidence=view.confidence,
                    evidence=view.evidence,
                )
                for view in inventory.cases
            )
        except (OSError, TypeError, ValueError):
            case_status = "unavailable"
            cases = ()

    cases_by_item: dict[str, list[str]] = {}
    for case in cases:
        for item_id in case.item_ids:
            cases_by_item.setdefault(item_id, []).append(case.case_id)

    rows: list[ReviewEvidenceItem] = []
    item_scan_unavailable_count = 0
    for candidate in candidates:
        try:
            raw_item = items_store.read_bytes_nofollow(candidate.id)
            item, _body = parse_item_markdown(
                raw_item.decode("utf-8-sig")
                .replace("\r\n", "\n")
                .replace("\r", "\n")
            )
            current_queue = list_review_candidates_from_items((item,))
        except (OSError, UnicodeError, ValueError):
            item_scan_unavailable_count += 1
            continue
        if not current_queue.candidates:
            item_scan_unavailable_count += 1
            continue
        current_candidate = current_queue.candidates[0]
        item_sha256 = _sha256(raw_item)
        sources = _resolve_sources(root, items_store, item)
        rows.append(
            _build_item_plan(
                item,
                item_sha256=item_sha256,
                review_reason=current_candidate.review_reason,
                sources=sources,
                case_ids=tuple(sorted(cases_by_item.get(item.id, ()))),
                routing_status=case_status,
            )
        )

    evidence_counts = Counter(row.evidence_status for row in rows)
    source_gap_rows = [row for row in rows if row.review_reason == "source_gap"]
    contested_rows = [
        row for row in rows if row.contestation_route != "not_contested"
    ]
    status = "pass"
    if case_status != "healthy" or item_scan_unavailable_count or any(
        row.evidence_status == "boundary_blocked" for row in rows
    ):
        status = "fail"
    elif any(
        row.evidence_status in {
            "source_gap",
            "traceability_only",
            "evidence_unavailable",
        }
        or row.review_reason == "source_gap"
        or row.contestation_route == "contested_unpaired"
        for row in rows
    ):
        status = "warn"
    generated = now or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    return ReviewEvidencePlan(
        schema_version=REVIEW_EVIDENCE_SCHEMA_VERSION,
        generated_at=generated.astimezone(timezone.utc).isoformat(),
        status=status,
        routing_status=case_status,
        total=len(candidates),
        evidence_available_count=evidence_counts["evidence_available"],
        source_gap_count=len(source_gap_rows),
        provenance_recoverable_count=sum(
            row.evidence_status == "provenance_available"
            for row in source_gap_rows
        ),
        unresolved_source_gap_count=sum(
            row.evidence_status != "provenance_available"
            for row in source_gap_rows
        ),
        traceability_only_count=evidence_counts["traceability_only"],
        evidence_unavailable_count=evidence_counts["evidence_unavailable"],
        boundary_blocked_count=evidence_counts["boundary_blocked"],
        contested_count=len(contested_rows),
        contested_case_count=sum(
            row.contestation_route == "contradiction_case"
            for row in contested_rows
        ),
        contested_unpaired_count=sum(
            row.contestation_route == "contested_unpaired"
            for row in contested_rows
        ),
        item_scan_unavailable_count=item_scan_unavailable_count,
        mutates_memory=False,
        mutates_confidence=False,
        items=tuple(rows),
    )


def _build_item_plan(
    item: MemoryItem,
    *,
    item_sha256: str,
    review_reason: str,
    sources: tuple[ReviewEvidenceSource, ...],
    case_ids: tuple[str, ...],
    routing_status: str,
) -> ReviewEvidenceItem:
    verified = [
        source for source in sources if source.availability == "verified"
    ]
    supporting_verified = [
        source
        for source in verified
        if source.supports_truth
    ]
    boundary_mismatches = [
        source
        for source in sources
        if source.availability == "boundary_mismatch"
    ]
    unavailable = [
        source
        for source in sources
        if source.availability in {"missing", "invalid", "unavailable"}
    ]
    if boundary_mismatches and not supporting_verified:
        evidence_status = "boundary_blocked"
    elif supporting_verified:
        evidence_status = (
            "provenance_available"
            if review_reason == "source_gap"
            else "evidence_available"
        )
    elif verified:
        evidence_status = "traceability_only"
    elif not sources:
        evidence_status = "source_gap"
    else:
        evidence_status = "evidence_unavailable"

    contested = (
        review_reason == "contested"
        or "contested" in {tag.casefold() for tag in item.tags}
    )
    if not contested:
        route: ContestationRoute = "not_contested"
    elif routing_status != "healthy":
        route = "routing_unavailable"
    elif case_ids:
        route = "contradiction_case"
    else:
        route = "contested_unpaired"

    if route == "contradiction_case":
        recommended = "inspect_or_adjudicate_contradiction_case"
    elif route == "contested_unpaired":
        recommended = "find_counterpart_or_dismiss_contestation"
    elif route == "routing_unavailable":
        recommended = "repair_contestation_routing"
    elif evidence_status == "provenance_available":
        recommended = "inspect_provenance_then_attach_source_or_reject"
    elif evidence_status == "evidence_available":
        recommended = "inspect_evidence_then_approve_or_reject"
    elif evidence_status == "traceability_only":
        recommended = "attach_independent_source_or_reject"
    elif evidence_status == "boundary_blocked":
        recommended = "repair_scope_boundary_or_reject"
    elif any(source.kind in {"resource", "extraction"} for source in sources):
        recommended = "repair_provenance_then_attach_or_reject"
    else:
        recommended = "attach_source_or_reject"

    return ReviewEvidenceItem(
        item_id=item.id,
        item_sha256=item_sha256,
        review_reason=review_reason,
        evidence_status=evidence_status,
        explicit_source_count=(
            len(item.refs.files)
            + len(item.refs.urls)
            + len(item.refs.mems)
            + len(item.refs.commits)
        ),
        provenance_source_count=(
            len(item.refs.resources) + len(item.refs.extractions)
        ),
        verified_source_count=len(verified),
        supporting_verified_source_count=len(supporting_verified),
        independent_verified_source_count=len(
            {source.independence_key for source in supporting_verified}
        ),
        unavailable_source_count=len(unavailable),
        boundary_mismatch_count=len(boundary_mismatches),
        contestation_route=route,
        contradiction_case_ids=case_ids,
        recommended_action=recommended,
        sources=sources,
    )


def _resolve_sources(
    brain_dir: Path,
    store: ItemsStore,
    item: MemoryItem,
) -> tuple[ReviewEvidenceSource, ...]:
    rows: list[ReviewEvidenceSource] = []
    ordinal = 0
    for locator in item.refs.files:
        rows.append(_resolve_file(item, locator, ordinal))
        ordinal += 1
    for locator in item.refs.urls:
        rows.append(_resolve_url(locator, ordinal))
        ordinal += 1
    for locator in item.refs.mems:
        rows.append(_resolve_memory(store, item, locator, ordinal))
        ordinal += 1
    for locator in item.refs.commits:
        rows.append(_resolve_commit(item, locator, ordinal))
        ordinal += 1
    resource_records: dict[str, ResourceRecord] = {}
    for locator in item.refs.resources:
        row, record = _resolve_resource(brain_dir, item, locator, ordinal)
        rows.append(row)
        if record is not None:
            resource_records[record.id] = record
        ordinal += 1
    for locator in item.refs.extractions:
        rows.append(
            _resolve_extraction(
                brain_dir,
                item,
                locator,
                ordinal,
                resource_records=resource_records,
            )
        )
        ordinal += 1
    if item.source.transcript_id:
        rows.append(
            _source(
                "conversation",
                ordinal,
                item.source.transcript_id,
                availability="referenced",
                reason="TRANSCRIPT_REFERENCE_NOT_CONTENT_BOUND",
                evidence_role="conversation_reference",
            )
        )
    return tuple(rows)


def _resolve_url(locator: str, ordinal: int) -> ReviewEvidenceSource:
    if (
        not _bounded_locator(locator)
        or (parsed := urlsplit(locator)).scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        return _source(
            "url",
            ordinal,
            locator,
            availability="invalid",
            reason="INVALID_HTTPS_SOURCE",
            evidence_role="explicit_url",
        )
    return _source(
        "url",
        ordinal,
        locator,
        availability="referenced",
        reason="NETWORK_FETCH_DISABLED",
        evidence_role="explicit_url",
    )


def _resolve_file(
    item: MemoryItem,
    locator: str,
    ordinal: int,
) -> ReviewEvidenceSource:
    path = Path(locator).expanduser()
    if not path.is_absolute():
        base = item.validity.repo or item.validity.cwd
        if not base:
            return _source(
                "file",
                ordinal,
                locator,
                availability="unavailable",
                reason="RELATIVE_FILE_WITHOUT_SCOPE_ROOT",
            )
        path = Path(base).expanduser() / path
    try:
        digest = _digest_regular_file(path)
    except FileNotFoundError:
        return _source(
            "file",
            ordinal,
            locator,
            availability="missing",
            reason="FILE_NOT_FOUND",
        )
    except (OSError, ValueError):
        return _source(
            "file",
            ordinal,
            locator,
            availability="unavailable",
            reason="FILE_UNSAFE_OR_UNREADABLE",
        )
    return _source(
        "file",
        ordinal,
        locator,
        availability="verified",
        content_digest=digest,
        digest_algorithm="sha256",
        reason="CONTENT_DIGESTED",
        evidence_role="explicit_file",
        supports_truth=True,
        independence_key=f"sha256:{digest}",
    )


def _resolve_memory(
    store: ItemsStore,
    item: MemoryItem,
    locator: str,
    ordinal: int,
) -> ReviewEvidenceSource:
    try:
        referenced, _body = store.get_nofollow(locator)
        data = store.read_bytes_nofollow(locator)
    except FileNotFoundError:
        return _source(
            "memory",
            ordinal,
            locator,
            availability="missing",
            reason="MEMORY_NOT_FOUND",
        )
    except (OSError, UnicodeError, ValueError):
        return _source(
            "memory",
            ordinal,
            locator,
            availability="invalid",
            reason="MEMORY_INVALID_OR_UNREADABLE",
        )
    boundary = _boundary_status(item, referenced.project, referenced.tenant_id)
    if boundary == "mismatch":
        return _source(
            "memory",
            ordinal,
            locator,
            availability="boundary_mismatch",
            boundary_status=boundary,
            reason="MEMORY_SCOPE_MISMATCH",
        )
    digest = _sha256(data)
    return _source(
        "memory",
        ordinal,
        locator,
        availability="verified",
        boundary_status=boundary,
        content_digest=digest,
        digest_algorithm="sha256",
        reason="CONTENT_DIGESTED",
        evidence_role="explicit_memory",
        supports_truth=True,
        independence_key=f"memory:{locator}",
    )


def _resolve_commit(
    item: MemoryItem,
    locator: str,
    ordinal: int,
) -> ReviewEvidenceSource:
    repo = item.validity.repo or item.validity.cwd
    if not repo or _COMMIT_REF.fullmatch(locator) is None:
        return _source(
            "commit",
            ordinal,
            locator,
            availability="unavailable" if repo else "referenced",
            reason="COMMIT_REPO_UNAVAILABLE" if not repo else "INVALID_COMMIT_REF",
        )
    git = shutil.which("git")
    repo_path = Path(repo).expanduser()
    if git is None or not repo_path.is_dir():
        return _source(
            "commit",
            ordinal,
            locator,
            availability="unavailable",
            reason="GIT_REPOSITORY_UNAVAILABLE",
        )
    environment = {
        "PATH": str(Path(git).parent),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    }
    try:
        result = subprocess.run(
            [
                git,
                "-C",
                str(repo_path),
                "-c",
                "core.hooksPath=/dev/null",
                "rev-parse",
                "--verify",
                f"{locator}^{{commit}}",
            ],
            check=False,
            capture_output=True,
            env=environment,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return _source(
            "commit",
            ordinal,
            locator,
            availability="unavailable",
            reason="GIT_LOOKUP_FAILED",
        )
    resolved = result.stdout.decode("ascii", "ignore").strip().lower()
    if result.returncode != 0 or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", resolved) is None:
        return _source(
            "commit",
            ordinal,
            locator,
            availability="missing",
            reason="COMMIT_NOT_FOUND",
        )
    return _source(
        "commit",
        ordinal,
        locator,
        availability="verified",
        content_digest=resolved,
        digest_algorithm="git-object-id",
        reason="COMMIT_RESOLVED",
        evidence_role="explicit_commit",
        supports_truth=True,
        independence_key=f"commit:{resolved}",
    )


def _resolve_resource(
    brain_dir: Path,
    item: MemoryItem,
    locator: str,
    ordinal: int,
) -> tuple[ReviewEvidenceSource, ResourceRecord | None]:
    try:
        validate_resource_id(locator)
        raw = _read_regular_bytes(
            brain_dir / "resources" / f"{locator}.json",
            max_bytes=8 * 1024 * 1024,
            secure_root=brain_dir,
        )
        record = ResourceRecord.model_validate(json.loads(raw.decode("utf-8")))
    except FileNotFoundError:
        return (
            _source(
                "resource",
                ordinal,
                locator,
                availability="missing",
                reason="RESOURCE_NOT_FOUND",
            ),
            None,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return (
            _source(
                "resource",
                ordinal,
                locator,
                availability="invalid",
                reason="RESOURCE_INVALID_OR_UNREADABLE",
            ),
            None,
        )
    boundary = _boundary_status(item, record.project, record.tenant_id)
    evidence_role = _resource_evidence_role(record)
    supports_truth = _resource_supports_truth(item, record)
    if boundary == "mismatch":
        return (
            _source(
                "resource",
                ordinal,
                locator,
                availability="boundary_mismatch",
                boundary_status=boundary,
                reason="RESOURCE_SCOPE_MISMATCH",
                evidence_role=evidence_role,
            ),
            record,
        )
    digest = _sha256(raw)
    independence_key = (
        f"sha256:{record.sha256}"
        if record.sha256
        else f"resource:{record.id}"
    )
    return (
        _source(
            "resource",
            ordinal,
            locator,
            availability="verified",
            boundary_status=boundary,
            content_digest=digest,
            digest_algorithm="sha256",
            reason="SIDECAR_DIGESTED",
            evidence_role=evidence_role,
            supports_truth=supports_truth,
            independence_key=independence_key,
        ),
        record,
    )


def _resolve_extraction(
    brain_dir: Path,
    item: MemoryItem,
    locator: str,
    ordinal: int,
    *,
    resource_records: dict[str, ResourceRecord],
) -> ReviewEvidenceSource:
    try:
        validate_extraction_id(locator)
        raw = _read_regular_bytes(
            brain_dir / "extractions" / f"{locator}.json",
            max_bytes=16 * 1024 * 1024,
            secure_root=brain_dir,
        )
        record = ExtractionRecord.model_validate(json.loads(raw.decode("utf-8")))
    except FileNotFoundError:
        return _source(
            "extraction",
            ordinal,
            locator,
            availability="missing",
            reason="EXTRACTION_NOT_FOUND",
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return _source(
            "extraction",
            ordinal,
            locator,
            availability="invalid",
            reason="EXTRACTION_INVALID_OR_UNREADABLE",
        )
    resource = resource_records.get(record.resource_id)
    evidence_role = (
        _resource_evidence_role(resource)
        if resource is not None
        else "extraction"
    )
    supports_truth = (
        _resource_supports_truth(item, resource)
        if resource is not None
        else False
    )
    boundary = (
        _boundary_status(item, resource.project, resource.tenant_id)
        if resource is not None
        else "unknown"
    )
    if boundary == "mismatch":
        return _source(
            "extraction",
            ordinal,
            locator,
            availability="boundary_mismatch",
            boundary_status=boundary,
            reason="EXTRACTION_RESOURCE_SCOPE_MISMATCH",
            evidence_role=evidence_role,
        )
    independence_key = (
        f"sha256:{resource.sha256}"
        if resource is not None and resource.sha256
        else f"resource:{record.resource_id}"
    )
    return _source(
        "extraction",
        ordinal,
        locator,
        availability="verified",
        boundary_status=boundary,
        content_digest=record.content_sha256,
        digest_algorithm="sha256",
        reason="CONTENT_DIGEST_VALIDATED",
        evidence_role=evidence_role,
        supports_truth=supports_truth,
        independence_key=independence_key,
    )


def _source(
    kind: str,
    ordinal: int,
    locator: str,
    *,
    availability: EvidenceAvailability,
    boundary_status: str = "unknown",
    content_digest: str | None = None,
    digest_algorithm: str | None = None,
    evidence_role: str = "reference",
    supports_truth: bool = False,
    reason: str,
    independence_key: str | None = None,
) -> ReviewEvidenceSource:
    normalized = str(locator)
    reference_sha256 = _sha256(normalized.encode("utf-8"))
    return ReviewEvidenceSource(
        kind=kind,
        ordinal=ordinal,
        availability=availability,
        boundary_status=boundary_status,
        reference_sha256=reference_sha256,
        content_digest=content_digest,
        digest_algorithm=digest_algorithm,
        evidence_role=evidence_role,
        supports_truth=supports_truth,
        locator=normalized,
        reason=reason,
        independence_key=independence_key or f"reference:{reference_sha256}",
    )


def _boundary_status(
    item: MemoryItem,
    project: str | None,
    tenant_id: str | None,
) -> str:
    if item.tenant_id is not None and tenant_id != item.tenant_id:
        return "mismatch"
    if item.project is not None and project not in {None, item.project}:
        return "mismatch"
    if item.tenant_id is None and tenant_id is not None:
        return "mismatch"
    if item.project is None and project is not None:
        return "compatible"
    return "compatible" if project is not None or tenant_id is not None else "unknown"


def _resource_evidence_role(record: ResourceRecord) -> str:
    value = record.metadata.get("evidence_role")
    if not isinstance(value, str):
        return "resource"
    normalized = value.strip().casefold()
    return normalized[:64] if normalized else "resource"


def _resource_supports_truth(
    item: MemoryItem,
    record: ResourceRecord,
) -> bool:
    role = _resource_evidence_role(record)
    if role in {"write_input", "self_assertion", "generated_summary"}:
        return False
    owner = record.metadata.get("memory_item_id")
    if owner == item.id and record.uri.endswith("/write-input"):
        return False
    return True


def _digest_regular_file(path: Path) -> str:
    return _sha256(
        _read_regular_bytes(path, max_bytes=_MAX_EVIDENCE_FILE_BYTES)
    )


def _read_regular_bytes(
    path: Path,
    *,
    max_bytes: int,
    secure_root: Path | None = None,
) -> bytes:
    if secure_root is not None:
        root_descriptor = open_directory_path_without_symlinks(secure_root)
        child_descriptor = -1
        descriptor = -1
        try:
            child_descriptor = open_child_directory(
                root_descriptor,
                path.parent.name,
            )
            descriptor = open_regular_file_at(child_descriptor, path.name)
            return _read_open_regular_bytes(descriptor, max_bytes=max_bytes)
        finally:
            if descriptor >= 0:
                close_descriptor(descriptor)
            if child_descriptor >= 0:
                close_descriptor(child_descriptor)
            close_descriptor(root_descriptor)
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
        raise OSError("UNSAFE_OR_OVERSIZED_EVIDENCE")
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or opened.st_size > max_bytes
        ):
            raise OSError("EVIDENCE_CHANGED_BEFORE_OPEN")
        data = _read_open_regular_bytes(descriptor, max_bytes=max_bytes)
        after = os.fstat(descriptor)
        if (
            len(data) != after.st_size
            or (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
                opened.st_mtime_ns,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            )
        ):
            raise OSError("EVIDENCE_CHANGED_DURING_READ")
        return data
    finally:
        os.close(descriptor)


def _read_open_regular_bytes(descriptor: int, *, max_bytes: int) -> bytes:
    opened = os.fstat(descriptor)
    if not stat.S_ISREG(opened.st_mode) or opened.st_size > max_bytes:
        raise OSError("UNSAFE_OR_OVERSIZED_EVIDENCE")
    chunks: list[bytes] = []
    remaining = max_bytes + 1
    while remaining > 0:
        chunk = os.read(descriptor, min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    after = os.fstat(descriptor)
    if (
        len(data) > max_bytes
        or len(data) != after.st_size
        or (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        )
        != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
    ):
        raise OSError("EVIDENCE_CHANGED_DURING_READ")
    return data


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bounded_locator(value: str) -> bool:
    return (
        bool(value.strip())
        and len(value.encode("utf-8")) <= 2048
        and not any(ord(character) < 32 for character in value)
    )


__all__ = [
    "REVIEW_EVIDENCE_SCHEMA_VERSION",
    "ReviewEvidenceItem",
    "ReviewEvidencePlan",
    "ReviewEvidenceSource",
    "build_review_evidence_plan",
]
