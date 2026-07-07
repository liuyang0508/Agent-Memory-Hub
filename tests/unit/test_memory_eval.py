from __future__ import annotations

import json
from pathlib import Path


FIXTURE = Path("tests/fixtures/memory_eval/p0_suite.json")


def test_memory_eval_report_to_dict_is_json_ready() -> None:
    from agent_brain.evaluation.memory_eval import MemoryEvalCaseResult, MemoryEvalReport

    case = MemoryEvalCaseResult(
        case_id="case-1",
        case_type="recall",
        passed=True,
        metrics={"mrr": 1.0},
        expected={"expected_ids": ("mem-target",)},
        observed={"ranking": ("mem-target",)},
        failures=[],
    )
    report = MemoryEvalReport(
        passed=True,
        metrics={"recall_at_1": 1.0},
        cases=[case],
        failures=[],
        temp_brain_dir=None,
    )

    payload = report.to_dict()

    assert payload["passed"] is True
    assert payload["cases"][0]["expected"]["expected_ids"] == ["mem-target"]
    assert payload["cases"][0]["observed"]["ranking"] == ["mem-target"]
    json.dumps(payload, ensure_ascii=False)


def test_memory_eval_case_result_defensively_copies_inputs() -> None:
    from agent_brain.evaluation.memory_eval import MemoryEvalCaseResult

    metrics = {"mrr": 1.0}
    expected = {"expected_ids": ["mem-target"], "nested": {"ids": ["nested-target"]}}
    observed = {"ranking": ["mem-target"]}
    failures = ["original_failure"]

    case = MemoryEvalCaseResult(
        case_id="case-1",
        case_type="recall",
        passed=False,
        metrics=metrics,
        expected=expected,
        observed=observed,
        failures=failures,
    )

    metrics["mrr"] = 0.0
    expected["expected_ids"].append("mem-other")
    expected["nested"]["ids"].append("nested-other")
    observed["ranking"].append("mem-other")
    failures.append("later_failure")

    assert case.metrics == {"mrr": 1.0}
    assert case.expected == {
        "expected_ids": ["mem-target"],
        "nested": {"ids": ["nested-target"]},
    }
    assert case.observed == {"ranking": ["mem-target"]}
    assert case.failures == ["original_failure"]


def test_memory_eval_report_defensively_copies_inputs() -> None:
    from agent_brain.evaluation.memory_eval import MemoryEvalCaseResult, MemoryEvalReport

    case = MemoryEvalCaseResult(
        case_id="case-1",
        case_type="recall",
        passed=True,
        metrics={"mrr": 1.0},
        expected={"expected_ids": ["mem-target"]},
        observed={"ranking": ["mem-target"]},
        failures=[],
    )
    metrics = {"recall_at_1": 1.0}
    cases = [case]
    failures = ["original_failure"]

    report = MemoryEvalReport(
        passed=False,
        metrics=metrics,
        cases=cases,
        failures=failures,
        temp_brain_dir=None,
    )

    metrics["recall_at_1"] = 0.0
    case.metrics["mrr"] = 0.0
    cases.append(
        MemoryEvalCaseResult(
            case_id="case-2",
            case_type="recall",
            passed=False,
            metrics={},
            expected={},
            observed={},
            failures=["later_failure"],
        )
    )
    failures.append("later_failure")

    assert report.metrics == {"recall_at_1": 1.0}
    assert len(report.cases) == 1
    assert report.cases[0].metrics == {"mrr": 1.0}
    assert report.failures == ["original_failure"]


def test_load_suite_from_path_reads_fixture() -> None:
    from agent_brain.evaluation.memory_eval import load_suite

    suite = load_suite(FIXTURE)

    assert suite["id"] == "memory-eval-p0-fixture"
    assert suite["top_k"] == 5
    assert [case["type"] for case in suite["cases"]] == [
        "conversation_replay",
        "recall",
        "dynamic_update",
    ]


def test_default_suite_has_required_case_types() -> None:
    from agent_brain.evaluation.memory_eval import default_suite

    suite = default_suite()

    assert suite["id"] == "memory-eval-p0"
    assert {"conversation_replay", "recall", "dynamic_update"} <= {
        case["type"] for case in suite["cases"]
    }


def test_unknown_case_type_fails_without_crashing(tmp_path) -> None:
    from agent_brain.evaluation.memory_eval import MemoryEvalHarness

    suite = {
        "id": "unknown-case-suite",
        "top_k": 5,
        "cases": [{"id": "unknown", "type": "does_not_exist"}],
    }

    report = MemoryEvalHarness(brain_dir=tmp_path / "brain").run(suite)

    assert report.passed is False
    assert report.cases[0].case_id == "unknown"
    assert report.cases[0].case_type == "does_not_exist"
    assert report.cases[0].failures == ["unknown_case_type:does_not_exist"]


def test_invalid_case_entry_fails_without_disappearing(tmp_path) -> None:
    from agent_brain.evaluation.memory_eval import MemoryEvalHarness

    suite = {
        "id": "invalid-case-suite",
        "top_k": 5,
        "cases": ["not-a-case"],
    }

    report = MemoryEvalHarness(brain_dir=tmp_path / "brain").run(suite)

    assert report.passed is False
    assert report.cases[0].case_id == "invalid-case-0"
    assert report.cases[0].case_type == "invalid"
    assert report.cases[0].failures == ["invalid_case_entry"]


def test_harness_deep_copies_external_suite_before_running(tmp_path) -> None:
    from agent_brain.evaluation.memory_eval import MemoryEvalCaseResult, MemoryEvalHarness

    class MutatingHarness(MemoryEvalHarness):
        def _run_case(self, case, brain_dir, *, top_k):
            del brain_dir, top_k
            case["expected"]["expected_ids"].append("mutated")
            return MemoryEvalCaseResult(
                case_id=str(case["id"]),
                case_type=str(case["type"]),
                passed=True,
                metrics={},
                expected=case["expected"],
                observed={},
                failures=[],
            )

    suite = {
        "id": "external-suite",
        "top_k": 5,
        "cases": [
            {
                "id": "case-1",
                "type": "custom",
                "expected": {"expected_ids": ["mem-target"]},
            }
        ],
    }

    report = MutatingHarness(brain_dir=tmp_path / "brain").run(suite)

    assert suite["cases"][0]["expected"]["expected_ids"] == ["mem-target"]
    assert report.cases[0].expected == {"expected_ids": ["mem-target", "mutated"]}


def test_recall_case_reports_expected_ranking_and_metrics(tmp_path) -> None:
    from agent_brain.evaluation.memory_eval import MemoryEvalHarness

    target_id = "mem-20260628-130000-current-decision"
    suite = {
        "id": "recall-suite",
        "top_k": 5,
        "cases": [
            {
                "id": "recall-current-decision",
                "type": "recall",
                "items": [
                    {
                        "id": target_id,
                        "type": "decision",
                        "title": "Offline harvesting decision",
                        "summary": "Use mechanical-first harvesting before optional LLM enrichment",
                        "body": "Decision: AMH uses mechanical-first harvesting so it works offline.",
                        "refs": {"urls": ["https://example.test/design"]},
                    }
                ],
                "queries": [
                    {
                        "query": "offline harvesting decision",
                        "expected_ids": [target_id],
                    }
                ],
            }
        ],
    }

    report = MemoryEvalHarness(brain_dir=tmp_path / "brain").run(suite)

    assert report.passed is True
    assert report.metrics["recall_at_1"] == 1.0
    assert report.metrics["recall_at_5"] == 1.0
    case = report.cases[0]
    assert case.passed is True
    assert case.metrics["mrr"] == 1.0
    assert case.observed["queries"][0]["ranking"][0] == target_id


def test_recall_case_reports_missing_expected_id(tmp_path) -> None:
    from agent_brain.evaluation.memory_eval import MemoryEvalHarness

    suite = {
        "id": "recall-failure-suite",
        "top_k": 5,
        "cases": [
            {
                "id": "recall-missing",
                "type": "recall",
                "items": [
                    {
                        "id": "mem-20260628-130001-other",
                        "type": "fact",
                        "title": "Unrelated note",
                        "summary": "This note does not match the expected id",
                        "body": "Nothing about the target.",
                        "refs": {"urls": ["https://example.test/other"]},
                    }
                ],
                "queries": [
                    {
                        "query": "offline harvesting decision",
                        "expected_ids": ["mem-20260628-130002-missing-target"],
                    }
                ],
            }
        ],
    }

    report = MemoryEvalHarness(brain_dir=tmp_path / "brain").run(suite)

    assert report.passed is False
    assert report.metrics["recall_at_1"] == 0.0
    assert report.cases[0].failures == [
        "missing_expected:mem-20260628-130002-missing-target"
    ]
    assert report.cases[0].observed["queries"][0]["rank"] is None


def test_dynamic_update_filters_forbidden_superseded_id(tmp_path) -> None:
    from agent_brain.evaluation.memory_eval import MemoryEvalHarness

    current_id = "mem-20260628-130004-new-cli"
    old_id = "mem-20260628-130003-old-cli"
    suite = {
        "id": "dynamic-suite",
        "top_k": 5,
        "cases": [
            {
                "id": "superseded-memory-guard",
                "type": "dynamic_update",
                "items": [
                    {
                        "id": old_id,
                        "type": "decision",
                        "title": "Use old CLI",
                        "summary": "Old memory eval command decision",
                        "body": "Use old eval command.",
                        "superseded_by": current_id,
                        "refs": {"urls": ["https://example.test/old"]},
                    },
                    {
                        "id": current_id,
                        "type": "decision",
                        "title": "Use memory eval run",
                        "summary": "Supported memory eval command decision",
                        "body": "Use memory eval run as the supported eval command.",
                        "refs": {"urls": ["https://example.test/new"]},
                    },
                ],
                "queries": [
                    {
                        "query": "supported memory eval command",
                        "expected_ids": [current_id],
                        "forbidden_ids": [old_id],
                    }
                ],
            }
        ],
    }

    report = MemoryEvalHarness(brain_dir=tmp_path / "brain").run(suite)

    assert report.passed is True
    query = report.cases[0].observed["queries"][0]
    assert query["ranking"][0] == current_id
    assert old_id not in query["ranking"]
    assert report.metrics["dynamic_update_pass_rate"] == 1.0


def test_conversation_replay_writes_raw_and_harvested_items_idempotently(tmp_path) -> None:
    from agent_brain.evaluation.memory_eval import MemoryEvalHarness

    suite = {
        "id": "conversation-suite",
        "cases": [
            {
                "id": "conversation-mechanical-harvest",
                "type": "conversation_replay",
                "transcript": [
                    {"role": "user", "content": "fix the failing test_cli_version"},
                    {
                        "role": "assistant",
                        "content": "Decision: chose mechanical-first harvesting over pure LLM so it works offline.",
                    },
                ],
                "expected": {
                    "raw_messages": 2,
                    "min_written_items": 1,
                    "item_types": ["decision"],
                    "source_kind": "harvested",
                },
            }
        ],
    }

    report = MemoryEvalHarness(brain_dir=tmp_path / "brain").run(suite)

    assert report.passed is True
    case = report.cases[0]
    assert case.observed["raw_messages"] == 2
    assert case.observed["written_items"] == 1
    assert case.observed["second_run_written_items"] == 0
    assert case.observed["item_types"] == ["decision"]
    assert case.observed["source_kinds"] == ["harvested"]


def test_default_suite_passes_in_isolated_brain(tmp_path) -> None:
    from agent_brain.evaluation.memory_eval import MemoryEvalHarness, load_suite

    report = MemoryEvalHarness(brain_dir=tmp_path / "brain").run(load_suite(FIXTURE))

    assert report.passed is True
    assert report.metrics["conversation_replay_pass_rate"] == 1.0
    assert report.metrics["recall_at_5"] == 1.0
    assert report.metrics["dynamic_update_pass_rate"] == 1.0
