"""Tests for evolve/crystallizer.py — cluster → policy/skill crystallization."""
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.governance.evolve.crystallizer import (
    SKILL_MATURITY_THRESHOLD,
    crystallize_policy,
    synthesize_skill,
)
from agent_brain.memory.governance.evolve.pattern_detector import PatternCluster
from agent_brain.contracts.memory_item import AbstractionLayer, MemoryItem, MemoryType


@pytest.fixture
def tmp_store(tmp_path):
    items_dir = tmp_path / "items"
    items_dir.mkdir()
    return ItemsStore(items_dir=items_dir)


def _seed_items(store, count=3, project="myproj"):
    """Write count L0 items and return their IDs."""
    ids = []
    for i in range(count):
        item = MemoryItem(
            id=f"mem-20260601-10000{i}-seed{i}",
            type=MemoryType.episode,
            created_at=datetime(2026, 6, 1, i, tzinfo=timezone.utc),
            title=f"Episode {i}",
            summary=f"Summary {i}",
            project=project,
            tags=["test"],
        )
        store.write(item, f"Body of episode {i}")
        ids.append(item.id)
    return ids


def test_crystallize_creates_policy(tmp_store):
    ids = _seed_items(tmp_store)
    cluster = PatternCluster(
        fingerprint="abc123",
        item_ids=ids,
        support_count=3,
        representative_text="SSE 比 WebSocket 简单",
        project="myproj",
        tags=["streaming"],
    )

    policy = crystallize_policy(cluster, tmp_store)

    assert policy.type == MemoryType.policy
    assert policy.abstraction == AbstractionLayer.L1
    assert policy.support_count == 3
    assert policy.evolved_from == ids
    assert policy.project == "myproj"
    assert "streaming" in policy.tags

    read_back, body = tmp_store.get(policy.id)
    assert "支撑证据" in body
    assert "3 次观察" in body


def test_crystallize_marks_sources_superseded(tmp_store):
    ids = _seed_items(tmp_store)
    cluster = PatternCluster(
        fingerprint="def456",
        item_ids=ids,
        support_count=3,
        representative_text="test pattern",
        project="myproj",
        tags=[],
    )

    policy = crystallize_policy(cluster, tmp_store)

    for src_id in ids:
        src_item, _ = tmp_store.get(src_id)
        assert src_item.superseded_by == policy.id


def test_synthesize_skill_basic(tmp_store):
    policies = []
    for i in range(3):
        p = MemoryItem(
            id=f"mem-20260601-10000{i}-pol{i}",
            type=MemoryType.policy,
            created_at=datetime(2026, 6, 1, i, tzinfo=timezone.utc),
            title=f"Policy {i}",
            summary=f"Summary {i}",
            project="myproj",
            tags=["api"],
            abstraction=AbstractionLayer.L1,
            support_count=SKILL_MATURITY_THRESHOLD + 1,
        )
        tmp_store.write(p, f"Policy body {i}")
        policies.append((p, f"Policy body {i}"))

    skill = synthesize_skill(policies, tmp_store)

    assert skill.type == MemoryType.skill
    assert skill.abstraction == AbstractionLayer.L2
    assert skill.version == 1
    assert len(skill.evolved_from) == 3
    assert skill.support_count == sum(p.support_count for p, _ in policies)


def test_synthesize_skill_version_up(tmp_store):
    existing_skill = MemoryItem(
        id="mem-20260601-100000-oldskill",
        type=MemoryType.skill,
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        title="Skill v1",
        summary="old",
        project="myproj",
        version=1,
        abstraction=AbstractionLayer.L2,
    )
    tmp_store.write(existing_skill, "Old skill body")

    policies = []
    for i in range(2):
        p = MemoryItem(
            id=f"mem-20260601-10000{i}-newpol{i}",
            type=MemoryType.policy,
            created_at=datetime(2026, 6, 1, i, tzinfo=timezone.utc),
            title=f"New Policy {i}",
            summary=f"new policy",
            project="myproj",
            tags=["v2"],
            support_count=6,
        )
        tmp_store.write(p, f"New policy body {i}")
        policies.append((p, f"New policy body {i}"))

    new_skill = synthesize_skill(
        policies, tmp_store, existing_skill=(existing_skill, "Old skill body")
    )

    assert new_skill.version == 2
    old_read, _ = tmp_store.get(existing_skill.id)
    assert old_read.superseded_by == new_skill.id
