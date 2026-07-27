"""Product platform capability levels.

This is a small executable registry for the L1/L2/L3 roadmap boundary. It keeps
Web/SDK/docs language anchored to what is shipped versus foundation/planned.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


Level = Literal["L1", "L2", "L3"]
CapabilityStatus = Literal["shipped", "foundation", "planned"]


@dataclass(frozen=True)
class PlatformCapability:
    name: str
    level: Level
    status: CapabilityStatus
    summary: str
    evidence: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


CAPABILITIES: tuple[PlatformCapability, ...] = (
    PlatformCapability(
        name="local_shared_brain",
        level="L1",
        status="shipped",
        summary="Local Markdown truth, rebuildable sqlite indexes, CLI/MCP/SDK/Web surfaces.",
        evidence=("agent_brain/memory/store/write_service.py", "web/api/routes/item_search.py"),
    ),
    PlatformCapability(
        name="team_sync_contract",
        level="L2",
        status="shipped",
        summary="End-to-end encrypted, tenant-isolated device sync preserves conflicts and keeps keys off the server.",
        evidence=(
            "agent_brain/product/encrypted_sync.py",
            "web/api/routes/sync.py",
        ),
    ),
    PlatformCapability(
        name="automatic_feedback_loop",
        level="L1",
        status="shipped",
        summary="Privacy-safe next-turn feedback updates retrieval trust signals without storing prompt text.",
        evidence=(
            "agent_brain/memory/governance/auto_feedback.py",
            "agent_runtime_kit/hooks/inject-context.sh",
        ),
    ),
    PlatformCapability(
        name="persistent_task_state_machine",
        level="L2",
        status="shipped",
        summary="Loop steps, dependencies, blockers, verification evidence, and handoff state persist across agents.",
        evidence=(
            "agent_brain/memory/loops/loop_store.py",
            "agent_brain/product/handoff.py",
        ),
    ),
    PlatformCapability(
        name="semantic_contradiction_baseline",
        level="L2",
        status="foundation",
        summary="Rule-based contradiction detection with optional embedding boost/advisory; LLM judge remains gated.",
        evidence=(
            "agent_brain/memory/governance/drift_contradictions.py",
            "tests/unit/test_semantic_contradiction.py",
        ),
    ),
    PlatformCapability(
        name="enterprise_release_gate",
        level="L3",
        status="foundation",
        summary="Benchmark and release gates are executable; enterprise policy plane remains planned.",
        evidence=("benchmarks/release_gate.py", "benchmarks/benchmark_relevance.py"),
    ),
    PlatformCapability(
        name="organization_rbac_console",
        level="L3",
        status="shipped",
        summary="Live organization RBAC and tenant-scoped operations views govern members and encrypted-sync devices.",
        evidence=(
            "web/organization_access.py",
            "web/api/routes/organizations.py",
        ),
    ),
)


def platform_capability_summary() -> dict[str, object]:
    levels: dict[str, dict[str, object]] = {}
    for level in ("L1", "L2", "L3"):
        caps = [cap for cap in CAPABILITIES if cap.level == level]
        status = "planned"
        if any(cap.status == "shipped" for cap in caps):
            status = "shipped"
        elif any(cap.status == "foundation" for cap in caps):
            status = "foundation"
        levels[level] = {
            "status": status,
            "capabilities": [cap.name for cap in caps],
        }
    return {
        "levels": levels,
        "capabilities": [cap.to_dict() for cap in CAPABILITIES],
    }


__all__ = ["CAPABILITIES", "PlatformCapability", "platform_capability_summary"]
