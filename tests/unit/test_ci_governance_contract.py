from pathlib import Path
import copy
import re

import pytest
import yaml


CHECKOUT_SHA = "11bd71901bbe5b1630ceea73d27597364c9af683"
SETUP_PYTHON_SHA = "a26af69be951a213d495a4c3e4e4022e16d87065"
UPLOAD_ARTIFACT_SHA = "ea165f8d65b6e75b540449e92b4886f43607fa02"


def _assert_all_official_actions_are_pinned(workflow: dict[str, object]) -> None:
    for configured_job in workflow["jobs"].values():
        for step in configured_job["steps"]:
            uses = str(step.get("uses", ""))
            if uses.startswith("actions/"):
                assert re.fullmatch(r"actions/[^@]+@[0-9a-f]{40}", uses)


def _assert_lifecycle_job_fail_closed(job: dict[str, object]) -> None:
    def visit(value: object) -> None:
        if isinstance(value, dict):
            assert "continue-on-error" not in value
            condition = str(value.get("if", ""))
            assert "always()" not in condition.replace(" ", "").lower()
            command = str(value.get("run", ""))
            assert "||" not in command
            assert not re.search(r"(?<!\|)\|(?!\|)", command)
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
    assert f"actions/upload-artifact@{UPLOAD_ARTIFACT_SHA}" in workflow_text
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


def test_lifecycle_governance_workflow_contract_is_exact_and_least_privilege() -> None:
    workflow = yaml.safe_load(
        Path(".github/workflows/governance-gates.yml").read_text(encoding="utf-8")
    )
    trigger = workflow.get("on", workflow.get(True))
    assert trigger == {
        "push": {"branches": ["main"]},
        "pull_request": {"branches": ["main"]},
    }
    assert workflow["permissions"] == {"contents": "read"}

    job = workflow["jobs"]["lifecycle-governance"]
    assert set(job) == {"runs-on", "steps"}
    assert job["runs-on"] == "ubuntu-latest"
    assert len(job["steps"]) == 5
    checkout, setup, install, contracts, evidence = job["steps"]
    assert checkout == {"uses": f"actions/checkout@{CHECKOUT_SHA}"}
    assert setup == {
        "uses": f"actions/setup-python@{SETUP_PYTHON_SHA}",
        "with": {"python-version": "3.12"},
    }
    assert install == {
        "name": "Install test runtime",
        "run": 'pip install -e ".[dev]"',
    }
    assert contracts == {
        "name": "Verify trusted lifecycle and pending contracts",
        "env": {"MEMORY_HUB_TEST_EMBEDDING": "1"},
        "run": (
            "python -m pytest "
            "tests/unit/test_supersession.py "
            "tests/unit/test_lifecycle_candidates.py "
            "tests/unit/test_pending_queue.py "
            "tests/unit/test_governance_readiness.py -q"
        ),
    }
    assert evidence == {
        "name": "Verify committed lifecycle governance evidence",
        "run": "python scripts/generate-lifecycle-governance-report.py --check",
    }
    _assert_all_official_actions_are_pinned(workflow)


def test_governance_workflow_rejects_mutable_upload_artifact_tag() -> None:
    workflow = yaml.safe_load(
        Path(".github/workflows/governance-gates.yml").read_text(encoding="utf-8")
    )
    mutated = copy.deepcopy(workflow)
    upload = next(
        step
        for step in mutated["jobs"]["recall-quality"]["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    upload["uses"] = "actions/upload-artifact@v4"

    with pytest.raises(AssertionError):
        _assert_all_official_actions_are_pinned(mutated)


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
        ("step", "run", "python check.py | tee lifecycle.log"),
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
