"""Tests for higher-order self-evolution control reports."""

from __future__ import annotations

from pathlib import Path


def test_evolution_control_turns_data_flow_gaps_into_review_recommendations(tmp_brain: Path):
    from agent_brain.agent_integrations.verifications import record_adapter_verification
    from agent_brain.memory.governance.recall_events import record_gap
    from agent_brain.product.evolution_control import build_evolution_control_report

    record_gap(
        tmp_brain,
        query="raw user query should not leak",
        reason="firewall_rejected_all",
        injected_ids=[],
        rejected_ids=["mem-a"],
        adapter="codex",
        session_id="sess-1",
    )
    record_adapter_verification(
        tmp_brain,
        adapter="codex",
        status="failed",
        verifier="pytest",
        evidence=["doctor", "runtime_events=0"],
    )

    report = build_evolution_control_report(tmp_brain, apply_requested=False).to_dict()

    assert report["mode"] == "shadow_mode"
    assert report["mutation_boundary"] == "proposal_only"
    assert report["data_flow"]["failures"] == 2
    assert {gate["name"] for gate in report["gates"]} >= {
        "audit_gate",
        "write_funnel",
        "release_gate",
        "data_flow_observability",
    }
    assert any(gate["status"] == "shadow_mode" for gate in report["gates"])
    actions = {rec["action"] for rec in report["recommendations"]}
    assert "review_recall_gaps" in actions
    assert "collect_adapter_verification" in actions
    serialized = str(report)
    assert "raw user query should not leak" not in serialized


def test_evolution_control_empty_data_flow_stays_in_safe_observation(tmp_brain: Path):
    from agent_brain.product.evolution_control import build_evolution_control_report

    report = build_evolution_control_report(tmp_brain, apply_requested=False).to_dict()

    assert report["data_flow"]["total"] == 0
    assert report["recommendations"] == [
        {
            "action": "continue_shadow_mode",
            "risk": "safe_observation",
            "reason": "No recent data-flow gap requires automatic mutation; keep evolve suggestions in preview/review mode.",
            "source_count": 0,
            "evidence": [],
        }
    ]
