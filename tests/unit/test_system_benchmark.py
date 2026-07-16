from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
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


def _load_dual_route_hook_benchmark():
    path = Path(__file__).parents[2] / "scripts" / "benchmark-dual-route-hook.py"
    spec = importlib.util.spec_from_file_location("benchmark_dual_route_hook", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dual_route_hook_benchmark_statistics_and_exit_policy() -> None:
    benchmark = _load_dual_route_hook_benchmark()
    old = benchmark.summarize([0.100, 0.110, 0.120, 0.130], timeouts=0)
    new = benchmark.summarize([0.200, 0.210, 0.220, 0.230], timeouts=0)
    too_slow = benchmark.summarize([0.100, 2.001], timeouts=1)
    errored = benchmark.summarize([0.100], timeouts=0, errors=1)

    assert old.p50_ms == 115.0
    assert old.p95_ms == 128.5
    assert benchmark.exit_code(old, new) == 0
    assert benchmark.exit_code(old, too_slow) == 1
    assert benchmark.exit_code(old, errored) == 1
    assert benchmark.exit_code(old, benchmark.summarize([0.300] * 4, timeouts=0)) == 1


def test_dual_route_hook_benchmark_warmup_and_output_are_privacy_bounded(
    tmp_path: Path,
) -> None:
    benchmark = _load_dual_route_hook_benchmark()
    payload = tmp_path / "payload.json"
    payload.write_text('{"prompt":"PRIVATE BENCHMARK PROMPT"}', encoding="utf-8")
    calls: list[tuple[list[str], bytes, float]] = []
    ticks = iter(index * 0.010 for index in range(20))
    valid_stdout = json.dumps({
        "status": "injected",
        "reason": "included",
        "context": "bounded fixture context",
        "routes": [{
            "route": "semantic_raw",
            "status": "ok",
            "candidate_count": 1,
            "reason": "route_completed",
        }],
    }).encode()

    def fake_runner(command, *, input, stdout, stderr, timeout, check):
        calls.append((command, input, timeout))
        return SimpleNamespace(returncode=0, stdout=valid_stdout)

    output = io.StringIO()
    status = benchmark.main(
        [
            "--old-command", "old-hook --mode legacy",
            "--new-command", "new-hook --mode routed",
            "--payload", str(payload),
            "--repeats", "3",
            "--warmup", "1",
            "--min-samples", "1",
            "--unit-test-mode",
        ],
        runner=fake_runner,
        clock=lambda: next(ticks),
        stdout=output,
    )

    report = json.loads(output.getvalue())
    assert status == 1
    assert len(calls) == 8
    assert [call[0][0] for call in calls] == [
        "old-hook",
        "new-hook",
        "new-hook",
        "old-hook",
        "old-hook",
        "new-hook",
        "new-hook",
        "old-hook",
    ]
    assert all(call[1] == payload.read_bytes() for call in calls)
    assert report.keys() == {
        "old",
        "new",
        "p95_delta_ms",
        "limits",
        "sample_policy",
        "publishable",
        "passed",
    }
    assert report["sample_policy"] == {
        "minimum": 1,
        "interleaved": True,
        "expected_result": "injected",
        "expected_reason": "included",
        "unit_test_mode": True,
    }
    assert report["publishable"] is False
    assert report["passed"] is False
    assert "PRIVATE BENCHMARK PROMPT" not in output.getvalue()
    assert "old-hook" not in output.getvalue()
    assert "new-hook" not in output.getvalue()


def test_dual_route_hook_benchmark_nonzero_process_is_an_error(tmp_path: Path) -> None:
    benchmark = _load_dual_route_hook_benchmark()
    payload = tmp_path / "payload.json"
    payload.write_text("{}", encoding="utf-8")
    output = io.StringIO()

    status = benchmark.main(
        [
            "--old-command",
            "/usr/bin/false",
            "--new-command",
            "/usr/bin/false",
            "--payload",
            str(payload),
            "--repeats",
            "1",
            "--warmup",
            "0",
            "--min-samples",
            "1",
            "--unit-test-mode",
        ],
        stdout=output,
    )

    report = json.loads(output.getvalue())
    assert status == 1
    assert report["old"]["errors"] == 1
    assert report["new"]["errors"] == 1
    assert report["old"]["timeouts"] == 0
    assert report["new"]["timeouts"] == 0


def test_dual_route_hook_benchmark_does_not_hide_warmup_errors(tmp_path: Path) -> None:
    benchmark = _load_dual_route_hook_benchmark()
    payload = tmp_path / "payload.json"
    payload.write_text("{}", encoding="utf-8")
    returncodes = iter((1, 0, 0, 0))
    ticks = iter(index * 0.010 for index in range(8))

    def fake_runner(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=next(returncodes),
            stdout=b'{"status":"injected","reason":"included","context":"ok","routes":[]}',
        )

    output = io.StringIO()
    status = benchmark.main(
        [
            "--old-command",
            "old-hook",
            "--new-command",
            "new-hook",
            "--payload",
            str(payload),
            "--repeats",
            "1",
            "--warmup",
            "1",
            "--min-samples",
            "1",
            "--unit-test-mode",
        ],
        runner=fake_runner,
        clock=lambda: next(ticks),
        stdout=output,
    )

    report = json.loads(output.getvalue())
    assert status == 1
    assert report["old"]["errors"] == 1


@pytest.mark.parametrize(
    "hook_stdout",
    [
        b"{}",
        b"not-json",
        b'{"status":"error","reason":"internal_error","context":"","routes":[]}',
        b'{"status":"timeout","reason":"overall_timeout","context":"","routes":[]}',
        b'{"status":"empty","reason":"no_candidates","context":"","routes":[]}',
        b'{"status":"injected","reason":"included","context":"","routes":[]}',
        b'{"status":"injected","reason":"wrong_reason","context":"ok","routes":[]}',
        b'{"status":"injected","reason":"included","context":"ok","routes":[{}]}',
        b'{"status":"injected","reason":"included","context":"ok","routes":[{"route":"semantic_raw","status":"ok","candidate_count":1,"reason":"route_error"}]}',
        b'{"status":"injected","reason":"included","context":"ok","routes":[],"extra":1}',
        b"x" * (64 * 1024 + 1),
    ],
)
def test_dual_route_hook_benchmark_treats_invalid_or_wrong_results_as_functional_errors(
    tmp_path: Path,
    hook_stdout: bytes,
) -> None:
    benchmark = _load_dual_route_hook_benchmark()
    payload = tmp_path / "payload.json"
    payload.write_text("{}", encoding="utf-8")
    ticks = iter(index * 0.010 for index in range(8))

    def fake_runner(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout=hook_stdout)

    output = io.StringIO()
    status = benchmark.main(
        [
            "--old-command", "old-hook",
            "--new-command", "new-hook",
            "--payload", str(payload),
            "--repeats", "1",
            "--warmup", "0",
            "--min-samples", "1",
            "--unit-test-mode",
        ],
        runner=fake_runner,
        clock=lambda: next(ticks),
        stdout=output,
    )

    report = json.loads(output.getvalue())
    assert status == 1
    assert report["old"]["errors"] == 1
    assert report["new"]["errors"] == 1
    assert hook_stdout.decode("utf-8", errors="ignore") not in output.getvalue()


def test_dual_route_hook_benchmark_can_require_a_valid_empty_result(tmp_path: Path) -> None:
    benchmark = _load_dual_route_hook_benchmark()
    payload = tmp_path / "payload.json"
    payload.write_text("{}", encoding="utf-8")
    ticks = iter(index * 0.010 for index in range(8))
    valid_empty = b'{"status":"empty","reason":"no_candidates","context":"","routes":[]}'

    def fake_runner(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout=valid_empty)

    output = io.StringIO()
    status = benchmark.main(
        [
            "--old-command", "old-hook",
            "--new-command", "new-hook",
            "--payload", str(payload),
            "--repeats", "1",
            "--warmup", "0",
            "--min-samples", "1",
            "--unit-test-mode",
            "--expected-result", "empty",
            "--expected-reason", "no_candidates",
        ],
        runner=fake_runner,
        clock=lambda: next(ticks),
        stdout=output,
    )

    report = json.loads(output.getvalue())
    assert status == 1
    assert report["old"]["errors"] == 0
    assert report["new"]["errors"] == 0
    assert report["sample_policy"]["expected_result"] == "empty"
    assert report["sample_policy"]["expected_reason"] == "no_candidates"


def test_dual_route_hook_benchmark_bounds_real_runner_stdout_before_reading(
    tmp_path: Path,
) -> None:
    benchmark = _load_dual_route_hook_benchmark()
    started_marker = tmp_path / "descendant-started.txt"
    survived_marker = tmp_path / "descendant-survived.txt"
    descendant_code = (
        "import pathlib,time;"
        f"pathlib.Path({str(started_marker)!r}).write_text('started');"
        "time.sleep(1);"
        f"pathlib.Path({str(survived_marker)!r}).write_text('survived')"
    )
    code = (
        "import pathlib,subprocess,sys,time;"
        "sys.stdin.buffer.read();"
        f"subprocess.Popen([{sys.executable!r},'-c',{descendant_code!r}]);"
        f"p=pathlib.Path({str(started_marker)!r});"
        "deadline=time.monotonic()+1;"
        "\nwhile not p.exists() and time.monotonic()<deadline: time.sleep(.01)\n"
        "sys.stdout.buffer.write(b'X'*70000);sys.stdout.buffer.flush();"
        "time.sleep(2)"
    )
    started = time.perf_counter()
    elapsed, timed_out, functional_error = benchmark._run_once(
        [sys.executable, "-c", code],
        b"{}",
        runner=None,
        clock=time.perf_counter,
        timeout_seconds=5.0,
        expected_result="injected",
        expected_reason="included",
    )

    assert time.perf_counter() - started < 1.0
    assert elapsed < 1.0
    assert timed_out is False
    assert functional_error is True
    assert started_marker.exists()
    time.sleep(1.1)
    assert not survived_marker.exists()


def test_dual_route_hook_benchmark_deadline_includes_large_stdin_delivery(
    tmp_path: Path,
) -> None:
    benchmark = _load_dual_route_hook_benchmark()
    survived_marker = tmp_path / "large-stdin-descendant-survived.txt"
    descendant_code = (
        "import pathlib,time;"
        "time.sleep(.8);"
        f"pathlib.Path({str(survived_marker)!r}).write_text('survived')"
    )
    code = (
        "import subprocess,time;"
        f"subprocess.Popen([{sys.executable!r},'-c',{descendant_code!r}]);"
        "time.sleep(2)"
    )
    private_marker = b"PRIVATE-LARGE-STDIN-MARKER"
    payload = private_marker + b"X" * (10 * 1024 * 1024)

    started = time.perf_counter()
    captured, _returncode, timed_out, overflowed = benchmark._run_streaming(
        [sys.executable, "-c", code],
        payload,
        timeout_seconds=0.2,
        clock=time.perf_counter,
    )
    wall_elapsed = time.perf_counter() - started

    assert wall_elapsed < 0.8
    assert timed_out is True
    assert overflowed is False
    assert captured == b""
    assert private_marker not in captured
    time.sleep(0.9)
    assert not survived_marker.exists()


def test_dual_route_hook_benchmark_requires_publishable_sample_floor(tmp_path: Path) -> None:
    benchmark = _load_dual_route_hook_benchmark()
    payload = tmp_path / "payload.json"
    payload.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit, match="minimum sample"):
        benchmark.main(
            [
                "--old-command", "old-hook",
                "--new-command", "new-hook",
                "--payload", str(payload),
                "--repeats", "29",
                "--warmup", "0",
            ]
        )


def test_dual_route_hook_benchmark_rejects_lowered_publishable_floor(tmp_path: Path) -> None:
    benchmark = _load_dual_route_hook_benchmark()
    payload = tmp_path / "payload.json"
    payload.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit, match="publishable minimum"):
        benchmark.main(
            [
                "--old-command", "old-hook",
                "--new-command", "new-hook",
                "--payload", str(payload),
                "--repeats", "30",
                "--min-samples", "1",
                "--warmup", "0",
            ]
        )


def test_dual_route_hook_benchmark_custom_runner_is_test_only(tmp_path: Path) -> None:
    benchmark = _load_dual_route_hook_benchmark()
    payload = tmp_path / "payload.json"
    payload.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit, match="custom runner requires unit-test mode"):
        benchmark.main(
            [
                "--old-command", "old-hook",
                "--new-command", "new-hook",
                "--payload", str(payload),
                "--repeats", "30",
                "--warmup", "0",
            ],
            runner=lambda *_args, **_kwargs: SimpleNamespace(
                returncode=0,
                stdout=b'{"status":"injected","reason":"included","context":"ok","routes":[]}',
            ),
        )


def test_dual_route_hook_benchmark_empty_result_requires_closed_set_reason(
    tmp_path: Path,
) -> None:
    benchmark = _load_dual_route_hook_benchmark()
    payload = tmp_path / "payload.json"
    payload.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit, match="expected reason is required"):
        benchmark.main(
            [
                "--old-command", "old-hook",
                "--new-command", "new-hook",
                "--payload", str(payload),
                "--repeats", "1",
                "--min-samples", "1",
                "--warmup", "0",
                "--expected-result", "empty",
                "--unit-test-mode",
            ]
        )
