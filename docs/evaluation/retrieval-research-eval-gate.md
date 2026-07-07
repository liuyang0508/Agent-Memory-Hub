# Retrieval Research Evaluation Gate

This is the public source-of-truth for retrieval research changes. It keeps
experimental ideas such as Hopfield expansion, HRR-style associative recall, and
OpenViking-inspired tiered context work behind an evaluation gate.

## Rule

There is no production retrieval change without an evaluation run. A proposal
must first show benchmark evidence and compare it against the current
`rrf_context_firewall` baseline.

## Required Evidence

- Run `benchmarks/benchmark_relevance.py --synthetic` before any production code
  change.
- Run `benchmarks/benchmark_relevance.py --synthetic --ablation` when changing
  ranking, expansion, filtering, or packing behavior.
- Run hand-labeled coverage with
  `--queries-file tests/fixtures/relevance/hand_labeled_queries.json`.
- Report MRR, Precision@5, Recall@10, NDCG@10, token cost, and stale hit rate.

## Boundary

Hopfield, HRR, OpenViking, graph expansion, rerankers, and compression can be
implemented as optional experiments, but they do not become the default path
until the gate shows an improvement without higher stale hit rate or token cost.
