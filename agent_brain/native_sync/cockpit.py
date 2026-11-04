"""Management view of organization memory: what value it creates, for whom, at
what cost in attention, and what an owner should decide next.

The page is organized as a balanced scorecard (value, adoption, operations,
learning), led by management by exception: only what needs a decision is
pushed to the top. Knowledge assets read as a growth-share style matrix and a
Pareto concentration. Every figure is derived from the recall ledger, the usage
labels and the asset store. Nothing here estimates money or time saved, because
nothing measures it. Benchmarks are defaults, stated as such, not the
organization's targets. Counts, names and titles only; never memory text.
"""

from __future__ import annotations

import json
import sqlite3
from statistics import median
from typing import Any

from . import insights

DAY_SECONDS = 86400
WEEKS = 8
FRESH_DAYS = 30
STALE_DAYS = 90
SILENT_DEVICE_DAYS = 14
VALUE_HIGH = 0.5
MIN_JUDGED_FOR_ALERT = 20
MIN_TRACES_FOR_ALERT = 20
PLANNED_ROWS = 100
USED = ("used", "consulted")

# Default management benchmarks: (good, warning, higher is better).
BENCHMARKS: dict[str, tuple[float, float, bool]] = {
    "usage_rate": (0.5, 0.3, True),
    "reuse_share": (0.3, 0.1, True),
    "penetration": (0.7, 0.4, True),
    "retention": (0.7, 0.4, True),
    "delivery": (0.8, 0.5, True),
    "semantic": (0.98, 0.9, True),
    "activation": (0.3, 0.1, True),
    "freshness": (0.5, 0.2, True),
}

QUADRANTS = {
    "star": ("明星记忆", "常被命中，也常被用上", "保持更新，作为团队范本"),
    "potential": ("潜力记忆", "用上的比例高，但命中不多", "补标题和关键词，让更多人搜得到"),
    "rework": ("待优化记忆", "常被命中，却很少被用上", "改写或拆分，内容不改就降低权重"),
    "dormant": ("沉睡记忆", "命中不多，用上的也不多", "确认是否过期，过期就归档"),
}


def _ratio(part: float, whole: float) -> float | None:
    return part / whole if whole else None


def _share(part: float, whole: float) -> float | None:
    """A ratio that is a share: rollups counted at different times cannot exceed all of it."""
    value = _ratio(part, whole)
    return None if value is None else min(1.0, value)


def _status(key: str, value: float | None) -> str:
    if value is None or key not in BENCHMARKS:
        return "none"
    good, warning, higher = BENCHMARKS[key]
    if higher:
        return "good" if value >= good else "warning" if value >= warning else "critical"
    return "good" if value <= good else "warning" if value <= warning else "critical"


def _indicator(
    key: str,
    label: str,
    value: float | None,
    previous: float | None,
    *,
    unit: str = "int",
    hint: str,
    higher: bool | None = True,
) -> dict[str, Any]:
    delta = None
    delta_kind = None
    if value is not None and previous is not None:
        if unit == "pct":
            delta, delta_kind = value - previous, "points"
        elif previous:
            delta, delta_kind = (value - previous) / previous, "percent"
        elif value:
            delta_kind = "new"
    benchmark = BENCHMARKS.get(key)
    return {
        "key": key,
        "label": label,
        "value": value,
        "unit": unit,
        "previous": previous,
        "delta": delta,
        "delta_kind": delta_kind,
        "higher_is_better": higher,
        "status": _status(key, value),
        "benchmark": {"good": benchmark[0], "warning": benchmark[1]} if benchmark else None,
        "hint": hint,
    }


def _labels(db: sqlite3.Connection, tenant: str, since: float) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in db.execute(
            "SELECT created,asset_id,label,member FROM recall_labels WHERE tenant=? AND created>=?",
            (tenant, since),
        )
    ]


def _usage(rows: list[dict[str, Any]], start: float, end: float) -> dict[str, int]:
    out = {"used": 0, "unused": 0, "consulted": 0}
    for row in rows:
        if start <= row["created"] < end and row["label"] in out:
            out[row["label"]] += 1
    return out


def _weekly(
    db: sqlite3.Connection, tenant: str, now: float, names: dict[str, str]
) -> dict[str, Any]:
    """Weekly active members and first-week cohorts, in seven-day steps ending today."""
    today = insights.day_key(now)
    start = insights.day_key(now - (WEEKS * 7 - 1) * DAY_SECONDS)
    days = [insights.day_key(now - offset * DAY_SECONDS) for offset in range(WEEKS * 7 - 1, -1, -1)]
    week_of = {day: index // 7 for index, day in enumerate(days)}
    active: list[set[str]] = [set() for _ in range(WEEKS)]
    first_seen: dict[str, str] = {}
    for row in db.execute(
        "SELECT member,min(day) AS first FROM recall_daily "
        "WHERE tenant=? AND json_extract(payload,'$.traces')>0 GROUP BY member",
        (tenant,),
    ):
        first_seen[str(row["member"])] = str(row["first"])
    for row in db.execute(
        "SELECT day,member FROM recall_daily WHERE tenant=? AND day>=? AND day<=? "
        "AND json_extract(payload,'$.traces')>0",
        (tenant, start, today),
    ):
        active[week_of[row["day"]]].add(str(row["member"]))
    labels = [days[index * 7 + 6][5:] for index in range(WEEKS)]
    cohorts = []
    for week in range(WEEKS):
        members = {
            member
            for member, first in first_seen.items()
            if week_of.get(first) == week
            or (week == 0 and first < start and member in active[0])
        }
        if not members:
            continue
        cohorts.append(
            {
                "week": labels[week],
                "size": len(members),
                "kept": [
                    len(members & active[later]) / len(members) for later in range(week, WEEKS)
                ],
            }
        )
    last, before = active[-1], active[-2]
    earlier = active[-3] if WEEKS >= 3 else set()
    return {
        "weeks": labels,
        "active": [len(group) for group in active],
        "cohorts": cohorts,
        "retention": _ratio(len(last & before), len(before)),
        "retention_previous": _ratio(len(before & earlier), len(earlier)),
        "lost": sorted(names.get(member, member[:8]) for member in before - last),
    }


def _live_assets(db: sqlite3.Connection, tenant: str) -> list[dict[str, Any]]:
    return [
        {
            "asset_id": row["id"],
            "title": row["title"] or "",
            "path": row["path"] or "",
            "kind": row["kind"],
            "observed": float(row["observed"] or 0),
            "producer": row["producer"],
        }
        for row in db.execute(
            "SELECT id,json_extract(payload,'$.title') AS title,"
            "json_extract(payload,'$.path') AS path,json_extract(payload,'$.kind') AS kind,"
            "json_extract(payload,'$.observed_at') AS observed,"
            "json_extract(payload,'$.producer_member_id') AS producer FROM assets "
            "WHERE tenant=? AND json_extract(payload,'$.deleted')=0 "
            "AND json_extract(payload,'$.kind') IN ('memory','rule')",
            (tenant,),
        )
    ]


def _window_assets(
    db: sqlite3.Connection, tenant: str, start_day: str, end_day: str
) -> dict[str, dict[str, Any]]:
    assets: dict[str, dict[str, Any]] = {}
    for row in db.execute(
        "SELECT asset_id,payload FROM asset_daily WHERE tenant=? AND day>=? AND day<=?",
        (tenant, start_day, end_day),
    ):
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            continue
        insights._merge_asset(assets.setdefault(row["asset_id"], insights._blank_asset()), payload)
    return assets


def asset_title(asset_id: str, title: str, path: str) -> str:
    """A readable name: generic index titles take the file name, parts their number."""
    name = title
    if not name or name.lower() in {"memory", "memory_summary", "claude", "agents"}:
        name = path.rsplit("/", 1)[-1] or title or asset_id
    if "__p" in asset_id:
        suffix = asset_id.rsplit("__p", 1)[1]
        if suffix.isdigit():
            name += f" 第{int(suffix) + 1}段"
    return name


def _assets_view(
    live: list[dict[str, Any]],
    window: dict[str, dict[str, Any]],
    labels: list[dict[str, Any]],
    start: float,
    now: float,
) -> dict[str, Any]:
    used: dict[str, int] = {}
    judged: dict[str, int] = {}
    for row in labels:
        if row["created"] < start:
            continue
        judged[row["asset_id"]] = judged.get(row["asset_id"], 0) + 1
        if row["label"] in USED:
            used[row["asset_id"]] = used.get(row["asset_id"], 0) + 1
    known = {asset["asset_id"]: asset for asset in live}
    demand = sorted(
        item["funnel"]["selected"] for key, item in window.items() if key in known and item["funnel"]["selected"]
    )
    high_demand = max(2.0, float(median(demand))) if demand else 2.0
    groups: dict[str, list[dict[str, Any]]] = {key: [] for key in QUADRANTS}
    unjudged = 0
    rows = []
    for asset in live:
        key = asset["asset_id"]
        funnel = (window.get(key) or insights._blank_asset())["funnel"]
        selected = funnel["selected"]
        count = judged.get(key, 0)
        rate = _ratio(used.get(key, 0), count)
        row = {
            "asset_id": key,
            "title": asset_title(key, asset["title"], asset["path"]),
            "selected": selected,
            "emitted": funnel["emitted"],
            "used": used.get(key, 0),
            "judged": count,
            "usage_rate": rate,
        }
        rows.append(row)
        if selected and rate is None:
            unjudged += 1
            continue
        value_high = rate is not None and rate >= VALUE_HIGH
        if selected >= high_demand:
            groups["star" if value_high else "rework"].append(row)
        else:
            groups["potential" if value_high else "dormant"].append(row)
    matrix = []
    for key, (name, meaning, action) in QUADRANTS.items():
        ordered = sorted(groups[key], key=lambda r: (-r["used"], -r["selected"], r["title"]))
        matrix.append(
            {
                "key": key,
                "name": name,
                "meaning": meaning,
                "action": action,
                "count": len(ordered),
                "top": [
                    {
                        "title": r["title"],
                        "selected": r["selected"],
                        "usage_rate": r["usage_rate"],
                    }
                    for r in ordered[:4]
                ],
            }
        )
    ranked = sorted((r for r in rows if r["emitted"]), key=lambda r: (-r["emitted"], r["title"]))
    total = sum(r["emitted"] for r in ranked)
    head = max(1, round(len(ranked) * 0.2)) if ranked else 0
    fresh = recent = stale = 0
    for asset in live:
        age = (now - asset["observed"]) / DAY_SECONDS if asset["observed"] else None
        if age is None or age > STALE_DAYS:
            stale += 1
        elif age > FRESH_DAYS:
            recent += 1
        else:
            fresh += 1
    return {
        "total": len(live),
        "activated": len(ranked),
        "matrix": matrix,
        "unjudged": unjudged,
        "high_demand": high_demand,
        "pareto": {
            "assets": len(ranked),
            "head": head,
            "head_share": _ratio(sum(r["emitted"] for r in ranked[:head]), total),
            "top": [{"title": r["title"], "emitted": r["emitted"], "used": r["used"]} for r in ranked[:10]],
        },
        "freshness": {"fresh": fresh, "recent": recent, "stale": stale},
    }


def _people(
    dashboard: dict[str, Any],
    current: dict[str, Any],
    labels: list[dict[str, Any]],
    start: float,
    names: dict[str, str],
) -> list[dict[str, Any]]:
    contributed = {row["key"]: row for row in dashboard["by_member"]}
    recalling = {row["member"]: row for row in current["by_member"]}
    reused = {row["member"]: row for row in current["by_producer"]}
    used_by: dict[str, int] = {}
    for row in labels:
        if row["created"] >= start and row["label"] in USED:
            used_by[row["member"]] = used_by.get(row["member"], 0) + 1
    out = []
    for member, name in names.items():
        own = contributed.get(member) or {}
        recall = recalling.get(member) or {}
        producer = reused.get(member) or {}
        traces = int(recall.get("traces") or 0)
        assets = int(own.get("assets") or 0)
        out.append(
            {
                "name": name,
                "assets": assets,
                "last_upload": own.get("last") or 0,
                "recalls": traces,
                "tasks": int(recall.get("tasks") or 0),
                "used": used_by.get(member, 0),
                "reused": int(producer.get("cross_member") or 0),
                "consumers": int(producer.get("consumers") or 0),
                "state": "active" if traces else "contributor" if assets else "silent",
            }
        )
    out.sort(key=lambda r: (-r["reused"], -r["assets"], -r["recalls"], r["name"]))
    return out[:PLANNED_ROWS]


def _alerts(
    *,
    health: dict[str, Any],
    totals: dict[str, Any],
    usage: dict[str, int],
    penetration: float | None,
    weekly: dict[str, Any],
    people: list[dict[str, Any]],
    assets: dict[str, Any],
    gaps: dict[str, Any],
    dashboard: dict[str, Any],
    now: float,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []

    def add(severity: str, title: str, evidence: str, action: str, section: str) -> None:
        alerts.append(
            {
                "severity": severity,
                "title": title,
                "evidence": evidence,
                "action": action,
                "section": section,
            }
        )

    state = health.get("semantic_state")
    if state in ("off", "unavailable", "error", "stale_index"):
        add(
            "critical",
            "语义检索没有运行",
            f"Hub 状态：{state}"
            + (f"（{health['semantic_reason']}）" if health.get("semantic_reason") else "")
            + "。此时只能关键词检索，门控不工作。",
            "运维检查模型和索引：hub.sh --model-dir，或停用遗留的索引进程。",
            "health",
        )
    traces = totals["traces"]
    if traces >= MIN_TRACES_FOR_ALERT and totals["degraded"] / traces > 0.1:
        add(
            "serious",
            "召回经常降级",
            f"本期 {totals['degraded']} / {traces} 次召回处于降级状态。",
            "查看系统健康里的降级原因。",
            "health",
        )
    judged = sum(usage.values())
    used = usage["used"] + usage["consulted"]
    if judged >= MIN_JUDGED_FOR_ALERT and used / judged < BENCHMARKS["usage_rate"][1]:
        add(
            "serious",
            "注入的记忆大多没被用上",
            f"判定过的 {judged} 条里只有 {used} 条被用上（{round(used / judged * 100)}%）。",
            "看知识资产里的「待优化记忆」，改写内容或提高门槛。",
            "assets",
        )
    silent = [row["name"] for row in people if row["state"] == "silent"]
    if penetration is not None and penetration < BENCHMARKS["penetration"][0] and silent:
        shown = "、".join(silent[:5]) + (f" 等 {len(silent)} 人" if len(silent) > 5 else "")
        add(
            "warning",
            "还有成员没用起来",
            f"成员渗透率 {round(penetration * 100)}%；本期既没贡献也没使用：{shown}。",
            "确认他们是否已安装客户端，安排一次上手指导。",
            "people",
        )
    if weekly["lost"]:
        shown = "、".join(weekly["lost"][:5])
        add(
            "warning",
            "上周活跃的成员本周没回来",
            f"{shown}（共 {len(weekly['lost'])} 人）。",
            "问问是否遇到问题；也可能只是本周没有相关工作。",
            "adoption",
        )
    contributors = [row for row in people if row["assets"]]
    total_assets = sum(row["assets"] for row in contributors)
    if len(people) >= 3 and total_assets >= 10:
        top = max(contributors, key=lambda row: row["assets"])
        if top["assets"] / total_assets > 0.7:
            add(
                "warning",
                "知识过度集中在一个人身上",
                f"{top['name']} 贡献了 {round(top['assets'] / total_assets * 100)}% 的记忆。",
                "这是关键人风险：鼓励其他成员开启上传，沉淀各自的经验。",
                "people",
            )
    repeated = gaps.get("repeated") or []
    if repeated:
        add(
            "info",
            "有问题反复问不到答案",
            f"{len(repeated)} 个问题重复出现且没有记忆可用，最多的问了 {repeated[0]['count']} 次。",
            "把答案沉淀成记忆；如果本来就不该召回，加进问题集。",
            "gaps",
        )
    stale = assets["freshness"]["stale"]
    if assets["total"] >= 10 and stale / assets["total"] > 0.5:
        add(
            "info",
            "过半记忆超过 90 天没更新",
            f"{stale} / {assets['total']} 条。",
            "抽查常被命中的记忆是否过期。",
            "assets",
        )
    quiet = []
    for device in dashboard["devices"]:
        if device["revoked"]:
            continue
        row = next((r for r in dashboard["by_device"] if r["key"] == device["id"]), None)
        last = float((row or {}).get("last") or 0)
        if now - last > SILENT_DEVICE_DAYS * DAY_SECONDS:
            quiet.append(device["name"])
    if quiet:
        add(
            "info",
            f"{len(quiet)} 台设备超过 {SILENT_DEVICE_DAYS} 天没有上传",
            "可能已闲置，也可能只是没有新记忆：" + "、".join(quiet[:5]) + "。",
            "闲置设备可以在管理后台吊销。",
            "people",
        )
    tune = health.get("autotune") or {}
    if not tune.get("gold_cases"):
        add(
            "info",
            "门控还没有问题集",
            "没有问题集，每周自动校准不会运行，门控不会根据本组织的数据调整。",
            "用 amh-native recall-gold-upload 提交问题集。",
            "health",
        )
    order = {"critical": 0, "serious": 1, "warning": 2, "info": 3}
    alerts.sort(key=lambda item: order[item["severity"]])
    return alerts


def _summary(
    *,
    north: dict[str, Any],
    days: int,
    usage_rate: float | None,
    totals: dict[str, Any],
    member_total: int,
    alerts: list[dict[str, Any]],
) -> list[str]:
    lines = []
    if not totals["traces"]:
        return ["本期还没有召回记录。成员安装客户端并开启召回后，这里会开始积累数据。"]
    head = f"最近 {days} 天，{totals['members_active']} / {member_total} 位成员发起了 {totals['traces']} 次召回"
    if north["basis"] == "used":
        head += f"，记忆被真正用上 {north['value']} 次"
        if usage_rate is not None:
            head += f"，使用率 {round(usage_rate * 100)}%"
    else:
        head += f"，注入 {north['value']} 次；使用判定还在积累"
    lines.append(head + "。")
    if totals["cross_member_emitted"]:
        lines.append(f"其中 {totals['cross_member_emitted']} 次注入来自其他成员的经验，知识在跨人流动。")
    urgent = [a for a in alerts if a["severity"] in ("critical", "serious")]
    if urgent:
        lines.append(f"有 {len(urgent)} 件事需要尽快处理：{urgent[0]['title']}。")
    elif alerts:
        lines.append(f"没有紧急问题，有 {len(alerts)} 项值得关注。")
    else:
        lines.append("没有需要决策的异常。")
    return lines


def build(
    db: sqlite3.Connection,
    tenant: str,
    *,
    days: int,
    now: float,
    names: dict[str, str],
    dashboard: dict[str, Any],
    current: dict[str, Any],
    previous: dict[str, Any],
    health: dict[str, Any],
) -> dict[str, Any]:
    """The cockpit payload for a window of whole days ending today."""
    start = now - days * DAY_SECONDS
    previous_start = start - days * DAY_SECONDS
    labels = _labels(db, tenant, previous_start)
    usage = _usage(labels, start, now + 1)
    usage_before = _usage(labels, previous_start, start)
    window_days = [row["day"] for row in current["by_day"]]
    live = _live_assets(db, tenant)
    window = _window_assets(db, tenant, window_days[0], window_days[-1])
    assets = _assets_view(live, window, labels, start, now)
    weekly = _weekly(db, tenant, now, names)
    people = _people(dashboard, current, labels, start, names)

    since = current.get("collection_since")
    has_before = bool(since and since <= previous["by_day"][-1]["day"])
    t, b = current["totals"], previous["totals"]

    def was(value: Any) -> Any:
        return value if has_before else None

    # North star: memory actually used. Without judgements yet, say so and show
    # the nearest observed step instead of implying use.
    used_now = usage["used"] + usage["consulted"]
    used_before = usage_before["used"] + usage_before["consulted"]
    basis = "used" if sum(usage.values()) else "emitted"
    by_day_used: dict[str, int] = {}
    by_day_unused: dict[str, int] = {}
    for row in labels:
        if row["created"] < start:
            continue
        day = insights.day_key(row["created"])
        target = by_day_used if row["label"] in USED else by_day_unused
        target[day] = target.get(day, 0) + 1
    trend = [
        {
            "day": row["day"],
            "traces": row["traces"],
            "emitted": row["emitted"],
            "confirmed": row["confirmed"],
            "no_match": row["no_match"],
            "used": by_day_used.get(row["day"], 0),
            "unused": by_day_unused.get(row["day"], 0),
        }
        for row in current["by_day"]
    ]
    north = {
        "basis": basis,
        "label": "记忆被真正用上" if basis == "used" else "记忆注入次数",
        "value": used_now if basis == "used" else t["emitted"],
        "previous": was(used_before if basis == "used" else b["emitted"]),
        "series": [row["used"] if basis == "used" else row["emitted"] for row in trend],
        "hint": (
            "模型的回答确实用到注入内容，或它主动读取了门控扣下的记忆。"
            if basis == "used"
            else "使用判定需要 1.4.11 以上的客户端，尚未积累；暂时显示注入次数。"
        ),
    }

    member_total = len(names)
    judged = sum(usage.values())
    judged_before = sum(usage_before.values())
    usage_rate = _ratio(used_now, judged)
    penetration = _ratio(t["members_active"], member_total)
    scorecard = [
        {
            "key": "value",
            "title": "价值创造",
            "question": "记忆有没有帮上忙？",
            "indicators": [
                _indicator(
                    "used",
                    "有效使用次数",
                    used_now if judged else None,
                    was(used_before) if judged else None,
                    hint="模型确实用上了注入或读取的记忆。",
                ),
                _indicator(
                    "usage_rate",
                    "使用率",
                    usage_rate,
                    was(_ratio(used_before, judged_before)),
                    unit="pct",
                    hint="被判定过的注入里，真正用上的比例。",
                ),
                _indicator(
                    "reuse_share",
                    "跨成员复用占比",
                    _share(t["cross_member_emitted"], t["emitted"]),
                    was(_share(b["cross_member_emitted"], b["emitted"])),
                    unit="pct",
                    hint="注入内容来自其他成员的比例：这是组织记忆区别于个人记忆的价值。",
                ),
                _indicator(
                    "tasks",
                    "覆盖任务数",
                    t["tasks_active"],
                    was(b["tasks_active"]),
                    hint="发起过召回的独立会话数。",
                ),
            ],
        },
        {
            "key": "adoption",
            "title": "用户采用",
            "question": "大家真的在用吗？",
            "indicators": [
                _indicator(
                    "penetration",
                    "成员渗透率",
                    penetration,
                    was(_ratio(b["members_active"], member_total)),
                    unit="pct",
                    hint="本期发起过召回的成员占全部成员的比例。",
                ),
                _indicator(
                    "members_active",
                    "活跃成员",
                    t["members_active"],
                    was(b["members_active"]),
                    hint="本期发起过召回的成员数。",
                ),
                _indicator(
                    "retention",
                    "周留存",
                    weekly["retention"],
                    weekly["retention_previous"],
                    unit="pct",
                    hint="上周活跃的成员里，本周仍然活跃的比例。",
                ),
                _indicator(
                    "tasks_per_member",
                    "人均任务数",
                    _ratio(t["tasks_active"], t["members_active"]),
                    was(_ratio(b["tasks_active"], b["members_active"])),
                    unit="dec",
                    hint="每位活跃成员的独立会话数。",
                ),
            ],
        },
        {
            "key": "operations",
            "title": "运营效率",
            "question": "链路顺不顺？",
            "indicators": [
                _indicator(
                    "semantic",
                    "语义可用率",
                    1 - t["degraded"] / t["traces"] if t["traces"] else None,
                    was(1 - b["degraded"] / b["traces"] if b["traces"] else None),
                    unit="pct",
                    hint="没有降级的召回占比。",
                ),
                _indicator(
                    "delivery",
                    "送达确认率",
                    _share(t["confirmed"], t["emitted"]),
                    was(_share(b["confirmed"], b["emitted"])),
                    unit="pct",
                    hint="注入后在会话记录里确认送达的比例；部分产品无法回报。",
                ),
                _indicator(
                    "injected_share",
                    "有注入的提问占比",
                    1 - t["no_match"] / t["traces"] if t["traces"] else None,
                    was(1 - b["no_match"] / b["traces"] if b["traces"] else None),
                    unit="pct",
                    higher=None,
                    hint="不是越高越好：门控正确地什么都不注入，也是好结果。",
                ),
                _indicator(
                    "emitted_per_trace",
                    "每次召回注入条数",
                    _ratio(t["emitted"], t["traces"]),
                    was(_ratio(b["emitted"], b["traces"])),
                    unit="dec",
                    higher=None,
                    hint="占用模型注意力的量。",
                ),
            ],
        },
        {
            "key": "learning",
            "title": "学习成长",
            "question": "知识在变多、变活吗？",
            "indicators": [
                _indicator(
                    "assets",
                    "记忆资产",
                    assets["total"],
                    None,
                    hint="当前可被召回的记忆和规则条数。",
                ),
                _indicator(
                    "in_window",
                    "本期新增或更新",
                    dashboard["totals"]["in_window"],
                    None,
                    hint="本期内上传过新版本的资产数（含会话摘要）。",
                ),
                _indicator(
                    "activation",
                    "资产活化率",
                    _ratio(assets["activated"], assets["total"]),
                    None,
                    unit="pct",
                    hint="本期至少被注入过一次的记忆占比。",
                ),
                _indicator(
                    "freshness",
                    "30 天内更新占比",
                    _ratio(assets["freshness"]["fresh"], assets["total"]),
                    None,
                    unit="pct",
                    hint="知识保鲜程度。",
                ),
            ],
        },
    ]
    alerts = _alerts(
        health=health,
        totals=t,
        usage=usage,
        penetration=penetration,
        weekly=weekly,
        people=people,
        assets=assets,
        gaps=current["gaps"],
        dashboard=dashboard,
        now=now,
    )
    return {
        "generated_at": now,
        "window_days": days,
        "period": {
            "start": window_days[0],
            "end": window_days[-1],
            "has_previous": has_before,
            "collection_since": since,
        },
        "summary": _summary(
            north=north,
            days=days,
            usage_rate=usage_rate,
            totals=t,
            member_total=member_total,
            alerts=alerts,
        ),
        "north_star": north,
        "alerts": alerts,
        "scorecard": scorecard,
        "trend": trend,
        "funnel": current["funnel"],
        "usage": usage,
        "weekly": weekly,
        "assets": assets,
        "people": people,
        "products": current["by_asking_product"],
        "projects": current["by_asking_project"],
        "contribution": {
            "products": [
                {"name": row["label"], "assets": row["assets"]} for row in dashboard["by_product"]
            ],
            "kinds": dashboard["by_kind"],
            "bytes": dashboard["totals"]["bytes"],
        },
        "gaps": current["gaps"],
        "preview_enabled": current["preview_enabled"],
        "modes": current["modes"],
        "reasons": current["reasons"],
        "dropped": current["dropped"],
        "channels": current["by_channel"],
        "health": health,
        "observability": current["observability"],
        "rollup": current["rollup"],
        "trace_ttl_days": current["trace_ttl_days"],
    }
