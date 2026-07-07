"""Semantic Compression — beyond mechanical concat.

Takes N related items and produces a single distilled summary via LLM,
preserving provenance and key insights. Unlike consolidation.py (which
template-merges same-tag L0 facts), this compresses across types and tags
based on semantic clustering.

Modes:
  - LLM mode (default): calls configured LLM for true semantic compression
  - Mechanical fallback: extractive summarization (title+summary concat with dedup)

The output is an L2 item (distilled) that references all source items.
Source items are NOT deleted — they remain for provenance.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.governance.compressor_candidates import find_compression_candidates
from agent_brain.memory.governance.compressor_types import CompressionCandidate, CompressionReport
from agent_brain.memory.governance.compressor_writeback import build_compressed_item, mark_sources_superseded
from agent_brain.contracts.memory_item import (
    MemoryItem,
)

_log = logging.getLogger(__name__)

_COMPRESS_SYSTEM_PROMPT = """\
You are a memory compression engine. Given multiple related memory items,
produce a single distilled summary that:
1. Captures ALL key decisions, facts, and insights (nothing important lost)
2. Removes redundancy and overlap between items
3. Organizes information logically (decisions first, then supporting facts)
4. Is concise but complete — aim for 30-50% length reduction

Respond with a JSON object:
{
  "title": "compressed topic title",
  "summary": "1-2 sentence overview",
  "body": "full compressed content preserving all key information",
  "tags": ["tag1", "tag2", ...]
}

Only output the JSON, no markdown fences or extra text."""


def _mechanical_compress(candidate: CompressionCandidate) -> tuple[str, str, str, list[str]]:
    """Extractive fallback: deduplicate and merge titles+summaries."""
    seen_summaries: set[str] = set()
    lines: list[str] = []
    all_tags: set[str] = set()

    for item, body in candidate.items:
        all_tags.update(item.tags)
        norm_summary = item.summary.strip().lower()
        if norm_summary not in seen_summaries:
            seen_summaries.add(norm_summary)
            lines.append(f"- **{item.title}**: {item.summary}")

    title = f"[compressed] {len(candidate.items)} items — {candidate.reason}"
    summary = f"Mechanical compression of {len(candidate.items)} items ({candidate.total_chars} chars)"
    body = "\n".join(lines)
    return title, summary, body, sorted(all_tags | {"compressed"})


def _llm_compress(candidate: CompressionCandidate) -> tuple[str, str, str, list[str]] | None:
    """Semantic compression via LLM."""
    if os.environ.get("MEMORY_HUB_NO_MODEL") == "1":
        return None

    try:
        import litellm
    except ImportError:
        return None

    items_text = []
    for item, body in candidate.items:
        items_text.append(
            f"[{item.type}] {item.title}\n"
            f"Summary: {item.summary}\n"
            f"Tags: {', '.join(item.tags)}\n"
            f"Body: {body[:300]}"
        )

    prompt = "\n\n---\n\n".join(items_text)
    if len(prompt) > 8000:
        prompt = prompt[:8000] + "\n\n[...truncated...]"

    model = os.environ.get("MEMORY_HUB_LLM_MODEL", "gpt-4o-mini")
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": _COMPRESS_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 2048,
    }

    base_url = os.environ.get("MEMORY_HUB_LLM_BASE_URL")
    if base_url:
        kwargs["api_base"] = base_url
    api_key = os.environ.get("MEMORY_HUB_LLM_API_KEY")
    if api_key:
        kwargs["api_key"] = api_key

    try:
        import json
        response = litellm.completion(**kwargs)
        text = response.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        result = json.loads(text)
        return (
            result.get("title", f"Compressed {len(candidate.items)} items"),
            result.get("summary", "LLM-compressed summary"),
            result.get("body", ""),
            result.get("tags", []) + ["compressed"],
        )
    except Exception as e:
        _log.warning("LLM compression failed: %s", e)
        return None


def compress(
    store: ItemsStore,
    *,
    candidates: list[CompressionCandidate] | None = None,
    project: str | None = None,
    use_llm: bool = True,
    dry_run: bool = True,
) -> CompressionReport:
    """Run semantic compression on the brain pool.

    Args:
        store: Items store to compress
        candidates: Pre-computed candidates (if None, auto-discovers)
        project: Filter to a specific project
        use_llm: Try LLM compression (falls back to mechanical)
        dry_run: If True, only report what would be compressed
    """
    if candidates is None:
        candidates = find_compression_candidates(store, project=project)

    report = CompressionReport(
        scanned=sum(len(c.items) for c in candidates),
        candidates=candidates,
    )

    for candidate in candidates:
        report.chars_before += candidate.total_chars

        if dry_run:
            report.chars_after += int(candidate.total_chars * (1 - candidate.estimated_reduction))
            continue

        result = None
        if use_llm:
            result = _llm_compress(candidate)

        if result:
            title, summary, body, tags = result
        else:
            title, summary, body, tags = _mechanical_compress(candidate)

        compressed_item = build_compressed_item(
            candidate,
            title=title,
            summary=summary,
            tags=tags,
        )
        store.write(compressed_item, body)
        report.compressed.append(compressed_item)
        report.chars_after += len(body) + len(title) + len(summary)

        mark_sources_superseded(store, candidate, compressed_item_id=compressed_item.id)

    return report
