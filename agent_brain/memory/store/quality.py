"""Advisory quality checks for memory items."""

from __future__ import annotations

import re
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem


REQUIRED_BODY_SECTIONS: dict[str, tuple[str, ...]] = {
    "decision": ("**决策**", "**理由**", "**改回去的代价**"),
    "fact": ("**事实**", "**来源**", "**有效期**"),
    "signal": ("**当前状态**", "**影响**", "**期望操作**"),
    "episode": ("**情境**", "**做了什么**", "**结果**", "**学到**"),
    "artifact": ("**产出物**", "**用途**"),
}

_SOURCE_TYPES = {"fact", "decision"}
_MULTIMODAL_PLACEHOLDER_RE = re.compile(
    r"\[(?:Image|Audio|Video|PDF|Document)\s+#\d+\]",
    re.IGNORECASE,
)


def quality_warnings_for(
    item: MemoryItem,
    body: str,
    *,
    brain_dir: Path | None = None,
) -> list[str]:
    warnings: list[str] = []
    item_type = getattr(item.type, "value", item.type)
    sections = REQUIRED_BODY_SECTIONS.get(str(item_type))
    if sections:
        missing = [section for section in sections if section not in body]
        if missing:
            warnings.append(
                f"{item_type} body missing required sections: {', '.join(missing)}"
            )

    refs = item.refs
    source_refs = (
        refs.files
        or refs.urls
        or refs.mems
        or refs.commits
        or refs.resources
        or refs.extractions
    )
    if str(item_type) in _SOURCE_TYPES and not source_refs:
        warnings.append(f"{item_type} item has no source refs")

    placeholders = _MULTIMODAL_PLACEHOLDER_RE.findall(body)
    if placeholders and not (refs.resources or refs.extractions):
        unique = sorted(set(placeholders), key=placeholders.index)
        warnings.append(
            "body contains multimodal placeholder without resource/extraction refs: "
            + ", ".join(unique)
        )

    if brain_dir is not None:
        root = Path(brain_dir)
        for resource_id in refs.resources:
            if not (root / "resources" / f"{resource_id}.json").exists():
                warnings.append(f"refs.resources points to missing resource: {resource_id}")
        for extraction_id in refs.extractions:
            if not (root / "extractions" / f"{extraction_id}.json").exists():
                warnings.append(f"refs.extractions points to missing extraction: {extraction_id}")

    return warnings
