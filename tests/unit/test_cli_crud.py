"""Tests for CLI CRUD commands: delete, update, link, unlink, tag-suggest."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_brain.interfaces.cli import app
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.contracts.memory_item import MemoryItem, MemoryType

runner = CliRunner()


@pytest.fixture
def populated_brain(tmp_brain_dir: Path):
    os.environ["BRAIN_DIR"] = str(tmp_brain_dir)
    store = ItemsStore(tmp_brain_dir / "items")
    item = MemoryItem(
        id="mem-20260528-100000-aaa",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Test fact",
        summary="A test fact",
        tags=["infra", "test"],
        project="hub",
    )
    store.write(item, "body of the test fact")
    yield tmp_brain_dir, store, item
    os.environ.pop("BRAIN_DIR", None)


class TestDeleteCLI:
    def test_delete_existing(self, populated_brain):
        tmp, store, item = populated_brain
        result = runner.invoke(app, ["delete", item.id])
        assert result.exit_code == 0
        assert "deleted" in result.output
        assert not (store.items_dir / f"{item.id}.md").exists()

    def test_delete_nonexistent(self, populated_brain):
        tmp, store, item = populated_brain
        result = runner.invoke(app, ["delete", "mem-nonexistent"])
        assert result.exit_code != 0
        assert "not found" in result.output


class TestWriteCLI:
    def test_write_reports_quality_warning_without_blocking(self, tmp_brain_dir, monkeypatch):
        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        monkeypatch.setenv("MEMORY_HUB_EMBEDDING_OFFLINE", "1")
        result = runner.invoke(app, [
            "write",
            "--type", "decision",
            "--title", "Decision missing sections",
            "--summary", "Exercise advisory quality warning",
            "--body", "We picked SSE.",
        ])

        assert result.exit_code == 0, result.output
        assert "mem-" in result.output
        assert "warning: decision body missing required sections" in result.output
        assert list((tmp_brain_dir / "items").glob("*.md"))

    def test_write_accepts_validity_scope(self, tmp_brain_dir, monkeypatch):
        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        monkeypatch.setenv("MEMORY_HUB_EMBEDDING_OFFLINE", "1")
        result = runner.invoke(app, [
            "write",
            "--type", "signal",
            "--title", "Browser unavailable",
            "--summary", "Browser unavailable in this repo",
            "--body", "**当前状态** Browser unavailable",
            "--cwd", "/repo/current",
            "--adapter", "codex",
        ])

        assert result.exit_code == 0, result.output
        store = ItemsStore(tmp_brain_dir / "items")
        item = next(item for item, _body in store.iter_all())
        assert item.validity.cwd == "/repo/current"
        assert item.validity.adapter == "codex"

    def test_write_accepts_explicit_source_refs(self, tmp_brain_dir, monkeypatch):
        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        monkeypatch.setenv("MEMORY_HUB_EMBEDDING_OFFLINE", "1")
        evidence = tmp_brain_dir / "evidence.md"
        evidence.write_text("source evidence", encoding="utf-8")

        result = runner.invoke(app, [
            "write",
            "--type", "fact",
            "--title", "Fact with explicit refs",
            "--summary", "Exercise refs persistence",
            "--body", "**事实**\nref-backed\n**来源**\nfile\n**有效期**\ncurrent",
            "--ref-file", str(evidence),
            "--ref-url", "https://example.test/source",
            "--ref-mem", "mem-20260618-010101-source",
            "--ref-commit", "abc1234",
        ])

        assert result.exit_code == 0, result.output
        store = ItemsStore(tmp_brain_dir / "items")
        item = next(item for item, _body in store.iter_all())
        assert item.refs.files == [str(evidence)]
        assert item.refs.urls == ["https://example.test/source"]
        assert item.refs.mems == ["mem-20260618-010101-source"]
        assert item.refs.commits == ["abc1234"]
        assert item.refs.resources
        assert item.refs.extractions

    def test_write_accepts_explicit_validity_fields(self, tmp_brain_dir, monkeypatch):
        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        monkeypatch.setenv("MEMORY_HUB_EMBEDDING_OFFLINE", "1")
        result = runner.invoke(app, [
            "write",
            "--type", "signal",
            "--title", "Build status",
            "--summary", "Build failed on one branch",
            "--body", "**当前状态** Build failed",
            "--validity-cwd", "/repo/current",
            "--validity-repo", "agent-memory-hub",
            "--validity-branch", "feature/recall",
            "--validity-os", "darwin",
            "--validity-adapter", "codex",
            "--validity-ttl-hours", "12",
        ])

        assert result.exit_code == 0, result.output
        item = next(item for item, _body in ItemsStore(tmp_brain_dir / "items").iter_all())
        assert item.validity.cwd == "/repo/current"
        assert item.validity.repo == "agent-memory-hub"
        assert item.validity.branch == "feature/recall"
        assert item.validity.os == "darwin"
        assert item.validity.adapter == "codex"
        assert item.validity.ttl_hours == 12


class TestInjectionFeedbackCLI:
    def test_injection_feedback_marks_adopted_rejected_and_ignored(self, tmp_brain_dir, monkeypatch):
        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")
        store = ItemsStore(tmp_brain_dir / "items")
        ids = []
        for suffix in ("adopted", "rejected", "ignored"):
            item = MemoryItem(
                id=f"mem-20260611-130000-{suffix}",
                type=MemoryType.episode,
                created_at=datetime.now(timezone.utc),
                title=suffix,
                summary=suffix,
                confidence=0.7,
            )
            store.write(item, f"body {suffix}")
            ids.append(item.id)

        result = runner.invoke(app, [
            "injection-feedback",
            "--injected",
            ",".join(ids),
            "--adopted",
            ids[0],
            "--rejected",
            ids[1],
        ])

        assert result.exit_code == 0, result.output
        assert "adopted=1 rejected=1 ignored=1" in result.output
        adopted = store.get(ids[0])[0]
        rejected = store.get(ids[1])[0]
        ignored = store.get(ids[2])[0]
        assert adopted.support_count == 1
        assert rejected.contradict_count == 1
        assert ignored.support_count == 0
        assert ignored.contradict_count == 0

    def test_injection_feedback_latest_uses_recorded_cohort(self, tmp_brain_dir, monkeypatch):
        from agent_brain.memory.context.injection_cohorts import record_injection_cohort

        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")
        store = ItemsStore(tmp_brain_dir / "items")
        ids = []
        for suffix in ("latest-adopted", "latest-rejected", "latest-ignored"):
            item = MemoryItem(
                id=f"mem-20260611-131000-{suffix}",
                type=MemoryType.episode,
                created_at=datetime.now(timezone.utc),
                title=suffix,
                summary=suffix,
                confidence=0.7,
            )
            store.write(item, f"body {suffix}")
            ids.append(item.id)
        record_injection_cohort(
            tmp_brain_dir,
            item_ids=ids,
            adapter="codex",
            session_id="sess-latest",
            query="raw prompt should only become a hash",
        )

        result = runner.invoke(app, [
            "injection-feedback",
            "--latest",
            "--adapter",
            "codex",
            "--session",
            "sess-latest",
            "--adopted",
            ids[0],
            "--rejected",
            ids[1],
        ])

        assert result.exit_code == 0, result.output
        assert "adopted=1 rejected=1 ignored=1" in result.output
        adopted = store.get(ids[0])[0]
        rejected = store.get(ids[1])[0]
        ignored = store.get(ids[2])[0]
        assert adopted.support_count == 1
        assert rejected.contradict_count == 1
        assert ignored.support_count == 0
        assert ignored.contradict_count == 0

    def test_injection_feedback_all_rejected_records_only_rejected_gap(self, tmp_brain_dir, monkeypatch):
        from agent_brain.memory.context.injection_cohorts import record_injection_cohort
        from agent_brain.memory.governance.recall_events import iter_gap_records

        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")
        store = ItemsStore(tmp_brain_dir / "items")
        ids = []
        for suffix in ("wrong-a", "wrong-b"):
            item = MemoryItem(
                id=f"mem-20260611-132000-{suffix}",
                type=MemoryType.episode,
                created_at=datetime.now(timezone.utc),
                title=suffix,
                summary=suffix,
                confidence=0.7,
            )
            store.write(item, f"body {suffix}")
            ids.append(item.id)
        cohort = record_injection_cohort(
            tmp_brain_dir,
            item_ids=ids,
            adapter="codex",
            session_id="sess-all-wrong",
            cwd="/repo",
            query="browser stale wrong recall",
        )

        result = runner.invoke(app, [
            "injection-feedback",
            "--latest",
            "--adapter",
            "codex",
            "--session",
            "sess-all-wrong",
            "--rejected",
            ",".join(ids),
        ])

        assert result.exit_code == 0, result.output
        gaps = list(iter_gap_records(tmp_brain_dir))
        assert len(gaps) == 1
        assert gaps[0].reason == "only_rejected"
        assert gaps[0].rejected_ids == tuple(ids)
        assert gaps[0].adapter == "codex"
        assert gaps[0].session_id == "sess-all-wrong"
        assert gaps[0].cwd == "/repo"
        assert any(cohort.cohort_id in evidence for evidence in gaps[0].evidence)

    def test_injection_feedback_records_task_outcome_for_recall_drift(self, tmp_brain_dir, monkeypatch):
        from agent_brain.memory.context.injection_cohorts import record_injection_cohort
        from agent_brain.memory.governance.recall_events import iter_task_outcomes

        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")
        store = ItemsStore(tmp_brain_dir / "items")
        ids = []
        for suffix in ("adopted-outcome", "rejected-outcome", "ignored-outcome"):
            item = MemoryItem(
                id=f"mem-20260611-132500-{suffix}",
                type=MemoryType.episode,
                created_at=datetime.now(timezone.utc),
                title=suffix,
                summary=suffix,
                confidence=0.7,
            )
            store.write(item, f"body {suffix}")
            ids.append(item.id)
        cohort = record_injection_cohort(
            tmp_brain_dir,
            item_ids=ids,
            adapter="codex",
            session_id="sess-outcome",
            cwd="/repo",
            query="raw prompt must not be stored",
        )

        result = runner.invoke(app, [
            "injection-feedback",
            "--latest",
            "--adapter",
            "codex",
            "--session",
            "sess-outcome",
            "--adopted",
            ids[0],
            "--rejected",
            ids[1],
        ])

        assert result.exit_code == 0, result.output
        outcomes = list(iter_task_outcomes(tmp_brain_dir))
        assert len(outcomes) == 1
        outcome = outcomes[0]
        assert outcome.task_id == f"injection-feedback:{cohort.cohort_id}"
        assert outcome.question == f"injection cohort {cohort.cohort_id}"
        assert outcome.outcome == "corrected"
        assert outcome.injected_ids == tuple(ids)
        assert outcome.adopted_ids == (ids[0],)
        assert outcome.rejected_ids == (ids[1],)
        assert "user_correction" in outcome.feedback_signals
        assert "injection_feedback" in outcome.feedback_signals
        assert outcome.adapter == "codex"
        assert outcome.session_id == "sess-outcome"
        assert outcome.cwd == "/repo"
        raw = (tmp_brain_dir / "runtime" / "task-outcomes.jsonl").read_text(encoding="utf-8")
        assert "raw prompt must not be stored" not in raw

    def test_injection_feedback_outcome_is_marked_applied_to_prevent_double_weighting(
        self,
        tmp_brain_dir,
        monkeypatch,
    ):
        from agent_brain.memory.context.injection_cohorts import record_injection_cohort

        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")
        store = ItemsStore(tmp_brain_dir / "items")
        ids = []
        for suffix in ("adopted-once", "rejected-once"):
            item = MemoryItem(
                id=f"mem-20260611-132600-{suffix}",
                type=MemoryType.episode,
                created_at=datetime.now(timezone.utc),
                title=suffix,
                summary=suffix,
                confidence=0.7,
            )
            store.write(item, f"body {suffix}")
            ids.append(item.id)
        record_injection_cohort(
            tmp_brain_dir,
            item_ids=ids,
            adapter="codex",
            session_id="sess-no-double",
            cwd="/repo",
            query="prompt hash only",
        )

        feedback = runner.invoke(app, [
            "injection-feedback",
            "--latest",
            "--adapter",
            "codex",
            "--session",
            "sess-no-double",
            "--adopted",
            ids[0],
            "--rejected",
            ids[1],
        ])
        apply = runner.invoke(app, ["recall-drift", "apply-outcomes", "--format", "json"])

        assert feedback.exit_code == 0, feedback.output
        assert apply.exit_code == 0, apply.output
        payload = json.loads(apply.output)
        assert payload["applied_count"] == 0
        assert payload["already_applied_count"] == 1
        assert store.get(ids[0])[0].support_count == 1
        assert store.get(ids[1])[0].contradict_count == 1

    def test_injection_feedback_partial_rejection_does_not_record_gap(self, tmp_brain_dir, monkeypatch):
        from agent_brain.memory.context.injection_cohorts import record_injection_cohort
        from agent_brain.memory.governance.recall_events import iter_gap_records

        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")
        store = ItemsStore(tmp_brain_dir / "items")
        ids = []
        for suffix in ("right", "wrong", "ignored"):
            item = MemoryItem(
                id=f"mem-20260611-133000-{suffix}",
                type=MemoryType.episode,
                created_at=datetime.now(timezone.utc),
                title=suffix,
                summary=suffix,
                confidence=0.7,
            )
            store.write(item, f"body {suffix}")
            ids.append(item.id)
        record_injection_cohort(
            tmp_brain_dir,
            item_ids=ids,
            adapter="codex",
            session_id="sess-partial",
            cwd="/repo",
        )

        result = runner.invoke(app, [
            "injection-feedback",
            "--latest",
            "--adapter",
            "codex",
            "--session",
            "sess-partial",
            "--adopted",
            ids[0],
            "--rejected",
            ids[1],
        ])

        assert result.exit_code == 0, result.output
        assert list(iter_gap_records(tmp_brain_dir)) == []


class TestRecallDriftCLI:
    def test_recall_drift_report_outputs_gap_and_outcome_summary(
        self,
        tmp_brain_dir,
        monkeypatch,
    ):
        import json

        from agent_brain.memory.governance.recall_events import record_gap, record_task_outcome

        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        record_gap(tmp_brain_dir, query="帮我打开浏览器", reason="manual_revalidation")
        record_gap(
            tmp_brain_dir,
            query="验证",
            reason="query_not_injectable",
            evidence=["query_signal:too_weak", "terms=验证"],
        )
        record_task_outcome(
            tmp_brain_dir,
            task_id="task-1",
            question="继续修复 recall drift",
            outcome="success",
            feedback_signals=["implicit_continue"],
            value_tags=["workflow_pattern"],
            confidence=0.35,
        )

        result = runner.invoke(app, ["recall-drift", "report", "--format", "json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["gap_count"] == 2
        assert data["task_outcome_count"] == 1
        assert data["gaps_by_reason"]["manual_revalidation"] == 1
        assert data["gaps_by_reason"]["query_not_injectable"] == 1
        assert data["gaps_by_family"]["manual_review"] == 1
        assert data["gaps_by_family"]["query_gate"] == 1
        assert data["task_outcomes_by_status"]["success"] == 1
        assert data["implicit_positive_count"] == 1

    def test_recall_drift_apply_outcomes_is_idempotent(
        self,
        tmp_brain_dir,
        monkeypatch,
    ):
        import json

        from agent_brain.memory.governance.recall_events import record_task_outcome

        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")
        store = ItemsStore(tmp_brain_dir / "items")
        adopted = MemoryItem(
            id="mem-20260612-030000-cli-adopted",
            type=MemoryType.episode,
            created_at=datetime.now(timezone.utc),
            title="CLI adopted",
            summary="CLI adopted",
        )
        rejected = MemoryItem(
            id="mem-20260612-030000-cli-rejected",
            type=MemoryType.episode,
            created_at=datetime.now(timezone.utc),
            title="CLI rejected",
            summary="CLI rejected",
        )
        for item in (adopted, rejected):
            store.write(item, item.summary)
        record_task_outcome(
            tmp_brain_dir,
            task_id="task-cli",
            question="apply outcome feedback",
            outcome="success",
            injected_ids=[adopted.id, rejected.id],
            adopted_ids=[adopted.id],
            rejected_ids=[rejected.id],
        )

        first = runner.invoke(app, ["recall-drift", "apply-outcomes", "--format", "json"])
        second = runner.invoke(app, ["recall-drift", "apply-outcomes", "--format", "json"])

        assert first.exit_code == 0, first.output
        assert second.exit_code == 0, second.output
        first_data = json.loads(first.output)
        second_data = json.loads(second.output)
        assert first_data["applied_count"] == 1
        assert second_data["already_applied_count"] == 1
        assert store.get(adopted.id)[0].support_count == 1
        assert store.get(rejected.id)[0].contradict_count == 1

    def test_recall_drift_gap_clusters_outputs_operational_clusters(
        self,
        tmp_brain_dir,
        monkeypatch,
    ):
        import json

        from agent_brain.memory.governance.recall_events import record_gap

        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        record_gap(
            tmp_brain_dir,
            query="帮我打开浏览器，历史记忆说 Linux 浏览器受限，但这已经修复了",
            reason="manual_revalidation",
        )
        record_gap(
            tmp_brain_dir,
            query="browser permission fixed but stale memory still says unavailable",
            reason="only_rejected",
        )
        record_gap(
            tmp_brain_dir,
            query="StepCode 424 quota model unavailable",
            reason="empty_recall",
        )

        result = runner.invoke(app, ["recall-drift", "gap-clusters", "--format", "json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["total_gaps"] == 3
        assert data["cluster_count"] == 2
        assert data["clusters"][0]["size"] == 2
        assert "browser" in data["clusters"][0]["labels"]
        assert data["clusters"][0]["profile"]["risk_level"] == "high"
        assert data["clusters"][0]["profile"]["suggested_owner"] == "memory-quality"

    def test_recall_drift_gap_clusters_table_includes_profile_routing(
        self,
        tmp_brain_dir,
        monkeypatch,
    ):
        from agent_brain.memory.governance.recall_events import record_gap

        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        record_gap(
            tmp_brain_dir,
            query="browser permission fixed but stale memory still says unavailable",
            reason="only_rejected",
        )

        result = runner.invoke(app, ["recall-drift", "gap-clusters"])

        assert result.exit_code == 0, result.output
        assert "risk" in result.output
        assert "owner" in result.output
        assert "high" in result.output
        assert "memory-quality" in result.output

    def test_recall_drift_replay_cohort_exports_query_gate_cases(
        self,
        tmp_brain_dir,
        monkeypatch,
    ):
        import json

        from agent_brain.memory.governance.recall_events import record_gap

        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        record_gap(
            tmp_brain_dir,
            query="验证",
            reason="query_not_injectable",
            evidence=["query_signal:too_weak", "terms=验证"],
            adapter="codex",
        )
        record_gap(
            tmp_brain_dir,
            query="browser permission fixed but stale memory still says unavailable",
            reason="only_rejected",
        )

        result = runner.invoke(
            app,
            [
                "recall-drift",
                "replay-cohort",
                "--root-cause",
                "query_gate_underqualified",
                "--format",
                "json",
            ],
        )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["matched_gap_count"] == 1
        assert data["deduped_query_count"] == 1
        assert data["cases"][0]["query"] == "验证"
        assert data["cases"][0]["expected_root_cause"] == "query_gate_underqualified"


class TestReviewCLI:
    def test_review_status_summarizes_review_and_pending_backlog(self, tmp_brain_dir, monkeypatch):
        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        from agent_brain.memory.store.pending import enqueue_write_record

        store = ItemsStore(tmp_brain_dir / "items")
        review = MemoryItem(
            id="mem-20260612-105900-review-status-candidate",
            type=MemoryType.episode,
            created_at=datetime.now(timezone.utc),
            title="Review status candidate",
            summary="Needs review status coverage",
            tags=["needs-review"],
            confidence=0.3,
        )
        store.write(review, review.summary)
        enqueue_write_record({"op": "write", "item": {"title": "pending", "summary": "pending"}})

        result = runner.invoke(app, ["review", "status", "--format", "json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["review_total"] == 1
        assert data["pending_depth"] == 1
        assert data["pending_dead"] == 0
        assert data["recommended_next"] == "review list --format json"

    def test_review_list_outputs_only_active_review_candidates(self, tmp_brain_dir, monkeypatch):
        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        store = ItemsStore(tmp_brain_dir / "items")
        review = MemoryItem(
            id="mem-20260612-110000-review-candidate",
            type=MemoryType.episode,
            created_at=datetime.now(timezone.utc),
            title="Review candidate",
            summary="Needs human review",
            tags=["needs-review", "unverified-boundary"],
            confidence=0.35,
        )
        rejected = MemoryItem(
            id="mem-20260612-110000-review-rejected",
            type=MemoryType.episode,
            created_at=datetime.now(timezone.utc),
            title="Rejected candidate",
            summary="Already rejected",
            tags=["review-rejected"],
            confidence=0.1,
        )
        normal = MemoryItem(
            id="mem-20260612-110000-normal",
            type=MemoryType.episode,
            created_at=datetime.now(timezone.utc),
            title="Normal item",
            summary="Normal",
            tags=[],
        )
        for item in (review, rejected, normal):
            store.write(item, item.summary)

        result = runner.invoke(app, ["review", "list", "--format", "json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["total"] == 1
        assert data["items"][0]["id"] == review.id
        assert data["items"][0]["tags"] == ["needs-review", "unverified-boundary"]

    def test_review_approve_promotes_candidate_out_of_queue(self, tmp_brain_dir, monkeypatch):
        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        store = ItemsStore(tmp_brain_dir / "items")
        item = MemoryItem(
            id="mem-20260612-110100-approve-candidate",
            type=MemoryType.episode,
            created_at=datetime.now(timezone.utc),
            title="Approve candidate",
            summary="Verified by user",
            tags=["needs-review", "unverified-boundary"],
            confidence=0.35,
        )
        store.write(item, item.summary)

        result = runner.invoke(app, ["review", "approve", item.id, "--confidence", "0.82"])
        listed = runner.invoke(app, ["review", "list", "--format", "json"])

        assert result.exit_code == 0, result.output
        updated = store.get(item.id)[0]
        assert "needs-review" not in updated.tags
        assert "unverified-boundary" not in updated.tags
        assert "review-approved" in updated.tags
        assert updated.confidence == 0.82
        assert json.loads(listed.output)["total"] == 0

    def test_review_reject_quarantines_candidate_out_of_queue(self, tmp_brain_dir, monkeypatch):
        monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
        store = ItemsStore(tmp_brain_dir / "items")
        item = MemoryItem(
            id="mem-20260612-110200-reject-candidate",
            type=MemoryType.episode,
            created_at=datetime.now(timezone.utc),
            title="Reject candidate",
            summary="Wrong inference",
            tags=["needs-review", "unverified-boundary"],
            confidence=0.35,
        )
        store.write(item, item.summary)

        result = runner.invoke(app, ["review", "reject", item.id])
        listed = runner.invoke(app, ["review", "list", "--format", "json"])

        assert result.exit_code == 0, result.output
        updated = store.get(item.id)[0]
        assert "needs-review" not in updated.tags
        assert "unverified-boundary" not in updated.tags
        assert "review-rejected" in updated.tags
        assert updated.confidence == 0.1
        assert updated.contradict_count == 1
        assert json.loads(listed.output)["total"] == 0


class TestUpdateCLI:
    def test_update_title(self, populated_brain):
        tmp, store, item = populated_brain
        result = runner.invoke(app, ["update", item.id, "--title", "Updated title"])
        assert result.exit_code == 0
        assert "updated" in result.output
        updated, _ = ItemsStore._read_one(store.items_dir / f"{item.id}.md")
        assert updated.title == "Updated title"

    def test_update_add_tags(self, populated_brain):
        tmp, store, item = populated_brain
        result = runner.invoke(app, ["update", item.id, "--add-tags", "new-tag,extra"])
        assert result.exit_code == 0
        updated, _ = ItemsStore._read_one(store.items_dir / f"{item.id}.md")
        assert "new-tag" in updated.tags
        assert "extra" in updated.tags
        assert "infra" in updated.tags

    def test_update_no_fields(self, populated_brain):
        tmp, store, item = populated_brain
        result = runner.invoke(app, ["update", item.id])
        assert result.exit_code != 0
        assert "no fields" in result.output

    def test_update_nonexistent(self, populated_brain):
        tmp, store, item = populated_brain
        result = runner.invoke(app, ["update", "mem-nonexistent", "--title", "x"])
        assert result.exit_code != 0
        assert "not found" in result.output


class TestLinkUnlinkCLI:
    def test_link_and_unlink(self, populated_brain):
        tmp, store, item = populated_brain
        item2 = MemoryItem(
            id="mem-20260528-100000-bbb",
            type=MemoryType.decision,
            created_at=datetime.now(timezone.utc),
            title="Test decision",
            summary="A decision",
        )
        store.write(item2, "decision body")

        result = runner.invoke(app, ["link", item.id, item2.id, "--label", "supports"])
        assert result.exit_code == 0
        assert "linked" in result.output
        linked, _ = ItemsStore._read_one(store.items_dir / f"{item.id}.md")
        assert item2.id in linked.refs.mems

        result = runner.invoke(app, ["unlink", item.id, item2.id])
        assert result.exit_code == 0
        assert "unlinked" in result.output


class TestTagSuggestCLI:
    def test_suggest_tags(self, populated_brain):
        result = runner.invoke(app, ["tag-suggest", "infrastructure test", "--max", "3"])
        assert result.exit_code == 0
