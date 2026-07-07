from __future__ import annotations

import re

import yaml

from agent_brain.contracts.memory_item import MemoryItem


_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

# Fix v0.5 YAML quirk: `key:[]` (no space after colon) is invalid YAML.
# Replace with `key: []` so yaml.safe_load can parse it.
_YAML_COLON_BRACKET_FIX = re.compile(r"^(\s*\w+):\[\]", re.MULTILINE)


def parse_item_markdown(text: str) -> tuple[MemoryItem, str]:
    text = text.lstrip("\ufeff")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.startswith("---\n"):
        raise ValueError("missing frontmatter")
    _, frontmatter_text, body = text.split("---\n", 2)
    frontmatter_text = _YAML_COLON_BRACKET_FIX.sub(r"\1: []", frontmatter_text)
    data = yaml.load(frontmatter_text, Loader=_YAML_LOADER)
    return MemoryItem.model_validate(data), body.lstrip("\n")


def render_item_markdown(item: MemoryItem, body: str) -> str:
    frontmatter = item.model_dump(mode="json", exclude_none=True)
    yaml_text = yaml.safe_dump(
        frontmatter,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    return f"---\n{yaml_text}---\n\n{body.rstrip()}\n"
