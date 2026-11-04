"""Recalibrate the prompt-time gate from how recalled memory was actually used.

The loop, end to end:

1. A member's client judges each turn: did the answer use the memory the hook
   injected, and did the model fetch one the gate held back (usage.py)? Only
   asset ids and labels reach the Hub.
2. The Hub keeps each label with the gate's evidence numbers at recall time
   (``recall_labels``), so learning never needs the prompt or the memory text.
3. Once a week (``serve``) or on demand (``amh-native recall-autotune``) the
   Hub refits the gate on the organization's gold questions plus the usage
   labels, and promotes the result only if it passes two independent checks:

   * **Gold does not regress.** Both recipes (gold only, gold + feedback) are
     scored by the same grouped cross-validation on gold cases the fold never
     trained on. The candidate must stay within tolerance on utility, hit@1,
     precision and abstention.
   * **It is not worse at predicting real use.** The newest quarter of each
     label class is held out; the candidate, trained without them, may rank
     them worse than the live gate only within the paired-bootstrap sampling
     error (at least 0.02 AUC). A few dozen labels cannot prove a gain, but
     they can expose a clearly worse gate.

   Gold utility itself must not fall at all: no promotion without a gain.

Feedback teaches but never judges: it joins training with class-balanced
weights whose total is capped at half the gold candidates, so a burst of
labels from one busy member cannot outvote the curated set, and only gold
cases pick the threshold. Every run is recorded with its numbers, promoted
or not; ``rollback`` restores the previous gate.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from . import recall_ledger, relevance
from .recall_eval import parse_gold, relevant
from .storage import database

INTERVAL = 7 * 86400
MIN_GOLD = 20
MIN_ANSWERABLE = 10
MIN_LABELS = 30
MIN_CLASS = 5
FEEDBACK_SHARE = 0.5
HOLDOUT = 0.25
TOLERANCE = 0.02
CV_SEEDS = 5  # repetitions of the 5-fold split, as in recall-calibrate
CHECKED = ("utility", "hit@1", "precision", "abstention")
POSITIVE = ("used", "consulted")
SETTING = "autotune:"


def gold_cases(db: sqlite3.Connection, tenant: str) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT case_id, query, expect, tags FROM recall_gold WHERE tenant=? ORDER BY case_id",
        (tenant,),
    ).fetchall()
    return parse_gold(
        {
            "id": r["case_id"],
            "query": r["query"],
            "expect": json.loads(r["expect"]),
            "tags": json.loads(r["tags"]),
        }
        for r in rows
    )


def store_gold(
    db: sqlite3.Connection,
    who: dict[str, Any],
    raw: list[dict[str, Any]],
    *,
    replace: bool = False,
    now: float | None = None,
) -> dict[str, int]:
    """Add or update a member's gold questions for their organization."""
    if not 1 <= len(raw) <= 500:
        raise ValueError("gold upload must carry 1 to 500 cases")
    cases = parse_gold(raw)
    for case in cases:
        if len(case["query"]) > 4000 or len(case["expect"]) > 16 or len(case["tags"]) > 16:
            raise ValueError("gold case too large")
    now = time.time() if now is None else now
    if replace:
        db.execute("DELETE FROM recall_gold WHERE tenant=?", (who["tenant"],))
    db.executemany(
        "INSERT OR REPLACE INTO recall_gold VALUES(?,?,?,?,?,?,?)",
        [
            (
                who["tenant"],
                case["id"][:128],
                case["query"],
                json.dumps(case["expect"], ensure_ascii=False),
                json.dumps(case["tags"], ensure_ascii=False),
                who["member_id"],
                now,
            )
            for case in cases
        ],
    )
    total = db.execute(
        "SELECT COUNT(*) FROM recall_gold WHERE tenant=?", (who["tenant"],)
    ).fetchone()[0]
    return {"stored": len(cases), "total": int(total)}


def collect(
    hub: Any,
    cases: list[dict[str, Any]],
    *,
    token: str = "",
    identity: dict[str, Any] | None = None,
) -> list[tuple[dict[str, Any], list[relevance.Candidate]]]:
    """Every gold case's judged candidates, with the gate's evidence for each."""
    collecting = relevance.GateModel(
        mean=[0.0] * len(relevance.FEATURES),
        scale=[1.0] * len(relevance.FEATURES),
        weights=[0.0] * len(relevance.FEATURES),
        bias=0.0,
        threshold=0.5,
    )
    # The learned gate needs the generic-prompt bank even where no gate is live yet.
    backend, _ = hub._semantic_backend()
    if backend is not None:
        hub.warm_gate(backend)
    judged = []
    for case in cases:
        items = hub.retrieve(
            token,
            case["query"],
            limit=50,
            kinds=["memory", "rule"],
            channel="hook",
            record=False,
            identity=identity,
            gate_override=collecting,
            collect_gate=True,
        )["items"]
        judged.append(
            (
                case,
                [
                    relevance.Candidate(
                        values=list(item["gate_features"]),
                        relevant=bool(case["expect"]) and relevant(item, case["expect"]),
                        shingles=relevance.shingles(str(item.get("body") or "")),
                        duplicate_key=(str(item.get("body_hash")), str(item.get("role"))),
                    )
                    for item in items
                    if "gate_features" in item
                ],
            )
        )
    return judged


def _weights(labels: list[dict[str, Any]], gold_rows: int) -> list[float]:
    """Class-balanced label weights whose total is capped at a share of gold."""
    positives = sum(1 for label in labels if label["label"] in POSITIVE)
    negatives = len(labels) - positives
    total = min(float(len(labels)), FEEDBACK_SHARE * gold_rows)
    return [
        total / 2 / positives if label["label"] in POSITIVE else total / 2 / negatives
        for label in labels
    ]


def _rows(labels: list[dict[str, Any]]) -> tuple[list[list[float]], list[float]]:
    return (
        [list(map(float, label["features"])) for label in labels],
        [1.0 if label["label"] in POSITIVE else 0.0 for label in labels],
    )


def _holdout(
    labels: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Newest quarter of each class held out; the rest (older) trains."""
    train, held = [], []
    for positive in (True, False):
        group = sorted(
            (row for row in labels if (row["label"] in POSITIVE) == positive),
            key=lambda row: row["created"],
        )
        cut = len(group) - max(1, round(len(group) * HOLDOUT))
        train.extend(group[:cut])
        held.extend(group[cut:])
    return train, held


def _usable(labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    width = len(relevance.FEATURES)
    return [
        label
        for label in labels
        if isinstance(label.get("features"), list) and len(label["features"]) == width
    ]


def _counts(labels: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"used": 0, "unused": 0, "consulted": 0}
    for label in labels:
        counts[label["label"]] = counts.get(label["label"], 0) + 1
    return counts


def run(
    hub: Any,
    tenant: str,
    *,
    cases: list[dict[str, Any]] | None = None,
    labels: list[dict[str, Any]] | None = None,
    token: str = "",
    identity: dict[str, Any] | None = None,
    apply: bool = True,
    now: float | None = None,
    min_labels: int = MIN_LABELS,
    activate: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """One recalibration: fit, check against gold and held-out use, maybe promote."""
    now = time.time() if now is None else now
    with database(hub.path) as db:
        if cases is None:
            cases = gold_cases(db, tenant)
        if labels is None:
            labels = recall_ledger.labels(db, tenant, since=now - recall_ledger.LABEL_TTL)
    labels = _usable(labels)
    base = hub.gate_model(tenant)
    report: dict[str, Any] = {
        "at": now,
        "tenant": tenant,
        "live_gate": getattr(base, "version", None),
        "gold": {"cases": len(cases), "answerable": sum(1 for c in cases if c["expect"])},
        "labels": _counts(labels),
        "promoted": False,
    }
    counts = report["labels"]
    positives = counts["used"] + counts["consulted"]
    if base is None:
        return {**report, "status": "no_learned_gate"}
    if len(cases) < MIN_GOLD or report["gold"]["answerable"] < MIN_ANSWERABLE:
        return {**report, "status": "insufficient_gold"}
    if len(labels) < min_labels or positives < MIN_CLASS or counts["unused"] < MIN_CLASS:
        return {**report, "status": "insufficient_feedback"}
    judged = collect(hub, cases, token=token, identity=identity)
    gold_rows = [c.values for _, candidates in judged for c in candidates]
    gold_labels = [1.0 if c.relevant else 0.0 for _, candidates in judged for c in candidates]

    # Held-out use: the newest quarter of each class (both must be present to
    # rank), never trained on below.
    train, held = _holdout(labels)
    train_rows, train_labels = _rows(train)
    extra = (train_rows, train_labels, _weights(train, len(gold_rows)))
    baseline = relevance.cross_validate(judged, seeds=CV_SEEDS, rule=base)
    candidate = relevance.cross_validate(judged, extra=extra, seeds=CV_SEEDS, rule=base)
    trained = relevance.fit(
        [*gold_rows, *train_rows],
        [*gold_labels, *train_labels],
        weights=[1.0] * len(gold_rows) + extra[2],
    )
    held_rows, held_labels = _rows(held)
    quality = {
        "live": relevance.ranking_quality(base, held_rows, held_labels),
        "candidate": relevance.ranking_quality(trained, held_rows, held_labels),
    }
    # Utility must not fall at all (no churn without a gain); the parts may
    # trade within the tolerance.
    gold_check = {
        key: {
            "live_recipe": baseline["best"][key],
            "candidate": candidate["best"][key],
            "ok": candidate["best"][key]
            >= baseline["best"][key] - (0.0 if key == "utility" else TOLERANCE),
        }
        for key in CHECKED
    }
    # Non-inferiority on real use: a few dozen held-out labels cannot prove a
    # gain, but they can show the candidate is clearly worse than the live
    # gate (by more than its sampling error).
    paired = relevance.auc_difference(trained, base, held_rows, held_labels)
    margin = max(TOLERANCE, paired["standard_error"] or 0.0)
    use_ok = paired["difference"] is not None and paired["difference"] >= -margin
    report.update(
        {
            "gold_check": gold_check,
            "held_out_use": {
                **quality,
                **paired,
                "margin": round(margin, 4),
                "ok": use_ok,
                "labels": len(held),
            },
            "threshold": candidate["best"]["threshold"],
            "standard_error": candidate["standard_error"],
        }
    )
    if not all(check["ok"] for check in gold_check.values()):
        return {**report, "status": "rejected_gold_regression"}
    if not use_ok:
        return {**report, "status": "rejected_worse_on_use"}

    # Passed: the deployed model learns from every label and every gold case.
    all_rows, all_labels = _rows(labels)
    final = relevance.fit(
        [*gold_rows, *all_rows],
        [*gold_labels, *all_labels],
        weights=[1.0] * len(gold_rows) + _weights(labels, len(gold_rows)),
    )
    day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%d")
    root = str(base.version).split("-fb")[0]
    final.version = f"{root}-fb{day}"
    final.threshold = candidate["best"]["threshold"]
    relevance.adopt_rule(final, base)
    embedding = str(
        (hub.semantic_state or {}).get("model") or getattr(hub._encoder, "model_id", "")
    )
    final.calibration = {
        "date": datetime.fromtimestamp(now, timezone.utc).date().isoformat(),
        "model": embedding,
        "base": base.version,
        "cases": len(cases),
        "answerable": report["gold"]["answerable"],
        "candidates": len(gold_rows),
        "labels": counts,
        "feedback_weight": round(sum(_weights(labels, len(gold_rows))), 2),
        "cross_validated": candidate["best"],
        "standard_error": candidate["standard_error"],
        "held_out_use": quality,
        "utility": "0.3*hit@1 + 0.2*hit@3 + 0.25*precision + 0.25*abstention",
    }
    report["version"] = final.version
    if apply:
        (activate or hub.activate_gate)(tenant, final, embedding=embedding, report=report)
        report["promoted"] = True
    return {**report, "status": "promoted" if apply else "would_promote"}


def last_report(db: sqlite3.Connection, tenant: str) -> dict[str, Any] | None:
    row = db.execute("SELECT value FROM settings WHERE key=?", (SETTING + tenant,)).fetchone()
    try:
        return json.loads(row["value"]) if row else None
    except ValueError:
        return None


def remember(db: sqlite3.Connection, tenant: str, report: dict[str, Any]) -> None:
    db.execute(
        "INSERT OR REPLACE INTO settings VALUES(?,?)",
        (SETTING + tenant, json.dumps(report, ensure_ascii=False)),
    )


def status(db: sqlite3.Connection, tenant: str, *, now: float | None = None) -> dict[str, Any]:
    """What a member may see: label counts, the last run's decision, gate history."""
    now = time.time() if now is None else now
    gates = [
        {
            "version": r["version"],
            "created": r["created"],
            "active": r["active"] == 1,
            "retired": r["active"] == -1,
        }
        for r in db.execute(
            "SELECT version, created, active FROM recall_gates WHERE tenant=? "
            "ORDER BY created DESC LIMIT 10",
            (tenant,),
        )
    ]
    gold = db.execute("SELECT COUNT(*) FROM recall_gold WHERE tenant=?", (tenant,)).fetchone()[0]
    return {
        "labels_7d": recall_ledger.usage_counts(db, tenant, since=now - 7 * 86400),
        "labels_30d": recall_ledger.usage_counts(db, tenant, since=now - 30 * 86400),
        "gold_cases": int(gold),
        "last_run": last_report(db, tenant),
        "gates": gates,
        "interval_days": INTERVAL / 86400,
    }


def due(hub: Any, *, now: float | None = None, interval: float = INTERVAL) -> list[str]:
    """Organizations with gold questions whose last run is older than the interval."""
    now = time.time() if now is None else now
    with database(hub.path) as db:
        tenants = [r[0] for r in db.execute("SELECT DISTINCT tenant FROM recall_gold")]
        return [
            tenant
            for tenant in tenants
            if now - float((last_report(db, tenant) or {}).get("at") or 0) >= interval
        ]


def internal_identity(db: sqlite3.Connection, tenant: str) -> dict[str, Any]:
    """Who the Hub judges gold cases as: the organization itself, with no device."""
    return {"tenant": tenant, "device_id": "hub-autotune", "member_id": "hub-autotune", "name": ""}


def run_and_remember(hub: Any, tenant: str, **kwargs: Any) -> dict[str, Any]:
    """One serialized run with its report stored for status, success or not."""
    with hub.autotune_lock:
        with database(hub.path) as db:
            who = internal_identity(db, tenant)
        try:
            report = run(hub, tenant, identity=who, **kwargs)
        except Exception as exc:  # noqa: BLE001 - a failed run is a status, never a crash
            report = {
                "at": time.time(),
                "tenant": tenant,
                "status": "error",
                "error": type(exc).__name__,
            }
        with database(hub.path) as db:
            remember(db, tenant, report)
        return report
