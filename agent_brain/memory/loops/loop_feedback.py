from __future__ import annotations

import hashlib
import shlex
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


ALLOWED_COMMAND_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("python", "-m", "pytest"),
    ("python", "-m", "compileall"),
    ("python", "-m", "ruff", "check"),
    ("git", "diff", "--check"),
    ("git", "status", "--short"),
    ("memory", "doctor"),
    ("memory", "adapter", "doctor"),
    ("memory", "adapter", "list"),
    ("memory", "benchmark", "retrieval"),
    ("memory", "benchmark", "compression"),
    ("memory", "benchmark", "ml-advisory"),
)

SHELL_CONTROL_MARKERS = ("&&", "||", ";", "|", "`", "$(", ">", "<")


@dataclass(frozen=True)
class CommandValidation:
    allowed: bool
    argv: list[str]
    reason: str | None = None


@dataclass(frozen=True)
class OutputSummary:
    text: str
    sha256: str
    line_count: int
    truncated: bool


@dataclass(frozen=True)
class LoopFeedback:
    feedback_id: str
    timestamp: str
    command: str
    cwd: str | None
    status: str
    category: str
    exit_code: int | None
    duration_ms: int
    stdout_summary: str
    stderr_summary: str
    stdout_sha256: str
    stderr_sha256: str
    stdout_lines: int
    stderr_lines: int
    truncated: bool
    artifact: str | None = None
    verifier_id: str | None = None
    contract_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LoopFeedback:
        return cls(
            feedback_id=str(data["feedback_id"]),
            timestamp=str(data["timestamp"]),
            command=str(data["command"]),
            cwd=str(data["cwd"]) if data.get("cwd") is not None else None,
            status=str(data["status"]),
            category=str(data["category"]),
            exit_code=int(data["exit_code"]) if data.get("exit_code") is not None else None,
            duration_ms=int(data.get("duration_ms") or 0),
            stdout_summary=str(data.get("stdout_summary") or ""),
            stderr_summary=str(data.get("stderr_summary") or ""),
            stdout_sha256=str(data.get("stdout_sha256") or ""),
            stderr_sha256=str(data.get("stderr_sha256") or ""),
            stdout_lines=int(data.get("stdout_lines") or 0),
            stderr_lines=int(data.get("stderr_lines") or 0),
            truncated=bool(data.get("truncated")),
            artifact=str(data["artifact"]) if data.get("artifact") is not None else None,
            verifier_id=str(data["verifier_id"]) if data.get("verifier_id") is not None else None,
            contract_id=str(data["contract_id"]) if data.get("contract_id") is not None else None,
        )


@dataclass(frozen=True)
class LoopVerificationSummary:
    loop_id: str
    attempted: int
    passed: int
    failed: int
    blocked: int
    timed_out: int
    feedback: list[LoopFeedback]

    def to_dict(self) -> dict[str, Any]:
        return {
            "loop_id": self.loop_id,
            "attempted": self.attempted,
            "passed": self.passed,
            "failed": self.failed,
            "blocked": self.blocked,
            "timed_out": self.timed_out,
            "feedback": [item.to_dict() for item in self.feedback],
        }


def validate_verification_command(command: str) -> CommandValidation:
    if any(marker in command for marker in SHELL_CONTROL_MARKERS):
        return CommandValidation(False, [], "shell_control")
    try:
        argv = shlex.split(command)
    except ValueError:
        return CommandValidation(False, [], "parse_error")
    if not argv:
        return CommandValidation(False, [], "empty")
    for prefix in ALLOWED_COMMAND_PREFIXES:
        if tuple(argv[: len(prefix)]) == prefix:
            return CommandValidation(True, argv)
    return CommandValidation(False, argv, "not_allowlisted")


def summarize_output(text: str | bytes | None, *, max_chars: int = 800) -> OutputSummary:
    if isinstance(text, bytes):
        value = text.decode("utf-8", errors="replace")
    else:
        value = text or ""
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    stripped = value.strip()
    line_count = len(value.splitlines())
    if not stripped:
        return OutputSummary("no output", digest, line_count, False)
    if len(stripped) <= max_chars:
        return OutputSummary(stripped, digest, line_count, False)
    half = max(1, max_chars // 2)
    summary = f"{stripped[:half]}\n... [truncated] ...\n{stripped[-half:]}"
    return OutputSummary(summary, digest, line_count, True)


def classify_feedback(
    *,
    exit_code: int | None,
    invalid_reason: str | None = None,
    timed_out: bool = False,
    runner_error: bool = False,
) -> tuple[str, str]:
    if timed_out:
        return "timed_out", "timeout"
    if invalid_reason:
        return "blocked", "invalid_action"
    if runner_error:
        return "blocked", "runner_error"
    if exit_code == 0:
        return "passed", "successful_execution"
    return "failed", "verification_failed"


def make_feedback_id(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    stamp = value.astimezone(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"lfb-{stamp}-{uuid.uuid4().hex[:8]}"


def feedback_rows(rows: list[dict[str, Any]]) -> list[LoopFeedback]:
    feedback: list[LoopFeedback] = []
    for row in rows:
        if row.get("feedback_id"):
            feedback.append(LoopFeedback.from_dict(row))
    return feedback


def format_feedback_view(
    loop_id: str,
    rows: list[dict[str, Any]],
    *,
    limit: int = 10,
    metadata: dict[str, Any] | None = None,
) -> str:
    feedback = feedback_rows(rows)[-limit:]
    if not feedback:
        return f"loop {loop_id} feedback:\n- no structured feedback recorded"
    lines = [f"loop {loop_id} feedback:"]
    for item in feedback:
        output = item.stdout_summary if item.stdout_summary != "no output" else item.stderr_summary
        verifier = f" [{item.verifier_id}]" if item.verifier_id else ""
        lines.append(f"- {item.category}{verifier}: {item.command} -> {_one_line(output)}")
    readiness = _contract_readiness(feedback, metadata or {})
    if readiness:
        lines.extend(readiness)
    elif all(item.status == "passed" for item in feedback):
        lines.append("next: evidence is strong enough for completion if implementation scope matches these checks.")
    elif any(item.status in {"failed", "timed_out", "blocked"} for item in feedback):
        lines.append("next: inspect failed or blocked feedback before completing this loop.")
    return "\n".join(lines)


def _one_line(value: str) -> str:
    return " / ".join(part.strip() for part in value.splitlines() if part.strip()) or "no output"


def _contract_readiness(
    feedback: list[LoopFeedback],
    metadata: dict[str, Any],
) -> list[str]:
    required = _required_verifiers(metadata)
    if not required:
        return []
    latest_by_id: dict[str, LoopFeedback] = {}
    latest_by_command: dict[str, LoopFeedback] = {}
    for item in feedback:
        if item.verifier_id:
            latest_by_id[item.verifier_id] = item
        latest_by_command[item.command] = item

    missing: list[str] = []
    failed: list[str] = []
    for verifier in required:
        verifier_id = str(verifier.get("id") or "")
        command = str(verifier.get("command") or "")
        item = latest_by_id.get(verifier_id) or latest_by_command.get(command)
        label = verifier_id or command
        if item is None:
            missing.append(label)
        elif item.status != "passed":
            failed.append(label)

    open_gates = _open_human_gate_ids(metadata)
    if not missing and not failed and not open_gates:
        return [
            "completion_readiness: ready",
            "stop_conditions:",
            "- all_required_verifiers_pass: satisfied",
            "- no_open_human_gate: satisfied",
        ]
    lines = [
        "completion_readiness: blocked",
        "stop_conditions:",
        (
            "- all_required_verifiers_pass: satisfied"
            if not missing and not failed
            else "- all_required_verifiers_pass: unsatisfied"
        ),
    ]
    if missing:
        lines.append(f"- missing_required_verifiers: {', '.join(missing)}")
    if failed:
        lines.append(f"- failed_required_verifiers: {', '.join(failed)}")
    if open_gates:
        lines.append("- no_open_human_gate: unsatisfied")
        lines.append(f"- open_human_gates: {', '.join(open_gates)}")
    return lines


def _required_verifiers(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    rows = metadata.get("contract_verifiers")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and row.get("required")]


def _open_human_gate_ids(metadata: dict[str, Any]) -> list[str]:
    rows = metadata.get("open_human_gates")
    if not isinstance(rows, list):
        return []
    return [str(row.get("id") or "") for row in rows if isinstance(row, dict) and row.get("id")]
