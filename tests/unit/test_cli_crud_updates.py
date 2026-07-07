from __future__ import annotations


def test_build_cli_update_fields_merges_added_tags_and_filters_empty_fields() -> None:
    from agent_brain.interfaces.cli.crud_updates import build_cli_update_fields

    updates = build_cli_update_fields(
        title="Renamed",
        summary=None,
        add_tags="new-tag, extra ,infra",
        current_tags=["infra", "existing"],
        confidence=0.88,
        project=None,
    )

    assert updates["title"] == "Renamed"
    assert updates["confidence"] == 0.88
    assert set(updates["tags"]) == {"infra", "existing", "new-tag", "extra"}
    assert "summary" not in updates
    assert "project" not in updates


def test_build_cli_update_fields_returns_empty_when_no_changes() -> None:
    from agent_brain.interfaces.cli.crud_updates import build_cli_update_fields

    assert build_cli_update_fields(current_tags=["existing"]) == {}
