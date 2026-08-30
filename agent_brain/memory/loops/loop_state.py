from __future__ import annotations

from agent_brain.memory.loops.loop_types import LoopStatus, LoopTransitionError


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    LoopStatus.created.value: {
        LoopStatus.running.value,
        LoopStatus.failed.value,
        LoopStatus.cancelled.value,
    },
    LoopStatus.running.value: {
        LoopStatus.blocked.value,
        LoopStatus.failed.value,
        LoopStatus.completed.value,
        LoopStatus.cancelled.value,
    },
    LoopStatus.blocked.value: {
        LoopStatus.running.value,
        LoopStatus.failed.value,
        LoopStatus.cancelled.value,
    },
    LoopStatus.failed.value: {LoopStatus.running.value},
    LoopStatus.completed.value: set(),
    LoopStatus.cancelled.value: set(),
}


def validate_transition(current: str, target: str) -> None:
    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if target not in allowed:
        allowed_text = ", ".join(sorted(allowed)) or "none"
        raise LoopTransitionError(
            f"illegal loop status transition {current!r} -> {target!r}; allowed: {allowed_text}"
        )


def require_completion_evidence(existing: list[dict[str, object]], evidence: str | None) -> None:
    has_existing = any(str(row.get("evidence") or "").strip() for row in existing)
    has_passed_feedback = any(
        str(row.get("feedback_id") or "").strip() and row.get("status") == "passed"
        for row in existing
    )
    has_new = bool((evidence or "").strip())
    if not has_existing and not has_passed_feedback and not has_new:
        raise LoopTransitionError("loop completion requires verification evidence")
