from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_brain.memory.loops.loop_events import append_loop_event
from agent_brain.memory.loops.loop_feedback import LoopFeedback
from agent_brain.memory.loops.loop_state import require_completion_evidence, validate_transition
from agent_brain.memory.loops.loop_types import (
    LoopEvent,
    LoopEventType,
    LoopNotFoundError,
    LoopRun,
    LoopStatus,
    LoopTransitionError,
    bounded_trigger,
    make_event_id,
    make_loop_id,
    timestamp,
)


class LoopStore:
    def __init__(self, brain_dir: Path) -> None:
        self.brain_dir = Path(brain_dir)
        self.loops_dir = self.brain_dir / "runtime" / "loops"

    def create(
        self,
        *,
        goal: str,
        project: str | None = None,
        adapter: str | None = None,
        session_id: str | None = None,
        cwd: str | None = None,
        verification_plan: list[str] | None = None,
        trigger: dict[str, Any] | None = None,
        budget: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        sensitivity: str = "internal",
        start: bool = False,
        actor: str = "cli",
        now: datetime | None = None,
    ) -> LoopRun:
        if not goal.strip():
            raise ValueError("goal is required")
        current_time = timestamp(now)
        status = LoopStatus.running.value if start else LoopStatus.created.value
        loop = LoopRun(
            loop_id=make_loop_id(goal, now),
            created_at=current_time,
            updated_at=current_time,
            status=status,
            goal=goal,
            trigger=bounded_trigger(trigger),
            project=project or None,
            cwd=cwd or None,
            adapter=adapter or None,
            session_id=session_id or None,
            budget=dict(budget or {}),
            context=dict(context or {}),
            metadata=dict(metadata or {}),
            verification_plan=list(verification_plan or []),
            sensitivity=sensitivity or "internal",
        )
        self._write(loop)
        self._event(
            loop,
            LoopEventType.created.value,
            actor,
            "loop created",
            payload={"status": LoopStatus.created.value},
            now=now,
        )
        if start:
            self._event(
                loop,
                LoopEventType.status_changed.value,
                actor,
                "loop started",
                payload={"from": LoopStatus.created.value, "to": LoopStatus.running.value},
                now=now,
            )
        return loop

    def get(self, loop_id: str) -> LoopRun:
        path = self._path(loop_id)
        if not path.exists():
            raise LoopNotFoundError(f"loop not found: {loop_id}")
        return LoopRun.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list(self, *, status: str | None = None, project: str | None = None) -> list[LoopRun]:
        if not self.loops_dir.exists():
            return []
        rows: list[LoopRun] = []
        for path in sorted(self.loops_dir.glob("*.json")):
            try:
                loop = LoopRun.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (KeyError, TypeError, json.JSONDecodeError):
                continue
            if status and loop.status != status:
                continue
            if project and loop.project != project:
                continue
            rows.append(loop)
        return rows

    def checkpoint(
        self,
        loop_id: str,
        *,
        note: str,
        artifact: str | None = None,
        actor: str = "cli",
        now: datetime | None = None,
    ) -> LoopRun:
        if not note.strip():
            raise LoopTransitionError("loop checkpoint requires note")
        loop = self.get(loop_id)
        if loop.status in {
            LoopStatus.completed.value,
            LoopStatus.cancelled.value,
        }:
            raise LoopTransitionError(f"loop checkpoint requires active loop; current: {loop.status}")
        if loop.status == LoopStatus.failed.value:
            validate_transition(loop.status, LoopStatus.running.value)
            loop = replace(loop, status=LoopStatus.running.value)
            self._event(
                loop,
                LoopEventType.status_changed.value,
                actor,
                "loop restarted by checkpoint",
                payload={"from": LoopStatus.failed.value, "to": LoopStatus.running.value},
                now=now,
            )
        elif loop.status == LoopStatus.created.value:
            validate_transition(loop.status, LoopStatus.running.value)
            loop = replace(loop, status=LoopStatus.running.value)
            self._event(
                loop,
                LoopEventType.status_changed.value,
                actor,
                "loop started by checkpoint",
                payload={"from": LoopStatus.created.value, "to": LoopStatus.running.value},
                now=now,
            )

        checkpoint = {"timestamp": timestamp(now), "note": note, "actor": actor}
        checkpoints = [*loop.checkpoints, checkpoint]
        artifacts = list(loop.artifacts)
        if artifact:
            artifacts.append(
                {"timestamp": timestamp(now), "kind": "artifact", "value": artifact, "actor": actor}
            )
        updated = replace(loop, updated_at=timestamp(now), checkpoints=checkpoints, artifacts=artifacts)
        self._write(updated)
        self._event(
            updated,
            LoopEventType.checkpoint_added.value,
            actor,
            note,
            payload=checkpoint,
            now=now,
        )
        if artifact:
            self._event(
                updated,
                LoopEventType.artifact_added.value,
                actor,
                artifact,
                payload={"artifact": artifact},
                now=now,
            )
        return updated

    def complete(
        self,
        loop_id: str,
        *,
        evidence: str | None = None,
        artifact: str | None = None,
        actor: str = "cli",
        now: datetime | None = None,
    ) -> LoopRun:
        loop = self.get(loop_id)
        require_completion_evidence(loop.verification_results, evidence)
        validate_transition(loop.status, LoopStatus.completed.value)
        verification_results = list(loop.verification_results)
        if evidence:
            verification_results.append({"timestamp": timestamp(now), "evidence": evidence, "actor": actor})
        artifacts = list(loop.artifacts)
        if artifact:
            artifacts.append(
                {"timestamp": timestamp(now), "kind": "artifact", "value": artifact, "actor": actor}
            )
        updated = replace(
            loop,
            status=LoopStatus.completed.value,
            updated_at=timestamp(now),
            verification_results=verification_results,
            artifacts=artifacts,
            outcome={"status": "completed"},
        )
        self._write(updated)
        if evidence:
            self._event(
                updated,
                LoopEventType.verification_added.value,
                actor,
                evidence,
                payload={"evidence": evidence},
                now=now,
            )
        if artifact:
            self._event(
                updated,
                LoopEventType.artifact_added.value,
                actor,
                artifact,
                payload={"artifact": artifact},
                now=now,
            )
        self._event(updated, LoopEventType.completed.value, actor, "loop completed", now=now)
        return updated

    def fail(
        self,
        loop_id: str,
        *,
        reason: str,
        evidence: str | None = None,
        actor: str = "cli",
        now: datetime | None = None,
    ) -> LoopRun:
        if not reason.strip():
            raise LoopTransitionError("loop failure requires reason")
        loop = self.get(loop_id)
        validate_transition(loop.status, LoopStatus.failed.value)
        verification_results = list(loop.verification_results)
        if evidence:
            verification_results.append({"timestamp": timestamp(now), "evidence": evidence, "actor": actor})
        updated = replace(
            loop,
            status=LoopStatus.failed.value,
            updated_at=timestamp(now),
            verification_results=verification_results,
            outcome={"status": "failed", "reason": reason},
        )
        self._write(updated)
        if evidence:
            self._event(
                updated,
                LoopEventType.verification_added.value,
                actor,
                evidence,
                payload={"evidence": evidence},
                now=now,
            )
        self._event(
            updated,
            LoopEventType.failed.value,
            actor,
            reason,
            payload={"reason": reason},
            now=now,
        )
        return updated

    def block(
        self,
        loop_id: str,
        *,
        reason: str,
        actor: str = "cli",
        now: datetime | None = None,
    ) -> LoopRun:
        reason = _require_text(reason, "loop block reason")
        loop = self.get(loop_id)
        if loop.status == LoopStatus.blocked.value:
            return loop
        validate_transition(loop.status, LoopStatus.blocked.value)
        updated = replace(
            loop,
            status=LoopStatus.blocked.value,
            updated_at=timestamp(now),
            outcome={"status": "blocked", "reason": reason},
        )
        self._write(updated)
        self._event(
            updated,
            LoopEventType.status_changed.value,
            actor,
            reason,
            payload={"from": loop.status, "to": LoopStatus.blocked.value, "reason": reason},
            now=now,
        )
        return updated

    def add_verification_feedback(
        self,
        loop_id: str,
        feedback: LoopFeedback,
        *,
        actor: str = "cli",
        now: datetime | None = None,
    ) -> LoopRun:
        loop = self.get(loop_id)
        verification_results = [*loop.verification_results, feedback.to_dict()]
        updated = replace(
            loop,
            updated_at=timestamp(now),
            verification_results=verification_results,
        )
        self._write(updated)
        payload = {
            "feedback_id": feedback.feedback_id,
            "command": feedback.command,
            "status": feedback.status,
            "category": feedback.category,
            "exit_code": feedback.exit_code,
            "duration_ms": feedback.duration_ms,
        }
        if feedback.verifier_id:
            payload["verifier_id"] = feedback.verifier_id
        if feedback.contract_id:
            payload["contract_id"] = feedback.contract_id
        self._event(
            updated,
            LoopEventType.verification_added.value,
            actor,
            f"{feedback.category}: {feedback.command}",
            payload=payload,
            now=now,
        )
        return updated

    def open_human_gate(
        self,
        loop_id: str,
        *,
        gate_id: str,
        reason: str,
        trigger: str | None = None,
        actor: str = "cli",
        now: datetime | None = None,
    ) -> LoopRun:
        gate_id = _require_text(gate_id, "human gate id")
        reason = _require_text(reason, "human gate reason")
        loop = self.get(loop_id)
        _validate_contract_gate(loop, gate_id)
        open_gates = _open_human_gates(loop)
        if any(gate.get("id") == gate_id for gate in open_gates):
            raise LoopTransitionError(f"human gate already open: {gate_id}")
        current_time = timestamp(now)
        gate = {
            "id": gate_id,
            "reason": reason,
            "trigger": trigger or _contract_gate_trigger(loop, gate_id),
            "opened_at": current_time,
            "actor": actor,
        }
        metadata = dict(loop.metadata)
        metadata["open_human_gates"] = [*open_gates, gate]
        metadata.setdefault("resolved_human_gates", _resolved_human_gates(loop))
        updated = replace(loop, updated_at=current_time, metadata=metadata)
        self._write(updated)
        self._event(
            updated,
            LoopEventType.human_gate_opened.value,
            actor,
            f"human gate opened: {gate_id}",
            payload=gate,
            now=now,
        )
        return updated

    def approve_human_gate(
        self,
        loop_id: str,
        *,
        gate_id: str,
        note: str,
        evidence: str | None = None,
        actor: str = "cli",
        now: datetime | None = None,
    ) -> LoopRun:
        return self._resolve_human_gate(
            loop_id,
            gate_id=gate_id,
            decision="approved",
            note=note,
            evidence=evidence,
            actor=actor,
            now=now,
        )

    def reject_human_gate(
        self,
        loop_id: str,
        *,
        gate_id: str,
        reason: str,
        evidence: str | None = None,
        actor: str = "cli",
        now: datetime | None = None,
    ) -> LoopRun:
        return self._resolve_human_gate(
            loop_id,
            gate_id=gate_id,
            decision="rejected",
            note=reason,
            evidence=evidence,
            actor=actor,
            now=now,
        )

    def _resolve_human_gate(
        self,
        loop_id: str,
        *,
        gate_id: str,
        decision: str,
        note: str,
        evidence: str | None,
        actor: str,
        now: datetime | None,
    ) -> LoopRun:
        gate_id = _require_text(gate_id, "human gate id")
        note = _require_text(note, "human gate resolution note")
        loop = self.get(loop_id)
        open_gates = _open_human_gates(loop)
        gate = next((row for row in open_gates if row.get("id") == gate_id), None)
        if gate is None:
            raise LoopTransitionError(f"human gate not open: {gate_id}")
        remaining = [row for row in open_gates if row.get("id") != gate_id]
        current_time = timestamp(now)
        resolved = {
            **gate,
            "decision": decision,
            "note": note,
            "evidence": evidence or None,
            "closed_at": current_time,
            "resolved_by": actor,
        }
        metadata = dict(loop.metadata)
        metadata["open_human_gates"] = remaining
        metadata["resolved_human_gates"] = [*_resolved_human_gates(loop), resolved]
        updated = replace(loop, updated_at=current_time, metadata=metadata)
        self._write(updated)
        event_type = (
            LoopEventType.human_gate_approved.value
            if decision == "approved"
            else LoopEventType.human_gate_rejected.value
        )
        self._event(
            updated,
            event_type,
            actor,
            f"human gate {decision}: {gate_id}",
            payload=resolved,
            now=now,
        )
        return updated

    def _path(self, loop_id: str) -> Path:
        return self.loops_dir / f"{loop_id}.json"

    def _write(self, loop: LoopRun) -> None:
        self.loops_dir.mkdir(parents=True, exist_ok=True)
        path = self._path(loop.loop_id)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(loop.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(path)

    def _event(
        self,
        loop: LoopRun,
        event_type: str,
        actor: str,
        summary: str,
        *,
        payload: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        append_loop_event(
            self.brain_dir,
            LoopEvent(
                event_id=make_event_id(now),
                loop_id=loop.loop_id,
                timestamp=timestamp(now),
                event_type=event_type,
                actor=actor,
                summary=summary,
                payload=dict(payload or {}),
            ),
        )


def _require_text(value: str, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise LoopTransitionError(f"{label} is required")
    return text


def _open_human_gates(loop: LoopRun) -> list[dict[str, Any]]:
    rows = loop.metadata.get("open_human_gates")
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _resolved_human_gates(loop: LoopRun) -> list[dict[str, Any]]:
    rows = loop.metadata.get("resolved_human_gates")
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _contract_human_gates(loop: LoopRun) -> list[dict[str, Any]]:
    rows = loop.metadata.get("contract_human_gates")
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _validate_contract_gate(loop: LoopRun, gate_id: str) -> None:
    gates = _contract_human_gates(loop)
    if not gates:
        return
    if not any(str(gate.get("id") or "") == gate_id for gate in gates):
        raise LoopTransitionError(f"human gate not defined by contract: {gate_id}")


def _contract_gate_trigger(loop: LoopRun, gate_id: str) -> str | None:
    for gate in _contract_human_gates(loop):
        if str(gate.get("id") or "") == gate_id:
            trigger = gate.get("trigger")
            return str(trigger) if trigger is not None else None
    return None
