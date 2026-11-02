"""Bounded public candidates, separate from the Hub's full internal records.

Excerpts are explicitly partial source text. A version hash identifies the
original document, never the excerpt or a claim's factual correctness.
"""

from __future__ import annotations

import json
import math
from typing import Any

from .knowledge import _source

MAX_RESPONSE_BYTES = 12000
MAX_EXCERPT_BYTES = 1200
MAX_ITEMS = 20


def _clip(value: str, byte_limit: int) -> str:
    return value[:byte_limit].encode()[:byte_limit].decode("utf8", errors="ignore")


def _size(value: dict[str, Any]) -> int:
    # Matches Starlette JSONResponse's compact UTF-8 wire representation,
    # including escaping of quotes, newlines and control characters.
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    )


def _diagnostics(result: dict[str, Any]) -> dict[str, Any]:
    """Project known aggregate fields, never arbitrary nested server state."""
    raw = result.get("retrieval", {})
    meta: dict[str, Any] = {
        "mode": raw.get("mode") if raw.get("mode") in {"lexical", "hybrid"} else "unknown",
        "degraded": bool(raw.get("degraded", False)),
        "reasons": [_clip(str(value), 80) for value in raw.get("reasons", [])[:10]],
        "reranked": bool(raw.get("reranked", False)),
        "mmr": bool(raw.get("mmr", False)),
    }
    for group, fields in (
        ("candidates", ("lexical", "semantic")),
        ("semantic_index", ("current", "eligible")),
    ):
        values = raw.get(group, {})
        meta[group] = {key: values[key] for key in fields if type(values.get(key)) is int}
    model = raw.get("model")
    if isinstance(model, dict):
        meta["model"] = {
            key: _clip(str(model[key]), 128) for key in ("model", "model_version") if key in model
        }
        meta["model"].update(
            {
                key: model[key]
                for key in ("dimension", "index_version")
                if type(model.get(key)) is int
            }
        )
    else:
        meta["model"] = None
    return meta


def _candidate(item: dict[str, Any]) -> dict[str, Any]:
    source = _source(item)
    field, start = "body", 0
    match = item.get("semantic_match")
    projection = None
    if isinstance(match, dict):
        name, left, right = (
            match.get("source_field"),
            match.get("char_start"),
            match.get("char_end"),
        )
        score = match.get("similarity")
        if (
            name in {"body", "index_text"}
            and isinstance(item.get(name), str)
            and type(left) is int
            and type(right) is int
            and 0 <= left <= right <= len(item[name])
            and isinstance(score, (int, float))
            and math.isfinite(score)
            and -1 <= score <= 1
            and match.get("offset_unit") == "unicode_code_points"
            and match.get("aggregation") == "max_window_cosine"
        ):
            field, start = name, left
            projection = {
                key: match[key]
                for key in (
                    "similarity",
                    "source_field",
                    "char_start",
                    "char_end",
                    "offset_unit",
                    "aggregation",
                )
            }
    body = item[field]
    excerpt = _clip(body[start : start + MAX_EXCERPT_BYTES], MAX_EXCERPT_BYTES)
    end = start + len(excerpt)
    result = {
        "asset_id": item["asset_id"],
        "revision": item["revision"],
        "document_id": item.get("document_id") or item["asset_id"],
        "document_hash": item.get("document_hash") or item["body_hash"],
        "part_index": item.get("part_index", 0),
        "part_count": item.get("part_count", 1),
        "role": source["role"],
        "source": source,
        "excerpt": excerpt,
        "excerpt_truncated": start > 0 or end < len(body),
        "excerpt_source_field": field,
        "excerpt_scope": "asset_chunk",
        "excerpt_char_start": start,
        "excerpt_char_end": end,
        "version_verified": item.get("verified") is True,
        "answer_status": "candidates_only",
    }
    if projection is not None:
        result["semantic_match"] = projection
    return result


def bounded_retrieval(result: dict[str, Any]) -> dict[str, Any]:
    """Return at most 20 candidates and 12000 encoded JSON bytes, metadata included.

    Metadata is never silently rewritten to fit. A candidate whose metadata and
    excerpt would exceed the remaining budget is omitted and counted. Callers can
    narrow the query or use an already known asset ID with the versioned read API.
    """
    raw = result.get("items", [])
    response: dict[str, Any] = {
        "items": [],
        "retrieval": _diagnostics(result),
        "truncated": False,
        "omitted_items": len(raw),
    }
    response["retrieval"]["answer_status"] = "candidates_only" if raw else "no_match"
    for item in raw[:MAX_ITEMS]:
        candidate = _candidate(item)
        response["items"].append(candidate)
        if _size(response) > MAX_RESPONSE_BYTES:
            response["items"].pop()
    response["omitted_items"] = len(raw) - len(response["items"])
    response["truncated"] = response["omitted_items"] > 0
    return response
