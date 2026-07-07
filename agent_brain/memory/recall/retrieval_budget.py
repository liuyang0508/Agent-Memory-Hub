"""Token budget helpers for retrieval/read context packing."""

from __future__ import annotations

from collections.abc import Callable


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token). Good enough for budget packing."""
    return len(text) // 4


def pack_within_budget(
    entries: list[dict],
    max_tokens: int,
    token_estimator: Callable[[str], int] = estimate_tokens,
) -> list[dict]:
    """Pack ranked entries into a token budget, demoting the first overflow to summary."""
    used = 0
    packed: list[dict] = []
    for entry in entries:
        full = entry.get("full", "")
        summary = entry.get("summary", "")
        full_tokens = token_estimator(full)
        if used + full_tokens <= max_tokens:
            packed.append({**entry, "text": full, "tier": "full"})
            used += full_tokens
            continue

        summary_tokens = token_estimator(summary)
        if used + summary_tokens <= max_tokens:
            packed.append({**entry, "text": summary, "tier": "summary"})
            used += summary_tokens
        break
    return packed


__all__ = ["estimate_tokens", "pack_within_budget"]
