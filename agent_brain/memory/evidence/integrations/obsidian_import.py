"""Pure markdown parsing helpers for Obsidian import."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import yaml

from agent_brain.contracts.memory_item import MemoryItem


_ID_PATTERN = re.compile(r"^mem-\d{8}-\d{6}-.+$")


@dataclass(frozen=True)
class ParsedObsidianMemory:
    item: MemoryItem
    body: str


def _strip_export_trailers(raw_body: str) -> str:
    body = raw_body
    body = re.sub(r"(?s)(.*)\n## Related Memories\n.*\Z", r"\1", body)
    body = re.sub(r"(?s)(.*)\n## References\n.*\Z", r"\1", body)
    return body


def _strip_rendered_heading_and_summary(body: str) -> str:
    body = re.sub(r"^#\s+.+\n?", "", body, count=1).strip()
    return re.sub(r"^>\s.+\n?", "", body, count=1).strip()


def parse_obsidian_memory_markdown(text: str, fallback_stem: str) -> ParsedObsidianMemory | None:
    """Parse one Obsidian markdown document into a canonical memory item."""
    if not text.startswith("---"):
        return None

    parts = text.split("---", 2)
    if len(parts) < 3:
        return None

    frontmatter = yaml.safe_load(parts[1])
    if not frontmatter or "id" not in frontmatter or not _ID_PATTERN.match(frontmatter["id"]):
        return None

    raw_body = parts[2].strip()
    h1 = re.search(r"^#\s+(.+?)\s*$", raw_body, flags=re.MULTILINE)
    title = h1.group(1).strip() if h1 else fallback_stem.replace("-", " ").title()

    body = _strip_export_trailers(raw_body)
    body = _strip_rendered_heading_and_summary(body)

    tags = [
        tag.replace("memory/", "")
        for tag in frontmatter.get("tags", [])
        if isinstance(tag, str)
    ]
    data: dict[str, Any] = {
        "id": frontmatter["id"],
        "type": frontmatter.get("type", "fact"),
        "created_at": frontmatter.get("created_at", frontmatter.get("created"))
        or datetime.now(timezone.utc),
        "title": title,
        "summary": frontmatter.get("summary") or body[:200],
        "tags": tags,
        "confidence": frontmatter.get("confidence", 0.7),
        "sensitivity": frontmatter.get("sensitivity", "internal"),
    }
    for opt in (
        "project",
        "agent",
        "session",
        "tenant_id",
        "auth_context",
        "schema_version",
    ):
        if frontmatter.get(opt) is not None:
            data[opt] = frontmatter[opt]
    if isinstance(frontmatter.get("refs"), dict):
        data["refs"] = frontmatter["refs"]
    if isinstance(frontmatter.get("retention"), dict):
        data["retention"] = frontmatter["retention"]

    return ParsedObsidianMemory(item=MemoryItem.model_validate(data), body=body)


__all__ = ["ParsedObsidianMemory", "parse_obsidian_memory_markdown"]
