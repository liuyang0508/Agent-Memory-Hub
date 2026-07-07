"""Dataset manifest for MemoryData-compatible external evaluations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LONGMEMEVAL_CLEANED_BASE_URL = (
    "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main"
)
MEMORYAGENTBENCH_HF_URL = "https://huggingface.co/datasets/ai-hyz/MemoryAgentBench"
MEMORYAGENTBENCH_HF_ID = "ai-hyz/MemoryAgentBench"
LOCOMO_RAW_URL = "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json"
LONGBENCH_V2_HF_URL = "https://huggingface.co/datasets/THUDM/LongBench-v2"
LONGBENCH_V2_HF_ID = "THUDM/LongBench-v2"
MEMBENCH_GITHUB_URL = "https://github.com/import-myself/Membench"
MEMBENCH_RAW_BASE_URL = "https://raw.githubusercontent.com/import-myself/Membench/main/MemData/FirstAgent"
MEMBENCH_FIRSTAGENT_SLICES = (
    "simple",
    "noisy",
    "knowledge_update",
    "highlevel",
    "RecMultiSession",
)


@dataclass(frozen=True)
class DatasetSpec:
    id: str
    alias: str
    name: str
    benchmark: str
    target_path: Path
    kind: str
    source_url: str
    materialization_mode: str
    config_paths: tuple[str, ...]
    notes: str
    required_files: tuple[str, ...] = ()


def build_memorydata_manifest(
    *,
    memorydata_repo: Path = Path(".cache/external/MemoryData"),
    cache_root: Path = Path(".cache/external"),
) -> dict[str, Any]:
    """Build a source/dataset readiness manifest for MemoryData benchmark families."""

    memorydata_repo = Path(memorydata_repo)
    cache_root = Path(cache_root)
    families = [_status(spec) for spec in memorydata_family_specs(memorydata_repo)]
    longmemeval = _status(longmemeval_spec(cache_root))
    return {
        "memorydata_families": families,
        "longmemeval": longmemeval,
        "ready_summary": {
            "memorydata_ready_count": sum(1 for family in families if family["ready"]),
            "memorydata_total_count": len(families),
            "longmemeval_ready": longmemeval["ready"],
        },
    }


def memorydata_family_specs(memorydata_repo: Path) -> tuple[DatasetSpec, ...]:
    """Return MemoryData full-family dataset specs in the expected runner layout."""

    memorydata_repo = Path(memorydata_repo)
    return (
        DatasetSpec(
            id="memoryagentbench",
            alias="memoryagentbench",
            name="MemoryAgentBench Hugging Face snapshot",
            benchmark="MemoryAgentBench",
            target_path=(
                memorydata_repo
                / "datasets"
                / "MemoryAgentBench"
                / "eval_dataset_collection"
            ),
            kind="dir",
            source_url=MEMORYAGENTBENCH_HF_URL,
            materialization_mode="hf_save_to_disk",
            config_paths=(
                "benchmark/memoryagentbench/Accurate_Retrieval/config/EventQA/"
                "Eventqa_full.yaml",
            ),
            notes="Hugging Face save_to_disk directory expected by MemoryData.",
        ),
        DatasetSpec(
            id="locomo",
            alias="locomo",
            name="LoCoMo 4-category preprocessed dist",
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
            materialization_mode="derived_json_from_official_raw",
            config_paths=("benchmark/locomo/config/Locomo_qa_4cat_600_dist.yaml",),
            notes=(
                "Derived from official LoCoMo locomo10.json by retaining QA categories 1-4 "
                "and writing the MemoryData full-preset path."
            ),
        ),
        DatasetSpec(
            id="locomo_category5_adversarial",
            alias="locomo-category5",
            name="LoCoMo category5 adversarial",
            benchmark="LoCoMo",
            target_path=memorydata_repo / "datasets" / "LoCoMo" / "locomo10.json",
            kind="file",
            source_url=LOCOMO_RAW_URL,
            materialization_mode="official_raw_category_filter",
            config_paths=("benchmark/locomo/config/Locomo_qa_category5_adversarial.yaml",),
            notes=(
                "Supplemental category 5 adversarial track read from official locomo10.json; "
                "kept separate from the category 1-4 QA score."
            ),
        ),
        DatasetSpec(
            id="longbench",
            alias="longbench",
            name="LongBench proportional subset",
            benchmark="LongBench",
            target_path=memorydata_repo / "datasets" / "longBench_rep150_proportional" / "datasets",
            kind="dir",
            source_url=LONGBENCH_V2_HF_URL,
            materialization_mode="huggingface_derived_save_to_disk",
            config_paths=("benchmark/longbench/config/LongBench_rep150_proportional.yaml",),
            notes=(
                "Derived from THUDM/LongBench-v2 train split as a deterministic 150-row "
                "proportional save_to_disk subset."
            ),
        ),
        DatasetSpec(
            id="longbench_v2_503_full",
            alias="longbench-v2-full",
            name="LongBench-v2 503-question full",
            benchmark="LongBench",
            target_path=memorydata_repo / "datasets" / "longBench_v2_503_full" / "datasets",
            kind="dir",
            source_url=LONGBENCH_V2_HF_URL,
            materialization_mode="huggingface_save_to_disk",
            config_paths=("benchmark/longbench/config/LongBench_v2_503_full.yaml",),
            notes=(
                "Official THUDM/LongBench-v2 train split saved to disk for the "
                "503-question full benchmark configuration."
            ),
        ),
        DatasetSpec(
            id="membench",
            alias="membench",
            name="MemBench FirstAgent slices",
            benchmark="MemBench",
            target_path=memorydata_repo / "datasets" / "MemBench" / "MemData" / "FirstAgent",
            kind="dir",
            source_url=MEMBENCH_GITHUB_URL,
            materialization_mode="github_raw_bundle",
            config_paths=(
                "benchmark/membench/config/MemBench_simple.yaml",
                "benchmark/membench/config/MemBench_noisy.yaml",
                "benchmark/membench/config/MemBench_knowledge_update.yaml",
                "benchmark/membench/config/MemBench_highlevel.yaml",
                "benchmark/membench/config/MemBench_RecMultiSession.yaml",
            ),
            notes="MemoryData expects FirstAgent JSON slices.",
            required_files=tuple(f"{slice_name}.json" for slice_name in MEMBENCH_FIRSTAGENT_SLICES),
        ),
    )


def longmemeval_spec(cache_root: Path) -> DatasetSpec:
    """Return the LongMemEval-S retrieval-smoke dataset spec."""

    cache_root = Path(cache_root)
    return DatasetSpec(
        id="longmemeval_s_cleaned",
        alias="longmemeval-s",
        name="LongMemEval-S cleaned",
        benchmark="LongMemEval",
        target_path=cache_root / "LongMemEval" / "data" / "longmemeval_s_cleaned.json",
        kind="file",
        source_url=f"{LONGMEMEVAL_CLEANED_BASE_URL}/longmemeval_s_cleaned.json",
        materialization_mode="direct_download",
        config_paths=(),
        notes="Retrieval-only benchmark artifact; not a MemoryData full family.",
    )


def _status(spec: DatasetSpec) -> dict[str, Any]:
    target = Path(spec.target_path)
    ready = _target_ready(target, spec.kind, required_files=spec.required_files)
    return {
        "id": spec.id,
        "alias": spec.alias,
        "name": spec.name,
        "benchmark": spec.benchmark,
        "ready": ready,
        "target_path": str(target),
        "kind": spec.kind,
        "source_url": spec.source_url,
        "source_type": spec.materialization_mode,
        "materialization_mode": spec.materialization_mode,
        "config_paths": list(spec.config_paths),
        "notes": spec.notes,
        "required_files": list(spec.required_files),
        "size_bytes": target.stat().st_size if target.is_file() else None,
        "sha256": _sha256(target) if target.is_file() else None,
    }


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "DatasetSpec",
    "LOCOMO_RAW_URL",
    "LONGMEMEVAL_CLEANED_BASE_URL",
    "LONGBENCH_V2_HF_ID",
    "LONGBENCH_V2_HF_URL",
    "MEMBENCH_FIRSTAGENT_SLICES",
    "MEMBENCH_GITHUB_URL",
    "MEMBENCH_RAW_BASE_URL",
    "MEMORYAGENTBENCH_HF_ID",
    "MEMORYAGENTBENCH_HF_URL",
    "build_memorydata_manifest",
    "longmemeval_spec",
    "memorydata_family_specs",
]
