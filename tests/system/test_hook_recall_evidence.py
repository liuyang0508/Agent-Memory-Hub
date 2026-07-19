from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests/fixtures/recall_quality_production_replay_v1.json"


def test_real_hook_matches_all_applicable_production_replay_cases(
    tmp_path: Path,
) -> None:
    from agent_brain.evaluation.hook_recall_runner import (
        run_hook_recall_evidence,
    )

    manifest = run_hook_recall_evidence(
        root=ROOT,
        corpus_path=FIXTURE,
        hook_path=ROOT / "agent_runtime_kit/hooks/inject-context.sh",
        adapter="codex",
        timeout_seconds=8.0,
        workspace=tmp_path,
    )

    assert manifest["status"] == "pass", manifest["failed_gates"]
    assert manifest["counts"] == {
        "planned": 12,
        "applicable": 11,
        "not_applicable": 1,
        "executed": 11,
    }
    results = manifest["results"]
    assert isinstance(results, list)
    project = next(
        row
        for row in results
        if isinstance(row, dict) and row.get("case_id") == "prod-project-mismatch"
    )
    assert project["actual_status"] == "not_applicable"
    assert project["reason"] == "explicit_project_scope_unavailable"
    assert all("raw_prompt" not in row for row in results if isinstance(row, dict))
