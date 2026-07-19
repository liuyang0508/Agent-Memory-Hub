from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from agent_brain.contracts.memory_enums import Sensitivity
from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.store.items_store import ItemsStore

SupersessionStatus = Literal["ready", "blocked", "already_applied"]

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
        if replacement_id == obsolete_id:
            return self._blocked(replacement_id, obsolete_id, "SELF_SUPERSESSION")
        try:
            replacement, _ = self.store.get(replacement_id)
            obsolete, _ = self.store.get(obsolete_id)
        except FileNotFoundError:
            return self._blocked(replacement_id, obsolete_id, "ITEM_MISSING")
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
        if "needs-review" in replacement.tags:
            return "REPLACEMENT_REQUIRES_REVIEW"
        if (
            SENSITIVITY_RANK[str(replacement.sensitivity)]
            > SENSITIVITY_RANK[str(obsolete.sensitivity)]
        ):
            return "VISIBILITY_REDUCTION"
        cursor = replacement
        seen = {obsolete.id}
        while cursor.superseded_by:
            if cursor.superseded_by in seen:
                return "SUPERSESSION_CYCLE"
            seen.add(cursor.superseded_by)
            try:
                cursor, _ = self.store.get(cursor.superseded_by)
            except FileNotFoundError:
                return "BROKEN_REPLACEMENT_CHAIN"
        return "OK"

    @staticmethod
    def _blocked(
        replacement_id: str, obsolete_id: str, reason: str
    ) -> SupersessionResult:
        return SupersessionResult("blocked", reason, replacement_id, obsolete_id)
