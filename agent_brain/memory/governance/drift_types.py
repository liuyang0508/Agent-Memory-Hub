"""Shared drift detection types."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DriftType(str, Enum):
    CONTRADICTION = 'contradiction'
    STALENESS = 'staleness'
    CITATION_ROT = 'citation_rot'
    DRIFT_CLUSTER = 'drift_cluster'


@dataclass
class DriftFinding:
    drift_type: DriftType
    item_ids: list[str]
    confidence: float  # 0.0 - 1.0
    description: str
    evidence: str


@dataclass
class DriftReport:
    scanned_items: int
    findings: list[DriftFinding] = field(default_factory=list)
    contradictions: int = 0
    stale: int = 0
    citation_rot: int = 0
    drift_clusters: int = 0

    @property
    def total_findings(self) -> int:
        return len(self.findings)

    @property
    def clean(self) -> bool:
        return self.total_findings == 0


__all__ = ["DriftFinding", "DriftReport", "DriftType"]
