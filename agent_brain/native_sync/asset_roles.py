"""Derive retrieval roles from known native layouts without altering source content."""

from __future__ import annotations

from pathlib import PurePosixPath

# Shared eligibility fragment for an assets table aliased as `a`. Connections
# register native_asset_role with the function below, so normalization is identical.
PRIMARY_MEMORY_SQL = (
    "native_asset_role(json_extract(a.payload,'$.product'),"
    "json_extract(a.payload,'$.path'),json_extract(a.payload,'$.kind')) "
    "NOT IN ('conversation_summary','raw_memory')"
)

# Session digests are summaries, kept out of injection and the catalog. But they are the
# only record of recent work and are short, so a search must be able to rank them.
DIGEST_PREFIX = "session_digests/"
SEMANTIC_MEMORY_SQL = (
    "("
    + PRIMARY_MEMORY_SQL
    + " OR substr(json_extract(a.payload,'$.path'),1,"
    + str(len(DIGEST_PREFIX))
    + ")='"
    + DIGEST_PREFIX
    + "')"
)

CODEX_PRODUCTS = frozenset(
    {"codex", "codex-shared", "codex-cli", "codex-desktop", "chatgpt-desktop"}
)


def is_session_digest(path: str) -> bool:
    return path.startswith(DIGEST_PREFIX)


def asset_role(product: str, path: str, kind: str) -> str:
    """A role describes a document's use, never whether its claims are true."""
    if kind != "memory":
        return kind
    relative = PurePosixPath(path.replace("\\", "/"))
    if relative.parts and relative.parts[0] == "session_digests":
        return "conversation_summary"
    if product in CODEX_PRODUCTS:
        if relative.parts and relative.parts[0] == "rollout_summaries":
            return "conversation_summary"
        if str(relative) == "raw_memories.md":
            return "raw_memory"
    if relative.name.lower() in {"memory.md", "memory_summary.md"}:
        return "navigation"
    return "topic_memory"
