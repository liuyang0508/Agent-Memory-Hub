"""Dry-run summary rewrite previews for governance maintenance."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SummaryRewritePreview:
    """Candidate rewrite metadata for an overlong memory summary."""

    current_summary: str
    current_length: int
    candidate_summary: str
    candidate_length: int
    target_length: int
    strategy: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def preview_summary_rewrite(summary: str, *, target_length: int = 200) -> SummaryRewritePreview:
    """Build a deterministic short-summary candidate without mutating the item."""
    current = _normalize_whitespace(summary)
    if len(current) <= target_length:
        candidate = current
        strategy = "unchanged"
    else:
        candidate = _truncate_at_boundary(current, target_length)
        strategy = "extractive_boundary_truncation"
    return SummaryRewritePreview(
        current_summary=summary,
        current_length=len(summary),
        candidate_summary=candidate,
        candidate_length=len(candidate),
        target_length=target_length,
        strategy=strategy,
    )


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _truncate_at_boundary(text: str, target_length: int) -> str:
    if target_length <= 3:
        return text[:target_length]

    limit = target_length - 3
    candidate = text[:limit].rstrip()
    boundary = _best_boundary(candidate)
    min_boundary = min(60, max(8, limit // 2))
    if boundary >= min_boundary:
        candidate = candidate[:boundary].rstrip()
    return candidate.rstrip(" ,;:，；：") + "..."


def _best_boundary(text: str) -> int:
    boundaries = [
        ". ",
        "? ",
        "! ",
        "; ",
        ", ",
        "。",
        "？",
        "！",
        "；",
        "，",
        "、",
    ]
    best = -1
    for marker in boundaries:
        idx = text.rfind(marker)
        if idx >= 0:
            best = max(best, idx + len(marker))
    return best


__all__ = ["SummaryRewritePreview", "preview_summary_rewrite"]
