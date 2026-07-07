from __future__ import annotations


def build_cli_update_fields(
    *,
    title: str | None = None,
    summary: str | None = None,
    add_tags: str | None = None,
    current_tags: list[str] | None = None,
    confidence: float | None = None,
    project: str | None = None,
) -> dict[str, object]:
    updates: dict[str, object] = {}
    if title is not None:
        updates["title"] = title
    if summary is not None:
        updates["summary"] = summary
    if confidence is not None:
        updates["confidence"] = confidence
    if project is not None:
        updates["project"] = project
    if add_tags:
        new_tags = [tag.strip() for tag in add_tags.split(",") if tag.strip()]
        updates["tags"] = list(set((current_tags or []) + new_tags))
    return updates
