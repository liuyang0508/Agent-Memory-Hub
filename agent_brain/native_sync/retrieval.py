"""Authorized native hybrid recall with explicit degraded-state reporting and a ranking trace.

Stage order (each stage records before/after rank and score per candidate):
scope filter in SQL → lexical ‖ semantic → RRF → Hopfield association (appended
to the fused list, not re-fused) → supporting weight ×0.55 → optional reranker
→ signals → declared supersession/expiry filter → decay → optional MMR →
exact-id pin → revalidation, dedup and limit.
Disabled stages are reported as 「未启用」 rather than left blank.
"""

from __future__ import annotations

from dataclasses import replace
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any
import secrets
import sqlite3
import json
import math
import time

from agent_brain.retrieval.fusion import rrf_fusion
from agent_brain.retrieval.hopfield import hopfield_expand
from agent_brain.retrieval.mmr import _cosine_sim, mmr_rerank
from agent_brain.retrieval.types import RetrievedItem
from . import facets, recall_ledger, semantic
from .asset_roles import PRIMARY_MEMORY_SQL, asset_role
from .common import digest, tokens
from .storage import DB_TIMEOUT, database

if TYPE_CHECKING:
    from .hub import Hub

KINDS = frozenset({"memory", "rule", "skill", "config", "transcript"})
SUPPORTING = frozenset({"conversation_summary", "raw_memory", "transcript"})
CHANNELS = frozenset({"tool", "hook", "cli", "benchmark"})
CANDIDATES = 100
HOPFIELD_SEEDS = 20
HOPFIELD_SCORE = 0.85
DISABLED = "未启用"
# Hopfield was enabled but no fused candidate carried a vector to seed it with.
NO_SEEDS = "no_seeds"
TRACE_CANDIDATES = 50
TITLE_CHARS = 80
QUERY_PREVIEW_CHARS = 120
# MMR re-orders at least this many of the top candidates; the rest keep their order.
MMR_MIN_TOP_K = 8
# Trace persistence is best effort: it must never hold a hook past its deadline.
TRACE_WRITE_TIMEOUT = 0.3


def _weighted(score: float, weight: float) -> float:
    """Apply a multiplicative weight so that it pulls in the same direction for negative scores."""
    return score + (weight - 1.0) * abs(score)


def _record_trace(hub: Hub, tenant: str, payload: dict[str, Any]) -> bool:
    """Persist the trace on a short-timeout connection; False when the lock or write fails."""
    token = DB_TIMEOUT.set(min(TRACE_WRITE_TIMEOUT, DB_TIMEOUT.get()))
    try:
        with database(hub.path) as db:
            db.execute("BEGIN IMMEDIATE")
            recall_ledger.record_trace(db, tenant, payload)
        return True
    except (sqlite3.Error, OSError):
        return False
    finally:
        DB_TIMEOUT.reset(token)


def _existing_targets(
    db: sqlite3.Connection, tenant: str, targets: set[str]
) -> dict[str, str | None]:
    """Declared ``superseded_by`` targets that exist undeleted here, each with its own target."""
    found: dict[str, str | None] = {}
    keys = sorted(targets)
    for start in range(0, len(keys), 400):
        chunk = keys[start : start + 400]
        rows = db.execute(
            "SELECT id, json_extract(payload,'$.source_meta.declared.superseded_by') target "
            "FROM assets WHERE tenant=? AND json_extract(payload,'$.deleted')=0 AND id IN ("
            + ",".join("?" for _ in chunk)
            + ")",
            [tenant, *chunk],
        ).fetchall()
        for row in rows:
            target = row["target"]
            found[row["id"]] = str(target).strip()[:128] if isinstance(target, str) else None
    return found


class _Trace:
    """Per-candidate ranking history keyed by asset id; identifiers and scores only."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def row(self, key: str) -> dict[str, Any]:
        return self.rows.setdefault(key, {"routes": {}, "stages": [], "state": "candidate"})

    @staticmethod
    def snapshot(hits: Sequence[RetrievedItem]) -> dict[str, tuple[int, float]]:
        return {hit.id: (rank, hit.score) for rank, hit in enumerate(hits, 1)}

    def stage(
        self,
        name: str,
        before: dict[str, tuple[int, float]],
        after: dict[str, tuple[int, float]],
        effects: dict[str, Any] | None = None,
    ) -> None:
        for key in list(before) + [k for k in after if k not in before]:
            previous, current = before.get(key), after.get(key)
            entry: dict[str, Any] = {
                "stage": name,
                "before_rank": previous[0] if previous else None,
                "after_rank": current[0] if current else None,
                "before_score": round(previous[1], 6) if previous else None,
                "after_score": round(current[1], 6) if current else None,
            }
            if effects and key in effects:
                entry["effect"] = effects[key]
            self.row(key)["stages"].append(entry)

    def drop(self, key: str, reason: str, detail: dict[str, Any] | None = None) -> None:
        row = self.row(key)
        row["state"] = "dropped:" + reason
        if detail:
            row["state_detail"] = detail


def _compact_times(times_set: dict[str, Any]) -> dict[str, Any]:
    return {
        key: None
        if value is None
        else {"value": round(value["value"], 3), "source": value["source"]}
        for key, value in times_set.items()
    }


def retrieve(
    hub: Hub,
    token: str,
    query: str,
    *,
    limit: int = 8,
    kinds: Sequence[str] | None = None,
    include_evidence: bool = False,
    project_filter: str | None = None,
    channel: str = "tool",
    record: bool = True,
) -> dict[str, Any]:
    kinds = ["memory"] if kinds is None else list(kinds)
    if not kinds or not set(kinds) <= KINDS:
        raise ValueError("invalid retrieval kinds")
    if not isinstance(query, str) or len(query) > 8000:
        raise ValueError("query exceeds 8000 characters")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be 1 to 50")
    if channel not in CHANNELS:
        raise ValueError("invalid recall channel")
    now = time.time()
    trace = _Trace()
    # The native index has CJK unigrams for explicit character lookup and
    # bigrams for lexical anchors (also used by recall.pack). A long query must
    # not match solely on disconnected one-character overlap such as particles.
    terms = [
        term for term in tokens(query) if len(term) > 1 or term.isascii() or len(query.strip()) == 1
    ][:64]
    reasons = []
    # Filtering happens inside SQL before LIMIT and before ANY model invocation.
    # Default memory requests omit supporting material. Explicit non-memory kinds
    # (e.g. transcript) are themselves an explicit evidence request.
    filters = [
        "a.tenant=?",
        "json_extract(a.payload,'$.deleted')=0",
        "json_extract(a.payload,'$.encoding')='utf8'",
        "json_extract(a.payload,'$.kind') IN (" + ",".join("?" for _ in kinds) + ")",
    ]
    if not include_evidence:
        filters.append(PRIMARY_MEMORY_SQL)
    if project_filter is not None:
        filters.append("json_extract(a.payload,'$.project')=?")
    predicate = " AND ".join(filters)
    with database(hub.path) as db:
        # One normalization/classification implementation for returned roles and
        # SQL eligibility, including ./, repeated separators and Windows paths.
        db.create_function("native_asset_role", 3, asset_role, deterministic=True)
        who = hub._identity(db, token)
        preview_setting = db.execute(
            "SELECT value FROM settings WHERE key='recall_query_preview'"
        ).fetchone()
        db.execute("BEGIN")
        args = [who["tenant"], *kinds] + ([project_filter] if project_filter is not None else [])
        lexical: list[sqlite3.Row | dict[str, Any]] = []
        if terms:
            match = " OR ".join('"' + t + '"' for t in terms)
            lexical = db.execute(
                "SELECT a.payload,bm25(search_index) score FROM search_index JOIN assets a ON a.tenant=search_index.tenant AND a.id=search_index.asset_id WHERE search_index MATCH ? AND "
                + predicate
                + " ORDER BY score LIMIT ?",
                [match, *args, CANDIDATES],
            ).fetchall()
        exact = db.execute(
            "SELECT a.payload FROM assets a WHERE " + predicate + " AND a.id=?",
            [*args, query.strip()],
        ).fetchone()
        if exact:
            exact_hits: list[sqlite3.Row | dict[str, Any]] = [
                {"payload": exact["payload"], "score": -1.0}
            ]
            lexical = exact_hits + [
                row for row in lexical if json.loads(row["payload"])["asset_id"] != query.strip()
            ]
        assets: dict[str, dict[str, Any]] = {}
        lex_hits: list[RetrievedItem] = []
        for row in lexical:
            item = json.loads(row["payload"])
            if hub._retrieval_valid(db, item):
                assets[item["asset_id"]] = item
                lex_hits.append(RetrievedItem(item["asset_id"], -row["score"], None, None))
        indexed: list[sqlite3.Row] = []
        metadata: semantic.SemanticDescriptor | None = None
        if semantic.exists(db):
            record_row = db.execute(
                "SELECT descriptor FROM native_semantic_meta WHERE tenant=?", (who["tenant"],)
            ).fetchone()
            if record_row:
                metadata = json.loads(record_row["descriptor"])
                # Only current authorized revisions are loaded from the derived
                # store; stale/deleted/cross-org rows cannot reach ranking models.
                indexed = db.execute(
                    "SELECT a.payload,v.vectors FROM native_semantic_vectors v JOIN assets a ON a.tenant=v.tenant AND a.id=v.asset_id AND a.revision=v.revision AND json_extract(a.payload,'$.content_hash')=v.content_hash WHERE "
                    + predicate,
                    args,
                ).fetchall()
        eligible_count = db.execute(
            "SELECT count(*) FROM assets a WHERE " + predicate, args
        ).fetchone()[0]
        current: list[tuple[dict[str, Any], list[semantic.StoredWindow]]] = []
        for row in indexed:
            item = json.loads(row["payload"])
            if hub._retrieval_valid(db, item):
                current.append((item, json.loads(row["vectors"])))
    vector_hits: list[RetrievedItem] = []
    semantic_used = False
    embeddings: dict[str, list[float]] = {}
    # Best-window vector per indexed asset, including those under the floor:
    # Hopfield expansion looks at the whole authorized store, not just hits.
    window_vectors: dict[str, list[float]] = {}
    indexed_items: dict[str, dict[str, Any]] = {}
    if metadata is None:
        reasons.append("semantic_index_not_built")
    else:
        backend, error = hub._semantic_backend()
        if backend is None:
            reasons.append(error or "semantic_model_not_configured")
        else:
            # The semantic pass fills private dicts and merges them only after
            # every stored vector was checked: a failure halfway leaves no
            # ghost candidate behind in assets or the trace.
            matched: dict[str, dict[str, Any]] = {}
            found_hits: list[RetrievedItem] = []
            found_embeddings: dict[str, list[float]] = {}
            found_vectors: dict[str, list[float]] = {}
            found_items: dict[str, dict[str, Any]] = {}
            try:
                if semantic.descriptor(backend) != metadata:
                    reasons.append("semantic_index_model_mismatch_rebuild_required")
                elif query.strip():
                    query_vector = semantic.vector(backend, query)
                    for item, windows in current:
                        if not windows or any(
                            len(v["vector"]) != backend.dim
                            or not all(math.isfinite(n) for n in v["vector"])
                            for v in windows
                        ):
                            raise ValueError("invalid persisted vector dimensions")
                        best = max(windows, key=lambda v: _cosine_sim(query_vector, v["vector"]))
                        score = _cosine_sim(query_vector, best["vector"])
                        key = item["asset_id"]
                        found_vectors[key] = best["vector"]
                        found_items[key] = item
                        # This is an eligibility floor, not an answer-confidence
                        # claim; corpus/model evaluation must calibrate it.
                        if score >= hub.semantic_min_score:
                            matched[key] = item
                            item["semantic_match"] = dict(
                                similarity=score,
                                source_field=best["source_field"],
                                char_start=best["start"],
                                char_end=best["end"],
                                offset_unit="unicode_code_points",
                                aggregation="max_window_cosine",
                            )
                            found_embeddings[key] = best["vector"]
                            found_hits.append(RetrievedItem(key, score, None, None))
                    found_hits.sort(key=lambda hit: hit.score, reverse=True)
                    semantic_used = True
                    vector_hits = found_hits[:CANDIDATES]
                    assets.update(matched)
                    embeddings = found_embeddings
                    window_vectors = found_vectors
                    indexed_items = found_items
            except Exception as exc:
                reasons.append("semantic_query_failed:" + type(exc).__name__)
                vector_hits = []
                semantic_used = False
                embeddings = {}
                window_vectors = {}
                indexed_items = {}
        if len(current) < eligible_count:
            reasons.append("semantic_index_incomplete_rebuild_required")
    fused = rrf_fusion(lex_hits, vector_hits, rrf_k=60, bm25_weight=1.0, vector_weight=1.0)
    route_ranks = {hit.id: (hit.bm25_rank, hit.vector_rank) for hit in fused}
    trace.stage("rrf_fusion", {}, trace.snapshot(fused))
    for key, item in assets.items():
        item["role"] = asset_role(item["product"], item["path"], item["kind"])
    # Hopfield association: an attractor formed from the fused seeds pulls in
    # indexed assets that neither route surfaced. It runs at candidate level so
    # every later filter and weight applies to the expanded set as well. Any
    # fused hit with an indexed vector seeds it, lexical hits included.
    hopfield_on = bool(hub.hopfield and semantic_used)
    seeds = (
        [
            (h.id, h.score, window_vectors[h.id])
            for h in fused[:HOPFIELD_SEEDS]
            if h.id in window_vectors
        ]
        if hopfield_on
        else []
    )
    hopfield_used = bool(seeds)
    hopfield_state = "on" if hopfield_used else NO_SEEDS if hopfield_on else DISABLED
    hopfield_ranks: dict[str, int] = {}
    if hopfield_used:
        before = trace.snapshot(fused)
        # The attractor forms over the whole authorized store (seeds included);
        # only assets no route has surfaced are added from it, and the limit
        # counts those additions, not the already-known neighbours.
        _, found = hopfield_expand(
            seeds,
            window_vectors,
            beta=hub.hopfield_beta,
            min_similarity=hub.hopfield_min_score,
            limit=CANDIDATES,
            exclude=set(assets),
        )
        top = fused[0].score if fused else 1.0
        effects: dict[str, Any] = {}
        rank = 0
        for key, similarity in found:
            if key in assets:
                continue
            rank += 1
            item = indexed_items[key]
            item["role"] = asset_role(item["product"], item["path"], item["kind"])
            item["hopfield_match"] = {"similarity": similarity, "rank": rank}
            assets[key] = item
            embeddings[key] = window_vectors[key]
            hopfield_ranks[key] = rank
            fused.append(RetrievedItem(key, top * HOPFIELD_SCORE * similarity, None, None))
            effects[key] = "hopfield_added:%.3f" % similarity
        fused.sort(key=lambda hit: hit.score, reverse=True)
        trace.stage("hopfield_expand", before, trace.snapshot(fused), effects)
    for key in assets:
        lexical_rank, semantic_rank = route_ranks.get(key, (None, None))
        trace.row(key)["routes"] = {
            "lexical": lexical_rank,
            "semantic": semantic_rank if semantic_used else DISABLED,
            "hopfield": hopfield_ranks.get(key) if hopfield_used else hopfield_state,
        }
    # Supporting records can be requested, but repetition is not evidence that
    # they should outrank topic facts. Exact ID lookup retains its lexical rank.
    before = trace.snapshot(fused)
    fused = [
        replace(hit, score=hit.score * (0.55 if assets[hit.id]["role"] in SUPPORTING else 1.0))
        for hit in fused
    ]
    fused.sort(key=lambda hit: hit.score, reverse=True)
    trace.stage(
        "supporting_weight",
        before,
        trace.snapshot(fused),
        {h.id: "x0.55" for h in fused if assets[h.id]["role"] in SUPPORTING},
    )
    reranked = False
    rerank_state = DISABLED
    if hub.reranker is not None and fused:
        before = trace.snapshot(fused)
        try:
            scores = hub.reranker(query, [dict(assets[h.id]) for h in fused])
            if len(scores) != len(fused) or not all(math.isfinite(float(s)) for s in scores):
                raise ValueError("invalid rerank scores")
            fused = [replace(h, score=float(s)) for h, s in zip(fused, scores)]
            fused.sort(key=lambda hit: hit.score, reverse=True)
            reranked = True
            rerank_state = "on"
            trace.stage("rerank", before, trace.snapshot(fused))
        except Exception as exc:
            reasons.append("rerank_failed:" + type(exc).__name__)
            rerank_state = "failed"
    for key in assets:
        trace.row(key)["routes"]["rerank"] = rerank_state if rerank_state != "on" else None
    results: list[dict[str, Any]] = []
    duplicates: dict[tuple[str, str], dict[str, Any]] = {}
    decay_used = bool(hub.decay_enabled)
    mmr_used = hub.mmr_lambda is not None and bool(embeddings)
    # Recheck revocation and revisions after potentially slow model work. An
    # update racing inference is omitted, never returned with old content.
    with database(hub.path) as db:
        now_who = hub._identity(db, token)
        if now_who["tenant"] != who["tenant"]:
            raise PermissionError("organization changed during retrieval")
        db.execute("BEGIN")
        signals = recall_ledger.signals_for(db, who["tenant"], [h.id for h in fused])
        # Declared retirement: only an explicit superseded_by or past expires
        # drops a candidate, and never the exact asset the caller asked for.
        # A superseded_by is honoured only when its target exists here: a
        # typo, a not-yet-synced successor, a self-reference or an A↔B cycle
        # keeps the candidate and notes why on its trace row.
        targets = {t for hit in fused if (t := facets.superseded_target(assets[hit.id]))}
        existing = _existing_targets(db, who["tenant"], targets) if targets else {}
        before = trace.snapshot(fused)
        kept: list[RetrievedItem] = []
        for hit in fused:
            verdict = facets.declared_filter(assets[hit.id], now=now, existing=existing)
            if verdict and not verdict.get("drop", True):
                trace.row(hit.id)["declared_filter"] = {
                    k: v for k, v in verdict.items() if k != "drop"
                }
            elif verdict and hit.id != query.strip():
                trace.drop(hit.id, verdict["reason"], verdict)
                continue
            kept.append(hit)
        fused = kept
        trace.stage("declared_filter", before, trace.snapshot(fused))
        for hit in fused:
            item = assets[hit.id]
            row = trace.row(hit.id)
            row["nature"] = facets.nature(item, item["role"])
            row["times"] = _compact_times(facets.times(item, signals.get(hit.id)))
            if hit.id in signals:
                row["signals"] = signals[hit.id]
        if decay_used:
            before = trace.snapshot(fused)
            effects = {}
            weighted: list[RetrievedItem] = []
            for hit in fused:
                item = assets[hit.id]
                verdict_decay = facets.decay(item, item["role"], signals.get(hit.id), now=now)
                trace.row(hit.id)["decay"] = {
                    k: verdict_decay[k]
                    for k in (
                        "half_life_days",
                        "reference",
                        "age_days",
                        "retention",
                        "multipliers",
                        "coefficient",
                        "weight",
                        "reason",
                    )
                }
                effects[hit.id] = "x%.4f" % verdict_decay["weight"]
                # Reranker scores may be negative logits: the weight must
                # still move a fresher memory up, so it scales the magnitude.
                weighted.append(replace(hit, score=_weighted(hit.score, verdict_decay["weight"])))
            weighted.sort(key=lambda hit: hit.score, reverse=True)
            fused = weighted
            trace.stage("decay", before, trace.snapshot(fused), effects)
        if mmr_used:
            before = trace.snapshot(fused)
            # Diversify only the head that can still reach the caller; the
            # tail keeps its order (MMR cost grows with top_k × candidates).
            top_k = min(len(fused), max(limit * 3, MMR_MIN_TOP_K))
            head = mmr_rerank(fused, embeddings, top_k=top_k, lambda_=hub.mmr_lambda)
            chosen = {hit.id for hit in head}
            fused = head + [hit for hit in fused if hit.id not in chosen]
            trace.stage("mmr", before, trace.snapshot(fused))
        if exact:
            # An exact authorized asset identifier is a deterministic lookup, even
            # when a semantic model associates its words with unrelated documents.
            before = trace.snapshot(fused)
            fused.sort(key=lambda hit: hit.id != query.strip())
            trace.stage("exact_pin", before, trace.snapshot(fused))
        for position, hit in enumerate(fused, 1):
            item = assets[hit.id]
            row = trace.row(hit.id)
            row["rank"] = position
            row["final_score"] = round(hit.score, 6)
            current_row = db.execute(
                "SELECT payload FROM assets WHERE tenant=? AND id=? AND revision=?",
                (who["tenant"], hit.id, item["revision"]),
            ).fetchone()
            if (
                current_row is None
                or json.loads(current_row["payload"])["content_hash"] != item["content_hash"]
                or not hub._retrieval_valid(db, item)
            ):
                trace.drop(hit.id, "revalidation")
                continue
            duplicate_key = (item["body_hash"], item["role"])
            if duplicate_key in duplicates:
                duplicates[duplicate_key]["duplicate_sources"].append(
                    {
                        k: item.get(k)
                        for k in (
                            "asset_id",
                            "revision",
                            "producer_name",
                            "source_device_id",
                            "path",
                        )
                    }
                )
                trace.drop(hit.id, "dedup", {"kept": duplicates[duplicate_key]["asset_id"]})
                continue
            if len(results) >= limit:
                trace.drop(hit.id, "limit", {"limit": limit})
                continue
            item.update(score=hit.score, verified=True, duplicate_sources=[])
            item["retrieval_ranks"] = dict(
                lexical=hit.bm25_rank,
                semantic=hit.vector_rank,
                hopfield=hopfield_ranks.get(hit.id),
            )
            duplicates[duplicate_key] = item
            results.append(item)
            row["state"] = "selected"
            row["final_rank"] = len(results)
    stages = {
        "lexical": "on",
        "semantic": "on" if semantic_used else DISABLED,
        "hopfield": hopfield_state,
        "rerank": rerank_state,
        "declared_filter": "on",
        "decay": "on" if decay_used else DISABLED,
        "mmr": "on" if mmr_used else DISABLED,
    }
    trace_id = secrets.token_hex(12)
    for item in results:
        row = trace.rows[item["asset_id"]]
        item["recall"] = {
            "trace_id": trace_id,
            "routes": row["routes"],
            "final_rank": row["final_rank"],
            "nature": row["nature"]["value"],
            "decay_weight": row["decay"]["weight"] if "decay" in row else None,
            "state": row["state"],
        }
    preview_on = (
        str(preview_setting["value"]).strip().lower() if preview_setting else "on"
    ) != "off"
    # Returned results come first so every item is inside the bounded trace
    # and its delivery feedback can be matched; the rest follow by rank.
    ordered = sorted(
        trace.rows.items(),
        key=lambda pair: (
            pair[1].get("final_rank") is None,
            pair[1].get("final_rank") or 0,
            pair[1].get("rank") is None,
            pair[1].get("rank") or 0,
        ),
    )
    payload = {
        "trace_id": trace_id,
        "created": now,
        "channel": channel,
        "device": who["device_id"],
        "member": who["member_id"],
        "query_digest": digest(query),
        "query_preview": query.strip()[:QUERY_PREVIEW_CHARS] if preview_on else "",
        "query_chars": len(query),
        "limit": limit,
        "kinds": kinds,
        "include_evidence": include_evidence,
        "project_filter": project_filter,
        "stages": stages,
        "retrieval": {"mode": "hybrid" if semantic_used else "lexical", "reasons": list(reasons)},
        "candidates": [
            {
                "asset_id": key,
                "revision": assets[key]["revision"],
                "title": str(assets[key].get("title", ""))[:TITLE_CHARS],
                "path": assets[key].get("path"),
                "product": assets[key].get("product"),
                "project": assets[key].get("project"),
                "kind": assets[key].get("kind"),
                "role": assets[key].get("role"),
                "producer": {
                    "name": assets[key].get("producer_name"),
                    "member_id": assets[key].get("producer_member_id"),
                    "unknown": bool(assets[key].get("producer_unknown")),
                },
                "uploader": assets[key].get("uploader_name"),
                "device": assets[key].get("source_device_id"),
                "chunk": {
                    "part_index": assets[key].get("part_index", 0),
                    "part_count": assets[key].get("part_count", 1),
                },
                **{k: v for k, v in row.items()},
            }
            for key, row in ordered[:TRACE_CANDIDATES]
        ],
    }
    if record and not _record_trace(hub, who["tenant"], payload):
        # Nothing to report feedback against: a trace id that was never
        # stored would only earn the client a 409 for every delivery event.
        reasons.append("recall_trace_not_recorded")
        payload["retrieval"]["reasons"].append("recall_trace_not_recorded")
        trace_id = None
        payload["trace_id"] = None
        for item in results:
            item["recall"]["trace_id"] = None
    return {
        "items": results,
        "retrieval": {
            "mode": "hybrid" if semantic_used else "lexical",
            "degraded": bool(reasons),
            "reasons": reasons,
            "model": metadata,
            "reranked": reranked,
            "mmr": mmr_used,
            "hopfield": hopfield_used,
            "decay": decay_used,
            "stages": stages,
            "trace_id": trace_id,
            "candidates": {
                "lexical": len(lex_hits),
                "semantic": len(vector_hits),
                "hopfield": len(hopfield_ranks),
            },
            "semantic_index": {"current": len(current), "eligible": eligible_count},
            "answer_status": "candidates_only" if results else "no_match",
        },
        "trace": payload,
    }
