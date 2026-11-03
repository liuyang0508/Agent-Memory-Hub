"""Insight rollups over the recall ledger: daily counts that outlive the trace TTL.

Every number here is derived from recorded traces and the delivery/feedback
states reported against them; nothing is inferred and nothing from before the
ledger existed is backfilled. Days still inside the trace TTL are recomputed
from the raw traces on each refresh, because states keep moving as deliveries
and feedback arrive. A day is frozen the moment its traces expire, so from then
on the rollup is the only history. Counts, identifiers and titles only — never
memory text, never full prompts.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

DAY = "%Y-%m-%d"
# The linear delivery funnel. A candidate dropped after selection (budget /
# covered) still counts as selected; any other drop stays at "candidate".
FUNNEL = ("candidate", "selected", "packed", "emitted", "confirmed", "cited")
FUNNEL_LABELS = {
    "candidate": "命中",
    "selected": "入选",
    "packed": "已打包",
    "emitted": "已注入",
    "confirmed": "已送达（回执可见）",
    "cited": "已引用（显式反馈）",
}
POST_SELECTION_DROPS = frozenset({"budget", "covered"})
EMITTED = FUNNEL.index("emitted")
REFRESH_INTERVAL = 600.0
GAP_SCAN = 500
GAP_RECENT = 20
GAP_REPEATED = 10
TOP_ASSETS = 50
# Distinct recalling members / tasks tracked per asset and day; beyond it only counts grow.
MEMBERS_PER_ASSET_DAY = 200
TASKS_PER_ASSET_DAY = 500
# Distinct task digests tracked per (member, channel) and day.
SESSIONS_PER_ROW = 1000
TOP_PROJECTS = 20
UNKNOWN = "未知"
TITLE_CHARS = 80

STAGES = ("trigger", "retrieve", "deliver", "receipt", "cite", "feedback")
STAGE_LABELS = {
    "trigger": "触发",
    "retrieve": "检索与筛选",
    "deliver": "投放 / 读取",
    "receipt": "送达（回执）",
    "cite": "引用",
    "feedback": "确认 / 有效",
}
VALUE_LABELS = {
    "observed": "可观测",
    "explicit": "显式反馈",
    "inferred": "推断",
    "not_recorded": "未采集",
    "unobservable": "不可观测",
    "unavailable": "无入口",
    "n/a": "不适用",
}
# What each product surface can and cannot show. Rendered as-is so an empty
# cell on a page reads as "not observable" rather than "did not happen".
OBSERVABILITY: tuple[dict[str, Any], ...] = (
    {
        "channel": "hook",
        "label": "Claude Code（Hook）",
        "stages": {
            "trigger": "observed",
            "retrieve": "observed",
            "deliver": "observed",
            "receipt": "observed",
            "cite": "not_recorded",
            "feedback": "explicit",
        },
        "note": "打包、注入由本机 Hook 判定；回执指下一次 Hook 在转录里看到的标记，不等于用户采用。",
    },
    {
        "channel": "hook",
        "label": "Cowork 插件",
        "stages": {
            "trigger": "observed",
            "retrieve": "observed",
            "deliver": "observed",
            "receipt": "observed",
            "cite": "not_recorded",
            "feedback": "explicit",
        },
        "note": "与 Claude Code 共用 hook 通道，本期不单独区分。",
    },
    {
        "channel": "tool",
        "label": "Codex / ChatGPT 桌面（MCP）",
        "stages": {
            "trigger": "observed",
            "retrieve": "observed",
            "deliver": "not_recorded",
            "receipt": "unobservable",
            "cite": "unobservable",
            "feedback": "unavailable",
        },
        "note": "MCP 工具只读：读取全文尚未计数，也没有反馈入口。",
    },
    {
        "channel": "cli",
        "label": "命令行（amh-native）",
        "stages": {
            "trigger": "observed",
            "retrieve": "observed",
            "deliver": "n/a",
            "receipt": "n/a",
            "cite": "explicit",
            "feedback": "explicit",
        },
        "note": "recall-feedback 可显式上报引用与有效性。",
    },
)


def day_key(stamp: float) -> str:
    """Calendar day of a timestamp in the Hub's local time, like the dashboard."""
    return time.strftime(DAY, time.localtime(stamp))


def day_bounds(day: str) -> tuple[float, float]:
    """[start, end) timestamps of a local calendar day; DST days are longer or shorter."""
    start = datetime.strptime(day, DAY)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def depth(state: str) -> int:
    """Index of the deepest funnel stage a candidate reached; drops keep the stage they left."""
    if state.startswith("dropped:"):
        return 1 if state[8:] in POST_SELECTION_DROPS else 0
    try:
        return FUNNEL.index(state)
    except ValueError:
        return 0


def _blank_bucket() -> dict[str, Any]:
    """Counts kept per asking-side product and project inside a rollup row."""
    return {"traces": 0, "no_match": 0, "funnel": {stage: 0 for stage in FUNNEL}}


def _blank_recall() -> dict[str, Any]:
    return {
        "traces": 0,
        "no_match": 0,
        "degraded": 0,
        "candidates": 0,
        "query_chars": 0,
        "funnel": {stage: 0 for stage in FUNNEL},
        "modes": {},
        "reasons": {},
        "dropped": {},
        "devices": [],
        "products": {},
        "projects": {},
        "sessions": [],
    }


def _blank_asset() -> dict[str, Any]:
    return {
        "title": "",
        "product": None,
        "project": None,
        "kind": None,
        "producer": None,
        "producer_name": None,
        "funnel": {stage: 0 for stage in FUNNEL},
        "members": {},
        "tasks": {},
        "cross_member": 0,
        "last": 0.0,
    }


def _count(store: dict[str, int], key: str) -> None:
    store[key] = store.get(key, 0) + 1


def summarize(
    traces: Iterable[dict[str, Any]],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, dict[str, Any]]]:
    """Fold traces into per-(member, channel) and per-asset counters.

    ``funnel`` counts are cumulative: a candidate that was emitted also counts
    as selected and packed. ``members`` maps each recalling member to the
    deepest stage the asset reached for them. ``cross_member`` counts
    deliveries to someone other than the asset's known producer.
    """
    recall: dict[tuple[str, str], dict[str, Any]] = {}
    assets: dict[str, dict[str, Any]] = {}
    for trace in traces:
        member = str(trace.get("member") or "")
        channel = str(trace.get("channel") or "")
        row = recall.setdefault((member, channel), _blank_recall())
        candidates = trace.get("candidates") or []
        raw_asking = trace.get("asking")
        asking = raw_asking if isinstance(raw_asking, dict) else {}
        product = str(asking.get("product") or "")
        project = str(asking.get("project") or "")
        session = str(asking.get("session_digest") or "")
        if session and session not in row["sessions"] and len(row["sessions"]) < SESSIONS_PER_ROW:
            row["sessions"].append(session)
        row["traces"] += 1
        row["candidates"] += len(candidates)
        row["query_chars"] += int(trace.get("query_chars") or 0)
        retrieval = trace.get("retrieval") or {}
        _count(row["modes"], str(retrieval.get("mode") or "unknown"))
        reasons = [str(reason) for reason in retrieval.get("reasons") or []]
        if reasons:
            row["degraded"] += 1
        for reason in reasons:
            _count(row["reasons"], reason)
        device = str(trace.get("device") or "")
        if device and device not in row["devices"]:
            row["devices"].append(device)
        created = float(trace.get("created") or 0)
        matched = False
        delta = {stage: 0 for stage in FUNNEL}
        for candidate in candidates:
            state = str(candidate.get("state") or "candidate")
            reached = depth(state)
            matched = matched or reached >= 1
            for index, stage in enumerate(FUNNEL):
                if index <= reached:
                    delta[stage] += 1
            if state.startswith("dropped:"):
                _count(row["dropped"], state[8:] or "unknown")
            asset_id = str(candidate.get("asset_id") or "")
            if not asset_id:
                continue
            item = assets.setdefault(asset_id, _blank_asset())
            if created >= item["last"]:
                producer = candidate.get("producer") or {}
                item["last"] = created
                item["title"] = str(candidate.get("title") or "")[:TITLE_CHARS]
                item["product"] = candidate.get("product")
                item["project"] = candidate.get("project")
                item["kind"] = candidate.get("kind")
                item["producer"] = (
                    None if producer.get("unknown") else producer.get("member_id") or None
                )
                item["producer_name"] = producer.get("name")
            for index, stage in enumerate(FUNNEL):
                if index <= reached:
                    item["funnel"][stage] += 1
            if member and (
                member in item["members"] or len(item["members"]) < MEMBERS_PER_ASSET_DAY
            ):
                item["members"][member] = max(int(item["members"].get(member, -1)), reached)
            if session and (session in item["tasks"] or len(item["tasks"]) < TASKS_PER_ASSET_DAY):
                item["tasks"][session] = max(int(item["tasks"].get(session, -1)), reached)
            if reached >= EMITTED and item["producer"] and member != item["producer"]:
                item["cross_member"] += 1
        for bucket in (
            row,
            row["products"].setdefault(product, _blank_bucket()),
            row["projects"].setdefault(project, _blank_bucket()),
        ):
            if bucket is not row:
                bucket["traces"] += 1
            if not matched:
                bucket["no_match"] += 1
            for stage in FUNNEL:
                bucket["funnel"][stage] += delta[stage]
    return recall, assets


def _dump(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def rollup_day(db: sqlite3.Connection, tenant: str, day: str, *, frozen: bool = False) -> int:
    """Recompute one day's rows from its raw traces; a frozen day is never touched again."""
    if db.execute(
        "SELECT 1 FROM recall_daily WHERE tenant=? AND day=? AND frozen=1 LIMIT 1", (tenant, day)
    ).fetchone():
        return 0
    start, end = day_bounds(day)
    traces: list[dict[str, Any]] = []
    for row in db.execute(
        "SELECT payload FROM recall_events WHERE tenant=? AND created>=? AND created<?",
        (tenant, start, end),
    ):
        try:
            traces.append(json.loads(row["payload"]))
        except (TypeError, ValueError):
            continue
    recall, assets = summarize(traces)
    db.execute("DELETE FROM recall_daily WHERE tenant=? AND day=?", (tenant, day))
    db.execute("DELETE FROM asset_daily WHERE tenant=? AND day=?", (tenant, day))
    flag = 1 if frozen else 0
    db.executemany(
        "INSERT INTO recall_daily VALUES(?,?,?,?,?,?)",
        [
            (tenant, day, member, channel, _dump(payload), flag)
            for (member, channel), payload in recall.items()
        ],
    )
    db.executemany(
        "INSERT INTO asset_daily VALUES(?,?,?,?,?)",
        [(tenant, day, asset_id, _dump(payload), flag) for asset_id, payload in assets.items()],
    )
    return len(traces)


def refresh(db: sqlite3.Connection, tenant: str) -> list[str]:
    """Recompute every unfrozen day that still has raw traces."""
    days = sorted(
        {
            day_key(float(row["created"]))
            for row in db.execute("SELECT created FROM recall_events WHERE tenant=?", (tenant,))
        }
    )
    for day in days:
        rollup_day(db, tenant, day)
    return days


def expire(db: sqlite3.Connection, tenant: str, *, now: float, ttl: float) -> list[str]:
    """Freeze and delete whole days whose traces have all passed the TTL.

    Purging by whole days keeps a day's rollup computed from all of its traces;
    a trace may therefore outlive the TTL by at most one day. Stragglers
    recorded into an already frozen day are deleted without being counted.
    """
    cutoff = now - ttl
    stale = db.execute(
        "SELECT created FROM recall_events WHERE tenant=? AND created<?", (tenant, cutoff)
    ).fetchall()
    frozen: list[str] = []
    for day in sorted({day_key(float(row["created"])) for row in stale}):
        start, end = day_bounds(day)
        if end > cutoff:
            continue
        rollup_day(db, tenant, day, frozen=True)
        db.execute(
            "DELETE FROM recall_events WHERE tenant=? AND created>=? AND created<?",
            (tenant, start, end),
        )
        frozen.append(day)
    return frozen


def _merge_recall(target: dict[str, Any], payload: dict[str, Any]) -> None:
    for key in ("traces", "no_match", "degraded", "candidates", "query_chars"):
        target[key] += int(payload.get(key) or 0)
    for stage in FUNNEL:
        target["funnel"][stage] += int((payload.get("funnel") or {}).get(stage) or 0)
    for key in ("modes", "reasons", "dropped"):
        for name, count in (payload.get(key) or {}).items():
            target[key][name] = target[key].get(name, 0) + int(count)
    for key in ("products", "projects"):
        for name, bucket in (payload.get(key) or {}).items():
            into = target[key].setdefault(name, _blank_bucket())
            into["traces"] += int(bucket.get("traces") or 0)
            into["no_match"] += int(bucket.get("no_match") or 0)
            for stage in FUNNEL:
                into["funnel"][stage] += int((bucket.get("funnel") or {}).get(stage) or 0)
    for device in payload.get("devices") or []:
        if device not in target["devices"]:
            target["devices"].append(device)
    for session in payload.get("sessions") or []:
        if session not in target["sessions"]:
            target["sessions"].append(session)


def _merge_asset(target: dict[str, Any], payload: dict[str, Any]) -> None:
    if float(payload.get("last") or 0) >= float(target["last"]):
        for key in ("title", "product", "project", "kind", "producer", "producer_name", "last"):
            target[key] = payload.get(key)
    for stage in FUNNEL:
        target["funnel"][stage] += int((payload.get("funnel") or {}).get(stage) or 0)
    for member, reached in (payload.get("members") or {}).items():
        target["members"][member] = max(int(target["members"].get(member, -1)), int(reached))
    for task, reached in (payload.get("tasks") or {}).items():
        target["tasks"][task] = max(int(target["tasks"].get(task, -1)), int(reached))
    target["cross_member"] += int(payload.get("cross_member") or 0)


def _public_counts(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "traces": row["traces"],
        "no_match": row["no_match"],
        "degraded": row["degraded"],
        "candidates": row["candidates"],
        "selected": row["funnel"]["selected"],
        "emitted": row["funnel"]["emitted"],
        "confirmed": row["funnel"]["confirmed"],
        "cited": row["funnel"]["cited"],
        "devices": len(row["devices"]),
        "tasks": len(row["sessions"]),
    }


def _bucket_counts(name: str, bucket: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name or UNKNOWN,
        "traces": bucket["traces"],
        "no_match": bucket["no_match"],
        "selected": bucket["funnel"]["selected"],
        "emitted": bucket["funnel"]["emitted"],
        "confirmed": bucket["funnel"]["confirmed"],
        "cited": bucket["funnel"]["cited"],
    }


def _buckets(store: dict[str, dict[str, Any]], limit: int | None = None) -> list[dict[str, Any]]:
    # Busiest first; among equals the unknown bucket goes last, then by name.
    ranked = sorted(store.items(), key=lambda item: (-item[1]["traces"], item[0] == "", item[0]))
    return [_bucket_counts(name, bucket) for name, bucket in ranked[:limit]]


def _ranked(store: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {"name": name, "count": count}
        for name, count in sorted(store.items(), key=lambda item: (-item[1], item[0]))
    ]


def _name(members: dict[str, str], member: str) -> str:
    return members.get(member) or (member[:8] + "…" if len(member) > 8 else member) or "未知"


def report(
    db: sqlite3.Connection,
    tenant: str,
    *,
    days: int,
    now: float,
    preview_on: bool,
    members: dict[str, str],
) -> dict[str, Any]:
    """Assemble the insight page data for a window of whole days ending today."""
    window = [day_key(now - offset * 86400) for offset in range(days - 1, -1, -1)]
    start_day, end_day = window[0], window[-1]
    total = _blank_recall()
    by_day = {day: _blank_recall() for day in window}
    by_channel: dict[str, dict[str, Any]] = {}
    by_member: dict[str, dict[str, Any]] = {}
    frozen_days: set[str] = set()
    live_days: set[str] = set()
    for row in db.execute(
        "SELECT day,member,channel,payload,frozen FROM recall_daily "
        "WHERE tenant=? AND day>=? AND day<=?",
        (tenant, start_day, end_day),
    ):
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            continue
        (frozen_days if row["frozen"] else live_days).add(row["day"])
        _merge_recall(total, payload)
        _merge_recall(by_day.setdefault(row["day"], _blank_recall()), payload)
        _merge_recall(by_channel.setdefault(row["channel"], _blank_recall()), payload)
        _merge_recall(by_member.setdefault(row["member"], _blank_recall()), payload)

    assets: dict[str, dict[str, Any]] = {}
    for row in db.execute(
        "SELECT asset_id,payload FROM asset_daily WHERE tenant=? AND day>=? AND day<=?",
        (tenant, start_day, end_day),
    ):
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            continue
        _merge_asset(assets.setdefault(row["asset_id"], _blank_asset()), payload)

    active_members = {member for member, row in by_member.items() if row["traces"]}
    used_assets = [a for a in assets.values() if a["funnel"]["emitted"]]
    top_assets = sorted(
        assets.items(),
        key=lambda item: (
            -item[1]["funnel"]["cited"],
            -item[1]["funnel"]["confirmed"],
            -item[1]["funnel"]["emitted"],
            -item[1]["funnel"]["selected"],
            -item[1]["funnel"]["candidate"],
            item[0],
        ),
    )[:TOP_ASSETS]

    producers: dict[str, dict[str, Any]] = {}
    for asset_id, item in assets.items():
        producer = item.get("producer")
        if not producer:
            continue
        entry = producers.setdefault(
            producer,
            {
                "assets_hit": set(),
                "assets_used": set(),
                "funnel": {stage: 0 for stage in FUNNEL},
                "consumers": set(),
                "tasks": set(),
                "cross_member": 0,
            },
        )
        entry["assets_hit"].add(asset_id)
        if item["funnel"]["emitted"]:
            entry["assets_used"].add(asset_id)
        for stage in FUNNEL:
            entry["funnel"][stage] += item["funnel"][stage]
        entry["cross_member"] += item["cross_member"]
        for member, reached in item["members"].items():
            if member != producer and int(reached) >= EMITTED:
                entry["consumers"].add(member)
        for task, reached in item["tasks"].items():
            if int(reached) >= EMITTED:
                entry["tasks"].add(task)

    since_rollup = db.execute(
        "SELECT min(day) FROM recall_daily WHERE tenant=?", (tenant,)
    ).fetchone()[0]
    since_trace = db.execute(
        "SELECT min(created) FROM recall_events WHERE tenant=?", (tenant,)
    ).fetchone()[0]
    starts = [day for day in (since_rollup, since_trace and day_key(float(since_trace))) if day]
    collection_since = min(starts) if starts else None

    gaps_since = now - days * 86400
    repeated: dict[str, dict[str, Any]] = {}
    recent: list[dict[str, Any]] = []
    for row in db.execute(
        "SELECT created,channel,member,query_digest,query_preview FROM recall_events "
        "WHERE tenant=? AND created>=? "
        "AND json_extract(payload,'$.candidates[0].final_rank') IS NULL "
        "ORDER BY created DESC LIMIT ?",
        (tenant, gaps_since, GAP_SCAN),
    ):
        preview = str(row["query_preview"] or "") if preview_on else ""
        entry = {
            "created": float(row["created"]),
            "channel": row["channel"],
            "member": _name(members, str(row["member"] or "")),
            "query_digest": str(row["query_digest"] or "")[:16],
            "preview": preview,
        }
        if len(recent) < GAP_RECENT:
            recent.append(entry)
        group = repeated.setdefault(
            str(row["query_digest"] or ""),
            {"query_digest": entry["query_digest"], "count": 0, "last": 0.0, "preview": preview},
        )
        group["count"] += 1
        group["last"] = max(group["last"], entry["created"])

    return {
        "collection_since": collection_since,
        "trace_ttl_days": 30,
        "preview_enabled": preview_on,
        "observability": {
            "stages": [{"key": key, "label": STAGE_LABELS[key]} for key in STAGES],
            "values": VALUE_LABELS,
            "rows": [dict(row) for row in OBSERVABILITY],
        },
        "funnel": [
            {"stage": stage, "label": FUNNEL_LABELS[stage], "count": total["funnel"][stage]}
            for stage in FUNNEL
        ],
        "totals": {
            **_public_counts(total),
            "members_active": len(active_members),
            "assets_hit": len(assets),
            "assets_used": len(used_assets),
            "cross_member_emitted": sum(a["cross_member"] for a in assets.values()),
            "tasks_active": len(total["sessions"]),
        },
        "by_asking_product": _buckets(total["products"]),
        "by_asking_project": _buckets(total["projects"], TOP_PROJECTS),
        "by_day": [{"day": day, **_public_counts(by_day[day])} for day in window],
        "by_channel": [
            {"channel": channel, **_public_counts(row)}
            for channel, row in sorted(by_channel.items(), key=lambda item: -item[1]["traces"])
        ],
        "by_member": [
            {"member": member, "name": _name(members, member), **_public_counts(row)}
            for member, row in sorted(
                by_member.items(), key=lambda item: (-item[1]["traces"], item[0])
            )
        ],
        "modes": _ranked(total["modes"]),
        "reasons": _ranked(total["reasons"]),
        "dropped": _ranked(total["dropped"]),
        "top_assets": [
            {
                "asset_id": asset_id,
                "title": item["title"],
                "product": item["product"],
                "project": item["project"],
                "kind": item["kind"],
                "producer": _name(members, item["producer"]) if item["producer"] else "未知",
                **{stage: item["funnel"][stage] for stage in FUNNEL},
                "members_selected": sum(1 for d in item["members"].values() if int(d) >= 1),
                "members_emitted": sum(1 for d in item["members"].values() if int(d) >= EMITTED),
                "tasks_emitted": sum(1 for d in item["tasks"].values() if int(d) >= EMITTED),
                "cross_member": item["cross_member"],
                "last": item["last"],
            }
            for asset_id, item in top_assets
        ],
        "by_producer": [
            {
                "member": producer,
                "name": _name(members, producer),
                "assets_hit": len(entry["assets_hit"]),
                "assets_used": len(entry["assets_used"]),
                "emitted": entry["funnel"]["emitted"],
                "confirmed": entry["funnel"]["confirmed"],
                "cited": entry["funnel"]["cited"],
                "consumers": len(entry["consumers"]),
                "tasks": len(entry["tasks"]),
                "cross_member": entry["cross_member"],
            }
            for producer, entry in sorted(
                producers.items(),
                key=lambda item: (
                    -item[1]["funnel"]["cited"],
                    -item[1]["funnel"]["confirmed"],
                    -item[1]["funnel"]["emitted"],
                    item[0],
                ),
            )
        ],
        "gaps": {
            "no_match_traces": total["no_match"],
            "repeated": sorted(
                (group for group in repeated.values() if group["count"] > 1),
                key=lambda group: (-group["count"], -group["last"]),
            )[:GAP_REPEATED],
            "recent": recent,
            "scanned_days": min(days, 30),
        },
        "rollup": {
            "frozen_days": len(frozen_days - live_days),
            "live_days": len(live_days),
        },
    }
