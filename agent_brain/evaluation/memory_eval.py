from __future__ import annotations

import json
import os
import re
import uuid
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp
from typing import Iterator, Mapping

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.evaluation.retrieval_gate import RetrievalCase, evaluate_rankings
from agent_brain.memory.evidence.conversation_store import ConversationStore
from agent_brain.memory.evidence.harvest.harvester import Harvester
from agent_brain.memory.recall.embedding_text import embedding_text_for_item
from agent_brain.memory.recall.retrieval import Retriever, SearchFilter
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex


@dataclass(frozen=True)
class MemoryEvalCaseResult:
    case_id: str
    case_type: str
    passed: bool
    metrics: dict[str, float]
    expected: dict[str, object]
    observed: dict[str, object]
    failures: list[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "metrics", dict(deepcopy(self.metrics)))
        object.__setattr__(self, "expected", dict(deepcopy(self.expected)))
        object.__setattr__(self, "observed", dict(deepcopy(self.observed)))
        object.__setattr__(self, "failures", list(deepcopy(self.failures)))

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "case_type": self.case_type,
            "passed": self.passed,
            "metrics": _json_ready(self.metrics),
            "expected": _json_ready(self.expected),
            "observed": _json_ready(self.observed),
            "failures": _json_ready(self.failures),
        }


@dataclass(frozen=True)
class MemoryEvalReport:
    passed: bool
    metrics: dict[str, float]
    cases: list[MemoryEvalCaseResult]
    failures: list[str]
    temp_brain_dir: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "metrics", dict(deepcopy(self.metrics)))
        object.__setattr__(self, "cases", list(deepcopy(self.cases)))
        object.__setattr__(self, "failures", list(deepcopy(self.failures)))

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "metrics": _json_ready(self.metrics),
            "cases": [case.to_dict() for case in self.cases],
            "failures": _json_ready(self.failures),
            "temp_brain_dir": self.temp_brain_dir,
        }


DEFAULT_SUITE: dict[str, object] = {
    "id": "memory-eval-p0",
    "top_k": 5,
    "cases": [
        {
            "id": "conversation-mechanical-harvest",
            "type": "conversation_replay",
            "transcript": [
                {
                    "role": "user",
                    "content": "fix the failing test_cli_version",
                },
                {
                    "role": "assistant",
                    "content": (
                        "Decision: chose mechanical-first harvesting over pure LLM "
                        "so it works offline."
                    ),
                },
            ],
            "expected": {
                "raw_messages": 2,
                "min_written_items": 1,
                "item_types": ["decision"],
                "source_kind": "harvested",
            },
        },
        {
            "id": "recall-current-decision",
            "type": "recall",
            "items": [
                {
                    "id": "mem-20260628-120000-current-decision",
                    "type": "decision",
                    "title": "Offline harvesting decision",
                    "summary": (
                        "Use mechanical-first harvesting before optional LLM enrichment"
                    ),
                    "body": (
                        "Decision: AMH uses mechanical-first harvesting so it works "
                        "offline."
                    ),
                    "refs": {"urls": ["https://example.test/design"]},
                }
            ],
            "queries": [
                {
                    "query": "offline harvesting decision",
                    "expected_ids": ["mem-20260628-120000-current-decision"],
                }
            ],
        },
        {
            "id": "superseded-memory-guard",
            "type": "dynamic_update",
            "items": [
                {
                    "id": "mem-20260628-120001-old-cli",
                    "type": "decision",
                    "title": "Use old CLI",
                    "summary": "Old memory eval command decision",
                    "body": "Use old eval command.",
                    "superseded_by": "mem-20260628-120002-new-cli",
                    "refs": {"urls": ["https://example.test/old"]},
                },
                {
                    "id": "mem-20260628-120002-new-cli",
                    "type": "decision",
                    "title": "Use memory eval run",
                    "summary": "Supported memory eval command decision",
                    "body": (
                        "Use memory eval run as the supported eval command."
                    ),
                    "refs": {"urls": ["https://example.test/new"]},
                },
            ],
            "queries": [
                {
                    "query": "supported memory eval command",
                    "expected_ids": ["mem-20260628-120002-new-cli"],
                    "forbidden_ids": ["mem-20260628-120001-old-cli"],
                }
            ],
        },
    ],
}


def default_suite() -> dict[str, object]:
    return json.loads(json.dumps(DEFAULT_SUITE))


def load_suite(path: Path) -> dict[str, object]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class MemoryEvalHarness:
    def __init__(
        self,
        *,
        brain_dir: Path | None = None,
        keep_temp: bool = False,
    ) -> None:
        self.brain_dir = Path(brain_dir) if brain_dir is not None else None
        self.keep_temp = keep_temp

    def run(
        self,
        suite: Mapping[str, object] | None = None,
        *,
        top_k: int | None = None,
    ) -> MemoryEvalReport:
        suite_payload = deepcopy(dict(suite)) if suite is not None else default_suite()
        effective_top_k = top_k if top_k is not None else int(suite_payload.get("top_k") or 5)
        if effective_top_k <= 0:
            raise ValueError("top-k must be positive")
        raw_cases = suite_payload.get("cases") or []
        if not isinstance(raw_cases, list):
            raw_cases = [raw_cases]

        with self._brain_dir_context() as brain_dir:
            case_results: list[MemoryEvalCaseResult] = []
            for index, case in enumerate(raw_cases):
                if not isinstance(case, Mapping):
                    case_results.append(_invalid_case_entry(index))
                    continue
                case_results.append(
                    self._run_case(deepcopy(dict(case)), brain_dir, top_k=effective_top_k)
                )

            failures = [
                f"{case.case_id}:{failure}"
                for case in case_results
                for failure in case.failures
            ]

            return MemoryEvalReport(
                passed=not failures,
                metrics=_aggregate_metrics(case_results),
                cases=case_results,
                failures=failures,
                temp_brain_dir=str(brain_dir) if self.keep_temp else None,
            )

    @contextmanager
    def _brain_dir_context(self) -> Iterator[Path]:
        if self.brain_dir is not None:
            self.brain_dir.mkdir(parents=True, exist_ok=True)
            yield self.brain_dir
            return

        if self.keep_temp:
            path = Path(mkdtemp(prefix="amh-memory-eval-"))
            yield path
            return

        with TemporaryDirectory(prefix="amh-memory-eval-") as tmp:
            yield Path(tmp)

    def _run_case(
        self,
        case: dict[str, object],
        brain_dir: Path,
        *,
        top_k: int,
    ) -> MemoryEvalCaseResult:
        case_id = str(case.get("id") or "unnamed")
        case_type = str(case.get("type") or "unknown")
        case_brain_dir = _case_brain_dir(brain_dir, case_id)
        try:
            if case_type == "conversation_replay":
                return self._run_conversation_replay_case(case, case_brain_dir)
            if case_type == "recall":
                return self._run_recall_case(
                    case,
                    case_brain_dir,
                    top_k=top_k,
                    case_type="recall",
                )
            if case_type == "dynamic_update":
                return self._run_recall_case(
                    case,
                    case_brain_dir,
                    top_k=top_k,
                    case_type="dynamic_update",
                )
        except Exception as exc:  # noqa: BLE001 - eval reports failures, not tracebacks.
            return MemoryEvalCaseResult(
                case_id=case_id,
                case_type=case_type,
                passed=False,
                metrics={},
                expected={},
                observed={},
                failures=[f"exception:{type(exc).__name__}:{exc}"],
            )
        return MemoryEvalCaseResult(
            case_id=case_id,
            case_type=case_type,
            passed=False,
            metrics={},
            expected={},
            observed={},
            failures=[f"unknown_case_type:{case_type}"],
        )

    def _run_conversation_replay_case(
        self,
        case: dict[str, object],
        brain_dir: Path,
    ) -> MemoryEvalCaseResult:
        case_id = str(case.get("id") or "unnamed")
        expected = dict(case.get("expected") or {})

        with _isolated_brain_env(brain_dir):
            transcripts_root = brain_dir / "eval_transcripts"
            transcript_dir = transcripts_root / "memory_eval"
            transcript_dir.mkdir(parents=True, exist_ok=True)
            transcript_path = transcript_dir / f"{_safe_segment(case_id)}.jsonl"
            _write_transcript_fixture(transcript_path, case.get("transcript") or [])

            first = Harvester(transcripts_root=transcripts_root).run(enrich=False)
            second = Harvester(transcripts_root=transcripts_root).run(enrich=False)

            messages = list(ConversationStore(brain_dir).iter_messages())
            items = list(ItemsStore(brain_dir / "items").iter_all())

        item_types = sorted({str(item.type) for item, _body in items})
        source_kinds = sorted({str(item.source.kind) for item, _body in items})
        source_extractors = sorted(
            {
                str(item.source.extractor)
                for item, _body in items
                if item.source.extractor is not None
            }
        )
        span_hashes = [
            str(item.source.span_hash)
            for item, _body in items
            if item.source.span_hash
        ]

        expected_raw = int(expected.get("raw_messages") or 0)
        min_written = int(expected.get("min_written_items") or 0)
        expected_types = {str(value) for value in expected.get("item_types") or []}
        expected_source_kind = expected.get("source_kind")
        failures: list[str] = []
        if len(messages) != expected_raw:
            failures.append(f"raw_messages:{len(messages)}!={expected_raw}")
        if first.written < min_written:
            failures.append(f"written_items:{first.written}<{min_written}")
        if second.written != 0:
            failures.append(f"second_run_written_items:{second.written}!=0")
        missing_types = sorted(expected_types - set(item_types))
        if missing_types:
            failures.append(f"missing_item_types:{','.join(missing_types)}")
        if expected_source_kind and str(expected_source_kind) not in source_kinds:
            failures.append(f"missing_source_kind:{expected_source_kind}")
        if not span_hashes:
            failures.append("missing_span_hash")

        return MemoryEvalCaseResult(
            case_id=case_id,
            case_type="conversation_replay",
            passed=not failures,
            metrics={
                "raw_messages": float(len(messages)),
                "written_items": float(first.written),
                "second_run_written_items": float(second.written),
            },
            expected=expected,
            observed={
                "conversation_ids": sorted({message.conversation_id for message in messages}),
                "raw_messages": len(messages),
                "first_run_raw_messages": first.raw_messages,
                "second_run_raw_messages": second.raw_messages,
                "written_items": first.written,
                "second_run_written_items": second.written,
                "item_ids": [item.id for item, _body in items],
                "item_types": item_types,
                "source_kinds": source_kinds,
                "source_extractors": source_extractors,
                "span_hashes": span_hashes,
            },
            failures=failures,
        )

    def _run_recall_case(
        self,
        case: dict[str, object],
        brain_dir: Path,
        *,
        top_k: int,
        case_type: str,
    ) -> MemoryEvalCaseResult:
        case_id = str(case.get("id") or "unnamed")
        items = _mapping_list(case.get("items") or [])
        queries = _mapping_list(case.get("queries") or [])
        if not queries:
            return MemoryEvalCaseResult(
                case_id=case_id,
                case_type=case_type,
                passed=False,
                metrics={"recall_at_1": 0.0, f"recall_at_{top_k}": 0.0, "mrr": 0.0},
                expected={"queries": []},
                observed={"queries": []},
                failures=["no_queries"],
            )

        with _isolated_brain_env(brain_dir):
            _store, index, retriever = _build_retrieval_world(brain_dir, items)
            try:
                query_rows, failures, metrics = _evaluate_queries(
                    queries,
                    retriever,
                    top_k=top_k,
                )
            finally:
                index.close()

        return MemoryEvalCaseResult(
            case_id=case_id,
            case_type=case_type,
            passed=not failures,
            metrics=metrics,
            expected={
                "queries": [
                    {
                        "query": str(query.get("query") or ""),
                        "expected_ids": _string_list(query.get("expected_ids") or []),
                        "forbidden_ids": _string_list(query.get("forbidden_ids") or []),
                    }
                    for query in queries
                ]
            },
            observed={"queries": query_rows},
            failures=failures,
        )


def _aggregate_metrics(cases: list[MemoryEvalCaseResult]) -> dict[str, float]:
    cases_by_type: dict[str, list[MemoryEvalCaseResult]] = {}
    for case in cases:
        cases_by_type.setdefault(case.case_type, []).append(case)

    metrics: dict[str, float] = {}
    for case_type, typed_cases in sorted(cases_by_type.items()):
        pass_count = sum(1 for case in typed_cases if case.passed)
        metrics[f"{case_type}_pass_rate"] = pass_count / len(typed_cases)

    concrete_metric_names = {
        metric_name
        for case in cases
        for metric_name in case.metrics
        if metric_name.startswith("recall_at_") or metric_name == "mrr"
    }
    for metric_name in sorted(concrete_metric_names):
        values = [
            case.metrics[metric_name]
            for case in cases
            if metric_name in case.metrics
        ]
        if values:
            metrics[metric_name] = sum(values) / len(values)

    return metrics


def _invalid_case_entry(index: int) -> MemoryEvalCaseResult:
    return MemoryEvalCaseResult(
        case_id=f"invalid-case-{index}",
        case_type="invalid",
        passed=False,
        metrics={},
        expected={},
        observed={},
        failures=["invalid_case_entry"],
    )


def _evaluate_queries(
    queries: list[dict[str, object]],
    retriever: Retriever,
    *,
    top_k: int,
) -> tuple[list[dict[str, object]], list[str], dict[str, float]]:
    def search(query: str, query_top_k: int) -> list[str]:
        return [
            hit.id
            for hit in retriever.search(
                query,
                top_k=query_top_k,
                filters=SearchFilter(),
            )
        ]

    gate_cases = [
        RetrievalCase(
            query=str(query.get("query") or ""),
            expected_ids=_string_list(query.get("expected_ids") or []),
        )
        for query in queries
    ]
    gate_report = evaluate_rankings(
        gate_cases,
        search,
        top_k=top_k,
        min_recall_at_1=0.0,
        min_mrr=0.0,
    )

    query_rows: list[dict[str, object]] = []
    failures: list[str] = []
    for row, query in zip(gate_report.cases, queries):
        ranking = _string_list(row["ranking"])
        expected_ids = _string_list(query.get("expected_ids") or [])
        forbidden_ids = _string_list(query.get("forbidden_ids") or [])
        if expected_ids and not set(expected_ids).intersection(ranking[:top_k]):
            failures.extend(
                f"missing_expected:{item_id}"
                for item_id in expected_ids
                if item_id not in ranking[:top_k]
            )
        failures.extend(
            f"forbidden_hit:{item_id}"
            for item_id in forbidden_ids
            if item_id in ranking
        )
        query_rows.append(
            {
                "query": row["query"],
                "expected_ids": expected_ids,
                "forbidden_ids": forbidden_ids,
                "ranking": ranking,
                "rank": row["rank"],
            }
        )
    return query_rows, failures, dict(gate_report.metrics)


def _build_retrieval_world(
    brain_dir: Path,
    raw_items: list[dict[str, object]],
) -> tuple[ItemsStore, HubIndex, Retriever]:
    brain_dir.mkdir(parents=True, exist_ok=True)
    store = ItemsStore(brain_dir / "items")
    embedder = HashingEmbedder()
    index = HubIndex(brain_dir / "index.db", embedding_dim=embedder.dim)
    for raw_item in raw_items:
        item, body = _memory_item_from_case(raw_item)
        store.write(item, body)
        index.upsert(item, body, embedding=embedder.embed(embedding_text_for_item(item)))
    retriever = Retriever(
        index=index,
        embedder=embedder,
        bm25_weight=1.0,
        vector_weight=0.0,
        apply_decay=False,
        record_access=False,
        rerank=False,
    )
    return store, index, retriever


def _memory_item_from_case(raw: dict[str, object]) -> tuple[MemoryItem, str]:
    data = dict(raw)
    body = str(data.pop("body", ""))
    data.setdefault("created_at", datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc).isoformat())
    data.setdefault("title", data.get("id", "memory eval item"))
    data.setdefault("summary", data.get("title", "memory eval item"))
    data.setdefault("refs", {})
    data.setdefault("agent", "benchmark")
    data.setdefault("sensitivity", "internal")
    item = MemoryItem.model_validate(data)
    return item, body or item.summary


@contextmanager
def _isolated_brain_env(brain_dir: Path) -> Iterator[None]:
    old_brain = os.environ.get("BRAIN_DIR")
    old_test_embedding = os.environ.get("MEMORY_HUB_TEST_EMBEDDING")
    os.environ["BRAIN_DIR"] = str(brain_dir)
    os.environ["MEMORY_HUB_TEST_EMBEDDING"] = "1"
    try:
        yield
    finally:
        if old_brain is None:
            os.environ.pop("BRAIN_DIR", None)
        else:
            os.environ["BRAIN_DIR"] = old_brain
        if old_test_embedding is None:
            os.environ.pop("MEMORY_HUB_TEST_EMBEDDING", None)
        else:
            os.environ["MEMORY_HUB_TEST_EMBEDDING"] = old_test_embedding


def _write_transcript_fixture(path: Path, transcript: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = _mapping_list(transcript)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _case_brain_dir(brain_dir: Path, case_id: str) -> Path:
    return brain_dir / "cases" / f"{_safe_segment(case_id)}-{uuid.uuid4().hex[:8]}"


def _safe_segment(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return safe or "case"


def _mapping_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _string_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return []


def _json_ready(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_ready(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(inner) for inner in value]
    return value


__all__ = [
    "DEFAULT_SUITE",
    "MemoryEvalCaseResult",
    "MemoryEvalHarness",
    "MemoryEvalReport",
    "default_suite",
    "load_suite",
]
