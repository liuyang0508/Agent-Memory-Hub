#!/usr/bin/env python3
"""Performance benchmark for memory hub retrieval, governance, and drift detection.

Usage:
    python benchmarks/benchmark_retrieval.py --count 126
    python benchmarks/benchmark_retrieval.py --count 10000
    python benchmarks/benchmark_retrieval.py --count 100000
"""

from __future__ import annotations

import argparse
import random
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from agent_brain.platform.embedding import get_default_embedder
from agent_brain.platform.indexing.index import HubIndex
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.governance.drift import DriftDetector
from agent_brain.memory.governance.pipeline import GovernancePipeline
from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs


class ItemsStoreAdapter:
    """Adapter to make ItemsStore compatible with GovernancePipeline expectations.
    
    GovernancePipeline expects iter_all() to return MemoryItem objects directly,
    but ItemsStore returns (MemoryItem, body) tuples. This adapter unwraps the tuples.
    """
    
    def __init__(self, store: ItemsStore):
        self._store = store
    
    def iter_all(self):
        """Yield MemoryItem objects only (unwrapping tuples from underlying store)."""
        for item, body in self._store.iter_all():
            yield item


class ItemsStoreTupleAdapter:
    """Adapter that preserves the (MemoryItem, body) tuple format for DriftDetector.
    
    DriftDetector expects iter_all() to return (MemoryItem, body) tuples.
    This adapter passes through the tuples unchanged.
    """
    
    def __init__(self, store: ItemsStore):
        self._store = store
    
    def iter_all(self):
        """Yield (MemoryItem, body) tuples as-is."""
        yield from self._store.iter_all()


def generate_synthetic_items(count: int, seed: int = 42) -> list[tuple[MemoryItem, str]]:
    """Generate synthetic memory items with diverse content."""
    rng = random.Random(seed)

    titles_pool = [
        "API Design Decision",
        "Database Migration Plan",
        "Performance Optimization",
        "Security Review Findings",
        "Code Refactoring Strategy",
        "Deployment Pipeline Update",
        "Monitoring Alert Configuration",
        "Cache Invalidation Pattern",
        "Authentication Flow Change",
        "Error Handling Improvement",
        "Logging Standard Update",
        "Testing Framework Selection",
        "Dependency Upgrade Plan",
        "Configuration Management",
        "Service Mesh Integration",
        "Data Schema Evolution",
        "Rate Limiting Strategy",
        "Circuit Breaker Implementation",
        "Load Balancing Configuration",
        "Backup Policy Definition",
    ]

    projects_pool = [
        "payment-service",
        "user-auth",
        "order-management",
        "inventory-system",
        "notification-service",
        "analytics-pipeline",
        "search-engine",
        "recommendation-engine",
        "fraud-detection",
        "compliance-audit",
    ]

    tags_pool = [
        "architecture",
        "performance",
        "security",
        "database",
        "api",
        "testing",
        "deployment",
        "monitoring",
        "logging",
        "refactoring",
        "optimization",
        "migration",
        "configuration",
        "integration",
        "scalability",
    ]

    body_templates = [
        "We decided to use {tool} for {purpose}. This approach provides better {benefit} compared to alternatives.",
        "After evaluating multiple options, we chose {tool} because it offers superior {benefit} for our {purpose} needs.",
        "The team agreed on implementing {tool} to handle {purpose}. Key benefits include improved {benefit}.",
        "Migration to {tool} was completed successfully. The new system handles {purpose} with enhanced {benefit}.",
        "Implemented {tool} as the standard for {purpose}. This ensures consistent {benefit} across all services.",
    ]

    tools_pool = [
        "PostgreSQL",
        "Redis",
        "Kafka",
        "Elasticsearch",
        "GraphQL",
        "gRPC",
        "Docker",
        "Kubernetes",
        "Terraform",
        "Prometheus",
    ]

    purposes_pool = [
        "data persistence",
        "caching layer",
        "message queuing",
        "full-text search",
        "API communication",
        "service orchestration",
        "container management",
        "infrastructure provisioning",
        "metrics collection",
        "log aggregation",
    ]

    benefits_pool = [
        "scalability",
        "performance",
        "reliability",
        "maintainability",
        "observability",
        "security",
        "cost efficiency",
        "developer productivity",
        "system resilience",
        "data consistency",
    ]

    items: list[tuple[MemoryItem, str]] = []
    # Use aware datetime to match GovernancePipeline expectations (uses timezone.utc)
    now = datetime.now(timezone.utc)

    for i in range(count):
        # Generate unique ID
        item_id = f"mem-{now.strftime('%Y%m%d')}-{now.strftime('%H%M%S')}-{i:06d}"

        # Random selections
        title = rng.choice(titles_pool)
        project = rng.choice(projects_pool)
        num_tags = rng.randint(1, 5)
        tags = rng.sample(tags_pool, num_tags)
        tool = rng.choice(tools_pool)
        purpose = rng.choice(purposes_pool)
        benefit = rng.choice(benefits_pool)
        body_template = rng.choice(body_templates)

        # Generate summary and body
        summary = f"{title} for {project}: Using {tool} for {purpose}"
        body = body_template.format(tool=tool, purpose=purpose, benefit=benefit)

        # Add some variation to make content more diverse
        if rng.random() > 0.5:
            body += "\n\nAdditional context: This decision was made after thorough evaluation of alternatives."
        if rng.random() > 0.7:
            body += f"\nRelated documentation: https://example.com/docs/{project}/{title.lower().replace(' ', '-')}"

        # Create MemoryItem with aware datetime (matching GovernancePipeline's timezone.utc usage)
        created_at = now.replace(hour=rng.randint(0, 23), minute=rng.randint(0, 59))
        item = MemoryItem(
            id=item_id,
            type=rng.choice(list(MemoryType)),
            created_at=created_at,
            agent="benchmark-agent",
            session=f"bench-session-{rng.randint(1000, 9999)}",
            project=project,
            tags=tags,
            title=title,
            summary=summary,
            refs=Refs(),
        )

        items.append((item, body))

    _add_synthetic_memory_links(items)
    return items


def _add_synthetic_memory_links(items: list[tuple[MemoryItem, str]]) -> None:
    """Add sparse deterministic memory links for graph/association benchmarks."""
    for index in range(0, max(0, len(items) - 1), 10):
        source, _ = items[index]
        target, _ = items[index + 1]
        source.refs.mems = [target.id]


def compute_percentile(values: list[float], percentile: float) -> float:
    """Compute percentile from a list of values."""
    if not values:
        return 0.0
    sorted_values = sorted(values)
    index = int(len(sorted_values) * percentile / 100)
    index = min(index, len(sorted_values) - 1)
    return sorted_values[index]


def run_benchmark(count: int) -> None:
    """Run performance benchmark with specified number of items."""
    print(f"\n=== Benchmark: {count} items ===")

    # Set test mode for embedding
    import os
    os.environ["MEMORY_HUB_TEST_EMBEDDING"] = "1"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        items_dir = tmp_path / "items"
        db_path = tmp_path / "index.db"

        # Step 1: Generate synthetic items
        gen_start = time.perf_counter_ns()
        items = generate_synthetic_items(count)
        gen_time = (time.perf_counter_ns() - gen_start) / 1e9

        # Step 2: Write items to store
        store = ItemsStore(items_dir)
        write_start = time.perf_counter_ns()
        for item, body in items:
            store.write(item, body)
        write_time = (time.perf_counter_ns() - write_start) / 1e9

        # Step 3: Build index
        embedder = get_default_embedder()
        index = HubIndex(db_path, embedding_dim=embedder.dim)

        index_start = time.perf_counter_ns()
        for item, body in items:
            embedding = embedder.embed(f"{item.title} {item.summary}")
            index.upsert(item, body, embedding)
        index_time = (time.perf_counter_ns() - index_start) / 1e9

        print(f"Generation time: {gen_time:.2f}s")
        print(f"Write time: {write_time:.2f}s")
        print(f"Index build time: {index_time:.2f}s")

        # Step 4: Execute 100 random queries
        query_latencies: list[float] = []
        num_queries = 100

        # Prepare query terms from generated items
        query_terms = [item.title.split()[0] for item, _ in items[:min(50, len(items))]]

        for q_idx in range(num_queries):
            query = rng.choice(query_terms) if query_terms else "test"

            query_start = time.perf_counter_ns()

            # BM25 search
            _bm25_results = index.bm25_search(query, top_k=5)

            # Vector search
            query_embedding = embedder.embed(query)
            _vector_results = index.vector_search(query_embedding, top_k=5)

            query_end = time.perf_counter_ns()
            latency_ms = (query_end - query_start) / 1e6
            query_latencies.append(latency_ms)

        p50 = compute_percentile(query_latencies, 50)
        p95 = compute_percentile(query_latencies, 95)
        p99 = compute_percentile(query_latencies, 99)

        print(f"\nQuery latency ({num_queries} queries):")
        print(f"  p50: {p50:.1f}ms")
        print(f"  p95: {p95:.1f}ms")
        print(f"  p99: {p99:.1f}ms")

        # Step 5: Governance pipeline scan
        gov_start = time.perf_counter_ns()
        try:
            # Use adapter to make ItemsStore compatible with GovernancePipeline (expects MemoryItem objects)
            store_adapter = ItemsStoreAdapter(store)
            pipeline = GovernancePipeline(items_store=store_adapter, embedder=embedder)
            report = pipeline.run()
            gov_time = (time.perf_counter_ns() - gov_start) / 1e9
            print(f"\nGovernance scan time: {gov_time:.2f}s")
            print(f"  Issues found: {report.total_issues}")
        except Exception as e:
            gov_time = (time.perf_counter_ns() - gov_start) / 1e9
            print(f"\nGovernance scan time: {gov_time:.2f}s (failed: {type(e).__name__})")

        # Step 6: Drift detection
        drift_start = time.perf_counter_ns()
        try:
            # DriftDetector expects iter_all() to return (MemoryItem, body) tuples
            store_tuple_adapter = ItemsStoreTupleAdapter(store)
            detector = DriftDetector(items_store=store_tuple_adapter)
            drift_report = detector.detect()
            drift_time = (time.perf_counter_ns() - drift_start) / 1e9
            print(f"Drift detection time: {drift_time:.2f}s")
            print(f"  Findings: {drift_report.total_findings}")
        except Exception as e:
            drift_time = (time.perf_counter_ns() - drift_start) / 1e9
            print(f"Drift detection time: {drift_time:.2f}s (failed: {type(e).__name__})")

        index.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Memory Hub Performance Benchmark")
    parser.add_argument(
        "--count",
        type=int,
        default=126,
        help="Number of synthetic items to generate (default: 126)",
    )
    args = parser.parse_args()

    # Use fixed seed for reproducibility
    rng = random.Random(42)

    run_benchmark(args.count)
