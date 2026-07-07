"""MemoryAgentBench capability matrix preparation helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MemoryAgentBenchCapabilitySpec:
    id: str
    dimension: str
    dataset: str
    sub_dataset: str
    config_path: str
    metric_field: str
    max_test_samples: int
    generation_max_length: int
    context_max_length: int
    chunk_size: int = 4096
    shots: int = 0
    use_chat_template: bool = True
    stop_new_line: bool = False

    def to_yaml(self) -> str:
        lines = [
            "# Data/Task configuration",
            f"dataset: {self.dataset}",
            f"chunk_size: {self.chunk_size}",
            "debug: false",
            "seed: 42",
            "",
            "# specific configuration",
            f"context_max_length: {self.context_max_length}",
            f"sub_dataset: {self.sub_dataset}",
            f"generation_max_length: {self.generation_max_length}",
            "test_files: ''",
            "demo_files: ''",
            f"use_chat_template: {_yaml_bool(self.use_chat_template)}",
            f"max_test_samples: {self.max_test_samples}",
            f"shots: {self.shots}",
        ]
        if self.stop_new_line:
            lines.append("stop_new_line: true")
        lines.append("tag: null")
        return "\n".join(lines) + "\n"


def memoryagentbench_capability_specs() -> tuple[MemoryAgentBenchCapabilitySpec, ...]:
    """Return the MemoryAgentBench core capability configs used by AMH."""

    return (
        MemoryAgentBenchCapabilitySpec(
            id="ar_eventqa_full",
            dimension="Accurate Retrieval",
            dataset="Accurate_Retrieval",
            sub_dataset="eventqa_full",
            config_path="benchmark/memoryagentbench/Accurate_Retrieval/config/EventQA/Eventqa_full.yaml",
            metric_field="substring_exact_match",
            max_test_samples=5,
            generation_max_length=40,
            context_max_length=800000,
        ),
        MemoryAgentBenchCapabilitySpec(
            id="ttl_icl_banking77",
            dimension="Test-Time Learning",
            dataset="Test_Time_Learning",
            sub_dataset="icl_banking77_5900shot_balance",
            config_path="benchmark/memoryagentbench/Test_Time_Learning/config/ICL/ICL_banking77.yaml",
            metric_field="exact_match",
            max_test_samples=100,
            generation_max_length=20,
            context_max_length=131072,
            use_chat_template=False,
            stop_new_line=True,
        ),
        MemoryAgentBenchCapabilitySpec(
            id="lru_detective_qa",
            dimension="Long-Range Understanding",
            dataset="Long_Range_Understanding",
            sub_dataset="detective_qa",
            config_path="benchmark/memoryagentbench/Long_Range_Understanding/config/Detective_QA.yaml",
            metric_field="exact_match",
            max_test_samples=10,
            generation_max_length=2000,
            context_max_length=200000,
        ),
        MemoryAgentBenchCapabilitySpec(
            id="cr_factconsolidation_mh_6k",
            dimension="Conflict Resolution",
            dataset="Conflict_Resolution",
            sub_dataset="factconsolidation_mh_6k",
            config_path=(
                "benchmark/memoryagentbench/Conflict_Resolution/config/"
                "Factconsolidation_mh_6k.yaml"
            ),
            metric_field="substring_exact_match",
            max_test_samples=1,
            generation_max_length=10,
            context_max_length=6000,
        ),
    )


def ensure_memoryagentbench_matrix_support(memorydata_repo: Path) -> dict[str, Any]:
    """Prepare MemoryData to run all core MemoryAgentBench capability configs."""

    memorydata_repo = Path(memorydata_repo)
    prepared_configs = []
    for spec in memoryagentbench_capability_specs():
        config_path = memorydata_repo / spec.config_path
        config_path.parent.mkdir(parents=True, exist_ok=True)
        if not config_path.exists() or config_path.read_text(encoding="utf-8") != spec.to_yaml():
            config_path.write_text(spec.to_yaml(), encoding="utf-8")
        prepared_configs.append(spec.id)

    return {
        "prepared_configs": prepared_configs,
        "loader_patched": _patch_memorydata_loader_for_lru(memorydata_repo),
    }


def _patch_memorydata_loader_for_lru(memorydata_repo: Path) -> bool:
    loader_path = Path(memorydata_repo) / "benchmark" / "memoryagentbench" / "loader.py"
    if not loader_path.is_file():
        raise FileNotFoundError(f"MemoryData MemoryAgentBench loader not found: {loader_path}")

    original = loader_path.read_text(encoding="utf-8")
    patched = original
    patched = _insert_lru_split_in_set(patched, "supported_splits", quote='"')
    patched = _insert_lru_split_in_set(patched, "supported_hf_datasets", quote="'")
    if patched == original:
        return False
    loader_path.write_text(patched, encoding="utf-8")
    return True


def _insert_lru_split_in_set(text: str, variable_name: str, *, quote: str) -> str:
    pattern = re.compile(rf"({re.escape(variable_name)}\s*=\s*\{{)(.*?)(\n\s*\}})", re.DOTALL)
    match = pattern.search(text)
    if not match:
        return text

    body = _separate_existing_lru_entry(match.group(2), quote=quote)
    if "Long_Range_Understanding" in body:
        return text[: match.start(2)] + body + text[match.start(3) :]

    indent_match = re.search(r"\n(\s*)", body)
    indent = indent_match.group(1) if indent_match else "        "
    insertion = f"\n{indent}{quote}Long_Range_Understanding{quote},"
    stripped_body = body.rstrip()
    separator = "," if stripped_body and not stripped_body.endswith(",") else ""
    updated_body = stripped_body + separator + insertion
    return text[: match.start(2)] + updated_body + text[match.start(3) :]


def _separate_existing_lru_entry(body: str, *, quote: str) -> str:
    quoted_conflict = re.escape(f"{quote}Conflict_Resolution{quote}")
    quoted_lru = re.escape(f"{quote}Long_Range_Understanding{quote}")
    return re.sub(
        rf"({quoted_conflict})(\s*\n\s*)({quoted_lru})",
        r"\1,\2\3",
        body,
    )


def _yaml_bool(value: bool) -> str:
    return "true" if value else "false"


__all__ = [
    "MemoryAgentBenchCapabilitySpec",
    "ensure_memoryagentbench_matrix_support",
    "memoryagentbench_capability_specs",
]
