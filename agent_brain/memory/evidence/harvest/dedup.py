"""Span-level dedup so the same transcript region never archives twice."""
from __future__ import annotations

import hashlib
import re


def span_hash(text: str) -> str:
    norm = re.sub(r"\s+", " ", text).strip().lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def is_duplicate_span(text: str, seen_span_hashes: set[str]) -> bool:
    return ("sha256:" + span_hash(text)) in seen_span_hashes
