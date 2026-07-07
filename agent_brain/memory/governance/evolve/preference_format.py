"""Formatting helpers for inferred preference profiles."""

from __future__ import annotations

from agent_brain.memory.governance.evolve.preference_types import PreferenceProfile


def format_preference_profile(profile: PreferenceProfile) -> str:
    """Render profile as human-readable text for injection into agent context."""
    if not profile.signals and not profile.decision_patterns:
        return ""

    lines = ["## Inferred User Preferences", ""]

    if profile.signals:
        for sig in profile.signals[:10]:
            icon = "+" if sig.dimension != "avoidance" else "-"
            lines.append(f"- [{icon}] {sig.preference} (conf={sig.confidence:.2f}, n={sig.evidence_count})")
        lines.append("")

    if profile.decision_patterns:
        lines.append("### High-gain decisions:")
        for p in profile.decision_patterns[:5]:
            lines.append(f"- {p}")
        lines.append("")

    return "\n".join(lines)


__all__ = ["format_preference_profile"]
