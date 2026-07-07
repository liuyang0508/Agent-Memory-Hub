#!/usr/bin/env python3
"""Retrieval relevance benchmark — measures search quality, not just speed.

Generates ground-truth queries from the real or synthetic brain pool and
computes MRR, Precision@K, Recall@K, NDCG@K.  Designed to run in CI or
interactively to guide retrieval improvements.

Usage:
    python benchmarks/benchmark_relevance.py                 # real pool
    python benchmarks/benchmark_relevance.py --synthetic 200 # synthetic
    python benchmarks/benchmark_relevance.py --format json   # machine output
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from agent_brain.platform.embedding import get_default_embedder  # noqa: E402
from agent_brain.platform.indexing.index import HubIndex  # noqa: E402
from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall  # noqa: E402
from agent_brain.memory.context.context_loading import (  # noqa: E402
    render_context_view,
    select_context_view,
)
from agent_brain.memory.recall.embedding_text import embedding_text_for_item  # noqa: E402
from agent_brain.memory.recall.retrieval_budget import estimate_tokens  # noqa: E402
from agent_brain.memory.store.items_store import ItemsStore  # noqa: E402
from agent_brain.memory.recall.retrieval import Retriever  # noqa: E402
from agent_brain.contracts.memory_item import MemoryItem  # noqa: E402


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RelevanceQuery:
    query: str
    expected_ids: list[str]
    category: str
    description: str = ""


@dataclass
class QueryResult:
    query: str
    category: str
    retrieved_ids: list[str]
    expected_ids: list[str]
    mrr: float
    precision_at_5: float
    precision_at_10: float
    recall_at_5: float
    recall_at_10: float
    ndcg_at_10: float
    token_cost: int = 0
    stale_hit_rate: float = 0.0


@dataclass
class BenchmarkReport:
    num_queries: int
    num_items_indexed: int
    index_build_time_s: float
    query_time_s: float
    mean_mrr: float
    mean_precision_at_5: float
    mean_precision_at_10: float
    mean_recall_at_5: float
    mean_recall_at_10: float
    mean_ndcg_at_10: float
    mean_token_cost: float = 0.0
    mean_stale_hit_rate: float = 0.0
    per_category: dict[str, dict[str, float]] = field(default_factory=dict)
    per_query: list[QueryResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def reciprocal_rank(retrieved: list[str], relevant: set[str]) -> float:
    for i, rid in enumerate(retrieved):
        if rid in relevant:
            return 1.0 / (i + 1)
    return 0.0


def precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    if k == 0:
        return 0.0
    top = retrieved[:k]
    return sum(1 for r in top if r in relevant) / k


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 1.0
    top = retrieved[:k]
    return sum(1 for r in top if r in relevant) / min(len(relevant), k)


def ndcg_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    dcg = 0.0
    for i, rid in enumerate(retrieved[:k]):
        if rid in relevant:
            dcg += 1.0 / math.log2(i + 2)
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    if idcg == 0:
        return 0.0
    return dcg / idcg


# ---------------------------------------------------------------------------
# Ground-truth generation
# ---------------------------------------------------------------------------

NOISE_TAGS = {"session-active", "needs-review", "auto-captured"}

TITLE_PARAPHRASES = {
    "API Design Decision": "interface contract choice",
    "Database Migration Plan": "move database rollout plan",
    "Performance Optimization": "speed improvement work",
    "Security Review Findings": "security audit issues",
    "Code Refactoring Strategy": "code cleanup approach",
    "Deployment Pipeline Update": "release automation change",
    "Monitoring Alert Configuration": "observability alert setup",
    "Cache Invalidation Pattern": "stale cache refresh approach",
    "Authentication Flow Change": "login journey update",
    "Error Handling Improvement": "failure response cleanup",
    "Logging Standard Update": "log format guideline change",
    "Testing Framework Selection": "test tool choice",
    "Dependency Upgrade Plan": "library update rollout",
    "Configuration Management": "settings governance approach",
    "Service Mesh Integration": "service networking adoption",
    "Data Schema Evolution": "data model migration",
    "Rate Limiting Strategy": "traffic throttling approach",
    "Circuit Breaker Implementation": "fault tolerance rollout",
    "Load Balancing Configuration": "traffic distribution setup",
    "Backup Policy Definition": "restore policy decision",
}


def _is_noise(item: MemoryItem) -> bool:
    return bool(set(item.tags or []) & NOISE_TAGS)


def generate_queries_from_pool(
    items: list[tuple[MemoryItem, str]],
    target_count: int = 50,
    include_categories: set[str] | None = None,
) -> list[RelevanceQuery]:
    """Build ground-truth queries from real pool items."""
    meaningful = [(item, body) for item, body in items if not _is_noise(item)]
    if not meaningful:
        return []

    buckets: dict[str, list[RelevanceQuery]] = {
        "title_recall": [],
        "tag_keyword": [],
        "project_scope": [],
        "type_scope": [],
        "multi_keyword": [],
        "semantic_paraphrase": [],
        "linked_association": [],
    }

    # --- Category 1: Title recall (use item title → expect that item) ---
    title_sample = meaningful[:min(12, len(meaningful))]
    for item, _ in title_sample:
        buckets["title_recall"].append(RelevanceQuery(
            query=item.title,
            expected_ids=[item.id],
            category="title_recall",
            description=f"Exact title lookup for '{item.title[:40]}'",
        ))

    # --- Category 2: Tag-based (search by distinctive tag → expect tagged items) ---
    from collections import Counter
    tag_items: dict[str, list[str]] = {}
    tag_freq = Counter()
    for item, _ in meaningful:
        for t in (item.tags or []):
            if t not in NOISE_TAGS:
                tag_items.setdefault(t, []).append(item.id)
                tag_freq[t] += 1
    good_tags = [t for t, c in tag_freq.most_common() if 2 <= c <= 15]
    for tag in good_tags[:min(12, len(good_tags))]:
        buckets["tag_keyword"].append(RelevanceQuery(
            query=tag.replace("-", " "),
            expected_ids=tag_items[tag],
            category="tag_keyword",
            description=f"Tag-based keyword '{tag}' ({len(tag_items[tag])} items)",
        ))

    # --- Category 3: Project-scoped (project name → expect items from that project) ---
    project_items: dict[str, list[str]] = {}
    for item, _ in meaningful:
        if item.project:
            project_items.setdefault(item.project, []).append(item.id)
    for proj, ids in sorted(project_items.items(), key=lambda x: -len(x[1])):
        if len(ids) >= 2:
            buckets["project_scope"].append(RelevanceQuery(
                query=proj.replace("-", " "),
                expected_ids=ids,
                category="project_scope",
                description=f"Project '{proj}' ({len(ids)} items)",
            ))
        if len(buckets["project_scope"]) >= max(1, target_count // len(buckets)):
            break

    # --- Category 4: Type-scoped (e.g., "decision" → expect decision items) ---
    type_items: dict[str, list[str]] = {}
    for item, _ in meaningful:
        type_items.setdefault(str(item.type), []).append(item.id)
    for t, ids in type_items.items():
        if len(ids) >= 3:
            buckets["type_scope"].append(RelevanceQuery(
                query=t,
                expected_ids=ids,
                category="type_scope",
                description=f"Type '{t}' ({len(ids)} items)",
            ))

    # --- Category 5: Multi-keyword (combine project + concept) ---
    for item, body in meaningful[:10]:
        words = item.title.split()
        if len(words) >= 3 and item.project:
            query = f"{item.project.replace('-', ' ')} {item.title}"
            buckets["multi_keyword"].append(RelevanceQuery(
                query=query,
                expected_ids=[item.id],
                category="multi_keyword",
                description=f"Combined project+keyword for '{item.title[:40]}'",
            ))

    # --- Category 6: Semantic paraphrase ---
    # Query uses related wording, not title text.
    buckets["semantic_paraphrase"].extend(_semantic_paraphrase_queries(meaningful))

    # --- Category 7: Linked association ---
    # Query one memory and expect explicitly linked memories. This is a
    # benchmark-only anchor for associative-memory ideas; production ranking
    # changes still need before/after evidence.
    buckets["linked_association"].extend(_linked_association_queries(meaningful))

    if include_categories is not None:
        buckets = {
            name: queries
            for name, queries in buckets.items()
            if name in include_categories
        }
    return _balanced_queries(buckets, target_count)


def _semantic_paraphrase_queries(
    items: list[tuple[MemoryItem, str]],
) -> list[RelevanceQuery]:
    title_ids: dict[str, list[str]] = {}
    for item, _body in items:
        if item.title in TITLE_PARAPHRASES:
            title_ids.setdefault(item.title, []).append(item.id)

    queries: list[RelevanceQuery] = []
    for title, query in TITLE_PARAPHRASES.items():
        ids = title_ids.get(title)
        if not ids:
            continue
        queries.append(RelevanceQuery(
            query=query,
            expected_ids=ids,
            category="semantic_paraphrase",
            description=f"Paraphrased title lookup for '{title}'",
        ))
    return queries


def _linked_association_queries(
    items: list[tuple[MemoryItem, str]],
) -> list[RelevanceQuery]:
    item_ids = {item.id for item, _ in items}
    queries: list[RelevanceQuery] = []
    for item, _body in items:
        linked_ids = [mid for mid in item.refs.mems if mid in item_ids]
        if not linked_ids:
            continue
        queries.append(RelevanceQuery(
            query=item.title,
            expected_ids=linked_ids[:5],
            category="linked_association",
            description=f"Explicit memory-link association from '{item.title[:40]}'",
        ))
    return queries


def _balanced_queries(
    buckets: dict[str, list[RelevanceQuery]],
    target_count: int,
) -> list[RelevanceQuery]:
    """Return a bounded query set without letting title recall crowd out all
    other categories when ``target_count`` is small."""
    ordered = [name for name, queries in buckets.items() if queries]
    if target_count <= 0 or not ordered:
        return []

    selected: list[RelevanceQuery] = []
    offsets = {name: 0 for name in ordered}

    while len(selected) < target_count:
        progressed = False
        for name in ordered:
            offset = offsets[name]
            if offset >= len(buckets[name]):
                continue
            selected.append(buckets[name][offset])
            offsets[name] = offset + 1
            progressed = True
            if len(selected) >= target_count:
                break
        if not progressed:
            break

    return selected


def load_labeled_queries(path: Path) -> list[RelevanceQuery]:
    """Load hand-labeled relevance queries from a JSON fixture.

    The fixture is intentionally plain JSON so a reviewer can inspect expected
    IDs before accepting research-inspired retrieval changes.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid labeled query JSON at {path}: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError(f"labeled query fixture must be a list: {path}")

    queries: list[RelevanceQuery] = []
    for idx, row in enumerate(raw):
        if not isinstance(row, dict):
            raise ValueError(f"labeled query #{idx} must be an object")
        query = row.get("query")
        expected_ids = row.get("expected_ids")
        category = row.get("category")
        description = row.get("description", "")
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"labeled query #{idx} has missing query")
        if not isinstance(expected_ids, list) or not expected_ids:
            raise ValueError(f"labeled query #{idx} has missing expected_ids")
        if not all(isinstance(item_id, str) and item_id for item_id in expected_ids):
            raise ValueError(f"labeled query #{idx} expected_ids must be non-empty strings")
        if not isinstance(category, str) or not category.strip():
            raise ValueError(f"labeled query #{idx} has missing category")
        if not isinstance(description, str):
            raise ValueError(f"labeled query #{idx} description must be a string")
        queries.append(RelevanceQuery(
            query=query,
            expected_ids=expected_ids,
            category=category,
            description=description,
        ))
    return queries


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def run_relevance_benchmark(
    items: list[tuple[MemoryItem, str]],
    queries: list[RelevanceQuery],
    retriever_kwargs: dict | None = None,
) -> BenchmarkReport:
    os.environ["MEMORY_HUB_TEST_EMBEDDING"] = "1"
    retriever_kwargs = dict(retriever_kwargs or {})
    use_context_firewall = bool(retriever_kwargs.pop("context_firewall", False))

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "index.db"
        embedder = get_default_embedder()
        index = HubIndex(db_path, embedding_dim=embedder.dim)
        item_bodies = {item.id: (item, body) for item, body in items}

        # Build index
        t0 = time.perf_counter()
        for item, body in items:
            index.upsert(item, body, embedding=embedder.embed(embedding_text_for_item(item)))
        build_time = time.perf_counter() - t0

        retriever = Retriever(index=index, embedder=embedder, **retriever_kwargs)

        # Run queries
        results: list[QueryResult] = []
        t0 = time.perf_counter()
        for q in queries:
            hits = retriever.search(q.query, top_k=40 if use_context_firewall else 10)
            firewall_decisions = {}
            if use_context_firewall:
                candidates = [
                    ContextCandidate(
                        item=item_bodies[hit.id][0],
                        body=item_bodies[hit.id][1],
                        score=hit.score,
                    )
                    for hit in hits
                    if hit.id in item_bodies
                ]
                firewall_result = ContextFirewall().filter(
                    candidates,
                    query=q.query,
                    max_items=10,
                )
                firewall_decisions = {
                    decision.candidate.item.id: decision
                    for decision in firewall_result.included
                }
                included_ids = set(firewall_decisions)
                hits = [hit for hit in hits if hit.id in included_ids][:10]
            retrieved_ids = [h.id for h in hits]
            relevant = set(q.expected_ids)
            token_cost = _token_cost_for_hits(
                retrieved_ids,
                item_bodies,
                firewall_decisions=firewall_decisions,
                use_context_firewall=use_context_firewall,
            )
            stale_hit_rate = _stale_hit_rate(retrieved_ids, item_bodies)

            results.append(QueryResult(
                query=q.query,
                category=q.category,
                retrieved_ids=retrieved_ids,
                expected_ids=q.expected_ids,
                mrr=reciprocal_rank(retrieved_ids, relevant),
                precision_at_5=precision_at_k(retrieved_ids, relevant, 5),
                precision_at_10=precision_at_k(retrieved_ids, relevant, 10),
                recall_at_5=recall_at_k(retrieved_ids, relevant, 5),
                recall_at_10=recall_at_k(retrieved_ids, relevant, 10),
                ndcg_at_10=ndcg_at_k(retrieved_ids, relevant, 10),
                token_cost=token_cost,
                stale_hit_rate=stale_hit_rate,
            ))
        query_time = time.perf_counter() - t0

        index.close()

    # Aggregate
    n = len(results)

    def mean(vals: list[float]) -> float:
        return sum(vals) / len(vals) if vals else 0.0

    # Per-category breakdown
    from collections import defaultdict
    cats: dict[str, list[QueryResult]] = defaultdict(list)
    for r in results:
        cats[r.category].append(r)

    per_category = {}
    for cat, cat_results in cats.items():
        per_category[cat] = {
            "count": len(cat_results),
            "mrr": mean([r.mrr for r in cat_results]),
            "precision_at_5": mean([r.precision_at_5 for r in cat_results]),
            "recall_at_10": mean([r.recall_at_10 for r in cat_results]),
            "ndcg_at_10": mean([r.ndcg_at_10 for r in cat_results]),
            "token_cost": mean([float(r.token_cost) for r in cat_results]),
            "stale_hit_rate": mean([r.stale_hit_rate for r in cat_results]),
        }

    return BenchmarkReport(
        num_queries=n,
        num_items_indexed=len(items),
        index_build_time_s=build_time,
        query_time_s=query_time,
        mean_mrr=mean([r.mrr for r in results]),
        mean_precision_at_5=mean([r.precision_at_5 for r in results]),
        mean_precision_at_10=mean([r.precision_at_10 for r in results]),
        mean_recall_at_5=mean([r.recall_at_5 for r in results]),
        mean_recall_at_10=mean([r.recall_at_10 for r in results]),
        mean_ndcg_at_10=mean([r.ndcg_at_10 for r in results]),
        mean_token_cost=mean([float(r.token_cost) for r in results]),
        mean_stale_hit_rate=mean([r.stale_hit_rate for r in results]),
        per_category=per_category,
        per_query=results,
    )


def _token_cost_for_hits(
    retrieved_ids: list[str],
    item_bodies: dict[str, tuple[MemoryItem, str]],
    *,
    firewall_decisions: dict,
    use_context_firewall: bool,
) -> int:
    total = 0
    for item_id in retrieved_ids:
        item_body = item_bodies.get(item_id)
        if item_body is None:
            continue
        item, body = item_body
        if use_context_firewall:
            selection = select_context_view(
                item,
                body,
                requested="auto",
                firewall_decision=firewall_decisions.get(item_id),
            )
            text = render_context_view(item, body, selection.view)
        else:
            text = render_context_view(item, body, "locator")
        total += estimate_tokens(text)
    return total


def _stale_hit_rate(
    retrieved_ids: list[str],
    item_bodies: dict[str, tuple[MemoryItem, str]],
) -> float:
    if not retrieved_ids:
        return 0.0
    stale = 0
    for item_id in retrieved_ids:
        item_body = item_bodies.get(item_id)
        if item_body is None:
            continue
        item, _body = item_body
        if _is_stale_benchmark_item(item):
            stale += 1
    return stale / len(retrieved_ids)


def _is_stale_benchmark_item(item: MemoryItem) -> bool:
    tags = {tag.lower() for tag in item.tags}
    if tags & {"stale", "stale-state", "outdated", "expired"}:
        return True
    return bool(getattr(item, "superseded_by", None))


def ablation_variants() -> dict[str, dict]:
    """Return retrieval variants for formula-axis regression checks."""
    common = {
        "rerank": False,
        "record_access": False,
        "query_expansion": True,
    }
    return {
        "bm25_only": {
            **common,
            "bm25_weight": 1.0,
            "vector_weight": 0.0,
            "vector_top": 0,
            "apply_decay": False,
        },
        "vector_only": {
            **common,
            "bm25_weight": 0.0,
            "bm25_top": 0,
            "vector_weight": 1.0,
            "apply_decay": False,
        },
        "rrf": {
            **common,
            "bm25_weight": 1.0,
            "vector_weight": 1.0,
            "apply_decay": False,
        },
        "rrf_decay": {
            **common,
            "bm25_weight": 1.0,
            "vector_weight": 1.0,
            "apply_decay": True,
        },
        "rrf_graph": {
            **common,
            "bm25_weight": 1.0,
            "vector_weight": 1.0,
            "apply_decay": False,
            "graph_expand": True,
            "graph_depth": 1,
        },
        "rrf_mmr": {
            **common,
            "bm25_weight": 1.0,
            "vector_weight": 1.0,
            "apply_decay": False,
            "mmr_lambda": 0.7,
        },
        "rrf_hopfield": {
            **common,
            "bm25_weight": 1.0,
            "vector_weight": 1.0,
            "apply_decay": False,
            "hopfield_expand": True,
            "hopfield_top": 20,
        },
        "rrf_context_firewall": {
            **common,
            "bm25_weight": 1.0,
            "vector_weight": 1.0,
            "apply_decay": False,
            "context_firewall": True,
        },
    }


def run_ablation_suite(
    items: list[tuple[MemoryItem, str]],
    queries: list[RelevanceQuery],
) -> dict[str, BenchmarkReport]:
    """Run all retrieval ablation variants on the same corpus and queries."""
    return {
        name: run_relevance_benchmark(items, queries, retriever_kwargs=kwargs)
        for name, kwargs in ablation_variants().items()
    }


def print_ablation_report(reports: dict[str, BenchmarkReport], fmt: str = "text") -> None:
    if fmt == "json":
        print(json.dumps({
            "ablation": {
                name: asdict(report)
                for name, report in reports.items()
            }
        }, indent=2, default=str))
        return

    print(f"\n{'='*60}")
    print("Retrieval Relevance Ablation")
    print(f"{'='*60}")
    print("variant               MRR    P@5    R@10   NDCG@10  tokens  stale")
    for name, report in reports.items():
        print(
            f"{name:<20} "
            f"{report.mean_mrr:>5.3f}  "
            f"{report.mean_precision_at_5:>5.3f}  "
            f"{report.mean_recall_at_10:>5.3f}  "
            f"{report.mean_ndcg_at_10:>7.3f}  "
            f"{report.mean_token_cost:>6.1f}  "
            f"{report.mean_stale_hit_rate:>5.3f}"
        )


def print_report(report: BenchmarkReport, fmt: str = "text") -> None:
    if fmt == "json":
        print(json.dumps(asdict(report), indent=2, default=str))
        return

    print(f"\n{'='*60}")
    print("Retrieval Relevance Benchmark")
    print(f"{'='*60}")
    print(f"Items indexed: {report.num_items_indexed}")
    print(f"Queries run:   {report.num_queries}")
    print(f"Index build:   {report.index_build_time_s:.2f}s")
    print(f"Query time:    {report.query_time_s:.3f}s")
    print()
    print(f"  MRR:            {report.mean_mrr:.3f}")
    print(f"  Precision@5:    {report.mean_precision_at_5:.3f}")
    print(f"  Precision@10:   {report.mean_precision_at_10:.3f}")
    print(f"  Recall@5:       {report.mean_recall_at_5:.3f}")
    print(f"  Recall@10:      {report.mean_recall_at_10:.3f}")
    print(f"  NDCG@10:        {report.mean_ndcg_at_10:.3f}")
    print(f"  Token cost:     {report.mean_token_cost:.1f}")
    print(f"  Stale hit rate: {report.mean_stale_hit_rate:.3f}")
    print()
    print("Per category:")
    for cat, stats in sorted(report.per_category.items()):
        print(f"  {cat} (n={stats['count']}):")
        print(f"    MRR={stats['mrr']:.3f}  P@5={stats['precision_at_5']:.3f}"
              f"  R@10={stats['recall_at_10']:.3f}  NDCG@10={stats['ndcg_at_10']:.3f}")

    # Show worst queries
    worst = sorted(report.per_query, key=lambda r: r.mrr)[:5]
    if worst:
        print("\nWorst 5 queries (by MRR):")
        for r in worst:
            print(f"  [{r.category}] '{r.query[:50]}' → MRR={r.mrr:.3f} R@10={r.recall_at_10:.3f}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Retrieval relevance benchmark")
    parser.add_argument("--synthetic", type=int, default=0,
                        help="Use N synthetic items instead of real pool")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--queries", type=int, default=50)
    parser.add_argument("--queries-file", type=Path, default=None,
                        help="Use hand-labeled relevance queries from JSON")
    parser.add_argument("--ablation", action="store_true",
                        help="Run BM25/vector/RRF/decay/graph/MMR variants")
    args = parser.parse_args()

    if args.synthetic > 0:
        from benchmark_retrieval import generate_synthetic_items
        items = generate_synthetic_items(args.synthetic)
    else:
        pool_dir = Path(os.path.expanduser("~/.agent-memory-hub/items"))
        if not pool_dir.exists():
            print(f"No brain pool at {pool_dir}, use --synthetic N", file=sys.stderr)
            sys.exit(1)
        store = ItemsStore(pool_dir)
        items = list(store.iter_all())

    if args.queries_file is not None:
        queries = load_labeled_queries(args.queries_file)
        if args.queries > 0:
            queries = queries[:args.queries]
    else:
        queries = generate_queries_from_pool(items, target_count=args.queries)
    if not queries:
        print("No meaningful queries could be generated from the pool.", file=sys.stderr)
        sys.exit(1)

    if args.ablation:
        reports = run_ablation_suite(items, queries)
        print_ablation_report(reports, fmt=args.format)
    else:
        report = run_relevance_benchmark(items, queries)
        print_report(report, fmt=args.format)


if __name__ == "__main__":
    main()
