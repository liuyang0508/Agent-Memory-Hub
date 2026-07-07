from __future__ import annotations

import pytest


def test_build_update_fields_syncs_type_and_decay_class() -> None:
    from agent_brain.interfaces.mcp.tools.mutation_updates import build_update_fields

    updates = build_update_fields(
        title="New title",
        summary=None,
        tags=["mcp", "update"],
        type="signal",
        confidence=0.42,
        project="agent-memory-hub",
    )

    assert updates == {
        "title": "New title",
        "tags": ["mcp", "update"],
        "type": "signal",
        "retention.decay_class": "ephemeral",
        "confidence": 0.42,
        "project": "agent-memory-hub",
    }


def test_build_update_fields_rejects_invalid_type() -> None:
    from agent_brain.interfaces.mcp.tools.mutation_updates import build_update_fields

    with pytest.raises(ValueError, match="invalid type"):
        build_update_fields(type="bogus")
