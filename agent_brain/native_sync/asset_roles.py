"""Derive retrieval roles from known native layouts without altering source content."""

from __future__ import annotations

from pathlib import PurePosixPath

CODEX_PRODUCTS = frozenset({"codex-shared", "codex-cli", "codex-desktop", "chatgpt-desktop"})


def asset_role(product: str, path: str, kind: str) -> str:
    """A role describes a document's use, never whether its claims are true."""
    if kind != "memory":
        return kind
    relative = PurePosixPath(path.replace("\\", "/"))
    if product in CODEX_PRODUCTS:
        if relative.parts and relative.parts[0] == "rollout_summaries":
            return "conversation_summary"
        if str(relative) == "raw_memories.md":
            return "raw_memory"
    if relative.name.lower() in {"memory.md", "memory_summary.md"}:
        return "navigation"
    return "topic_memory"
