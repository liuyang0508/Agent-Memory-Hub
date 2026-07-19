"""Fail-closed contracts for fresh real-hook recall evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping


_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_RUN_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_CASE_STATUSES = frozenset({"injected", "empty", "not_applicable", "error", "timeout"})
_TERMINAL_STATUSES = frozenset({"pass", "fail", "blocked"})
_NOT_APPLICABLE_REASONS = frozenset({"explicit_project_scope_unavailable"})
_SENSITIVE_KEYS = frozenset({
    "raw_prompt",
    "stdout",
    "stderr",
    "session_id",
    "raw_query",
    "memory_body",
})


@dataclass(frozen=True)
class HookCaseEvidence:
    case_id: str
    applicable: bool
    expected_status: str | None
    actual_status: str
    expected_item_ids: tuple[str, ...]
    observed_item_ids: tuple[str, ...]
    prohibited_item_ids: tuple[str, ...]
    cohort_item_ids: tuple[str, ...]
    protocol_valid: bool
    cohort_consistent: bool
    gap_consistent: bool
    exit_code: int | None
    duration_ms: float
    reason: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        for field in (
            "expected_item_ids",
            "observed_item_ids",
            "prohibited_item_ids",
            "cohort_item_ids",
        ):
            data[field] = list(data[field])
        return data


@dataclass(frozen=True)
class HookRecallExpectedProvenance:
    git_commit: str
    hook_sha256: str
    implementation_sha256: str
    corpus_sha256: str
    corpus_version: str
    config_sha256: str
    require_clean: bool = False


def validate_hook_recall_manifest(
    payload: object,
    *,
    expected: HookRecallExpectedProvenance,
) -> list[str]:
    """Return stable gate failures without trusting runner-owned objects."""

    base_failures = derive_hook_recall_gate_failures(payload, expected=expected)
    if not isinstance(payload, dict):
        return base_failures
    failures = list(base_failures)
    declared_failures = payload.get("failed_gates")
    if declared_failures != base_failures:
        failures.append("G0:failed_gate_summary_mismatch")
    derived_status = "pass" if not base_failures else "fail"
    if payload.get("status") != derived_status:
        if payload.get("status") == "pass":
            failures.append("G0:false_pass_status")
        else:
            failures.append("G0:incorrect_terminal_status")
    return sorted(set(failures))


def derive_hook_recall_gate_failures(
    payload: object,
    *,
    expected: HookRecallExpectedProvenance,
) -> list[str]:
    """Derive G0-G3 failures without trusting declared status or summaries."""

    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return ["G0:invalid_manifest_schema"]
    failures: list[str] = []
    _check_manifest_metadata(payload, failures)
    _check_sensitive_keys(payload, failures)
    _check_provenance(payload.get("provenance"), expected, failures)
    results = _validated_results(payload.get("results"), failures)
    _check_case_closure(payload, results, failures)
    for result in results:
        _check_case_result(result, failures)
    return sorted(set(failures))


def write_manifest_atomic(
    path: Path,
    payload: Mapping[str, object],
) -> None:
    """Durably replace one manifest without exposing a partial JSON file."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor, raw_tmp = tempfile.mkstemp(
        prefix=f".{target.name}.",
        dir=target.parent,
    )
    temporary = Path(raw_tmp)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def load_hook_recall_manifest(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(Path(path).read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("invalid hook recall evidence manifest") from exc
    if not isinstance(payload, dict):
        raise ValueError("hook recall evidence manifest must be an object")
    return payload


def _check_manifest_metadata(
    payload: Mapping[str, object],
    failures: list[str],
) -> None:
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        failures.append("G0:invalid_run_id")
    started = _parse_timestamp(payload.get("started_at"))
    completed = _parse_timestamp(payload.get("completed_at"))
    if started is None or completed is None or completed < started:
        failures.append("G0:invalid_run_window")
    if payload.get("status") not in _TERMINAL_STATUSES:
        failures.append("G0:invalid_terminal_status")


def _check_provenance(
    raw: object,
    expected: HookRecallExpectedProvenance,
    failures: list[str],
) -> None:
    if not isinstance(raw, dict):
        failures.append("G0:invalid_provenance")
        return
    expected_values = {
        "git_commit": expected.git_commit,
        "hook_sha256": expected.hook_sha256,
        "implementation_sha256": expected.implementation_sha256,
        "corpus_sha256": expected.corpus_sha256,
        "corpus_version": expected.corpus_version,
        "config_sha256": expected.config_sha256,
    }
    if not isinstance(raw.get("git_commit"), str) or not _GIT_COMMIT_RE.fullmatch(
        str(raw.get("git_commit"))
    ):
        failures.append("G0:invalid_git_commit")
    for field in (
        "hook_sha256",
        "implementation_sha256",
        "corpus_sha256",
        "config_sha256",
    ):
        value = raw.get(field)
        if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
            failures.append(f"G0:invalid_{field}")
    for field, value in expected_values.items():
        if raw.get(field) != value:
            failures.append(f"G0:{field}_mismatch")
    if type(raw.get("dirty")) is not bool:
        failures.append("G0:invalid_dirty_state")
    elif expected.require_clean and raw["dirty"]:
        failures.append("G0:dirty_worktree")
    adapter = raw.get("adapter")
    if not isinstance(adapter, str) or not adapter.strip():
        failures.append("G0:invalid_adapter")
    timeout = raw.get("timeout_seconds")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        failures.append("G0:invalid_timeout")


def _validated_results(
    raw: object,
    failures: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or any(not isinstance(row, dict) for row in raw):
        failures.append("G0:invalid_results")
        return []
    results = [dict(row) for row in raw]
    case_ids = [row.get("case_id") for row in results]
    if any(not isinstance(case_id, str) or not case_id for case_id in case_ids):
        failures.append("G0:invalid_case_id")
    if len(case_ids) != len(set(case_ids)):
        failures.append("G0:duplicate_case_result")
    return results


def _check_case_closure(
    payload: Mapping[str, object],
    results: list[dict[str, Any]],
    failures: list[str],
) -> None:
    planned = payload.get("planned_case_ids")
    if (
        not isinstance(planned, list)
        or any(not isinstance(case_id, str) or not case_id for case_id in planned)
        or len(planned) != len(set(planned))
    ):
        failures.append("G0:invalid_planned_cases")
        planned = []
    result_ids = [row.get("case_id") for row in results]
    if set(planned) != set(result_ids) or len(planned) != len(results):
        failures.append("G0:planned_result_mismatch")
    counts = payload.get("counts")
    if not isinstance(counts, dict):
        failures.append("G0:invalid_counts")
        return
    required = {"planned", "applicable", "not_applicable", "executed"}
    if set(counts) != required or any(
        type(counts.get(field)) is not int or counts[field] < 0 for field in required
    ):
        failures.append("G0:invalid_counts")
        return
    applicable = sum(row.get("applicable") is True for row in results)
    not_applicable = sum(row.get("applicable") is False for row in results)
    executed = sum(
        row.get("applicable") is True
        and row.get("actual_status") != "not_applicable"
        for row in results
    )
    expected_counts = {
        "planned": len(planned),
        "applicable": applicable,
        "not_applicable": not_applicable,
        "executed": executed,
    }
    if counts != expected_counts:
        failures.append("G0:count_mismatch")


def _check_case_result(result: Mapping[str, Any], failures: list[str]) -> None:
    case_id = result.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        return
    if type(result.get("applicable")) is not bool:
        failures.append(f"G0:invalid_applicability:{case_id}")
        return
    expected = _item_ids(result.get("expected_item_ids"), case_id, failures)
    observed = _item_ids(result.get("observed_item_ids"), case_id, failures)
    prohibited = _item_ids(result.get("prohibited_item_ids"), case_id, failures)
    cohort = _item_ids(result.get("cohort_item_ids"), case_id, failures)
    actual_status = result.get("actual_status")
    if actual_status not in _CASE_STATUSES:
        failures.append(f"G0:invalid_case_status:{case_id}")
        return
    duration = result.get("duration_ms")
    if (
        not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or duration < 0
    ):
        failures.append(f"G0:invalid_duration:{case_id}")
    if result["applicable"] is False:
        if (
            actual_status != "not_applicable"
            or result.get("expected_status") is not None
            or result.get("reason") not in _NOT_APPLICABLE_REASONS
            or result.get("exit_code") is not None
            or expected
            or observed
            or prohibited
            or cohort
        ):
            failures.append(f"G0:invalid_not_applicable_result:{case_id}")
        return
    if result.get("expected_status") not in {"injected", "empty"}:
        failures.append(f"G0:invalid_expected_status:{case_id}")
    if result.get("protocol_valid") is not True:
        failures.append(f"G1:invalid_hook_protocol:{case_id}")
    if result.get("exit_code") != 0:
        failures.append(f"G3:hook_error:{case_id}")
    if actual_status == "timeout":
        failures.append(f"G3:hook_timeout:{case_id}")
    elif actual_status == "error":
        failures.append(f"G3:hook_error:{case_id}")
    if set(observed) & set(prohibited):
        failures.append(f"G1:prohibited_injection:{case_id}")
    if result.get("cohort_consistent") is not True or observed != cohort:
        failures.append(f"G1:stdout_cohort_mismatch:{case_id}")
    if result.get("gap_consistent") is not True:
        failures.append(f"G1:gap_outcome_mismatch:{case_id}")
    expected_status = result.get("expected_status")
    if actual_status != expected_status:
        failures.append(f"G2:unexpected_hook_status:{case_id}")
    if expected_status == "injected":
        if not set(expected) <= set(observed):
            failures.append(f"G2:missing_expected_items:{case_id}")
        if set(observed) - set(expected):
            failures.append(f"G2:unexpected_context:{case_id}")
    elif observed:
        failures.append(f"G2:unexpected_context:{case_id}")


def _item_ids(
    raw: object,
    case_id: str,
    failures: list[str],
) -> tuple[str, ...]:
    if (
        not isinstance(raw, list)
        or any(not isinstance(value, str) or not value for value in raw)
        or len(raw) != len(set(raw))
    ):
        failures.append(f"G0:invalid_item_ids:{case_id}")
        return ()
    return tuple(raw)


def _check_sensitive_keys(value: object, failures: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in _SENSITIVE_KEYS:
                failures.append(f"G1:sensitive_manifest_field:{key}")
            _check_sensitive_keys(child, failures)
    elif isinstance(value, list):
        for child in value:
            _check_sensitive_keys(child, failures)


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


__all__ = [
    "derive_hook_recall_gate_failures",
    "HookCaseEvidence",
    "HookRecallExpectedProvenance",
    "load_hook_recall_manifest",
    "validate_hook_recall_manifest",
    "write_manifest_atomic",
]
