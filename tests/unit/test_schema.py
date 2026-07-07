from datetime import datetime

import pytest
from pydantic import ValidationError


def test_memory_schema_enums_are_split_and_reexported():
    from agent_brain.contracts import memory_item
    from agent_brain.contracts.memory_enums import (
        DECAY_HALF_LIFE_DAYS,
        TYPE_TO_DECAY_CLASS,
        AbstractionLayer,
        DecayClass,
        MemoryType,
        Sensitivity,
    )

    assert memory_item.MemoryType is MemoryType
    assert memory_item.Sensitivity is Sensitivity
    assert memory_item.AbstractionLayer is AbstractionLayer
    assert memory_item.DecayClass is DecayClass
    assert memory_item.TYPE_TO_DECAY_CLASS is TYPE_TO_DECAY_CLASS
    assert memory_item.DECAY_HALF_LIFE_DAYS is DECAY_HALF_LIFE_DAYS


def test_minimal_valid_item():
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Sensitivity

    item = MemoryItem(
        id="mem-20260519-100000-test",
        type=MemoryType.fact,
        created_at=datetime.fromisoformat("2026-05-19T10:00:00+08:00"),
        title="测试事实",
        summary="一个最小有效 item",
    )
    assert item.sensitivity == Sensitivity.internal
    assert item.tags == []
    assert item.refs.files == []
    assert item.confidence == 0.7
    assert item.retention.decay_class == "fact"
    assert item.retention.access_count == 0


def test_memory_item_validity_scope_round_trips():
    from agent_brain.contracts.memory_item import MemoryItem

    item = MemoryItem.model_validate({
        "id": "mem-20260612-020000-validity-scope",
        "type": "signal",
        "created_at": "2026-06-12T02:00:00+08:00",
        "title": "Browser status",
        "summary": "Browser was unavailable in one repo",
        "validity": {
            "cwd": "/repo/a",
            "repo": "agent-memory-hub",
            "branch": "main",
            "os": "linux",
            "adapter": "codex",
        },
    })

    dumped = item.model_dump(mode="json")
    assert dumped["validity"]["cwd"] == "/repo/a"
    assert dumped["validity"]["repo"] == "agent-memory-hub"
    assert dumped["validity"]["branch"] == "main"
    assert dumped["validity"]["os"] == "linux"
    assert dumped["validity"]["adapter"] == "codex"


def test_memory_item_context_views_default_from_summary_and_id():
    from agent_brain.contracts.memory_item import MemoryItem

    item = MemoryItem.model_validate({
        "id": "mem-20260615-010000-context-views-default",
        "type": "fact",
        "created_at": "2026-06-15T01:00:00+00:00",
        "title": "Context loading defaults",
        "summary": "Summary should become the locator view for old items.",
    })

    assert item.context_views.locator == "Summary should become the locator view for old items."
    assert item.context_views.overview == ""
    assert item.context_views.detail_uri == "memory://items/mem-20260615-010000-context-views-default/body"
    assert item.maturity == "raw"


def test_memory_item_maturity_maps_from_existing_abstraction_axis():
    from agent_brain.contracts.memory_item import MemoryItem

    item = MemoryItem.model_validate({
        "id": "mem-20260615-010001-maturity-from-abstraction",
        "type": "skill",
        "created_at": "2026-06-15T01:00:01+00:00",
        "title": "Mature skill",
        "summary": "Existing L2 items should read as skill maturity.",
        "abstraction": "L2",
    })

    assert item.abstraction == "L2"
    assert item.maturity == "skill"


def test_invalid_type_rejected():
    from agent_brain.contracts.memory_item import MemoryItem

    with pytest.raises(ValidationError, match="type"):
        MemoryItem(
            id="mem-20260519-100000-x",
            type="not-a-real-type",
            created_at=datetime.now(),
            title="x",
            summary="x",
        )


def test_id_pattern_enforced():
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType

    with pytest.raises(ValidationError, match="id"):
        MemoryItem(
            id="not-an-id",
            type=MemoryType.fact,
            created_at=datetime.now(),
            title="x",
            summary="x",
        )


def test_v05_historical_item_loads():
    """v0.5 写的 item 必须能用 v1 schema 加载（无破坏性变更）。"""
    from agent_brain.contracts.memory_item import MemoryItem

    historical_yaml = {
        "id": "mem-20260515-142857-test",
        "schema_version": "0.2",
        "type": "decision",
        "created_at": "2026-05-15T14:28:57+0800",
        "agent": "claude-code",
        "session": None,
        "project": "agent-memory-hub",
        "tenant_id": None,
        "auth_context": None,
        "tags": ["x", "y"],
        "sensitivity": "internal",
        "title": "历史 item",
        "summary": "v0.5 写的",
        "refs": {"files": [], "urls": [], "mems": [], "commits": []},
    }
    item = MemoryItem.model_validate(historical_yaml)
    assert item.schema_version == "0.2"  # preserved from input for backwards compat
    assert item.tenant_id is None
    # v0.2 → v0.3 migration fills defaults
    assert item.confidence == 0.7
    assert item.retention.decay_class == "decision"  # type=decision → decay_class=decision
    assert item.retention.access_count == 0


def test_legacy_durable_decay_class_maps_to_current_type_default():
    from agent_brain.contracts.memory_item import MemoryItem

    fact_item = MemoryItem.model_validate(
        {
            "id": "mem-20260528-205500-decimal-json-audit-gotcha",
            "schema_version": "0.3",
            "type": "fact",
            "created_at": "2026-05-28T20:55:00.000000+08:00",
            "title": "legacy durable fact",
            "summary": "old item with durable retention",
            "retention": {"decay_class": "durable", "access_count": 0},
        }
    )
    artifact_item = MemoryItem.model_validate(
        {
            "id": "mem-20260528-205501-credit-risk-audit-and-reports-shipped",
            "schema_version": "0.3",
            "type": "artifact",
            "created_at": "2026-05-28T20:55:01.000000+08:00",
            "title": "legacy durable artifact",
            "summary": "old artifact with durable retention",
            "retention": {"decay_class": "durable", "access_count": 0},
        }
    )

    assert fact_item.retention.decay_class == "fact"
    assert artifact_item.retention.decay_class == "architecture"
