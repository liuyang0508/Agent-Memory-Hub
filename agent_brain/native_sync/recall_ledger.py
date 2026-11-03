"""Hub-side recall ledger: per-asset signals and per-query traces. No bodies, ever.

Signals (delivered / cited / support / contradict / gain / confirmed) are the
mutable counters the decay stage multiplies in. Traces record how one query
was ranked and what happened to each candidate afterwards, so a member can see
"retrieved → ranked → packed → emitted → confirmed → cited" or where it stopped.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

TRACE_TTL = 30 * 86400
MAX_EVENTS = 64
# Per-candidate event history kept in the trace; beyond it only a count grows.
MAX_CANDIDATE_EVENTS = 32
STATES = ("candidate", "selected", "packed", "emitted", "confirmed", "cited")
DELIVERY_EVENTS = frozenset({"packed", "emitted", "confirmed", "budget", "covered"})
FEEDBACK_EVENTS = frozenset({"cite", "support", "contradict", "confirm"})
EVENTS = DELIVERY_EVENTS | FEEDBACK_EVENTS
# A delivery can only be reported for something the recall actually returned.
DELIVERABLE_STATES = frozenset({"selected", "packed", "emitted", "confirmed", "cited"})
SIGNAL_FIELDS = (
    "delivered",
    "confirmed",
    "cited",
    "support",
    "contradict",
    "gain",
    "last_delivered",
    "last_cited",
    "last_feedback",
    "confirmed_at",
)


def signals_for(
    db: sqlite3.Connection, tenant: str, asset_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """Current signal rows for the given assets; missing rows mean no signals."""
    result: dict[str, dict[str, Any]] = {}
    keys = list(dict.fromkeys(asset_ids))
    for start in range(0, len(keys), 400):
        chunk = keys[start : start + 400]
        rows = db.execute(
            "SELECT * FROM recall_signals WHERE tenant=? AND asset_id IN ("
            + ",".join("?" for _ in chunk)
            + ")",
            [tenant, *chunk],
        ).fetchall()
        for row in rows:
            result[row["asset_id"]] = {field: row[field] for field in SIGNAL_FIELDS}
    return result


def record_trace(db: sqlite3.Connection, tenant: str, trace: dict[str, Any]) -> None:
    """Persist one bounded trace and prune expired ones for the tenant."""
    now = float(trace["created"])
    db.execute(
        "INSERT OR REPLACE INTO recall_events VALUES(?,?,?,?,?,?,?,?,?)",
        (
            tenant,
            trace["trace_id"],
            now,
            trace["device"],
            trace["member"],
            trace["channel"],
            trace["query_digest"],
            trace.get("query_preview") or "",
            json.dumps(trace, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    db.execute("DELETE FROM recall_events WHERE tenant=? AND created<?", (tenant, now - TRACE_TTL))


def _advance(current: str, target: str) -> str:
    if current.startswith("dropped:"):
        return current
    try:
        return target if STATES.index(target) > STATES.index(current) else current
    except ValueError:
        return current


def _note_event(candidate: dict[str, Any], name: str, now: float, member: str) -> None:
    """Append to the bounded per-candidate history; past the cap only a counter grows."""
    events = candidate.setdefault("events", [])
    if len(events) >= MAX_CANDIDATE_EVENTS:
        candidate["events_overflow"] = int(candidate.get("events_overflow", 0)) + 1
        return
    events.append({"event": name, "at": round(now, 3), "by": member})


def _bump(
    db: sqlite3.Connection, tenant: str, asset_id: str, *, now: float, **changes: float
) -> None:
    db.execute(
        "INSERT OR IGNORE INTO recall_signals(tenant,asset_id) VALUES(?,?)", (tenant, asset_id)
    )
    for field, delta in changes.items():
        if field not in SIGNAL_FIELDS:
            raise ValueError("unknown signal")
        if field.startswith("last_") or field == "confirmed_at":
            db.execute(
                f"UPDATE recall_signals SET {field}=? WHERE tenant=? AND asset_id=?",
                (now, tenant, asset_id),
            )
        else:
            db.execute(
                f"UPDATE recall_signals SET {field}={field}+? WHERE tenant=? AND asset_id=?",
                (delta, tenant, asset_id),
            )


def apply_feedback(
    db: sqlite3.Connection,
    who: dict[str, Any],
    trace_id: str,
    events: list[dict[str, Any]],
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Apply delivery/feedback events to a trace this organization recorded.

    Delivery events must come from the device that ran the recall. Knowledge
    feedback (cite/support/contradict/confirm) may come from any member of the
    organization. An event for an asset that was not a candidate is refused.

    Counting is idempotent: packed/emitted/confirmed only advance the state
    and bump counters when the candidate has not reached that state yet, and
    each member's cite/support/contradict/confirm counts once per candidate.
    Repeats are accepted as no-ops (``noop``). An event the candidate's state
    does not allow (a delivery for something never returned, feedback on a
    dropped candidate, budget/covered after a delivery) is skipped and counted
    in ``ignored`` rather than failing the batch.
    """
    now = time.time() if now is None else now
    if not 1 <= len(events) <= MAX_EVENTS:
        raise ValueError("feedback must carry 1 to 64 events")
    row = db.execute(
        "SELECT * FROM recall_events WHERE tenant=? AND trace_id=?", (who["tenant"], trace_id)
    ).fetchone()
    if row is None:
        raise ValueError("unknown recall trace")
    trace = json.loads(row["payload"])
    candidates = {c["asset_id"]: c for c in trace.get("candidates", [])}
    states: dict[str, str] = {}
    applied = ignored = noop = 0
    for event in events:
        name = event.get("event")
        asset_id = str(event.get("asset_id") or "")
        candidate = candidates.get(asset_id)
        if name not in EVENTS or candidate is None:
            raise ValueError("feedback event does not match a trace candidate")
        if candidate["revision"] != event.get("revision"):
            raise ValueError("feedback revision does not match the recalled revision")
        if name in DELIVERY_EVENTS and row["device"] != who["device_id"]:
            raise ValueError("delivery events must come from the recalling device")
        current = candidate.get("state", "candidate")
        states[asset_id] = current
        if name in ("packed", "emitted", "confirmed"):
            if current not in DELIVERABLE_STATES:
                ignored += 1
                continue
            if STATES.index(current) >= STATES.index(name):
                noop += 1  # already there (or further): nothing to count again
                applied += 1
                continue
            candidate["state"] = name
            if name == "emitted":
                _bump(db, who["tenant"], asset_id, now=now, delivered=1, last_delivered=now)
            elif name == "confirmed":
                _bump(db, who["tenant"], asset_id, now=now, confirmed=1)
        elif name in ("budget", "covered"):
            if current not in ("candidate", "selected"):
                ignored += 1
                continue
            candidate["state"] = "dropped:" + name
        else:  # knowledge feedback
            if current.startswith("dropped:"):
                ignored += 1
                continue
            members = candidate.setdefault("feedback_by", {}).setdefault(name, [])
            if who["member_id"] in members:
                noop += 1
                applied += 1
                continue
            members.append(who["member_id"])
            if name == "cite":
                candidate["state"] = _advance(current, "cited")
                _bump(
                    db,
                    who["tenant"],
                    asset_id,
                    now=now,
                    cited=1,
                    support=1,
                    gain=0.1,
                    last_cited=now,
                    last_feedback=now,
                )
            elif name == "support":
                _bump(db, who["tenant"], asset_id, now=now, support=1, gain=0.1, last_feedback=now)
            elif name == "contradict":
                _bump(
                    db, who["tenant"], asset_id, now=now, contradict=1, gain=-0.2, last_feedback=now
                )
            elif name == "confirm":
                _bump(
                    db,
                    who["tenant"],
                    asset_id,
                    now=now,
                    support=1,
                    confirmed_at=now,
                    last_feedback=now,
                )
        applied += 1
        _note_event(candidate, name, now, who["member_id"])
        states[asset_id] = candidate["state"]
    db.execute(
        "UPDATE recall_events SET payload=? WHERE tenant=? AND trace_id=?",
        (json.dumps(trace, ensure_ascii=False, separators=(",", ":")), who["tenant"], trace_id),
    )
    return {
        "trace_id": trace_id,
        "applied": applied,
        "ignored": ignored,
        "noop": noop,
        "states": states,
    }


def summary(trace: dict[str, Any], *, top: int = 8) -> dict[str, Any]:
    """A listing row: counts, stage switches and the first candidates, no stage history."""
    candidates = trace.get("candidates", [])
    counts: dict[str, int] = {}
    for candidate in candidates:
        state = candidate.get("state", "candidate")
        counts[state] = counts.get(state, 0) + 1
    return {
        "trace_id": trace["trace_id"],
        "created": trace["created"],
        "channel": trace["channel"],
        "device": trace["device"],
        "member": trace["member"],
        "query_digest": trace["query_digest"],
        "query_preview": trace.get("query_preview", ""),
        "stages": trace.get("stages", {}),
        "retrieval": trace.get("retrieval", {}),
        "counts": {"candidates": len(candidates), **counts},
        "top": [
            {
                key: candidate.get(key)
                for key in (
                    "rank",
                    "final_rank",
                    "asset_id",
                    "revision",
                    "title",
                    "role",
                    "product",
                    "project",
                    "producer",
                    "routes",
                    "state",
                    "nature",
                )
            }
            for candidate in candidates[:top]
        ],
    }


def list_traces(
    db: sqlite3.Connection,
    tenant: str,
    *,
    device: str | None = None,
    trace_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Most recent traces first; one full trace when trace_id is given, summaries otherwise."""
    filters = ["tenant=?"]
    args: list[Any] = [tenant]
    if device is not None:
        filters.append("device=?")
        args.append(device)
    if trace_id is not None:
        filters.append("trace_id=?")
        args.append(trace_id)
    rows = db.execute(
        "SELECT payload FROM recall_events WHERE "
        + " AND ".join(filters)
        + " ORDER BY created DESC LIMIT ?",
        [*args, max(1, min(int(limit), 100))],
    ).fetchall()
    traces = [json.loads(row["payload"]) for row in rows]
    return traces if trace_id is not None else [summary(trace) for trace in traces]
