from datetime import datetime, timezone
from pathlib import Path

from agent_brain.agent_integrations.registry import get_adapter


def test_every_registered_adapter_has_complete_v1_manifest(tmp_path: Path) -> None:
    from agent_brain.agent_integrations.manifests import manifests_for_all

    manifests = manifests_for_all(tmp_path)

    assert len(manifests) == 16
    assert {item.schema_version for item in manifests} == {"amh-adapter-manifest/v1"}
    assert len({item.adapter_id for item in manifests}) == 16
    assert all(item.adapter_version for item in manifests)
    assert all(item.platforms for item in manifests)
    assert all(item.client_version_range for item in manifests)
    assert all(item.payload_schema for item in manifests)
    assert all(item.output_protocol for item in manifests)
    assert all(item.lifecycle.install for item in manifests)
    assert all(item.lifecycle.verify for item in manifests)
    assert all(item.lifecycle.doctor for item in manifests)
    assert all(item.lifecycle.repair for item in manifests)
    assert all(item.lifecycle.upgrade for item in manifests)
    assert all(item.lifecycle.uninstall for item in manifests)
    assert all(item.evidence.runtime_ttl_seconds > 0 for item in manifests)
    assert all(item.evidence.context_ttl_seconds > 0 for item in manifests)
    assert all(item.evidence.verification_ttl_seconds > 0 for item in manifests)
    assert all(item.feature_flag.startswith("adapter.") for item in manifests)
    assert all(item.degrade_mode for item in manifests)
    assert all(item.rollback_mode for item in manifests)


def test_hook_manifest_uses_adapter_declared_events(tmp_path: Path) -> None:
    from agent_brain.agent_integrations.manifests import manifest_for_adapter

    codex = get_adapter("codex", tmp_path)
    qoder = get_adapter("qoder", tmp_path)

    codex_manifest = manifest_for_adapter("codex", codex)
    qoder_manifest = manifest_for_adapter("qoder", qoder)

    assert codex_manifest.hook_events == tuple(codex.HOOK_EVENTS)
    assert qoder_manifest.hook_events == tuple(qoder.HOOK_EVENTS)
    assert codex_manifest.output_protocol == "codex-hook-json/v1"
    assert qoder_manifest.output_protocol == "qoder-hook-json/v1"
    assert set(codex_manifest.channels) == {"awareness", "cli", "hook", "mcp"}


def test_capability_projects_six_truth_states_from_observed_evidence(tmp_path: Path) -> None:
    from agent_brain.agent_integrations.capabilities import capability_for_adapter
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.agent_integrations.verifications import record_adapter_verification
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort

    now = datetime.now(timezone.utc)
    record_runtime_event(
        tmp_path,
        adapter="codex",
        event_name="UserPromptSubmit",
        session_id="stage3-manifest-runtime",
        now=now,
    )
    record_injection_cohort(
        tmp_path,
        adapter="codex",
        session_id="stage3-manifest-runtime",
        item_ids=["mem-stage3-manifest"],
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

    cap = capability_for_adapter("codex", get_adapter("codex", tmp_path), now=now)

    assert cap.manifest["schema_version"] == "amh-adapter-manifest/v1"
    assert cap.states == {
        "implemented": True,
        "installed": True,
        "configured": True,
        "doctor_passed": True,
        "runtime_observed": True,
        "context_injected": True,
    }
    assert cap.verified is True


def test_verification_record_without_context_injection_is_not_verified(tmp_path: Path) -> None:
    from agent_brain.agent_integrations.capabilities import capability_for_adapter
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.agent_integrations.verifications import record_adapter_verification

    now = datetime.now(timezone.utc)
    record_runtime_event(
        tmp_path,
        adapter="codex",
        event_name="UserPromptSubmit",
        session_id="stage3-no-injection",
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

    cap = capability_for_adapter("codex", get_adapter("codex", tmp_path), now=now)

    assert cap.states["context_injected"] is False
    assert cap.verified is False
    assert "context injection not observed" in cap.verification_blockers
