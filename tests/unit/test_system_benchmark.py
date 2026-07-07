from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from typer.testing import CliRunner

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.interfaces.cli import app
from agent_brain.memory.store.items_store import ItemsStore


runner = CliRunner()
NOW = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)


def _bench_item(
    idx: int,
    *,
    type_: MemoryType = MemoryType.fact,
    title: str | None = None,
    summary: str | None = None,
    body: str | None = None,
    tags: list[str] | None = None,
) -> tuple[MemoryItem, str]:
    item_id = f"mem-20260628-120000-system-bench-{idx:03d}"
    item_title = title or f"System benchmark {type_.value} sentinel {idx}"
    item_summary = summary or f"system benchmark {type_.value} locator sentinel {idx}"
    item_body = body or f"{item_title}\n{item_summary}\nbody sentinel {idx}"
    item = MemoryItem.model_validate(
        {
            "id": item_id,
            "type": type_.value,
            "created_at": NOW.isoformat(),
            "title": item_title,
            "summary": item_summary,
            "project": "agent-memory-hub",
            "tags": tags or ["system-benchmark", type_.value],
            "confidence": 0.86,
            "abstraction": "L1",
            "support_count": 2,
            "gain_score": 0.2,
            "refs": {"urls": [f"https://example.test/system-benchmark/{idx}"]},
            "context_views": {
                "locator": item_summary,
                "overview": f"overview {item_summary}",
                "detail_uri": f"memory://items/{item_id}/body",
            },
        }
    )
    return item, item_body


def _seed_brain(brain_dir: Path, rows: list[tuple[MemoryItem, str]]) -> None:
    store = ItemsStore(brain_dir / "items")
    for item, body in rows:
        store.write(item, body)


def test_system_benchmark_runs_query_retrieval_firewall_and_pack_matrix(tmp_brain_dir: Path) -> None:
    from agent_brain.evaluation.system_benchmark import (
        SystemBenchmarkCase,
        build_synthetic_system_cases,
        run_system_benchmark,
    )

    rows = [
        _bench_item(
            1,
            type_=MemoryType.artifact,
            title="AMH README 深度叙事和算法解释二次打磨",
            summary="README.zh.md 调整阅读路线、维护链路、召回链路、Loop Engineering 和算法公式。",
            body="深度叙事 算法解释 二次打磨 problem fix evidence verification remaining boundary",
            tags=["system-benchmark", "readme", "agent-memory-hub"],
        ),
        _bench_item(
            2,
            type_=MemoryType.fact,
            title="ClaudeCode adapter runtime evidence",
            summary="ClaudeCode hooks MCP doctor verify runtime evidence.",
            tags=["system-benchmark", "claudecode", "adapter"],
        ),
        *[
            _bench_item(idx + 10, type_=memory_type)
            for idx, memory_type in enumerate(MemoryType)
        ],
    ]
    unsafe_fact, unsafe_fact_body = _bench_item(
        40,
        type_=MemoryType.fact,
        title="Unsafe fact without source refs benchmark",
        summary="unsafe fact without source refs should be retrieved but not injected",
        tags=["system-benchmark", "unsafe"],
    )
    unsafe_fact = MemoryItem.model_validate({
        **unsafe_fact.model_dump(mode="json"),
        "refs": {},
    })
    rows.append((unsafe_fact, unsafe_fact_body))
    _seed_brain(tmp_brain_dir, rows)

    cases = build_synthetic_system_cases(
        [(item, body) for item, body in rows],
        max_cases=40,
        weak_prompts=("继续", "好的", "确认", "为什么", "再说说", "可以可以"),
    )
    cases.append(
        SystemBenchmarkCase(
            name="real-readme-cjk-question",
            query="关于多智能体共享第二单的深度叙事和算法解释二次打磨，都做了什么",
            expected_decision="inject",
            expected_ids=(rows[0][0].id,),
            category="real_cjk_recall",
        )
    )
    cases.append(
        SystemBenchmarkCase(
            name="unsafe-fact-firewall-exclusion",
            query="Unsafe fact without source refs benchmark",
            expected_decision="inject",
            expected_ids=(unsafe_fact.id,),
            category="firewall_expected_exclude",
            expect_firewall_include=False,
        )
    )

    report = run_system_benchmark(
        tmp_brain_dir,
        cases,
        top_k=6,
        min_block_accuracy=1.0,
        min_inject_accuracy=1.0,
        min_recall_at_k=0.9,
        min_firewall_include_rate=0.9,
        min_pack_reversible_rate=1.0,
    )

    assert report.passed is True
    payload = report.to_dict()
    assert payload["metrics"]["case_count"] >= 20
    assert payload["metrics"]["query_gate"]["block_accuracy"] == 1.0
    assert payload["metrics"]["query_gate"]["inject_accuracy"] == 1.0
    assert payload["metrics"]["retrieval"]["recall_at_k"] >= 0.9
    assert payload["metrics"]["context"]["firewall_include_rate"] >= 0.9
    assert payload["metrics"]["context"]["firewall_exclude_rate"] == 1.0
    assert payload["metrics"]["context"]["pack_reversible_rate"] == 1.0
    assert payload["failures"] == []

    readme_case = next(case for case in payload["cases"] if case["name"] == "real-readme-cjk-question")
    assert readme_case["stages"]["query_signal"]["decision"] == "inject"
    assert rows[0][0].id in readme_case["stages"]["retrieval"]["ranking"]
    assert readme_case["stages"]["firewall"]["expected_included"] is True
    assert readme_case["stages"]["context_pack"]["expected_reversible"] is True
    assert "bm25" in readme_case["stages"]["retrieval"]["signals"] or "vector" in readme_case["stages"]["retrieval"]["signals"]

    unsafe_case = next(case for case in payload["cases"] if case["name"] == "unsafe-fact-firewall-exclusion")
    assert unsafe_case["passed"] is True
    assert unsafe_case["stages"]["retrieval"]["expected_found"] is True
    assert unsafe_case["stages"]["firewall"]["expected_outcome_ok"] is True
    assert unsafe_case["stages"]["context_pack"]["skipped_expected_exclusion"] is True


def test_system_benchmark_cli_outputs_large_fewshot_report(tmp_brain: Path) -> None:
    rows = [
        _bench_item(1, type_=MemoryType.artifact, title="AMH README Loop Engineering benchmark", tags=["system-benchmark", "loop"]),
        _bench_item(2, type_=MemoryType.decision, title="Agent runtime kit integration benchmark", tags=["system-benchmark", "adapter"]),
        _bench_item(3, type_=MemoryType.fact, title="QoderWork GUI memory candidates benchmark", tags=["system-benchmark", "qoder_work"]),
        _bench_item(4, type_=MemoryType.signal, title="ClaudeCode context probe benchmark", tags=["system-benchmark", "claudecode"]),
        _bench_item(5, type_=MemoryType.policy, title="Context firewall governance benchmark", tags=["system-benchmark", "firewall"]),
        _bench_item(6, type_=MemoryType.skill, title="Hierarchical context pack benchmark", tags=["system-benchmark", "context_pack"]),
    ]
    _seed_brain(tmp_brain, rows)

    result = runner.invoke(
        app,
        [
            "benchmark",
            "system",
            "--max-cases",
            "24",
            "--format",
            "json",
            "--min-block-accuracy",
            "1.0",
            "--min-inject-accuracy",
            "1.0",
            "--min-recall-at-k",
            "0.7",
            "--min-firewall-include-rate",
            "0.7",
            "--min-pack-reversible-rate",
            "1.0",
        ],
        env={"BRAIN_DIR": str(tmp_brain), "MEMORY_HUB_TEST_EMBEDDING": "1"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["metrics"]["case_count"] >= 18
    assert payload["metrics"]["items_indexed"] == len(rows)
    assert payload["metrics"]["query_gate"]["weak_block_cases"] >= 6
    assert payload["metrics"]["retrieval"]["retrieval_cases"] > 0
    assert payload["metrics"]["context"]["packed_cases"] > 0
