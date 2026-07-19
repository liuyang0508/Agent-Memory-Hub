from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from agent_brain.contracts.memory_enums import Sensitivity, memory_enum_value
from agent_brain.contracts.memory_item import MemoryItem, is_valid_memory_item_id
from agent_brain.memory.context.context_firewall_rules import REVIEW_REQUIRED_TAGS
from agent_brain.memory.store.items_store import ItemsStore

SupersessionStatus = Literal["ready", "blocked", "already_applied"]
_ItemLoadStatus = Literal["ok", "missing", "invalid"]

_ITEM_READ_ERRORS = (
    OSError,
    ValueError,
    yaml.YAMLError,
)

SENSITIVITY_RANK = {
    Sensitivity.public.value: 0,
    Sensitivity.internal.value: 1,
    Sensitivity.private.value: 2,
    Sensitivity.secret.value: 3,
}


@dataclass(frozen=True)
class SupersessionResult:
    status: SupersessionStatus
    reason: str
    replacement_id: str
    obsolete_id: str
    dry_run: bool = True
    snapshot: str | None = None
    index_repair_required: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class SupersessionService:
    def __init__(self, brain_dir: Path, store: ItemsStore, index: Any = None) -> None:
        self.brain_dir = Path(brain_dir)
        self.store = store
        self.index = index

    def preview(self, replacement_id: str, obsolete_id: str) -> SupersessionResult:
        if not (
            is_valid_memory_item_id(replacement_id)
            and is_valid_memory_item_id(obsolete_id)
        ):
            return self._blocked(replacement_id, obsolete_id, "INVALID_ITEM_ID")
        if replacement_id == obsolete_id:
            return self._blocked(replacement_id, obsolete_id, "SELF_SUPERSESSION")
        replacement, load_status = self._load_item(replacement_id)
        if replacement is None:
            reason = "ITEM_MISSING" if load_status == "missing" else "ITEM_INVALID"
            return self._blocked(replacement_id, obsolete_id, reason)
        obsolete, load_status = self._load_item(obsolete_id)
        if obsolete is None:
            reason = "ITEM_MISSING" if load_status == "missing" else "ITEM_INVALID"
            return self._blocked(replacement_id, obsolete_id, reason)
        reason = self._validate_pair(replacement, obsolete)
        if reason != "OK":
            return self._blocked(replacement_id, obsolete_id, reason)
        if obsolete.superseded_by == replacement.id:
            return SupersessionResult(
                "already_applied", "ALREADY_APPLIED", replacement.id, obsolete.id
            )
        if obsolete.superseded_by:
            return self._blocked(
                replacement_id, obsolete_id, "OBSOLETE_ALREADY_SUPERSEDED"
            )
        return SupersessionResult("ready", "OK", replacement.id, obsolete.id)

    def _validate_pair(self, replacement: MemoryItem, obsolete: MemoryItem) -> str:
        if replacement.tenant_id != obsolete.tenant_id:
            return "TENANT_MISMATCH"
        if replacement.project != obsolete.project:
            return "PROJECT_MISMATCH"
        if REVIEW_REQUIRED_TAGS & {tag.lower() for tag in replacement.tags}:
            return "REPLACEMENT_REQUIRES_REVIEW"
        if (
            SENSITIVITY_RANK[memory_enum_value(replacement.sensitivity)]
            > SENSITIVITY_RANK[memory_enum_value(obsolete.sensitivity)]
        ):
            return "VISIBILITY_REDUCTION"
        cursor = replacement
        seen = {obsolete.id}
        while cursor.superseded_by:
            next_id = cursor.superseded_by
            if not is_valid_memory_item_id(next_id):
                return "BROKEN_REPLACEMENT_CHAIN"
            if next_id in seen:
                return "SUPERSESSION_CYCLE"
            seen.add(next_id)
            next_item, load_status = self._load_item(next_id)
            if next_item is None or load_status != "ok":
                return "BROKEN_REPLACEMENT_CHAIN"
            cursor = next_item
        return "OK"

    def _load_item(self, item_id: str) -> tuple[MemoryItem | None, _ItemLoadStatus]:
        try:
            item, _ = self.store.get(item_id)
        except FileNotFoundError:
            return None, "missing"
        except _ITEM_READ_ERRORS:
            return None, "invalid"
        if item.id != item_id:
            return None, "invalid"
        return item, "ok"

    @staticmethod
    def _blocked(
        replacement_id: str, obsolete_id: str, reason: str
    ) -> SupersessionResult:
        return SupersessionResult("blocked", reason, replacement_id, obsolete_id)
