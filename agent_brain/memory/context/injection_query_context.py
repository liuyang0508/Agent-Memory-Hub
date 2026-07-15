"""Immutable routed-query evidence consumed by injection governance."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from agent_brain.memory.context.query_signal import QuerySignal
from agent_brain.memory.recall.admission import RecallAdmission
from agent_brain.memory.recall.routed_types import RouteEvidence


@dataclass(frozen=True)
class InjectionQueryContext:
    """Complete query evidence for the routed injection path."""

    raw_query: str
    admission: RecallAdmission
    query_signal: QuerySignal
    evidence_by_id: Mapping[str, RouteEvidence]

    def __post_init__(self) -> None:
        if not isinstance(self.raw_query, str):
            raise TypeError("raw_query must be a string")
        if not isinstance(self.admission, RecallAdmission):
            raise TypeError("admission must be a RecallAdmission")
        if not isinstance(self.query_signal, QuerySignal):
            raise TypeError("query_signal must be a QuerySignal")
        if not isinstance(self.evidence_by_id, Mapping):
            raise TypeError("evidence_by_id must be a mapping")
        if self.admission.allowed and not self.raw_query.strip():
            raise ValueError("allowed routed context requires a raw query")
        copied = dict(self.evidence_by_id)
        if any(
            not isinstance(item_id, str)
            or not item_id
            or not isinstance(evidence, RouteEvidence)
            for item_id, evidence in copied.items()
        ):
            raise TypeError("evidence_by_id must map strings to RouteEvidence")
        object.__setattr__(self, "evidence_by_id", MappingProxyType(copied))


__all__ = ["InjectionQueryContext"]
