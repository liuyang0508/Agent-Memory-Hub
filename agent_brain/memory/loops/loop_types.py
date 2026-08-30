from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class LoopError(Exception):
    """Base exception for loop ledger errors."""


class LoopNotFoundError(LoopError):
    """Raised when a loop id does not exist."""


class LoopTransitionError(LoopError):
    """Raised when a state transition or completion gate is invalid."""


class LoopStatus(str, Enum):
    created = "created"
    running = "running"
    blocked = "blocked"
    failed = "failed"
    completed = "completed"
    cancelled = "cancelled"


class LoopEventType(str, Enum):
    created = "created"
    status_changed = "status_changed"
    checkpoint_added = "checkpoint_added"
    verification_added = "verification_added"
    artifact_added = "artifact_added"
    human_gate_opened = "human_gate_opened"
    human_gate_approved = "human_gate_approved"
    human_gate_rejected = "human_gate_rejected"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


@dataclass(frozen=True)
class LoopRun:
    loop_id: str
    created_at: str
    updated_at: str
    status: str
    goal: str
    trigger: dict[str, Any] = field(default_factory=lambda: {"kind": "manual"})
    project: str | None = None
    cwd: str | None = None
    adapter: str | None = None
    session_id: str | None = None
    budget: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    verification_plan: list[str] = field(default_factory=list)
    verification_results: list[dict[str, Any]] = field(default_factory=list)
    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    outcome: dict[str, Any] | None = None
    memory_candidates: list[dict[str, Any]] = field(default_factory=list)
    sensitivity: str = "internal"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LoopRun:
        return cls(
            loop_id=str(data["loop_id"]),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            status=str(data["status"]),
            goal=str(data["goal"]),
            trigger=dict(data.get("trigger") or {"kind": "manual"}),
            project=_optional_str(data.get("project")),
            cwd=_optional_str(data.get("cwd")),
            adapter=_optional_str(data.get("adapter")),
            session_id=_optional_str(data.get("session_id")),
            budget=dict(data.get("budget") or {}),
            context=dict(data.get("context") or {}),
            metadata=dict(data.get("metadata") or {}),
            verification_plan=[str(item) for item in data.get("verification_plan") or []],
            verification_results=[dict(item) for item in data.get("verification_results") or []],
            checkpoints=[dict(item) for item in data.get("checkpoints") or []],
            artifacts=[dict(item) for item in data.get("artifacts") or []],
            outcome=dict(data["outcome"]) if isinstance(data.get("outcome"), dict) else None,
            memory_candidates=[dict(item) for item in data.get("memory_candidates") or []],
            sensitivity=str(data.get("sensitivity") or "internal"),
        )


@dataclass(frozen=True)
class LoopEvent:
    event_id: str
    loop_id: str
    timestamp: str
    event_type: str
    actor: str = "cli"
    summary: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LoopEvent:
        return cls(
            event_id=str(data["event_id"]),
            loop_id=str(data["loop_id"]),
            timestamp=str(data["timestamp"]),
            event_type=str(data["event_type"]),
            actor=str(data.get("actor") or "cli"),
            summary=str(data.get("summary") or ""),
            payload=dict(data.get("payload") or {}),
        )


def timestamp(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def make_loop_id(goal: str, now: datetime | None = None) -> str:
    stamp = _stamp(now)
    slug = _slug(goal)
    return f"loop-{stamp}-{slug}-{uuid.uuid4().hex[:8]}"


def make_event_id(now: datetime | None = None) -> str:
    return f"lev-{_stamp(now)}-{uuid.uuid4().hex[:8]}"


def bounded_trigger(trigger: dict[str, Any] | None) -> dict[str, Any]:
    if not trigger:
        return {"kind": "manual"}
    return {"kind": str(trigger.get("kind") or "manual")}


def _stamp(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _slug(value: str) -> str:
    normalized = []
    for ch in value.strip().lower():
        if "a" <= ch <= "z" or "0" <= ch <= "9":
            normalized.append(ch)
        else:
            normalized.append("-")
    parts = [part for part in "".join(normalized).split("-") if part]
    slug = "-".join(parts)[:48].strip("-")
    return slug or "loop"


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
