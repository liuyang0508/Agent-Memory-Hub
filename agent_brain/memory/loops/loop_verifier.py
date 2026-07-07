from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from agent_brain.memory.loops.loop_feedback import (
    LoopFeedback,
    LoopVerificationSummary,
    classify_feedback,
    make_feedback_id,
    summarize_output,
    validate_verification_command,
)
from agent_brain.memory.loops.loop_store import LoopStore


class LoopVerifier:
    def __init__(self, brain_dir: Path) -> None:
        self.store = LoopStore(brain_dir)

    def verify(
        self,
        loop_id: str,
        *,
        limit: int | None = None,
        timeout: int = 60,
        cwd: str | None = None,
        commands: list[str] | None = None,
        actor: str = "cli",
    ) -> LoopVerificationSummary:
        if timeout < 1 or timeout > 600:
            raise ValueError("timeout must be between 1 and 600 seconds")
        loop = self.store.get(loop_id)
        selected = list(commands if commands is not None else loop.verification_plan)
        if limit is not None:
            selected = selected[: max(0, limit)]
        run_cwd = cwd or loop.cwd
        verifier_lookup = _verifier_lookup(loop.metadata)
        contract_id = str(loop.metadata["contract_id"]) if loop.metadata.get("contract_id") else None
        feedback: list[LoopFeedback] = []
        for command in selected:
            verifier = verifier_lookup.get(command, {})
            verifier_id = str(verifier["id"]) if verifier.get("id") else None
            item = self._run_command(
                command,
                cwd=run_cwd,
                timeout=timeout,
                verifier_id=verifier_id,
                contract_id=contract_id,
            )
            self.store.add_verification_feedback(loop_id, item, actor=actor)
            feedback.append(item)
        return LoopVerificationSummary(
            loop_id=loop_id,
            attempted=len(feedback),
            passed=sum(1 for item in feedback if item.status == "passed"),
            failed=sum(1 for item in feedback if item.status == "failed"),
            blocked=sum(1 for item in feedback if item.status == "blocked"),
            timed_out=sum(1 for item in feedback if item.status == "timed_out"),
            feedback=feedback,
        )

    def _run_command(
        self,
        command: str,
        *,
        cwd: str | None,
        timeout: int,
        verifier_id: str | None = None,
        contract_id: str | None = None,
    ) -> LoopFeedback:
        now = datetime.now(timezone.utc)
        validation = validate_verification_command(command)
        if not validation.allowed:
            return _feedback(
                command=command,
                cwd=cwd,
                now=now,
                status="blocked",
                category="invalid_action",
                exit_code=None,
                duration_ms=0,
                stdout="",
                stderr=validation.reason or "invalid action",
                verifier_id=verifier_id,
                contract_id=contract_id,
            )
        if cwd and not Path(cwd).exists():
            return _feedback(
                command=command,
                cwd=cwd,
                now=now,
                status="blocked",
                category="runner_error",
                exit_code=None,
                duration_ms=0,
                stdout="",
                stderr=f"cwd does not exist: {cwd}",
                verifier_id=verifier_id,
                contract_id=contract_id,
            )
        started = time.monotonic()
        try:
            result = subprocess.run(
                validation.argv,
                cwd=cwd or None,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            return _feedback(
                command=command,
                cwd=cwd,
                now=now,
                status="timed_out",
                category="timeout",
                exit_code=None,
                duration_ms=duration_ms,
                stdout=exc.stdout,
                stderr=exc.stderr or f"timed out after {timeout}s",
                verifier_id=verifier_id,
                contract_id=contract_id,
            )
        except OSError as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            return _feedback(
                command=command,
                cwd=cwd,
                now=now,
                status="blocked",
                category="runner_error",
                exit_code=None,
                duration_ms=duration_ms,
                stdout="",
                stderr=str(exc),
                verifier_id=verifier_id,
                contract_id=contract_id,
            )
        duration_ms = int((time.monotonic() - started) * 1000)
        status, category = classify_feedback(exit_code=result.returncode)
        return _feedback(
            command=command,
            cwd=cwd,
            now=now,
            status=status,
            category=category,
            exit_code=result.returncode,
            duration_ms=duration_ms,
            stdout=result.stdout,
            stderr=result.stderr,
            verifier_id=verifier_id,
            contract_id=contract_id,
        )


def _feedback(
    *,
    command: str,
    cwd: str | None,
    now: datetime,
    status: str,
    category: str,
    exit_code: int | None,
    duration_ms: int,
    stdout: str | bytes | None,
    stderr: str | bytes | None,
    verifier_id: str | None = None,
    contract_id: str | None = None,
) -> LoopFeedback:
    stdout_summary = summarize_output(stdout)
    stderr_summary = summarize_output(stderr)
    return LoopFeedback(
        feedback_id=make_feedback_id(now),
        timestamp=now.isoformat(),
        command=command,
        cwd=cwd,
        status=status,
        category=category,
        exit_code=exit_code,
        duration_ms=duration_ms,
        stdout_summary=stdout_summary.text,
        stderr_summary=stderr_summary.text,
        stdout_sha256=stdout_summary.sha256,
        stderr_sha256=stderr_summary.sha256,
        stdout_lines=stdout_summary.line_count,
        stderr_lines=stderr_summary.line_count,
        truncated=stdout_summary.truncated or stderr_summary.truncated,
        verifier_id=verifier_id,
        contract_id=contract_id,
    )


def _verifier_lookup(metadata: dict[str, object]) -> dict[str, dict[str, object]]:
    rows = metadata.get("contract_verifiers")
    if not isinstance(rows, list):
        return {}
    lookup: dict[str, dict[str, object]] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("command"):
            lookup[str(row["command"])] = row
    return lookup
