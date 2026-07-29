"""Stable connected-component cases for overlapping contradiction findings."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable

from agent_brain.memory.governance.drift_types import DriftFinding, DriftType


@dataclass(frozen=True)
class ContradictionCase:
    case_id: str
    item_ids: tuple[str, ...]
    pair_count: int
    confidence: float
    evidence: tuple[str, ...]


def build_contradiction_cases(
    findings: Iterable[DriftFinding],
) -> tuple[ContradictionCase, ...]:
    """Collapse overlapping contradiction pairs into deterministic cases."""

    contradiction_findings = [
        finding
        for finding in findings
        if finding.drift_type == DriftType.CONTRADICTION and finding.item_ids
    ]
    parent: dict[str, str] = {}

    def find(item_id: str) -> str:
        parent.setdefault(item_id, item_id)
        while parent[item_id] != item_id:
            parent[item_id] = parent[parent[item_id]]
            item_id = parent[item_id]
        return item_id

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        smaller, larger = sorted((left_root, right_root))
        parent[larger] = smaller

    for finding in contradiction_findings:
        first = finding.item_ids[0]
        find(first)
        for item_id in finding.item_ids[1:]:
            union(first, item_id)

    grouped_items: dict[str, set[str]] = {}
    for item_id in parent:
        grouped_items.setdefault(find(item_id), set()).add(item_id)

    cases: list[ContradictionCase] = []
    for item_ids_set in grouped_items.values():
        item_ids = tuple(sorted(item_ids_set))
        scoped = [
            finding
            for finding in contradiction_findings
            if set(finding.item_ids).issubset(item_ids_set)
        ]
        digest = hashlib.sha256("\0".join(item_ids).encode("utf-8")).hexdigest()
        evidence = tuple(
            dict.fromkeys(
                finding.evidence
                for finding in sorted(
                    scoped,
                    key=lambda row: (-row.confidence, tuple(row.item_ids)),
                )
                if finding.evidence
            )
        )[:3]
        cases.append(
            ContradictionCase(
                case_id=f"contradiction-{digest[:16]}",
                item_ids=item_ids,
                pair_count=len(scoped),
                confidence=max(
                    (finding.confidence for finding in scoped),
                    default=0.0,
                ),
                evidence=evidence,
            )
        )
    return tuple(sorted(cases, key=lambda case: case.case_id))


__all__ = ["ContradictionCase", "build_contradiction_cases"]
