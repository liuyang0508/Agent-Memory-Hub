"""Server-side recall pipeline fixes from the 2026-11-03 review (B1/B6/B9-B13, B17-B20, B29, B33).

Every test names the review item it covers. Vector backends are synthetic:
they prove protocol and bookkeeping, never ranking quality.
"""

import json
import math
import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from agent_brain.native_sync import recall_ledger, retrieval
from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.storage import database
from agent_brain.retrieval.hopfield import _rank_weights, hopfield_expand
from agent_brain.retrieval.mmr import _cosine_sim, mmr_rerank
from agent_brain.retrieval.types import RetrievedItem
from test_hub import asset
from test_hybrid_retrieval import SyntheticBackend

DISABLED = "未启用"


def make(tmp_path, **options):
    hub = Hub(tmp_path / "hub.sqlite", **options)
    who = hub.provision("one", "device")
    return hub, who


def declared(**block):
    return {"declared": {k: str(v) for k, v in block.items()}}


def days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")


def feedback(hub, who, trace_id, *events):
    return hub.recall_feedback(
        who["token"],
        trace_id,
        [{"asset_id": a, "revision": 1, "event": e} for a, e in events],
    )


def signals(hub, asset_id):
    with database(hub.path) as db:
        row = db.execute("SELECT * FROM recall_signals WHERE asset_id=?", (asset_id,)).fetchone()
    return dict(row) if row else {}


def stored_trace(hub, trace_id):
    with database(hub.path) as db:
        row = db.execute(
            "SELECT payload FROM recall_events WHERE trace_id=?", (trace_id,)
        ).fetchone()
    return json.loads(row["payload"])


# --- B1: delivery events are idempotent per state -----------------------------


def test_repeated_delivery_reports_count_once(tmp_path):
    hub, who = make(tmp_path)
    hub.put(who["token"], asset("a", body="审批复核"))
    trace_id = hub.retrieve(who["token"], "审批复核")["retrieval"]["trace_id"]
    # One request carrying the same delivery 64 times (a replayed queue).
    batch = [{"asset_id": "a", "revision": 1, "event": "emitted"}] * 64
    first = hub.recall_feedback(who["token"], trace_id, batch)
    assert first["applied"] == 64 and first["noop"] == 63 and first["ignored"] == 0
    assert first["states"] == {"a": "emitted"}
    # The hook re-reporting confirmed on every later prompt.
    for _ in range(3):
        again = feedback(hub, who, trace_id, ("a", "packed"), ("a", "confirmed"))
        assert again["applied"] == 2 and again["states"] == {"a": "confirmed"}
    row = signals(hub, "a")
    assert (row["delivered"], row["confirmed"]) == (1, 1)
    events = stored_trace(hub, trace_id)["candidates"][0]["events"]
    assert [e["event"] for e in events] == ["emitted", "confirmed"]
    # cite is further along than confirmed: a later confirmed is a no-op too.
    assert feedback(hub, who, trace_id, ("a", "cite"))["states"] == {"a": "cited"}
    assert feedback(hub, who, trace_id, ("a", "confirmed"))["noop"] == 1
    assert signals(hub, "a")["confirmed"] == 1


# --- B9: bounded event history and once-per-member feedback -------------------


def test_feedback_counts_once_per_member_and_event_history_is_capped(tmp_path, monkeypatch):
    hub, who = make(tmp_path)
    hub.put(who["token"], asset("a", body="审批复核"))
    trace_id = hub.retrieve(who["token"], "审批复核")["retrieval"]["trace_id"]
    # A member looping POST support 300 times used to add 300 to the counter.
    for _ in range(5):
        hub.recall_feedback(
            who["token"], trace_id, [{"asset_id": "a", "revision": 1, "event": "support"}] * 64
        )
    assert signals(hub, "a")["support"] == 1 and signals(hub, "a")["gain"] == pytest.approx(0.1)
    other = hub.provision("two", "other-device")
    assert feedback(hub, other, trace_id, ("a", "support"), ("a", "contradict"))["noop"] == 0
    assert feedback(hub, other, trace_id, ("a", "support"), ("a", "contradict"))["noop"] == 2
    row = signals(hub, "a")
    assert (row["support"], row["contradict"]) == (2, 1)
    candidate = stored_trace(hub, trace_id)["candidates"][0]
    assert candidate["feedback_by"] == {
        "support": [who["member_id"], other["member_id"]],
        "contradict": [other["member_id"]],
    }
    assert [e["event"] for e in candidate["events"]] == ["support", "support", "contradict"]
    # The per-candidate history stops growing at the cap; only a counter continues.
    assert recall_ledger.MAX_CANDIDATE_EVENTS == 32
    monkeypatch.setattr(recall_ledger, "MAX_CANDIDATE_EVENTS", 4)
    for name in ("x", "y", "z"):
        member = hub.provision(name, name + "-device")
        feedback(hub, member, trace_id, ("a", "cite"))
    candidate = stored_trace(hub, trace_id)["candidates"][0]
    assert len(candidate["events"]) == 4 and candidate["events_overflow"] == 2
    assert signals(hub, "a")["cited"] == 3  # counting is not affected by the history cap


# --- B19: state-gated events are skipped, never a 409 for the whole batch ------


def test_events_the_state_does_not_allow_are_skipped_and_counted(tmp_path):
    hub, who = make(tmp_path)
    hub.put(who["token"], asset("a", body="审批复核 alpha"))
    hub.put(who["token"], asset("b", body="审批复核 bravo"))
    result = hub.retrieve(who["token"], "审批复核", limit=1)
    trace_id = result["retrieval"]["trace_id"]
    assert {c["asset_id"]: c["state"] for c in result["trace"]["candidates"]} == {
        "a": "selected",
        "b": "dropped:limit",
    }
    outcome = feedback(
        hub,
        who,
        trace_id,
        ("b", "packed"),  # never returned: cannot have been delivered
        ("b", "emitted"),
        ("b", "cite"),  # feedback on a dropped candidate
        ("a", "emitted"),
        ("a", "budget"),  # budget/covered only start from candidate/selected
    )
    assert outcome["applied"] == 1 and outcome["ignored"] == 4
    assert outcome["states"] == {"a": "emitted", "b": "dropped:limit"}
    assert signals(hub, "b") == {} and signals(hub, "a")["delivered"] == 1
    assert "events" not in stored_trace(hub, trace_id)["candidates"][1]
    # Over HTTP the batch succeeds, so the client does not discard it.
    api = TestClient(create_app(hub))
    response = api.post(
        "/native/v1/recall/feedback",
        json={
            "trace_id": trace_id,
            "events": [{"asset_id": "b", "revision": 1, "event": "emitted"}],
        },
        headers={"Authorization": "Bearer " + who["token"]},
    )
    assert response.status_code == 200 and response.json()["ignored"] == 1
    # Structural problems still refuse the batch as before.
    with pytest.raises(ValueError):
        feedback(hub, who, trace_id, ("a", "emitted"), ("zzz", "emitted"))


# --- B6: every returned item is inside the bounded trace ----------------------


def test_selected_items_outrank_dropped_duplicates_in_the_trace(tmp_path, monkeypatch):
    hub, who = make(tmp_path)
    monkeypatch.setattr(retrieval, "TRACE_CANDIDATES", 3)
    for i in range(4):  # the same rule file from four devices: dedup drops three
        hub.put(who["token"], asset("dup%d" % i, body="审批复核 alpha"))
    hub.put(who["token"], asset("unique", body="审批复核 另外 一些 更长 的 文本 内容 以 降低 分数"))
    result = hub.retrieve(who["token"], "审批复核")
    assert [i["asset_id"] for i in result["items"]] == ["dup0", "unique"]
    rows = [(c["asset_id"], c["rank"], c.get("final_rank")) for c in result["trace"]["candidates"]]
    assert rows == [("dup0", 1, 1), ("unique", 5, 2), ("dup1", 2, None)]
    trace_id = result["retrieval"]["trace_id"]
    outcome = feedback(hub, who, trace_id, ("unique", "packed"), ("unique", "emitted"))
    assert outcome["applied"] == 2 and outcome["states"] == {"unique": "emitted"}


# --- B10 + B20: trace persistence never waits for a busy writer --------------


def test_locked_database_does_not_delay_retrieval_and_yields_no_trace_id(tmp_path):
    hub, who = make(tmp_path)
    hub.put(who["token"], asset("a", body="审批复核"))
    assert hub.retrieve(who["token"], "审批复核")["retrieval"]["trace_id"]
    lock = sqlite3.connect(str(hub.path), timeout=0)
    lock.execute("BEGIN IMMEDIATE")
    try:
        started = time.monotonic()
        result = hub.retrieve(who["token"], "审批复核")
        recalled = hub.recall(who["token"], "审批复核")
        elapsed = time.monotonic() - started
    finally:
        lock.rollback()
        lock.close()
    assert elapsed < 3.0  # two retrievals; the default DB_TIMEOUT alone is 10s
    assert result["retrieval"]["degraded"] is True
    assert "recall_trace_not_recorded" in result["retrieval"]["reasons"]
    assert "recall_trace_not_recorded" in result["trace"]["retrieval"]["reasons"]
    assert result["retrieval"]["trace_id"] is None and result["trace"]["trace_id"] is None
    assert result["items"] and all(i["recall"]["trace_id"] is None for i in result["items"])
    assert recalled["trace_id"] is None and recalled["items"]
    assert len(hub.recalls(who["token"])["traces"]) == 1  # only the first one was stored
    with database(hub.path) as db:
        assert db.execute("SELECT count(*) FROM recall_events").fetchone()[0] == 1


# --- B11: incremental MMR and a bounded head -------------------------------


def _reference_mmr(candidates, embeddings, *, top_k, lambda_):
    """The direct definition the shared mmr_rerank replaced (verbatim behaviour)."""
    if len(candidates) <= 1 or lambda_ is None or not embeddings:
        return candidates
    score_by_id = {c.id: c.score for c in candidates}
    max_score = max(score_by_id.values()) or 1.0
    selected, remaining = [], list(candidates)
    while remaining and len(selected) < top_k:
        best_idx, best_mmr = 0, -float("inf")
        for i, cand in enumerate(remaining):
            rel = score_by_id[cand.id] / max_score
            max_sim = 0.0
            cand_emb = embeddings.get(cand.id)
            if cand_emb and selected:
                for item in selected:
                    emb = embeddings.get(item.id)
                    if emb:
                        max_sim = max(max_sim, _cosine_sim(cand_emb, emb))
            mmr_score = lambda_ * rel - (1 - lambda_) * max_sim
            if mmr_score > best_mmr:
                best_mmr, best_idx = mmr_score, i
        selected.append(remaining.pop(best_idx))
    return selected


@pytest.mark.parametrize("seed", range(6))
def test_incremental_mmr_matches_the_direct_definition(seed):
    rng = random.Random(seed)
    dim = 6
    candidates = [
        RetrievedItem("c%d" % i, rng.uniform(-1, 2) if seed % 2 else rng.uniform(0, 1), None, None)
        for i in range(40)
    ]
    embeddings = {}
    for cand in candidates:
        roll = rng.random()
        if roll < 0.1:
            continue  # no vector at all
        if roll < 0.2:
            embeddings[cand.id] = [0.0] * dim  # zero vector
        elif roll < 0.3:
            embeddings[cand.id] = []  # empty vector
        else:
            embeddings[cand.id] = [rng.gauss(0, 1) for _ in range(dim)]
    for lambda_ in (0.0, 0.3, 0.5, 0.9, 1.0):
        for top_k in (1, 8, 40):
            fast = mmr_rerank(candidates, embeddings, top_k=top_k, lambda_=lambda_)
            slow = _reference_mmr(candidates, embeddings, top_k=top_k, lambda_=lambda_)
            assert [c.id for c in fast] == [c.id for c in slow], (lambda_, top_k)


def test_incremental_mmr_is_fast_enough_for_the_candidate_pool():
    rng = random.Random(0)
    candidates = [RetrievedItem("c%d" % i, rng.random(), None, None) for i in range(300)]
    embeddings = {c.id: [rng.gauss(0, 1) for _ in range(384)] for c in candidates}
    started = time.monotonic()
    mmr_rerank(candidates, embeddings, top_k=24, lambda_=0.5)
    assert time.monotonic() - started < 2.0  # the direct form took ~90s here


class SpreadBackend(SyntheticBackend):
    """Each document points a little further away from the query direction."""

    def embed(self, text):
        self.seen.append(text)
        if text.strip() == "approval":
            return [1.0, 0.0, 0.0]
        tail = text.rsplit(" ", 1)[-1]
        angle = (int(tail) if tail.isdigit() else 0) * 0.06
        return [math.cos(angle), math.sin(angle), 0.0]


def test_mmr_diversifies_only_the_reachable_head_and_keeps_the_tail_order(tmp_path, monkeypatch):
    backend = SpreadBackend()
    hub = Hub(tmp_path / "hub.sqlite", semantic_backend=backend, mmr_lambda=0.5)
    who = hub.provision("one", "device")
    for i in range(12):
        hub.put(who["token"], asset("d%d" % i, title="", body="approval %d" % i))
    hub.rebuild_semantic(who["token"])
    calls = []
    original = retrieval.mmr_rerank

    def capture(candidates, embeddings, *, top_k, lambda_):
        calls.append((len(candidates), top_k, lambda_))
        return original(candidates, embeddings, top_k=top_k, lambda_=lambda_)

    monkeypatch.setattr(retrieval, "mmr_rerank", capture)
    result = hub.retrieve(who["token"], "approval", limit=1)
    assert calls == [(12, 8, 0.5)] and result["retrieval"]["stages"]["mmr"] == "on"
    stage = {
        c["asset_id"]: next(s for s in c["stages"] if s["stage"] == "mmr")
        for c in result["trace"]["candidates"]
    }
    assert sorted(s["after_rank"] for s in stage.values()) == list(range(1, 13))
    tail = sorted(
        (s["after_rank"], s["before_rank"]) for s in stage.values() if s["after_rank"] > 8
    )
    assert len(tail) == 4 and [b for _, b in tail] == sorted(b for _, b in tail)
    calls.clear()
    hub.retrieve(who["token"], "approval", limit=5)
    assert calls == [(12, 12, 0.5)]  # min(len, max(3·limit, 8))


# --- B12: supersession is honoured only towards an existing target -----------


def test_unverifiable_supersession_keeps_the_candidate_and_says_why(tmp_path):
    hub, who = make(tmp_path)
    put = lambda key, body, **meta: hub.put(  # noqa: E731
        who["token"],
        asset(key, body="审批复核 " + body, source_meta=declared(**meta) if meta else None),
    )
    put("typo", "typo", superseded_by="no-such-asset")
    put("selfish", "selfish", superseded_by="selfish")
    put("cycle-a", "cycle a", superseded_by="cycle-b")
    put("cycle-b", "cycle b", superseded_by="cycle-a")
    put("old", "old", superseded_by="new")
    put("new", "new")
    put("orphan", "orphan", superseded_by="gone")
    put("gone", "gone")
    hub.put(who["token"], asset("gone", body="", deleted=True, base_revision=1))
    result = hub.retrieve(who["token"], "审批复核")
    ids = {i["asset_id"] for i in result["items"]}
    assert ids == {"typo", "selfish", "cycle-a", "cycle-b", "new", "orphan"}
    rows = {c["asset_id"]: c for c in result["trace"]["candidates"]}
    assert (
        rows["old"]["state"] == "dropped:superseded" and rows["old"]["state_detail"]["by"] == "new"
    )
    assert rows["typo"]["declared_filter"] == {
        "reason": "superseded_target_missing",
        "by": "no-such-asset",
    }
    assert rows["orphan"]["declared_filter"] == {
        "reason": "superseded_target_missing",
        "by": "gone",
    }
    assert rows["selfish"]["declared_filter"] == {"reason": "superseded_self", "by": "selfish"}
    assert rows["cycle-a"]["declared_filter"] == {"reason": "superseded_cycle", "by": "cycle-b"}
    assert rows["cycle-b"]["declared_filter"] == {"reason": "superseded_cycle", "by": "cycle-a"}
    assert all(rows[k]["state"] == "selected" for k in ids) and "declared_filter" not in rows["new"]
    assert [i["asset_id"] for i in hub.retrieve(who["token"], "old")["items"]] == ["old"]


# --- B13: file mtime is the last-resort reference ------------------------------


def test_clone_time_does_not_make_a_dated_plan_fresh(tmp_path):
    hub, who = make(tmp_path)
    hub.put(
        who["token"],
        asset(
            "plan",
            path=days_ago(300) + "-plan.md",
            body="审批复核",
            source_meta={"mtime": time.time()},
        ),
    )
    hub.put(
        who["token"],
        asset("copy", path="notes.md", body="审批复核 copy", source_meta={"mtime": time.time()}),
    )
    rows = {c["asset_id"]: c for c in hub.retrieve(who["token"], "审批复核")["trace"]["candidates"]}
    assert rows["plan"]["times"]["content_modified"] is None
    assert rows["plan"]["times"]["file_mtime"]["source"] == "parsed"
    assert rows["plan"]["decay"]["reference"]["basis"] == "content_created"
    assert rows["plan"]["decay"]["age_days"] > 290
    assert rows["copy"]["decay"]["reference"]["basis"] == "file_mtime"
    assert rows["copy"]["decay"]["age_days"] < 1


# --- B17: weights keep their direction for negative reranker scores -----------


def test_decay_weight_keeps_direction_when_the_reranker_returns_negative_scores(tmp_path):
    assert retrieval._weighted(-3.0, 0.95) == pytest.approx(-3.15)
    assert retrieval._weighted(2.0, 0.95) == pytest.approx(1.9)
    assert retrieval._weighted(0.0, 1.03) == 0.0
    hub, who = make(tmp_path)
    body = "灰度发布审批需要两人复核 "
    hub.put(
        who["token"],
        asset(
            "stale",
            body=body + "alpha",
            source_meta=declared(type="project", modified=days_ago(300)),
        ),
    )
    hub.put(
        who["token"],
        asset(
            "fresh",
            body=body + "bravo",
            source_meta=declared(type="feedback", modified=days_ago(2)),
        ),
    )
    hub.reranker = lambda query, items: [-3.0] * len(items)  # cross-encoder style logits
    result = hub.retrieve(who["token"], "审批复核")
    assert result["retrieval"]["reranked"] is True
    assert [i["asset_id"] for i in result["items"]] == ["fresh", "stale"]
    scores = {i["asset_id"]: i["score"] for i in result["items"]}
    assert scores["stale"] < scores["fresh"] < 0
    assert scores["stale"] == pytest.approx(-3.15, abs=1e-3)


# --- B18: a failed semantic pass leaves no ghost candidates ------------------


def test_semantic_failure_halfway_leaves_no_ghost_candidate(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite", semantic_backend=SyntheticBackend())
    who = hub.provision("one", "device")
    hub.put(who["token"], asset("a-good", title="", body="两人复核"))
    hub.put(who["token"], asset("z-bad", title="", body="复核 bad"))
    hub.rebuild_semantic(who["token"])
    with database(hub.path) as db:
        row = db.execute(
            "SELECT vectors FROM native_semantic_vectors WHERE asset_id='z-bad'"
        ).fetchone()
        windows = json.loads(row["vectors"])
        windows[0]["vector"][0] = float("nan")
        db.execute(
            "UPDATE native_semantic_vectors SET vectors=? WHERE asset_id='z-bad'",
            (json.dumps(windows),),
        )
    result = hub.retrieve(who["token"], "approval")
    assert result["retrieval"]["reasons"] == ["semantic_query_failed:ValueError"]
    assert result["retrieval"]["mode"] == "lexical" and result["items"] == []
    assert result["trace"]["candidates"] == []
    assert result["retrieval"]["candidates"] == {"lexical": 0, "semantic": 0, "hopfield": 0}


# --- B29: Hopfield exclusion, rank weights and lexical seeds -----------------


def test_hopfield_excludes_known_candidates_before_the_limit():
    seeds = [("seed", 1.0, [1.0, 0.0, 0.0])]
    patterns = {
        "seed": [1, 0, 0],
        "known1": [0.99, 0.1, 0],
        "known2": [0.98, 0.15, 0],
        "new": [0.9, 0.3, 0],
    }
    _, naive = hopfield_expand(seeds, patterns, min_similarity=0.5, limit=2)
    assert {key for key, _ in naive} == {"known1", "known2"}  # the limit ate the new one
    _, found = hopfield_expand(
        seeds, patterns, min_similarity=0.5, limit=2, exclude={"known1", "known2"}
    )
    assert [key for key, _ in found] == ["new"]


def test_hopfield_seed_weights_follow_rank_not_near_equal_fused_scores():
    assert _rank_weights(1) == [1.0]
    assert _rank_weights(3) == [1.0, 2 / 3, 1 / 3]
    # RRF scores of neighbouring ranks differ by ~1%: a softmax over them was
    # uniform; by rank the first seed dominates the attractor.
    seeds = [("b", 0.0160, [0.0, 1.0, 0.0]), ("a", 0.0162, [1.0, 0.0, 0.0])]
    attractor, _ = hopfield_expand(seeds, {"a": [1, 0, 0], "b": [0, 1, 0]}, beta=8.0)
    assert attractor[0] > 0.9 > attractor[1]


class LedgerBackend(SyntheticBackend):
    """The lexical hit points away from the query; its neighbour is only near the hit."""

    def embed(self, text):
        self.seen.append(text)
        if "ledger" in text:
            return [0.0, 0.1, 0.99] if "流程" in text else [0.0, 0.0, 1.0]
        if "approval" in text:
            return [1.0, 0.0, 0.0]
        return [0.0, 1.0, 0.0]


def test_lexical_hits_with_vectors_seed_hopfield_and_no_seeds_is_reported(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite", semantic_backend=LedgerBackend(), hopfield=True)
    who = hub.provision("one", "device")
    hub.put(who["token"], asset("lex", title="", body="approval ledger"))
    hub.put(who["token"], asset("neighbour", title="", body="ledger 流程"))
    hub.rebuild_semantic(who["token"])
    result = hub.retrieve(who["token"], "approval")
    assert [i["asset_id"] for i in result["items"]] == ["lex", "neighbour"]
    assert result["retrieval"]["stages"]["hopfield"] == "on"
    assert result["retrieval"]["candidates"] == {"lexical": 1, "semantic": 0, "hopfield": 1}
    assert result["items"][0]["recall"]["routes"] == {
        "lexical": 1,
        "semantic": None,
        "hopfield": None,
        "rerank": DISABLED,
    }
    assert result["items"][1]["recall"]["routes"]["hopfield"] == 1
    # B33: enabled and semantic ran, but the only hit has no indexed vector.
    hub.put(who["token"], asset("later", title="", body="unindexed approval later"))
    without = hub.retrieve(who["token"], "later")
    assert [i["asset_id"] for i in without["items"]] == ["later"]
    assert without["retrieval"]["stages"]["semantic"] == "on"
    assert without["retrieval"]["stages"]["hopfield"] == "no_seeds"
    assert without["retrieval"]["hopfield"] is False
    assert without["items"][0]["recall"]["routes"]["hopfield"] == "no_seeds"
    assert without["trace"]["stages"]["hopfield"] == "no_seeds"
    hub.hopfield = False
    assert hub.retrieve(who["token"], "later")["retrieval"]["stages"]["hopfield"] == DISABLED


# --- B32: empty source_meta containers are no metadata at all ----------------


def test_empty_source_meta_containers_do_not_bump_the_revision(tmp_path):
    hub, who = make(tmp_path)
    first = hub.put(who["token"], asset("a", body="审批复核"))
    assert "source_meta" not in first
    replay = hub.put(who["token"], asset("a", body="审批复核", source_meta={"links": []}))
    assert replay["revision"] == first["revision"] == 1
    again = hub.put(
        who["token"],
        asset("a", body="审批复核", source_meta={"declared": {}, "links": [], "sections": []}),
    )
    assert again["content_hash"] == first["content_hash"]
    changed = hub.put(
        who["token"], asset("a", body="审批复核", source_meta={"links": ["x"]}, base_revision=1)
    )
    assert changed["revision"] == 2 and changed["source_meta"] == {"links": ["x"]}
