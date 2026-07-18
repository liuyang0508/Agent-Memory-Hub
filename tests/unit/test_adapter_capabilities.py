from pathlib import Path

from agent_brain.agent_integrations import WIPAdapter
from agent_brain.agent_integrations.registry import get_adapter


BRAIN_DIR = Path("/tmp/test_brain")


def test_capability_for_ready_adapter_reports_install_ready():
    from agent_brain.agent_integrations.capabilities import capability_for_adapter

    adapter = get_adapter("codex", BRAIN_DIR)
    cap = capability_for_adapter("codex", adapter)

    assert cap.name == "codex"
    assert cap.support_level == "install-ready"
    assert cap.status == "ready"
    assert "mcp" in cap.integration_modes or "file" in cap.integration_modes
    assert cap.limitations
    assert cap.runtime_observed is False
    assert cap.runtime_event_count == 0
    assert cap.last_runtime_event is None
    assert cap.verification_status == "not_verified"
    assert "evidence level is install-ready, not verified" in cap.verification_blockers
    assert "runtime event not observed" in cap.verification_blockers


def test_codex_capability_reports_three_layer_install():
    from agent_brain.agent_integrations.capabilities import capability_for_adapter

    cap = capability_for_adapter("codex", get_adapter("codex", BRAIN_DIR))

    assert cap.support_level == "install-ready"
    assert set(cap.integration_modes) >= {"file", "hook", "mcp"}
    assert cap.limitations == [
        "real Codex config doctor passed; hook runtime event not yet observed"
    ]
    assert cap.evidence_paths == [
        "tests/unit/test_adapters.py",
        "tests/unit/test_cli_adapter.py",
        "agent_brain/agent_integrations/codex.py",
        "agent_brain/agent_integrations/codex_hooks.py",
        "agent_brain/agent_integrations/codex_diagnostics.py",
    ]
    assert cap.evidence_level == "install-ready"
    assert cap.verification_status == "not_verified"
    assert cap.verified is False
    assert cap.memory_boundary["amh_role"] == "shared_truth_source"
    assert cap.memory_boundary["native_memory_role"] == "candidate_hint"
    assert cap.memory_boundary["native_memory_state"] == "documented"
    assert cap.memory_boundary["explored_trace_role"] == "session_trace_only"
    assert cap.memory_boundary["priority_order"] == [
        "current_user_message",
        "live_repository_evidence",
        "current_project_instructions",
        "amh_memory_item",
        "agent_native_memory",
        "explored_trace",
    ]
    assert cap.memory_boundary["evidence_layers"] == [
        "awareness",
        "tool",
        "automatic_hook",
        "fallback",
    ]
    assert cap.memory_boundary["native_memory_observed"] is False
    assert cap.memory_boundary["last_injection"] == {"observed": False}


def test_capability_memory_boundary_reports_latest_injection_observation(tmp_path):
    from agent_brain.agent_integrations.capabilities import capability_for_adapter
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort

    record_injection_cohort(
        tmp_path,
        item_ids=["mem-boundary-runtime"],
        adapter="codex",
        session_id="sess-boundary-runtime",
        cwd="/repo/runtime",
        query="memory boundary runtime",
        pack_metrics={
            "packed_tokens": 34,
            "full_tokens": 233,
            "items": [{"id": "mem-boundary-runtime", "selected_view": "locator"}],
        },
    )

    cap = capability_for_adapter("codex", get_adapter("codex", tmp_path))
    last_injection = cap.memory_boundary["last_injection"]

    assert cap.memory_boundary["native_memory_observed"] is False
    assert last_injection["observed"] is True
    assert last_injection["cohort_id"].startswith("inj-")
    assert last_injection["session_id"] == "sess-boundary-runtime"
    assert last_injection["cwd"] == "/repo/runtime"
    assert last_injection["item_count"] == 1
    assert last_injection["packed_tokens"] == 34
    assert last_injection["full_tokens"] == 233


def test_doctor_memory_boundary_marks_native_bridge_check_as_observed(tmp_path):
    from agent_brain.agent_integrations.diagnostics import (
        AdapterDiagnosticCheck,
        AdapterDiagnosticReport,
    )

    report = AdapterDiagnosticReport(
        adapter="qoder",
        overall_status="ok",
        checks=[
            AdapterDiagnosticCheck(
                name="Qoder native memory bridge",
                status="ok",
                detail="AMH native memory bridge present",
            )
        ],
        brain_dir=tmp_path,
    )

    boundary = report.to_dict()["memory_boundary"]

    assert boundary["native_memory_observed"] is True


def test_adapter_doctor_reports_wire_brain_dir_for_memory_boundary():
    repo = Path(__file__).resolve().parents[2]
    adapter_files = [
        "aider.py",
        "aone_copilot.py",
        "claude_code.py",
        "cline.py",
        "continue_dev.py",
        "cursor.py",
        "github_copilot.py",
        "hermes_agent.py",
        "openclaw.py",
        "openhuman.py",
        "opensquilla.py",
        "qoder.py",
        "qoder_work.py",
        "wukong.py",
    ]

    for filename in adapter_files:
        text = (repo / "agent_brain" / "agent_integrations" / filename).read_text(encoding="utf-8")
        assert "brain_dir=self.brain_dir" in text, filename

    codex_diagnostics = (
        repo / "agent_brain" / "agent_integrations" / "codex_diagnostics.py"
    ).read_text(encoding="utf-8")
    assert "brain_dir=brain_dir" in codex_diagnostics


def test_codex_evidence_is_registered_separately_from_capability_projection():
    from agent_brain.agent_integrations.evidence import evidence_for_adapter

    evidence = evidence_for_adapter("codex", is_wip=False)

    assert evidence.support_level == "install-ready"
    assert evidence.limitations == (
        "real Codex config doctor passed; hook runtime event not yet observed",
    )
    assert evidence.evidence_paths == (
        "tests/unit/test_adapters.py",
        "tests/unit/test_cli_adapter.py",
        "agent_brain/agent_integrations/codex.py",
        "agent_brain/agent_integrations/codex_hooks.py",
        "agent_brain/agent_integrations/codex_diagnostics.py",
    )
    assert evidence.evidence_level == "install-ready"


def test_claude_code_capability_reports_doctor_test_evidence():
    from agent_brain.agent_integrations.capabilities import capability_for_adapter

    cap = capability_for_adapter("claude_code", get_adapter("claude_code", BRAIN_DIR))

    assert cap.support_level == "install-ready"
    assert cap.status == "ready"
    assert cap.limitations == [
        "real Claude Code config doctor passed; runtime hook event not recorded"
    ]
    assert cap.evidence_paths == [
        "tests/unit/test_adapters.py",
        "tests/unit/test_cli_adapter.py",
        "agent_brain/agent_integrations/claude_code.py",
        "agent_brain/agent_integrations/claude_code_diagnostics.py",
    ]
    assert cap.evidence_level == "install-ready"


def test_cursor_capability_reports_doctor_with_local_config_limitation():
    from agent_brain.agent_integrations.capabilities import capability_for_adapter

    cap = capability_for_adapter("cursor", get_adapter("cursor", BRAIN_DIR))

    assert cap.support_level == "install-ready"
    assert cap.status == "ready"
    assert cap.limitations == [
        "config doctor implemented; local Cursor MCP config malformed; runtime not verified"
    ]
    assert cap.evidence_paths == [
        "tests/unit/test_adapters.py",
        "tests/unit/test_cli_adapter.py",
        "agent_brain/agent_integrations/cursor.py",
        "agent_brain/agent_integrations/mcp_config_diagnostics.py",
    ]
    assert cap.evidence_level == "install-ready"


def test_cline_capability_reports_doctor_with_missing_local_config_limitation():
    from agent_brain.agent_integrations.capabilities import capability_for_adapter

    cap = capability_for_adapter("cline", get_adapter("cline", BRAIN_DIR))

    assert cap.support_level == "install-ready"
    assert cap.status == "ready"
    assert cap.limitations == [
        "config doctor implemented; local Cline MCP config missing; runtime not verified"
    ]
    assert cap.evidence_paths == [
        "tests/unit/test_adapters.py",
        "tests/unit/test_cli_adapter.py",
        "agent_brain/agent_integrations/cline.py",
        "agent_brain/agent_integrations/mcp_config_diagnostics.py",
    ]
    assert cap.evidence_level == "install-ready"


def test_aider_capability_reports_doctor_with_missing_local_config_limitation():
    from agent_brain.agent_integrations.capabilities import capability_for_adapter

    cap = capability_for_adapter("aider", get_adapter("aider", BRAIN_DIR))

    assert cap.support_level == "install-ready"
    assert cap.status == "ready"
    assert cap.limitations == [
        "config doctor implemented; local Aider config missing; runtime not verified"
    ]
    assert cap.evidence_paths == [
        "tests/unit/test_adapters.py",
        "tests/unit/test_cli_adapter.py",
        "agent_brain/agent_integrations/aider.py",
        "agent_brain/agent_integrations/aider_diagnostics.py",
    ]
    assert cap.evidence_level == "install-ready"


def test_wukong_capability_reports_code_level_install_evidence():
    from agent_brain.agent_integrations.capabilities import capability_for_adapter

    cap = capability_for_adapter("wukong", get_adapter("wukong", BRAIN_DIR))

    assert cap.support_level == "install-ready"
    assert cap.status == "ready"
    assert cap.limitations == [
        "scoped Wukong MCP install is implemented; verified requires a local Wukong CLI runtime probe record"
    ]
    assert cap.evidence_paths == [
        "tests/unit/test_adapters.py",
        "tests/unit/test_adapter_robustness_p36.py",
        "tests/unit/test_cli_adapter.py",
        "agent_brain/agent_integrations/wukong.py",
        "<rewinddesktop-repo>/tauri-app/src-tauri/src/mcp/config.rs",
    ]
    assert cap.evidence_level == "install-ready"


def test_openclaw_capability_reports_cli_registry_install_evidence():
    from agent_brain.agent_integrations.capabilities import capability_for_adapter

    cap = capability_for_adapter("openclaw", get_adapter("openclaw", BRAIN_DIR))

    assert cap.support_level == "install-ready"
    assert cap.status == "ready"
    assert set(cap.integration_modes) >= {"command", "mcp"}
    assert cap.limitations == [
        "official OpenClaw MCP registry CLI path implemented; real runtime not verified"
    ]
    assert cap.evidence_paths == [
        "tests/unit/test_adapters.py",
        "https://docs.openclaw.ai/cli/mcp",
    ]
    assert cap.evidence_level == "install-ready"


def test_hermes_agent_capability_reports_mcp_config_install_evidence():
    from agent_brain.agent_integrations.capabilities import capability_for_adapter

    cap = capability_for_adapter("hermes_agent", get_adapter("hermes_agent", BRAIN_DIR))

    assert cap.support_level == "install-ready"
    assert cap.status == "ready"
    assert set(cap.integration_modes) >= {"mcp"}
    assert cap.limitations == [
        "Hermes MCP server config installed; Hermes provider listing/upstream runtime not verified"
    ]
    assert cap.evidence_paths == [
        "tests/unit/test_adapters.py",
        "agent_brain/agent_integrations/hermes/provider.py",
        "https://hermes-agent.nousresearch.com/docs/user-guide/features/tool-calling-mcp",
    ]
    assert cap.evidence_level == "install-ready"


def test_openhuman_capability_reports_agentmemory_backend_install_evidence():
    from agent_brain.agent_integrations.capabilities import capability_for_adapter

    cap = capability_for_adapter("openhuman", get_adapter("openhuman", BRAIN_DIR))

    assert cap.support_level == "install-ready"
    assert cap.status == "ready"
    assert cap.integration_modes == ["file"]
    assert cap.limitations == [
        "agentmemory backend config bridge implemented; real OpenHuman runtime not verified"
    ]
    assert cap.evidence_paths == [
        "tests/unit/test_adapters.py",
        "https://github.com/tinyhumansai/openhuman",
    ]
    assert cap.evidence_level == "install-ready"


def test_opensquilla_capability_reports_toml_mcp_install_evidence():
    from agent_brain.agent_integrations.capabilities import capability_for_adapter

    cap = capability_for_adapter("opensquilla", get_adapter("opensquilla", BRAIN_DIR))

    assert cap.support_level == "install-ready"
    assert cap.status == "ready"
    assert set(cap.integration_modes) >= {"file", "mcp"}
    assert cap.limitations == [
        "OpenSquilla config.toml MCP entry implemented; real runtime not verified"
    ]
    assert cap.evidence_paths == [
        "tests/unit/test_adapters.py",
        "https://github.com/opensquilla/opensquilla",
    ]
    assert cap.evidence_level == "install-ready"


def test_qoder_capability_reports_hooks_install_evidence():
    from agent_brain.agent_integrations.capabilities import capability_for_adapter

    cap = capability_for_adapter("qoder", get_adapter("qoder", BRAIN_DIR))

    assert cap.support_level == "install-ready"
    assert cap.status == "ready"
    assert set(cap.integration_modes) >= {"file", "hook", "mcp"}
    assert cap.limitations == [
        "official Qoder hooks, awareness path, and SharedClientCache MCP config implemented; AMH context effectiveness not verified"
    ]
    assert cap.evidence_paths == [
        "tests/unit/test_adapters.py",
        "tests/unit/test_cli_adapter.py",
        "agent_brain/agent_integrations/qoder.py",
        "agent_brain/agent_integrations/qoder_diagnostics.py",
    ]
    assert cap.evidence_level == "install-ready"


def test_qoder_work_capability_reports_workspace_hooks_install_evidence():
    from agent_brain.agent_integrations.capabilities import capability_for_adapter

    cap = capability_for_adapter("qoder_work", get_adapter("qoder_work", BRAIN_DIR))

    assert cap.support_level == "install-ready"
    assert cap.status == "ready"
    assert set(cap.integration_modes) >= {"file", "hook", "mcp"}
    assert cap.limitations == [
        "QoderWork hooks, awareness/main, and custom MCP config implemented; verified requires transcript-level AMH context evidence"
    ]
    assert cap.evidence_paths == [
        "tests/unit/test_adapters.py",
        "tests/unit/test_cli_adapter.py",
        "QoderWork built-in guide-mcp.md",
    ]
    assert cap.evidence_level == "install-ready"


def test_qoder_work_runtime_and_old_passed_record_do_not_count_as_verified(tmp_path):
    from agent_brain.agent_integrations.capabilities import capability_for_adapter
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.agent_integrations.verifications import record_adapter_verification

    record_runtime_event(
        tmp_path,
        adapter="qoder_work",
        event_name="UserPromptSubmit",
        session_id="qoderwork-runtime-only",
    )
    record_adapter_verification(
        tmp_path,
        adapter="qoder_work",
        status="passed",
        verifier="old-smoke",
        evidence=["memory adapter doctor qoder_work --format json", "runtime_events=1"],
        note="old smoke only observed hooks",
    )

    cap = capability_for_adapter("qoder_work", get_adapter("qoder_work", tmp_path))

    assert cap.runtime_observed is True
    assert cap.verified is False
    assert cap.support_level == "install-ready"
    assert "context effectiveness not observed" in cap.verification_blockers


def test_qoder_work_cli_context_effective_evidence_does_not_promote_gui_verified(tmp_path):
    from agent_brain.agent_integrations.capabilities import capability_for_adapter
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.agent_integrations.verifications import record_adapter_verification

    record_runtime_event(
        tmp_path,
        adapter="qoder_work",
        event_name="UserPromptSubmit",
        session_id="qoderwork-context-effective",
    )
    record_adapter_verification(
        tmp_path,
        adapter="qoder_work",
        status="passed",
        verifier="real-client",
        evidence=[
            "memory adapter doctor qoder_work --format json",
            "runtime_events=1",
            "context_effective=model_observed_agent_brain:qoderwork-cli-session",
        ],
        note="standalone qodercli saw AMH context",
    )

    cap = capability_for_adapter("qoder_work", get_adapter("qoder_work", tmp_path))

    assert cap.verified is False
    assert cap.support_level == "install-ready"
    assert "context effectiveness not observed" in cap.verification_blockers


def test_qoder_work_gui_context_effective_evidence_can_promote_verified(tmp_path):
    from agent_brain.agent_integrations.capabilities import capability_for_adapter
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.agent_integrations.verifications import record_adapter_verification
    from agent_brain.memory.context.injection_cohorts import record_injection_cohort

    record_runtime_event(
        tmp_path,
        adapter="qoder_work",
        event_name="UserPromptSubmit",
        session_id="qoderwork-gui-context-effective",
    )
    record_injection_cohort(
        tmp_path,
        adapter="qoder_work",
        session_id="qoderwork-gui-context-effective",
        item_ids=["mem-qoderwork-gui-context-effective"],
    )
    record_adapter_verification(
        tmp_path,
        adapter="qoder_work",
        status="passed",
        verifier="real-client",
        evidence=[
            "memory adapter doctor qoder_work --format json",
            "runtime_events=1",
            "context_effective=qoderwork_gui_agent_brain:qoderwork-gui-context-effective",
        ],
        note="QoderWork GUI session saw AMH context",
    )

    cap = capability_for_adapter("qoder_work", get_adapter("qoder_work", tmp_path))

    assert cap.verified is True
    assert cap.support_level == "verified"
    assert cap.verification_blockers == []


def test_continue_capability_reports_mcp_install_evidence():
    from agent_brain.agent_integrations.capabilities import capability_for_adapter

    cap = capability_for_adapter("continue_dev", get_adapter("continue_dev", BRAIN_DIR))

    assert cap.support_level == "install-ready"
    assert cap.status == "ready"
    assert cap.integration_modes == ["mcp"]
    assert cap.limitations == [
        "official Continue global config.yaml MCP path implemented; real runtime not verified"
    ]
    assert cap.evidence_paths == [
        "tests/unit/test_adapters.py",
        "tests/unit/test_cli_adapter.py",
        "agent_brain/agent_integrations/continue_dev.py",
    ]
    assert cap.evidence_level == "install-ready"


def test_github_copilot_capability_reports_repository_instructions_evidence():
    from agent_brain.agent_integrations.capabilities import capability_for_adapter

    cap = capability_for_adapter("github_copilot", get_adapter("github_copilot", BRAIN_DIR))

    assert cap.support_level == "install-ready"
    assert cap.status == "ready"
    assert cap.integration_modes == ["file"]
    assert cap.limitations == [
        "repository-level copilot-instructions.md installer implemented; real Copilot runtime not verified"
    ]
    assert cap.evidence_paths == [
        "tests/unit/test_adapters.py",
        "tests/unit/test_cli_adapter.py",
        "agent_brain/agent_integrations/github_copilot.py",
    ]
    assert cap.evidence_level == "install-ready"


def test_aone_copilot_capability_reports_intellij_plugin_sidecar():
    from agent_brain.agent_integrations.capabilities import capability_for_adapter

    cap = capability_for_adapter("aone_copilot", get_adapter("aone_copilot", BRAIN_DIR))

    assert cap.support_level == "install-ready"
    assert cap.status == "ready"
    assert cap.integration_modes == ["file"]
    assert cap.limitations == [
        "IntelliJ IDEA Aone Copilot plugin sidecar implemented; plugin runtime/tool bridge not verified"
    ]
    assert cap.evidence_paths == [
        "tests/unit/test_adapters.py",
        "/Applications/IntelliJ IDEA Ultimate.app",
    ]
    assert cap.evidence_level == "install-ready"


def test_capability_for_wip_adapter_reports_wip():
    from agent_brain.agent_integrations.capabilities import capability_for_adapter

    adapter = get_adapter("mulerun", BRAIN_DIR)
    assert isinstance(adapter, WIPAdapter)

    cap = capability_for_adapter("mulerun", adapter)

    assert cap.name == "mulerun"
    assert cap.support_level == "wip"
    assert cap.status == "wip"
    assert cap.limitations == ["install path not implemented"]
    assert cap.evidence_paths == []
    assert cap.evidence_level is None
    assert cap.verification_status == "not_verified"
    assert cap.verified is False
    assert "install path not implemented" in cap.verification_blockers


def test_runtime_observation_does_not_make_install_ready_adapter_verified(tmp_path):
    from agent_brain.agent_integrations.capabilities import capability_for_adapter
    from agent_brain.agent_integrations.runtime_events import record_runtime_event

    record_runtime_event(
        tmp_path,
        adapter="codex",
        event_name="UserPromptSubmit",
        session_id="sess-runtime",
    )

    cap = capability_for_adapter("codex", get_adapter("codex", tmp_path))

    assert cap.runtime_observed is True
    assert cap.support_level == "install-ready"
    assert cap.verification_status == "not_verified"
    assert cap.verified is False
    assert cap.verification_blockers == [
        "evidence level is install-ready, not verified",
        "context injection not observed",
    ]
