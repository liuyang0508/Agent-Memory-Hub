"""Human-readable preview builders for evolve proposals."""
from __future__ import annotations

from datetime import datetime, timezone

from agent_brain.contracts.memory_item import MemoryItem


def generate_consolidate_preview(project: str, items: list[tuple[MemoryItem, str]]) -> str:
    """Generate preview for consolidation proposal."""
    lines = [f"# Consolidated Memory: {project}", ""]
    lines.append("## Source Items")
    lines.append("")
    for item, _ in items[:10]:
        lines.append(f"- [{item.type}] {item.title} (ID: {item.id})")
    lines.append("")
    lines.append("## Consolidated Content")
    lines.append("")
    lines.append("This section would contain merged content from all source items...")
    return "\n".join(lines)


def generate_promote_preview(item: MemoryItem, body: str, keywords: list[str]) -> str:
    """Generate preview for promotion proposal."""
    lines = [f"# Promoted Knowledge: {item.title}", ""]
    lines.append(f"**Original Type**: {item.type}")
    lines.append("**Promoted To**: decision")
    lines.append(f"**Key Patterns**: {', '.join(keywords)}")
    lines.append("")
    lines.append("## Content")
    lines.append("")
    lines.append(body[:500])
    if len(body) > 500:
        lines.append("...")
    return "\n".join(lines)


def generate_archive_preview(item: MemoryItem) -> str:
    """Generate preview for archive proposal."""
    lines = [f"# Archived Signal: {item.title}", ""]
    lines.append("**Status**: archived")
    lines.append(f"**Original ID**: {item.id}")
    lines.append(f"**Archived At**: {datetime.now(timezone.utc).isoformat()}")
    lines.append("")
    lines.append("This signal has been moved to archive due to age.")
    return "\n".join(lines)


def generate_skill_preview(project: str, tags: str, items: list[tuple[MemoryItem, str]]) -> str:
    """Generate preview for skill generation proposal."""
    lines = [f"# Proposed Skill: {project}", ""]
    lines.append(f"**Source Tags**: {tags}")
    lines.append(f"**Pattern Count**: {len(items)}")
    lines.append("")
    lines.append("## Pattern Analysis")
    lines.append("")
    for item, body in items[:5]:
        lines.append(f"### {item.title}")
        lines.append(f"- Type: {item.type}")
        lines.append(f"- Key insight: {body[:100]}...")
        lines.append("")
    lines.append("## Proposed Skill Structure")
    lines.append("")
    lines.append("This section would define the reusable skill based on identified patterns...")
    return "\n".join(lines)


__all__ = [
    "generate_archive_preview",
    "generate_consolidate_preview",
    "generate_promote_preview",
    "generate_skill_preview",
]
