"""Contradiction finding helpers for drift detection."""

from __future__ import annotations

import math
import re
from typing import Any

from agent_brain.memory.governance.drift_patterns import DecisionPatternExtractor
from agent_brain.memory.governance.drift_types import DriftFinding, DriftType


_BROAD_TAGS = {
    "agent-memory-hub",
    "memory",
    "decision",
    "fact",
    "artifact",
    "episode",
    "signal",
    "handoff",
    "architecture",
    "refactor",
    "verification",
    "test",
    "tests",
    "needs-review",
    "auto-captured",
}
_TITLE_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "use",
    "using",
    "choice",
    "decision",
    "updated",
    "update",
}


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a)) or 1.0
    norm_b = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (norm_a * norm_b)


def detect_contradictions(
    items_with_bodies: list,
    *,
    pattern_extractor: DecisionPatternExtractor,
    embedder: Any = None,
    semantic_threshold: float = 0.8,
) -> list[DriftFinding]:
    """Find decision contradictions within each project.

    Two layers:
      1. Heuristic decision-pattern contradiction detection.
      2. Optional semantic similarity confidence boost/advisory when an embedder
         is provided.
    """
    findings: list[DriftFinding] = []
    seen_pairs: set[tuple[str, str]] = set()

    decisions = [
        (item, body)
        for item, body in items_with_bodies
        if getattr(getattr(item, "type", None), "value", item.type) == "decision"
    ]

    project_groups: dict[str, list] = {}
    for item, body in decisions:
        project = item.project or "unknown"
        if project not in project_groups:
            project_groups[project] = []
        project_groups[project].append((item, body))

    embeddings: dict[str, list[float]] = {}
    if embedder is not None:
        for item, body in decisions:
            embeddings[item.id] = embedder.embed(f"{item.title} {body}")

    for project, group in project_groups.items():
        if len(group) < 2:
            continue

        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                item_a, body_a = group[i]
                item_b, body_b = group[j]
                pair_key = (min(item_a.id, item_b.id), max(item_a.id, item_b.id))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                if not _same_decision_topic(item_a, item_b):
                    continue

                patterns_a = pattern_extractor.extract_decision_patterns(body_a)
                patterns_b = pattern_extractor.extract_decision_patterns(body_b)
                heuristic_contradiction = None
                if patterns_a and patterns_b:
                    heuristic_contradiction = pattern_extractor.check_contradiction(
                        patterns_a,
                        patterns_b,
                    )

                sim = None
                if item_a.id in embeddings and item_b.id in embeddings:
                    sim = _cosine_similarity(embeddings[item_a.id], embeddings[item_b.id])

                if heuristic_contradiction:
                    if sim is not None and sim >= semantic_threshold:
                        confidence = 0.8
                        evidence = f"[semantic sim={sim:.2f}] {heuristic_contradiction}"
                    else:
                        confidence = 0.5
                        evidence = heuristic_contradiction
                    findings.append(DriftFinding(
                        drift_type=DriftType.CONTRADICTION,
                        item_ids=[item_a.id, item_b.id],
                        confidence=confidence,
                        description=f"Contradictory decisions in project {project}",
                        evidence=evidence,
                    ))
                elif sim is not None and sim >= semantic_threshold:
                    findings.append(DriftFinding(
                        drift_type=DriftType.CONTRADICTION,
                        item_ids=[item_a.id, item_b.id],
                        confidence=0.6,
                        description=(
                            f"Semantically similar decisions in project {project} "
                            "— review for potential conflict"
                        ),
                        evidence=f"Cosine similarity {sim:.2f} >= threshold {semantic_threshold}",
                    ))

    return findings


def _same_decision_topic(item_a: Any, item_b: Any) -> bool:
    shared_tags = _meaningful_tags(item_a) & _meaningful_tags(item_b)
    if shared_tags:
        return True
    title_overlap = _title_terms(getattr(item_a, "title", "")) & _title_terms(
        getattr(item_b, "title", "")
    )
    return len(title_overlap) >= 1


def _meaningful_tags(item: Any) -> set[str]:
    tags = set()
    for tag in getattr(item, "tags", ()) or ():
        value = str(tag).strip().lower()
        if not value or value in _BROAD_TAGS or value.startswith("session-"):
            continue
        tags.add(value)
    return tags


def _title_terms(title: object) -> set[str]:
    terms = set()
    for term in re.findall(r"[\w\u4e00-\u9fff]+", str(title).lower()):
        if len(term) < 3 or term.isdigit() or term in _TITLE_STOPWORDS:
            continue
        terms.add(term)
    return terms


__all__ = ["detect_contradictions"]
