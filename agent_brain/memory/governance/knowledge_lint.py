from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Mapping

from agent_brain.contracts.memory_enums import memory_enum_value
from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.validity import ValidityEvaluator, ValidityState


@dataclass(frozen=True)
class LintFinding:
    item_id: str
    issue_type: str
    severity: str
    title: str
    summary: str
    reasons: tuple[str, ...]
    evidence: tuple[str, ...]
    suggested_action: str

    def to_dict(self) -> dict[str, object]:
        return {
            "item_id": self.item_id,
            "issue_type": self.issue_type,
            "severity": self.severity,
            "title": self.title,
            "summary": self.summary,
            "reasons": list(self.reasons),
            "evidence": list(self.evidence),
            "suggested_action": self.suggested_action,
        }


@dataclass(frozen=True)
class LintReport:
    total_items: int
    total_findings: int
    counts_by_type: dict[str, int]
    findings: list[LintFinding]

    def to_dict(self) -> dict[str, object]:
        return {
            "total_items": self.total_items,
            "total_findings": self.total_findings,
            "counts_by_type": dict(self.counts_by_type),
            "findings": [finding.to_dict() for finding in self.findings],
        }


SOURCE_REQUIRED_TYPES = {"fact", "decision"}
VALIDITY_FINDING_META = {
    ValidityState.stale: ("stale", "warning", "refresh_or_mark_historical"),
    ValidityState.superseded: ("superseded", "info", "use_superseding_memory"),
    ValidityState.review_required: ("review_required", "warning", "review_approve_or_reject"),
    ValidityState.contradicted: ("contradicted", "error", "inspect_feedback_and_supersede"),
    ValidityState.scope_mismatch: ("scope_mismatch", "warning", "revalidate_in_current_scope"),
}


class KnowledgeLinter:
    def __init__(
        self,
        store: ItemsStore,
        *,
        evaluator: ValidityEvaluator | None = None,
        current_scope: Mapping[str, str] | None = None,
    ) -> None:
        self.store = store
        self.evaluator = evaluator or ValidityEvaluator()
        self.current_scope = current_scope

    def run(
        self,
        *,
        project: str | None = None,
        issue_type: str | None = None,
        limit: int | None = None,
    ) -> LintReport:
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")

        entries = list(self.store.iter_all())
        all_item_ids = {item.id for item, _body in entries}
        scanned = [
            (item, body)
            for item, body in entries
            if project is None or item.project == project
        ]

        findings: list[LintFinding] = []
        for item, body in scanned:
            validity_finding = self._validity_finding(item, body)
            if validity_finding is not None:
                findings.append(validity_finding)
            findings.extend(self._structural_findings(item, all_item_ids))

        if issue_type is not None:
            findings = [finding for finding in findings if finding.issue_type == issue_type]
        if limit is not None:
            findings = findings[:limit]

        counts = Counter(finding.issue_type for finding in findings)
        return LintReport(
            total_items=len(scanned),
            total_findings=len(findings),
            counts_by_type=dict(sorted(counts.items())),
            findings=findings,
        )

    def _validity_finding(self, item: MemoryItem, body: str) -> LintFinding | None:
        evaluation = self.evaluator.evaluate(
            item,
            body,
            current_scope=self.current_scope,
        )
        meta = VALIDITY_FINDING_META.get(evaluation.state)
        if meta is None:
            return None
        issue_type, severity, suggested_action = meta
        return _finding(
            item,
            issue_type=issue_type,
            severity=severity,
            reasons=evaluation.reasons,
            evidence=evaluation.evidence,
            suggested_action=suggested_action,
        )

    def _structural_findings(
        self,
        item: MemoryItem,
        all_item_ids: set[str],
    ) -> list[LintFinding]:
        findings: list[LintFinding] = []
        if _requires_source(item) and not _has_source_refs(item):
            findings.append(_finding(
                item,
                issue_type="source_missing",
                severity="warning",
                reasons=("missing_source_refs",),
                evidence=(),
                suggested_action="add_source_reference",
            ))

        missing_refs = tuple(ref_id for ref_id in item.refs.mems if ref_id not in all_item_ids)
        if missing_refs:
            findings.append(_finding(
                item,
                issue_type="orphan_link",
                severity="warning",
                reasons=("missing_refs_mems",),
                evidence=tuple(f"missing_ref:{ref_id}" for ref_id in missing_refs),
                suggested_action="unlink_missing_memory_or_restore_target",
            ))
        return findings


def _finding(
    item: MemoryItem,
    *,
    issue_type: str,
    severity: str,
    reasons: tuple[str, ...],
    evidence: tuple[str, ...],
    suggested_action: str,
) -> LintFinding:
    return LintFinding(
        item_id=item.id,
        issue_type=issue_type,
        severity=severity,
        title=item.title,
        summary=item.summary,
        reasons=reasons,
        evidence=evidence,
        suggested_action=suggested_action,
    )


def _requires_source(item: MemoryItem) -> bool:
    return memory_enum_value(item.type) in SOURCE_REQUIRED_TYPES


def _has_source_refs(item: MemoryItem) -> bool:
    refs = item.refs
    return bool(
        refs.files
        or refs.urls
        or refs.commits
        or refs.resources
        or refs.extractions
        or refs.mems
    )


__all__ = [
    "KnowledgeLinter",
    "LintFinding",
    "LintReport",
]
