"""Authorized native hybrid recall with explicit degraded-state reporting."""

from __future__ import annotations

from dataclasses import replace
import json
import math

from agent_brain.retrieval.fusion import rrf_fusion
from agent_brain.retrieval.mmr import _cosine_sim, mmr_rerank
from agent_brain.retrieval.types import RetrievedItem
from .asset_roles import asset_role
from .common import tokens
from .storage import database
from . import semantic

KINDS = frozenset({"memory", "rule", "skill", "config", "transcript"})
SUPPORTING = frozenset({"conversation_summary", "raw_memory", "transcript"})
CANDIDATES = 100


def retrieve(
    hub, token, query, *, limit=8, kinds=None, include_evidence=False, project_filter=None
):
    kinds = ["memory"] if kinds is None else list(kinds)
    if not kinds or not set(kinds) <= KINDS:
        raise ValueError("invalid retrieval kinds")
    if not isinstance(query, str) or len(query) > 8000:
        raise ValueError("query exceeds 8000 characters")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be 1 to 50")
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
    role_sql = "native_asset_role(json_extract(a.payload,'$.product'),json_extract(a.payload,'$.path'),json_extract(a.payload,'$.kind')) NOT IN ('conversation_summary','raw_memory')"
    if not include_evidence:
        filters.append(role_sql)
    if project_filter is not None:
        filters.append("json_extract(a.payload,'$.project')=?")
    predicate = " AND ".join(filters)
    with database(hub.path) as db:
        # One normalization/classification implementation for returned roles and
        # SQL eligibility, including ./, repeated separators and Windows paths.
        db.create_function("native_asset_role", 3, asset_role, deterministic=True)
        who = hub._identity(db, token)
        db.execute("BEGIN")
        args = [who["tenant"], *kinds] + ([project_filter] if project_filter is not None else [])
        lexical = []
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
            lexical = [{"payload": exact["payload"], "score": -1.0}] + [
                row for row in lexical if json.loads(row["payload"])["asset_id"] != query.strip()
            ]
        assets = {}
        lex_hits = []
        for row in lexical:
            item = json.loads(row["payload"])
            if hub._retrieval_valid(db, item):
                assets[item["asset_id"]] = item
                lex_hits.append(RetrievedItem(item["asset_id"], -row["score"], None, None))
        indexed = []
        metadata = None
        if semantic.exists(db):
            record = db.execute(
                "SELECT descriptor FROM native_semantic_meta WHERE tenant=?", (who["tenant"],)
            ).fetchone()
            if record:
                metadata = json.loads(record["descriptor"])
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
        current = []
        for row in indexed:
            item = json.loads(row["payload"])
            if hub._retrieval_valid(db, item):
                current.append((item, json.loads(row["vectors"])))
    vector_hits = []
    semantic_used = False
    embeddings = {}
    if metadata is None:
        reasons.append("semantic_index_not_built")
    else:
        backend, error = hub._semantic_backend()
        if backend is None:
            reasons.append(error or "semantic_model_not_configured")
        else:
            try:
                if semantic.descriptor(backend) != metadata:
                    reasons.append("semantic_index_model_mismatch_rebuild_required")
                elif query.strip():
                    query_vector = semantic.vector(backend, query)
                    semantic_used = True
                    for item, windows in current:
                        if not windows or any(
                            len(v["vector"]) != backend.dim
                            or not all(math.isfinite(n) for n in v["vector"])
                            for v in windows
                        ):
                            raise ValueError("invalid persisted vector dimensions")
                        best = max(windows, key=lambda v: _cosine_sim(query_vector, v["vector"]))
                        score = _cosine_sim(query_vector, best["vector"])
                        # This is an eligibility floor, not an answer-confidence
                        # claim; corpus/model evaluation must calibrate it.
                        if score >= hub.semantic_min_score:
                            key = item["asset_id"]
                            assets[key] = item
                            item["semantic_match"] = dict(
                                similarity=score,
                                source_field=best["source_field"],
                                char_start=best["start"],
                                char_end=best["end"],
                                offset_unit="unicode_code_points",
                                aggregation="max_window_cosine",
                            )
                            embeddings[key] = best["vector"]
                            vector_hits.append(RetrievedItem(key, score, None, None))
                    vector_hits.sort(key=lambda hit: hit.score, reverse=True)
                    vector_hits = vector_hits[:CANDIDATES]
            except Exception as exc:
                reasons.append("semantic_query_failed:" + type(exc).__name__)
                vector_hits = []
                semantic_used = False
                embeddings = {}
        if len(current) < eligible_count:
            reasons.append("semantic_index_incomplete_rebuild_required")
    fused = rrf_fusion(lex_hits, vector_hits, rrf_k=60, bm25_weight=1.0, vector_weight=1.0)
    for key, item in assets.items():
        item["role"] = asset_role(item["product"], item["path"], item["kind"])
    # Supporting records can be requested, but repetition is not evidence that
    # they should outrank topic facts. Exact ID lookup retains its lexical rank.
    fused = [
        replace(hit, score=hit.score * (0.55 if assets[hit.id]["role"] in SUPPORTING else 1.0))
        for hit in fused
    ]
    fused.sort(key=lambda hit: hit.score, reverse=True)
    reranked = False
    if hub.reranker is not None and fused:
        try:
            scores = hub.reranker(query, [dict(assets[h.id]) for h in fused])
            if len(scores) != len(fused) or not all(math.isfinite(float(s)) for s in scores):
                raise ValueError("invalid rerank scores")
            fused = [replace(h, score=float(s)) for h, s in zip(fused, scores)]
            fused.sort(key=lambda hit: hit.score, reverse=True)
            reranked = True
        except Exception as exc:
            reasons.append("rerank_failed:" + type(exc).__name__)
    fused = mmr_rerank(fused, embeddings, top_k=len(fused), lambda_=hub.mmr_lambda)
    if exact:
        # An exact authorized asset identifier is a deterministic lookup, even
        # when a semantic model associates its words with unrelated documents.
        fused.sort(key=lambda hit: hit.id != query.strip())
    results = []
    duplicates = {}
    # Recheck revocation and revisions after potentially slow model work. An
    # update racing inference is omitted, never returned with old content.
    with database(hub.path) as db:
        now = hub._identity(db, token)
        if now["tenant"] != who["tenant"]:
            raise PermissionError("organization changed during retrieval")
        db.execute("BEGIN")
        for hit in fused:
            item = assets[hit.id]
            row = db.execute(
                "SELECT payload FROM assets WHERE tenant=? AND id=? AND revision=?",
                (who["tenant"], hit.id, item["revision"]),
            ).fetchone()
            if (
                row is None
                or json.loads(row["payload"])["content_hash"] != item["content_hash"]
                or not hub._retrieval_valid(db, item)
            ):
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
                continue
            if len(results) >= limit:
                continue
            item.update(score=hit.score, verified=True, duplicate_sources=[])
            item["retrieval_ranks"] = dict(lexical=hit.bm25_rank, semantic=hit.vector_rank)
            duplicates[duplicate_key] = item
            results.append(item)
    return {
        "items": results,
        "retrieval": {
            "mode": "hybrid" if semantic_used else "lexical",
            "degraded": bool(reasons),
            "reasons": reasons,
            "model": metadata,
            "reranked": reranked,
            "mmr": hub.mmr_lambda is not None and bool(embeddings),
            "candidates": {"lexical": len(lex_hits), "semantic": len(vector_hits)},
            "semantic_index": {"current": len(current), "eligible": eligible_count},
            "answer_status": "candidates_only" if results else "no_match",
        },
    }
