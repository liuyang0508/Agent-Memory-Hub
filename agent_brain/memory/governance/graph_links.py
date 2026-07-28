"""Governed ordinary memory-link mutation shared by every public surface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_brain.contracts.memory_item import is_valid_memory_item_id


@dataclass(frozen=True)
class LinkMemoryResult:
    linked: bool
    status: str
    reason: str
    index_repair_required: bool = False


def link_memory_ref(
    store: Any,
    index: Any,
    source_id: str,
    target_id: str,
    relation: str = "refs",
) -> LinkMemoryResult:
    """Persist the Markdown source of truth before its derived graph edge."""

    if not (
        is_valid_memory_item_id(source_id)
        and is_valid_memory_item_id(target_id)
    ):
        return LinkMemoryResult(False, "blocked", "INVALID_ITEM_ID")

    try:
        source, _source_body = store.get(source_id)
        target, _target_body = store.get(target_id)
    except FileNotFoundError:
        return LinkMemoryResult(False, "blocked", "ITEM_MISSING")
    except Exception:
        return LinkMemoryResult(False, "blocked", "ITEM_INVALID")
    if source.id != source_id or target.id != target_id:
        return LinkMemoryResult(False, "blocked", "ITEM_INVALID")

    if target_id not in source.refs.mems:
        try:
            changed = bool(store.link_mem(source_id, target_id))
            if not changed:
                current, _body = store.get(source_id)
                if target_id not in current.refs.mems:
                    return LinkMemoryResult(
                        False, "blocked", "MARKDOWN_UPDATE_FAILED"
                    )
        except Exception:
            return LinkMemoryResult(
                False, "blocked", "MARKDOWN_UPDATE_FAILED"
            )

    try:
        index.add_ref(source_id, target_id, relation)
    except Exception:
        return LinkMemoryResult(
            True,
            "partial",
            "INDEX_UPDATE_FAILED",
            index_repair_required=True,
        )
    return LinkMemoryResult(True, "linked", "OK")


__all__ = ["LinkMemoryResult", "link_memory_ref"]
