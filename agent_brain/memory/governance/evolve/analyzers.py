"""Proposal analysis strategies for the evolve engine."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from agent_brain.memory.governance.evolve.archive_analysis import find_archive_candidates
from agent_brain.memory.governance.evolve.engine import EvolveAction, EvolveProposal, build_consolidated_body
from agent_brain.memory.governance.evolve.promotion_analysis import find_promotion_candidates
from agent_brain.memory.governance.evolve.proposal_previews import (
    generate_archive_preview,
    generate_consolidate_preview,
    generate_promote_preview,
    generate_skill_preview,
)
from agent_brain.memory.governance.evolve.skill_generation_analysis import find_skill_generation_candidates
from agent_brain.contracts.memory_item import MemoryItem, MemoryType


class ProposalAnalyzer:
    """Analyze memory items and produce evolve proposals."""

    def __init__(self, index: Any = None, decay_archive_threshold: float = 0.1) -> None:
        self.index = index
        self.decay_archive_threshold = decay_archive_threshold

    def analyze(self, items: list[tuple[MemoryItem, str]]) -> list[EvolveProposal]:
        """Run all proposal analyzers in stable report order."""
        proposals: list[EvolveProposal] = []
        proposals.extend(self._analyze_crystallization(items))
        proposals.extend(self._analyze_skill_synthesis(items))
        proposals.extend(self._analyze_consolidation_candidates(items))
        proposals.extend(self._analyze_promotion_candidates(items))
        proposals.extend(self._analyze_archive_candidates(items))
        proposals.extend(self._analyze_skill_generation(items))
        return proposals

    def _analyze_consolidation_candidates(self, items: list[tuple[MemoryItem, str]]) -> list[EvolveProposal]:
        """Find items that can be consolidated (same project, similar topic, >3 items)."""
        project_groups: dict[str, list[tuple[MemoryItem, str]]] = defaultdict(list)
        for item, body in items:
            if item.project:
                project_groups[item.project].append((item, body))

        proposals = []
        for project, group_items in project_groups.items():
            if len(group_items) > 3:
                item_ids = [item.id for item, _ in group_items]
                titles = [item.title for item, _ in group_items[:5]]

                proposals.append(EvolveProposal(
                    action=EvolveAction.CONSOLIDATE,
                    item_ids=item_ids,
                    title=f"Consolidate {len(group_items)} items in project '{project}'",
                    description=f"Merge {len(group_items)} related items into a single comprehensive item",
                    rationale=f"Project '{project}' has {len(group_items)} items that could be consolidated for better organization. Sample titles: {', '.join(titles)}",
                    confidence=min(0.9, 0.5 + len(group_items) * 0.05),
                    output_preview=self._generate_consolidate_preview(project, group_items),
                    audit_payload=build_consolidated_body(
                        sorted(group_items, key=lambda p: p[0].created_at)
                    ),
                ))

        return proposals

    def _analyze_promotion_candidates(self, items: list[tuple[MemoryItem, str]]) -> list[EvolveProposal]:
        """Find episodes that contain generalizable learnings."""
        return find_promotion_candidates(
            items,
            promote_preview=self._generate_promote_preview,
        )

    def _analyze_archive_candidates(self, items: list[tuple[MemoryItem, str]]) -> list[EvolveProposal]:
        """Find expired signals, very old artifacts, and decay-dead items."""
        return find_archive_candidates(
            items,
            index=self.index,
            decay_archive_threshold=self.decay_archive_threshold,
            archive_preview=self._generate_archive_preview,
        )

    def _analyze_skill_generation(self, items: list[tuple[MemoryItem, str]]) -> list[EvolveProposal]:
        """Find repeated patterns across episodes/decisions that could become a skill."""
        return find_skill_generation_candidates(
            items,
            skill_preview=self._generate_skill_preview,
        )

    def _analyze_crystallization(self, items: list[tuple[MemoryItem, str]]) -> list[EvolveProposal]:
        """Detect recurring patterns in L0 items and propose crystallization into policies."""
        from agent_brain.memory.governance.evolve.pattern_detector import detect_patterns

        clusters = detect_patterns(items, only_l0=True)
        proposals = []
        for cluster in clusters:
            proposals.append(EvolveProposal(
                action=EvolveAction.CRYSTALLIZE,
                item_ids=cluster.item_ids,
                title=f"Crystallize pattern → policy ({cluster.support_count} observations)",
                description=f"Pattern: {cluster.representative_text}",
                rationale=f"同一模式出现 {cluster.support_count} 次 (阈值 3)，project={cluster.project}, tags={cluster.tags}",
                confidence=min(0.9, 0.5 + cluster.support_count * 0.05),
                output_preview=f"**Policy**: {cluster.representative_text}\n**Support**: {cluster.support_count}\n**From**: {', '.join(cluster.item_ids[:5])}...",
            ))
        return proposals

    def _analyze_skill_synthesis(self, items: list[tuple[MemoryItem, str]]) -> list[EvolveProposal]:
        """Find mature policies that can be synthesized into skills."""
        from agent_brain.memory.governance.evolve.crystallizer import SKILL_MATURITY_THRESHOLD

        policies = [(it, body) for it, body in items if it.type == MemoryType.policy]
        if not policies:
            return []

        by_project: dict[str, list[tuple[MemoryItem, str]]] = defaultdict(list)
        for it, body in policies:
            if it.support_count >= SKILL_MATURITY_THRESHOLD and it.superseded_by is None:
                by_project[it.project or "__general__"].append((it, body))

        proposals = []
        for project, group in by_project.items():
            if len(group) < 2:
                continue
            item_ids = [it.id for it, _ in group]
            proposals.append(EvolveProposal(
                action=EvolveAction.SYNTHESIZE_SKILL,
                item_ids=item_ids,
                title=f"Synthesize skill from {len(group)} mature policies (project={project})",
                description=f"Policies: {', '.join(it.title for it, _ in group[:3])}...",
                rationale=f"{len(group)} 条 policy 各自 support>={SKILL_MATURITY_THRESHOLD}，可合成 skill",
                confidence=min(0.95, 0.6 + len(group) * 0.08),
                output_preview=f"**Skill**: {project}\n**Policies**: {len(group)}\n**Total support**: {sum(it.support_count for it, _ in group)}",
            ))
        return proposals

    def _generate_consolidate_preview(self, project: str, items: list[tuple[MemoryItem, str]]) -> str:
        return generate_consolidate_preview(project, items)

    def _generate_promote_preview(self, item: MemoryItem, body: str, keywords: list[str]) -> str:
        return generate_promote_preview(item, body, keywords)

    def _generate_archive_preview(self, item: MemoryItem) -> str:
        return generate_archive_preview(item)

    def _generate_skill_preview(self, project: str, tags: str, items: list[tuple[MemoryItem, str]]) -> str:
        return generate_skill_preview(project, tags, items)


__all__ = [
    "ProposalAnalyzer",
    "find_promotion_candidates",
    "find_skill_generation_candidates",
]
