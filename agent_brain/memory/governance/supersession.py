from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml

from agent_brain.contracts.memory_enums import Sensitivity, memory_enum_value
from agent_brain.contracts.memory_item import MemoryItem, is_valid_memory_item_id
from agent_brain.memory.context.context_firewall_rules import REVIEW_REQUIRED_TAGS
from agent_brain.memory.governance.lifecycle_ledger import (
    LifecycleLedgerRecord,
    append_lifecycle_record,
    latest_applied_supersession_record,
    lifecycle_transaction_lock,
)
from agent_brain.memory.governance.lifecycle_snapshot import (
    LifecycleSnapshotError,
    LifecycleSnapshotStore,
)
from agent_brain.memory.store.durable_fs import lifecycle_mutation_capability
from agent_brain.memory.store.items_store import ItemsStore, LockedItemsView

SupersessionStatus = Literal[
    "ready", "blocked", "already_applied", "applied", "reverted"
]
_ItemLoadStatus = Literal["ok", "missing", "invalid"]

_log = logging.getLogger(__name__)
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


class RollbackFailedError(RuntimeError):
    """The raw and snapshot rollback paths could not restore exact bytes."""


class SupersessionService:
    def __init__(self, brain_dir: Path, store: ItemsStore, index: Any = None) -> None:
        self.brain_dir = Path(brain_dir)
        self.store = store
        self.index = index
        self.snapshot_store = LifecycleSnapshotStore(
            self.brain_dir, self.store.items_dir
        )

    def preview(self, replacement_id: str, obsolete_id: str) -> SupersessionResult:
        return self._preview_current(replacement_id, obsolete_id)

    def apply(
        self,
        replacement_id: str,
        obsolete_id: str,
        *,
        apply: bool = False,
    ) -> SupersessionResult:
        if not apply:
            return self.preview(replacement_id, obsolete_id)
        if not (
            is_valid_memory_item_id(replacement_id)
            and is_valid_memory_item_id(obsolete_id)
        ):
            return self._executed(self.preview(replacement_id, obsolete_id))
        if not lifecycle_mutation_capability():
            return SupersessionResult(
                "blocked",
                "PLATFORM_UNSUPPORTED",
                replacement_id,
                obsolete_id,
                dry_run=False,
            )

        with (
            lifecycle_transaction_lock(self.brain_dir),
            self.store.locked_items([replacement_id, obsolete_id]) as locked,
        ):
            preview = self.preview(replacement_id, obsolete_id)
            if preview.status == "already_applied":
                return self._already_applied_with_index_sync(preview)
            if preview.status != "ready":
                return self._executed(preview)
            current = self._preview_current(replacement_id, obsolete_id)
            if current.status == "already_applied":
                return self._already_applied_with_index_sync(current)
            if current.status != "ready":
                return self._executed(current)

            replacement, _ = locked.get(replacement_id)
            obsolete, _ = locked.get(obsolete_id)
            replacement_ref_preexisted = obsolete.id in replacement.refs.mems
            old_bytes = locked.read_bytes(obsolete.id)
            new_bytes = locked.read_bytes(replacement.id)
            try:
                snapshot = self._snapshot(
                    obsolete.id, old_bytes, replacement.id, new_bytes
                )
            except LifecycleSnapshotError:
                return SupersessionResult(
                    "blocked",
                    "SNAPSHOT_FAILED",
                    replacement.id,
                    obsolete.id,
                    dry_run=False,
                )

            if not self._revalidate_locked_pair(
                locked,
                replacement.id,
                obsolete.id,
                new_bytes,
                old_bytes,
                reverting=False,
            ):
                self._record_blocked_best_effort(
                    "supersede",
                    "CONCURRENT_MODIFICATION",
                    obsolete.id,
                    replacement.id,
                    snapshot,
                    replacement_ref_preexisted,
                )
                return SupersessionResult(
                    "blocked",
                    "CONCURRENT_MODIFICATION",
                    replacement.id,
                    obsolete.id,
                    dry_run=False,
                    snapshot=snapshot,
                )

            produced_old_bytes = locked.prepare_update_frontmatter(
                obsolete.id, superseded_by=replacement.id
            ).data
            prepared_new = (
                None
                if replacement_ref_preexisted
                else locked.prepare_link_mem(replacement.id, obsolete.id)
            )
            produced_new_bytes = (
                new_bytes if prepared_new is None else prepared_new.data
            )
            try:
                self.store.update_frontmatter(
                    obsolete.id, superseded_by=replacement.id
                )
                if locked.read_bytes(obsolete.id) != produced_old_bytes:
                    raise OSError("PREPARED_MUTATION_MISMATCH")
                if not replacement_ref_preexisted:
                    self.store.link_mem(replacement.id, obsolete.id)
                    if locked.read_bytes(replacement.id) != produced_new_bytes:
                        raise OSError("PREPARED_MUTATION_MISMATCH")
            except BaseException as error:
                rollback_result = self._rollback_result(
                    "supersede",
                    replacement.id,
                    obsolete.id,
                    replacement_ref_preexisted,
                    old_bytes,
                    new_bytes,
                    snapshot,
                    locked,
                    produced_old_bytes,
                    produced_new_bytes,
                )
                if rollback_result is not None:
                    if not isinstance(error, Exception):
                        raise
                    return rollback_result
                self._record_blocked_best_effort(
                    "supersede",
                    "MARKDOWN_UPDATE_FAILED",
                    obsolete.id,
                    replacement.id,
                    snapshot,
                    replacement_ref_preexisted,
                )
                if not isinstance(error, Exception):
                    raise
                return SupersessionResult(
                    "blocked",
                    "MARKDOWN_UPDATE_FAILED",
                    replacement.id,
                    obsolete.id,
                    dry_run=False,
                    snapshot=snapshot,
                )

            try:
                self._record(
                    "supersede",
                    "applied",
                    "OK",
                    obsolete.id,
                    replacement.id,
                    snapshot,
                    replacement_ref_preexisted,
                )
            except BaseException as error:
                rollback_result = self._rollback_result(
                    "supersede",
                    replacement.id,
                    obsolete.id,
                    replacement_ref_preexisted,
                    old_bytes,
                    new_bytes,
                    snapshot,
                    locked,
                    produced_old_bytes,
                    produced_new_bytes,
                )
                if not isinstance(error, Exception):
                    raise
                if rollback_result is not None:
                    return rollback_result
                return SupersessionResult(
                    "blocked",
                    "LEDGER_WRITE_FAILED",
                    replacement.id,
                    obsolete.id,
                    dry_run=False,
                    snapshot=snapshot,
                )

            index_repair_required = not self._sync_index(
                replacement.id, obsolete.id
            )
            return SupersessionResult(
                "applied",
                "OK",
                replacement.id,
                obsolete.id,
                dry_run=False,
                snapshot=snapshot,
                index_repair_required=index_repair_required,
            )

    def revert(
        self,
        replacement_id: str,
        obsolete_id: str,
        *,
        apply: bool = False,
    ) -> SupersessionResult:
        if not apply:
            return self._preview_revert_current(replacement_id, obsolete_id)
        if not (
            is_valid_memory_item_id(replacement_id)
            and is_valid_memory_item_id(obsolete_id)
        ):
            return self._executed(
                self._preview_revert_current(replacement_id, obsolete_id)
            )
        if not lifecycle_mutation_capability():
            return SupersessionResult(
                "blocked",
                "PLATFORM_UNSUPPORTED",
                replacement_id,
                obsolete_id,
                dry_run=False,
            )

        with (
            lifecycle_transaction_lock(self.brain_dir),
            self.store.locked_items([replacement_id, obsolete_id]) as locked,
        ):
            preview = self._preview_revert_current(replacement_id, obsolete_id)
            if preview.status != "ready":
                return self._executed(preview)
            current = self._preview_revert_current(replacement_id, obsolete_id)
            if current.status != "ready":
                return self._executed(current)

            replacement, _ = locked.get(replacement_id)
            obsolete, _ = locked.get(obsolete_id)
            applied_record = latest_applied_supersession_record(
                self.brain_dir, replacement.id, obsolete.id
            )
            replacement_ref_preexisted = (
                applied_record.replacement_ref_preexisted
                if applied_record is not None
                else True
            )
            old_bytes = locked.read_bytes(obsolete.id)
            new_bytes = locked.read_bytes(replacement.id)
            try:
                snapshot = self._snapshot(
                    obsolete.id, old_bytes, replacement.id, new_bytes
                )
            except LifecycleSnapshotError:
                return SupersessionResult(
                    "blocked",
                    "SNAPSHOT_FAILED",
                    replacement.id,
                    obsolete.id,
                    dry_run=False,
                )

            if not self._revalidate_locked_pair(
                locked,
                replacement.id,
                obsolete.id,
                new_bytes,
                old_bytes,
                reverting=True,
            ):
                self._record_blocked_best_effort(
                    "revert-supersession",
                    "CONCURRENT_MODIFICATION",
                    obsolete.id,
                    replacement.id,
                    snapshot,
                    replacement_ref_preexisted,
                )
                return SupersessionResult(
                    "blocked",
                    "CONCURRENT_MODIFICATION",
                    replacement.id,
                    obsolete.id,
                    dry_run=False,
                    snapshot=snapshot,
                )

            produced_old_bytes = locked.prepare_update_frontmatter(
                obsolete.id, superseded_by=None
            ).data
            prepared_new = (
                None
                if replacement_ref_preexisted
                else locked.prepare_unlink_mem(replacement.id, obsolete.id)
            )
            produced_new_bytes = (
                new_bytes if prepared_new is None else prepared_new.data
            )
            try:
                self.store.update_frontmatter(obsolete.id, superseded_by=None)
                if locked.read_bytes(obsolete.id) != produced_old_bytes:
                    raise OSError("PREPARED_MUTATION_MISMATCH")
                if not replacement_ref_preexisted:
                    self.store.unlink_mem(replacement.id, obsolete.id)
                    if locked.read_bytes(replacement.id) != produced_new_bytes:
                        raise OSError("PREPARED_MUTATION_MISMATCH")
            except BaseException as error:
                rollback_result = self._rollback_result(
                    "revert-supersession",
                    replacement.id,
                    obsolete.id,
                    replacement_ref_preexisted,
                    old_bytes,
                    new_bytes,
                    snapshot,
                    locked,
                    produced_old_bytes,
                    produced_new_bytes,
                )
                if rollback_result is not None:
                    if not isinstance(error, Exception):
                        raise
                    return rollback_result
                self._record_blocked_best_effort(
                    "revert-supersession",
                    "MARKDOWN_UPDATE_FAILED",
                    obsolete.id,
                    replacement.id,
                    snapshot,
                    replacement_ref_preexisted,
                )
                if not isinstance(error, Exception):
                    raise
                return SupersessionResult(
                    "blocked",
                    "MARKDOWN_UPDATE_FAILED",
                    replacement.id,
                    obsolete.id,
                    dry_run=False,
                    snapshot=snapshot,
                )

            try:
                self._record(
                    "revert-supersession",
                    "reverted",
                    "OK",
                    obsolete.id,
                    replacement.id,
                    snapshot,
                    replacement_ref_preexisted,
                )
            except BaseException as error:
                rollback_result = self._rollback_result(
                    "revert-supersession",
                    replacement.id,
                    obsolete.id,
                    replacement_ref_preexisted,
                    old_bytes,
                    new_bytes,
                    snapshot,
                    locked,
                    produced_old_bytes,
                    produced_new_bytes,
                )
                if not isinstance(error, Exception):
                    raise
                if rollback_result is not None:
                    return rollback_result
                return SupersessionResult(
                    "blocked",
                    "LEDGER_WRITE_FAILED",
                    replacement.id,
                    obsolete.id,
                    dry_run=False,
                    snapshot=snapshot,
                )

            index_repair_required = not self._sync_index(
                replacement.id, obsolete.id
            )
            return SupersessionResult(
                "reverted",
                "OK",
                replacement.id,
                obsolete.id,
                dry_run=False,
                snapshot=snapshot,
                index_repair_required=index_repair_required,
            )

    def _preview_current(
        self, replacement_id: str, obsolete_id: str
    ) -> SupersessionResult:
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

    def _preview_revert_current(
        self, replacement_id: str, obsolete_id: str
    ) -> SupersessionResult:
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
        if obsolete.superseded_by is None:
            return self._blocked(
                replacement.id, obsolete.id, "SUPERSESSION_NOT_APPLIED"
            )
        if obsolete.superseded_by != replacement.id:
            return self._blocked(
                replacement.id, obsolete.id, "SUPERSESSION_MISMATCH"
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

    def _item_path(self, item_id: str) -> Path:
        if not is_valid_memory_item_id(item_id):
            raise ValueError("invalid memory item id")
        return self.store.items_dir / f"{item_id}.md"

    def _snapshot(
        self,
        obsolete_id: str,
        obsolete_bytes: bytes,
        replacement_id: str,
        replacement_bytes: bytes,
    ) -> str:
        return self.snapshot_store.snapshot_pair(
            obsolete_id,
            obsolete_bytes,
            replacement_id,
            replacement_bytes,
        )

    def _revalidate_locked_pair(
        self,
        locked: LockedItemsView,
        replacement_id: str,
        obsolete_id: str,
        replacement_bytes: bytes,
        obsolete_bytes: bytes,
        *,
        reverting: bool,
    ) -> bool:
        try:
            if locked.read_bytes(replacement_id) != replacement_bytes:
                return False
            if locked.read_bytes(obsolete_id) != obsolete_bytes:
                return False
            replacement, _ = locked.get(replacement_id)
            obsolete, _ = locked.get(obsolete_id)
        except (OSError, UnicodeError, ValueError, yaml.YAMLError):
            return False
        if replacement.id != replacement_id or obsolete.id != obsolete_id:
            return False
        if reverting:
            return obsolete.superseded_by == replacement.id
        return (
            self._validate_pair(replacement, obsolete) == "OK"
            and obsolete.superseded_by is None
        )

    def _restore_pair(
        self,
        obsolete_id: str,
        obsolete_bytes: bytes,
        replacement_id: str,
        replacement_bytes: bytes,
        snapshot: str | None,
        locked: LockedItemsView,
        produced_obsolete_bytes: bytes,
        produced_replacement_bytes: bytes,
    ) -> None:
        try:
            current_obsolete = locked.read_bytes(obsolete_id)
            current_replacement = locked.read_bytes(replacement_id)
        except BaseException as error:
            raise RollbackFailedError("ROLLBACK_FAILED") from error
        if current_obsolete not in {obsolete_bytes, produced_obsolete_bytes} or (
            current_replacement
            not in {replacement_bytes, produced_replacement_bytes}
        ):
            raise RollbackFailedError("ROLLBACK_FAILED")
        try:
            self.store.restore_raw(obsolete_id, obsolete_bytes)
        except BaseException:
            pass
        try:
            self.store.restore_raw(replacement_id, replacement_bytes)
        except BaseException:
            pass
        if self._pair_matches(
            obsolete_id,
            obsolete_bytes,
            replacement_id,
            replacement_bytes,
            locked,
        ):
            return
        if snapshot is not None:
            try:
                self._restore_snapshot_pair(
                    snapshot, obsolete_id, replacement_id
                )
            except BaseException:
                pass
        if self._pair_matches(
            obsolete_id,
            obsolete_bytes,
            replacement_id,
            replacement_bytes,
            locked,
        ):
            return
        raise RollbackFailedError("ROLLBACK_FAILED")

    def _restore_snapshot_pair(
        self,
        snapshot: str,
        obsolete_id: str,
        replacement_id: str,
    ) -> None:
        self.snapshot_store.restore_pair(snapshot, obsolete_id, replacement_id)

    def _pair_matches(
        self,
        obsolete_id: str,
        obsolete_bytes: bytes,
        replacement_id: str,
        replacement_bytes: bytes,
        locked: LockedItemsView,
    ) -> bool:
        try:
            return (
                locked.read_bytes(obsolete_id) == obsolete_bytes
                and locked.read_bytes(replacement_id) == replacement_bytes
            )
        except BaseException:
            return False

    def _rollback_result(
        self,
        action: str,
        replacement_id: str,
        obsolete_id: str,
        replacement_ref_preexisted: bool,
        obsolete_bytes: bytes,
        replacement_bytes: bytes,
        snapshot: str | None,
        locked: LockedItemsView,
        produced_obsolete_bytes: bytes,
        produced_replacement_bytes: bytes,
    ) -> SupersessionResult | None:
        try:
            self._restore_pair(
                obsolete_id,
                obsolete_bytes,
                replacement_id,
                replacement_bytes,
                snapshot,
                locked,
                produced_obsolete_bytes,
                produced_replacement_bytes,
            )
        except RollbackFailedError:
            self._record_blocked_best_effort(
                action,
                "ROLLBACK_FAILED",
                obsolete_id,
                replacement_id,
                snapshot,
                replacement_ref_preexisted,
            )
            return SupersessionResult(
                "blocked",
                "ROLLBACK_FAILED",
                replacement_id,
                obsolete_id,
                dry_run=False,
                snapshot=snapshot,
            )
        return None

    def _record(
        self,
        action: str,
        status: str,
        reason: str,
        obsolete_id: str,
        replacement_id: str | None,
        snapshot: str | None,
        replacement_ref_preexisted: bool,
    ) -> None:
        append_lifecycle_record(
            self.brain_dir,
            LifecycleLedgerRecord(
                action=action,
                timestamp=datetime.now(timezone.utc).isoformat(),
                status=status,
                reason=reason,
                obsolete_id=obsolete_id,
                replacement_id=replacement_id,
                snapshot=snapshot,
                replacement_ref_preexisted=replacement_ref_preexisted,
            ),
        )

    def _record_blocked_best_effort(
        self,
        action: str,
        reason: str,
        obsolete_id: str,
        replacement_id: str,
        snapshot: str | None,
        replacement_ref_preexisted: bool,
    ) -> None:
        try:
            self._record(
                action,
                "blocked",
                reason,
                obsolete_id,
                replacement_id,
                snapshot,
                replacement_ref_preexisted,
            )
        except Exception:
            _log.warning("LIFECYCLE_LEDGER_WRITE_FAILED")

    def _sync_index(self, replacement_id: str, obsolete_id: str) -> bool:
        if self.index is None:
            return False
        try:
            obsolete, obsolete_body = self.store.get(obsolete_id)
            replacement, replacement_body = self.store.get(replacement_id)
            self.index.upsert(obsolete, obsolete_body, embedding=None)
            self.index.upsert(replacement, replacement_body, embedding=None)
        except Exception:
            return False
        return True

    def _already_applied_with_index_sync(
        self, result: SupersessionResult
    ) -> SupersessionResult:
        return replace(
            result,
            dry_run=False,
            index_repair_required=not self._sync_index(
                result.replacement_id, result.obsolete_id
            ),
        )

    @staticmethod
    def _executed(result: SupersessionResult) -> SupersessionResult:
        return replace(result, dry_run=False)

    @staticmethod
    def _blocked(
        replacement_id: str, obsolete_id: str, reason: str
    ) -> SupersessionResult:
        return SupersessionResult("blocked", reason, replacement_id, obsolete_id)
