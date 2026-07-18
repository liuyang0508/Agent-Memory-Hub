"""Per-adapter rollout stages and kill-switch decisions."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Literal
import uuid

from .lifecycle_records import LifecycleReasonCode, record_lifecycle_event


RELEASE_CONTROL_SCHEMA_VERSION = "amh-adapter-release-controls/v1"
RELEASE_CONTROLS_RELATIVE_PATH = "runtime/adapter-release-controls.json"
ReleaseStage = Literal["shadow", "canary", "default", "disabled"]
ReleaseDecisionName = Literal["enabled", "shadow", "canary_excluded", "disabled"]

_STAGES = frozenset({"shadow", "canary", "default", "disabled"})
_ALLOWED_TRANSITIONS: dict[str | None, frozenset[str]] = {
    None: frozenset({"shadow", "disabled"}),
    "shadow": frozenset({"shadow", "canary", "disabled"}),
    "canary": frozenset({"shadow", "canary", "default", "disabled"}),
    "default": frozenset({"shadow", "default", "disabled"}),
    "disabled": frozenset({"shadow", "disabled"}),
}


@dataclass(frozen=True)
class AdapterReleaseControl:
    adapter: str
    stage: ReleaseStage
    cohort_percent: int
    updated_at: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AdapterReleaseResult:
    status: Literal["passed", "blocked"]
    reason_code: LifecycleReasonCode
    control: AdapterReleaseControl
    previous_stage: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AdapterReleaseDecision:
    decision: ReleaseDecisionName
    control: AdapterReleaseControl | None
    bucket: int | None = None


def release_controls_path(brain_dir: Path) -> Path:
    return Path(brain_dir) / RELEASE_CONTROLS_RELATIVE_PATH


def get_adapter_release_control(
    brain_dir: Path,
    adapter: str,
) -> AdapterReleaseControl | None:
    payload = _read_payload(release_controls_path(brain_dir))
    controls = payload.get("controls")
    if not isinstance(controls, dict):
        return None
    data = controls.get(adapter)
    if not isinstance(data, dict):
        return None
    return _parse_control(adapter, data)


def set_adapter_release(
    brain_dir: Path,
    adapter: str,
    stage: ReleaseStage,
    *,
    cohort_percent: int | None = None,
    reason: str = "",
    now: datetime | None = None,
) -> AdapterReleaseResult:
    """Persist an ordered rollout transition and its provenance."""

    from .registry import resolve_adapter_name

    canonical, _alias = resolve_adapter_name(adapter)
    if stage not in _STAGES:
        raise ValueError(f"unsupported release stage: {stage}")
    previous = get_adapter_release_control(brain_dir, canonical)
    previous_stage = previous.stage if previous else None
    percent = _cohort_percent(stage, cohort_percent)
    control = AdapterReleaseControl(
        adapter=canonical,
        stage=stage,
        cohort_percent=percent,
        updated_at=_timestamp(now),
        reason=_safe_reason(reason),
    )
    if stage not in _ALLOWED_TRANSITIONS[previous_stage]:
        record_lifecycle_event(
            brain_dir,
            adapter=canonical,
            action="release",
            status="blocked",
            reason_code="INVALID_PROMOTION",
            cohort=stage,
            now=now,
        )
        return AdapterReleaseResult(
            status="blocked",
            reason_code="INVALID_PROMOTION",
            control=previous or control,
            previous_stage=previous_stage,
        )
    path = release_controls_path(brain_dir)
    payload = _read_payload(path)
    controls = payload.get("controls")
    if not isinstance(controls, dict):
        controls = {}
    controls[canonical] = control.to_dict()
    output = {
        "schema_version": RELEASE_CONTROL_SCHEMA_VERSION,
        "controls": controls,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(output, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    os.replace(temporary, path)
    path.chmod(0o600)
    record_lifecycle_event(
        brain_dir,
        adapter=canonical,
        action="release",
        status="passed",
        reason_code="OK",
        cohort=stage,
        now=now,
    )
    return AdapterReleaseResult(
        status="passed",
        reason_code="OK",
        control=control,
        previous_stage=previous_stage,
    )


def adapter_release_decision(
    brain_dir: Path,
    adapter: str,
    *,
    session_id: str | None,
) -> AdapterReleaseDecision:
    """Return a deterministic hook decision without affecting core CLI/MCP."""

    control = get_adapter_release_control(brain_dir, adapter)
    if control is None or control.stage == "default":
        return AdapterReleaseDecision("enabled", control)
    if control.stage == "disabled":
        return AdapterReleaseDecision("disabled", control)
    if control.stage == "shadow":
        return AdapterReleaseDecision("shadow", control)
    if not session_id:
        return AdapterReleaseDecision("canary_excluded", control)
    bucket = _bucket(adapter, session_id)
    decision: ReleaseDecisionName = (
        "enabled" if bucket < control.cohort_percent else "canary_excluded"
    )
    return AdapterReleaseDecision(decision, control, bucket)


def _read_payload(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": RELEASE_CONTROL_SCHEMA_VERSION, "controls": {}}
    if not isinstance(payload, dict) or payload.get("schema_version") != RELEASE_CONTROL_SCHEMA_VERSION:
        return {"schema_version": RELEASE_CONTROL_SCHEMA_VERSION, "controls": {}}
    return payload


def _parse_control(adapter: str, data: dict[str, object]) -> AdapterReleaseControl | None:
    stage = str(data.get("stage") or "")
    percent = data.get("cohort_percent")
    if stage not in _STAGES or not isinstance(percent, int) or isinstance(percent, bool):
        return None
    if not 0 <= percent <= 100:
        return None
    return AdapterReleaseControl(
        adapter=adapter,
        stage=stage,  # type: ignore[arg-type]
        cohort_percent=percent,
        updated_at=str(data.get("updated_at") or ""),
        reason=_safe_reason(str(data.get("reason") or "")),
    )


def _cohort_percent(stage: ReleaseStage, requested: int | None) -> int:
    if stage == "default":
        return 100
    if stage in {"shadow", "disabled"}:
        return 0
    value = 10 if requested is None else requested
    if isinstance(value, bool) or not 1 <= value <= 100:
        raise ValueError("canary cohort_percent must be between 1 and 100")
    return value


def _bucket(adapter: str, session_id: str) -> int:
    digest = hashlib.sha256(f"{adapter}:{session_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % 100


def _safe_reason(reason: str) -> str:
    value = " ".join(str(reason).split())
    return value[:256]


def _timestamp(now: datetime | None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate an adapter release control.")
    parser.add_argument("decision", choices=["decision"])
    parser.add_argument("--brain-dir", type=Path, required=True)
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--session")
    args = parser.parse_args(argv)
    decision = adapter_release_decision(
        args.brain_dir,
        args.adapter,
        session_id=args.session,
    )
    print(decision.decision)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AdapterReleaseControl",
    "AdapterReleaseDecision",
    "AdapterReleaseResult",
    "RELEASE_CONTROLS_RELATIVE_PATH",
    "RELEASE_CONTROL_SCHEMA_VERSION",
    "ReleaseDecisionName",
    "ReleaseStage",
    "adapter_release_decision",
    "get_adapter_release_control",
    "release_controls_path",
    "set_adapter_release",
]
