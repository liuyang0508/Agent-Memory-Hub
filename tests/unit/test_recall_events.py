from __future__ import annotations

from pathlib import Path


def test_record_gap_appends_jsonl_without_memory_body(tmp_path: Path) -> None:
    from agent_brain.memory.governance.recall_events import iter_gap_records, record_gap

    record = record_gap(
        tmp_path,
        query="帮我打开浏览器",
        reason="manual_revalidation",
        injected_ids=["mem-a"],
        rejected_ids=["mem-a"],
        evidence=["user said historical browser limit was already fixed"],
        adapter="codex",
        session_id="s1",
        cwd="/repo",
    )

    rows = list(iter_gap_records(tmp_path))
    raw = (tmp_path / "runtime" / "recall-gaps.jsonl").read_text(encoding="utf-8")

    assert rows == [record]
    assert record.normalized_query == "帮我打开浏览器"
    assert "memory body" not in raw
    assert "帮我打开浏览器" in raw


def test_record_task_outcome_round_trips_low_confidence_implicit_signal(
    tmp_path: Path,
) -> None:
    from agent_brain.memory.governance.recall_events import iter_task_outcomes, record_task_outcome

    outcome = record_task_outcome(
        tmp_path,
        task_id="task-1",
        question="继续修复 recall drift",
        outcome="success",
        feedback_signals=["implicit_continue"],
        value_tags=["workflow_pattern"],
        confidence=0.35,
        injected_ids=["mem-a"],
        adopted_ids=[],
        rejected_ids=[],
    )

    rows = list(iter_task_outcomes(tmp_path))

    assert rows == [outcome]
    assert outcome.normalized_question == "继续修复 recall drift"
    assert outcome.confidence == 0.35


def test_record_task_outcome_feedback_application_round_trips(tmp_path: Path) -> None:
    from agent_brain.memory.governance.recall_events import (
        iter_task_outcome_feedback_applications,
        record_task_outcome_feedback_application,
    )

    application = record_task_outcome_feedback_application(
        tmp_path,
        outcome_id="out-20260612-explicit",
        applied=True,
        adopted_ids=["mem-a"],
        rejected_ids=["mem-b"],
        skipped_reason=None,
        adapter="codex",
        session_id="sess-1",
    )

    rows = list(iter_task_outcome_feedback_applications(tmp_path))

    assert rows == [application]
    assert application.applied is True
    assert application.adopted_ids == ("mem-a",)
    assert application.rejected_ids == ("mem-b",)


def test_iterators_skip_malformed_jsonl_rows(tmp_path: Path) -> None:
    from agent_brain.memory.governance.recall_events import iter_gap_records, record_gap

    record_gap(tmp_path, query="q", reason="empty_recall")
    path = tmp_path / "runtime" / "recall-gaps.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write("not-json\n")

    rows = list(iter_gap_records(tmp_path))

    assert len(rows) == 1
    assert rows[0].query == "q"


def test_recall_drift_report_summarizes_gap_and_outcome_jsonl(tmp_path: Path) -> None:
    from agent_brain.memory.governance.recall_drift_report import build_recall_drift_report
    from agent_brain.memory.governance.recall_events import (
        record_gap,
        record_task_outcome,
        record_task_outcome_feedback_application,
    )

    record_gap(tmp_path, query="q1", reason="manual_revalidation")
    record_gap(tmp_path, query="q2", reason="only_rejected")
    record_gap(
        tmp_path,
        query="q3",
        reason="partial_candidates_rejected",
        injected_ids=["mem-keep"],
        rejected_ids=["mem-drop"],
        evidence=["mem-drop:missing_source"],
    )
    record_gap(
        tmp_path,
        query="验证",
        reason="query_not_injectable",
        evidence=["query_signal:too_weak", "terms=验证"],
    )
    record_task_outcome(
        tmp_path,
        task_id="task-1",
        question="q1",
        outcome="success",
        feedback_signals=["implicit_continue"],
        value_tags=["workflow_pattern"],
        confidence=0.35,
    )
    record_task_outcome(
        tmp_path,
        task_id="task-2",
        question="q2",
        outcome="corrected",
        feedback_signals=["user_correction"],
        value_tags=["high_risk"],
        confidence=0.8,
    )
    record_task_outcome_feedback_application(
        tmp_path,
        outcome_id="out-applied",
        applied=True,
        adopted_ids=["mem-a"],
    )
    record_task_outcome_feedback_application(
        tmp_path,
        outcome_id="out-skipped",
        applied=False,
        skipped_reason="no_explicit_feedback",
    )

    report = build_recall_drift_report(tmp_path)

    assert report.gap_count == 4
    assert report.gaps_by_reason["manual_revalidation"] == 1
    assert report.gaps_by_reason["only_rejected"] == 1
    assert report.gaps_by_reason["partial_candidates_rejected"] == 1
    assert report.gaps_by_reason["query_not_injectable"] == 1
    assert report.gaps_by_family["manual_review"] == 1
    assert report.gaps_by_family["context_rejected"] == 2
    assert report.gaps_by_family["query_gate"] == 1
    assert report.task_outcome_count == 2
    assert report.task_outcomes_by_status["success"] == 1
    assert report.task_outcomes_by_status["corrected"] == 1
    assert report.implicit_positive_count == 1
    assert report.explicit_correction_count == 1
    assert report.task_outcome_feedback_applied_count == 1
    assert report.task_outcome_feedback_skipped_count == 1


def test_recall_gap_clustering_groups_same_root_cause_variants(tmp_path: Path) -> None:
    from agent_brain.memory.governance.recall_events import record_gap
    from agent_brain.memory.governance.recall_gap_clustering import build_gap_cluster_report

    first = record_gap(
        tmp_path,
        query="帮我打开浏览器，历史记忆说 Linux 浏览器受限，但这已经修复了",
        reason="manual_revalidation",
    )
    second = record_gap(
        tmp_path,
        query="browser permission fixed but stale memory still says unavailable",
        reason="only_rejected",
    )
    quota = record_gap(
        tmp_path,
        query="StepCode 424 quota model unavailable",
        reason="empty_recall",
    )

    report = build_gap_cluster_report(tmp_path)

    assert report.total_gaps == 3
    assert report.cluster_count == 2
    browser_cluster = next(cluster for cluster in report.clusters if "browser" in cluster.labels)
    assert browser_cluster.size == 2
    assert set(browser_cluster.gap_ids) == {first.gap_id, second.gap_id}
    assert browser_cluster.profile.root_cause == "stale_or_rejected_context"
    assert browser_cluster.profile.risk_level == "high"
    assert browser_cluster.profile.suggested_owner == "memory-quality"
    assert "browser" in browser_cluster.profile.trigger_terms
    assert any("live evidence" in exclusion for exclusion in browser_cluster.profile.exclusions)
    quota_cluster = next(cluster for cluster in report.clusters if quota.gap_id in cluster.gap_ids)
    assert quota_cluster.size == 1
    assert quota_cluster.profile.suggested_owner == "knowledge-base"


def test_recall_gap_clustering_labels_query_gate_gaps(tmp_path: Path) -> None:
    from agent_brain.memory.governance.recall_events import record_gap
    from agent_brain.memory.governance.recall_gap_clustering import build_gap_cluster_report

    gap = record_gap(
        tmp_path,
        query="验证",
        reason="query_not_injectable",
        evidence=["query_signal:too_weak", "terms=验证"],
    )

    report = build_gap_cluster_report(tmp_path)

    cluster = next(cluster for cluster in report.clusters if gap.gap_id in cluster.gap_ids)
    assert "query-gate" in cluster.labels


def test_recall_gap_cluster_json_includes_profile(tmp_path: Path) -> None:
    from agent_brain.memory.governance.recall_events import record_gap
    from agent_brain.memory.governance.recall_gap_clustering import build_gap_cluster_report

    record_gap(
        tmp_path,
        query="验证",
        reason="query_not_injectable",
        evidence=["query_signal:too_weak", "terms=验证"],
    )

    data = build_gap_cluster_report(tmp_path).to_dict()
    profile = data["clusters"][0]["profile"]

    assert profile["root_cause"] == "query_gate_underqualified"
    assert profile["risk_level"] == "medium"
    assert profile["suggested_owner"] == "retrieval-policy"
    assert "query gate" in " ".join(profile["exclusions"])


def test_recall_gap_replay_cohort_filters_root_cause_and_dedupes_queries(
    tmp_path: Path,
) -> None:
    from agent_brain.memory.governance.recall_events import record_gap
    from agent_brain.memory.governance.recall_gap_clustering import build_gap_replay_cohort

    first = record_gap(
        tmp_path,
        query="验证",
        reason="query_not_injectable",
        evidence=["query_signal:too_weak", "terms=验证"],
        adapter="codex",
        cwd="/repo",
    )
    record_gap(
        tmp_path,
        query="验证",
        reason="query_not_injectable",
        evidence=["query_signal:too_weak", "duplicate"],
    )
    record_gap(
        tmp_path,
        query="browser permission fixed but stale memory still says unavailable",
        reason="only_rejected",
    )

    cohort = build_gap_replay_cohort(
        tmp_path,
        root_cause="query_gate_underqualified",
        limit=10,
    )
    data = cohort.to_dict()

    assert data["matched_gap_count"] == 2
    assert data["deduped_query_count"] == 1
    assert data["cases"][0]["gap_id"] == first.gap_id
    assert data["cases"][0]["query"] == "验证"
    assert data["cases"][0]["expected_root_cause"] == "query_gate_underqualified"
    assert data["cases"][0]["expected_owner"] == "retrieval-policy"
    assert data["cases"][0]["adapter"] == "codex"
