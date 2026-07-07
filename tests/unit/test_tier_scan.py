from __future__ import annotations


def test_tier_scan_helpers_are_split_and_reexported() -> None:
    from agent_brain.memory.governance import tiering
    from agent_brain.memory.governance.tier_scan import scan_tiers, tier_distribution

    assert tiering.scan_tiers is scan_tiers
    assert tiering.tier_distribution is tier_distribution


def test_tier_distribution_includes_zero_counts_for_all_tiers() -> None:
    from agent_brain.memory.governance.tier_scan import tier_distribution
    from agent_brain.memory.governance.tiering import Tier

    assert tier_distribution([Tier.hot]) == {
        Tier.hot: 1,
        Tier.warm: 0,
        Tier.cold: 0,
    }
