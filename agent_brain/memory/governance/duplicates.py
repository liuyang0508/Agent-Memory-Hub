"""Duplicate detection helpers for governance pipeline scans."""
from __future__ import annotations

import hashlib
import re

from agent_brain.memory.governance.pipeline_types import GovernanceIssue
from agent_brain.memory.governance.runtime_noise import is_governance_noise
from agent_brain.contracts.memory_item import MemoryItem


def fingerprint(item: MemoryItem) -> str:
    """Return normalized title+summary fingerprint for exact duplicate checks."""
    payload = f"{item.title.strip().lower()}\n{item.summary.strip().lower()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def detect_duplicates(items: list[MemoryItem]) -> list[GovernanceIssue]:
    """Find exact and near-duplicate memory items."""
    issues: list[GovernanceIssue] = []
    knowledge_items = [item for item in items if not is_governance_noise(item)]

    by_fp: dict[str, list[MemoryItem]] = {}
    for item in knowledge_items:
        by_fp.setdefault(fingerprint(item), []).append(item)

    exact_dup_ids: set[str] = set()
    for fp_items in by_fp.values():
        if len(fp_items) < 2:
            continue
        canonical = fp_items[0]
        for dup in fp_items[1:]:
            exact_dup_ids.add(dup.id)
            issues.append(GovernanceIssue(
                item_id=dup.id,
                issue_type="duplicate",
                severity="error",
                description=(
                    f"Item '{dup.title}' is an EXACT duplicate of "
                    f"'{canonical.title}' (sha256 match on title+summary)"
                ),
                suggestion=f"Delete {dup.id} or supersede via refs.mems → {canonical.id}",
            ))

    candidates = [item for item in knowledge_items if item.id not in exact_dup_ids]
    by_project: dict[str | None, list[MemoryItem]] = {}
    for item in candidates:
        by_project.setdefault(item.project, []).append(item)

    seen_pairs: set[tuple[str, str]] = set()
    for project_items in by_project.values():
        for i in range(len(project_items)):
            for j in range(i + 1, len(project_items)):
                item_a = project_items[i]
                item_b = project_items[j]
                pair_key = tuple(sorted([item_a.id, item_b.id]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                jaccard = _jaccard_title_summary(item_a, item_b)
                if jaccard >= 0.8:
                    issues.append(GovernanceIssue(
                        item_id=item_b.id,
                        issue_type="duplicate",
                        severity="warning",
                        description=(
                            f"Item '{item_b.title}' is near-duplicate of "
                            f"'{item_a.title}' (jaccard={jaccard:.2f})"
                        ),
                        suggestion=f"Consider merging with {item_a.id} or removing duplicate",
                    ))

    return issues


def _jaccard_title_summary(item_a: MemoryItem, item_b: MemoryItem) -> float:
    text_a = f"{item_a.title} {item_a.summary}".lower()
    text_b = f"{item_b.title} {item_b.summary}".lower()
    words_a = set(re.findall(r"\w+", text_a))
    words_b = set(re.findall(r"\w+", text_b))
    if not words_a or not words_b:
        return 0.0
    union = words_a | words_b
    if not union:
        return 0.0
    return len(words_a & words_b) / len(union)


__all__ = ["detect_duplicates", "fingerprint"]
