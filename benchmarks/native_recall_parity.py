"""Synthetic recall-parity evaluation: lexical vs hybrid vs +decay vs +Hopfield, hit@k and packed@k.

Run: PYTHONPATH=. python benchmarks/native_recall_parity.py --output /tmp/parity.json
Uses a deterministic synthetic embedder by default so the ranking *mechanics*
can be compared without model weights; pass --model <cached model> for a real
local encoder. This is a mechanics check on a tiny corpus, not quality evidence.

Two corpora run side by side: ``supporting_noise`` (identical conversation
summaries, which dedup folds and the supporting weight demotes) and
``competing_noise`` (distinct ordinary memories that really compete for the
top ranks). Declared dates are generated relative to today so the numbers do
not drift as the calendar moves.
"""

from __future__ import annotations

from typing import Any

import argparse
import hashlib
import json
import math
import re
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.recall import pack

TOPICS = {
    "approval": ("发布审批", ["审批", "复核", "变更清单", "负责人", "签字"]),
    "refund": ("退款规则", ["退款", "七个工作日", "原支付账户", "退订"]),
    "rollback": ("回滚流程", ["回滚", "停止队列", "上个版本", "恢复配置"]),
    "oncall": ("值班安排", ["值班", "轮换", "告警", "响应时限"]),
    "naming": ("命名规范", ["命名", "小写", "短横线", "目录"]),
}

# Ages relative to the run, not calendar dates: the fresh rule is a week old,
# the stale state is well past the 365-day half-life window's useful range.
FRESH_DAYS = 7
STALE_DAYS = 300
NOISE_ITEMS = 30
NOTE = "合成机制测试，不是质量指标：只证明各阶段按设计生效，不能作为真实记忆上的检索质量引用。"


CUES = [cue for _, cues in TOPICS.values() for cue in cues]


def days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")


class CueBackend:
    """Deterministic embedder: one axis per cue phrase, L2 normalised.

    Documents sharing cue phrases are close; a query naming one cue is close to
    documents containing it, and orthogonal to documents that only share a
    *topic* with it. That gap is what Hopfield association is meant to bridge.
    """

    semantic = True
    model_id = "synthetic-cue-axes"
    model_version = "1"
    dim = len(CUES) + 1

    def embed(self, text: str) -> list[float]:
        vector = [float(text.count(cue)) for cue in CUES] + [0.0]
        if not any(vector):
            vector[-1] = 1.0
        norm = math.sqrt(sum(v * v for v in vector))
        return [v / norm for v in vector]


def asset(key: str, body: str, **extra: Any) -> dict[str, Any]:
    value = dict(
        asset_id=key,
        source_store="synthetic-eval",
        product="claude-code",
        project="eval",
        kind="memory",
        path=key + ".md",
        title=extra.pop("title", ""),
        body=body,
    )
    value.update(extra)
    return value


def declared(**block: Any) -> dict[str, dict[str, str]]:
    return {"declared": {k: str(v) for k, v in block.items()}}


def corpus(hub: Hub, token: str, *, competing_noise: bool = False) -> list[tuple[str, str, str]]:
    """Per topic: a fresh rule, a stale state that lexically outscores it, a neighbour
    reachable only by association, a superseded copy; plus noise.

    Default noise is 30 identical Codex conversation summaries: dedup folds them
    into one candidate and the supporting weight demotes it, so they never
    compete. ``competing_noise`` makes each body distinct and files it as an
    ordinary topic memory, so every noise item stays a full-weight candidate.
    """
    cases = []
    for key, (title, cues) in TOPICS.items():
        fresh = f"{cues[0]}需要{cues[1]}，{cues[2]}由{cues[3]}确认后归档，{cues[0]}完成后由{cues[3]}留档。"
        # Shorter and repeats the query cue: BM25 prefers it over the fresh rule.
        stale = f"{cues[0]}（旧）：{cues[0]}只要{cues[3]}口头确认。"
        # Shares cues with the fresh rule, none with the association query.
        neighbour = f"{cues[2]}归{cues[3]}管理，{cues[1]}记录也由{cues[3]}保存。"
        # Titles carry no cue phrase so the cue counts in the bodies decide the vectors.
        hub.put(
            token,
            asset(
                key + "-fresh",
                fresh,
                title="现行规范",
                source_meta=declared(type="feedback", modified=days_ago(FRESH_DAYS)),
            ),
        )
        hub.put(
            token,
            asset(
                key + "-stale",
                stale,
                title="旧做法",
                source_meta=declared(type="project", modified=days_ago(STALE_DAYS)),
            ),
        )
        hub.put(token, asset(key + "-neighbour", neighbour, title="补充说明"))
        hub.put(
            token,
            asset(
                key + "-superseded",
                stale + " 作废。",
                title="作废",
                source_meta=declared(superseded_by=key + "-fresh"),
            ),
        )
        cases.append((key, f"{cues[0]}现在要怎么做？", key + "-fresh"))
        cases.append((key + "-assoc", f"{cues[0]}之后还要补什么材料？", key + "-neighbour"))
    cue_run = "、".join(cue for _, (_, cues) in TOPICS.items() for cue in cues[:2])
    for i in range(NOISE_ITEMS):
        if competing_noise:
            hub.put(
                token,
                asset(
                    f"noise-{i}",
                    f"第{i}条备忘：" + cue_run * 6 + f"（备注 {i}）",
                    title=f"备忘 {i}",
                    path=f"notes/noise-{i}.md",
                ),
            )
        else:
            hub.put(
                token,
                asset(
                    f"noise-{i}",
                    "会话记录：" + cue_run * 6,
                    path=f"rollout_summaries/noise-{i}.md",
                    product="codex-cli",
                ),
            )
    return cases


def evaluate(
    hub: Hub, token: str, cases: list[tuple[str, str, str]], *, k: int = 3
) -> dict[str, Any]:
    hits = packed = fresh_first = topics = 0
    ranks = []
    for name, query, target in cases:
        result = hub.retrieve(token, query, limit=8, record=False)
        ids = [item["asset_id"] for item in result["items"]]
        rank = ids.index(target) + 1 if target in ids else None
        ranks.append(rank)
        hits += bool(rank and rank <= k)
        body, keys = pack(result["items"], covered=set(), max_bytes=4096 - 44, query=query)
        packed += any(key.startswith(target + ":") for key in keys)
        if target.endswith("-fresh"):
            topics += 1
            stale = target[: -len("-fresh")] + "-stale"
            # A fresh rule that did not make the list at all is not "above" anything.
            fresh_first += target in ids and (
                stale not in ids or ids.index(target) < ids.index(stale)
            )
    return {
        "cases": len(cases),
        f"hit@{k}": round(hits / len(cases), 3),
        "packed@budget": round(packed / len(cases), 3),
        "mrr": round(sum(1 / r for r in ranks if r) / len(cases), 3),
        "fresh_rule_above_stale_state": round(fresh_first / max(1, topics), 3),
        "ranks": ranks,
    }


def run_group(model: str | None, *, competing_noise: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    """All variants on one fresh Hub; returns (variants, sample_trace)."""
    variants: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="amh-recall-parity-") as temp:
        path = Path(temp) / "hub.sqlite"
        backend = None if model else CueBackend()
        hub = Hub(path, semantic_model=model, semantic_backend=backend, decay=False, hopfield=False)
        who = hub.provision("Synthetic", "bench", tenant="eval")
        cases = corpus(hub, who["token"], competing_noise=competing_noise)
        lexical = Hub(path, decay=False, hopfield=False)
        variants["lexical"] = evaluate(lexical, who["token"], cases)
        hub.rebuild_semantic(who["token"])
        variants["hybrid"] = evaluate(hub, who["token"], cases)
        hub.decay_enabled = True
        variants["hybrid+decay"] = evaluate(hub, who["token"], cases)
        hub.hopfield = True
        variants["hybrid+decay+hopfield"] = evaluate(hub, who["token"], cases)
        # Feedback changes the next ranking: cite every fresh rule once.
        for key, query, target in cases:
            if target.endswith("-fresh"):
                trace = hub.retrieve(who["token"], query, limit=8)["retrieval"]["trace_id"]
                hub.recall_feedback(
                    who["token"], trace, [{"asset_id": target, "revision": 1, "event": "cite"}]
                )
        variants["hybrid+decay+hopfield+feedback"] = evaluate(hub, who["token"], cases)
        sample = hub.retrieve(who["token"], cases[0][1], limit=3, record=False)
        sample_trace = {
            "stages": sample["retrieval"]["stages"],
            "candidates": [
                {
                    k: c.get(k)
                    for k in ("asset_id", "routes", "rank", "final_rank", "state", "nature")
                }
                for c in sample["trace"]["candidates"][:6]
            ],
        }
    return variants, sample_trace


def run(model: str | None, output: Path | None) -> dict[str, Any]:
    started = time.monotonic()
    report: dict[str, Any] = {
        "model": model or CueBackend.model_id,
        "note": NOTE,
        "dates": {"fresh": days_ago(FRESH_DAYS), "stale": days_ago(STALE_DAYS)},
    }
    report["variants"], report["sample_trace"] = run_group(model, competing_noise=False)
    report["variants_competing_noise"], report["sample_trace_competing_noise"] = run_group(
        model, competing_noise=True
    )
    report["elapsed_s"] = round(time.monotonic() - started, 2)
    report["caveat"] = (
        "Synthetic corpus and (by default) a synthetic cue embedder: this checks that the"
        " stages behave as designed, it is not evidence of retrieval quality on real memories."
        " 'variants' uses identical supporting-role noise; 'variants_competing_noise' uses"
        " distinct ordinary memories as noise."
    )
    if output:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=None, help="cached local sentence-transformers model")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    result = run(args.model, args.output)
    print(NOTE)
    print(
        json.dumps(
            {k: v for k, v in result.items() if not k.startswith("sample_trace")},
            ensure_ascii=False,
            indent=2,
        )
    )
