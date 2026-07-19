from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]


def _observation(case_id: str, split: str, **overrides):
    from agent_brain.evaluation.recall_quality import RecallQualityObservation

    values = {
        "case_id": case_id,
        "split": split,
        "adapter": "codex",
        "project_scope": "global",
        "language": "zh",
        "category": "semantic",
        "expected_item_ids": (f"expected-{case_id}",),
        "allowed_item_ids": (),
        "candidate_ids": (f"expected-{case_id}",),
        "injected_ids": (f"expected-{case_id}",),
        "prohibited_item_ids": (f"prohibited-{case_id}",),
        "expected_admission": True,
        "admission_allowed": True,
        "admission_reason": "meaningful_query",
        "expected_answerability": "supported",
        "actual_answerability": "supported",
        "expected_temporal": "stable",
        "actual_temporal": "stable",
        "expected_abstention": False,
        "actual_abstention": False,
        "expected_injection": True,
        "used_tokens": 10,
    }
    values.update(overrides)
    return RecallQualityObservation(**values)


def test_six_layer_report_is_split_and_dimension_bucketed() -> None:
    from agent_brain.evaluation.recall_quality import build_recall_quality_report

    rows = [
        _observation("cal", "calibration"),
        _observation("held", "heldout", language="en", category="multilingual"),
        _observation(
            "prod",
            "production_replay",
            expected_item_ids=(),
            candidate_ids=(),
            injected_ids=(),
            expected_answerability="not_applicable",
            actual_answerability="not_applicable",
            expected_temporal="not_applicable",
            actual_temporal="not_applicable",
            expected_abstention=True,
            actual_abstention=True,
            expected_injection=False,
            used_tokens=0,
        ),
    ]

    report = build_recall_quality_report(
        rows,
        corpus_sha256={"legacy": "sha256:" + "a" * 64},
        implementation_sha256="sha256:" + "b" * 64,
        evaluation_now="2026-07-19T02:00:00+00:00",
    )

    assert report["status"] == "pass"
    assert set(report["layers"]) == {
        "retrieval",
        "admission",
        "answerability",
        "temporal",
        "abstention",
        "injection",
    }
    assert set(report["breakdowns"]) == {
        "split",
        "adapter",
        "project_scope",
        "language",
        "category",
    }
    assert set(report["breakdowns"]["split"]) == {
        "calibration",
        "heldout",
        "production_replay",
    }
    assert report["layers"]["retrieval"]["recall_at_10"] == 1.0
    assert report["layers"]["retrieval"]["mrr"] == 1.0
    assert report["layers"]["abstention"]["precision"] == 1.0
    assert report["layers"]["injection"]["used_tokens"] == 20


def test_report_fails_closed_on_prohibited_injection() -> None:
    from agent_brain.evaluation.recall_quality import build_recall_quality_report

    rows = [
        _observation("cal", "calibration"),
        _observation("held", "heldout"),
        _observation(
            "prod",
            "production_replay",
            injected_ids=("prohibited-prod",),
        ),
    ]

    report = build_recall_quality_report(
        rows,
        corpus_sha256={"legacy": "sha256:" + "a" * 64},
        implementation_sha256="sha256:" + "b" * 64,
        evaluation_now="2026-07-19T02:00:00+00:00",
    )

    assert report["status"] == "fail"
    assert "production_replay:injection" in report["failed_gates"]
    assert "overall:prohibited_injection" in report["failed_gates"]


def test_report_requires_all_three_splits() -> None:
    from agent_brain.evaluation.recall_quality import build_recall_quality_report

    with pytest.raises(ValueError, match="requires all three splits"):
        build_recall_quality_report(
            [_observation("cal", "calibration")],
            corpus_sha256={},
            implementation_sha256="sha256:" + "b" * 64,
            evaluation_now="2026-07-19T02:00:00+00:00",
        )


def test_legacy_safety_summary_executes_all_41_case_contracts() -> None:
    script_path = ROOT / "scripts" / "check-recall-quality.py"
    spec = importlib.util.spec_from_file_location("check_recall_quality", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module._legacy_safety_fixture_summary() == {
        "case_count": 41,
        "false_positive_count": 0,
        "false_negative_count": 0,
        "prohibited_injection_count": 0,
        "failed_case_ids": [],
    }


def test_readiness_separates_routed_core_from_real_hook_evidence() -> None:
    script_path = ROOT / "scripts" / "check-recall-quality.py"
    spec = importlib.util.spec_from_file_location("check_recall_quality", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    markdown = module.render_markdown(module.generate_report())

    assert "不把它写成真实 Hook PASS" in markdown
    assert "hook-recall-evidence artifact" in markdown
    assert "explicit project hard-filter" in markdown
