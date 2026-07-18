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
    }


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
