#!/usr/bin/env python3
"""Generate or verify the committed six-layer recall-quality report."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / ".venv/bin/python"
if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), __file__, *sys.argv[1:]])
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_brain.evaluation.recall_quality import (  # noqa: E402
    RecallQualityObservation,
    build_recall_quality_report,
)
from agent_brain.evaluation.recall_quality_corpus import (  # noqa: E402
    load_recall_quality_corpus,
)
from agent_brain.memory.context.context_firewall_types import ContextCandidate  # noqa: E402
from agent_brain.memory.context.injection_gateway import build_injection_context  # noqa: E402
from agent_brain.memory.context.injection_query_context import InjectionQueryContext  # noqa: E402
from agent_brain.memory.recall.admission import build_recall_request  # noqa: E402
from agent_brain.memory.recall.retrieval import Retriever, SearchFilter  # noqa: E402


REPORT_PATH = ROOT / "docs/evaluation/stage2-recall-quality-report.json"
READINESS_PATH = ROOT / "docs/evaluation/stage2-recall-quality-readiness.zh.md"
LEGACY_FIXTURE = ROOT / "tests/fixtures/dual_route_recall_cases.json"
PRODUCTION_FIXTURE = ROOT / "tests/fixtures/recall_quality_production_replay_v1.json"
EVALUATION_NOW = "2026-07-19T02:00:00+00:00"
IMPLEMENTATION_PATHS = (
    "agent_brain/evaluation/recall_quality.py",
    "agent_brain/evaluation/recall_quality_corpus.py",
    "agent_brain/memory/context/answerability.py",
    "agent_brain/memory/context/context_firewall.py",
    "agent_brain/memory/context/injection_gateway.py",
    "agent_brain/memory/context/query_signal.py",
    "agent_brain/memory/governance/temporal_state.py",
    "agent_brain/memory/recall/retrieval.py",
    "agent_brain/memory/recall/routed_types.py",
    "scripts/check-recall-quality.py",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    report = generate_report()
    json_text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    markdown = render_markdown(report)
    if args.write:
        REPORT_PATH.write_text(json_text, encoding="utf-8")
        READINESS_PATH.write_text(markdown, encoding="utf-8")
    else:
        if not REPORT_PATH.exists() or REPORT_PATH.read_text(encoding="utf-8") != json_text:
            print("recall quality report is stale; run scripts/check-recall-quality.py --write")
            return 1
        if not READINESS_PATH.exists() or READINESS_PATH.read_text(encoding="utf-8") != markdown:
            print("recall quality readiness markdown is stale")
            return 1
    if report["status"] != "pass":
        print("recall quality gate failed: " + ", ".join(report["failed_gates"]))
        return 1
    print(
        f"recall quality pass: cases={report['case_count']} "
        f"implementation={report['implementation_sha256']}"
    )
    return 0


def generate_report() -> dict[str, object]:
    observations = [*_legacy_observations(), *_production_observations()]
    report = build_recall_quality_report(
        observations,
        corpus_sha256={
            "calibration_heldout": _file_sha256(LEGACY_FIXTURE),
            "production_replay": _file_sha256(PRODUCTION_FIXTURE),
        },
        implementation_sha256=_implementation_sha256(),
        evaluation_now=EVALUATION_NOW,
    )
    safety = _legacy_safety_fixture_summary()
    report["safety_fixture"] = safety
    if any(safety[key] for key in ("false_positive_count", "false_negative_count", "prohibited_injection_count")):
        report["status"] = "fail"
        report["failed_gates"] = sorted({*report["failed_gates"], "legacy_41_case_safety"})
    return report


def _legacy_observations() -> list[RecallQualityObservation]:
    from tests.system import test_dual_route_recall_matrix as matrix

    cases = [case for case in matrix._cases() if case.get("calibration_split")]
    with tempfile.TemporaryDirectory(prefix="amh-recall-quality-legacy-") as raw_tmp:
        tmp_path = Path(raw_tmp)
        index, items, embedder = matrix._seed_fixture_brain(tmp_path)
        retriever = Retriever(
            index,
            embedder,
            rerank=False,
            apply_decay=False,
            record_access=False,
            temporal_now=matrix.EVALUATION_NOW,
        )
        observations: list[RecallQualityObservation] = []
        try:
            for case in cases:
                request = build_recall_request(
                    case["query"],
                    adapter="codex",
                    cwd="/repo/current",
                )
                result = retriever.search_routed(
                    request,
                    top_k=10,
                    filters=SearchFilter(),
                    record_access=False,
                )
                gateway = matrix._gateway_result(
                    result.hits,
                    items=items,
                    request=request,
                    evidence=dict(result.evidence_by_id),
                )
                injected = tuple(
                    entry.decision.candidate.item.id for entry in gateway.included
                )
                expect_injection = bool(case["expect_injection"])
                expected = tuple(case["expected_item_ids"])
                expected_answerability = "supported" if expect_injection else "not_applicable"
                actual_answerability = (
                    "supported"
                    if expect_injection and set(expected) <= set(injected)
                    else "insufficient"
                    if expect_injection
                    else "not_applicable"
                )
                observations.append(RecallQualityObservation(
                    case_id=f"legacy:{case['id']}",
                    split=str(case["calibration_split"]),
                    adapter="codex",
                    project_scope="global",
                    language=_language(case["query"]),
                    category=str(case["category"]),
                    expected_item_ids=expected,
                    allowed_item_ids=tuple(case.get("allowed_related_item_ids", ())),
                    candidate_ids=tuple(hit.id for hit in result.hits),
                    injected_ids=injected,
                    prohibited_item_ids=tuple(case.get("prohibited_item_ids", ())),
                    expected_admission=bool(case["expect_admission"]),
                    admission_allowed=request.admission.allowed,
                    admission_reason=request.admission.reason,
                    expected_answerability=expected_answerability,
                    actual_answerability=actual_answerability,
                    expected_temporal="not_applicable",
                    actual_temporal="not_applicable",
                    expected_abstention=not expect_injection,
                    actual_abstention=not injected,
                    expected_injection=expect_injection,
                    excluded_reasons=tuple(
                        reason
                        for decision in gateway.excluded
                        for reason in set(decision.reasons)
                    ),
                    used_tokens=gateway.used_tokens,
                ))
        finally:
            index.close()
    return observations


def _production_observations() -> list[RecallQualityObservation]:
    from tests.system import test_recall_quality_replay as replay

    corpus = load_recall_quality_corpus(PRODUCTION_FIXTURE)
    with tempfile.TemporaryDirectory(prefix="amh-recall-quality-production-") as raw_tmp:
        tmp_path = Path(raw_tmp)
        index = replay.HubIndex(
            tmp_path / "production.db",
            embedding_dim=replay._NoModelEmbedder.dim,
        )
        items = {}
        for case in corpus.cases:
            for raw in case.memory_items:
                value, body = replay._item(raw)
                if value.id not in items:
                    items[value.id] = (value, body)
                    index.upsert(
                        value,
                        body,
                        embedding=[0.0] * replay._NoModelEmbedder.dim,
                    )
        retriever = Retriever(
            index,
            replay._NoModelEmbedder(),
            rerank=False,
            apply_decay=False,
            record_access=False,
            temporal_now=replay.EVALUATION_NOW,
        )
        observations: list[RecallQualityObservation] = []
        try:
            for case in corpus.cases:
                request = build_recall_request(
                    case.query,
                    adapter="codex",
                    project_scope=replay._scope(case),
                    cwd="/sanitized/replay",
                )
                result = retriever.search_routed(
                    request,
                    top_k=10,
                    filters=SearchFilter(),
                    record_access=False,
                )
                candidates = [
                    ContextCandidate(items[hit.id][0], items[hit.id][1], score=hit.score)
                    for hit in result.hits
                    if hit.id in items
                ]
                gateway = build_injection_context(
                    candidates,
                    query_context=InjectionQueryContext(
                        raw_query=request.raw_query,
                        admission=request.admission,
                        query_signal=request.query_signal,
                        evidence_by_id=dict(result.evidence_by_id),
                    ),
                    current_scope={"cwd": "/sanitized/replay", "adapter": "codex"},
                    max_items=10,
                    now=replay.EVALUATION_NOW,
                )
                injected = tuple(
                    entry.decision.candidate.item.id for entry in gateway.included
                )
                expected = tuple(case.expected_item_ids)
                actual_answerability = (
                    "not_applicable"
                    if not request.admission.allowed
                    else "supported"
                    if expected and set(expected) <= set(injected)
                    else "insufficient"
                )
                observations.append(RecallQualityObservation(
                    case_id=f"production:{case.id}",
                    split=case.split,
                    adapter="codex",
                    project_scope=(
                        str(case.project_scope["value"])
                        if case.project_scope is not None
                        else "global"
                    ),
                    language=case.language,
                    category=case.category,
                    expected_item_ids=expected,
                    allowed_item_ids=(),
                    candidate_ids=tuple(hit.id for hit in result.hits),
                    injected_ids=injected,
                    prohibited_item_ids=tuple(case.prohibited_item_ids),
                    expected_admission=case.expected_admission,
                    admission_allowed=request.admission.allowed,
                    admission_reason=request.admission.reason,
                    expected_answerability=case.expected_answerability,
                    actual_answerability=actual_answerability,
                    expected_temporal=case.expected_temporal,
                    actual_temporal=replay._temporal_expectation(case, items),
                    expected_abstention=case.expected_abstention,
                    actual_abstention=not injected,
                    expected_injection=case.expected_injection,
                    excluded_reasons=tuple(
                        reason
                        for decision in gateway.excluded
                        for reason in set(decision.reasons)
                    ),
                    used_tokens=gateway.used_tokens,
                    project_mismatch_count=len(result.project_shadow),
                ))
        finally:
            index.close()
    return observations


def _legacy_safety_fixture_summary() -> dict[str, object]:
    from tests.system import test_dual_route_recall_matrix as matrix

    cases = matrix._cases()
    routed_cases = [case for case in cases if not case.get("gateway_exception_test")]
    with tempfile.TemporaryDirectory(prefix="amh-recall-quality-safety-") as raw_tmp:
        index, items, embedder = matrix._seed_fixture_brain(Path(raw_tmp))
        retriever = Retriever(
            index,
            embedder,
            rerank=False,
            apply_decay=False,
            record_access=False,
            temporal_now=matrix.EVALUATION_NOW,
        )
        try:
            outcomes = [
                (case, matrix._routed_outcome(retriever, case, items))
                for case in routed_cases
            ]
        finally:
            index.close()
    corpus_ids = frozenset(items)
    hard_negative_ids = {
        item["id"]
        for case in cases
        for item in case.get("hard_negative_items", [])
    }
    false_negatives = [
        case["id"]
        for case, outcome in outcomes
        if case["expect_injection"]
        and not case.get("known_capability_gap")
        and not set(case["expected_item_ids"]) <= outcome.injected
    ]
    false_positives = [
        case["id"]
        for case, outcome in outcomes
        if matrix._unexpected_negative_injections(
            case,
            outcome,
            corpus_ids=corpus_ids,
        )
    ]
    prohibited = [
        case["id"]
        for case, outcome in outcomes
        if (set(case.get("prohibited_item_ids", ())) | hard_negative_ids)
        & outcome.injected
    ]
    gateway_exception_failures = _gateway_exception_safety_failures(cases, matrix)
    return {
        "case_count": len(cases),
        "false_positive_count": len(false_positives),
        "false_negative_count": len(false_negatives),
        "prohibited_injection_count": len(prohibited) + len(gateway_exception_failures),
        "failed_case_ids": sorted(
            set(
                false_positives
                + false_negatives
                + prohibited
                + gateway_exception_failures
            )
        ),
    }


def _gateway_exception_safety_failures(cases, matrix) -> list[str]:
    from agent_brain.interfaces.cli import routed_query
    from agent_brain.memory.recall.retrieval_types import RetrievedItem
    from agent_brain.memory.recall.routed_types import RouteEvidence, RoutedSearchResult, RouteTrace

    case = next(value for value in cases if value.get("gateway_exception_test"))
    item, body = matrix._memory_item(case["brain_item"])

    class Store:
        def iter_all(self):
            return iter([(item, body)])

    class RetrieverStub:
        def search_routed(self, request, **_kwargs):
            hit = RetrievedItem(item.id, 1.0, bm25_rank=1, vector_rank=None)
            return RoutedSearchResult(
                [hit],
                (RouteTrace("lexical_terms", "ok", 0.0, 1, "route_completed"),),
                request.admission,
                {item.id: RouteEvidence(("lexical_terms",), None, None, 1, None)},
            )

    def explode(*_args, **_kwargs):
        raise RuntimeError("gateway verifier failed")

    with (
        patch.object(routed_query, "build_injection_context", explode),
        patch.object(routed_query.logger, "warning"),
    ):
        payload = routed_query.execute_routed_query(
            raw_query=case["query"],
            store=Store(),
            retriever=RetrieverStub(),
            top_k=10,
            filters=SearchFilter(),
            requested="auto",
            project=None,
            adapter="codex",
            session_id=None,
            cwd="/repo/current",
        )
    serialized = json.dumps(payload.to_dict(), ensure_ascii=False)
    if payload.status == "error" and payload.context == "" and item.id not in serialized:
        return []
    return [str(case["id"])]


def render_markdown(report: dict[str, object]) -> str:
    layers = report["layers"]
    retrieval = layers["retrieval"]
    injection = layers["injection"]
    abstention = layers["abstention"]
    split_rows = []
    for split, split_layers in report["breakdowns"]["split"].items():
        split_rows.append(
            f"| {split} | {split_layers['retrieval']['case_count']} | "
            f"{split_layers['retrieval']['recall_at_10']:.2%} | "
            f"{split_layers['retrieval']['mrr']:.2%} | "
            f"{split_layers['injection']['fp']} | {split_layers['injection']['fn']} | "
            f"{split_layers['answerability']['mismatch_count']} | "
            f"{split_layers['temporal']['mismatch_count']} |"
        )
    return "\n".join([
        "# 阶段二召回质量就绪报告",
        "",
        f"> 状态：**{str(report['status']).upper()}**；评测时间冻结为 `{report['evaluation_now']}`。",
        "",
        "## 六层总览",
        "",
        f"- cases：{report['case_count']}",
        f"- retrieval：R@10 {retrieval['recall_at_10']:.2%}，MRR {retrieval['mrr']:.2%}",
        f"- admission：FP {layers['admission']['fp']}，FN {layers['admission']['fn']}",
        f"- answerability mismatch：{layers['answerability']['mismatch_count']}",
        f"- temporal mismatch：{layers['temporal']['mismatch_count']}",
        f"- abstention：precision {abstention['precision']:.2%}，recall {abstention['recall']:.2%}",
        f"- injection：FP {injection['fp']}，FN {injection['fn']}，prohibited {injection['prohibited_injection_count']}",
        f"- packed token cost：{injection['used_tokens']}",
        f"- 41-case safety fixture：FP {report['safety_fixture']['false_positive_count']}，FN {report['safety_fixture']['false_negative_count']}，prohibited {report['safety_fixture']['prohibited_injection_count']}",
        "",
        "## Split 结果",
        "",
        "| split | cases | R@10 | MRR | injection FP | injection FN | answerability mismatch | temporal mismatch |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        *split_rows,
        "",
        "## 事实边界",
        "",
        "- retrieval 命中不替代 Gateway 注入结论；六层分别计数。",
        "- 本 committed 报告验证 routed-core 六层结果；不把它写成真实 Hook PASS。",
        "- 真实 UserPromptSubmit Hook 结论由 required CI fresh 生成的 hook-recall-evidence artifact 决定。",
        "- explicit project hard-filter 不存在于当前 Hook payload，相关 case 不计入 Hook 分母。",
        "- production replay 为去敏运行时回放，公开报告不包含原始 prompt、session 或路径。",
        "- project shadow 只计诊断数量，不进入 hits、evidence、Gateway 或 access count。",
        "- committed JSON 必须与 corpus hash 和 implementation hash 一致，否则门禁失败。",
        "",
    ])


def _file_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _implementation_sha256() -> str:
    digest = hashlib.sha256()
    for relative in IMPLEMENTATION_PATHS:
        path = ROOT / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _language(query: str) -> str:
    has_cjk = re.search(r"[\u3400-\u9fff]", query) is not None
    has_ascii = re.search(r"[A-Za-z]", query) is not None
    if has_cjk and has_ascii:
        return "mixed"
    if has_cjk:
        return "zh"
    return "en" if has_ascii else "other"


if __name__ == "__main__":
    raise SystemExit(main())
