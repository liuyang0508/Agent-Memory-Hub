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

# What a search can rank by meaning: everything a person might ask about. Summaries stay out
# of injection and the catalog but are the only record of how work actually went. Native
# readers grep them; a raw extraction is not meant to be read.
SEMANTIC_MEMORY_SQL = (
    "native_asset_role(json_extract(a.payload,'$.product'),"
    "json_extract(a.payload,'$.path'),json_extract(a.payload,'$.kind')) "
    "<> 'raw_memory'"
)

CODEX_PRODUCTS = frozenset(
    {"codex", "codex-shared", "codex-cli", "codex-desktop", "chatgpt-desktop"}
)


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
