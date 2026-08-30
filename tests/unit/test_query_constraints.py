from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_brain.memory.context.query_constraints import (
    QueryConstraint,
    constraint_parse_confidence,
    extract_query_constraints,
)
from agent_brain.memory.context.query_signal import (
    analyze_injection_query,
    query_gate_gap_evidence,
)
from agent_brain.memory.recall.constraint_alignment import (
    apply_constraint_alignment,
    assess_constraint_alignment,
)
from agent_brain.memory.recall.retrieval_types import RetrievedItem


def test_negated_constraint_survives_precise_anchor_focus() -> None:
    signal = analyze_injection_query("关于数据库迁移，不要停机，怎么做？")

    assert signal.injectable
    assert signal.terms[:2] == ("数据库迁移", "不要停机")
    assert signal.exact_phrases == ("数据库迁移", "不要停机")
    assert signal.constraints == (
        QueryConstraint(
            predicate="停机",
            polarity=False,
            evidence="不要停机",
            kind="prohibition",
            confidence=0.92,
        ),
    )
    assert "constraint_count=1" in signal.trace
    assert signal.parse_confidence >= 0.9


@pytest.mark.parametrize(
    ("prompt", "predicate", "kind"),
    [
        ("迁移时不能删除旧索引", "删除旧索引", "prohibition"),
        ("这项验证尚未完成", "完成", "absence"),
        ("Do not delete the old index", "delete the old index", "prohibition"),
    ],
)
def test_constraint_extraction_keeps_source_evidence(
    prompt: str,
    predicate: str,
    kind: str,
) -> None:
    constraints = extract_query_constraints(prompt)

    assert len(constraints) == 1
    assert constraints[0].predicate == predicate
    assert constraints[0].kind == kind
    assert constraints[0].evidence.casefold() in prompt.casefold()


def test_unparsed_double_negation_lowers_confidence_without_inventing_constraint() -> None:
    prompt = "不能不删除旧索引"
    constraints = extract_query_constraints(prompt)

    assert constraints == ()
    assert constraint_parse_confidence(prompt, constraints) < 0.7


def test_anchored_double_negation_fails_closed_for_automatic_injection() -> None:
    signal = analyze_injection_query("关于数据库迁移，不能不停机")

    assert not signal.injectable
    assert signal.reason == "ambiguous_constraint"
    assert signal.parse_confidence < 0.7


def test_alignment_filters_only_explicit_opposite_assertion() -> None:
    constraint = extract_query_constraints("不要停机")[0]

    assert assess_constraint_alignment("迁移方案无需停机", (constraint,)) == "aligned"
    assert assess_constraint_alignment("迁移方案需要停机", (constraint,)) == "conflict"
    assert assess_constraint_alignment("迁移方案评估停机窗口", (constraint,)) == "unknown"
    assert (
        assess_constraint_alignment("比较需要停机和无需停机两种方案", (constraint,))
        == "unknown"
    )


def test_ambiguous_constraint_is_visible_in_query_gate_gap_evidence() -> None:
    evidence = query_gate_gap_evidence("关于数据库迁移，不能不停机")

    assert evidence is not None
    assert "query_signal:ambiguous_constraint" in evidence.evidence


def test_constraint_alignment_filters_conflict_and_boosts_alignment() -> None:
    class Index:
        def get_texts(self, item_ids: list[str]) -> dict[str, str]:
            return {
                "aligned": "数据库迁移不要停机，采用双写和灰度切换",
                "conflict": "数据库迁移需要停机，不要在线执行",
                "unknown": "数据库迁移的停机窗口评估",
            }

    candidates = [
        RetrievedItem("conflict", 3.0, 1, None),
        RetrievedItem("unknown", 2.0, 2, None),
        RetrievedItem("aligned", 1.0, 3, None),
    ]
    constraints = extract_query_constraints("关于数据库迁移，不要停机")

    result = apply_constraint_alignment(Index(), candidates, constraints)

    assert [candidate.id for candidate in result] == ["unknown", "aligned"]
    assert next(candidate for candidate in result if candidate.id == "aligned").score == pytest.approx(1.08)


def test_english_constraint_alignment_ignores_articles() -> None:
    constraint = extract_query_constraints("Do not delete the old index")[0]

    assert assess_constraint_alignment("We must delete old index", (constraint,)) == "conflict"
    assert assess_constraint_alignment("We should not delete an old index", (constraint,)) == "aligned"


def test_routed_retrieval_rejects_opposite_downtime_memory(tmp_path: Path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.recall.admission import build_recall_request
    from agent_brain.memory.recall.retrieval import Retriever, SearchFilter
    from agent_brain.platform.indexing.index import HubIndex

    class DegradedEmbedder:
        degraded = True

    index = HubIndex(tmp_path / "constraint-aware.db", embedding_dim=2)
    correct = MemoryItem(
        id="mem-20260726-120000-online-migration",
        type=MemoryType.decision,
        created_at=datetime.now(UTC),
        title="数据库迁移不要停机",
        summary="采用双写和灰度切换完成在线迁移",
    )
    opposite = MemoryItem(
        id="mem-20260726-120001-offline-migration",
        type=MemoryType.decision,
        created_at=datetime.now(UTC),
        title="数据库迁移需要停机",
        summary="必须离线执行，不要在线迁移",
    )
    try:
        index.upsert(correct, correct.summary, embedding=None)
        index.upsert(opposite, opposite.summary, embedding=None)
        request = build_recall_request(
            "关于数据库迁移，不要停机，怎么做？",
            adapter="codex",
        )
        result = Retriever(
            index=index,
            embedder=DegradedEmbedder(),  # type: ignore[arg-type]
            rerank=False,
            apply_decay=False,
            record_access=False,
        ).search_routed(
            request,
            filters=SearchFilter(include_superseded=True, include_stale_state=True),
            explain=True,
        )

        assert [hit.id for hit in result.hits] == [correct.id]
        assert result.hits[0].trace is not None
        assert any(
            stage.name == "constraint_alignment"
            for stage in result.hits[0].trace.stages
        )
    finally:
        index.close()
