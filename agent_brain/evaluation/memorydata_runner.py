"""Runner utilities for invoking the upstream MemoryData benchmark."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_brain.evaluation.memorydata_amh import materialize_memorydata_amh_adapter
from agent_brain.evaluation.public_hygiene import public_path, redact_public_text


FAMILY_CONFIGS = {
    "MemoryAgentBench": "benchmark/memoryagentbench/Accurate_Retrieval/config/EventQA/Eventqa_full.yaml",
    "MemoryAgentBenchTTL": "benchmark/memoryagentbench/Test_Time_Learning/config/ICL/ICL_banking77.yaml",
    "MemoryAgentBenchLRU": "benchmark/memoryagentbench/Long_Range_Understanding/config/Detective_QA.yaml",
    "MemoryAgentBenchCR": "benchmark/memoryagentbench/Conflict_Resolution/config/Factconsolidation_mh_6k.yaml",
    "LoCoMo": "benchmark/locomo/config/Locomo_qa_4cat_600_dist.yaml",
    "LoCoMoCategory5": "benchmark/locomo/config/Locomo_qa_category5_adversarial.yaml",
    "LongBench": "benchmark/longbench/config/LongBench_rep150_proportional.yaml",
    "LongBenchV2Full": "benchmark/longbench/config/LongBench_v2_503_full.yaml",
    "MemBench": "benchmark/membench/config/MemBench_simple.yaml",
    "MemBenchNoisy": "benchmark/membench/config/MemBench_noisy.yaml",
    "MemBenchKnowledgeUpdate": "benchmark/membench/config/MemBench_knowledge_update.yaml",
    "MemBenchHighlevel": "benchmark/membench/config/MemBench_highlevel.yaml",
    "MemBenchRecMultiSession": "benchmark/membench/config/MemBench_RecMultiSession.yaml",
}


@dataclass(frozen=True)
class MemoryDataRunOptions:
    memorydata_repo: Path
    family: str = "MemoryAgentBench"
    agent_config: str = "config/reference_simple_rag_bm25.yaml"
    artifact_root: Path = Path("docs/evaluation/memorydata-artifacts")
    max_test_queries: int = 1
    query_start_index: int = 0
    timeout_s: int = 1800
    force: bool = False
    run_level: str = "smoke"
    env: dict[str, str] | None = None


def plan_memorydata_run(
    options: MemoryDataRunOptions,
    *,
    prereqs: dict[str, Any],
) -> dict[str, Any]:
    """Return a runnable or blocked MemoryData invocation plan."""

    command = memorydata_command(options, public=True)
    blockers: list[str] = []
    if not Path(options.memorydata_repo, "main.py").is_file():
        blockers.append("MemoryData launcher missing")
    if not prereqs.get("dependencies_ready"):
        blockers.append("missing dependencies: " + ", ".join(prereqs.get("missing_dependencies") or []))
    if not prereqs.get("datasets_ready"):
        blockers.append("missing datasets")
    if not prereqs.get("endpoint_ready"):
        blockers.append("model endpoint not ready")
    if blockers:
        return {
            "name": f"memorydata-{options.family.lower()}-{options.run_level}",
            "family": options.family,
            "run_level": options.run_level,
            "status": "blocked",
            "reason": "; ".join(blockers),
            "command": command,
            "artifact": "-",
        }
    return {
        "name": f"memorydata-{options.family.lower()}-{options.run_level}",
        "family": options.family,
        "run_level": options.run_level,
        "status": "planned",
        "reason": "ready to execute",
        "command": command,
            "artifact": public_path(options.artifact_root),
    }


def run_memorydata(options: MemoryDataRunOptions, *, prereqs: dict[str, Any]) -> dict[str, Any]:
    """Run MemoryData when prereqs are ready and persist a normalized run record."""

    artifact_root = _resolved_artifact_root(options.artifact_root)
    execution_command = memorydata_command(options)
    planned = plan_memorydata_run(options, prereqs=prereqs)
    if planned["status"] == "blocked":
        return planned
    if _uses_memoryagentbench_matrix_config(options.family):
        from agent_brain.evaluation.memoryagentbench_matrix import (
            ensure_memoryagentbench_matrix_support,
        )

        ensure_memoryagentbench_matrix_support(options.memorydata_repo)
    if _uses_amh_agent_config(options.agent_config):
        materialize_memorydata_amh_adapter(options.memorydata_repo)

    started_at = datetime.now(timezone.utc)
    try:
        result = subprocess.run(
            execution_command,
            cwd=options.memorydata_repo,
            env=dict(os.environ, **(options.env or {})),
            capture_output=True,
            text=True,
            timeout=options.timeout_s,
            check=False,
        )
        _redact_artifact_tree(artifact_root)
        failed_query_count = _memorydata_failed_query_count(artifact_root)
        status = "passed" if result.returncode == 0 and failed_query_count == 0 else "failed"
        payload = {
            **planned,
            "status": status,
            "returncode": result.returncode,
            "started_at": started_at.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "stdout_tail": redact_public_text(result.stdout[-4000:]),
            "stderr_tail": redact_public_text(result.stderr[-4000:]),
            "memorydata_failed_query_count": failed_query_count,
            "artifact_root": public_path(artifact_root),
        }
        if failed_query_count:
            payload["reason"] = f"MemoryData result contains {failed_query_count} failed query record(s)"
    except subprocess.TimeoutExpired as exc:
        _redact_artifact_tree(artifact_root)
        payload = {
            **planned,
            "status": "failed",
            "returncode": None,
            "started_at": started_at.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "stdout_tail": redact_public_text((exc.stdout or "")[-4000:]) if isinstance(exc.stdout, str) else "",
            "stderr_tail": redact_public_text((exc.stderr or "")[-4000:]) if isinstance(exc.stderr, str) else "",
            "reason": f"MemoryData run timed out after {options.timeout_s}s",
            "artifact_root": public_path(artifact_root),
        }

    raw_path = artifact_root / "run-record.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return {**payload, "run_record": public_path(raw_path)}


def memorydata_command(options: MemoryDataRunOptions, *, public: bool = False) -> list[str]:
    dataset_config = FAMILY_CONFIGS.get(options.family, options.family)
    artifact_root = _resolved_artifact_root(options.artifact_root)
    artifact_arg = public_path(artifact_root) if public else str(artifact_root)
    command = [
        "python",
        "main.py",
        "--agent_config",
        options.agent_config,
        "--dataset_config",
        dataset_config,
        "--max_test_queries_ablation",
        str(options.max_test_queries),
        "--artifact_root",
        artifact_arg,
    ]
    if options.force:
        command.append("--force")
    if options.query_start_index > 0:
        command.extend(["--query_start_index", str(options.query_start_index)])
    return command


def _resolved_artifact_root(artifact_root: Path) -> Path:
    return Path(artifact_root).expanduser().resolve()


def _redact_artifact_tree(artifact_root: Path) -> int:
    if not artifact_root.exists():
        return 0
    changed = 0
    for path in artifact_root.rglob("*"):
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data:
            continue
        text = data.decode("utf-8", errors="ignore")
        redacted = redact_public_text(text, root=artifact_root)
        if redacted == text:
            continue
        path.write_text(redacted, encoding="utf-8")
        changed += 1
    return changed


def _memorydata_failed_query_count(artifact_root: Path) -> int:
    failed_count = 0
    for result_path in Path(artifact_root).rglob("*_results.json"):
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows = payload.get("data")
        if not isinstance(rows, list):
            continue
        failed_count += sum(
            1 for row in rows if isinstance(row, dict) and row.get("status") == "failed"
        )
    return failed_count


def _uses_amh_agent_config(agent_config: str | Path) -> bool:
    normalized = str(agent_config).replace("\\", "/").lower()
    return Path(normalized).stem == "hybrid_amh"


def _uses_memoryagentbench_matrix_config(family: str) -> bool:
    return family in {
        "MemoryAgentBenchTTL",
        "MemoryAgentBenchLRU",
        "MemoryAgentBenchCR",
    }


__all__ = [
    "FAMILY_CONFIGS",
    "MemoryDataRunOptions",
    "memorydata_command",
    "materialize_memorydata_amh_adapter",
    "plan_memorydata_run",
    "run_memorydata",
]
