from pathlib import Path
import copy
import re

import pytest
import yaml


def _assert_lifecycle_job_fail_closed(job: dict[str, object]) -> None:
    def visit(value: object) -> None:
        if isinstance(value, dict):
            assert "continue-on-error" not in value
            condition = str(value.get("if", ""))
            assert "always()" not in condition.replace(" ", "").lower()
            command = str(value.get("run", ""))
            assert "||" not in command
            assert not re.search(r"(?m)^\s*set\s+\+e(?:\s|$)", command)
            assert not re.search(
                r"(?m)(?:^|;)\s*(?:true|:|exit\s+0)\s*(?:;|$)", command
            )
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(job)


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
        "lifecycle-governance",
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
    assert "tests/unit/test_runtime_authority.py" in commands
    assert "tests/unit/test_hook_config.py" in commands
    assert "tests/unit/test_qoder_config_convergence.py" in commands
    assert "tests/system/test_adapter_lifecycle_contract.py" in commands
    assert "tests/system/test_adapter_core_isolation.py" in commands
    assert "./scripts/generate-adapter-governance.py --check" in commands


def test_lifecycle_governance_job_is_fail_closed_and_checks_committed_evidence() -> None:
    workflow_text = Path(".github/workflows/governance-gates.yml").read_text(
        encoding="utf-8"
    )
    workflow = yaml.safe_load(workflow_text)
    job = workflow["jobs"]["lifecycle-governance"]
    commands = "\n".join(
        str(step.get("run", "")) for step in job["steps"] if isinstance(step, dict)
    )

    _assert_lifecycle_job_fail_closed(job)
    assert "tests/unit/test_supersession.py" in commands
    assert "tests/unit/test_lifecycle_candidates.py" in commands
    assert "tests/unit/test_pending_queue.py" in commands
    assert "tests/unit/test_governance_readiness.py" in commands
    assert "python scripts/generate-lifecycle-governance-report.py --check" in commands


@pytest.mark.parametrize(
    ("scope", "field", "value"),
    [
        ("job", "continue-on-error", True),
        ("job", "continue-on-error", "${{ matrix.advisory }}"),
        ("step", "continue-on-error", False),
        ("step", "continue-on-error", True),
        ("step", "continue-on-error", "${{ matrix.advisory }}"),
        ("step", "run", "python -m pytest tests/unit/test_supersession.py -q || true"),
        ("step", "run", "python check.py || echo ignored"),
        ("step", "run", "set +e\npython -m pytest tests/unit/test_supersession.py -q"),
        ("step", "run", "python check.py; exit 0"),
        ("step", "if", "${{ always() }}"),
    ],
)
def test_lifecycle_governance_contract_rejects_advisory_job_or_step_mutations(
    scope: str,
    field: str,
    value: object,
) -> None:
    workflow = yaml.safe_load(
        Path(".github/workflows/governance-gates.yml").read_text(encoding="utf-8")
    )
    job = copy.deepcopy(workflow["jobs"]["lifecycle-governance"])
    target = job if scope == "job" else job["steps"][-1]
    target[field] = value

    with pytest.raises(AssertionError):
        _assert_lifecycle_job_fail_closed(job)


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
