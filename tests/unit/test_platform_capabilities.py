"""L2/L3 platform capability registry contracts."""

from __future__ import annotations


def test_platform_capability_registry_separates_l1_l2_l3_statuses() -> None:
    from agent_brain.platform.capability_levels import platform_capability_summary

    summary = platform_capability_summary()

    assert summary["levels"]["L1"]["status"] == "shipped"
    assert summary["levels"]["L2"]["status"] in {"foundation", "planned"}
    assert summary["levels"]["L3"]["status"] in {"foundation", "planned"}
    names = {capability["name"] for capability in summary["capabilities"]}
    assert {
        "local_shared_brain",
        "team_sync_contract",
        "semantic_contradiction_baseline",
        "enterprise_release_gate",
    } <= names
