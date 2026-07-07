from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _write_fixture(path: Path) -> None:
    samples = [
        {
            "question_id": "q1",
            "question": "What degree did I graduate with?",
            "answer": "Business Administration",
            "answer_session_ids": ["s2"],
            "haystack_session_ids": ["s1", "s2"],
            "haystack_sessions": [
                [{"role": "user", "content": "We discussed transport puzzles and river crossings."}],
                [{"role": "assistant", "content": "You graduated with a Business Administration degree."}],
            ],
        },
        {
            "question_id": "q2",
            "question": "Which city did I visit for the design conference?",
            "answer": "Berlin",
            "answer_session_ids": ["s3"],
            "haystack_session_ids": ["s3", "s4"],
            "haystack_sessions": [
                [{"role": "user", "content": "The design conference trip was in Berlin."}],
                [{"role": "assistant", "content": "We talked about unrelated benchmark setup."}],
            ],
        },
    ]
    path.write_text(json.dumps(samples), encoding="utf-8")


def test_longmemeval_retrieval_smoke_computes_recall_and_mrr(tmp_path: Path) -> None:
    from agent_brain.evaluation.longmemeval_retrieval import run_longmemeval_retrieval_smoke

    dataset_file = tmp_path / "longmemeval_s_cleaned.json"
    _write_fixture(dataset_file)

    report = run_longmemeval_retrieval_smoke(
        dataset_file=dataset_file,
        max_cases=2,
        top_ks=(1, 2),
    )

    assert report["status"] == "passed"
    assert report["case_count"] == 2
    assert report["total_available_cases"] == 2
    assert report["run_scope"] == "full-rk"
    assert report["mode"] == "retrieval-only lexical R@K full"
    assert report["metrics"]["recall_at_1"] == 1.0
    assert report["metrics"]["recall_at_2"] == 1.0
    assert report["metrics"]["mrr"] == 1.0
    assert report["cases"][0]["answer_session_ids"] == ["s2"]
    assert report["cases"][0]["ranked_session_ids"][0] == "s2"


def test_longmemeval_amh_ranking_uses_real_memory_items_and_retriever(tmp_path: Path) -> None:
    from agent_brain.evaluation.longmemeval_retrieval import run_longmemeval_amh_ranking

    dataset_file = tmp_path / "longmemeval_s_cleaned.json"
    _write_fixture(dataset_file)

    report = run_longmemeval_amh_ranking(
        dataset_file=dataset_file,
        max_cases=2,
        top_ks=(1, 2),
        workspace_dir=tmp_path / "amh-ranking-workspace",
    )

    assert report["status"] == "passed"
    assert report["mode"] == "amh-ranking"
    assert report["run_scope"] == "full-rk"
    assert report["total_available_cases"] == 2
    assert report["case_count"] == 2
    assert report["metrics"]["recall_at_1"] == 1.0
    assert report["metrics"]["recall_at_2"] == 1.0
    assert report["cases"][0]["ranked_session_ids"][0] == "s2"
    assert report["cases"][0]["ranked_item_ids"][0].startswith("mem-")
    assert report["workspace_dir"].endswith("amh-ranking-workspace")


def test_longmemeval_amh_ranking_can_reuse_workspace_across_runs(tmp_path: Path) -> None:
    from agent_brain.evaluation.longmemeval_retrieval import run_longmemeval_amh_ranking

    dataset_file = tmp_path / "longmemeval_s_cleaned.json"
    workspace = tmp_path / "amh-ranking-workspace"
    _write_fixture(dataset_file)

    first = run_longmemeval_amh_ranking(
        dataset_file=dataset_file,
        max_cases=2,
        top_ks=(1, 2),
        workspace_dir=workspace,
    )
    second = run_longmemeval_amh_ranking(
        dataset_file=dataset_file,
        max_cases=2,
        top_ks=(1, 2),
        workspace_dir=workspace,
    )

    assert first["status"] == "passed"
    assert second["status"] == "passed"
    assert first["run_workspace_dir"] != second["run_workspace_dir"]
    assert second["metrics"]["recall_at_1"] == 1.0


def test_longmemeval_retrieval_marks_subset_as_smoke(tmp_path: Path) -> None:
    from agent_brain.evaluation.longmemeval_retrieval import run_longmemeval_retrieval_smoke

    dataset_file = tmp_path / "longmemeval_s_cleaned.json"
    _write_fixture(dataset_file)

    report = run_longmemeval_retrieval_smoke(
        dataset_file=dataset_file,
        max_cases=1,
        top_ks=(1, 2),
    )

    assert report["case_count"] == 1
    assert report["total_available_cases"] == 2
    assert report["run_scope"] == "smoke"
    assert report["mode"] == "retrieval-only lexical smoke"


def test_longmemeval_retrieval_cli_writes_report(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    dataset_file = tmp_path / "longmemeval_s_cleaned.json"
    output_file = tmp_path / "smoke.json"
    _write_fixture(dataset_file)

    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "benchmarks" / "run_longmemeval_retrieval_smoke.py"),
            "--dataset-file",
            str(dataset_file),
            "--max-cases",
            "2",
            "--output",
            str(output_file),
            "--format",
            "json",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert output_file.is_file()
    assert json.loads(output_file.read_text(encoding="utf-8"))["metrics"]["recall_at_5"] == 1.0


def test_longmemeval_retrieval_cli_can_run_amh_ranking(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    dataset_file = tmp_path / "longmemeval_s_cleaned.json"
    output_file = tmp_path / "amh-ranking.json"
    _write_fixture(dataset_file)

    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "benchmarks" / "run_longmemeval_retrieval_smoke.py"),
            "--mode",
            "amh-ranking",
            "--dataset-file",
            str(dataset_file),
            "--max-cases",
            "2",
            "--workspace-dir",
            str(tmp_path / "workspace"),
            "--output",
            str(output_file),
            "--format",
            "json",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert payload["mode"] == "amh-ranking"
    assert output_file.is_file()


def test_longmemeval_generation_prompt_uses_ranked_sessions() -> None:
    from agent_brain.evaluation.longmemeval_generation import build_generation_prompt

    sample = {
        "question": "What degree did I graduate with?",
        "haystack_session_ids": ["s1", "s2"],
        "haystack_sessions": [
            [{"role": "user", "content": "Unrelated session."}],
            [{"role": "assistant", "content": "You graduated with Business Administration."}],
        ],
    }

    prompt = build_generation_prompt(sample, ranked_session_ids=["s2", "s1"], top_k_context=1)

    assert "What degree did I graduate with?" in prompt
    assert "Session s2" in prompt
    assert "Business Administration" in prompt
    assert "Session s1" not in prompt


def test_longmemeval_generation_result_row_is_judge_compatible() -> None:
    from agent_brain.evaluation.longmemeval_generation import build_result_row

    sample = {
        "question_id": "q1",
        "question": "What degree did I graduate with?",
        "answer": "Business Administration",
        "question_type": "single-session-user",
    }

    row = build_result_row(
        sample,
        generated_answer="Business Administration",
        ranked_session_ids=["s2", "s1"],
    )

    assert row["output"] == "Business Administration"
    assert row["eval_metadata"]["question_id"] == "q1"
    assert row["eval_metadata"]["question_type"] == "single-session-user"
    assert row["ranked_session_ids"] == ["s2", "s1"]


def test_longmemeval_generation_supports_start_index_and_parallel_rows(tmp_path: Path) -> None:
    from agent_brain.evaluation.longmemeval_generation import run_longmemeval_generation

    class _Message:
        def __init__(self, content: str) -> None:
            self.content = content

    class _Choice:
        def __init__(self, content: str) -> None:
            self.message = _Message(content)

    class _Response:
        def __init__(self, content: str) -> None:
            self.choices = [_Choice(content)]

    class _Completions:
        def create(self, *, model: str, messages: list[dict[str, str]], temperature: int) -> _Response:
            prompt = messages[0]["content"]
            if "Which city" in prompt:
                return _Response("Berlin")
            return _Response("Business Administration")

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    dataset_file = tmp_path / "longmemeval_s_cleaned.json"
    output_file = tmp_path / "generation.json"
    _write_fixture(dataset_file)

    report = run_longmemeval_generation(
        dataset_file=dataset_file,
        output_file=output_file,
        start_index=1,
        max_cases=1,
        max_concurrency=2,
        top_k_context=1,
        model="fake-model",
        client=_Client(),
    )

    assert report["status"] == "passed"
    assert report["case_count"] == 1
    assert report["total_available_cases"] == 2
    assert report["start_index"] == 1
    assert report["max_concurrency"] == 2
    assert report["data"][0]["question_id"] == "q2"
    assert report["data"][0]["output"] == "Berlin"
    assert json.loads(output_file.read_text(encoding="utf-8"))["data"][0]["question_id"] == "q2"


def test_longmemeval_generation_metrics_include_substring_and_rouge() -> None:
    from agent_brain.evaluation.longmemeval_generation import build_generation_payload

    payload = build_generation_payload(
        dataset_file=Path("longmemeval_s_cleaned.json"),
        rows=[
            {
                "answer": "Target",
                "output": "You redeemed it at Target.",
            },
            {
                "answer": "Business Administration",
                "output": "Not available.",
            },
        ],
        total_available_cases=2,
        model="fake-model",
        base_url=None,
        top_k_context=1,
    )

    metrics = payload["averaged_metrics"]
    assert metrics["exact_match"] == 0.0
    assert metrics["substring_exact_match"] == 50.0
    assert metrics["f1"] > 0.0
    assert metrics["rougeL_f1"] > 0.0
    assert metrics["rougeL_recall"] == 50.0


def test_longmemeval_generation_metrics_follow_memorydata_normalization() -> None:
    from agent_brain.evaluation.longmemeval_generation import build_generation_payload

    payload = build_generation_payload(
        dataset_file=Path("longmemeval_s_cleaned.json"),
        rows=[
            {
                "answer": "target",
                "output": "The Target!",
            },
        ],
        total_available_cases=1,
        model="fake-model",
        base_url=None,
        top_k_context=1,
    )

    metrics = payload["averaged_metrics"]
    assert metrics["exact_match"] == 100.0
    assert metrics["substring_exact_match"] == 100.0
    assert metrics["f1"] == 100.0
    assert metrics["rougeL_recall"] == 100.0


def test_longmemeval_generation_f1_matches_memorydata_special_answers() -> None:
    from agent_brain.evaluation.longmemeval_generation import build_generation_payload

    payload = build_generation_payload(
        dataset_file=Path("longmemeval_s_cleaned.json"),
        rows=[
            {
                "answer": "no",
                "output": "No, it was Target.",
            },
        ],
        total_available_cases=1,
        model="fake-model",
        base_url=None,
        top_k_context=1,
    )

    assert payload["averaged_metrics"]["f1"] == 0.0
