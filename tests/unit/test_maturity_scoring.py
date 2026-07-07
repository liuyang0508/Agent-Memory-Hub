from __future__ import annotations

from datetime import datetime, timezone

from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def _item(**kw) -> MemoryItem:
    data = {
        "id": "mem-20260618-120000-maturity-test",
        "type": MemoryType.fact,
        "created_at": datetime(2026, 6, 18, tzinfo=timezone.utc),
        "title": "Maturity test",
        "summary": "Maturity locator",
        "tags": ["maturity"],
    }
    data.update(kw)
    return MemoryItem(**data)


def test_maturity_score_keeps_unproven_l0_raw() -> None:
    from agent_brain.memory.governance.maturity_scoring import score_maturity

    result = score_maturity(_item(abstraction="L0", confidence=0.5))

    assert result.maturity == "raw"
    assert result.abstraction == "L0"
    assert result.score < 0.45
    assert "no_source_refs" in result.reasons


def test_maturity_score_promotes_evidence_backed_decision_to_consolidated() -> None:
    from agent_brain.memory.governance.maturity_scoring import score_maturity

    result = score_maturity(_item(
        type=MemoryType.decision,
        abstraction="L0",
        confidence=0.85,
        refs={"files": ["docs/architecture.md"], "mems": ["mem-20260618-010101-source"]},
        support_count=4,
        gain_score=0.3,
        context_views={
            "locator": "Decision locator",
            "overview": "Decision overview with reusable governance context.",
            "detail_uri": "memory://items/mem-20260618-120000-maturity-test/body",
        },
    ))

    assert result.maturity == "consolidated"
    assert result.abstraction == "L1"
    assert result.score >= 0.65
    assert "direct_source_refs" in result.reasons
    assert "positive_feedback" in result.reasons


def test_maturity_score_rewards_validation_or_test_evidence() -> None:
    from agent_brain.memory.governance.maturity_scoring import score_maturity

    without_validation = score_maturity(_item(
        refs={"files": ["docs/architecture.md"]},
        confidence=0.65,
    ))
    with_validation = score_maturity(_item(
        refs={"files": ["tests/unit/test_example.py", "docs/evaluation/latest-memory-benchmark-report.zh.md"]},
        confidence=0.65,
    ))

    assert with_validation.score > without_validation.score
    assert "validation_evidence" in with_validation.reasons


def test_maturity_score_allows_skill_only_for_skill_type_or_l2() -> None:
    from agent_brain.memory.governance.maturity_scoring import score_maturity

    result = score_maturity(_item(
        type=MemoryType.skill,
        abstraction="L2",
        confidence=0.95,
        refs={"files": ["agent_runtime_kit/skills/example/SKILL.md"]},
        support_count=8,
        gain_score=0.6,
        context_views={
            "locator": "Skill locator",
            "overview": "Skill overview with reusable steps and evidence.",
            "detail_uri": "memory://items/mem-20260618-120000-maturity-test/body",
        },
    ))

    assert result.maturity == "skill"
    assert result.abstraction == "L2"
    assert result.score >= 0.80


def test_maturity_score_penalizes_contradictions_and_stale_scope() -> None:
    from agent_brain.memory.governance.maturity_scoring import score_maturity

    result = score_maturity(_item(
        confidence=0.9,
        refs={"files": ["docs/architecture.md"]},
        support_count=4,
        contradict_count=5,
        gain_score=-0.2,
        tags=["maturity", "stale-state"],
    ))

    assert result.maturity == "raw"
    assert result.score < 0.65
    assert "contradiction_penalty" in result.reasons
    assert "stale_scope_penalty" in result.reasons
