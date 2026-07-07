"""Hermes governance, evolution, and statistics tool implementations."""

from __future__ import annotations

from typing import Any, Callable


ComponentsFactory = Callable[[], tuple[Any, Any, Any]]
EmbedderFactory = Callable[[], Any]
SuggestTagsFunc = Callable[[Any, Any, str], list[tuple[str, int]]]


def hub_drift_impl(
    components: ComponentsFactory,
    staleness_days: int = 180,
) -> dict[str, Any]:
    """Run drift detection on the brain pool."""
    from agent_brain.memory.governance.drift import DriftDetector

    store, _, _ = components()
    detector = DriftDetector(items_store=store, staleness_days=staleness_days)
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
            }
            for f in report.findings
        ],
    }


def hub_evolve_impl(
    components: ComponentsFactory,
    apply: bool = False,
) -> dict[str, Any]:
    """Run self-evolve engine on the brain pool."""
    from agent_brain.memory.governance.audit.scanner import SkillScanner
    from agent_brain.memory.governance.evolve.engine import EvolveEngine

    store, idx, _ = components()
    engine = EvolveEngine(
        items_store=store,
        scanner=SkillScanner(),
        dry_run=not apply,
        index=idx,
    )
    report = engine.evolve()
    return {
        "scanned_items": report.scanned_items,
        "total_proposals": len(report.proposals),
        "approved": len(report.approved_proposals),
        "executed": report.executed,
        "proposals": [
            {
                "action": p.action.value,
                "title": p.title,
                "confidence": p.confidence,
                "audit_passed": p.audit_passed,
            }
            for p in report.proposals
        ],
    }


def hub_stats_impl(
    components: ComponentsFactory,
    project: str | None = None,
) -> dict[str, Any]:
    """Return statistics and health score for the brain pool."""
    from agent_brain.memory.governance.drift import DriftDetector
    from agent_brain.memory.governance.pipeline import GovernancePipeline
    from agent_brain.observability import HealthScore, collect_stats

    store, _, _ = components()
    items = list(store.iter_all())
    stats = collect_stats(items, project_filter=project, skipped_count=store.last_scan.skipped_count)

    gov = GovernancePipeline(items_store=store)
    gov_report = gov.run()
    detector = DriftDetector(items_store=store)
    drift_report = detector.detect()

    issue_ids = {i.item_id for i in gov_report.issues} | {
        mid for f in drift_report.findings for mid in f.item_ids
    }

    health = HealthScore(
        total_items=stats.total_items,
        governance_issues=gov_report.total_issues,
        duplicates=gov_report.duplicates,
        noise=gov_report.noise,
        expired=gov_report.expired,
        low_quality=gov_report.low_quality,
        drift_findings=drift_report.total_findings,
        contradictions=drift_report.contradictions,
        stale=drift_report.stale,
        citation_rot=drift_report.citation_rot,
        drift_clusters=drift_report.drift_clusters,
        skipped_items=stats.skipped_count,
        items_with_issues=len(issue_ids),
    )

    return {
        "total_items": stats.total_items,
        "type_counts": stats.type_counts,
        "project_counts": stats.project_counts,
        "tag_counts": stats.tag_counts,
        "health_grade": health.grade,
        "healthy": health.healthy,
        "issue_rate": round(health.issue_rate, 3),
    }


def hub_govern_impl(
    components: ComponentsFactory,
    ttl_days: int = 90,
) -> dict[str, Any]:
    """Run governance pipeline on the brain pool."""
    from agent_brain.memory.governance.pipeline import GovernancePipeline

    store, _, _ = components()
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


def hub_tag_suggest_impl(
    components: ComponentsFactory,
    embedder_factory: EmbedderFactory,
    suggest_tags_func: SuggestTagsFunc,
    text: str,
    max_tags: int = 5,
) -> dict[str, Any]:
    """Suggest tags for content based on similar existing items."""
    _, idx, _ = components()
    embedder = embedder_factory()
    suggestions = suggest_tags_func(idx, embedder, text, max_tags=max_tags)
    return {
        "suggestions": [{"tag": tag, "frequency": freq} for tag, freq in suggestions],
    }
