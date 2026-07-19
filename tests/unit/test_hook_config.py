from agent_brain.agent_integrations.hook_config import (
    reconcile_managed_hub_hook_event,
    remove_managed_hub_hook_handlers,
)


def _entry(*commands: str, **metadata: object) -> dict:
    return {
        "matcher": "",
        **metadata,
        "hooks": [{"type": "command", "command": command} for command in commands],
    }


def _commands(entries: list) -> list[str]:
    return [
        hook["command"]
        for entry in entries
        if isinstance(entry, dict)
        for hook in entry.get("hooks", [])
        if isinstance(hook, dict) and isinstance(hook.get("command"), str)
    ]


def test_reconcile_removes_cross_checkout_duplicates_and_wrong_event_script() -> None:
    entries = [
        _entry("/tmp/amh-bench-x/agent_runtime_kit/hooks/inject-context.sh"),
        _entry(
            "~/.config/superpowers/worktrees/amh/old/agent_runtime_kit/hooks/session-end-signal.sh"
        ),
        _entry("/stable/agent_runtime_kit/hooks/inject-context.sh"),
    ]

    changed = reconcile_managed_hub_hook_event(
        entries,
        expected_script_path="/stable/agent_runtime_kit/hooks/inject-context.sh",
        expected_command="ENV=1 /stable/agent_runtime_kit/hooks/inject-context.sh",
        managed_script_names={"inject-context.sh", "session-end-signal.sh"},
        place_first=True,
    )

    assert changed is True
    assert _commands(entries) == ["ENV=1 /stable/agent_runtime_kit/hooks/inject-context.sh"]


def test_reconcile_preserves_foreign_handlers_metadata_and_relative_order() -> None:
    entries = [
        _entry("foreign-before", source="first"),
        _entry(
            "/old/agent_runtime_kit/hooks/inject-context.sh",
            "foreign-mixed",
            source="mixed",
        ),
        _entry("foreign-after", source="last"),
    ]

    reconcile_managed_hub_hook_event(
        entries,
        expected_script_path="/stable/agent_runtime_kit/hooks/inject-context.sh",
        expected_command="/stable/agent_runtime_kit/hooks/inject-context.sh",
        managed_script_names={"inject-context.sh", "session-end-signal.sh"},
        place_first=True,
    )

    assert _commands(entries) == [
        "/stable/agent_runtime_kit/hooks/inject-context.sh",
        "foreign-before",
        "foreign-mixed",
        "foreign-after",
    ]
    assert [entry.get("source") for entry in entries[1:]] == ["first", "mixed", "last"]


def test_stop_uses_first_managed_slot_without_reordering_foreign_entries() -> None:
    entries = [
        _entry("foreign-before"),
        _entry("/old/agent_runtime_kit/hooks/session-end-signal.sh"),
        _entry("foreign-after"),
    ]

    reconcile_managed_hub_hook_event(
        entries,
        expected_script_path="/stable/agent_runtime_kit/hooks/session-end-signal.sh",
        expected_command="/stable/agent_runtime_kit/hooks/session-end-signal.sh",
        managed_script_names={"inject-context.sh", "session-end-signal.sh"},
        place_first=False,
    )

    assert _commands(entries) == [
        "foreign-before",
        "/stable/agent_runtime_kit/hooks/session-end-signal.sh",
        "foreign-after",
    ]


def test_reconcile_is_byte_structure_idempotent() -> None:
    entries = [_entry("/stable/agent_runtime_kit/hooks/inject-context.sh")]
    kwargs = {
        "expected_script_path": "/stable/agent_runtime_kit/hooks/inject-context.sh",
        "expected_command": "/stable/agent_runtime_kit/hooks/inject-context.sh",
        "managed_script_names": {"inject-context.sh", "session-end-signal.sh"},
        "place_first": True,
    }

    assert reconcile_managed_hub_hook_event(entries, **kwargs) is False
    assert reconcile_managed_hub_hook_event(entries, **kwargs) is False


def test_remove_managed_handlers_keeps_unknown_and_foreign_commands() -> None:
    entries = [
        _entry("foreign", "/old/agent_runtime_kit/hooks/inject-context.sh"),
        _entry("/custom/agent_runtime_kit/hooks/future-event.sh"),
    ]

    removed = remove_managed_hub_hook_handlers(
        entries,
        managed_script_names={"inject-context.sh", "session-end-signal.sh"},
    )

    assert removed == 1
    assert _commands(entries) == [
        "foreign",
        "/custom/agent_runtime_kit/hooks/future-event.sh",
    ]


def test_remove_managed_handlers_preserves_empty_foreign_entry() -> None:
    entries = [{"matcher": "foreign", "hooks": []}]

    removed = remove_managed_hub_hook_handlers(
        entries,
        managed_script_names={"inject-context.sh", "session-end-signal.sh"},
    )

    assert removed == 0
    assert entries == [{"matcher": "foreign", "hooks": []}]


def test_reconcile_rejects_unmanaged_expected_script() -> None:
    entries = [_entry("foreign")]

    try:
        reconcile_managed_hub_hook_event(
            entries,
            expected_script_path="/custom/future-event.sh",
            expected_command="/custom/future-event.sh",
            managed_script_names={"inject-context.sh", "session-end-signal.sh"},
            place_first=True,
        )
    except ValueError as exc:
        assert "not managed" in str(exc)
    else:
        raise AssertionError("an unmanaged canonical script must be rejected")

    assert _commands(entries) == ["foreign"]
