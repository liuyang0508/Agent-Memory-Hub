from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_memory_eval_dataset_plan_exposes_longmemeval_sources_and_commands(tmp_path: Path) -> None:
    from agent_brain.evaluation.memory_eval_datasets import build_memory_eval_dataset_plan

    memorydata_repo = tmp_path / "MemoryData"
    cache_root = tmp_path / "external"
    plan = build_memory_eval_dataset_plan(
        memorydata_repo=memorydata_repo,
        cache_root=cache_root,
    )

    artifacts = {artifact["id"]: artifact for artifact in plan["artifacts"]}
    longmemeval = artifacts["longmemeval_s_cleaned"]
    assert longmemeval["ready"] is False
    assert longmemeval["target_path"].endswith("LongMemEval/data/longmemeval_s_cleaned.json")
    assert longmemeval["source_url"] == (
        "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/"
        "resolve/main/longmemeval_s_cleaned.json"
    )
    assert longmemeval["materialize_command"] == [
        "python",
        "benchmarks/materialize_memory_eval_datasets.py",
        "--dataset",
        "longmemeval-s",
        "--cache-root",
        str(cache_root),
        "--memorydata-repo",
        str(memorydata_repo),
    ]

    memoryagentbench = artifacts["memoryagentbench_hf"]
    assert memoryagentbench["source_url"] == "https://huggingface.co/datasets/ai-hyz/MemoryAgentBench"
    assert memoryagentbench["target_path"].endswith("MemoryData/datasets/MemoryAgentBench/eval_dataset_collection")
    locomo_4cat = artifacts["locomo_4cat_dist"]
    assert locomo_4cat["alias"] == "locomo-4cat"
    assert locomo_4cat["source_type"] == "derived_json_from_official_raw"
    assert locomo_4cat["target_path"].endswith(
        "MemoryData/datasets/LoCoMo/rq1_4cat_600_dist/locomo_4cat_600_dist.json"
    )
    longbench = artifacts["longbench_rep150_proportional"]
    assert longbench["alias"] == "longbench-rep150"
    assert longbench["source_type"] == "huggingface_derived_save_to_disk"
    assert longbench["target_path"].endswith("MemoryData/datasets/longBench_rep150_proportional/datasets")
    longbench_full = artifacts["longbench_v2_503_full"]
    assert longbench_full["alias"] == "longbench-v2-full"
    assert longbench_full["source_type"] == "huggingface_save_to_disk"
    assert longbench_full["target_path"].endswith("MemoryData/datasets/longBench_v2_503_full/datasets")
    membench = artifacts["membench_firstagent"]
    assert membench["alias"] == "membench"
    assert membench["source_type"] == "github_raw_bundle"
    assert membench["target_path"].endswith("MemoryData/datasets/MemBench/MemData/FirstAgent")
    assert plan["memorydata_manifest"]["ready_summary"]["memorydata_total_count"] == 6
    assert {family["id"] for family in plan["memorydata_manifest"]["memorydata_families"]} == {
        "memoryagentbench",
        "locomo",
        "locomo_category5_adversarial",
        "longbench",
        "longbench_v2_503_full",
        "membench",
    }
    assert plan["ready_count"] == 0

    Path(longmemeval["target_path"]).parent.mkdir(parents=True)
    Path(longmemeval["target_path"]).write_text("[]\n", encoding="utf-8")
    ready_plan = build_memory_eval_dataset_plan(
        memorydata_repo=memorydata_repo,
        cache_root=cache_root,
    )
    ready_artifacts = {artifact["id"]: artifact for artifact in ready_plan["artifacts"]}
    assert ready_artifacts["longmemeval_s_cleaned"]["ready"] is True
    assert ready_plan["ready_count"] == 1


def test_materializer_cli_dry_run_reports_downloadable_datasets(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "benchmarks" / "materialize_memory_eval_datasets.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--dataset",
            "longmemeval-s",
            "--cache-root",
            str(tmp_path / "external"),
            "--memorydata-repo",
            str(tmp_path / "MemoryData"),
            "--dry-run",
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
    assert payload["mode"] == "dry-run"
    assert payload["selected"] == ["longmemeval_s_cleaned"]
    assert payload["artifacts"][0]["ready"] is False
    assert payload["artifacts"][0]["source_url"].endswith("/longmemeval_s_cleaned.json")
    assert payload["memorydata_manifest"]["ready_summary"]["memorydata_total_count"] == 6
    assert not (tmp_path / "external" / "LongMemEval" / "data" / "longmemeval_s_cleaned.json").exists()


def test_memorydata_manifest_lists_full_families_and_materialization_modes(tmp_path: Path) -> None:
    from agent_brain.evaluation.memorydata_manifest import build_memorydata_manifest

    manifest = build_memorydata_manifest(
        memorydata_repo=tmp_path / "MemoryData",
        cache_root=tmp_path / "external",
    )

    families = {family["id"]: family for family in manifest["memorydata_families"]}
    assert set(families) == {
        "memoryagentbench",
        "locomo",
        "locomo_category5_adversarial",
        "longbench",
        "longbench_v2_503_full",
        "membench",
    }
    assert families["memoryagentbench"]["materialization_mode"] == "hf_save_to_disk"
    assert families["locomo"]["materialization_mode"] == "derived_json_from_official_raw"
    assert families["locomo_category5_adversarial"]["materialization_mode"] == "official_raw_category_filter"
    assert families["longbench"]["materialization_mode"] == "huggingface_derived_save_to_disk"
    assert families["longbench_v2_503_full"]["materialization_mode"] == "huggingface_save_to_disk"
    assert families["membench"]["materialization_mode"] == "github_raw_bundle"
    assert manifest["longmemeval"]["id"] == "longmemeval_s_cleaned"
    assert manifest["ready_summary"]["memorydata_ready_count"] == 0


def test_memorydata_manifest_marks_locomo_and_longbench_ready_from_strict_paths(tmp_path: Path) -> None:
    from agent_brain.evaluation.memorydata_manifest import build_memorydata_manifest

    memorydata_repo = tmp_path / "MemoryData"
    locomo = memorydata_repo / "datasets" / "LoCoMo" / "rq1_4cat_600_dist" / "locomo_4cat_600_dist.json"
    locomo.parent.mkdir(parents=True)
    locomo.write_text("[]", encoding="utf-8")
    longbench = memorydata_repo / "datasets" / "longBench_rep150_proportional" / "datasets"
    longbench.mkdir(parents=True)
    (longbench / "dataset_info.json").write_text("{}", encoding="utf-8")

    manifest = build_memorydata_manifest(
        memorydata_repo=memorydata_repo,
        cache_root=tmp_path / "external",
    )

    families = {family["id"]: family for family in manifest["memorydata_families"]}
    assert families["locomo"]["ready"] is True
    assert families["longbench"]["ready"] is True
    assert manifest["ready_summary"]["memorydata_ready_count"] == 2


def test_materialize_locomo_4cat_filters_official_raw_to_first_four_categories(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_brain.evaluation import memory_eval_datasets
    from agent_brain.evaluation.memory_eval_datasets import materialize_memory_eval_datasets

    raw_rows = [
        {
            "sample_id": "sample-1",
            "conversation": {},
            "qa": [
                {"question": "q1", "answer": "a1", "category": 1},
                {"question": "q5", "answer": "a5", "category": 5},
                {"question": "q2", "answer": "a2", "category": "2"},
            ],
        },
        {
            "sample_id": "sample-2",
            "conversation": {},
            "qa": [{"question": "q5-only", "answer": "a", "category": 5}],
        },
    ]

    def fake_download(source_url: str, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(raw_rows), encoding="utf-8")

    monkeypatch.setattr(memory_eval_datasets, "_download_file", fake_download)
    memorydata_repo = tmp_path / "MemoryData"

    payload = materialize_memory_eval_datasets(
        dataset="locomo-4cat",
        memorydata_repo=memorydata_repo,
        cache_root=tmp_path / "external",
    )

    assert payload["selected"] == ["locomo_4cat_dist"]
    assert payload["artifacts"][0]["status"] == "derived"
    target = (
        memorydata_repo
        / "datasets"
        / "LoCoMo"
        / "rq1_4cat_600_dist"
        / "locomo_4cat_600_dist.json"
    )
    derived = json.loads(target.read_text(encoding="utf-8"))
    assert [sample["sample_id"] for sample in derived] == ["sample-1"]
    assert [qa["category"] for qa in derived[0]["qa"]] == [1, "2"]
    assert payload["memorydata_manifest"]["ready_summary"]["memorydata_ready_count"] == 2


def test_materialize_longbench_rep150_writes_memorydata_save_to_disk_target(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_brain.evaluation import memory_eval_datasets
    from agent_brain.evaluation.memory_eval_datasets import materialize_memory_eval_datasets

    saved_targets: list[Path] = []

    def fake_save(target: Path) -> None:
        saved_targets.append(target)
        target.mkdir(parents=True)
        (target / "dataset_info.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(memory_eval_datasets, "_save_longbench_rep150_to_disk", fake_save)
    memorydata_repo = tmp_path / "MemoryData"

    payload = materialize_memory_eval_datasets(
        dataset="longbench-rep150",
        memorydata_repo=memorydata_repo,
        cache_root=tmp_path / "external",
    )

    expected = memorydata_repo / "datasets" / "longBench_rep150_proportional" / "datasets"
    assert payload["selected"] == ["longbench_rep150_proportional"]
    assert payload["artifacts"][0]["status"] == "downloaded"
    assert saved_targets == [expected]
    assert payload["memorydata_manifest"]["ready_summary"]["memorydata_ready_count"] == 1


def test_memoryagentbench_matrix_prepares_lru_configs_and_loader(tmp_path: Path) -> None:
    from agent_brain.evaluation.memoryagentbench_matrix import (
        ensure_memoryagentbench_matrix_support,
        memoryagentbench_capability_specs,
    )

    memorydata_repo = tmp_path / "MemoryData"
    loader = memorydata_repo / "benchmark" / "memoryagentbench" / "loader.py"
    loader.parent.mkdir(parents=True)
    loader.write_text(
        """
supported_splits = {
    "Accurate_Retrieval", "Test_Time_Learning",
    "Conflict_Resolution"
}
supported_hf_datasets = {
    'Accurate_Retrieval', 'Test_Time_Learning',
    'Conflict_Resolution'
}
""",
        encoding="utf-8",
    )

    payload = ensure_memoryagentbench_matrix_support(memorydata_repo)

    specs = {spec.id: spec for spec in memoryagentbench_capability_specs()}
    detective = specs["lru_detective_qa"]
    detective_config = memorydata_repo / detective.config_path
    assert detective_config.is_file()
    assert "dataset: Long_Range_Understanding" in detective_config.read_text(encoding="utf-8")
    assert "sub_dataset: detective_qa" in detective_config.read_text(encoding="utf-8")
    patched_loader = loader.read_text(encoding="utf-8")
    assert '"Long_Range_Understanding"' in patched_loader
    assert "'Long_Range_Understanding'" in patched_loader
    assert payload["loader_patched"] is True
    assert "lru_detective_qa" in payload["prepared_configs"]


def test_memoryagentbench_matrix_keeps_loader_set_items_separated(tmp_path: Path) -> None:
    from agent_brain.evaluation.memoryagentbench_matrix import ensure_memoryagentbench_matrix_support

    memorydata_repo = tmp_path / "MemoryData"
    loader = memorydata_repo / "benchmark" / "memoryagentbench" / "loader.py"
    loader.parent.mkdir(parents=True)
    loader.write_text(
        """
supported_splits = {
    "Accurate_Retrieval", "Test_Time_Learning",
    "Conflict_Resolution"
}
supported_hf_datasets = {
    'Accurate_Retrieval', 'Test_Time_Learning',
    'Conflict_Resolution'
}
""",
        encoding="utf-8",
    )

    ensure_memoryagentbench_matrix_support(memorydata_repo)

    patched_loader = loader.read_text(encoding="utf-8")
    assert '"Conflict_Resolution",\n    "Long_Range_Understanding",' in patched_loader
    assert "'Conflict_Resolution',\n    'Long_Range_Understanding'," in patched_loader


def test_memoryagentbench_matrix_repairs_previously_concatenated_lru_entry(
    tmp_path: Path,
) -> None:
    from agent_brain.evaluation.memoryagentbench_matrix import ensure_memoryagentbench_matrix_support

    memorydata_repo = tmp_path / "MemoryData"
    loader = memorydata_repo / "benchmark" / "memoryagentbench" / "loader.py"
    loader.parent.mkdir(parents=True)
    loader.write_text(
        """
supported_splits = {
    "Accurate_Retrieval", "Test_Time_Learning",
    "Conflict_Resolution"
    "Long_Range_Understanding",
}
supported_hf_datasets = {
    'Accurate_Retrieval', 'Test_Time_Learning',
    'Conflict_Resolution'
    'Long_Range_Understanding',
}
""",
        encoding="utf-8",
    )

    ensure_memoryagentbench_matrix_support(memorydata_repo)

    patched_loader = loader.read_text(encoding="utf-8")
    assert "Conflict_ResolutionLong_Range_Understanding" not in patched_loader
    assert '"Conflict_Resolution",\n    "Long_Range_Understanding",' in patched_loader
    assert "'Conflict_Resolution',\n    'Long_Range_Understanding'," in patched_loader


def test_memorydata_manifest_requires_all_membench_slices(tmp_path: Path) -> None:
    from agent_brain.evaluation.memorydata_manifest import (
        MEMBENCH_FIRSTAGENT_SLICES,
        build_memorydata_manifest,
    )

    memorydata_repo = tmp_path / "MemoryData"
    target = memorydata_repo / "datasets" / "MemBench" / "MemData" / "FirstAgent"
    target.mkdir(parents=True)
    (target / f"{MEMBENCH_FIRSTAGENT_SLICES[0]}.json").write_text("[]", encoding="utf-8")

    partial_manifest = build_memorydata_manifest(
        memorydata_repo=memorydata_repo,
        cache_root=tmp_path / "external",
    )
    partial = {
        family["id"]: family
        for family in partial_manifest["memorydata_families"]
    }["membench"]
    assert partial["ready"] is False

    for slice_name in MEMBENCH_FIRSTAGENT_SLICES[1:]:
        (target / f"{slice_name}.json").write_text("[]", encoding="utf-8")

    ready_manifest = build_memorydata_manifest(
        memorydata_repo=memorydata_repo,
        cache_root=tmp_path / "external",
    )
    ready = {
        family["id"]: family
        for family in ready_manifest["memorydata_families"]
    }["membench"]
    assert ready["ready"] is True


def test_materialize_membench_downloads_all_firstagent_slices(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_brain.evaluation import memory_eval_datasets
    from agent_brain.evaluation.memorydata_manifest import MEMBENCH_FIRSTAGENT_SLICES
    from agent_brain.evaluation.memory_eval_datasets import materialize_memory_eval_datasets

    downloaded: list[tuple[str, Path]] = []

    def fake_download(source_url: str, target: Path) -> None:
        downloaded.append((source_url, target))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("[]", encoding="utf-8")

    monkeypatch.setattr(memory_eval_datasets, "_download_file", fake_download)

    payload = materialize_memory_eval_datasets(
        dataset="membench",
        memorydata_repo=tmp_path / "MemoryData",
        cache_root=tmp_path / "external",
    )

    assert payload["selected"] == ["membench_firstagent"]
    assert payload["artifacts"][0]["status"] == "downloaded"
    assert [target.name for _, target in downloaded] == [
        f"{slice_name}.json" for slice_name in MEMBENCH_FIRSTAGENT_SLICES
    ]
    assert all(
        "https://raw.githubusercontent.com/import-myself/Membench/main/MemData/FirstAgent/"
        in source_url
        for source_url, _ in downloaded
    )
    assert payload["memorydata_manifest"]["ready_summary"]["memorydata_ready_count"] == 1
