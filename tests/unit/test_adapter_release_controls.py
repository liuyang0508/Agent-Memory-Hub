import stat
from datetime import datetime, timezone
from pathlib import Path


def test_release_control_requires_ordered_promotion(tmp_path: Path) -> None:
    from agent_brain.agent_integrations.release_controls import set_adapter_release

    shadow = set_adapter_release(tmp_path, "codex", "shadow")
    invalid = set_adapter_release(tmp_path, "codex", "default")
    canary = set_adapter_release(tmp_path, "codex", "canary", cohort_percent=25)
    default = set_adapter_release(tmp_path, "codex", "default")

    assert shadow.status == "passed"
    assert shadow.control.stage == "shadow"
    assert invalid.status == "blocked"
    assert invalid.reason_code == "INVALID_PROMOTION"
    assert canary.status == "passed"
    assert canary.control.cohort_percent == 25
    assert default.status == "passed"
    assert default.control.stage == "default"
    assert default.control.cohort_percent == 100


def test_disabled_adapter_must_return_to_shadow(tmp_path: Path) -> None:
    from agent_brain.agent_integrations.release_controls import set_adapter_release

    disabled = set_adapter_release(tmp_path, "qoder", "disabled", reason="recall regression")
    direct_default = set_adapter_release(tmp_path, "qoder", "default")
    recovered = set_adapter_release(tmp_path, "qoder", "shadow", reason="fixed candidate")

    assert disabled.status == "passed"
    assert direct_default.status == "blocked"
    assert recovered.status == "passed"
    assert recovered.control.stage == "shadow"


def test_canary_decision_is_deterministic_and_bounded(tmp_path: Path) -> None:
    from agent_brain.agent_integrations.release_controls import (
        adapter_release_decision,
        set_adapter_release,
    )

    set_adapter_release(tmp_path, "codex", "shadow")
    set_adapter_release(tmp_path, "codex", "canary", cohort_percent=10)

    first = adapter_release_decision(tmp_path, "codex", session_id="stable-session")
    second = adapter_release_decision(tmp_path, "codex", session_id="stable-session")
    decisions = {
        adapter_release_decision(tmp_path, "codex", session_id=f"session-{index}").decision
        for index in range(200)
    }

    assert first == second
    assert first.bucket is not None and 0 <= first.bucket < 100
    assert decisions == {"enabled", "canary_excluded"}


def test_missing_control_is_backward_compatible_and_file_is_private(tmp_path: Path) -> None:
    from agent_brain.agent_integrations.release_controls import (
        adapter_release_decision,
        release_controls_path,
        set_adapter_release,
    )

    implicit = adapter_release_decision(tmp_path, "claude_code", session_id="legacy")
    set_adapter_release(tmp_path, "claude_code", "shadow")
    path = release_controls_path(tmp_path)

    assert implicit.decision == "enabled"
    assert implicit.control is None
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_disabled_release_control_blocks_verified_projection(tmp_path: Path) -> None:
    from agent_brain.agent_integrations.capabilities import capability_for_adapter
    from agent_brain.agent_integrations.registry import get_adapter
    from agent_brain.agent_integrations.release_controls import set_adapter_release
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.agent_integrations.verifications import record_adapter_verification
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort

    now = datetime.now(timezone.utc)
    record_runtime_event(tmp_path, adapter="codex", event_name="UserPromptSubmit", now=now)
    record_injection_cohort(
        tmp_path,
        adapter="codex",
        item_ids=["mem-disabled-projection"],
        now=now,
    )
    record_adapter_verification(
        tmp_path,
        adapter="codex",
        status="passed",
        verifier="pytest",
        evidence=["memory adapter doctor codex --format json"],
        now=now,
    )
    set_adapter_release(tmp_path, "codex", "disabled", reason="regression", now=now)

    capability = capability_for_adapter("codex", get_adapter("codex", tmp_path), now=now)

    assert capability.verified is False
    assert capability.release_control is not None
    assert capability.release_control["stage"] == "disabled"
    assert "adapter disabled by release control" in capability.verification_blockers


def test_disabled_release_control_blocks_mutating_lifecycle_action(tmp_path: Path) -> None:
    from agent_brain.agent_integrations.release_controls import set_adapter_release
    from agent_brain.product.adapter_onboarding import execute_adapter_action

    set_adapter_release(tmp_path, "codex", "disabled", reason="regression")

    result = execute_adapter_action(tmp_path, "codex", "install", verifier="pytest")

    assert result.status == "blocked"
    assert result.reason_code == "ADAPTER_DISABLED"
    assert result.provenance is not None
