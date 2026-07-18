"""Read-only Cockpit summary for trusted cross-agent handoff."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_brain.agent_integrations.capabilities import AdapterCapability, capabilities_for_all
from agent_brain.agent_integrations.runtime_events import iter_runtime_events
from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.loops.loop_store import LoopStore
from agent_brain.memory.loops.loop_types import LoopRun
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.product.proactive_memory import list_candidates


def build_cockpit_summary(brain_dir: Path, *, now: datetime | None = None) -> dict[str, Any]:
    """Build the Web-safe Cockpit read model from existing local evidence."""

    root = Path(brain_dir)
    generated_at = _utc(now)
    items = _load_items(root)
    capabilities, adapter_status = _load_capabilities(root)
    return {
        "generated_at": generated_at.isoformat(),
        "brain_dir": str(root),
        "handoff_pack": _handoff_pack(items, generated_at),
        "key_decisions": _key_decisions(items, generated_at),
        "open_signals": _open_signals(items, generated_at),
        "trust_risks": _trust_risks(items, generated_at),
        "adapter_health": _adapter_health(capabilities, adapter_status),
        "loop_governance": _loop_governance(root),
        "memory_candidates": _memory_candidates(root),
        "cross_agent_timeline": _timeline(root),
    }


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _load_items(brain_dir: Path) -> list[tuple[MemoryItem, str]]:
    return list(ItemsStore(brain_dir / "items").iter_all())


def _load_capabilities(brain_dir: Path) -> tuple[list[AdapterCapability], str]:
    try:
        return capabilities_for_all(brain_dir), "ok"
    except Exception:
        return [], "degraded"


def _handoff_pack(items: list[tuple[MemoryItem, str]], now: datetime) -> list[dict[str, Any]]:
    candidates = [
        (item, body)
        for item, body in items
        if _type(item) in {"signal", "handoff"}
        or "handoff" in item.tags
        or "blocker" in item.tags
        or (_type(item) == "decision" and _age_days(item, now) <= 30)
    ]
    ranked = sorted(
        candidates,
        key=lambda pair: (_score_handoff(pair[0], pair[1], now), pair[0].created_at),
        reverse=True,
    )
    return [_memory_card(item, body, now) for item, body in ranked[:8]]


def _key_decisions(items: list[tuple[MemoryItem, str]], now: datetime) -> list[dict[str, Any]]:
    decisions = [(item, body) for item, body in items if _type(item) == "decision"]
    ranked = sorted(
        decisions,
        key=lambda pair: (_score_decision(pair[0], now), pair[0].created_at),
        reverse=True,
    )
    return [_memory_card(item, body, now) for item, body in ranked[:8]]


def _open_signals(items: list[tuple[MemoryItem, str]], now: datetime) -> list[dict[str, Any]]:
    signals = [(item, body) for item, body in items if _type(item) in {"signal", "handoff"}]
    ranked = sorted(
        signals,
        key=lambda pair: (_score_handoff(pair[0], pair[1], now), pair[0].created_at),
        reverse=True,
    )
    return [_memory_card(item, body, now) for item, body in ranked[:8]]


def _trust_risks(items: list[tuple[MemoryItem, str]], now: datetime) -> list[dict[str, Any]]:
    cards = [_memory_card(item, body, now) for item, body in items]
    risks = [card for card in cards if card["risk_reasons"]]
    return sorted(
        risks,
        key=lambda card: (len(card["risk_reasons"]), card["created_at"]),
        reverse=True,
    )[:10]


def _adapter_health(capabilities: list[AdapterCapability], status: str) -> dict[str, Any]:
    priority_names = {"codex", "claude_code", "qoder", "wukong", "hermes_agent"}
    priority = [
        _adapter_priority_row(cap)
        for cap in capabilities
        if cap.name in priority_names or cap.runtime_observed or cap.verified
    ]
    return {
        "status": status,
        "total": len(capabilities),
        "install_ready": sum(1 for cap in capabilities if cap.support_level == "install-ready"),
        "wip": sum(1 for cap in capabilities if cap.status == "wip"),
        "verified": sum(1 for cap in capabilities if cap.verified),
        "priority": priority[:8],
    }


def _adapter_priority_row(cap: AdapterCapability) -> dict[str, Any]:
    data = cap.to_dict()
    data["next_action"] = _adapter_next_action(cap)
    data["onboarding_url"] = f"/api/adapters/{cap.name}/doctor"
    return data


def _adapter_next_action(cap: AdapterCapability) -> str:
    if cap.release_control and cap.release_control.get("stage") == "disabled":
        return "enable-shadow"
    if cap.verified:
        return "verified"
    if not cap.states["implemented"]:
        return "unsupported"
    if not cap.states["installed"]:
        return "install"
    if not cap.states["configured"] or not cap.states["doctor_passed"]:
        return "repair"
    stale_reasons = cap.evidence_freshness.get("stale_reasons")
    if isinstance(stale_reasons, list) and stale_reasons:
        return "verify"
    if not cap.states["runtime_observed"]:
        return "wait-runtime"
    if not cap.states["context_injected"]:
        return "trigger-recall"
    return "verify"


def _loop_governance(brain_dir: Path) -> dict[str, Any]:
    try:
        loops = LoopStore(brain_dir).list()
    except Exception:
        return {
            "status": "degraded",
            "total": 0,
            "contract_loops": 0,
            "ready": 0,
            "blocked": 0,
            "open_human_gates": 0,
            "recent": [],
        }

    contract_loops = [loop for loop in loops if loop.metadata.get("contract_id")]
    rows = [_loop_governance_row(loop) for loop in contract_loops]
    return {
        "status": "ok",
        "total": len(loops),
        "contract_loops": len(contract_loops),
        "ready": sum(1 for row in rows if row["completion_readiness"] == "ready"),
        "blocked": sum(1 for row in rows if row["completion_readiness"] == "blocked"),
        "open_human_gates": sum(len(_open_human_gates(loop)) for loop in contract_loops),
        "recent": sorted(rows, key=lambda row: row["updated_at"], reverse=True)[:8],
    }


def _loop_governance_row(loop: LoopRun) -> dict[str, Any]:
    readiness = _loop_readiness(loop)
    return {
        "loop_id": loop.loop_id,
        "status": loop.status,
        "goal": loop.goal,
        "updated_at": loop.updated_at,
        "contract_id": loop.metadata.get("contract_id"),
        "completion_readiness": readiness,
        "open_human_gates": len(_open_human_gates(loop)),
    }


def _loop_readiness(loop: LoopRun) -> str:
    if _open_human_gates(loop):
        return "blocked"
    required = _required_verifiers(loop)
    if not required:
        return "blocked"
    latest_by_id: dict[str, dict[str, Any]] = {}
    latest_by_command: dict[str, dict[str, Any]] = {}
    for row in loop.verification_results:
        if not row.get("feedback_id"):
            continue
        if row.get("verifier_id"):
            latest_by_id[str(row["verifier_id"])] = row
        latest_by_command[str(row.get("command") or "")] = row
    for verifier in required:
        verifier_id = str(verifier.get("id") or "")
        command = str(verifier.get("command") or "")
        feedback = latest_by_id.get(verifier_id) or latest_by_command.get(command)
        if not feedback or feedback.get("status") != "passed":
            return "blocked"
    return "ready"


def _required_verifiers(loop: LoopRun) -> list[dict[str, Any]]:
    rows = loop.metadata.get("contract_verifiers")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and row.get("required")]


def _open_human_gates(loop: LoopRun) -> list[dict[str, Any]]:
    rows = loop.metadata.get("open_human_gates")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _memory_candidates(brain_dir: Path) -> dict[str, Any]:
    try:
        data = list_candidates(brain_dir)
    except Exception:
        return {"pending": 0, "approved": 0, "rejected": 0, "top": []}
    return {
        "pending": data["pending"],
        "approved": data["approved"],
        "rejected": data["rejected"],
        "top": data["items"][:5],
    }


def _timeline(brain_dir: Path) -> list[dict[str, str | None]]:
    events = list(iter_runtime_events(brain_dir, limit=12))
    events.sort(key=lambda event: event.timestamp, reverse=True)
    return [event.to_dict() for event in events]


def _memory_card(item: MemoryItem, body: str, now: datetime) -> dict[str, Any]:
    return {
        "id": item.id,
        "type": _type(item),
        "title": item.title,
        "summary": item.summary,
        "created_at": item.created_at.isoformat(),
        "agent": item.agent,
        "project": item.project,
        "tags": list(item.tags),
        "confidence": item.confidence,
        "detail_uri": item.context_views.detail_uri or f"memory://items/{item.id}/body",
        "retrieve_hint": item.context_views.locator or item.summary,
        "trust_reasons": _trust_reasons(item, body, now),
        "risk_reasons": _risk_reasons(item, body, now),
    }


def _trust_reasons(item: MemoryItem, body: str, now: datetime) -> list[str]:
    reasons: list[str] = []
    item_type = _type(item)
    if item_type in {"signal", "handoff"}:
        reasons.append("open_signal")
    if item_type == "decision":
        reasons.append("decision")
    if item.support_count > 0:
        reasons.append("supported")
    if item.refs.files or item.refs.urls or item.refs.commits or item.refs.resources or item.refs.extractions:
        reasons.append("resource")
    if item.source.kind and item.source.kind != "manual":
        reasons.append("source")
    if item.confidence >= 0.8:
        reasons.append("confidence")
    if _age_days(item, now) <= 7:
        reasons.append("fresh")
    if "**来源**" in body or "source" in body.lower():
        reasons.append("trace")
    return reasons


def _risk_reasons(item: MemoryItem, body: str, now: datetime) -> list[str]:
    reasons: list[str] = []
    if item.confidence < 0.5:
        reasons.append("low_confidence")
    if item.contradict_count > 0:
        reasons.append("contested")
    if _is_stale(item, now):
        reasons.append("stale")
    lowered = " ".join([*item.tags, body]).lower()
    if "needs-review" in lowered or "needs_review" in lowered:
        reasons.append("needs_review")
    if "firewall" in lowered and ("exclude" in lowered or "excluded" in lowered):
        reasons.append("firewall_excluded")
    if item.superseded_by:
        reasons.append("superseded")
    return reasons


def _score_handoff(item: MemoryItem, body: str, now: datetime) -> float:
    score = 0.0
    if _type(item) in {"signal", "handoff"}:
        score += 10.0
    if "blocker" in item.tags or "阻塞" in body or "blocked" in body.lower():
        score += 3.0
    score += max(0.0, 7.0 - min(_age_days(item, now), 7.0))
    score += item.confidence
    return score


def _score_decision(item: MemoryItem, now: datetime) -> float:
    score = 4.0
    score += min(float(item.support_count), 5.0)
    score += item.confidence
    score -= min(_age_days(item, now) / 30.0, 4.0)
    score -= float(item.contradict_count)
    return score


def _is_stale(item: MemoryItem, now: datetime) -> bool:
    item_type = _type(item)
    stale_after = {
        "signal": 14,
        "handoff": 14,
        "episode": 30,
        "fact": 45,
        "decision": 90,
        "artifact": 120,
        "policy": 120,
        "skill": 180,
    }.get(item_type, 45)
    return _age_days(item, now) > stale_after


def _age_days(item: MemoryItem, now: datetime) -> float:
    created = item.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return max(0.0, (now - created.astimezone(timezone.utc)).total_seconds() / 86400)


def _type(item: MemoryItem) -> str:
    return str(item.type)
