from __future__ import annotations


def test_retrieval_gate_computes_mrr_and_passes_thresholds() -> None:
    from agent_brain.evaluation.retrieval_gate import RetrievalCase, evaluate_rankings

    cases = [
        RetrievalCase(query="write funnel", expected_ids=["mem-write"]),
        RetrievalCase(query="adapter verified", expected_ids=["mem-adapter"]),
    ]
    rankings = {
        "write funnel": ["mem-write", "mem-other"],
        "adapter verified": ["mem-other", "mem-adapter"],
    }

    report = evaluate_rankings(
        cases,
        lambda query, top_k: rankings[query][:top_k],
        top_k=3,
        min_recall_at_1=0.5,
        min_mrr=0.75,
    )

    assert report.passed is True
    assert report.metrics["recall_at_1"] == 0.5
    assert report.metrics["mrr"] == 0.75
    assert report.cases[1]["rank"] == 2


def test_retrieval_gate_fails_when_expected_items_are_missing() -> None:
    from agent_brain.evaluation.retrieval_gate import RetrievalCase, evaluate_rankings

    report = evaluate_rankings(
        [RetrievalCase(query="missing", expected_ids=["mem-target"])],
        lambda query, top_k: ["mem-other"],
        min_recall_at_1=1.0,
        min_mrr=1.0,
    )

    assert report.passed is False
    assert report.cases[0]["rank"] is None
    assert any("mrr" in failure for failure in report.failures)
