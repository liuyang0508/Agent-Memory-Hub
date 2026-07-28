from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.interfaces.cli import app
from agent_brain.memory.context.injection_cohorts import record_injection_cohort
from agent_brain.memory.governance.recall_events import (
    record_task_outcome,
    record_task_outcome_feedback_application,
)
from agent_brain.memory.recall.adaptive_learning import (
    apply_learning_profile_weight,
    load_learning_profile,
    refresh_learning_profile,
    rollback_learning_profile,
)
from agent_brain.memory.recall.retrieval_types import RetrievedItem
from typer.testing import CliRunner


def _record_applied_outcome(
    brain_dir: Path,
    *,
    item_ids: list[str],
    scores: list[float],
    adopted_ids: list[str] | None = None,
    rejected_ids: list[str] | None = None,
) -> None:
    cohort = record_injection_cohort(
        brain_dir,
        item_ids=item_ids,
        adapter="codex",
        session_id="session-1",
        cwd="/repo",
        pack_metrics={
            "retrieval_trace": [
                {"final_rank": rank, "final_score": score}
                for rank, score in enumerate(scores, start=1)
            ]
        },
    )
    outcome = record_task_outcome(
        brain_dir,
        task_id=f"task-{cohort.cohort_id}",
        question="adaptive recall",
        outcome="success",
        confidence=0.95,
        injected_ids=item_ids,
        adopted_ids=adopted_ids or [],
        rejected_ids=rejected_ids or [],
        adapter="codex",
        project="memory-hub",
        cohort_id=cohort.cohort_id,
    )
    record_task_outcome_feedback_application(
        brain_dir,
        outcome_id=outcome.outcome_id,
        applied=True,
        adopted_ids=adopted_ids or [],
        rejected_ids=rejected_ids or [],
        adapter="codex",
        session_id="session-1",
    )


def test_learning_profile_activates_and_weights_exact_adapter_project(tmp_path: Path) -> None:
    adopted = "mem-20260728-100000-adopted"
    other = "mem-20260728-100001-other"
    _record_applied_outcome(
        tmp_path,
        item_ids=[adopted, other],
        scores=[1.0, 0.9],
        adopted_ids=[adopted],
    )

    report = refresh_learning_profile(tmp_path)
    weighted = apply_learning_profile_weight(
        [
            RetrievedItem(adopted, 1.0, None, None),
            RetrievedItem(other, 1.02, None, None),
        ],
        brain_dir=tmp_path,
        adapter="codex",
        project="memory-hub",
    )

    assert report.status == "activated"
    assert report.evidence_cases == 1
    assert weighted[0].id == adopted
    assert weighted[0].score == 1.03


def test_retrieval_trace_exposes_adaptive_learning_stage(tmp_path: Path) -> None:
    from agent_brain.memory.recall.retrieval import Retriever, SearchFilter
    from agent_brain.platform.embedding import HashingEmbedder
    from agent_brain.platform.indexing.index import HubIndex

    adopted = "mem-20260728-100005-adopted"
    _record_applied_outcome(
        tmp_path,
        item_ids=[adopted],
        scores=[1.0],
        adopted_ids=[adopted],
    )
    assert refresh_learning_profile(tmp_path).status == "activated"
    item = MemoryItem(
        id=adopted,
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Adaptive trace",
        summary="adaptive trace recall",
        project="memory-hub",
    )
    embedder = HashingEmbedder(dim=8)
    index = HubIndex(tmp_path / "index.db", embedding_dim=8)
    index.upsert(item, "adaptive trace recall", embedding=embedder.embed("adaptive trace recall"))

    hit = Retriever(
        index,
        embedder,
        vector_weight=0,
        apply_decay=False,
        record_access=False,
    ).search(
        "adaptive trace",
        top_k=1,
        filters=SearchFilter(project="memory-hub"),
        adapter="codex",
        explain=True,
    )[0]

    assert hit.trace is not None
    stage = next(stage for stage in hit.trace.stages if stage.name == "adaptive_learning")
    assert stage.before_score is not None
    assert stage.after_score == stage.before_score * 1.03
    index.close()


def test_learning_profile_rejects_replay_regression_and_can_rollback(tmp_path: Path) -> None:
    adopted = "mem-20260728-100010-adopted"
    rival = "mem-20260728-100011-rival"
    _record_applied_outcome(
        tmp_path,
        item_ids=[adopted, rival],
        scores=[1.0, 0.99],
        adopted_ids=[adopted],
    )
    assert refresh_learning_profile(tmp_path).status == "activated"
    first = load_learning_profile(tmp_path)

    _record_applied_outcome(
        tmp_path,
        item_ids=[adopted],
        scores=[1.0],
        rejected_ids=[adopted],
    )
    rejected = refresh_learning_profile(tmp_path)
    assert rejected.status == "rejected_regression"
    assert load_learning_profile(tmp_path) == first

    for _ in range(2):
        _record_applied_outcome(
            tmp_path,
            item_ids=[adopted],
            scores=[1.0],
            adopted_ids=[adopted],
        )
    assert refresh_learning_profile(tmp_path).status == "activated"
    assert rollback_learning_profile(tmp_path) is True
    assert load_learning_profile(tmp_path) == first


def test_loop_completion_attributes_only_fresh_same_session_cohort(tmp_path: Path) -> None:
    from agent_brain.memory.governance.recall_events import iter_task_outcomes
    from agent_brain.memory.loops.loop_store import LoopStore
    from agent_brain.memory.store.items_store import ItemsStore
    from agent_brain.platform.embedding import HashingEmbedder
    from agent_brain.platform.indexing.index import HubIndex

    now = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc)
    item = MemoryItem(
        id="mem-20260728-100020-loop",
        type=MemoryType.fact,
        created_at=now,
        title="Loop memory",
        summary="Useful loop memory",
    )
    items = ItemsStore(tmp_path / "items")
    items.write(item, "loop body")
    index = HubIndex(tmp_path / "index.db", embedding_dim=8)
    index.upsert(item, "loop body", embedding=HashingEmbedder(dim=8).embed("loop body"))
    index.close()
    cohort = record_injection_cohort(
        tmp_path,
        item_ids=[item.id],
        adapter="codex",
        session_id="session-loop",
        cwd="/repo",
        now=now,
        pack_metrics={"retrieval_trace": [{"final_rank": 1, "final_score": 1.0}]},
    )

    loop = LoopStore(tmp_path).create(
        goal="Finish adaptive learning",
        project="memory-hub",
        adapter="codex",
        session_id="session-loop",
        cwd="/repo",
        start=True,
        now=now + timedelta(minutes=5),
    )
    completed = LoopStore(tmp_path).complete(
        loop.loop_id,
        evidence="tests passed",
        now=now + timedelta(minutes=6),
    )
    outcome = list(iter_task_outcomes(tmp_path))[-1]

    assert completed.memory_candidates == [
        {
            "id": item.id,
            "source": "injection_cohort",
            "cohort_id": cohort.cohort_id,
        }
    ]
    assert outcome.cohort_id == cohort.cohort_id
    assert outcome.project == "memory-hub"
    assert outcome.feedback_signals == ("loop_verified", "implicit_task_success")
    assert items.get(item.id)[0].gain_score == 0.03
    assert load_learning_profile(tmp_path)["profiles"]


def test_loop_does_not_attach_stale_or_cross_worktree_cohort(tmp_path: Path) -> None:
    from agent_brain.memory.loops.loop_store import LoopStore

    now = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc)
    record_injection_cohort(
        tmp_path,
        item_ids=["mem-20260728-100030-stale"],
        adapter="codex",
        session_id="session-stale",
        cwd="/repo-a",
        now=now,
    )

    stale = LoopStore(tmp_path).create(
        goal="Stale cohort",
        adapter="codex",
        session_id="session-stale",
        cwd="/repo-a",
        now=now + timedelta(minutes=31),
    )
    cross_worktree = LoopStore(tmp_path).create(
        goal="Cross worktree cohort",
        adapter="codex",
        session_id="session-stale",
        cwd="/repo-b",
        now=now + timedelta(minutes=1),
    )

    assert stale.memory_candidates == []
    assert cross_worktree.memory_candidates == []


def test_learning_cli_reports_active_profile(
    tmp_path: Path,
    monkeypatch,
) -> None:
    adopted = "mem-20260728-100040-cli"
    _record_applied_outcome(
        tmp_path,
        item_ids=[adopted],
        scores=[1.0],
        adopted_ids=[adopted],
    )
    refresh_learning_profile(tmp_path)
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path))

    result = CliRunner().invoke(app, ["learning", "status", "--format", "json"])
    payload = json.loads(result.stdout)

    assert result.exit_code == 0
    assert payload["active"] is True
    assert payload["profile_count"] == 2
