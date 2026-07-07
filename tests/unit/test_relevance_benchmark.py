"""Tests for retrieval relevance benchmark metrics and query generation."""

from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.benchmark_relevance import (
    RelevanceQuery,
    ablation_variants,
    generate_queries_from_pool,
    load_labeled_queries,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    run_ablation_suite,
    run_relevance_benchmark,
)
from benchmarks.benchmark_retrieval import generate_synthetic_items
from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def _item(idx: int, **kw) -> tuple[MemoryItem, str]:
    from datetime import datetime, timezone

    defaults = dict(
        id=f"mem-20260527-100000-item-{idx}",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title=f"Item {idx}",
        summary=f"Summary {idx}",
        tags=["test"],
        project="bench",
    )
    defaults.update(kw)
    item = MemoryItem(**defaults)
    return item, f"Body for item {idx}"


class TestReciprocalRank:
    def test_first_is_relevant(self):
        assert reciprocal_rank(["a", "b", "c"], {"a"}) == 1.0

    def test_second_is_relevant(self):
        assert reciprocal_rank(["a", "b", "c"], {"b"}) == 0.5

    def test_third_is_relevant(self):
        assert reciprocal_rank(["a", "b", "c"], {"c"}) == pytest.approx(1 / 3)

    def test_none_relevant(self):
        assert reciprocal_rank(["a", "b", "c"], {"x"}) == 0.0

    def test_empty_retrieved(self):
        assert reciprocal_rank([], {"a"}) == 0.0


class TestPrecisionAtK:
    def test_all_relevant(self):
        assert precision_at_k(["a", "b", "c"], {"a", "b", "c"}, 3) == 1.0

    def test_none_relevant(self):
        assert precision_at_k(["x", "y", "z"], {"a"}, 3) == 0.0

    def test_half_relevant(self):
        assert precision_at_k(["a", "x", "b", "y"], {"a", "b"}, 4) == 0.5

    def test_k_zero(self):
        assert precision_at_k(["a"], {"a"}, 0) == 0.0


class TestRecallAtK:
    def test_all_found(self):
        assert recall_at_k(["a", "b"], {"a", "b"}, 2) == 1.0

    def test_partial(self):
        assert recall_at_k(["a", "x"], {"a", "b"}, 2) == 0.5

    def test_none_found(self):
        assert recall_at_k(["x", "y"], {"a", "b"}, 2) == 0.0

    def test_empty_relevant(self):
        assert recall_at_k(["a"], set(), 1) == 1.0

    def test_large_relevant_set_is_bounded_by_k(self):
        retrieved = [f"mem-{i}" for i in range(10)]
        relevant = {f"mem-{i}" for i in range(20)}

        assert recall_at_k(retrieved, relevant, 10) == 1.0


class TestNDCG:
    def test_perfect_ranking(self):
        assert ndcg_at_k(["a", "b"], {"a", "b"}, 2) == pytest.approx(1.0)

    def test_inverted_ranking(self):
        val = ndcg_at_k(["x", "a"], {"a"}, 2)
        assert 0.0 < val < 1.0

    def test_no_relevant(self):
        assert ndcg_at_k(["x", "y"], {"a"}, 2) == 0.0

    def test_single_relevant_at_top(self):
        assert ndcg_at_k(["a", "x", "y"], {"a"}, 3) == pytest.approx(1.0)


class TestQueryGeneration:
    def test_generates_queries_from_meaningful_items(self):
        items = [_item(i, title=f"Unique title {i}", tags=["arch", "test"])
                 for i in range(10)]
        queries = generate_queries_from_pool(items, target_count=20)
        assert len(queries) > 0
        assert all(isinstance(q, RelevanceQuery) for q in queries)

    def test_skips_noise_items(self):
        noise = [_item(i, tags=["session-active", "needs-review", "auto-captured"])
                 for i in range(10)]
        queries = generate_queries_from_pool(noise, target_count=10)
        assert len(queries) == 0

    def test_respects_target_count(self):
        items = [
            _item(i, title=f"Topic {i}", tags=[f"tag-{i % 3}", "common"], project=f"proj-{i % 2}")
            for i in range(30)
        ]
        queries = generate_queries_from_pool(items, target_count=5)
        assert len(queries) <= 5

    def test_categories_present(self):
        items = [
            _item(i, title=f"Decision about topic {i}", tags=["arch", "api"], project="myproj")
            for i in range(15)
        ]
        queries = generate_queries_from_pool(items, target_count=50)
        categories = {q.category for q in queries}
        assert "title_recall" in categories

    def test_small_target_count_keeps_category_coverage(self):
        items = [
            _item(
                i,
                title=f"Decision about checkout topic {i}",
                tags=["arch", "api", f"tag-{i % 3}"],
                project="checkout-service",
                type=MemoryType.decision if i % 2 == 0 else MemoryType.fact,
            )
            for i in range(20)
        ]

        queries = generate_queries_from_pool(items, target_count=10)
        categories = {q.category for q in queries}

        assert {
            "title_recall",
            "tag_keyword",
            "project_scope",
            "type_scope",
            "multi_keyword",
        } <= categories

    def test_synthetic_style_titles_generate_semantic_paraphrase_queries(self):
        titles = [
            "API Design Decision",
            "Circuit Breaker Implementation",
            "Performance Optimization",
            "Security Review Findings",
        ]
        items = [
            _item(
                i,
                title=titles[i % len(titles)],
                tags=["architecture", "security", f"tag-{i % 3}"],
                project="payment-service",
                type=MemoryType.decision,
            )
            for i in range(12)
        ]

        queries = generate_queries_from_pool(items, target_count=30)
        semantic = [q for q in queries if q.category == "semantic_paraphrase"]

        assert semantic
        assert all(q.expected_ids for q in semantic)
        assert any(q.query == "fault tolerance rollout" for q in semantic)

    def test_linked_items_generate_associative_query_category(self):
        source, _ = _item(
            1,
            id="mem-20260527-100000-source",
            title="Checkout incident timeline",
            summary="The incident analysis references the rollback decision",
            tags=["checkout", "incident"],
            project="checkout-service",
        )
        target, target_body = _item(
            2,
            id="mem-20260527-100000-target",
            title="Rollback decision",
            summary="Decision to revert the payment gateway deploy",
            tags=["checkout", "decision"],
            project="checkout-service",
        )
        source.refs.mems = [target.id]

        queries = generate_queries_from_pool([(source, "source body"), (target, target_body)], target_count=10)
        linked = [q for q in queries if q.category == "linked_association"]

        assert linked
        assert linked[0].query == "Checkout incident timeline"
        assert linked[0].expected_ids == [target.id]

    def test_synthetic_items_include_linked_association_queries(self):
        items = generate_synthetic_items(30)

        queries = generate_queries_from_pool(items, target_count=20)
        linked = [q for q in queries if q.category == "linked_association"]

        assert linked
        assert linked[0].expected_ids

    def test_loads_hand_labeled_query_fixture(self):
        fixture = Path("tests/fixtures/relevance/hand_labeled_queries.json")

        queries = load_labeled_queries(fixture)

        assert len(queries) >= 6
        assert all(isinstance(q, RelevanceQuery) for q in queries)
        categories = {q.category for q in queries}
        assert {
            "semantic_paraphrase",
            "linked_association",
            "memory_risk",
            "false_friend",
        } <= categories
        assert all(q.expected_ids for q in queries)
        assert any("stale memory" in q.query for q in queries)

    def test_labeled_query_fixture_rejects_missing_expected_ids(self, tmp_path):
        fixture = tmp_path / "bad-queries.json"
        fixture.write_text(
            '[{"query": "missing ids", "category": "bad"}]',
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="expected_ids"):
            load_labeled_queries(fixture)

    def test_ablation_suite_runs_stable_variant_set(self):
        items = generate_synthetic_items(24)
        queries = generate_queries_from_pool(items, target_count=8)

        reports = run_ablation_suite(items, queries)

        assert set(reports) == set(ablation_variants())
        assert {
            "bm25_only",
            "vector_only",
            "rrf",
            "rrf_decay",
            "rrf_graph",
            "rrf_mmr",
            "rrf_hopfield",
            "rrf_context_firewall",
        } <= set(reports)
        assert all(report.num_queries == len(queries) for report in reports.values())
        assert all(hasattr(report, "mean_token_cost") for report in reports.values())
        assert all(hasattr(report, "mean_stale_hit_rate") for report in reports.values())

    def test_benchmark_reports_token_cost_and_stale_hit_rate(self):
        stale_item, stale_body = _item(
            100,
            id="mem-20260527-100000-stale-rate",
            title="Stale browser permission",
            summary="Stale browser permission locator",
            tags=["stale-state"],
        )
        fresh_item, fresh_body = _item(
            101,
            id="mem-20260527-100000-fresh-rate",
            title="Fresh browser permission",
            summary="Fresh browser permission locator",
            tags=["current"],
        )
        query = RelevanceQuery(
            query="Stale browser permission",
            expected_ids=[stale_item.id],
            category="stale_metric",
        )

        report = run_relevance_benchmark(
            [(stale_item, stale_body), (fresh_item, fresh_body)],
            [query],
            retriever_kwargs={
                "apply_decay": False,
                "record_access": False,
                "rerank": False,
            },
        )

        assert report.mean_token_cost > 0
        assert report.mean_stale_hit_rate > 0
        assert report.per_query[0].token_cost > 0
        assert report.per_query[0].stale_hit_rate > 0
