"""Recall quality evaluation: a gold set of prompts through the hook channel.

The design follows three rules:

* **Measure what the member sees.** Cases run through ``retrieve`` on the hook
  channel with the same limit, kinds, gate and dedup as prompt-time recall,
  with ``record=False`` so an evaluation leaves no trace, feedback or insight
  behind.
* **Precision and abstention count as much as recall.** A wrong memory in the
  prompt costs the member attention and can mislead the model, so the report
  scores what was injected (precision, unanswerable prompts that still got
  memories) next to whether the right memory was found (hit@1, hit@3, MRR).
* **Never tune and report on the same cases.** Each case belongs to the
  ``dev`` split (thresholds are chosen there) or the held-out ``test`` split
  (only reported), stably by its id unless the case names its split.
  Small sets get bootstrap 95% intervals so a change is not read into noise.

Gold cases name expected memories by stable keys (a path or title fragment,
or a phrase of the body) rather than asset ids, so one gold set works on any
copy of the organization's Hub. ``expect: []`` marks an unanswerable prompt.
"""

from __future__ import annotations

import hashlib
import json
import random
import statistics
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

SPLITS = ("dev", "test")
# Headline metrics a regression check compares; "lower" ones are better low.
HEADLINE = ("hit@1", "hit@3", "mrr", "precision", "abstention")
LOWER_IS_BETTER = ("false_injection", "latency_p95_ms")


def load_gold(path: Path) -> list[dict[str, Any]]:
    """Read a gold set from JSON (a list) or JSONL; validate each case."""
    text = Path(path).read_text(encoding="utf-8")
    stripped = text.lstrip()
    raw = (
        json.loads(text)
        if stripped.startswith("[")
        else [json.loads(line) for line in text.splitlines() if line.strip()]
    )
    return parse_gold(raw)


def parse_gold(raw: Iterable[Any]) -> list[dict[str, Any]]:
    """Validate gold cases given as dicts (a file's entries or an upload)."""
    cases, seen = [], set()
    for entry in raw:
        if not isinstance(entry, dict) or "id" not in entry:
            raise ValueError("gold case must be an object with an id")
        case_id = str(entry["id"])
        query = str(entry.get("query", entry.get("q", "")))
        split = entry.get("split") or split_of(case_id)
        if not query.strip() or case_id in seen or split not in SPLITS:
            raise ValueError(f"invalid or duplicate gold case: {case_id}")
        case = {
            "id": case_id,
            "query": query,
            "expect": [str(key) for key in entry.get("expect", entry.get("targets")) or []],
            "tags": [str(tag) for tag in entry.get("tags") or []],
            "split": split,
        }
        seen.add(case_id)
        cases.append(case)
    return cases


def split_of(case_id: str) -> str:
    """A stable two-thirds/one-third dev/test assignment by case id."""
    return "test" if hashlib.sha256(case_id.encode()).digest()[0] % 3 == 0 else "dev"


def relevant(item: dict[str, Any], expect: Sequence[str]) -> bool:
    """Whether a returned memory is one the case expects."""
    fields = [str(item.get(k) or "") for k in ("path", "title", "document_id", "body")]
    return any(key and any(key in field for field in fields) for key in expect)


def judge(case: dict[str, Any], items: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Per-case outcome: the first relevant rank and how much was injected."""
    flags = [relevant(item, case["expect"]) for item in items] if case["expect"] else []
    first = next((rank for rank, ok in enumerate(flags, 1) if ok), None)
    return {
        "id": case["id"],
        "split": case["split"],
        "tags": case["tags"],
        "answerable": bool(case["expect"]),
        "returned": len(items),
        "relevant_returned": sum(flags),
        "first_relevant": first,
    }


def _ci(values: Sequence[float], fn: Callable[[Sequence[float]], float]) -> list[float]:
    if len(values) < 2:
        return [fn(values), fn(values)] if values else [0.0, 0.0]
    rng = random.Random(0)
    draws = sorted(fn([values[rng.randrange(len(values))] for _ in values]) for _ in range(1000))
    return [round(draws[25], 3), round(draws[974], 3)]


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def summarize(
    outcomes: Sequence[dict[str, Any]], latencies: Sequence[float] = ()
) -> dict[str, Any]:
    """Headline metrics with bootstrap 95% intervals."""
    answerable = [o for o in outcomes if o["answerable"]]
    unanswerable = [o for o in outcomes if not o["answerable"]]
    first = [o["first_relevant"] for o in answerable]
    columns = {
        "hit@1": [1.0 if r == 1 else 0.0 for r in first],
        "hit@3": [1.0 if r and r <= 3 else 0.0 for r in first],
        "mrr": [1.0 / r if r else 0.0 for r in first],
        "abstention": [1.0 if o["returned"] == 0 else 0.0 for o in unanswerable],
    }
    report: dict[str, Any] = {
        "cases": len(outcomes),
        "answerable": len(answerable),
        "unanswerable": len(unanswerable),
    }
    for name, values in columns.items():
        report[name] = round(_mean(values), 3)
        report[name + "_ci95"] = _ci(values, _mean)
    returned = sum(o["returned"] for o in answerable)
    report["precision"] = round(
        sum(o["relevant_returned"] for o in answerable) / returned if returned else 0.0, 3
    )
    report["false_injection"] = round(1 - report["abstention"], 3) if unanswerable else 0.0
    report["mean_returned"] = round(_mean([o["returned"] for o in answerable]), 2)
    if latencies:
        ordered = sorted(latencies)
        report["latency_p50_ms"] = round(statistics.median(ordered) * 1000, 1)
        report["latency_p95_ms"] = round(
            ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] * 1000, 1
        )
    return report


def evaluate(
    hub: Any,
    token: str,
    cases: Iterable[dict[str, Any]],
    *,
    limit: int = 8,
) -> dict[str, Any]:
    """Run every case through hook-channel retrieval and score it."""
    outcomes, latencies, details = [], [], []
    for case in cases:
        started = time.perf_counter()
        result = hub.retrieve(
            token,
            case["query"],
            limit=limit,
            kinds=["memory", "rule"],
            channel="hook",
            record=False,
        )
        latencies.append(time.perf_counter() - started)
        items = result["items"]
        outcome = judge(case, items)
        outcomes.append(outcome)
        details.append(
            {
                **outcome,
                "mode": result["retrieval"]["mode"],
                "got": [
                    {
                        "title": str(item.get("title") or "")[:60],
                        "path": item.get("path"),
                        "relevant": relevant(item, case["expect"]) if case["expect"] else False,
                        "similarity": (item.get("semantic_match") or {}).get("similarity"),
                    }
                    for item in items
                ],
            }
        )
    report: dict[str, Any] = {"overall": summarize(outcomes, latencies)}
    for split in SPLITS:
        chosen = [o for o in outcomes if o["split"] == split]
        if chosen:
            report[split] = summarize(chosen)
    tags = sorted({tag for o in outcomes for tag in o["tags"]})
    report["by_tag"] = {tag: summarize([o for o in outcomes if tag in o["tags"]]) for tag in tags}
    report["cases"] = details
    return report


def regressions(
    report: dict[str, Any], baseline: dict[str, Any], *, tolerance: float = 0.02
) -> list[str]:
    """Headline metrics that got worse than the baseline beyond ``tolerance``."""
    found = []
    now, then = report.get("overall", {}), baseline.get("overall", {})
    for name in HEADLINE:
        if name in then and now.get(name, 0.0) < then[name] - tolerance:
            found.append(f"{name}: {then[name]} -> {now.get(name)}")
    if (
        "false_injection" in then
        and now.get("false_injection", 0.0) > then["false_injection"] + tolerance
    ):
        found.append(f"false_injection: {then['false_injection']} -> {now.get('false_injection')}")
    return found
