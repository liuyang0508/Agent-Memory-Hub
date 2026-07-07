"""Claude Code local history readers for non-transcript sources."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from agent_brain.memory.evidence.harvest.transcript_reader import TranscriptSpan


def read_claude_markdown_spans(path: Path) -> Iterator[TranscriptSpan]:
    source = Path(path)
    text = source.read_text(encoding="utf-8-sig", errors="ignore").strip()
    if not text:
        return
    if source.parent.name == "plans":
        role = "claude_plan"
        label = "Claude Code plan file"
    elif source.parent.name == "memory":
        role = "claude_memory"
        label = "Claude Code memory file"
    else:
        role = "claude_markdown"
        label = "Claude Code markdown file"
    yield TranscriptSpan(
        text=f"{label}: {source.name}\n\n{text}",
        start_offset=0,
        end_offset=len(text.encode("utf-8")),
        role=role,
    )


def read_claude_task_spans(path: Path) -> Iterator[TranscriptSpan]:
    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8-sig", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    text = _task_text(data, source)
    if not text.strip():
        return
    yield TranscriptSpan(
        text=text,
        start_offset=0,
        end_offset=source.stat().st_size,
        role="claude_task",
    )


def _task_text(data: dict[str, Any], source: Path) -> str:
    def _s(value: Any) -> str:
        return value if isinstance(value, str) else ""

    lines = [
        f"Claude Code task: {_s(data.get('subject')) or source.stem}",
        f"Task ID: {_s(data.get('id')) or source.stem}",
        f"Status: {_s(data.get('status'))}",
        f"Active form: {_s(data.get('activeForm'))}",
        f"Description: {_s(data.get('description'))}",
    ]
    blocks = data.get("blocks")
    blocked_by = data.get("blockedBy")
    if isinstance(blocks, list) and blocks:
        lines.append(f"Blocks: {len(blocks)}")
    if isinstance(blocked_by, list) and blocked_by:
        lines.append(f"Blocked by: {len(blocked_by)}")
    return "\n".join(line for line in lines if line.strip())
