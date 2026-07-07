from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from typer.testing import CliRunner

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.evaluation.system_benchmark import (
    SystemBenchmarkCase,
    build_synthetic_system_cases,
    run_system_benchmark,
)
from agent_brain.interfaces.cli import app
from agent_brain.memory.store.items_store import ItemsStore


runner = CliRunner()
NOW = datetime(2026, 6, 29, 12, 0, tzinfo=timezone.utc)


def _report_item(
    idx: int,
    *,
    type_: MemoryType = MemoryType.fact,
    title: str | None = None,
    summary: str | None = None,
    body: str | None = None,
    tags: list[str] | None = None,
) -> tuple[MemoryItem, str]:
    item_id = f"mem-20260629-120000-eval-report-{idx:03d}"
    item_title = title or f"Professional evaluation {type_.value} sentinel {idx}"
    item_summary = summary or f"professional evaluation {type_.value} locator sentinel {idx}"
    item_body = body or f"{item_title}\n{item_summary}\nbody sentinel {idx}"
    item = MemoryItem.model_validate(
        {
            "id": item_id,
            "type": type_.value,
            "created_at": NOW.isoformat(),
            "title": item_title,
            "summary": item_summary,
            "project": "agent-memory-hub",
            "tags": tags or ["professional-evaluation", type_.value],
            "confidence": 0.88,
            "abstraction": "L1",
            "support_count": 2,
            "gain_score": 0.3,
            "refs": {"urls": [f"https://example.test/professional-evaluation/{idx}"]},
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


def _real_system_report(tmp_brain_dir: Path):
    rows = [
        _report_item(
            1,
            type_=MemoryType.artifact,
            title="AMH README 深度叙事和算法解释二次打磨",
            summary="README.zh.md 维护链路 召回链路 Loop Engineering 算法公式。",
            body="深度叙事 算法解释 二次打磨 maintenance retrieval loop scoring formula",
            tags=["professional-evaluation", "readme", "loop"],
        ),
        _report_item(
            2,
            type_=MemoryType.decision,
            title="Agent runtime kit integration benchmark",
            tags=["professional-evaluation", "adapter"],
        ),
        _report_item(
            3,
            type_=MemoryType.fact,
            title="Context firewall source required benchmark",
            tags=["professional-evaluation", "firewall"],
        ),
        _report_item(
            4,
            type_=MemoryType.skill,
            title="Hierarchical context pack benchmark",
            tags=["professional-evaluation", "context-pack"],
        ),
    ]
    _seed_brain(tmp_brain_dir, rows)
    cases = build_synthetic_system_cases(rows, max_cases=24, weak_prompts=("继续", "好的", "确认", "为什么"))
    cases.append(
        SystemBenchmarkCase(
            name="real-readme-cjk-evaluation-question",
            query="关于多智能体共享第二单的深度叙事和算法解释二次打磨，都做了什么",
            expected_decision="inject",
            expected_ids=(rows[0][0].id,),
            category="real_cjk_recall",
        )
    )
    return run_system_benchmark(
        tmp_brain_dir,
        cases,
        top_k=6,
        min_block_accuracy=1.0,
        min_inject_accuracy=1.0,
        min_recall_at_k=0.7,
        min_firewall_include_rate=0.7,
        min_pack_reversible_rate=1.0,
    )


def test_professional_evaluation_report_renders_real_system_metrics(tmp_brain_dir: Path) -> None:
    from agent_brain.evaluation.professional_report import build_professional_evaluation_report

    system_report = _real_system_report(tmp_brain_dir)
    professional = build_professional_evaluation_report(
        system_report,
        generated_at=NOW,
        adapter_capabilities=[
            {
                "name": "codex",
                "display_names": ["Codex"],
                "status": "ready",
                "support_level": "install-ready",
                "verified": False,
                "runtime_observed": True,
                "integration_modes": ["file", "hook", "mcp"],
                "verification_blockers": ["context effectiveness not observed"],
            }
        ],
    )

    payload = professional.to_dict()
    assert payload["report"]["title"] == "Agent Memory Hub Evaluation Report"
    assert payload["report"]["source"] == "memory benchmark system"
    assert payload["benchmark_layers"] == [
        {
            "name": "public_report",
            "purpose": "开源展示当前本地可复核结果",
            "status_source": "system_benchmark.passed",
        },
        {
            "name": "system_benchmark",
            "purpose": "验证 query gate、retrieval、firewall、context pack 的端到端链路",
            "status_source": "memory benchmark system",
        },
        {
            "name": "release_gate",
            "purpose": "发布前质量门禁，必须单独运行 benchmarks/release_gate.py",
            "status_source": "benchmarks/release_gate.py",
        },
        {
            "name": "relevance_benchmark",
            "purpose": "开放式相关性基准，衡量非精确标题/locator 查询表现",
            "status_source": "benchmarks/benchmark_relevance.py",
        },
        {
            "name": "memorydata_external_loop",
            "purpose": "外部横评 source-lock 与可执行入口；MemoryData smoke/full 需依赖、数据集和 endpoint 就绪",
            "status_source": "docs/evaluation/latest-memory-benchmark-report.zh.md",
        },
    ]
    assert payload["system_benchmark"]["passed"] is True
    assert "cases" not in payload["system_benchmark"]
    assert payload["system_benchmark"]["case_summary"]["total"] >= 1

    charts = {chart["id"]: chart for chart in payload["charts"]}
    assert charts["completion_rate"]["title"] == "完成率指标对比"
    assert charts["token_cost"]["title"] == "Token 消耗对比"
    assert charts["governance"]["title"] == "治理与防火墙"
    assert charts["completion_rate"]["data"][0]["value"] == 100.0
    assert charts["token_cost"]["summary"]["savings_rate"] >= 0.0
    assert payload["adapter_matrix"]["summary"]["total"] == 1
    assert payload["adapter_matrix"]["summary"]["runtime_observed"] == 1
    assert "LOCOMO" not in json.dumps(payload, ensure_ascii=False)
    assert "82.08" not in json.dumps(payload, ensure_ascii=False)

    markdown = professional.to_markdown()
    assert "评测结果" in markdown
    assert "完成率指标对比" in markdown
    assert "Token 消耗对比" in markdown
    assert "治理与防火墙" in markdown
    assert "数据口径" in markdown
    assert "不是 OpenViking LOCOMO 横评" in markdown
    assert "报告 PASS 不等于 release gate PASS" in markdown
    assert "benchmarks/release_gate.py" in markdown
    assert "benchmarks/benchmark_relevance.py" in markdown
    assert "核心指标快照" in markdown
    assert "外部横评状态" in markdown
    assert "OpenDataBox/MemoryData" in markdown
    assert "docs/evaluation/latest-memory-benchmark-report.zh.md" in markdown

    html = professional.to_html()
    assert "<h1>评测结果</h1>" in html
    assert "核心指标快照" in html
    assert "完成率指标对比" in html
    assert "Token 消耗对比" in html
    assert "多 Agent 适配矩阵" in html
    assert "数据口径" in html
    assert "table-layout: fixed" in html
    assert "overflow-wrap: anywhere" in html
    assert "报告 PASS 不等于 release gate PASS" in html
    assert "外部横评状态" in html
    assert "MemoryData source-lock" in html


def test_professional_evaluation_report_writer_creates_json_markdown_and_html(tmp_brain_dir: Path, tmp_path: Path) -> None:
    from agent_brain.evaluation.professional_report import write_professional_evaluation_report

    system_report = _real_system_report(tmp_brain_dir)
    written = write_professional_evaluation_report(
        tmp_path,
        system_report,
        generated_at=NOW,
        adapter_capabilities=[],
    )

    assert written.json_path.exists()
    assert written.markdown_path.exists()
    assert written.html_path.exists()
    assert json.loads(written.json_path.read_text(encoding="utf-8"))["report"]["title"] == (
        "Agent Memory Hub Evaluation Report"
    )
    assert "评测结果" in written.markdown_path.read_text(encoding="utf-8")
    assert "<!doctype html>" in written.html_path.read_text(encoding="utf-8")


def test_benchmark_report_cli_writes_public_artifacts(tmp_brain: Path, tmp_path: Path) -> None:
    rows = [
        _report_item(1, type_=MemoryType.artifact, title="AMH evaluation report CLI artifact benchmark"),
        _report_item(2, type_=MemoryType.decision, title="AMH evaluation report CLI decision benchmark"),
        _report_item(3, type_=MemoryType.fact, title="AMH evaluation report CLI fact benchmark"),
        _report_item(4, type_=MemoryType.skill, title="AMH evaluation report CLI skill benchmark"),
    ]
    _seed_brain(tmp_brain, rows)
    output_dir = tmp_path / "evaluation"

    result = runner.invoke(
        app,
        [
            "benchmark",
            "report",
            "--max-cases",
            "24",
            "--top-k",
            "6",
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
            "--output-dir",
            str(output_dir),
            "--format",
            "json",
        ],
        env={"BRAIN_DIR": str(tmp_brain), "MEMORY_HUB_TEST_EMBEDDING": "1"},
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["paths"]["json"].endswith("amh-evaluation-report.json")
    assert payload["report"]["system_benchmark"]["passed"] is True
    assert (output_dir / "amh-evaluation-report.json").exists()
    assert (output_dir / "amh-evaluation-report.zh.md").exists()
    assert (output_dir / "amh-evaluation-report.html").exists()
