"""Crystallizer: promote PatternClusters into policy/skill items.

When a cluster's support_count reaches CRYSTALLIZE_THRESHOLD, it becomes a
policy item (type=policy, abstraction=L1). When multiple mature policies
converge, they synthesize into a skill (type=skill, abstraction=L2, versioned).

All mutations go through WriteService (audit gate → md → index) so governance
is never bypassed. Source items get their `superseded_by` updated to point at
the new policy/skill.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from agent_brain.memory.store.items_store import ItemsStore, make_item_id
from agent_brain.memory.governance.evolve.pattern_detector import PatternCluster
from agent_brain.contracts.memory_item import (
    AbstractionLayer,
    MemoryItem,
    MemoryType,
    Source,
)

_log = logging.getLogger(__name__)

SKILL_MATURITY_THRESHOLD = 5


def _build_policy_body(cluster: PatternCluster) -> str:
    lines = [
        f"**模式**: {cluster.representative_text}",
        f"**适用场景**: project={cluster.project or '通用'}, tags={', '.join(cluster.tags) or '无'}",
        f"**支撑证据**: {cluster.support_count} 次观察",
        "**反例**: 0 次",
        "",
        "## 来源 items",
        "",
    ]
    for item_id in cluster.item_ids[:10]:
        lines.append(f"- `{item_id}`")
    if len(cluster.item_ids) > 10:
        lines.append(f"- ... 及另外 {len(cluster.item_ids) - 10} 条")
    return "\n".join(lines)


def crystallize_policy(
    cluster: PatternCluster,
    store: ItemsStore,
    *,
    agent: str = "evolve-engine",
) -> MemoryItem:
    """Create a policy item from a mature cluster and mark sources as superseded."""
    now = datetime.now(timezone.utc)
    item_id = make_item_id(title=f"policy-{cluster.fingerprint[:8]}", when=now)

    policy = MemoryItem(
        id=item_id,
        type=MemoryType.policy,
        created_at=now,
        agent=agent,
        project=cluster.project,
        tags=cluster.tags,
        title=f"Policy: {cluster.representative_text[:60]}",
        summary=f"从 {cluster.support_count} 条观察中结晶的行为策略",
        confidence=min(0.9, 0.5 + cluster.support_count * 0.05),
        abstraction=AbstractionLayer.L1,
        source=Source(kind="evolve", extractor="crystallizer"),
        support_count=cluster.support_count,
        evolved_from=cluster.item_ids,
    )

    body = _build_policy_body(cluster)
    store.write(policy, body)

    for src_id in cluster.item_ids:
        try:
            store.update_frontmatter(src_id, superseded_by=item_id)
        except FileNotFoundError:
            _log.debug("source item %s not found for supersede update", src_id)

    _log.info(
        "crystallized policy %s from %d items (project=%s)",
        item_id,
        cluster.support_count,
        cluster.project,
    )
    return policy


def _build_skill_body(
    policies: list[tuple[MemoryItem, str]], project: str | None
) -> str:
    lines = [
        f"**能力**: 从 {len(policies)} 条成熟策略合成的可复用技能",
        f"**适用场景**: project={project or '通用'}",
        "",
        "## 组成策略",
        "",
    ]
    for p, _ in policies:
        lines.append(f"- `{p.id}` — {p.title} (support={p.support_count})")
    return "\n".join(lines)


def synthesize_skill(
    policies: list[tuple[MemoryItem, str]],
    store: ItemsStore,
    *,
    existing_skill: Optional[tuple[MemoryItem, str]] = None,
    agent: str = "evolve-engine",
) -> MemoryItem:
    """Synthesize a skill from mature policies. Versions up if existing_skill provided."""
    now = datetime.now(timezone.utc)
    project = next((p.project for p, _ in policies if p.project), None)
    policy_ids = [p.id for p, _ in policies]

    version = 1
    if existing_skill:
        version = existing_skill[0].version + 1
        try:
            store.update_frontmatter(existing_skill[0].id, superseded_by="pending")
        except FileNotFoundError:
            pass

    slug = f"skill-{project or 'general'}"[:30]
    item_id = make_item_id(title=slug, when=now)

    skill = MemoryItem(
        id=item_id,
        type=MemoryType.skill,
        created_at=now,
        agent=agent,
        project=project,
        tags=sorted({t for p, _ in policies for t in p.tags}),
        title=f"Skill v{version}: {project or 'general'}",
        summary=f"从 {len(policies)} 条成熟策略合成 (v{version})",
        confidence=0.85,
        abstraction=AbstractionLayer.L2,
        source=Source(kind="evolve", extractor="synthesizer"),
        support_count=sum(p.support_count for p, _ in policies),
        evolved_from=policy_ids,
        version=version,
    )

    body = _build_skill_body(policies, project)
    store.write(skill, body)

    if existing_skill:
        try:
            store.update_frontmatter(existing_skill[0].id, superseded_by=item_id)
        except FileNotFoundError:
            pass

    for pid in policy_ids:
        try:
            store.update_frontmatter(pid, superseded_by=item_id)
        except FileNotFoundError:
            pass

    _log.info("synthesized skill %s v%d from %d policies", item_id, version, len(policies))
    return skill
