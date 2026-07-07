"""Pure markdown rendering helpers for Obsidian export."""
from __future__ import annotations

import yaml

from agent_brain.contracts.memory_item import MemoryItem


def build_obsidian_frontmatter(item: MemoryItem) -> dict:
    """Build canonical memory frontmatter with Obsidian-friendly aliases."""
    frontmatter = item.model_dump(mode="json", exclude_none=False)
    frontmatter["created"] = frontmatter.pop("created_at")
    frontmatter["tags"] = [f"memory/{tag}" for tag in item.tags]
    frontmatter["aliases"] = [item.id]
    return frontmatter


def render_obsidian_markdown(
    item: MemoryItem,
    body: str,
    *,
    items_by_id: dict[str, MemoryItem],
) -> str:
    """Render one memory item as an Obsidian-compatible markdown document."""
    yaml_text = yaml.safe_dump(
        build_obsidian_frontmatter(item),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )

    parts = [f"---\n{yaml_text}---\n"]
    parts.append(f"# {item.title}\n")

    if item.summary and item.summary != body[:len(item.summary)]:
        parts.append(f"> {item.summary}\n")

    parts.append(f"\n{body.rstrip()}\n")

    if item.refs.mems:
        parts.append("\n## Related Memories\n")
        for mem_id in item.refs.mems:
            related = items_by_id.get(mem_id)
            if related:
                # Note files are keyed on id, so wikilink by id (the note
                # name) with the title as the display alias.
                parts.append(f"- [[{related.id}|{related.title}]]")
            else:
                parts.append(f"- `{mem_id}` (not found)")
        parts.append("")

    if item.refs.urls:
        parts.append("\n## References\n")
        for url in item.refs.urls:
            parts.append(f"- {url}")
        parts.append("")

    return "\n".join(parts)


__all__ = ["build_obsidian_frontmatter", "render_obsidian_markdown"]
