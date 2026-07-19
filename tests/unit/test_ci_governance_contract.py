from pathlib import Path

import yaml


def test_core_ci_does_not_silence_type_failures() -> None:
    workflow = Path(".github/workflows/python-tests.yml").read_text(encoding="utf-8")

    assert "continue-on-error: true" not in workflow
    assert "check_mypy_baseline.py" in workflow
    assert "Strict type check for governance-critical modules" in workflow


def test_governance_workflow_has_stable_required_job_names() -> None:
    workflow = yaml.safe_load(
        Path(".github/workflows/governance-gates.yml").read_text(encoding="utf-8")
    )

    assert set(workflow["jobs"]) == {
        "security",
        "benchmark-integrity",
        "docker-smoke",
        "recall-quality",
        "adapter-governance",
    }


def test_recall_quality_job_is_fail_closed_and_replays_fresh_evidence() -> None:
    workflow_text = Path(".github/workflows/governance-gates.yml").read_text(
        encoding="utf-8"
    )
    workflow = yaml.safe_load(workflow_text)
    job = workflow["jobs"]["recall-quality"]
    commands = "\n".join(
        str(step.get("run", "")) for step in job["steps"] if isinstance(step, dict)
    )

    assert "continue-on-error" not in job
    assert "tests/unit/test_recall_quality_corpus.py" in commands
    assert "tests/system/test_recall_quality_replay.py" in commands
    assert "tests/system/test_dual_route_recall_matrix.py" in commands
    assert "scripts/run-hook-recall-evidence.py" in commands
    assert "scripts/check-hook-recall-evidence.py" in commands
    assert "--require-clean" in commands
    assert "./scripts/check-recall-quality.py" in commands
    assert "--write" not in commands
    assert "actions/upload-artifact@v4" in workflow_text
    assert "hook-recall-evidence" in workflow_text


def test_adapter_governance_job_is_fail_closed_and_checks_committed_evidence() -> None:
    workflow_text = Path(".github/workflows/governance-gates.yml").read_text(
        encoding="utf-8"
    )
    workflow = yaml.safe_load(workflow_text)
    job = workflow["jobs"]["adapter-governance"]
    commands = "\n".join(
        str(step.get("run", "")) for step in job["steps"] if isinstance(step, dict)
    )

    assert "continue-on-error" not in job
    assert "tests/unit/test_adapter_manifests.py" in commands
    assert "tests/unit/test_adapter_lifecycle_records.py" in commands
    assert "tests/unit/test_adapter_release_controls.py" in commands
    assert "tests/unit/test_adapter_governance_report.py" in commands
    assert "tests/system/test_adapter_lifecycle_contract.py" in commands
    assert "tests/system/test_adapter_core_isolation.py" in commands
    assert "./scripts/generate-adapter-governance.py --check" in commands


def test_distribution_workflows_explain_missing_secrets_without_becoming_core_gates() -> None:
    gitee = Path(".github/workflows/sync-gitee.yml").read_text(encoding="utf-8")
    npm = Path(".github/workflows/publish-npm.yml").read_text(encoding="utf-8")
    site = Path(".github/workflows/deploy-official-site.yml").read_text(encoding="utf-8")
    core = Path(".github/workflows/python-tests.yml").read_text(encoding="utf-8")
    governance = Path(".github/workflows/governance-gates.yml").read_text(encoding="utf-8")

    for workflow in (gitee, npm, site):
        assert "reason=missing_secret" in workflow
    for distribution_job in ("mirror", "publish", "deploy"):
        assert distribution_job not in core
        assert distribution_job not in governance


def test_mypy_baseline_is_checked_in_and_never_rewritten_by_ci() -> None:
    checker = Path("scripts/check_mypy_baseline.py").read_text(encoding="utf-8")
    baseline = Path(".github/mypy-baseline.txt")
    workflow = Path(".github/workflows/python-tests.yml").read_text(encoding="utf-8")

    assert baseline.is_file()
    assert "--write-baseline" in checker
    assert "--write-baseline" not in workflow
