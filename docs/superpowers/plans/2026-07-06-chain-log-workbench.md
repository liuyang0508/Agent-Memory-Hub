# Chain Log Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Web admin request-chain log workbench that shows complete hook-to-feedback flow, algorithm sub-stages, candidate score decisions, hover previews, and click-through detail drawers.

**Architecture:** Add a focused `agent_brain.product.chain_log` read model that aggregates existing runtime JSONL sidecars into stable request chains without exposing raw prompt/query/body text. Expose it through `web/api/routes/chain_logs.py`, then add a second tab inside the existing lineage console so memory-lineage and request-chain views share navigation but keep separate data contracts.

**Tech Stack:** Python dataclasses, FastAPI route modules, existing AMH runtime sidecar readers, vanilla JS/CSS inside `web/templates/dashboard.html`, pytest + FastAPI TestClient.

---

## Scope

This plan implements P0 from `docs/superpowers/specs/2026-07-06-chain-log-workbench-design.md`.

P0 includes:

- Chain read model from existing sidecars.
- `/api/chain-logs` list endpoint.
- `/api/chain-logs/{chain_id}` detail endpoint.
- Request-chain tab in the existing Web lineage console.
- Fixed main stages and fixed Retrieval algorithm stages, including `not_enabled`, `skipped`, and `not_observed`.
- Candidate trace table derived from injection cohorts, recall gaps, item metadata, and existing retrieval trace shape where available.
- Tests for grouping, sanitization, route surface, and dashboard wiring.

P0 does not change Retriever internals to emit richer per-algorithm before/after rows. It exposes missing algorithm evidence honestly as `not_observed`, and Task 6 adds the P1 test seam for later instrumentation.

## File Structure

- Create `agent_brain/product/chain_log.py`
  - Owns dataclasses, sidecar aggregation, chain correlation, fixed stage contract, fixed algorithm contract, sanitization, candidate metadata lookup, list/detail reports.
- Create `web/api/routes/chain_logs.py`
  - Owns FastAPI route glue only.
- Modify `web/app.py`
  - Imports and includes the new router.
- Modify `web/templates/dashboard.html`
  - Adds `记忆链路 / 请求链路` tabs inside lineage console, request-chain workbench, node rail, algorithm waterfall, detail drawer, and client-side render functions.
- Create `tests/unit/test_chain_log.py`
  - Unit coverage for read model, grouping, completeness, algorithm visibility, sanitization, candidate decisions.
- Modify `tests/unit/test_web_api.py`
  - API auth, response shape, dashboard strings, route count.
- Modify `tests/conformance/test_web_surface_lock.py`
  - Adds two GET routes.
- Modify `tests/unit/test_cli_smoke.py`
  - Updates discovered API count and checks new routes are listed.
- Modify docs only if route-count documentation tests require it.

## Task 1: Add Chain Read Model Tests

**Files:**
- Create: `tests/unit/test_chain_log.py`
- Read: `agent_brain/agent_integrations/runtime_events.py`
- Read: `agent_brain/memory/context/injection_cohorts.py`
- Read: `agent_brain/memory/governance/recall_events.py`
- Read: `agent_brain/memory/store/write_service.py`

- [ ] **Step 1: Write failing tests for request grouping and sanitization**

Create `tests/unit/test_chain_log.py` with this starting content:

```python
"""Tests for the Web request-chain log read model."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.store.write_service import WriteService


def _item(item_id: str = "mem-20260706-010203-chain-demo") -> MemoryItem:
    return MemoryItem(
        id=item_id,
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        agent="codex",
        session="sess-chain",
        project="agent-memory-hub",
        tags=["chain-log", "retrieval"],
        title="Chain log demo",
        summary="Used to verify request-chain reporting",
        refs=Refs(),
        confidence=0.84,
    )


def _write_item(brain_dir: Path, item_id: str = "mem-20260706-010203-chain-demo") -> str:
    item = _item(item_id)
    WriteService(ItemsStore(brain_dir / "items"), brain_dir=brain_dir).write(
        item=item,
        body="secret memory body should not leak into chain logs",
        allow_unsafe=True,
    )
    return item.id


def test_chain_log_groups_hook_injection_and_gap_by_session(tmp_path: Path) -> None:
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.memory.governance.recall_events import record_gap
    from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report

    item_id = _write_item(tmp_path)
    record_runtime_event(
        tmp_path,
        adapter="codex",
        event_name="UserPromptSubmit",
        session_id="sess-chain",
        cwd="/repo/agent-memory-hub",
    )
    record_injection_cohort(
        tmp_path,
        item_ids=[item_id],
        adapter="codex",
        session_id="sess-chain",
        cwd="/repo/agent-memory-hub",
        query="raw user query should not leak",
        source="search",
        pack_metrics={
            "context_pack_chars": 128,
            "detail_refs": 1,
            "candidate_count": 3,
            "query_terms_count": 4,
        },
    )
    record_gap(
        tmp_path,
        query="another raw query should not leak",
        reason="partial_candidates_rejected",
        injected_ids=[item_id],
        rejected_ids=["mem-20260706-010203-rejected"],
        evidence=["mem-20260706-010203-rejected:query_mismatch"],
        adapter="codex",
        session_id="sess-chain",
        cwd="/repo/agent-memory-hub",
    )

    report = build_chain_log_report(tmp_path, hours=72, limit=20).to_dict()

    assert report["summary"]["total_chains"] == 1
    chain = report["chains"][0]
    assert chain["adapter"] == "codex"
    assert chain["session_id"] == "sess-chain"
    assert chain["final_outcome"] == "partial"
    assert chain["injected_count"] == 1
    assert chain["rejected_count"] == 1
    assert chain["completeness"]["expected_stage_count"] == 9
    assert chain["completeness"]["observed_stage_count"] >= 4

    detail = build_chain_log_detail(tmp_path, chain["chain_id"]).to_dict()
    stage_ids = [stage["stage_id"] for stage in detail["stages"]]
    assert stage_ids == [
        "hook_capture",
        "prompt_frame",
        "query_gate",
        "retrieval",
        "context_firewall",
        "context_loading",
        "packing",
        "injection",
        "feedback",
    ]
    assert any(stage["status"] == "partial" for stage in detail["stages"])
    assert "raw user query should not leak" not in json.dumps(detail)
    assert "another raw query should not leak" not in json.dumps(detail)
    assert "secret memory body" not in json.dumps(detail)
```

- [ ] **Step 2: Write failing tests for fixed algorithm nodes and candidate decisions**

Append:

```python
def test_chain_log_keeps_algorithm_nodes_visible_when_not_observed(tmp_path: Path) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.product.chain_log import build_chain_log_report, build_chain_log_detail

    item_id = _write_item(tmp_path)
    record_injection_cohort(
        tmp_path,
        item_ids=[item_id],
        adapter="codex",
        session_id="sess-algo",
        cwd="/repo",
        query="algorithm trace query should not leak",
        pack_metrics={"context_pack_chars": 90},
    )

    report = build_chain_log_report(tmp_path, hours=72, limit=20).to_dict()
    detail = build_chain_log_detail(tmp_path, report["chains"][0]["chain_id"]).to_dict()

    algorithm_ids = [stage["algorithm_id"] for stage in detail["algorithm_trace"]]
    assert algorithm_ids == [
        "metadata_filter",
        "bm25",
        "vector",
        "rrf",
        "cross_encoder",
        "retention",
        "decay_coefficient",
        "feedback_value",
        "runtime_status",
        "temporal_supersession",
        "mmr",
        "hopfield",
        "graph_expansion",
        "budget_trim",
    ]
    assert any(stage["status"] == "not_observed" for stage in detail["algorithm_trace"])
    assert any(stage["algorithm_id"] == "rrf" for stage in detail["algorithm_trace"])
    assert detail["candidates"][0]["item_id"] == item_id
    assert detail["candidates"][0]["firewall_action"] in {"include", "defer"}
    assert detail["candidates"][0]["title"] == "Chain log demo"


def test_chain_log_filters_by_adapter_status_and_session(tmp_path: Path) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.memory.governance.recall_events import record_gap
    from agent_brain.product.chain_log import build_chain_log_report

    item_id = _write_item(tmp_path)
    record_injection_cohort(
        tmp_path,
        item_ids=[item_id],
        adapter="codex",
        session_id="sess-injected",
        cwd="/repo",
        query="injected query",
    )
    record_gap(
        tmp_path,
        query="blocked query",
        reason="query_not_injectable",
        adapter="claude_code",
        session_id="sess-blocked",
        cwd="/repo",
    )

    codex = build_chain_log_report(tmp_path, hours=72, adapter="codex").to_dict()
    assert [chain["adapter"] for chain in codex["chains"]] == ["codex"]

    blocked = build_chain_log_report(tmp_path, hours=72, status="blocked").to_dict()
    assert blocked["chains"][0]["session_id"] == "sess-blocked"

    injected = build_chain_log_report(tmp_path, hours=72, session_id="sess-injected").to_dict()
    assert injected["chains"][0]["final_outcome"] == "injected"
```

- [ ] **Step 3: Run the tests and verify they fail**

Run:

```bash
pytest tests/unit/test_chain_log.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'agent_brain.product.chain_log'`.

- [ ] **Step 4: Commit only the failing tests**

```bash
git add tests/unit/test_chain_log.py
git commit -m "test: cover chain log read model"
```

## Task 2: Implement Chain Read Model

**Files:**
- Create: `agent_brain/product/chain_log.py`
- Test: `tests/unit/test_chain_log.py`

- [ ] **Step 1: Create dataclasses and fixed contracts**

Create `agent_brain/product/chain_log.py` with these dataclasses and constants:

```python
"""Request-chain log read model for Web diagnostics.

This module is observational. It joins existing runtime sidecars into request
chains, exposes only sanitized metadata, and never returns raw prompt, query,
memory body, or tool arguments.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from agent_brain.agent_integrations.runtime_events import AdapterRuntimeEvent, iter_runtime_events
from agent_brain.memory.context.injection_cohorts import InjectionCohort, iter_injection_cohorts
from agent_brain.memory.governance.recall_events import GapRecord, TaskOutcome, iter_gap_records, iter_task_outcomes
from agent_brain.memory.store.items_store import ItemsStore


MAX_WINDOW_HOURS = 72
MAX_LIMIT = 500

STAGE_CONTRACT: tuple[tuple[str, str], ...] = (
    ("hook_capture", "Hook 捕获"),
    ("prompt_frame", "Prompt Frame"),
    ("query_gate", "Query Gate"),
    ("retrieval", "Retrieval"),
    ("context_firewall", "Context Firewall"),
    ("context_loading", "Context Loading"),
    ("packing", "Packing"),
    ("injection", "Injection"),
    ("feedback", "Feedback / Gap"),
)

ALGORITHM_CONTRACT: tuple[tuple[str, str], ...] = (
    ("metadata_filter", "Metadata Filter"),
    ("bm25", "BM25"),
    ("vector", "Vector"),
    ("rrf", "RRF Fusion"),
    ("cross_encoder", "Cross-Encoder Rerank"),
    ("retention", "遗忘曲线"),
    ("decay_coefficient", "衰减系数"),
    ("feedback_value", "Feedback Value"),
    ("runtime_status", "Runtime / Status Boost"),
    ("temporal_supersession", "Temporal / Supersession"),
    ("mmr", "MMR"),
    ("hopfield", "Hopfield"),
    ("graph_expansion", "Graph Expansion"),
    ("budget_trim", "Budget Trim"),
)

REDACTED_KEYS = {
    "body",
    "content",
    "content_text",
    "normalized_query",
    "normalized_question",
    "prompt",
    "query",
    "question",
    "tool_arguments",
}


@dataclass(frozen=True)
class ChainStage:
    stage_id: str
    name: str
    status: str
    summary: str
    preview: dict[str, Any] = field(default_factory=dict)
    evidence: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = list(self.evidence)
        data["preview"] = _sanitize(self.preview)
        data["raw"] = _sanitize(self.raw)
        return data


@dataclass(frozen=True)
class AlgorithmStage:
    algorithm_id: str
    name: str
    status: str
    summary: str
    input_count: int | None = None
    output_count: int | None = None
    reason: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _sanitize(asdict(self))


@dataclass(frozen=True)
class CandidateTrace:
    item_id: str
    title: str | None = None
    summary: str | None = None
    type: str | None = None
    project: str | None = None
    maturity: str | None = None
    final_rank: int | None = None
    final_score: float | None = None
    firewall_action: str = "defer"
    firewall_reasons: tuple[str, ...] = ()
    loaded_view: str | None = None
    score_trace: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["firewall_reasons"] = list(self.firewall_reasons)
        data["score_trace"] = _sanitize(self.score_trace)
        return data


@dataclass(frozen=True)
class ChainSummary:
    chain_id: str
    adapter: str
    session_id: str | None
    cwd: str | None
    started_at: str
    completed_at: str | None
    final_outcome: str
    injected_count: int
    rejected_count: int
    gap_reason: str | None
    completeness: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return _sanitize(asdict(self))


@dataclass(frozen=True)
class ChainDetail:
    chain_id: str
    adapter: str
    session_id: str | None
    cwd: str | None
    started_at: str
    completed_at: str | None
    final_outcome: str
    completeness: dict[str, Any]
    stages: tuple[ChainStage, ...]
    algorithm_trace: tuple[AlgorithmStage, ...]
    candidates: tuple[CandidateTrace, ...]
    evidence: tuple[str, ...]
    boundaries: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "adapter": self.adapter,
            "session_id": self.session_id,
            "cwd": self.cwd,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "final_outcome": self.final_outcome,
            "completeness": _sanitize(self.completeness),
            "stages": [stage.to_dict() for stage in self.stages],
            "algorithm_trace": [stage.to_dict() for stage in self.algorithm_trace],
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "evidence": list(self.evidence),
            "boundaries": list(self.boundaries),
        }


@dataclass(frozen=True)
class ChainLogReport:
    filters: dict[str, Any]
    summary: dict[str, Any]
    chains: tuple[ChainSummary, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "filters": _sanitize(self.filters),
            "summary": _sanitize(self.summary),
            "chains": [chain.to_dict() for chain in self.chains],
        }
```

- [ ] **Step 2: Add aggregation helpers and public builders**

Append implementation functions:

```python
@dataclass
class _ChainBucket:
    key: str
    runtime_events: list[AdapterRuntimeEvent] = field(default_factory=list)
    injections: list[InjectionCohort] = field(default_factory=list)
    gaps: list[GapRecord] = field(default_factory=list)
    outcomes: list[TaskOutcome] = field(default_factory=list)


def build_chain_log_report(
    brain_dir: Path,
    *,
    hours: int = MAX_WINDOW_HOURS,
    limit: int = 100,
    adapter: str | None = None,
    session_id: str | None = None,
    cwd: str | None = None,
    status: str | None = None,
) -> ChainLogReport:
    chains = _chain_details(brain_dir, hours=hours)
    if adapter:
        chains = [chain for chain in chains if chain.adapter == adapter]
    if session_id:
        chains = [chain for chain in chains if chain.session_id == session_id]
    if cwd:
        chains = [chain for chain in chains if (chain.cwd or "").endswith(cwd) or cwd in (chain.cwd or "")]
    if status:
        chains = [chain for chain in chains if chain.final_outcome == status]
    chains.sort(key=lambda chain: chain.started_at, reverse=True)
    bounded = chains[: _bounded_limit(limit)]
    summaries = tuple(_summary_from_detail(chain) for chain in bounded)
    return ChainLogReport(
        filters={
            "hours": _bounded_hours(hours),
            "limit": _bounded_limit(limit),
            "adapter": adapter,
            "session_id": session_id,
            "cwd": cwd,
            "status": status,
        },
        summary={
            "total_chains": len(summaries),
            "by_outcome": dict(Counter(chain.final_outcome for chain in summaries)),
            "by_adapter": dict(Counter(chain.adapter for chain in summaries)),
        },
        chains=summaries,
    )


def build_chain_log_detail(brain_dir: Path, chain_id: str, *, hours: int = MAX_WINDOW_HOURS) -> ChainDetail:
    for chain in _chain_details(brain_dir, hours=hours):
        if chain.chain_id == chain_id:
            return chain
    raise KeyError(chain_id)
```

- [ ] **Step 3: Add chain construction, stages, algorithms, candidates, and sanitization**

Append:

```python
def _chain_details(brain_dir: Path, *, hours: int) -> list[ChainDetail]:
    brain = Path(brain_dir)
    start = datetime.now(timezone.utc) - timedelta(hours=_bounded_hours(hours))
    buckets: dict[str, _ChainBucket] = {}

    for event in iter_runtime_events(brain):
        if not _within(event.timestamp, start):
            continue
        _bucket(buckets, _bucket_key(event.session_id, event.adapter, event.cwd)).runtime_events.append(event)
    for cohort in iter_injection_cohorts(brain):
        if not _within(cohort.timestamp, start):
            continue
        _bucket(buckets, _bucket_key(cohort.session_id, cohort.adapter, cohort.cwd)).injections.append(cohort)
    for gap in iter_gap_records(brain):
        if not _within(gap.timestamp, start):
            continue
        _bucket(buckets, _bucket_key(gap.session_id, gap.adapter, gap.cwd)).gaps.append(gap)
    for outcome in iter_task_outcomes(brain):
        if not _within(outcome.timestamp, start):
            continue
        _bucket(buckets, _bucket_key(outcome.session_id, outcome.adapter, outcome.cwd)).outcomes.append(outcome)

    item_meta = _items_by_id(brain)
    return [_detail_from_bucket(bucket, item_meta) for bucket in buckets.values()]


def _detail_from_bucket(bucket: _ChainBucket, item_meta: dict[str, Any]) -> ChainDetail:
    adapter = _first_present(
        [event.adapter for event in bucket.runtime_events],
        [cohort.adapter for cohort in bucket.injections],
        [gap.adapter for gap in bucket.gaps],
        [outcome.adapter for outcome in bucket.outcomes],
    ) or "unknown"
    session_id = _first_present(
        [event.session_id for event in bucket.runtime_events],
        [cohort.session_id for cohort in bucket.injections],
        [gap.session_id for gap in bucket.gaps],
        [outcome.session_id for outcome in bucket.outcomes],
    )
    cwd = _first_present(
        [event.cwd for event in bucket.runtime_events],
        [cohort.cwd for cohort in bucket.injections],
        [gap.cwd for gap in bucket.gaps],
        [outcome.cwd for outcome in bucket.outcomes],
    )
    timestamps = [
        value
        for value in (
            [event.timestamp for event in bucket.runtime_events]
            + [cohort.timestamp for cohort in bucket.injections]
            + [gap.timestamp for gap in bucket.gaps]
            + [outcome.timestamp for outcome in bucket.outcomes]
        )
        if value
    ]
    started_at = min(timestamps) if timestamps else datetime.now(timezone.utc).isoformat()
    completed_at = max(timestamps) if len(timestamps) > 1 else None
    chain_id = _chain_id(session_id, adapter, cwd, started_at, bucket)
    final_outcome = _final_outcome(bucket)
    candidates = tuple(_candidate_traces(bucket, item_meta))
    stages = tuple(_chain_stages(bucket, final_outcome))
    algorithms = tuple(_algorithm_stages(bucket, candidates))
    completeness = _completeness(stages, algorithms, final_outcome)
    return ChainDetail(
        chain_id=chain_id,
        adapter=adapter,
        session_id=session_id,
        cwd=cwd,
        started_at=started_at,
        completed_at=completed_at,
        final_outcome=final_outcome,
        completeness=completeness,
        stages=stages,
        algorithm_trace=algorithms,
        candidates=candidates,
        evidence=tuple(_evidence(bucket)),
        boundaries=(
            "Web chain logs expose sanitized runtime metadata only; raw prompt, query, question, body, content, and tool arguments are removed.",
            "not_observed means the current sidecars do not contain evidence for that stage; it is not reported as success or failure.",
            "P0 derives algorithm visibility from existing sidecars and candidate metadata; richer before/after score deltas require Retriever instrumentation.",
        ),
    )


def _chain_stages(bucket: _ChainBucket, final_outcome: str) -> list[ChainStage]:
    runtime_count = len(bucket.runtime_events)
    injection_count = sum(len(cohort.item_ids) for cohort in bucket.injections)
    rejected_count = sum(len(gap.rejected_ids) for gap in bucket.gaps)
    gap_reason = bucket.gaps[-1].reason if bucket.gaps else None
    statuses = {
        "hook_capture": "passed" if runtime_count else "not_observed",
        "prompt_frame": "passed" if runtime_count or bucket.gaps or bucket.injections else "not_observed",
        "query_gate": "blocked" if gap_reason == "query_not_injectable" else ("passed" if bucket.injections else ("partial" if bucket.gaps else "not_observed")),
        "retrieval": "passed" if bucket.injections else ("blocked" if bucket.gaps else "not_observed"),
        "context_firewall": "partial" if rejected_count and injection_count else ("blocked" if rejected_count else ("passed" if injection_count else "not_observed")),
        "context_loading": "passed" if injection_count else "not_observed",
        "packing": "passed" if bucket.injections else "not_observed",
        "injection": "passed" if injection_count else ("blocked" if final_outcome == "blocked" else "not_observed"),
        "feedback": "passed" if bucket.outcomes else ("partial" if bucket.gaps else "not_observed"),
    }
    previews = {
        "hook_capture": {"events": runtime_count},
        "query_gate": {"gap_reason": gap_reason, "has_gap": bool(bucket.gaps)},
        "retrieval": {"injected_count": injection_count, "rejected_count": rejected_count},
        "context_firewall": {"rejected_count": rejected_count},
        "packing": {"pack_metrics": [cohort.pack_metrics or {} for cohort in bucket.injections]},
        "injection": {"cohort_count": len(bucket.injections), "item_count": injection_count},
        "feedback": {"outcomes": len(bucket.outcomes), "gaps": len(bucket.gaps)},
    }
    return [
        ChainStage(
            stage_id=stage_id,
            name=name,
            status=statuses[stage_id],
            summary=_stage_summary(stage_id, statuses[stage_id], injection_count, rejected_count, gap_reason),
            preview=previews.get(stage_id, {}),
            evidence=tuple(_stage_evidence(stage_id, bucket)),
        )
        for stage_id, name in STAGE_CONTRACT
    ]


def _algorithm_stages(bucket: _ChainBucket, candidates: tuple[CandidateTrace, ...]) -> list[AlgorithmStage]:
    injected_count = sum(len(cohort.item_ids) for cohort in bucket.injections)
    rejected_count = sum(len(gap.rejected_ids) for gap in bucket.gaps)
    candidate_count = len(candidates)
    observed = {
        "metadata_filter": bool(bucket.injections or bucket.gaps),
        "bm25": bool(bucket.injections),
        "vector": bool(bucket.injections),
        "rrf": bool(bucket.injections),
        "retention": bool(candidates),
        "decay_coefficient": bool(candidates),
        "feedback_value": bool(candidates),
        "budget_trim": any((cohort.pack_metrics or {}).get("trimmed_ids") for cohort in bucket.injections),
    }
    stages: list[AlgorithmStage] = []
    for algorithm_id, name in ALGORITHM_CONTRACT:
        status = "applied" if observed.get(algorithm_id) else "not_observed"
        if algorithm_id in {"cross_encoder", "mmr", "hopfield", "graph_expansion"} and not observed.get(algorithm_id):
            status = "not_observed"
        stages.append(
            AlgorithmStage(
                algorithm_id=algorithm_id,
                name=name,
                status=status,
                summary=_algorithm_summary(algorithm_id, status),
                input_count=candidate_count if candidate_count else None,
                output_count=injected_count if algorithm_id in {"budget_trim", "rrf"} and injected_count else None,
                reason=None if status == "applied" else "runtime sidecar has no structured evidence for this algorithm",
                metrics={
                    "injected_count": injected_count,
                    "rejected_count": rejected_count,
                },
            )
        )
    return stages


def _candidate_traces(bucket: _ChainBucket, item_meta: dict[str, Any]) -> list[CandidateTrace]:
    injected: list[str] = []
    rejected: dict[str, list[str]] = {}
    for cohort in bucket.injections:
        injected.extend(cohort.item_ids)
    for gap in bucket.gaps:
        for item_id in gap.rejected_ids:
            rejected.setdefault(item_id, []).append(gap.reason)
        for evidence in gap.evidence:
            if ":" in evidence:
                item_id, reason = evidence.split(":", 1)
                rejected.setdefault(item_id, []).append(reason)
    rows: list[CandidateTrace] = []
    seen: set[str] = set()
    for rank, item_id in enumerate(_dedupe(injected), start=1):
        seen.add(item_id)
        rows.append(_candidate(item_id, item_meta, action="include", rank=rank))
    for item_id, reasons in rejected.items():
        if item_id in seen:
            continue
        rows.append(_candidate(item_id, item_meta, action="exclude", reasons=tuple(_dedupe(reasons))))
    return rows
```

Also add utility functions used above:

```python
def _candidate(
    item_id: str,
    item_meta: dict[str, Any],
    *,
    action: str,
    rank: int | None = None,
    reasons: tuple[str, ...] = (),
) -> CandidateTrace:
    item = item_meta.get(item_id)
    return CandidateTrace(
        item_id=item_id,
        title=getattr(item, "title", None),
        summary=getattr(item, "summary", None),
        type=str(getattr(item, "type", "")) if item else None,
        project=getattr(item, "project", None),
        maturity=str(getattr(item, "maturity", "")) if item else None,
        final_rank=rank,
        firewall_action=action,
        firewall_reasons=reasons,
        loaded_view="overview" if action == "include" else None,
        score_trace={},
    )


def _summary_from_detail(detail: ChainDetail) -> ChainSummary:
    return ChainSummary(
        chain_id=detail.chain_id,
        adapter=detail.adapter,
        session_id=detail.session_id,
        cwd=detail.cwd,
        started_at=detail.started_at,
        completed_at=detail.completed_at,
        final_outcome=detail.final_outcome,
        injected_count=sum(1 for candidate in detail.candidates if candidate.firewall_action == "include"),
        rejected_count=sum(1 for candidate in detail.candidates if candidate.firewall_action == "exclude"),
        gap_reason=next((stage.preview.get("gap_reason") for stage in detail.stages if stage.stage_id == "query_gate"), None),
        completeness=detail.completeness,
    )


def _items_by_id(brain: Path) -> dict[str, Any]:
    return {item.id: item for item, _body in ItemsStore(brain / "items").iter_all()}


def _bucket(buckets: dict[str, _ChainBucket], key: str) -> _ChainBucket:
    if key not in buckets:
        buckets[key] = _ChainBucket(key=key)
    return buckets[key]


def _bucket_key(session_id: str | None, adapter: str | None, cwd: str | None) -> str:
    return "|".join([session_id or "no-session", adapter or "unknown", cwd or ""])


def _chain_id(session_id: str | None, adapter: str, cwd: str | None, started_at: str, bucket: _ChainBucket) -> str:
    query_hash = next((cohort.query_sha256 for cohort in bucket.injections if cohort.query_sha256), None)
    seed = "|".join([session_id or "", adapter, cwd or "", query_hash or started_at])
    return "chain-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _final_outcome(bucket: _ChainBucket) -> str:
    injected = sum(len(cohort.item_ids) for cohort in bucket.injections)
    rejected = sum(len(gap.rejected_ids) for gap in bucket.gaps)
    if injected and rejected:
        return "partial"
    if injected:
        return "injected"
    if bucket.gaps:
        return "blocked"
    return "not_observed"


def _completeness(stages: tuple[ChainStage, ...], algorithms: tuple[AlgorithmStage, ...], outcome: str) -> dict[str, Any]:
    observed = [stage for stage in stages if stage.status != "not_observed"]
    missing = [stage.stage_id for stage in stages if stage.status == "not_observed"]
    blocked = next((stage.stage_id for stage in stages if stage.status == "blocked"), None)
    algorithm_observed = [stage for stage in algorithms if stage.status not in {"not_observed", "not_enabled"}]
    return {
        "expected_stage_count": len(STAGE_CONTRACT),
        "observed_stage_count": len(observed),
        "missing_stage_ids": missing,
        "blocked_stage_id": blocked,
        "final_outcome": outcome,
        "algorithm_expected_count": len(ALGORITHM_CONTRACT),
        "algorithm_observed_count": len(algorithm_observed),
        "evidence_quality": "partial" if missing else "complete",
    }


def _stage_summary(stage_id: str, status: str, injected_count: int, rejected_count: int, gap_reason: str | None) -> str:
    if stage_id == "query_gate" and gap_reason:
        return f"{status}: {gap_reason}"
    if stage_id == "retrieval":
        return f"{status}: injected={injected_count}, rejected={rejected_count}"
    return status


def _algorithm_summary(algorithm_id: str, status: str) -> str:
    if status == "applied":
        return f"{algorithm_id} has observable sidecar evidence"
    return f"{algorithm_id} has no structured runtime evidence in this chain"


def _stage_evidence(stage_id: str, bucket: _ChainBucket) -> list[str]:
    if stage_id == "hook_capture":
        return [f"adapter-events:{event.event_name}" for event in bucket.runtime_events]
    if stage_id in {"packing", "injection"}:
        return [f"injection-cohorts:{cohort.cohort_id}" for cohort in bucket.injections]
    if stage_id in {"query_gate", "context_firewall", "feedback"}:
        return [f"recall-gaps:{gap.gap_id}" for gap in bucket.gaps]
    return []


def _evidence(bucket: _ChainBucket) -> list[str]:
    evidence: list[str] = []
    for stage_id, _name in STAGE_CONTRACT:
        evidence.extend(_stage_evidence(stage_id, bucket))
    return _dedupe(evidence)


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize(child)
            for key, child in value.items()
            if str(key).lower() not in REDACTED_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(child) for child in value]
    return value


def _bounded_hours(hours: int) -> int:
    return max(1, min(int(hours or MAX_WINDOW_HOURS), MAX_WINDOW_HOURS))


def _bounded_limit(limit: int) -> int:
    return max(1, min(int(limit or 100), MAX_LIMIT))


def _within(timestamp: str, start: datetime) -> bool:
    parsed = _parse_time(timestamp)
    return parsed is not None and parsed >= start


def _parse_time(timestamp: str) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _first_present(*groups: Iterable[Any]) -> Any:
    for group in groups:
        for value in group:
            if value:
                return value
    return None


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


__all__ = [
    "ALGORITHM_CONTRACT",
    "STAGE_CONTRACT",
    "build_chain_log_detail",
    "build_chain_log_report",
]
```

- [ ] **Step 4: Run read model tests**

Run:

```bash
pytest tests/unit/test_chain_log.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit read model implementation**

```bash
git add agent_brain/product/chain_log.py tests/unit/test_chain_log.py
git commit -m "feat: add chain log read model"
```

## Task 3: Add Chain Log API Routes

**Files:**
- Create: `web/api/routes/chain_logs.py`
- Modify: `web/app.py`
- Modify: `tests/unit/test_web_api.py`
- Modify: `tests/conformance/test_web_surface_lock.py`
- Modify: `tests/unit/test_cli_smoke.py`

- [ ] **Step 1: Add failing API tests**

In `tests/unit/test_web_api.py`, add a new class after `TestMemoryLineageAPI`:

```python
class TestChainLogsAPI:
    def test_chain_logs_require_auth(self, client: TestClient):
        resp = client.get("/api/chain-logs")
        assert resp.status_code == 401

    def test_chain_logs_return_list_and_detail(
        self,
        client: TestClient,
        admin_token: str,
        brain_dir: Path,
    ):
        from agent_brain.memory.context.injection_cohorts import record_injection_cohort

        record_injection_cohort(
            brain_dir,
            item_ids=["mem-20260706-010203-api-demo"],
            adapter="codex",
            session_id="sess-chain-api",
            cwd="/repo",
            query="api raw query should not leak",
            pack_metrics={"context_pack_chars": 77},
        )

        headers = {"Authorization": f"Bearer {admin_token}"}
        listing = client.get("/api/chain-logs?hours=72&limit=10", headers=headers)
        assert listing.status_code == 200
        data = listing.json()
        assert data["summary"]["total_chains"] == 1
        chain_id = data["chains"][0]["chain_id"]

        detail = client.get(f"/api/chain-logs/{chain_id}", headers=headers)
        assert detail.status_code == 200
        payload = detail.json()
        assert payload["chain_id"] == chain_id
        assert len(payload["stages"]) == 9
        assert len(payload["algorithm_trace"]) == 14
        assert "api raw query should not leak" not in json.dumps(payload)
```

- [ ] **Step 2: Run API tests and verify they fail**

Run:

```bash
pytest tests/unit/test_web_api.py::TestChainLogsAPI -q
```

Expected: FAIL with 404 for `/api/chain-logs`.

- [ ] **Step 3: Create route module**

Create `web/api/routes/chain_logs.py`:

```python
"""Request-chain log routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from agent_brain.product.chain_log import build_chain_log_detail, build_chain_log_report
from web._base import _brain_dir
from web.auth import CurrentUser, get_current_user


router = APIRouter()


@router.get("/api/chain-logs")
async def chain_logs(
    hours: int = Query(72, ge=1, le=72),
    limit: int = Query(100, ge=1, le=500),
    adapter: str | None = Query(None),
    session_id: str | None = Query(None),
    cwd: str | None = Query(None),
    status: str | None = Query(None, pattern="^(injected|blocked|partial|not_observed)$"),
    user: CurrentUser = Depends(get_current_user),
):
    """Return recent sanitized request-chain summaries."""

    return build_chain_log_report(
        _brain_dir(),
        hours=hours,
        limit=limit,
        adapter=adapter,
        session_id=session_id,
        cwd=cwd,
        status=status,
    ).to_dict()


@router.get("/api/chain-logs/{chain_id}")
async def chain_log_detail(
    chain_id: str,
    hours: int = Query(72, ge=1, le=72),
    user: CurrentUser = Depends(get_current_user),
):
    """Return one sanitized request-chain detail."""

    try:
        return build_chain_log_detail(_brain_dir(), chain_id, hours=hours).to_dict()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="chain log not found") from exc
```

- [ ] **Step 4: Register router in app**

Modify `web/app.py` imports and router list:

```python
from web.api.routes import (
    adapters, agent_history, auth, backups, chain_logs, cockpit, data_flow, events, governance, graph, health, items,
    memory_candidates, memory_lineage, product_capabilities,
)
```

Add `chain_logs,` in the `for _r in (...)` tuple immediately before `cockpit`.

- [ ] **Step 5: Update route-count tests**

In `tests/conformance/test_web_surface_lock.py`, add:

```python
"GET /api/chain-logs",
"GET /api/chain-logs/{chain_id}",
```

In `tests/unit/test_web_api.py::TestApiDocsRoutes.test_routes_endpoint_lists_current_web_surface`, update:

```python
assert data["total"] == 99
assert "/api/chain-logs" in paths
assert "/api/chain-logs/{chain_id}" in paths
```

In `tests/unit/test_cli_smoke.py`, update:

```python
assert len(API_ENDPOINTS) == 99
assert any(method == "GET" and path == "/api/chain-logs" for method, path, _desc in API_ENDPOINTS)
assert any(method == "GET" and path == "/api/chain-logs/{chain_id}" for method, path, _desc in API_ENDPOINTS)
assert "Total: 99 endpoints" in result.output
assert "/api/chain-logs" in result.output
```

- [ ] **Step 6: Run API and route tests**

Run:

```bash
pytest tests/unit/test_web_api.py::TestChainLogsAPI tests/unit/test_web_api.py::TestApiDocsRoutes tests/conformance/test_web_surface_lock.py tests/unit/test_cli_smoke.py::test_api_docs_endpoint_rows_are_split tests/unit/test_cli_smoke.py::test_api_docs_cli_uses_current_web_route_count -q
```

Expected: PASS.

- [ ] **Step 7: Commit API route**

```bash
git add web/api/routes/chain_logs.py web/app.py tests/unit/test_web_api.py tests/conformance/test_web_surface_lock.py tests/unit/test_cli_smoke.py
git commit -m "feat: expose chain log api"
```

## Task 4: Add Request-Chain Workbench UI

**Files:**
- Modify: `web/templates/dashboard.html`
- Modify: `tests/unit/test_web_api.py`
- Modify: `tests/unit/test_dashboard_integrity.py`

- [ ] **Step 1: Add failing dashboard assertions**

In `tests/unit/test_web_api.py::TestDataFlowAPI.test_dashboard_contains_data_flow_panel`, add:

```python
assert "id=\"lineageMemoryTab\"" in resp.text
assert "id=\"lineageChainTab\"" in resp.text
assert "function loadChainLogs" in resp.text
assert "/api/chain-logs?hours=${_chainHours}&limit=100" in resp.text
assert "chain-workbench" in resp.text
assert "chain-node-rail" in resp.text
assert "chain-algorithm-waterfall" in resp.text
assert "chain-detail-drawer" in resp.text
assert "chain-candidate-table" in resp.text
```

In `tests/unit/test_dashboard_integrity.py`, add:

```python
def test_dashboard_exposes_chain_log_workbench():
    html = DASH.read_text(encoding="utf-8")
    assert "chain-workbench" in html
    assert "chain-node-rail" in html
    assert "chain-algorithm-waterfall" in html
    assert "chain-detail-drawer" in html
    assert "function chainNodeCard" in html
    assert "function chainAlgorithmWaterfall" in html
    assert "function chainOpenDrawer" in html
    assert "raw prompt" not in html.lower()
```

- [ ] **Step 2: Run dashboard tests and verify they fail**

Run:

```bash
pytest tests/unit/test_web_api.py::TestDataFlowAPI::test_dashboard_contains_data_flow_panel tests/unit/test_dashboard_integrity.py::test_dashboard_exposes_chain_log_workbench -q
```

Expected: FAIL because chain UI symbols are absent.

- [ ] **Step 3: Add chain CSS near existing Memory lineage CSS**

In `web/templates/dashboard.html`, after the `.lineage-reference-body` rules, add focused chain styles:

```css
.lineage-tabs { display: inline-grid; grid-template-columns: repeat(2, minmax(110px, 1fr)); gap: 6px; padding: 4px; border: 1px solid var(--border2); border-radius: 10px; background: var(--surface); }
.lineage-tab { appearance: none !important; border: 0 !important; border-radius: 8px !important; padding: 8px 10px; background: transparent !important; color: var(--text2) !important; font-size: 0.78rem; font-weight: 850; cursor: pointer; box-shadow: none !important; }
.lineage-tab.active { background: var(--accent) !important; color: var(--text-on-accent) !important; }
.chain-workbench { display: grid; grid-template-columns: minmax(290px, 360px) minmax(0, 1fr); gap: 12px; align-items: start; }
.chain-list { display: flex; flex-direction: column; gap: 7px; max-height: 680px; overflow: auto; }
.chain-row { appearance: none !important; width: 100%; text-align: left; border: 1px solid var(--border2) !important; border-left: 3px solid transparent !important; border-radius: 10px !important; background: var(--surface) !important; color: var(--text) !important; padding: 10px; cursor: pointer; box-shadow: none !important; }
.chain-row.active, .chain-row:hover { border-left-color: var(--accent2) !important; background: var(--bg2) !important; }
.chain-node-rail { display: grid; grid-template-columns: repeat(auto-fit, minmax(118px, 1fr)); gap: 8px; margin-bottom: 12px; }
.chain-node { position: relative; min-height: 86px; border: 1px solid var(--border2); border-radius: 10px; background: var(--surface); padding: 10px; cursor: pointer; outline: none; }
.chain-node:hover, .chain-node:focus { border-color: var(--accent2); box-shadow: 0 0 0 3px var(--accent-glow); }
.chain-node.passed { border-left: 3px solid var(--green); }
.chain-node.partial { border-left: 3px solid var(--orange); }
.chain-node.blocked { border-left: 3px solid var(--red); }
.chain-node.not_observed, .chain-node.skipped { border-left: 3px solid var(--gray); opacity: 0.78; }
.chain-node-title { color: var(--text); font-weight: 850; font-size: 0.78rem; line-height: 1.3; }
.chain-node-meta { margin-top: 6px; color: var(--text3); font-size: 0.68rem; line-height: 1.45; overflow-wrap: anywhere; }
.chain-node-preview { display: none; position: absolute; z-index: 20; left: 8px; right: 8px; top: calc(100% + 6px); border: 1px solid var(--border2); border-radius: 10px; background: var(--surface3); color: var(--text2); padding: 9px; box-shadow: var(--shadow-lg); font-size: 0.72rem; line-height: 1.45; }
.chain-node:hover .chain-node-preview, .chain-node:focus .chain-node-preview { display: block; }
.chain-algorithm-waterfall { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 8px; }
.chain-algorithm-node { border: 1px solid var(--border2); border-radius: 10px; background: var(--bg2); padding: 9px; min-height: 74px; cursor: pointer; }
.chain-algorithm-node.applied { border-color: var(--blue); }
.chain-algorithm-node.not_observed, .chain-algorithm-node.not_enabled, .chain-algorithm-node.skipped { border-style: dashed; color: var(--text3); }
.chain-detail-drawer { position: fixed; top: 0; right: 0; bottom: 0; width: min(720px, 92vw); z-index: 260; background: var(--surface); border-left: 1px solid var(--border2); box-shadow: -18px 0 48px rgba(15,23,42,0.18); padding: 18px; overflow: auto; }
.chain-drawer-backdrop { position: fixed; inset: 0; z-index: 255; background: rgba(15,23,42,0.25); }
.chain-candidate-table { width: 100%; border-collapse: collapse; font-size: 0.74rem; }
.chain-candidate-table th, .chain-candidate-table td { border-top: 1px solid var(--border2); padding: 8px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
@media (max-width: 880px) {
  .chain-workbench { grid-template-columns: 1fr; }
  .chain-detail-drawer { top: auto; left: 0; width: auto; max-height: 82vh; border-left: 0; border-top: 1px solid var(--border2); }
}
```

- [ ] **Step 4: Add JS state and render functions near Memory Lineage script**

In `web/templates/dashboard.html`, before `function lineageRender()`, add:

```javascript
let _lineageView = 'memory';
let _chainHours = 72;
let _chainData = null;
let _chainSelected = '';
let _chainDetail = null;
let _chainDrawerNode = null;

function lineageSetView(view) {
  _lineageView = view || 'memory';
  if (_lineageView === 'chain') {
    loadChainLogs(document.getElementById('mainContent'));
  } else {
    lineageRender();
  }
}

function lineageTabsHtml() {
  const isZh = LANG === 'zh';
  return `<div class="lineage-tabs" role="tablist">
    <button id="lineageMemoryTab" class="lineage-tab ${_lineageView === 'memory' ? 'active' : ''}" onclick="lineageSetView('memory')">${isZh ? '记忆链路' : 'Memory lineage'}</button>
    <button id="lineageChainTab" class="lineage-tab ${_lineageView === 'chain' ? 'active' : ''}" onclick="lineageSetView('chain')">${isZh ? '请求链路' : 'Request chain'}</button>
  </div>`;
}

async function loadChainLogs(el) {
  const isZh = LANG === 'zh';
  el.innerHTML = `<div class="empty-state"><div class="empty-state-title">${isZh ? '正在加载请求链路日志' : 'Loading request chains'}</div></div>`;
  try {
    _chainData = await api(`/api/chain-logs?hours=${_chainHours}&limit=100`);
    if (!_chainSelected && (_chainData.chains || []).length) _chainSelected = _chainData.chains[0].chain_id;
    if (_chainSelected) _chainDetail = await api(`/api/chain-logs/${encodeURIComponent(_chainSelected)}?hours=${_chainHours}`);
    chainRender();
  } catch (err) {
    el.innerHTML = `<div class="empty-state"><div class="empty-state-title">${escHtml(err.message || err)}</div></div>`;
  }
}

async function chainSelect(chainId) {
  _chainSelected = chainId || '';
  _chainDetail = _chainSelected ? await api(`/api/chain-logs/${encodeURIComponent(_chainSelected)}?hours=${_chainHours}`) : null;
  chainRender();
}

function chainRender() {
  const el = document.getElementById('mainContent');
  if (!el || !_chainData) return;
  const isZh = LANG === 'zh';
  const chains = _chainData.chains || [];
  el.innerHTML = `<div class="lineage-view lineage-console">
    <section class="lineage-topbar">
      <div>
        <div class="lineage-kicker">${isZh ? '链路日志工作台' : 'Chain log workbench'}</div>
        <h1>${isZh ? '按一次请求追踪完整召回链路' : 'Trace complete recall flow per request'}</h1>
        <p>${isZh ? '主链路展示 hook 到 feedback，Retrieval 内部展开算法子链路；未观测的环节不会被隐藏。' : 'Main stages show hook to feedback, while Retrieval expands algorithm sub-stages. Missing evidence stays visible.'}</p>
      </div>
      <div class="lineage-topbar-actions">${lineageTabsHtml()}</div>
    </section>
    <section class="lineage-control-panel">
      <div class="lineage-control-group">
        <label class="lineage-control-label" for="chainHoursSelect">${isZh ? '时间范围' : 'Time range'}</label>
        <select id="chainHoursSelect" class="lineage-select" onchange="_chainHours=Number(this.value||72);_chainSelected='';loadChainLogs(document.getElementById('mainContent'))">
          <option value="1"${_chainHours === 1 ? ' selected' : ''}>${isZh ? '近 1 小时' : 'Last 1h'}</option>
          <option value="6"${_chainHours === 6 ? ' selected' : ''}>${isZh ? '近 6 小时' : 'Last 6h'}</option>
          <option value="24"${_chainHours === 24 ? ' selected' : ''}>${isZh ? '近 24 小时' : 'Last 24h'}</option>
          <option value="72"${_chainHours === 72 ? ' selected' : ''}>${isZh ? '近 3 天' : 'Last 3d'}</option>
        </select>
      </div>
      <div class="lineage-metrics">
        ${lineageMetric(isZh ? '链路' : 'Chains', lineageNumber(_chainData.summary?.total_chains || chains.length), isZh ? '当前窗口' : 'current window')}
        ${lineageMetric(isZh ? '状态' : 'Outcomes', Object.keys(_chainData.summary?.by_outcome || {}).length, isZh ? '结果类别' : 'outcome types')}
      </div>
    </section>
    <div class="chain-workbench">
      <section class="lineage-panel">
        <h2>${isZh ? '请求列表' : 'Requests'}</h2>
        <div class="chain-list">${chainListRows(chains)}</div>
      </section>
      <section class="lineage-section">
        ${_chainDetail ? chainDetailHtml(_chainDetail) : `<div class="lineage-empty">${isZh ? '暂无请求链路。' : 'No request chains.'}</div>`}
      </section>
    </div>
  </div>`;
}
```

- [ ] **Step 5: Add node, algorithm, drawer, and candidate render helpers**

Append below the functions from Step 4:

```javascript
function chainListRows(chains) {
  const isZh = LANG === 'zh';
  if (!chains.length) return `<div class="lineage-empty">${isZh ? '当前窗口没有请求链路。' : 'No chains in this window.'}</div>`;
  return chains.map(chain => {
    const active = chain.chain_id === _chainSelected ? ' active' : '';
    const comp = chain.completeness || {};
    return `<button type="button" class="chain-row${active}" onclick="chainSelect('${jsString(chain.chain_id)}')">
      <div class="lineage-memory-title">${escHtml(chain.final_outcome || '-')} · ${escHtml(chain.adapter || 'unknown')}</div>
      <div class="lineage-memory-id">${escHtml(chain.session_id || chain.chain_id)}</div>
      <div class="lineage-memory-meta">
        <span class="lineage-pill">${escHtml(cockpitTime(chain.started_at))}</span>
        <span class="lineage-pill">${lineageNumber(comp.observed_stage_count)}/${lineageNumber(comp.expected_stage_count)}</span>
        <span class="lineage-pill load">${isZh ? '注入' : 'injected'} ${lineageNumber(chain.injected_count)}</span>
        <span class="lineage-pill risk">${isZh ? '拒绝' : 'rejected'} ${lineageNumber(chain.rejected_count)}</span>
      </div>
    </button>`;
  }).join('');
}

function chainDetailHtml(detail) {
  const isZh = LANG === 'zh';
  const comp = detail.completeness || {};
  return `<div>
    <div class="lineage-detail-header">
      <div>
        <h2 class="lineage-detail-title">${escHtml(detail.final_outcome || '-')} · ${escHtml(detail.adapter || 'unknown')}</h2>
        <div class="lineage-detail-id">${escHtml(detail.chain_id)}</div>
      </div>
      <div class="lineage-memory-meta">
        <span class="lineage-pill">${isZh ? '完整度' : 'observed'} ${lineageNumber(comp.observed_stage_count)}/${lineageNumber(comp.expected_stage_count)}</span>
        <span class="lineage-pill">${isZh ? '算法' : 'algorithms'} ${lineageNumber(comp.algorithm_observed_count)}/${lineageNumber(comp.algorithm_expected_count)}</span>
      </div>
    </div>
    <div class="chain-node-rail">${(detail.stages || []).map(chainNodeCard).join('')}</div>
    <h2>${isZh ? 'Retrieval 算法子链路' : 'Retrieval algorithm waterfall'}</h2>
    <p class="lineage-section-desc">${isZh ? '未启用或未观测的算法节点会保留在链路中，避免把缺证据误读为成功。' : 'Disabled or unobserved algorithms stay visible.'}</p>
    ${chainAlgorithmWaterfall(detail.algorithm_trace || [])}
  </div>`;
}

function chainNodeCard(stage) {
  const status = String(stage.status || 'not_observed');
  const preview = Object.entries(stage.preview || {}).slice(0, 4).map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join('<br>');
  return `<div class="chain-node ${escHtml(status)}" role="button" tabindex="0" onclick="chainOpenDrawer('stage','${jsString(stage.stage_id)}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();chainOpenDrawer('stage','${jsString(stage.stage_id)}')}">
    <div class="chain-node-title">${escHtml(stage.name || stage.stage_id)}</div>
    <div class="chain-node-meta">${escHtml(status)}<br>${escHtml(stage.summary || '')}</div>
    <div class="chain-node-preview">${preview || escHtml(stage.summary || status)}</div>
  </div>`;
}

function chainAlgorithmWaterfall(stages) {
  return `<div class="chain-algorithm-waterfall">${stages.map(stage => `
    <div class="chain-algorithm-node ${escHtml(stage.status || 'not_observed')}" role="button" tabindex="0" onclick="chainOpenDrawer('algorithm','${jsString(stage.algorithm_id)}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();chainOpenDrawer('algorithm','${jsString(stage.algorithm_id)}')}">
      <div class="chain-node-title">${escHtml(stage.name || stage.algorithm_id)}</div>
      <div class="chain-node-meta">${escHtml(stage.status || '')}<br>${escHtml(stage.reason || stage.summary || '')}</div>
    </div>
  `).join('')}</div>`;
}

function chainOpenDrawer(kind, id) {
  if (!_chainDetail) return;
  const source = kind === 'algorithm'
    ? (_chainDetail.algorithm_trace || []).find(item => item.algorithm_id === id)
    : (_chainDetail.stages || []).find(item => item.stage_id === id);
  if (!source) return;
  chainCloseDrawer();
  const backdrop = document.createElement('div');
  backdrop.className = 'chain-drawer-backdrop';
  backdrop.onclick = chainCloseDrawer;
  const drawer = document.createElement('aside');
  drawer.className = 'chain-detail-drawer';
  drawer.innerHTML = chainDrawerHtml(kind, source);
  document.body.appendChild(backdrop);
  document.body.appendChild(drawer);
  _chainDrawerNode = { backdrop, drawer };
}

function chainCloseDrawer() {
  if (!_chainDrawerNode) return;
  _chainDrawerNode.backdrop.remove();
  _chainDrawerNode.drawer.remove();
  _chainDrawerNode = null;
}

function chainDrawerHtml(kind, source) {
  const isZh = LANG === 'zh';
  return `<div class="lineage-detail-header">
    <div>
      <h2 class="lineage-detail-title">${escHtml(source.name || source.stage_id || source.algorithm_id)}</h2>
      <div class="lineage-detail-id">${escHtml(source.status || '')}</div>
    </div>
    <button class="lineage-refresh" onclick="chainCloseDrawer()">${isZh ? '关闭' : 'Close'}</button>
  </div>
  <p class="lineage-section-desc">${escHtml(source.summary || source.reason || '')}</p>
  <h2>${isZh ? '候选流水' : 'Candidate trace'}</h2>
  ${chainCandidateTable(_chainDetail.candidates || [])}
  <h2>${isZh ? '证据' : 'Evidence'}</h2>
  <code class="lineage-code">${escHtml(JSON.stringify(source, null, 2))}</code>`;
}

function chainCandidateTable(candidates) {
  const isZh = LANG === 'zh';
  if (!candidates.length) return `<div class="lineage-empty">${isZh ? '没有候选记录。' : 'No candidates.'}</div>`;
  return `<table class="chain-candidate-table">
    <thead><tr><th>Memory</th><th>${isZh ? '动作' : 'Action'}</th><th>${isZh ? '原因' : 'Reasons'}</th><th>${isZh ? '视图' : 'View'}</th></tr></thead>
    <tbody>${candidates.map(row => `<tr>
      <td><strong>${escHtml(row.title || row.item_id)}</strong><br><span class="lineage-detail-id">${escHtml(row.item_id)}</span></td>
      <td>${escHtml(row.firewall_action || '-')}</td>
      <td>${escHtml((row.firewall_reasons || []).join(', ') || '-')}</td>
      <td>${escHtml(row.loaded_view || '-')}</td>
    </tr>`).join('')}</tbody>
  </table>`;
}
```

- [ ] **Step 6: Wire lineage tabs into existing lineage render**

In `lineageRender()`, replace the existing topbar actions block with:

```javascript
<div class="lineage-topbar-actions">
  ${lineageTabsHtml()}
  ${lineageMetric(isZh ? '记忆' : 'Memories', lineageNumber(memoryRows.length), isZh ? '当前筛选' : 'in view')}
  ${lineageMetric(isZh ? '事件' : 'Events', lineageNumber(modeEvents.length), isZh ? '当前行为面' : 'current mode')}
</div>
```

At the start of `lineageRender()`, after `const isZh = LANG === 'zh';`, add:

```javascript
if (_lineageView === 'chain') {
  if (_chainData) chainRender();
  else loadChainLogs(el);
  return;
}
```

- [ ] **Step 7: Run dashboard tests**

Run:

```bash
pytest tests/unit/test_web_api.py::TestDataFlowAPI::test_dashboard_contains_data_flow_panel tests/unit/test_dashboard_integrity.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit UI**

```bash
git add web/templates/dashboard.html tests/unit/test_web_api.py tests/unit/test_dashboard_integrity.py
git commit -m "feat: add chain log workbench ui"
```

## Task 5: Add Web-Safe Candidate Detail Coverage

**Files:**
- Modify: `tests/unit/test_chain_log.py`
- Modify: `agent_brain/product/chain_log.py`

- [ ] **Step 1: Add a focused test for rejected candidate reasons**

Append to `tests/unit/test_chain_log.py`:

```python
def test_chain_log_candidate_trace_preserves_reject_reason_without_raw_query(tmp_path: Path) -> None:
    from agent_brain.memory.governance.recall_events import record_gap
    from agent_brain.product.chain_log import build_chain_log_report, build_chain_log_detail

    _write_item(tmp_path, "mem-20260706-010203-rejected-known")
    record_gap(
        tmp_path,
        query="raw rejected query should not leak",
        reason="all_candidates_rejected",
        rejected_ids=["mem-20260706-010203-rejected-known"],
        evidence=["mem-20260706-010203-rejected-known:answerability_mismatch"],
        adapter="codex",
        session_id="sess-reject",
        cwd="/repo",
    )

    report = build_chain_log_report(tmp_path, hours=72).to_dict()
    detail = build_chain_log_detail(tmp_path, report["chains"][0]["chain_id"]).to_dict()

    assert detail["final_outcome"] == "blocked"
    candidate = detail["candidates"][0]
    assert candidate["firewall_action"] == "exclude"
    assert "answerability_mismatch" in candidate["firewall_reasons"]
    assert "raw rejected query should not leak" not in json.dumps(detail)
```

- [ ] **Step 2: Run the test and verify current behavior**

Run:

```bash
pytest tests/unit/test_chain_log.py::test_chain_log_candidate_trace_preserves_reject_reason_without_raw_query -q
```

Expected: PASS if Task 2 already parses evidence reasons; otherwise FAIL with missing `answerability_mismatch`.

- [ ] **Step 3: If needed, adjust `_candidate_traces` reason merge**

Ensure this block exists in `_candidate_traces`:

```python
for evidence in gap.evidence:
    if ":" in evidence:
        item_id, reason = evidence.split(":", 1)
        rejected.setdefault(item_id, []).append(reason)
```

Ensure the final candidate call dedupes reasons:

```python
rows.append(_candidate(item_id, item_meta, action="exclude", reasons=tuple(_dedupe(reasons))))
```

- [ ] **Step 4: Run all chain log tests**

Run:

```bash
pytest tests/unit/test_chain_log.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit candidate detail coverage**

```bash
git add agent_brain/product/chain_log.py tests/unit/test_chain_log.py
git commit -m "test: cover chain candidate decisions"
```

## Task 6: Add P1 Instrumentation Seam Without Changing Runtime Behavior

**Files:**
- Modify: `agent_brain/product/chain_log.py`
- Modify: `tests/unit/test_chain_log.py`

- [ ] **Step 1: Add a test for future retrieval trace ingestion shape**

Append to `tests/unit/test_chain_log.py`:

```python
def test_chain_log_accepts_retrieval_trace_like_pack_metrics(tmp_path: Path) -> None:
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort
    from agent_brain.product.chain_log import build_chain_log_report, build_chain_log_detail

    item_id = _write_item(tmp_path)
    record_injection_cohort(
        tmp_path,
        item_ids=[item_id],
        adapter="codex",
        session_id="sess-trace",
        cwd="/repo",
        query="trace query should not leak",
        pack_metrics={
            "retrieval_trace": {
                item_id: {
                    "initial_bm25_rank": 2,
                    "initial_vector_rank": 1,
                    "initial_score": 0.42,
                    "final_rank": 1,
                    "final_score": 0.61,
                    "stages": [
                        {"name": "decay", "before_rank": 1, "after_rank": 1, "before_score": 0.7, "after_score": 0.61, "effect": "rescored"},
                        {"name": "mmr", "before_rank": 1, "after_rank": 1, "before_score": 0.61, "after_score": 0.61, "effect": "kept"},
                    ],
                }
            }
        },
    )

    report = build_chain_log_report(tmp_path, hours=72).to_dict()
    detail = build_chain_log_detail(tmp_path, report["chains"][0]["chain_id"]).to_dict()
    candidate = detail["candidates"][0]
    assert candidate["score_trace"]["initial_bm25_rank"] == 2
    assert candidate["score_trace"]["final_score"] == 0.61
    assert any(stage["algorithm_id"] == "mmr" and stage["status"] in {"applied", "no_change"} for stage in detail["algorithm_trace"])
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
pytest tests/unit/test_chain_log.py::test_chain_log_accepts_retrieval_trace_like_pack_metrics -q
```

Expected: FAIL because P0 read model has not consumed `pack_metrics.retrieval_trace`.

- [ ] **Step 3: Add trace extraction helpers**

In `agent_brain/product/chain_log.py`, add:

```python
def _retrieval_trace_by_item(bucket: _ChainBucket) -> dict[str, dict[str, Any]]:
    traces: dict[str, dict[str, Any]] = {}
    for cohort in bucket.injections:
        metrics = cohort.pack_metrics or {}
        raw_trace = metrics.get("retrieval_trace")
        if not isinstance(raw_trace, dict):
            continue
        for item_id, trace in raw_trace.items():
            if isinstance(trace, dict):
                traces[str(item_id)] = _sanitize(trace)
    return traces
```

Change `_detail_from_bucket`:

```python
trace_by_item = _retrieval_trace_by_item(bucket)
candidates = tuple(_candidate_traces(bucket, item_meta, trace_by_item))
algorithms = tuple(_algorithm_stages(bucket, candidates, trace_by_item))
```

Change signatures:

```python
def _algorithm_stages(
    bucket: _ChainBucket,
    candidates: tuple[CandidateTrace, ...],
    trace_by_item: dict[str, dict[str, Any]],
) -> list[AlgorithmStage]:
```

```python
def _candidate_traces(
    bucket: _ChainBucket,
    item_meta: dict[str, Any],
    trace_by_item: dict[str, dict[str, Any]],
) -> list[CandidateTrace]:
```

Pass `score_trace=trace_by_item.get(item_id, {})` from `_candidate`.

In `_algorithm_stages`, mark an algorithm as observed if any trace stage name matches:

```python
trace_stage_names = {
    str(stage.get("name"))
    for trace in trace_by_item.values()
    for stage in trace.get("stages", [])
    if isinstance(stage, dict)
}
if "mmr" in trace_stage_names:
    observed["mmr"] = True
if "decay" in trace_stage_names:
    observed["retention"] = True
    observed["decay_coefficient"] = True
if "feedback_value" in trace_stage_names:
    observed["feedback_value"] = True
```

- [ ] **Step 4: Run read model tests**

Run:

```bash
pytest tests/unit/test_chain_log.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit instrumentation seam**

```bash
git add agent_brain/product/chain_log.py tests/unit/test_chain_log.py
git commit -m "feat: read retrieval trace metrics in chain logs"
```

## Task 7: Final Verification

**Files:**
- Verify only.

- [ ] **Step 1: Run focused backend and API tests**

Run:

```bash
pytest tests/unit/test_chain_log.py tests/unit/test_memory_lineage.py tests/unit/test_data_flow_ledger.py -q
pytest tests/unit/test_web_api.py::TestChainLogsAPI tests/unit/test_web_api.py::TestMemoryLineageAPI tests/unit/test_web_api.py::TestDataFlowAPI tests/unit/test_web_api.py::TestApiDocsRoutes -q
```

Expected: PASS.

- [ ] **Step 2: Run route and dashboard guards**

Run:

```bash
pytest tests/conformance/test_web_surface_lock.py tests/unit/test_dashboard_integrity.py tests/unit/test_cli_smoke.py::test_api_docs_endpoint_rows_are_split tests/unit/test_cli_smoke.py::test_api_docs_cli_uses_current_web_route_count -q
```

Expected: PASS.

- [ ] **Step 3: Run formatting / lint checks used by this repo**

Run:

```bash
ruff check agent_brain/product/chain_log.py web/api/routes/chain_logs.py tests/unit/test_chain_log.py
```

Expected: PASS.

- [ ] **Step 4: Start local Web server for manual smoke**

Run:

```bash
python -m agent_brain.interfaces.cli serve --port 8765
```

Expected: server starts. Open `http://127.0.0.1:8765/#lineage`, switch to `请求链路`, and verify:

- Request list renders.
- Main chain nodes render as fixed blocks.
- Retrieval algorithm waterfall shows 14 nodes.
- Hover previews appear without shifting layout.
- Clicking a node opens the right drawer.
- Candidate table appears inside the drawer.

- [ ] **Step 5: Commit any verification-only fixture adjustments**

If no files changed, skip this step. If route count docs or tests need deterministic fixture updates:

```bash
git add <exact files changed by verification>
git commit -m "test: update chain log verification fixtures"
```

## Self-Review Checklist

- Spec coverage: P0 read model, API, UI tabs, hover/click drawer, complete main stages, complete algorithm stages, candidate decisions, sanitization, and regression tests are covered by Tasks 1-7.
- Scope control: Retriever internal instrumentation is not required for P0. Task 6 only accepts a trace-shaped pack metric so future instrumentation has a read-model landing zone.
- Type consistency: The route returns `ChainLogReport.to_dict()` and `ChainDetail.to_dict()`; frontend uses `chains`, `stages`, `algorithm_trace`, `candidates`, and `completeness` exactly as defined.
- Security boundary: raw prompt/query/question/body/content/tool arguments are removed by `_sanitize`, and tests assert raw strings do not leak.
- Worktree note: execute this plan in a dedicated clean worktree or carefully stage only listed files. The current main worktree may contain unrelated user edits.
