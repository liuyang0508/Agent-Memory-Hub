"""Read agent transcript jsonl into text spans with byte offsets.

What it does: streams ``~/.claude/projects/*/*.jsonl``, decoding each line's
message text when it matches common agent JSONL shapes, yielding
``TranscriptSpan(text, start_offset, end_offset, role)``. Tolerant of a truncated
final line (a live session may be mid-write) — an undecodable or partial tail line
is skipped rather than raising. Byte offsets are what make harvesting
watermark-resumable: a caller persists ``end_offset`` and later resumes by passing
it back as ``start_offset``.

How to use::

    from agent_brain.memory.evidence.harvest.transcript_reader import read_spans, discover_transcripts
    for path in discover_transcripts():
        for span in read_spans(path, start_offset=watermark.get_offset(path)):
            ...  # span.text, span.start_offset, span.end_offset, span.role

Depends on: stdlib only (json, dataclasses, pathlib). Discovery still defaults
to Claude Code's transcript directory; explicit ingest can pass any JSONL path
that follows a supported message/content shape.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass
class TranscriptSpan:
    """One non-empty message's text plus its byte range within the transcript.

    ``start_offset``/``end_offset`` are byte positions in the file (end is the
    offset just past this line, i.e. the start of the next), so a full read of a
    complete file ends with ``end_offset == path.stat().st_size``.
    """

    text: str
    start_offset: int
    end_offset: int
    role: str


def _content_to_text(content) -> str:
    """Normalize common message content shapes to human-readable text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            c.get("text", "")
            for c in content
            if isinstance(c, dict)
            and (
                c.get("type") in {"text", "input_text", "output_text"}
                or "text" in c
            )
        )
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        if "content" in content:
            return _content_to_text(content["content"])
    return ""


def _candidate_messages(obj: dict) -> list[dict]:
    candidates: list[dict] = []
    for key in ("message", "item"):
        value = obj.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    payload = obj.get("payload")
    if isinstance(payload, dict):
        message = payload.get("message")
        if isinstance(message, dict):
            candidates.append(message)
        item = payload.get("item")
        if isinstance(item, dict):
            candidates.append(item)
        if "content" in payload or "role" in payload:
            candidates.append(payload)
    if "content" in obj or "role" in obj:
        candidates.append(obj)
    return candidates


def _extract_text(obj: dict) -> str:
    """Pull human-readable text from common agent transcript objects.

    Claude Code writes ``message.content``; other runtimes often write top-level
    ``role/content`` or event-wrapped ``item.content`` / ``payload.message``.
    Tool calls, images, and blocks without text yield no text and are skipped.
    """
    for message in _candidate_messages(obj):
        text = _content_to_text(message.get("content", ""))
        if text.strip():
            return text
    return ""


def _extract_role(obj: dict) -> str:
    for message in _candidate_messages(obj):
        role = message.get("role")
        if isinstance(role, str) and role:
            return role
    typ = obj.get("type")
    return typ if isinstance(typ, str) else ""


def read_spans(path: Path, start_offset: int = 0) -> Iterator[TranscriptSpan]:
    """Yield text spans from ``path`` starting at byte ``start_offset``.

    Reads in binary so offsets are exact byte positions regardless of multi-byte
    characters. Empty/whitespace-only and non-text spans are skipped, but their
    bytes still advance the offset so resumption stays aligned. A line that fails
    to decode or parse (e.g. the partially-flushed tail of a live session) is
    skipped without raising.
    """
    with path.open("rb") as fh:
        fh.seek(start_offset)
        offset = start_offset
        for raw in fh:
            new_offset = offset + len(raw)
            try:
                obj = json.loads(raw.decode("utf-8-sig").strip())
            except (UnicodeDecodeError, json.JSONDecodeError):
                offset = new_offset
                continue  # tolerate a truncated/partial tail line
            text = _extract_text(obj)
            if text.strip():
                yield TranscriptSpan(
                    text=text,
                    start_offset=offset,
                    end_offset=new_offset,
                    role=_extract_role(obj),
                )
            offset = new_offset


def discover_transcripts(root: Path | None = None) -> list[Path]:
    """List CC transcript files under ``root`` (defaults to ~/.claude/projects).

    Returns a sorted list (stable order for deterministic harvesting) or an empty
    list when the root is absent — harvesting on a machine with no CC history is
    a clean no-op, never an error.
    """
    root = root or (Path.home() / ".claude" / "projects")
    return sorted(root.glob("*/*.jsonl")) if root.exists() else []
