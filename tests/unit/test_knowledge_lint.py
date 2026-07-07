from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.store.items_store import ItemsStore


NOW = datetime(2026, 6, 28, 9, 0, tzinfo=timezone.utc)


def _item(
    suffix: str,
    type_: str,
    title: str,
    summary: str,
    *,
    hours_ago: int = 0,
    project: str | None = "agent-memory-hub",
    tags: list[str] | None = None,
    refs: dict | None = None,
    validity: dict | None = None,
    superseded_by: str | None = None,
    support_count: int = 0,
    contradict_count: int = 0,
    gain_score: float = 0.0,
) -> MemoryItem:
    return MemoryItem.model_validate({
        "id": f"mem-20260628-090000-{suffix}",
        "type": type_,
        "created_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
        "title": title,
        "summary": summary,
        "project": project,
        "tags": tags or [],
        "refs": refs or {},
        "validity": validity or {},
        "superseded_by": superseded_by,
        "support_count": support_count,
        "contradict_count": contradict_count,
        "gain_score": gain_score,
    })


def _store(tmp_brain_dir) -> ItemsStore:
    return ItemsStore(tmp_brain_dir / "items")


def _write(store: ItemsStore, item: MemoryItem, body: str = "body") -> MemoryItem:
    store.write(item, body)
    return item


def _finding_types(report) -> list[str]:
    return [finding.issue_type for finding in report.findings]


def test_source_missing_for_fact_without_refs(tmp_brain_dir) -> None:
    from agent_brain.memory.governance.knowledge_lint import KnowledgeLinter

    store = _store(tmp_brain_dir)
    item = _write(store, _item("source-missing", "fact", "Unsourced fact", "No refs"))

    report = KnowledgeLinter(store).run()

    assert report.total_items == 1
    assert report.total_findings == 1
    assert report.counts_by_type == {"source_missing": 1}
    finding = report.findings[0]
    assert finding.item_id == item.id
    assert finding.issue_type == "source_missing"
    assert finding.severity == "warning"
    assert finding.reasons == ("missing_source_refs",)
    assert finding.suggested_action == "add_source_reference"


def test_sourced_fact_is_not_source_missing(tmp_brain_dir) -> None:
    from agent_brain.memory.governance.knowledge_lint import KnowledgeLinter

    store = _store(tmp_brain_dir)
    _write(
        store,
        _item(
            "sourced-fact",
            "fact",
            "Sourced fact",
            "Has refs",
            refs={"urls": ["https://example.test/source"]},
        ),
    )

    report = KnowledgeLinter(store).run()

    assert report.total_items == 1
    assert report.findings == []


def test_decision_without_refs_is_source_missing(tmp_brain_dir) -> None:
    from agent_brain.memory.governance.knowledge_lint import KnowledgeLinter

    store = _store(tmp_brain_dir)
    _write(store, _item("decision-source", "decision", "Use SSE", "Decision lacks refs"))

    report = KnowledgeLinter(store).run()

    assert _finding_types(report) == ["source_missing"]


def test_episode_without_refs_is_not_source_missing(tmp_brain_dir) -> None:
    from agent_brain.memory.governance.knowledge_lint import KnowledgeLinter

    store = _store(tmp_brain_dir)
    _write(store, _item("episode-no-source", "episode", "Debug episode", "No refs"))

    report = KnowledgeLinter(store).run()

    assert report.findings == []


def test_orphan_link_reports_missing_refs_mems(tmp_brain_dir) -> None:
    from agent_brain.memory.governance.knowledge_lint import KnowledgeLinter

    store = _store(tmp_brain_dir)
    target = _write(store, _item("target", "episode", "Existing target", "Exists"))
    source = _write(
        store,
        _item(
            "source",
            "episode",
            "Source item",
            "Links to one missing memory",
            refs={"mems": [target.id, "mem-20260628-090000-missing"]},
        ),
    )

    report = KnowledgeLinter(store).run()

    assert report.total_items == 2
    assert report.total_findings == 1
    finding = report.findings[0]
    assert finding.item_id == source.id
    assert finding.issue_type == "orphan_link"
    assert finding.reasons == ("missing_refs_mems",)
    assert finding.evidence == ("missing_ref:mem-20260628-090000-missing",)


def test_project_filter_scans_only_matching_items(tmp_brain_dir) -> None:
    from agent_brain.memory.governance.knowledge_lint import KnowledgeLinter

    store = _store(tmp_brain_dir)
    alpha = _write(store, _item("alpha", "fact", "Alpha fact", "No refs", project="alpha"))
    _write(store, _item("beta", "fact", "Beta fact", "No refs", project="beta"))

    report = KnowledgeLinter(store).run(project="alpha")

    assert report.total_items == 1
    assert [finding.item_id for finding in report.findings] == [alpha.id]


def test_project_filter_uses_global_item_ids_for_refs_mems(tmp_brain_dir) -> None:
    from agent_brain.memory.governance.knowledge_lint import KnowledgeLinter

    store = _store(tmp_brain_dir)
    beta = _write(
        store,
        _item("beta-target", "episode", "Beta target", "Existing target", project="beta"),
    )
    _write(
        store,
        _item(
            "alpha-source",
            "episode",
            "Alpha source",
            "References beta target",
            project="alpha",
            refs={"mems": [beta.id]},
        ),
    )

    report = KnowledgeLinter(store).run(project="alpha")

    assert report.total_items == 1
    assert report.findings == []


def test_issue_type_filter_returns_only_requested_issue(tmp_brain_dir) -> None:
    from agent_brain.memory.governance.knowledge_lint import KnowledgeLinter

    store = _store(tmp_brain_dir)
    _write(store, _item("missing-source", "fact", "Missing source", "No refs"))
    _write(
        store,
        _item(
            "orphan",
            "episode",
            "Orphan source",
            "Broken link",
            refs={"mems": ["mem-20260628-090000-missing"]},
        ),
    )

    report = KnowledgeLinter(store).run(issue_type="orphan_link")

    assert report.total_findings == 1
    assert _finding_types(report) == ["orphan_link"]


def test_limit_caps_returned_findings(tmp_brain_dir) -> None:
    from agent_brain.memory.governance.knowledge_lint import KnowledgeLinter

    store = _store(tmp_brain_dir)
    _write(store, _item("first", "fact", "First fact", "No refs"))
    _write(store, _item("second", "decision", "Second decision", "No refs"))

    report = KnowledgeLinter(store).run(limit=1)

    assert report.total_findings == 1
    assert len(report.findings) == 1


def test_negative_limit_is_rejected(tmp_brain_dir) -> None:
    from agent_brain.memory.governance.knowledge_lint import KnowledgeLinter

    store = _store(tmp_brain_dir)

    with pytest.raises(ValueError, match="limit must be non-negative"):
        KnowledgeLinter(store).run(limit=-1)


def test_report_to_dict_is_json_ready(tmp_brain_dir) -> None:
    from agent_brain.memory.governance.knowledge_lint import KnowledgeLinter

    store = _store(tmp_brain_dir)
    _write(store, _item("json-ready", "fact", "JSON fact", "No refs"))

    payload = KnowledgeLinter(store).run().to_dict()

    assert payload["total_items"] == 1
    assert payload["counts_by_type"] == {"source_missing": 1}
    assert payload["findings"][0]["reasons"] == ["missing_source_refs"]


def test_report_to_dict_returns_counts_copy(tmp_brain_dir) -> None:
    from agent_brain.memory.governance.knowledge_lint import KnowledgeLinter

    store = _store(tmp_brain_dir)
    _write(store, _item("copy-counts", "fact", "Copy counts", "No refs"))

    report = KnowledgeLinter(store).run()
    payload = report.to_dict()
    payload["counts_by_type"]["source_missing"] = 99

    assert report.counts_by_type == {"source_missing": 1}


def test_stale_runtime_memory_produces_stale_finding(tmp_brain_dir) -> None:
    from agent_brain.memory.governance.knowledge_lint import KnowledgeLinter
    from agent_brain.memory.validity import ValidityEvaluator

    store = _store(tmp_brain_dir)
    item = _write(
        store,
        _item(
            "stale-runtime",
            "signal",
            "Browser unavailable",
            "Ghostty Operation not permitted; browser unavailable",
            hours_ago=49,
            tags=["browser", "runtime"],
        ),
    )

    report = KnowledgeLinter(store, evaluator=ValidityEvaluator(now=NOW)).run()

    finding = next(f for f in report.findings if f.issue_type == "stale")
    assert finding.item_id == item.id
    assert finding.severity == "warning"
    assert finding.reasons == ("ttl_expired",)
    assert "ttl_hours:48" in finding.evidence
    assert finding.suggested_action == "refresh_or_mark_historical"


def test_superseded_memory_produces_superseded_finding(tmp_brain_dir) -> None:
    from agent_brain.memory.governance.knowledge_lint import KnowledgeLinter
    from agent_brain.memory.validity import ValidityEvaluator

    store = _store(tmp_brain_dir)
    item = _write(
        store,
        _item(
            "superseded",
            "decision",
            "Old decision",
            "Use old behavior",
            refs={"urls": ["https://example.test/decision"]},
            superseded_by="mem-20260628-090000-new-decision",
        ),
    )

    report = KnowledgeLinter(store, evaluator=ValidityEvaluator(now=NOW)).run()

    finding = next(f for f in report.findings if f.issue_type == "superseded")
    assert finding.item_id == item.id
    assert finding.severity == "info"
    assert finding.suggested_action == "use_superseding_memory"


def test_review_required_memory_produces_review_required_finding(tmp_brain_dir) -> None:
    from agent_brain.memory.governance.knowledge_lint import KnowledgeLinter
    from agent_brain.memory.validity import ValidityEvaluator

    store = _store(tmp_brain_dir)
    item = _write(
        store,
        _item(
            "needs-review",
            "episode",
            "Browser might work",
            "No verification exists",
            tags=["needs-review", "unverified-boundary"],
        ),
    )

    report = KnowledgeLinter(store, evaluator=ValidityEvaluator(now=NOW)).run()

    finding = next(f for f in report.findings if f.issue_type == "review_required")
    assert finding.item_id == item.id
    assert finding.severity == "warning"
    assert "tag:needs-review" in finding.evidence
    assert finding.suggested_action == "review_approve_or_reject"


def test_negative_feedback_memory_produces_contradicted_finding(tmp_brain_dir) -> None:
    from agent_brain.memory.governance.knowledge_lint import KnowledgeLinter
    from agent_brain.memory.validity import ValidityEvaluator

    store = _store(tmp_brain_dir)
    item = _write(
        store,
        _item(
            "contradicted",
            "episode",
            "Rejected workaround",
            "User rejected this repeatedly",
            contradict_count=3,
            gain_score=-0.6,
            support_count=0,
        ),
    )

    report = KnowledgeLinter(store, evaluator=ValidityEvaluator(now=NOW)).run()

    finding = next(f for f in report.findings if f.issue_type == "contradicted")
    assert finding.item_id == item.id
    assert finding.severity == "error"
    assert "negative_feedback" in finding.reasons
    assert finding.suggested_action == "inspect_feedback_and_supersede"


def test_scope_mismatch_memory_produces_scope_mismatch_finding(tmp_brain_dir) -> None:
    from agent_brain.memory.governance.knowledge_lint import KnowledgeLinter
    from agent_brain.memory.validity import ValidityEvaluator

    store = _store(tmp_brain_dir)
    item = _write(
        store,
        _item(
            "scope-mismatch",
            "signal",
            "Browser unavailable",
            "Browser unavailable in another repo",
            tags=["browser", "runtime"],
            validity={"cwd": "/repo/other", "adapter": "codex"},
        ),
    )

    report = KnowledgeLinter(
        store,
        evaluator=ValidityEvaluator(now=NOW),
        current_scope={"cwd": "/repo/current", "adapter": "codex"},
    ).run()

    finding = next(f for f in report.findings if f.issue_type == "scope_mismatch")
    assert finding.item_id == item.id
    assert finding.severity == "warning"
    assert "scope_mismatch:cwd" in finding.reasons
    assert finding.suggested_action == "revalidate_in_current_scope"
