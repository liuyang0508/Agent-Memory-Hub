from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "evaluation" / "retrieval-research-eval-gate.md"


def test_retrieval_research_eval_gate_exists_and_names_required_evidence():
    text = DOC.read_text(encoding="utf-8")

    for term in ["Hopfield", "HRR", "OpenViking", "benchmark_relevance.py"]:
        assert term in text

    for metric in ["MRR", "Precision@5", "Recall@10", "NDCG@10", "token cost", "stale hit rate"]:
        assert metric in text

    assert "no production retrieval change" in text.lower()
    assert "benchmarks/benchmark_relevance.py --synthetic" in text
    assert "--ablation" in text
    assert "rrf_context_firewall" in text
    assert "--queries-file tests/fixtures/relevance/hand_labeled_queries.json" in text
    assert "hand-labeled" in text
