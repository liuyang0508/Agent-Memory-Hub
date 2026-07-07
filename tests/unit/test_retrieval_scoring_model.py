from __future__ import annotations

import pytest


def test_rrf_base_score_matches_documented_formula() -> None:
    from agent_brain.memory.recall.scoring_model import rrf_base_score

    score = rrf_base_score(
        bm25_rank=0,
        vector_rank=2,
        rrf_k=60,
        bm25_weight=1.0,
        vector_weight=0.5,
    )

    assert score == pytest.approx((1.0 / 61) + (0.5 / 63))


def test_final_score_breakdown_explains_multipliers() -> None:
    from agent_brain.memory.recall.scoring_model import final_score_breakdown

    breakdown = final_score_breakdown(
        base_score=0.4,
        confidence=0.8,
        retention=0.5,
        feedback_value=1.25,
        status_boost=1.1,
        adapter_runtime_boost=1.0,
        freshness_guard=0.75,
    )

    assert breakdown.final_score == pytest.approx(0.4 * 0.8 * 0.5 * 1.25 * 1.1 * 1.0 * 0.75)
    assert breakdown.waterfall[0] == ("S0", pytest.approx(0.4))
    assert breakdown.waterfall[-1] == ("freshness_guard", pytest.approx(breakdown.final_score))


def test_graph_neighbor_and_mmr_formulas_are_explicit() -> None:
    from agent_brain.memory.recall.scoring_model import graph_neighbor_score, mmr_score

    assert graph_neighbor_score(min_score=0.3, alpha=0.5) == pytest.approx(0.15)
    assert mmr_score(relevance=0.9, max_similarity=0.4, lambda_=0.7) == pytest.approx(
        0.7 * 0.9 - 0.3 * 0.4
    )
