"""MCP governance tier tools. Bodies moved verbatim from mcp_server.py (design §6.2)."""
from __future__ import annotations

from agent_brain.interfaces.mcp.tools.governance_batch import batch_archive, batch_confirm
from agent_brain.interfaces.mcp.tools._shared import *  # noqa: F401,F403


def audit_skill(path: str) -> dict[str, Any]:
    """Audit a skill file or directory for prompt-injection / tool-abuse patterns.

    Returns finding counts by severity plus the full finding list. Passed = no
    critical or high severity issues.

    WHEN TO USE
    -----------
    Call this BEFORE the user installs/imports any third-party skill, prompt,
    or agent file. Also call before writing a `skill`-type memory whose body
    comes from external/untrusted source. Critical/high findings should block
    installation; medium/low should be surfaced to the user for review.
    """
    target = Path(path).expanduser()
    if not target.exists():
        raise ValueError(f"path does not exist: {target}")
    scanner = SkillScanner(rules=load_builtin_rules())
    # Single-file scans go through scan_directory with a name glob to reuse
    # the same AuditReport aggregation path used by the CLI.
    report = (
        scanner.scan_directory(target)
        if target.is_dir()
        else scanner.scan_directory(target.parent, glob=target.name)
    )
    return {
        "scanned_files": report.scanned_files,
        "total_findings": report.total_findings,
        "critical": report.critical,
        "high": report.high,
        "medium": report.medium,
        "low": report.low,
        "passed": report.passed,
        "findings": [
            {
                "rule_id": f.rule_id,
                "severity": f.severity,
                "category": f.category,
                "file": f.file_path,
                "line": f.line_number,
                "description": f.description,
                "remediation": f.remediation,
            }
            for f in report.findings
        ],
    }


def audit_outbound(since_days: int = 30) -> list:
    """List outbound audit events from the local machine within the time window.

    WHEN TO USE
    -----------
    Privacy / data-egress review: "what did agents send out lately". Typical
    triggers: user asks about sent data, suspicious behavior, security audit,
    or before sharing the brain with a third party.
    """
    events = list_outbound_events(since_days=since_days)
    return [
        {
            "timestamp": e.timestamp,
            "destination": e.destination,
            "payload_type": e.payload_type,
            "size_bytes": e.size_bytes,
            "source_tool": e.source_tool,
            "approved_by": e.approved_by,
        }
        for e in events
    ]


def drift_check(
    staleness_days: int = 180,
    check_urls: bool = False,
) -> dict[str, Any]:
    """Run drift detector on the brain pool.

    Checks for contradictions, staleness, citation rot, and drift clusters.
    Returns a summary plus individual findings.

    WHEN TO USE
    -----------
    Call this when `brain_stats` shows a health-grade regression, OR weekly
    as routine hygiene, OR whenever the user asks "is my brain getting stale".
    A `contradictions` cluster usually means two `policy` items disagree and
    one needs to `supersede` the other.

    CHAIN
    -----
    Findings with `drift_type=stale` are good candidates for
    `confirm_memory` (if still valid) or `link_memories(…supersedes…)` (if
    replaced). Contradictions warrant user attention before auto-fix.
    """
    from agent_brain.memory.governance.drift import DriftDetector

    store, _, _ = _components()
    detector = DriftDetector(
        items_store=store,
        staleness_days=staleness_days,
        check_urls=check_urls,
    )
    report = detector.detect()
    return {
        "scanned_items": report.scanned_items,
        "total_findings": report.total_findings,
        "clean": report.clean,
        "contradictions": report.contradictions,
        "stale": report.stale,
        "citation_rot": report.citation_rot,
        "drift_clusters": report.drift_clusters,
        "findings": [
            {
                "drift_type": f.drift_type.value,
                "item_ids": f.item_ids,
                "confidence": f.confidence,
                "description": f.description,
                "evidence": f.evidence,
            }
            for f in report.findings
        ],
    }


def govern(
    ttl_days: int = 90,
) -> dict[str, Any]:
    """Run governance pipeline on the brain pool.

    Checks for duplicates, noise, expired items, and quality issues.
    Returns a summary plus individual findings.

    WHEN TO USE
    -----------
    Routine hygiene (weekly) or when `brain_stats.issue_rate > 0.1`.
    Pair with `drift_check` for full health sweep. Duplicates are the most
    actionable finding — surface them to the user with a merge suggestion
    (`link_memories` + `update_memory` to consolidate).
    """
    from agent_brain.memory.governance.pipeline import GovernancePipeline

    store, _, _ = _components()
    pipeline = GovernancePipeline(items_store=store, ttl_days=ttl_days)
    report = pipeline.run()
    return {
        "scanned_items": report.scanned_items,
        "total_issues": report.total_issues,
        "healthy": report.healthy,
        "duplicates": report.duplicates,
        "noise": report.noise,
        "expired": report.expired,
        "low_quality": report.low_quality,
        "issues": [
            {
                "item_id": i.item_id,
                "issue_type": i.issue_type,
                "severity": i.severity,
                "description": i.description,
                "suggestion": i.suggestion,
            }
            for i in report.issues
        ],
    }


def register(mcp) -> None:
    """Register this tier's tools on the FastMCP instance (called by server.register_all)."""
    mcp.tool()(audit_skill)
    mcp.tool()(audit_outbound)
    mcp.tool()(drift_check)
    mcp.tool()(govern)
    mcp.tool()(batch_confirm)
    mcp.tool()(batch_archive)


__all__ = ['audit_skill', 'audit_outbound', 'drift_check', 'govern', 'batch_confirm', 'batch_archive']
