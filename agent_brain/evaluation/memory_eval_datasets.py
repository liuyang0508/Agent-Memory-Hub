"""Dataset materialization plan for external memory evaluation loops."""

from __future__ import annotations

import json
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_brain.evaluation.memorydata_manifest import (
    LOCOMO_RAW_URL,
    LONGBENCH_V2_HF_ID,
    LONGBENCH_V2_HF_URL,
    LONGMEMEVAL_CLEANED_BASE_URL,
    MEMBENCH_FIRSTAGENT_SLICES,
    MEMBENCH_RAW_BASE_URL,
    MEMORYAGENTBENCH_HF_ID,
    MEMORYAGENTBENCH_HF_URL,
    build_memorydata_manifest,
)


@dataclass(frozen=True)
class DatasetArtifactSpec:
    id: str
    alias: str
    name: str
    benchmark: str
    target_path: Path
    kind: str
    source_url: str
    source_type: str
    notes: str
    required_files: tuple[str, ...] = ()


def build_memory_eval_dataset_plan(
    *,
    memorydata_repo: Path = Path(".cache/external/MemoryData"),
    cache_root: Path = Path(".cache/external"),
) -> dict[str, Any]:
    memorydata_repo = Path(memorydata_repo)
    cache_root = Path(cache_root)
    specs = _dataset_specs(memorydata_repo=memorydata_repo, cache_root=cache_root)
    artifacts = [
        _artifact_status(spec, memorydata_repo=memorydata_repo, cache_root=cache_root)
        for spec in specs
    ]
    return {
        "ready_count": sum(1 for artifact in artifacts if artifact["ready"]),
        "total_count": len(artifacts),
        "artifacts": artifacts,
        "longmemeval_primary_id": "longmemeval_s_cleaned",
        "memorydata_manifest": build_memorydata_manifest(
            memorydata_repo=memorydata_repo,
            cache_root=cache_root,
        ),
    }


def materialize_memory_eval_datasets(
    *,
    dataset: str,
    memorydata_repo: Path = Path(".cache/external/MemoryData"),
    cache_root: Path = Path(".cache/external"),
    dry_run: bool = False,
) -> dict[str, Any]:
    plan = build_memory_eval_dataset_plan(memorydata_repo=memorydata_repo, cache_root=cache_root)
    selected = _select_artifacts(plan["artifacts"], dataset)
    if dry_run:
        return {
            "mode": "dry-run",
            "selected": [artifact["id"] for artifact in selected],
            "artifacts": selected,
            "memorydata_manifest": plan["memorydata_manifest"],
        }

    results = []
    for artifact in selected:
        if artifact["ready"]:
            results.append({**artifact, "status": "ready"})
            continue
        if artifact["source_type"] == "direct_download":
            _download_file(artifact["source_url"], Path(artifact["target_path"]))
            refreshed = build_memory_eval_dataset_plan(memorydata_repo=memorydata_repo, cache_root=cache_root)
            refreshed_artifact = _artifact_by_id(refreshed["artifacts"], artifact["id"])
            results.append({**refreshed_artifact, "status": "downloaded" if refreshed_artifact["ready"] else "failed"})
            continue
        if artifact["source_type"] == "huggingface_dataset":
            _save_memoryagentbench_to_disk(Path(artifact["target_path"]))
            refreshed = build_memory_eval_dataset_plan(memorydata_repo=memorydata_repo, cache_root=cache_root)
            refreshed_artifact = _artifact_by_id(refreshed["artifacts"], artifact["id"])
            results.append({**refreshed_artifact, "status": "downloaded" if refreshed_artifact["ready"] else "failed"})
            continue
        if artifact["source_type"] == "github_raw_bundle":
            _download_membench_firstagent(Path(artifact["target_path"]))
            refreshed = build_memory_eval_dataset_plan(memorydata_repo=memorydata_repo, cache_root=cache_root)
            refreshed_artifact = _artifact_by_id(refreshed["artifacts"], artifact["id"])
            results.append({**refreshed_artifact, "status": "downloaded" if refreshed_artifact["ready"] else "failed"})
            continue
        if artifact["source_type"] == "derived_json_from_official_raw":
            raw_target = memorydata_repo / "datasets" / "LoCoMo" / "locomo10.json"
            if not _target_ready(raw_target, "file"):
                _download_file(LOCOMO_RAW_URL, raw_target)
            _write_locomo_4cat_dist(raw_target, Path(artifact["target_path"]))
            refreshed = build_memory_eval_dataset_plan(memorydata_repo=memorydata_repo, cache_root=cache_root)
            refreshed_artifact = _artifact_by_id(refreshed["artifacts"], artifact["id"])
            results.append({**refreshed_artifact, "status": "derived" if refreshed_artifact["ready"] else "failed"})
            continue
        if artifact["source_type"] == "huggingface_derived_save_to_disk":
            _save_longbench_rep150_to_disk(Path(artifact["target_path"]))
            refreshed = build_memory_eval_dataset_plan(memorydata_repo=memorydata_repo, cache_root=cache_root)
            refreshed_artifact = _artifact_by_id(refreshed["artifacts"], artifact["id"])
            results.append({**refreshed_artifact, "status": "downloaded" if refreshed_artifact["ready"] else "failed"})
            continue
        if artifact["source_type"] == "huggingface_save_to_disk":
            _save_longbench_v2_full_to_disk(Path(artifact["target_path"]))
            refreshed = build_memory_eval_dataset_plan(memorydata_repo=memorydata_repo, cache_root=cache_root)
            refreshed_artifact = _artifact_by_id(refreshed["artifacts"], artifact["id"])
            results.append({**refreshed_artifact, "status": "downloaded" if refreshed_artifact["ready"] else "failed"})
            continue
        results.append({**artifact, "status": "manual"})

    return {
        "mode": "materialize",
        "selected": [artifact["id"] for artifact in selected],
        "artifacts": results,
        "memorydata_manifest": build_memory_eval_dataset_plan(
            memorydata_repo=memorydata_repo,
            cache_root=cache_root,
        )["memorydata_manifest"],
    }


def _dataset_specs(*, memorydata_repo: Path, cache_root: Path) -> tuple[DatasetArtifactSpec, ...]:
    return (
        DatasetArtifactSpec(
            id="longmemeval_s_cleaned",
            alias="longmemeval-s",
            name="LongMemEval-S cleaned",
            benchmark="LongMemEval",
            target_path=cache_root / "LongMemEval" / "data" / "longmemeval_s_cleaned.json",
            kind="file",
            source_url=f"{LONGMEMEVAL_CLEANED_BASE_URL}/longmemeval_s_cleaned.json",
            source_type="direct_download",
            notes="Primary retrieval-only benchmark for R@5/R@10 parity with public comparison tables.",
        ),
        DatasetArtifactSpec(
            id="longmemeval_oracle",
            alias="longmemeval-oracle",
            name="LongMemEval oracle cleaned",
            benchmark="LongMemEval",
            target_path=cache_root / "LongMemEval" / "data" / "longmemeval_oracle.json",
            kind="file",
            source_url=f"{LONGMEMEVAL_CLEANED_BASE_URL}/longmemeval_oracle.json",
            source_type="direct_download",
            notes="Oracle split used for sanity checks and methodology comparison.",
        ),
        DatasetArtifactSpec(
            id="memoryagentbench_hf",
            alias="memoryagentbench",
            name="MemoryAgentBench Hugging Face snapshot",
            benchmark="MemoryAgentBench",
            target_path=memorydata_repo / "datasets" / "MemoryAgentBench" / "eval_dataset_collection",
            kind="dir",
            source_url=MEMORYAGENTBENCH_HF_URL,
            source_type="huggingface_dataset",
            notes="MemoryData loader falls back to this HF dataset when local save_to_disk copy is absent.",
        ),
        DatasetArtifactSpec(
            id="locomo_raw",
            alias="locomo-raw",
            name="LoCoMo raw locomo10",
            benchmark="LoCoMo",
            target_path=memorydata_repo / "datasets" / "LoCoMo" / "locomo10.json",
            kind="file",
            source_url=LOCOMO_RAW_URL,
            source_type="direct_download",
            notes="Raw LoCoMo data; MemoryData full preset still expects a preprocessed 4-category dist file.",
        ),
        DatasetArtifactSpec(
            id="locomo_4cat_dist",
            alias="locomo-4cat",
            name="LoCoMo 4-category MemoryData dist",
            benchmark="LoCoMo",
            target_path=(
                memorydata_repo
                / "datasets"
                / "LoCoMo"
                / "rq1_4cat_600_dist"
                / "locomo_4cat_600_dist.json"
            ),
            kind="file",
            source_url=LOCOMO_RAW_URL,
            source_type="derived_json_from_official_raw",
            notes=(
                "Derived from official locomo10.json by retaining categories 1-4, "
                "the four LoCoMo QA types used by common memory benchmark reports."
            ),
        ),
        DatasetArtifactSpec(
            id="longbench_rep150_proportional",
            alias="longbench-rep150",
            name="LongBench-v2 150-row proportional subset",
            benchmark="LongBench",
            target_path=memorydata_repo / "datasets" / "longBench_rep150_proportional" / "datasets",
            kind="dir",
            source_url=LONGBENCH_V2_HF_URL,
            source_type="huggingface_derived_save_to_disk",
            notes=(
                "Derived from THUDM/LongBench-v2 train split and saved to the "
                "MemoryData LongBench_rep150_proportional path."
            ),
        ),
        DatasetArtifactSpec(
            id="longbench_v2_503_full",
            alias="longbench-v2-full",
            name="LongBench-v2 503-question full",
            benchmark="LongBench",
            target_path=memorydata_repo / "datasets" / "longBench_v2_503_full" / "datasets",
            kind="dir",
            source_url=LONGBENCH_V2_HF_URL,
            source_type="huggingface_save_to_disk",
            notes=(
                "Official THUDM/LongBench-v2 train split saved as a MemoryData-compatible "
                "503-question full dataset."
            ),
        ),
        DatasetArtifactSpec(
            id="membench_firstagent",
            alias="membench",
            name="MemBench FirstAgent slices",
            benchmark="MemBench",
            target_path=memorydata_repo / "datasets" / "MemBench" / "MemData" / "FirstAgent",
            kind="dir",
            source_url=MEMBENCH_RAW_BASE_URL,
            source_type="github_raw_bundle",
            notes="Direct raw download of the five MemoryData FirstAgent JSON slices.",
            required_files=tuple(f"{slice_name}.json" for slice_name in MEMBENCH_FIRSTAGENT_SLICES),
        ),
    )


def _artifact_status(
    spec: DatasetArtifactSpec,
    *,
    memorydata_repo: Path,
    cache_root: Path,
) -> dict[str, Any]:
    target = Path(spec.target_path)
    return {
        "id": spec.id,
        "alias": spec.alias,
        "name": spec.name,
        "benchmark": spec.benchmark,
        "ready": _target_ready(target, spec.kind, required_files=spec.required_files),
        "target_path": str(target),
        "kind": spec.kind,
        "source_url": spec.source_url,
        "source_type": spec.source_type,
        "notes": spec.notes,
        "required_files": list(spec.required_files),
        "materialize_command": [
            "python",
            "benchmarks/materialize_memory_eval_datasets.py",
            "--dataset",
            spec.alias,
            "--cache-root",
            str(cache_root),
            "--memorydata-repo",
            str(memorydata_repo),
        ],
    }


def _select_artifacts(artifacts: list[dict[str, Any]], dataset: str) -> list[dict[str, Any]]:
    normalized = dataset.strip().lower()
    if normalized in {"all", "downloadable"}:
        return [artifact for artifact in artifacts if artifact["source_type"] != "manual"]
    selected = [
        artifact
        for artifact in artifacts
        if normalized in {artifact["id"].lower(), artifact["alias"].lower()}
    ]
    if not selected:
        aliases = ", ".join(sorted(artifact["alias"] for artifact in artifacts))
        raise ValueError(f"Unknown dataset '{dataset}'. Available aliases: {aliases}")
    return selected


def _artifact_by_id(artifacts: list[dict[str, Any]], artifact_id: str) -> dict[str, Any]:
    for artifact in artifacts:
        if artifact["id"] == artifact_id:
            return artifact
    raise KeyError(artifact_id)


def _target_ready(target: Path, kind: str, *, required_files: tuple[str, ...] = ()) -> bool:
    if kind == "file":
        return target.is_file() and target.stat().st_size > 0
    if kind == "dir":
        if required_files:
            return target.is_dir() and all(
                (target / required_file).is_file()
                and (target / required_file).stat().st_size > 0
                for required_file in required_files
            )
        return target.is_dir() and any(target.iterdir())
    raise ValueError(f"Unsupported artifact kind: {kind}")


def _download_file(source_url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_target = target.with_suffix(target.suffix + ".tmp")
    with urllib.request.urlopen(source_url, timeout=60) as response, tmp_target.open("wb") as out:
        shutil.copyfileobj(response, out)
    tmp_target.replace(target)


def _save_memoryagentbench_to_disk(target: Path) -> None:
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise RuntimeError("Install the `datasets` package before materializing MemoryAgentBench.") from exc

    target.parent.mkdir(parents=True, exist_ok=True)
    dataset = load_dataset(MEMORYAGENTBENCH_HF_ID)
    dataset.save_to_disk(str(target))


def _write_locomo_4cat_dist(raw_source: Path, target: Path) -> None:
    raw_samples = json.loads(Path(raw_source).read_text(encoding="utf-8"))
    filtered_samples = []
    for sample in raw_samples:
        qa_rows = [
            qa
            for qa in sample.get("qa", [])
            if str(qa.get("category", "")).strip() in {"1", "2", "3", "4"}
        ]
        if not qa_rows:
            continue
        filtered_sample = dict(sample)
        filtered_sample["qa"] = qa_rows
        filtered_samples.append(filtered_sample)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(filtered_samples, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _save_longbench_rep150_to_disk(target: Path) -> None:
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise RuntimeError("Install the `datasets` package before materializing LongBench.") from exc

    dataset = load_dataset(LONGBENCH_V2_HF_ID, split="train")
    indices = _select_longbench_rep150_indices(list(dataset))
    subset = dataset.select(indices)
    _save_dataset_dir_atomically(subset, target)


def _save_longbench_v2_full_to_disk(target: Path) -> None:
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise RuntimeError("Install the `datasets` package before materializing LongBench.") from exc

    dataset = load_dataset(LONGBENCH_V2_HF_ID, split="train")
    if len(dataset) > 503:
        dataset = dataset.select(range(503))
    _save_dataset_dir_atomically(dataset, target)


def _select_longbench_rep150_indices(rows: list[dict[str, Any]], target_size: int = 150) -> list[int]:
    if len(rows) <= target_size:
        return list(range(len(rows)))

    grouped: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        key = str(row.get("domain") or "unknown")
        grouped.setdefault(key, []).append(index)

    target_size = min(target_size, len(rows))
    total = len(rows)
    allocations: dict[str, int] = {}
    fractions: list[tuple[float, int, str]] = []
    for key, indices in grouped.items():
        exact = len(indices) * target_size / total
        base = int(exact)
        allocations[key] = base
        fractions.append((exact - base, len(indices), key))

    remaining = target_size - sum(allocations.values())
    for _, _, key in sorted(fractions, reverse=True)[:remaining]:
        allocations[key] += 1

    selected: list[int] = []
    for key, indices in grouped.items():
        selected.extend(_evenly_spaced_indices(indices, allocations[key]))
    return sorted(selected)


def _evenly_spaced_indices(indices: list[int], count: int) -> list[int]:
    if count <= 0:
        return []
    if count >= len(indices):
        return list(indices)
    if count == 1:
        return [indices[0]]

    positions = [
        round(position * (len(indices) - 1) / (count - 1))
        for position in range(count)
    ]
    selected = []
    seen = set()
    for position in positions:
        candidate_position = position
        while candidate_position < len(indices) and indices[candidate_position] in seen:
            candidate_position += 1
        if candidate_position >= len(indices):
            candidate_position = position
            while candidate_position >= 0 and indices[candidate_position] in seen:
                candidate_position -= 1
        if candidate_position >= 0:
            selected.append(indices[candidate_position])
            seen.add(indices[candidate_position])
    return selected


def _save_dataset_dir_atomically(dataset: Any, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_target = target.with_name(target.name + ".tmp")
    if tmp_target.exists():
        shutil.rmtree(tmp_target)
    dataset.save_to_disk(str(tmp_target))
    if target.exists():
        shutil.rmtree(target)
    tmp_target.rename(target)


def _download_membench_firstagent(target: Path) -> None:
    for slice_name in MEMBENCH_FIRSTAGENT_SLICES:
        _download_file(
            f"{MEMBENCH_RAW_BASE_URL}/{slice_name}.json",
            target / f"{slice_name}.json",
        )


__all__ = [
    "build_memory_eval_dataset_plan",
    "materialize_memory_eval_datasets",
]
