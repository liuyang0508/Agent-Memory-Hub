"""Supersession safety guard for retrieval candidates."""

from __future__ import annotations

from pathlib import Path

from agent_brain.memory.store.item_markdown import parse_item_markdown
from agent_brain.memory.recall.retrieval_types import RetrievedItem


def filter_md_superseded_candidates(
    index,
    candidates: list[RetrievedItem],
    *,
    include_superseded: bool = False,
) -> list[RetrievedItem]:
    """Drop candidates whose markdown source now has ``superseded_by``.

    The sqlite index is rebuildable and may lag when governance code updates
    frontmatter directly. The markdown item is the source of truth, so default
    retrieval performs this cheap candidate-level guard before returning final
    results. Audit callers can opt out with ``include_superseded=True``.
    """
    if include_superseded or not candidates:
        return candidates

    items_dir = _items_dir_for_index(index)
    if items_dir is None:
        return candidates

    filtered: list[RetrievedItem] = []
    for candidate in candidates:
        if _md_superseded(items_dir, candidate.id):
            continue
        filtered.append(candidate)
    return filtered


def _items_dir_for_index(index) -> Path | None:
    db_path = getattr(index, "db_path", None)
    if db_path is None:
        return None
    return Path(db_path).parent / "items"


def _md_superseded(items_dir: Path, item_id: str) -> bool:
    path = items_dir / f"{item_id}.md"
    if not path.exists():
        return False
    try:
        item, _body = parse_item_markdown(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return False
    return bool(item.superseded_by)


__all__ = ["filter_md_superseded_candidates"]
