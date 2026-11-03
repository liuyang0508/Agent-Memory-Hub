"""Small real-model isolation evaluation, NOT a sealed generalization benchmark.

Run: PYTHONPATH=. python benchmarks/native_hybrid_smoke.py --output /tmp/report.json
Uses only local cached weights and temporary synthetic assets; no production writes.
"""

import argparse
import hashlib
import math
import json
import tempfile
import time
from pathlib import Path

from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.storage import database
from agent_brain.retrieval.mmr import _cosine_sim

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def value(key, body, **kw):
    return dict(
        asset_id=key,
        source_store="synthetic-eval",
        product="codex-cli",
        project="eval",
        kind="memory",
        path=key + ".md",
        title="",
        body=body,
        **kw,
    )


def run():
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="amh-native-semantic-eval-") as temp:
        hub = Hub(Path(temp) / "hub.sqlite", semantic_model=MODEL, mmr_lambda=0.85)
        who = hub.provision("Synthetic A", "test", tenant="eval-a")
        hidden = hub.provision("Synthetic B", "test", tenant="eval-b")
        token = who["token"]
        hub.put(token, value("approval", "发布上线之前，变更清单必须由两名负责人复核签字。"))
        hub.put(token, value("refund", "客户退订后，款项会在七个工作日内退回原支付账户。"))
        hub.put(token, value("exact", "故障追踪号 TRACE_731，根因是连接池耗尽。"))
        hub.put(token, value("duplicate", "发布上线之前，变更清单必须由两名负责人复核签字。"))
        hub.put(
            token,
            value(
                "long-tail",
                "常规会议日程记录。" * 300 + "\n恢复旧配置需要先停止队列，再切换到上个发布版本。",
            ),
        )
        hub.put(token, value("old", "旧规范：发布仅需一人审批。"))
        hub.put(
            hidden["token"], value("secret", "The lunar base password is SECRET_ISOLATION_CANARY.")
        )
        for i in range(10):
            v = value(
                "failed-" + str(i), "发布上线之前需要如何审批？本轮未找到资料，无法回答。" * 30
            )
            v["path"] = "rollout_summaries/failed-" + str(i) + ".md"
            hub.put(token, v)
        built = hub.rebuild_semantic(token)
        hub.put(token, value("old", "旧规范已经作废。", base_revision=1))
        cases = [
            ("zh-paraphrase", "部署之前找谁审核变更？", "approval"),
            (
                "cross-language",
                "How long does it take to get my money back after cancellation?",
                "refund",
            ),
            ("synonym", "退款多久到账？", "refund"),
            ("exact-id", "TRACE_731", "exact"),
            ("exact-asset-id", "approval", "approval"),
            ("long-tail", "怎样回滚到之前的部署？", "long-tail"),
            ("no-answer", "How many rings does Saturn have?", None),
            ("isolation", "SECRET_ISOLATION_CANARY", None),
        ]
        holdout_file = (
            Path(__file__).resolve().parents[1]
            / "docs/evaluation/native-hybrid/holdout-queries.json"
        )
        holdout_bytes = holdout_file.read_bytes()
        holdout = json.loads(holdout_bytes)
        cases += [(c["case"], c["query"], c["expected"]) for c in holdout]
        baseline = Hub(hub.path)
        baseline.semantic_model = None  # same SQL/role/RRF pipeline, model disabled
        observations = []
        for name, query, target in cases:
            before = time.perf_counter()
            result = hub.retrieve(token, query, limit=3)
            hybrid_ms = (time.perf_counter() - before) * 1000
            before = time.perf_counter()
            lexical = baseline.retrieve(token, query, limit=3)
            lexical_ms = (time.perf_counter() - before) * 1000
            baseline_ids = [v["asset_id"] for v in lexical["items"]]
            diagnostic = None
            if target:
                with database(hub.path) as db:
                    row = db.execute(
                        "SELECT vectors FROM native_semantic_vectors WHERE tenant=? AND asset_id=?",
                        (who["tenant_id"], target),
                    ).fetchone()
                windows = json.loads(row["vectors"])
                query_vector = hub._encoder.embed(query)
                best = max(windows, key=lambda w: _cosine_sim(query_vector, w["vector"]))
                diagnostic = {k: v for k, v in best.items() if k != "vector"}
                diagnostic["max_cosine"] = _cosine_sim(query_vector, best["vector"])
                diagnostic["eligible_at_configured_floor"] = (
                    diagnostic["max_cosine"] >= hub.semantic_min_score
                )
            ids = [v["asset_id"] for v in result["items"]]
            # Duplicates are equivalent answers, provenance remains attached.
            found = target in ids or (target == "approval" and "duplicate" in ids)
            observations.append(
                dict(
                    case=name,
                    split="holdout" if name.startswith("holdout-") else "development",
                    baseline_ids=baseline_ids,
                    baseline_passed=(
                        target in baseline_ids
                        or (target == "approval" and "duplicate" in baseline_ids)
                    )
                    if target
                    else not baseline_ids,
                    baseline_ms=round(lexical_ms, 3),
                    hybrid_ms=round(hybrid_ms, 3),
                    expected_semantic_window=diagnostic,
                    query=query,
                    expected=target,
                    first_relevant_rank=next(
                        (
                            i
                            for i, key in enumerate(ids, 1)
                            if key == target or (target == "approval" and key == "duplicate")
                        ),
                        None,
                    ),
                    ids=ids,
                    passed=(found if target else not ids),
                    retrieval=result["retrieval"],
                )
            )

        def timing(split, key):
            values = sorted(c[key] for c in observations if c["split"] == split)
            return dict(
                n=len(values),
                p50_ms=values[math.ceil(len(values) * 0.5) - 1],
                p95_ms=values[math.ceil(len(values) * 0.95) - 1],
            )

        return dict(
            holdout_sha256=hashlib.sha256(holdout_bytes).hexdigest(),
            holdout_policy="Frozen before first execution; no subsequent threshold/model tuning. Same synthetic corpus; not a sealed generalization test.",
            timing_note="Single warm query per case; nearest-rank percentiles on tiny local corpus, excludes build/model load, not production latency.",
            timings={
                split: {key: timing(split, key) for key in ["baseline_ms", "hybrid_ms"]}
                for split in ["development", "holdout"]
            },
            evaluation="development smoke; synthetic corpus, REAL cached multilingual encoder",
            model=MODEL,
            build=built,
            elapsed_seconds=round(time.monotonic() - start, 3),
            cases=observations,
            passed=sum(c["passed"] for c in observations),
            total=len(observations),
            metric="answerable Hit@3 plus empty-result checks; not all Top1",
            top1=sum(c["first_relevant_rank"] == 1 for c in observations),
            answerable=sum(c["expected"] is not None for c in observations),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = run()
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
