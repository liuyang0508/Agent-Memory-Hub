"""Tests for dreaming skill synthesis helper ownership."""
from datetime import datetime, timezone

from agent_brain.memory.governance.evolve.crystallizer import SKILL_MATURITY_THRESHOLD
from agent_brain.memory.governance.evolve.dream_skill_synthesis import synthesize_mature_policy_skills
from agent_brain.contracts.memory_item import AbstractionLayer, MemoryItem, MemoryType


def _item(
    item_id: str,
    memory_type: MemoryType,
    *,
    project: str | None,
    support_count: int = 0,
    superseded_by: str | None = None,
) -> MemoryItem:
    return MemoryItem(
        id=item_id,
        type=memory_type,
        created_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
        title=item_id,
        summary=item_id,
        project=project,
        abstraction=AbstractionLayer.L1 if memory_type == MemoryType.policy else AbstractionLayer.L2,
        support_count=support_count,
        superseded_by=superseded_by,
    )


def test_synthesizes_mature_policy_groups_with_existing_skill() -> None:
    items = [
        (
            _item(
                "mem-20260610-100000-pol-a",
                MemoryType.policy,
                project="alpha",
                support_count=SKILL_MATURITY_THRESHOLD,
            ),
            "policy a",
        ),
        (
            _item(
                "mem-20260610-100001-pol-b",
                MemoryType.policy,
                project="alpha",
                support_count=SKILL_MATURITY_THRESHOLD + 1,
            ),
            "policy b",
        ),
        (
            _item(
                "mem-20260610-100002-pol-c",
                MemoryType.policy,
                project="beta",
                support_count=SKILL_MATURITY_THRESHOLD + 1,
            ),
            "single project policy",
        ),
        (
            _item("mem-20260610-100003-skill", MemoryType.skill, project="alpha"),
            "existing skill",
        ),
    ]
    calls = []

    def fake_synthesize(group, store, *, existing_skill=None):
        calls.append((group, store, existing_skill))

    synthesized, errors = synthesize_mature_policy_skills(
        items,
        store=object(),
        synthesize=fake_synthesize,
    )

    assert synthesized == 1
    assert errors == []
    assert [policy.id for policy, _ in calls[0][0]] == [
        "mem-20260610-100000-pol-a",
        "mem-20260610-100001-pol-b",
    ]
    assert calls[0][2][0].id == "mem-20260610-100003-skill"


def test_collects_synthesize_errors_without_stopping_other_groups() -> None:
    items = [
        (
            _item(
                "mem-20260610-100010-alpha-a",
                MemoryType.policy,
                project="alpha",
                support_count=SKILL_MATURITY_THRESHOLD,
            ),
            "alpha a",
        ),
        (
            _item(
                "mem-20260610-100011-alpha-b",
                MemoryType.policy,
                project="alpha",
                support_count=SKILL_MATURITY_THRESHOLD,
            ),
            "alpha b",
        ),
        (
            _item(
                "mem-20260610-100012-beta-a",
                MemoryType.policy,
                project="beta",
                support_count=SKILL_MATURITY_THRESHOLD,
            ),
            "beta a",
        ),
        (
            _item(
                "mem-20260610-100013-beta-b",
                MemoryType.policy,
                project="beta",
                support_count=SKILL_MATURITY_THRESHOLD,
            ),
            "beta b",
        ),
    ]

    def fake_synthesize(group, store, *, existing_skill=None):
        if group[0][0].project == "alpha":
            raise RuntimeError("boom")

    synthesized, errors = synthesize_mature_policy_skills(
        items,
        store=object(),
        synthesize=fake_synthesize,
    )

    assert synthesized == 1
    assert errors == ["synthesize: boom"]
