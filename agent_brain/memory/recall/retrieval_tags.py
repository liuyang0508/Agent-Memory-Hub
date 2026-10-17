from __future__ import annotations

from collections import Counter
import json
import math
import re
from typing import Any
import unicodedata


def _normalized(text: str) -> str:
    return re.sub(r"[\W_]+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def _supported_tag(tag: str, content: str) -> bool:
    """Require a literal content anchor; frequency alone is not evidence.

    Normalize separators and case, but do not infer a project's meaning or
    accept English substrings such as 'api' inside 'rapid'. CJK can be unspaced.
    """
    normalized = _normalized(tag)
    return bool(normalized and re.search(
        r"(?<![a-z0-9])" + re.escape(normalized) + r"(?![a-z0-9])", content,
    ))


def suggest_tags(
    index: Any,
    embedder: Any,
    text: str,
    top_k_neighbors: int = 10,
    max_tags: int = 5,
    *,
    project: str | None = None,
    tenant_id: str | None = None,
    min_similarity: float = 0.5,
    exclude_tags: list[str] | None = None,
    exclude_item_ids: set[str] | None = None,
) -> list[tuple[str, int]]:
    """Suggest content-supported tags from sufficiently similar, scoped items.

    No scope is inferred when project/tenant_id is omitted. Returning fewer
    suggestions (including none) is preferable to importing unrelated labels.
    Suggestions never update the source memory's tags.
    """
    if not text.strip() or top_k_neighbors <= 0 or max_tags <= 0:
        return []
    clauses = ["superseded_by IS NULL"]
    params: list[str] = []
    for column, value in (("project", project), ("tenant_id", tenant_id)):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)
    rows = index.connection.execute(
        "SELECT id, tags_json FROM items_meta WHERE " + " AND ".join(clauses), params,
    ).fetchall()
    candidates = {row[0]: row[1] for row in rows if row[0] not in (exclude_item_ids or set())}
    if not candidates:
        return []
    emb = embedder.embed(text)
    hits = index.vector_search(emb, top_k=top_k_neighbors, allowed_ids=set(candidates))
    if not hits:
        return []
    vectors = index.vector.get_embeddings([hit.id for hit in hits])
    query_norm = math.sqrt(sum(value * value for value in emb))
    content = _normalized(text)
    excluded = {_normalized(tag) for tag in (exclude_tags or [])}
    tag_counter: Counter[str] = Counter()
    for hit in hits:
        vector = vectors.get(hit.id, [])
        denominator = query_norm * math.sqrt(sum(value * value for value in vector))
        similarity = sum(a * b for a, b in zip(emb, vector)) / denominator if denominator else 0
        if similarity < min_similarity:
            continue
        tags = json.loads(candidates[hit.id] or "[]")
        tag_counter.update(list(dict.fromkeys(
            tag for tag in tags
            if _normalized(tag) not in excluded and _supported_tag(tag, content)
        )))
    return tag_counter.most_common(max_tags)


__all__ = ["suggest_tags"]
