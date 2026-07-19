from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).parents[2]


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _expected():
    from agent_brain.evaluation.hook_recall_evidence import (
        HookRecallExpectedProvenance,
    )

    return HookRecallExpectedProvenance(
        git_commit="a" * 40,
        hook_sha256=_digest("b"),
        implementation_sha256=_digest("c"),
        corpus_sha256=_digest("d"),
        corpus_version="production-replay-v2",
        config_sha256=_digest("e"),
    )


def _valid_manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": "12345678-1234-4234-8234-123456789abc",
        "started_at": "2026-07-19T07:00:00+00:00",
        "completed_at": "2026-07-19T07:00:01+00:00",
        "status": "pass",
        "provenance": {
            "git_commit": "a" * 40,
            "dirty": False,
            "hook_sha256": _digest("b"),
            "implementation_sha256": _digest("c"),
            "corpus_sha256": _digest("d"),
            "corpus_version": "production-replay-v2",
            "config_sha256": _digest("e"),
            "adapter": "codex",
            "timeout_seconds": 8.0,
        },
        "counts": {
            "planned": 3,
            "applicable": 2,
            "not_applicable": 1,
            "executed": 2,
        },
        "planned_case_ids": ["case-injected", "case-empty", "case-project"],
        "results": [
            {
                "case_id": "case-injected",
                "applicable": True,
                "expected_status": "injected",
                "actual_status": "injected",
                "expected_item_ids": ["mem-a"],
                "observed_item_ids": ["mem-a"],
                "prohibited_item_ids": [],
                "cohort_item_ids": ["mem-a"],
                "protocol_valid": True,
                "cohort_consistent": True,
                "gap_consistent": True,
                "exit_code": 0,
                "duration_ms": 120.0,
                "reason": "included",
            },
            {
                "case_id": "case-empty",
                "applicable": True,
                "expected_status": "empty",
                "actual_status": "empty",
                "expected_item_ids": [],
                "observed_item_ids": [],
                "prohibited_item_ids": [],
                "cohort_item_ids": [],
                "protocol_valid": True,
                "cohort_consistent": True,
                "gap_consistent": True,
                "exit_code": 0,
                "duration_ms": 80.0,
                "reason": "query_not_injectable",
            },
            {
                "case_id": "case-project",
                "applicable": False,
                "expected_status": None,
                "actual_status": "not_applicable",
                "expected_item_ids": [],
                "observed_item_ids": [],
                "prohibited_item_ids": [],
                "cohort_item_ids": [],
                "protocol_valid": True,
                "cohort_consistent": True,
                "gap_consistent": True,
                "exit_code": None,
                "duration_ms": 0.0,
                "reason": "explicit_project_scope_unavailable",
            },
        ],
        "failed_gates": [],
    }


def test_valid_manifest_has_no_gate_failures() -> None:
    from agent_brain.evaluation.hook_recall_evidence import (
        validate_hook_recall_manifest,
    )

    assert validate_hook_recall_manifest(_valid_manifest(), expected=_expected()) == []


def test_manifest_rejects_false_pass_with_missing_case() -> None:
    from agent_brain.evaluation.hook_recall_evidence import (
        validate_hook_recall_manifest,
    )

    manifest = _valid_manifest()
    manifest["results"] = list(manifest["results"])[:-1]  # type: ignore[arg-type]

    failures = validate_hook_recall_manifest(manifest, expected=_expected())

    assert "G0:planned_result_mismatch" in failures
    assert "G0:false_pass_status" in failures


def test_manifest_rejects_stdout_cohort_divergence() -> None:
    from agent_brain.evaluation.hook_recall_evidence import (
        validate_hook_recall_manifest,
    )

    manifest = _valid_manifest()
    results = manifest["results"]
    assert isinstance(results, list) and isinstance(results[0], dict)
    results[0]["cohort_item_ids"] = ["mem-other"]

    failures = validate_hook_recall_manifest(manifest, expected=_expected())

    assert "G1:stdout_cohort_mismatch:case-injected" in failures


def test_manifest_rejects_missing_expected_and_prohibited_injection() -> None:
    from agent_brain.evaluation.hook_recall_evidence import (
        validate_hook_recall_manifest,
    )

    manifest = _valid_manifest()
    results = manifest["results"]
    assert isinstance(results, list) and isinstance(results[0], dict)
    results[0]["observed_item_ids"] = ["mem-prohibited"]
    results[0]["cohort_item_ids"] = ["mem-prohibited"]
    results[0]["prohibited_item_ids"] = ["mem-prohibited"]

    failures = validate_hook_recall_manifest(manifest, expected=_expected())

    assert "G1:prohibited_injection:case-injected" in failures
    assert "G2:missing_expected_items:case-injected" in failures


def test_manifest_rejects_stale_provenance() -> None:
    from agent_brain.evaluation.hook_recall_evidence import (
        validate_hook_recall_manifest,
    )

    manifest = _valid_manifest()
    provenance = manifest["provenance"]
    assert isinstance(provenance, dict)
    provenance["hook_sha256"] = _digest("f")

    failures = validate_hook_recall_manifest(manifest, expected=_expected())

    assert "G0:hook_sha256_mismatch" in failures


def test_atomic_manifest_writer_does_not_add_sensitive_fields(tmp_path: Path) -> None:
    from agent_brain.evaluation.hook_recall_evidence import (
        load_hook_recall_manifest,
        write_manifest_atomic,
    )

    path = tmp_path / "manifest.json"
    manifest = _valid_manifest()

    write_manifest_atomic(path, manifest)

    assert load_hook_recall_manifest(path) == manifest
    serialized = path.read_text(encoding="utf-8")
    for forbidden in (
        "raw_prompt",
        "stdout",
        "stderr",
        "session_id",
        "memory body",
        "SECRET_TOKEN_SENTINEL",
    ):
        assert forbidden not in serialized
    assert json.loads(serialized)["status"] == "pass"


def test_verifier_cli_rejects_partial_manifest(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    manifest = _valid_manifest()
    manifest["results"] = list(manifest["results"])[:-1]  # type: ignore[arg-type]
    path.write_text(json.dumps(manifest), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/check-hook-recall-evidence.py",
            str(path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "G0:planned_result_mismatch" in completed.stdout


def test_runner_cli_writes_terminal_manifest_when_hook_times_out(
    tmp_path: Path,
) -> None:
    hook = tmp_path / "timeout-hook.sh"
    hook.write_text("#!/usr/bin/env bash\nsleep 1\nprintf '{}\\n'\n", encoding="utf-8")
    hook.chmod(0o700)
    output = tmp_path / "evidence.json"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run-hook-recall-evidence.py",
            "--hook",
            str(hook),
            "--timeout-seconds",
            "0.05",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "fail"
    assert payload["counts"]["executed"] == 11
    assert any(failure.startswith("G3:hook_timeout:") for failure in payload["failed_gates"])
