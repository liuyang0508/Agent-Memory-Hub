"""MCP evolve tier tools. Bodies moved verbatim from mcp_server.py (design §6.2)."""
from __future__ import annotations

from agent_brain.interfaces.mcp.tools._shared import *  # noqa: F401,F403


def evolve_memory(
    apply: bool = False,
    decay_archive_threshold: float = 0.1,
) -> dict[str, Any]:
    """Run self-evolve engine on the brain pool.

    Analyzes items and proposes consolidation, promotion, archiving,
    and skill generation. When apply=True, approved proposals are executed.

    WHEN TO USE
    -----------
    Periodic (weekly/monthly) batch maintenance. Always run with `apply=False`
    first to review proposals, then `apply=True` after the user approves.
    Especially valuable when many low-confidence drafts have accumulated:
    the engine will promote stable ones, archive decayed ones, and propose
    `skill` extractions from recurring `episode`/`decision` clusters.

    DO NOT
    ------
    Run with `apply=True` without showing the proposals first — archive/
    consolidate is destructive (creates supersedes chains).
    """
    from agent_brain.memory.governance.evolve.engine import EvolveEngine

    store, idx, _ = _components()
    scanner = SkillScanner()
    engine = EvolveEngine(
        items_store=store,
        scanner=scanner,
        dry_run=not apply,
        index=idx,
        decay_archive_threshold=decay_archive_threshold,
    )
    report = engine.evolve()
    return {
        "scanned_items": report.scanned_items,
        "total_proposals": len(report.proposals),
        "audit_blocked": report.audit_blocked,
        "approved": len(report.approved_proposals),
        "executed": report.executed,
        "proposals": [
            {
                "action": p.action.value,
                "item_ids": p.item_ids,
                "title": p.title,
                "confidence": p.confidence,
                "audit_passed": p.audit_passed,
            }
            for p in report.proposals
        ],
    }


def register(mcp) -> None:
    """Register this tier's tools on the FastMCP instance (called by server.register_all)."""
    mcp.tool()(evolve_memory)


__all__ = ['evolve_memory']
