from __future__ import annotations

import re
from dataclasses import dataclass

from agent_brain.memory.evidence.resource_store import ResourceStore
from agent_brain.memory.recall.retrieval_budget import estimate_tokens
from agent_brain.contracts.resource import ExtractionKind, ExtractionRecord, ResourceKind, ResourceRecord


PROMPT_RESOURCE_SENSITIVITIES = ("public", "internal")


def resource_visible_for_prompt(
    resource: ResourceRecord,
    *,
    tenant_id: str | None,
    is_admin: bool = False,
) -> bool:
    """Return whether a resource may be added to generated prompt context."""
    sensitivity = str(
        getattr(resource.sensitivity, "value", resource.sensitivity)
    )
    if sensitivity not in PROMPT_RESOURCE_SENSITIVITIES:
        return False
    return bool(
        is_admin
        or resource.tenant_id is None
        or resource.tenant_id == tenant_id
    )


@dataclass(frozen=True)
class ResourceSearchFilter:
    project: str | None = None
    tags: list[str] | None = None
    kind: ResourceKind | str | None = None
    tenant_ids: tuple[str | None, ...] | None = None
    allowed_sensitivities: tuple[str, ...] | None = None


@dataclass(frozen=True)
class ResourceHit:
    resource: ResourceRecord
    score: float
    matched_extractions: list[str]


@dataclass(frozen=True)
class ResourceReadResult:
    status: str
    resource_id: str
    level: str
    content_text: str
    extraction_id: str | None = None
    source_locator: str | None = None
    confidence: float | None = None


def _tokens(text: str) -> list[str]:
    return re.findall(r"[\w\u4e00-\u9fff]+", text.lower())


def _kind_value(kind: ResourceKind | str | None) -> str | None:
    if kind is None:
        return None
    return kind.value if isinstance(kind, ResourceKind) else str(kind)


class ResourceReader:
    """Progressive read helpers over ResourceStore sidecar evidence."""

    def __init__(self, store: ResourceStore) -> None:
        self.store = store

    def search_resource(
        self,
        query: str,
        *,
        top_k: int = 10,
        filters: ResourceSearchFilter | None = None,
    ) -> list[ResourceHit]:
        query_tokens = _tokens(query)
        if not query_tokens:
            return []

        hits: list[ResourceHit] = []
        for resource in self.store.iter_resources():
            if not self._matches_filters(resource, filters):
                continue
            extractions = list(self.store.iter_extractions(resource_id=resource.id))
            score, matched = self._score_resource(resource, extractions, query_tokens)
            if score > 0:
                hits.append(ResourceHit(resource=resource, score=score, matched_extractions=matched))

        hits.sort(key=lambda hit: (-hit.score, hit.resource.title.lower(), hit.resource.id))
        return hits[:top_k]

    def read_resource_summary(self, resource_id: str) -> ResourceReadResult:
        return self._read_best(resource_id, ExtractionKind.summary, "summary")

    def read_resource_outline(self, resource_id: str) -> ResourceReadResult:
        return self._read_best(resource_id, ExtractionKind.outline, "outline")

    def read_resource_segment(self, resource_id: str, *, locator: str | None = None) -> ResourceReadResult:
        segments = self._extractions(resource_id, ExtractionKind.segment)
        if locator is not None:
            segments = [ext for ext in segments if ext.source_locator == locator]
        if not segments:
            suffix = f" at {locator}" if locator else ""
            return self._degraded(resource_id, "segment", f"No segment extraction{suffix}.")
        return self._result(resource_id, "segment", self._best_extraction(segments))

    def read_resource_exact(
        self, resource_id: str, *, locator: str | None = None
    ) -> ResourceReadResult:
        exact = self._extractions(resource_id, ExtractionKind.text)
        if locator is not None:
            exact = [ext for ext in exact if ext.source_locator == locator]
        if not exact:
            suffix = f" at {locator}" if locator else ""
            return self._degraded(resource_id, "exact", f"No exact text extraction{suffix}.")
        return self._result(resource_id, "exact", self._best_extraction(exact))

    def read_resource_context(self, resource_id: str, *, max_tokens: int) -> list[ResourceReadResult]:
        candidates: list[ResourceReadResult] = [
            self.read_resource_summary(resource_id),
            self.read_resource_outline(resource_id),
        ]
        for segment in self._extractions(resource_id, ExtractionKind.segment):
            candidates.append(self._result(resource_id, "segment", segment))
        candidates = [candidate for candidate in candidates if candidate.status == "ok"]
        if not candidates:
            return [self.read_resource_summary(resource_id)]

        packed: list[ResourceReadResult] = []
        used = 0
        for candidate in candidates:
            cost = estimate_tokens(candidate.content_text)
            if packed and used + cost > max_tokens:
                break
            if not packed or used + cost <= max_tokens:
                packed.append(candidate)
                used += cost
        return packed

    def _matches_filters(
        self, resource: ResourceRecord, filters: ResourceSearchFilter | None
    ) -> bool:
        if filters is None:
            return True
        if filters.project is not None and resource.project != filters.project:
            return False
        if filters.kind is not None and str(resource.kind) != _kind_value(filters.kind):
            return False
        if (
            filters.tenant_ids is not None
            and resource.tenant_id not in filters.tenant_ids
        ):
            return False
        sensitivity = str(
            getattr(resource.sensitivity, "value", resource.sensitivity)
        )
        if (
            filters.allowed_sensitivities is not None
            and sensitivity not in filters.allowed_sensitivities
        ):
            return False
        if filters.tags:
            tags = set(resource.tags)
            if any(tag not in tags for tag in filters.tags):
                return False
        return True

    def _score_resource(
        self,
        resource: ResourceRecord,
        extractions: list[ExtractionRecord],
        query_tokens: list[str],
    ) -> tuple[float, list[str]]:
        title_text = " ".join([resource.title, resource.uri, resource.project or "", str(resource.kind)])
        tag_text = " ".join(resource.tags)
        score = 0.0
        for token in query_tokens:
            if token in title_text.lower():
                score += 3.0
            if token in tag_text.lower():
                score += 2.0

        matched: list[str] = []
        for extraction in extractions:
            haystack = " ".join([
                extraction.content_text,
                extraction.source_locator or "",
                str(extraction.kind),
            ]).lower()
            matches = sum(1 for token in query_tokens if token in haystack)
            if matches:
                score += matches
                matched.append(extraction.id)
        return score, matched

    def _read_best(
        self, resource_id: str, kind: ExtractionKind, level: str
    ) -> ResourceReadResult:
        matches = self._extractions(resource_id, kind)
        if not matches:
            return self._degraded(resource_id, level, f"No {level} extraction.")
        return self._result(resource_id, level, self._best_extraction(matches))

    def _extractions(self, resource_id: str, kind: ExtractionKind) -> list[ExtractionRecord]:
        matches = [ext for ext in self.store.iter_extractions(resource_id=resource_id) if ext.kind == kind.value]
        matches.sort(key=lambda ext: (ext.source_locator or "", ext.id))
        return matches

    @staticmethod
    def _best_extraction(extractions: list[ExtractionRecord]) -> ExtractionRecord:
        return sorted(
            extractions,
            key=lambda ext: (-ext.confidence, ext.source_locator or "", ext.id),
        )[0]

    @staticmethod
    def _result(resource_id: str, level: str, extraction: ExtractionRecord) -> ResourceReadResult:
        return ResourceReadResult(
            status="ok",
            resource_id=resource_id,
            level=level,
            content_text=extraction.content_text,
            extraction_id=extraction.id,
            source_locator=extraction.source_locator,
            confidence=extraction.confidence,
        )

    @staticmethod
    def _degraded(resource_id: str, level: str, message: str) -> ResourceReadResult:
        return ResourceReadResult(
            status="degraded",
            resource_id=resource_id,
            level=level,
            content_text=message,
        )


def search_resource(
    store: ResourceStore,
    query: str,
    *,
    top_k: int = 10,
    filters: ResourceSearchFilter | None = None,
) -> list[ResourceHit]:
    return ResourceReader(store).search_resource(query, top_k=top_k, filters=filters)


def read_resource_summary(store: ResourceStore, resource_id: str) -> ResourceReadResult:
    return ResourceReader(store).read_resource_summary(resource_id)


def read_resource_outline(store: ResourceStore, resource_id: str) -> ResourceReadResult:
    return ResourceReader(store).read_resource_outline(resource_id)


def read_resource_segment(
    store: ResourceStore,
    resource_id: str,
    *,
    locator: str | None = None,
) -> ResourceReadResult:
    return ResourceReader(store).read_resource_segment(resource_id, locator=locator)


def read_resource_exact(
    store: ResourceStore,
    resource_id: str,
    *,
    locator: str | None = None,
) -> ResourceReadResult:
    return ResourceReader(store).read_resource_exact(resource_id, locator=locator)


def read_resource_context(
    store: ResourceStore,
    resource_id: str,
    *,
    max_tokens: int,
) -> list[ResourceReadResult]:
    return ResourceReader(store).read_resource_context(resource_id, max_tokens=max_tokens)


__all__ = [
    "PROMPT_RESOURCE_SENSITIVITIES",
    "ResourceHit",
    "ResourceReadResult",
    "ResourceReader",
    "ResourceSearchFilter",
    "read_resource_context",
    "read_resource_exact",
    "read_resource_outline",
    "read_resource_segment",
    "read_resource_summary",
    "resource_visible_for_prompt",
    "search_resource",
]
