from __future__ import annotations

import json
from pathlib import Path


def test_onboarding_summary_reports_truth_contract_counts(tmp_path: Path) -> None:
    from agent_brain.product.adapter_onboarding import build_onboarding_summary

    data = build_onboarding_summary(tmp_path)

    assert data["total"] == 16
    assert data["install_ready"] == 15
    assert data["wip"] == 1
    assert data["verified"] == 0
    assert data["adapters"][0]["name"] in {"codex", "claude_code"}
    assert data["adapters"][0]["next_action"] in {
        "doctor",
        "install",
        "verify",
        "wait-runtime",
    }


def test_adapter_doctor_returns_web_safe_payload(tmp_path: Path) -> None:
    from agent_brain.product.adapter_onboarding import doctor_adapter

    data = doctor_adapter(tmp_path, "codex")

    assert data["adapter"] == "codex"
    assert data["requested_adapter"] == "codex"
    assert data["overall_status"] in {"ok", "warn", "error"}
    assert isinstance(data["checks"], list)


def test_lifecycle_executor_returns_stable_unknown_adapter_contract(tmp_path: Path) -> None:
    from agent_brain.product.adapter_onboarding import execute_adapter_action

    result = execute_adapter_action(tmp_path, "does-not-exist", "install", verifier="pytest")
    data = result.to_dict()

    assert data["schema_version"] == "amh-adapter-lifecycle-result/v1"
    assert data["status"] == "blocked"
    assert data["reason_code"] == "UNKNOWN_ADAPTER"
    assert data["provenance"] is None
    assert data["repair_command"] == "memory adapter list"


def test_lifecycle_executor_records_wip_blocker(tmp_path: Path) -> None:
    from agent_brain.agent_integrations.lifecycle_records import iter_lifecycle_records
    from agent_brain.product.adapter_onboarding import execute_adapter_action

    result = execute_adapter_action(tmp_path, "mulerun", "install", verifier="pytest")

    assert result.status == "blocked"
    assert result.reason_code == "ADAPTER_WIP"
    assert result.provenance is not None
    records = list(iter_lifecycle_records(tmp_path, adapter="mulerun"))
    assert len(records) == 1
    assert records[0].status == "blocked"
    assert records[0].reason_code == "ADAPTER_WIP"


def test_adapter_verify_hooks_only_without_runtime_does_not_promote_verified(tmp_path: Path) -> None:
    from agent_brain.product.adapter_onboarding import verify_adapter

    data = verify_adapter(tmp_path, "qoder", verifier="pytest")

    assert data["adapter"] == "qoder"
    assert data["status"] == "failed"
    assert "runtime event not observed" in data["blockers"]


def test_adapter_verify_qoder_accepts_recent_amh_mcp_tool_trace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_brain.agent_integrations import qoder as qoder_mod
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.product import adapter_onboarding as onboarding
    from agent_brain.product.adapter_onboarding import verify_adapter

    transcript_root = tmp_path / ".qoder" / "projects" / "-tmp"
    monkeypatch.setattr(qoder_mod, "SETTINGS_PATH", tmp_path / ".qoder" / "settings.json")
    monkeypatch.setattr(qoder_mod, "AWARENESS_PATH", tmp_path / ".qoder" / "AGENTS.md")
    monkeypatch.setattr(
        qoder_mod,
        "MCP_CONFIG_PATH",
        tmp_path / "Library" / "Application Support" / "Qoder" / "SharedClientCache" / "mcp.json",
    )
    monkeypatch.setattr(
        qoder_mod,
        "MCP_EXTENSION_CONFIG_PATH",
        tmp_path
        / "Library"
        / "Application Support"
        / "Qoder"
        / "SharedClientCache"
        / "extension"
        / "local"
        / "mcp.json",
    )
    monkeypatch.setattr(qoder_mod, "QODER_PROJECTS_DIR", tmp_path / ".qoder" / "projects")
    monkeypatch.setattr(qoder_mod, "QODER_MEMORIES_DIR", tmp_path / ".qoder" / "memories", raising=False)
    monkeypatch.setattr(onboarding, "_context_transcript_roots", lambda adapter: [transcript_root])

    qoder_mod.QoderAdapter(brain_dir=tmp_path).install()
    record_runtime_event(
        tmp_path,
        adapter="qoder",
        event_name="UserPromptSubmit",
        session_id="qoder-amh-mcp",
    )
    transcript_root.mkdir(parents=True)
    (transcript_root / "qoder-amh-mcp.jsonl").write_text(
        json.dumps({
            "type": "assistant",
            "sessionId": "qoder-amh-mcp",
            "message": {
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "name": "mcp_agent-memory-hub_search_memory",
                    "input": {"query": "Alpha"},
                }],
            },
        }, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    data = verify_adapter(tmp_path, "qoder", verifier="pytest")

    assert data["adapter"] == "qoder"
    assert data["status"] == "passed"
    assert any("context_effective=amh_mcp_tool_use" in entry for entry in data["evidence"])


def test_adapter_verify_qoder_rejects_native_search_memory_trace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_brain.agent_integrations import qoder as qoder_mod
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.product import adapter_onboarding as onboarding
    from agent_brain.product.adapter_onboarding import verify_adapter

    transcript_root = tmp_path / ".qoder" / "projects" / "-tmp"
    monkeypatch.setattr(qoder_mod, "SETTINGS_PATH", tmp_path / ".qoder" / "settings.json")
    monkeypatch.setattr(qoder_mod, "AWARENESS_PATH", tmp_path / ".qoder" / "AGENTS.md")
    monkeypatch.setattr(
        qoder_mod,
        "MCP_CONFIG_PATH",
        tmp_path / "Library" / "Application Support" / "Qoder" / "SharedClientCache" / "mcp.json",
    )
    monkeypatch.setattr(
        qoder_mod,
        "MCP_EXTENSION_CONFIG_PATH",
        tmp_path
        / "Library"
        / "Application Support"
        / "Qoder"
        / "SharedClientCache"
        / "extension"
        / "local"
        / "mcp.json",
    )
    monkeypatch.setattr(qoder_mod, "QODER_PROJECTS_DIR", tmp_path / ".qoder" / "projects")
    monkeypatch.setattr(qoder_mod, "QODER_MEMORIES_DIR", tmp_path / ".qoder" / "memories", raising=False)
    monkeypatch.setattr(onboarding, "_context_transcript_roots", lambda adapter: [transcript_root])

    qoder_mod.QoderAdapter(brain_dir=tmp_path).install()
    record_runtime_event(
        tmp_path,
        adapter="qoder",
        event_name="UserPromptSubmit",
        session_id="qoder-native-memory",
    )
    transcript_root.mkdir(parents=True)
    (transcript_root / "qoder-native-memory.jsonl").write_text(
        json.dumps({
            "type": "assistant",
            "sessionId": "qoder-native-memory",
            "message": {
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "name": "SearchMemory",
                    "input": {"query": "用户个人信息"},
                }],
            },
        }, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    data = verify_adapter(tmp_path, "qoder", verifier="pytest")

    assert data["adapter"] == "qoder"
    assert data["status"] == "failed"
    assert "context effectiveness not observed" in data["blockers"]


def test_adapter_verify_qoder_work_requires_context_effectiveness(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_brain.agent_integrations import qoder_work as qw_mod
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.product.adapter_onboarding import verify_adapter

    monkeypatch.setattr(qw_mod, "SETTINGS_PATH", tmp_path / ".qoderwork" / "settings.json")
    monkeypatch.setattr(qw_mod, "MCP_CONFIG_PATH", tmp_path / ".qoderwork" / "mcp.json")
    monkeypatch.setattr(
        qw_mod,
        "AWARENESS_PATH",
        tmp_path / ".qoderwork" / "awareness" / "main" / "AGENTS.md",
    )
    qw_mod.QoderWorkAdapter(brain_dir=tmp_path).install()
    record_runtime_event(
        tmp_path,
        adapter="qoder_work",
        event_name="UserPromptSubmit",
        session_id="qoderwork-runtime-only",
    )

    data = verify_adapter(tmp_path, "qoder_work", verifier="pytest")

    assert data["adapter"] == "qoder_work"
    assert data["status"] == "failed"
    assert "context effectiveness not observed" in data["blockers"]
    assert data["record"]["status"] == "failed"


def test_adapter_verify_qoder_work_rejects_non_gui_model_observed_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_brain.agent_integrations import qoder_work as qw_mod
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.product import adapter_onboarding as onboarding
    from agent_brain.product.adapter_onboarding import verify_adapter

    monkeypatch.setattr(qw_mod, "SETTINGS_PATH", tmp_path / ".qoderwork" / "settings.json")
    monkeypatch.setattr(qw_mod, "MCP_CONFIG_PATH", tmp_path / ".qoderwork" / "mcp.json")
    monkeypatch.setattr(
        qw_mod,
        "AWARENESS_PATH",
        tmp_path / ".qoderwork" / "awareness" / "main" / "AGENTS.md",
    )
    projects_dir = tmp_path / ".qoderwork" / "projects" / "-Users-example-Desktop"
    monkeypatch.setattr(onboarding, "_context_transcript_roots", lambda adapter: [projects_dir])

    session_id = "qoderwork-cli-context-seen"
    qw_mod.QoderWorkAdapter(brain_dir=tmp_path).install()
    record_runtime_event(
        tmp_path,
        adapter="qoder_work",
        event_name="UserPromptSubmit",
        session_id=session_id,
    )
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "injection-cohorts.jsonl").write_text(
        json.dumps({
            "adapter": "qoder_work",
            "session_id": session_id,
            "item_ids": ["mem-20260624-161446-wukong-linux-guide"],
            "source": "search",
        }, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    transcript = projects_dir / "session.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(
        json.dumps({
            "sessionId": session_id,
            "type": "assistant",
            "message": {
                "content": [{
                    "type": "thinking",
                    "thinking": "Looking at the system context, I can see the `<agent_brain>` section."
                }]
            },
        }, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    data = verify_adapter(tmp_path, "qoder_work", verifier="pytest")

    assert data["adapter"] == "qoder_work"
    assert data["status"] == "failed"
    assert "context effectiveness not observed" in data["blockers"]


def test_adapter_verify_qoder_work_accepts_gui_model_observed_context_with_cohort(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_brain.agent_integrations import qoder_work as qw_mod
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.product import adapter_onboarding as onboarding
    from agent_brain.product.adapter_onboarding import verify_adapter

    monkeypatch.setattr(qw_mod, "SETTINGS_PATH", tmp_path / ".qoderwork" / "settings.json")
    monkeypatch.setattr(qw_mod, "MCP_CONFIG_PATH", tmp_path / ".qoderwork" / "mcp.json")
    monkeypatch.setattr(
        qw_mod,
        "AWARENESS_PATH",
        tmp_path / ".qoderwork" / "awareness" / "main" / "AGENTS.md",
    )
    projects_dir = (
        tmp_path
        / ".qoderwork"
        / "projects"
        / "-Users-example--qoderwork-workspace-mqwnn7e9ykx7d1jc"
    )
    monkeypatch.setattr(onboarding, "_context_transcript_roots", lambda adapter: [projects_dir])

    session_id = "qoderwork-gui-context-seen"
    qw_mod.QoderWorkAdapter(brain_dir=tmp_path).install()
    record_runtime_event(
        tmp_path,
        adapter="qoder_work",
        event_name="UserPromptSubmit",
        session_id=session_id,
    )
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "injection-cohorts.jsonl").write_text(
        json.dumps({
            "adapter": "qoder_work",
            "session_id": session_id,
            "item_ids": ["mem-20260624-161446-wukong-linux-guide"],
            "source": "search",
        }, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    transcript = projects_dir / f"{session_id}.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(
        json.dumps({
            "sessionId": session_id,
            "type": "assistant",
            "message": {
                "content": [{
                    "type": "thinking",
                    "thinking": "Looking at the system context, I can see the `<agent_brain>` section."
                }]
            },
        }, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    data = verify_adapter(tmp_path, "qoder_work", verifier="pytest")

    assert data["adapter"] == "qoder_work"
    assert data["status"] == "passed"
    assert any("qoderwork_gui_agent_brain" in entry for entry in data["evidence"])


def test_adapter_verify_qoder_work_accepts_gui_memory_candidate_answer_with_cohort(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_brain.agent_integrations import qoder_work as qw_mod
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.product import adapter_onboarding as onboarding
    from agent_brain.product.adapter_onboarding import verify_adapter

    monkeypatch.setattr(qw_mod, "SETTINGS_PATH", tmp_path / ".qoderwork" / "settings.json")
    monkeypatch.setattr(qw_mod, "MCP_CONFIG_PATH", tmp_path / ".qoderwork" / "mcp.json")
    monkeypatch.setattr(
        qw_mod,
        "AWARENESS_PATH",
        tmp_path / ".qoderwork" / "awareness" / "main" / "AGENTS.md",
    )
    projects_dir = (
        tmp_path
        / ".qoderwork"
        / "projects"
        / "-Users-example--qoderwork-workspace-mqwxkmgidplk0gmp"
    )
    monkeypatch.setattr(onboarding, "_context_transcript_roots", lambda adapter: [projects_dir])

    session_id = "qoderwork-gui-candidates-used"
    qw_mod.QoderWorkAdapter(brain_dir=tmp_path).install()
    record_runtime_event(
        tmp_path,
        adapter="qoder_work",
        event_name="UserPromptSubmit",
        session_id=session_id,
    )
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "injection-cohorts.jsonl").write_text(
        json.dumps({
            "adapter": "qoder_work",
            "session_id": session_id,
            "item_ids": ["mem-20260610-alpha-deploy"],
            "source": "search",
        }, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    transcript = projects_dir / f"{session_id}.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(
        json.dumps({
            "sessionId": session_id,
            "type": "assistant",
            "message": {
                "content": [{
                    "type": "text",
                    "text": "根据召回的 memory 候选，Alpha 部署在 example.test/alpha。"
                }]
            },
        }, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    data = verify_adapter(tmp_path, "qoder_work", verifier="pytest")

    assert data["adapter"] == "qoder_work"
    assert data["status"] == "passed"
    assert any("qoderwork_gui_memory_candidates" in entry for entry in data["evidence"])


def test_adapter_verify_mcp_adapter_uses_active_probe_without_hook_runtime(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_brain.agent_integrations import continue_dev as cont_mod
    from agent_brain.product.adapter_onboarding import verify_adapter

    mcp_path = tmp_path / ".continue" / "config.yaml"
    awareness_path = tmp_path / ".continue" / "rules" / "agent-memory-hub.md"
    monkeypatch.setattr(cont_mod, "MCP_CONFIG_PATH", mcp_path)
    monkeypatch.setattr(cont_mod, "AWARENESS_PATH", awareness_path)
    cont_mod.ContinueAdapter(brain_dir=tmp_path).install()

    data = verify_adapter(tmp_path, "continue_dev", verifier="pytest")

    assert data["adapter"] == "continue_dev"
    assert data["status"] == "passed"
    assert data["runtime_events"] == 1
    assert any("mcp_tools=" in entry for entry in data["evidence"])


def test_adapter_verify_context_probe_requires_transcript_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_brain.agent_integrations import codex as cx_mod
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.product import adapter_onboarding as onboarding
    from agent_brain.product.adapter_onboarding import verify_adapter

    monkeypatch.setattr(cx_mod, "AGENTS_MD", tmp_path / ".codex" / "AGENTS.md")
    monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", tmp_path / ".codex" / "hooks.json")
    monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", tmp_path / ".codex" / "config.toml")
    monkeypatch.setattr(onboarding, "_context_transcript_roots", lambda adapter: [])
    cx_mod.CodexAdapter(brain_dir=tmp_path).install()
    record_runtime_event(
        tmp_path,
        adapter="codex",
        event_name="UserPromptSubmit",
        session_id="codex-context-probe-missing",
    )

    data = verify_adapter(
        tmp_path,
        "codex",
        verifier="pytest",
        context_probe=True,
    )

    assert data["adapter"] == "codex"
    assert data["status"] == "failed"
    assert "context effectiveness not observed" in data["blockers"]
    assert data["context_probe"]["status"] == "failed"
    assert data["record"]["status"] == "failed"


def test_adapter_verify_context_probe_records_generic_transcript_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_brain.agent_integrations import codex as cx_mod
    from agent_brain.agent_integrations.runtime_events import record_runtime_event
    from agent_brain.product import adapter_onboarding as onboarding
    from agent_brain.product.adapter_onboarding import verify_adapter

    transcript_root = tmp_path / ".codex" / "sessions"
    monkeypatch.setattr(cx_mod, "AGENTS_MD", tmp_path / ".codex" / "AGENTS.md")
    monkeypatch.setattr(cx_mod, "CODEX_HOOKS_JSON", tmp_path / ".codex" / "hooks.json")
    monkeypatch.setattr(cx_mod, "CODEX_CONFIG_TOML", tmp_path / ".codex" / "config.toml")
    monkeypatch.setattr(onboarding, "_context_transcript_roots", lambda adapter: [transcript_root])
    cx_mod.CodexAdapter(brain_dir=tmp_path).install()
    record_runtime_event(
        tmp_path,
        adapter="codex",
        event_name="UserPromptSubmit",
        session_id="codex-context-probe-present",
    )
    transcript_root.mkdir(parents=True)
    (transcript_root / "probe.jsonl").write_text(
        "<agent_brain>\nAuto-injected memory candidates, not chat history\n</agent_brain>\n",
        encoding="utf-8",
    )

    data = verify_adapter(
        tmp_path,
        "codex",
        verifier="pytest",
        context_probe=True,
    )

    assert data["adapter"] == "codex"
    assert data["status"] == "passed"
    assert data["context_probe"]["status"] == "passed"
    assert any(entry.startswith("context_effective=transcript_agent_brain") for entry in data["evidence"])
    assert any(entry.startswith("context_effective=transcript_agent_brain") for entry in data["record"]["evidence"])


def test_install_verify_uninstall_check_accepts_mcp_probe_without_persisting_runtime(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_brain.agent_integrations import continue_dev as cont_mod
    from agent_brain.agent_integrations.runtime_events import runtime_event_summary
    from agent_brain.product.adapter_onboarding import install_verify_adapter

    monkeypatch.setattr(cont_mod, "MCP_CONFIG_PATH", tmp_path / ".continue" / "config.yaml")
    monkeypatch.setattr(
        cont_mod,
        "AWARENESS_PATH",
        tmp_path / ".continue" / "rules" / "agent-memory-hub.md",
    )

    data = install_verify_adapter(tmp_path, "continue_dev", verifier="pytest", uninstall_check=True)

    assert data["status"] == "passed"
    assert data["verification"]["status"] == "passed"
    assert data["persistent_verification_recorded"] is False
    assert runtime_event_summary(tmp_path, "continue_dev").observed is False
