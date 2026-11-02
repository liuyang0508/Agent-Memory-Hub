"""Synthetic recall-parity evaluation: lexical vs hybrid vs +decay vs +Hopfield, hit@k and packed@k.

Run: PYTHONPATH=. python benchmarks/native_recall_parity.py --output /tmp/parity.json
Uses a deterministic synthetic embedder by default so the ranking *mechanics*
can be compared without model weights; pass --model <cached model> for a real
local encoder. This is a mechanics check on a tiny corpus, not quality evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import tempfile
import time
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


CUES = [cue for _, cues in TOPICS.values() for cue in cues]


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


def asset(key: str, body: str, **extra):
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


def declared(**block):
    return {"declared": {k: str(v) for k, v in block.items()}}


def corpus(hub: Hub, token: str) -> list[tuple[str, str, str]]:
    """Per topic: a fresh rule, a stale state that lexically outscores it, a neighbour
    reachable only by association, a superseded copy; plus supporting-role noise."""
    cases = []
    for key, (title, cues) in TOPICS.items():
        fresh = f"{cues[0]}需要{cues[1]}，{cues[2]}由{cues[3]}确认后归档，{cues[0]}完成后由{cues[3]}留档。"
        # Shorter and repeats the query cue: BM25 prefers it over the fresh rule.
        stale = f"{cues[0]}（旧）：{cues[0]}只要{cues[3]}口头确认。"
        # Shares cues with the fresh rule, none with the association query.
        neighbour = f"{cues[2]}归{cues[3]}管理，{cues[1]}记录也由{cues[3]}保存。"
        # Titles carry no cue phrase so the cue counts in the bodies decide the vectors.
        hub.put(token, asset(key + "-fresh", fresh, title="现行规范", source_meta=declared(type="feedback", modified="2026-11-01")))
        hub.put(token, asset(key + "-stale", stale, title="旧做法", source_meta=declared(type="project", modified="2025-12-01")))
        hub.put(token, asset(key + "-neighbour", neighbour, title="补充说明"))
        hub.put(token, asset(key + "-superseded", stale + " 作废。", title="作废", source_meta=declared(superseded_by=key + "-fresh")))
        cases.append((key, f"{cues[0]}现在要怎么做？", key + "-fresh"))
        cases.append((key + "-assoc", f"{cues[0]}之后还要补什么材料？", key + "-neighbour"))
    for i in range(30):
        hub.put(
            token,
            asset(
                f"noise-{i}",
                "会话记录：" + "、".join(cue for _, (_, cues) in TOPICS.items() for cue in cues[:2]) * 6,
                path=f"rollout_summaries/noise-{i}.md",
                product="codex-cli",
            ),
        )
    return cases


def evaluate(hub: Hub, token: str, cases, *, k: int = 3) -> dict:
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
            fresh_first += stale not in ids or ids.index(target) < ids.index(stale)
    return {
        "cases": len(cases),
        f"hit@{k}": round(hits / len(cases), 3),
        "packed@budget": round(packed / len(cases), 3),
        "mrr": round(sum(1 / r for r in ranks if r) / len(cases), 3),
        "fresh_rule_above_stale_state": round(fresh_first / max(1, topics), 3),
        "ranks": ranks,
    }


def run(model: str | None, output: Path | None) -> dict:
    started = time.monotonic()
    report: dict = {"model": model or CueBackend.model_id, "variants": {}}
    with tempfile.TemporaryDirectory(prefix="amh-recall-parity-") as temp:
        path = Path(temp) / "hub.sqlite"
        backend = None if model else CueBackend()
        hub = Hub(path, semantic_model=model, semantic_backend=backend, decay=False, hopfield=False)
        who = hub.provision("Synthetic", "bench", tenant="eval")
        cases = corpus(hub, who["token"])
        lexical = Hub(path, decay=False, hopfield=False)
        report["variants"]["lexical"] = evaluate(lexical, who["token"], cases)
        hub.rebuild_semantic(who["token"])
        report["variants"]["hybrid"] = evaluate(hub, who["token"], cases)
        hub.decay_enabled = True
        report["variants"]["hybrid+decay"] = evaluate(hub, who["token"], cases)
        hub.hopfield = True
        report["variants"]["hybrid+decay+hopfield"] = evaluate(hub, who["token"], cases)
        # Feedback changes the next ranking: cite every fresh rule once.
        for key, query, target in cases:
            if target.endswith("-fresh"):
                trace = hub.retrieve(who["token"], query, limit=8)["retrieval"]["trace_id"]
                hub.recall_feedback(who["token"], trace, [{"asset_id": target, "revision": 1, "event": "cite"}])
        report["variants"]["hybrid+decay+hopfield+feedback"] = evaluate(hub, who["token"], cases)
        sample = hub.retrieve(who["token"], cases[0][1], limit=3, record=False)
        report["sample_trace"] = {
            "stages": sample["retrieval"]["stages"],
            "candidates": [
                {k: c.get(k) for k in ("asset_id", "routes", "rank", "final_rank", "state", "nature")}
                for c in sample["trace"]["candidates"][:6]
            ],
        }
    report["elapsed_s"] = round(time.monotonic() - started, 2)
    report["caveat"] = (
        "Synthetic corpus and (by default) a synthetic cue embedder: this checks that the"
        " stages behave as designed, it is not evidence of retrieval quality on real memories."
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
    print(json.dumps({k: v for k, v in result.items() if k != "sample_trace"}, ensure_ascii=False, indent=2))
