from __future__ import annotations

import logging
import os
import subprocess
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
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.history import BrainHistory

SupersessionStatus = Literal[
    "ready", "blocked", "already_applied", "applied", "reverted"
]
_ItemLoadStatus = Literal["ok", "missing", "invalid"]

_log = logging.getLogger(__name__)
_LIFECYCLE_HISTORY_EXCLUDES = (
    "/runtime/lifecycle-actions.jsonl",
    "/runtime/.lifecycle-ledger.lock",
    "/runtime/.lifecycle-transaction.lock",
)

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

        with lifecycle_transaction_lock(self.brain_dir):
            preview = self.preview(replacement_id, obsolete_id)
            if preview.status != "ready":
                return self._executed(preview)
            current = self._preview_current(replacement_id, obsolete_id)
            if current.status != "ready":
                return self._executed(current)

            replacement, _ = self.store.get(replacement_id)
            obsolete, _ = self.store.get(obsolete_id)
            replacement_ref_preexisted = obsolete.id in replacement.refs.mems
            old_bytes = self._item_path(obsolete.id).read_bytes()
            new_bytes = self._item_path(replacement.id).read_bytes()
            snapshot = self._snapshot(
                f"pre-supersession {replacement.id} -> {obsolete.id}"
            )

            try:
                self.store.update_frontmatter(
                    obsolete.id, superseded_by=replacement.id
                )
                if not replacement_ref_preexisted:
                    self.store.link_mem(replacement.id, obsolete.id)
            except BaseException as error:
                rollback_result = self._rollback_result(
                    "supersede",
                    replacement.id,
                    obsolete.id,
                    replacement_ref_preexisted,
                    old_bytes,
                    new_bytes,
                    snapshot,
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

        with lifecycle_transaction_lock(self.brain_dir):
            preview = self._preview_revert_current(replacement_id, obsolete_id)
            if preview.status != "ready":
                return self._executed(preview)
            current = self._preview_revert_current(replacement_id, obsolete_id)
            if current.status != "ready":
                return self._executed(current)

            replacement, _ = self.store.get(replacement_id)
            obsolete, _ = self.store.get(obsolete_id)
            applied_record = latest_applied_supersession_record(
                self.brain_dir, replacement.id, obsolete.id
            )
            replacement_ref_preexisted = (
                applied_record.replacement_ref_preexisted
                if applied_record is not None
                else True
            )
            old_bytes = self._item_path(obsolete.id).read_bytes()
            new_bytes = self._item_path(replacement.id).read_bytes()
            snapshot = self._snapshot(
                f"pre-revert-supersession {replacement.id} -> {obsolete.id}"
            )

            try:
                self.store.update_frontmatter(obsolete.id, superseded_by=None)
                if not replacement_ref_preexisted:
                    self.store.unlink_mem(replacement.id, obsolete.id)
            except BaseException as error:
                rollback_result = self._rollback_result(
                    "revert-supersession",
                    replacement.id,
                    obsolete.id,
                    replacement_ref_preexisted,
                    old_bytes,
                    new_bytes,
                    snapshot,
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

    def _snapshot(self, message: str) -> str | None:
        history = BrainHistory(self.brain_dir)
        exclude_path = self.brain_dir / ".git" / "info" / "exclude"
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        existing = (
            exclude_path.read_text(encoding="utf-8")
            if exclude_path.is_file()
            else ""
        )
        missing = [
            pattern
            for pattern in _LIFECYCLE_HISTORY_EXCLUDES
            if pattern not in existing.splitlines()
        ]
        if missing:
            prefix = "" if not existing or existing.endswith("\n") else "\n"
            with exclude_path.open("a", encoding="utf-8") as handle:
                handle.write(prefix + "\n".join(missing) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        snapshot = history.snapshot(message)
        if snapshot is not None:
            return snapshot
        latest = history.log(limit=1)
        return latest[0]["sha"] if latest else None

    def _restore_pair(
        self,
        obsolete_id: str,
        obsolete_bytes: bytes,
        replacement_id: str,
        replacement_bytes: bytes,
        snapshot: str | None,
    ) -> None:
        try:
            self.store.restore_raw(obsolete_id, obsolete_bytes)
        except BaseException:
            pass
        try:
            self.store.restore_raw(replacement_id, replacement_bytes)
        except BaseException:
            pass
        if self._pair_matches(
            obsolete_id, obsolete_bytes, replacement_id, replacement_bytes
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
            obsolete_id, obsolete_bytes, replacement_id, replacement_bytes
        ):
            return
        raise RollbackFailedError("ROLLBACK_FAILED")

    def _restore_snapshot_pair(
        self,
        snapshot: str,
        obsolete_id: str,
        replacement_id: str,
    ) -> None:
        brain_root = self.brain_dir.resolve()
        expected_items_dir = (brain_root / "items").resolve()
        actual_items_dir = self.store.items_dir.resolve()
        if actual_items_dir != expected_items_dir:
            raise RollbackFailedError("ROLLBACK_FAILED")

        relative_paths: list[str] = []
        for item_id in (obsolete_id, replacement_id):
            if not is_valid_memory_item_id(item_id):
                raise RollbackFailedError("ROLLBACK_FAILED")
            resolved_path = (actual_items_dir / f"{item_id}.md").resolve()
            if resolved_path.parent != expected_items_dir:
                raise RollbackFailedError("ROLLBACK_FAILED")
            try:
                relative_path = resolved_path.relative_to(brain_root)
            except ValueError as error:
                raise RollbackFailedError("ROLLBACK_FAILED") from error
            relative_paths.append(str(relative_path))

        process = subprocess.run(
            [
                "git",
                "--literal-pathspecs",
                "-C",
                str(brain_root),
                "checkout",
                snapshot,
                "--",
                *relative_paths,
            ],
            capture_output=True,
            text=True,
        )
        if process.returncode != 0:
            raise RollbackFailedError("ROLLBACK_FAILED")

    def _pair_matches(
        self,
        obsolete_id: str,
        obsolete_bytes: bytes,
        replacement_id: str,
        replacement_bytes: bytes,
    ) -> bool:
        try:
            return (
                self._item_path(obsolete_id).read_bytes() == obsolete_bytes
                and self._item_path(replacement_id).read_bytes() == replacement_bytes
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
    ) -> SupersessionResult | None:
        try:
            self._restore_pair(
                obsolete_id,
                obsolete_bytes,
                replacement_id,
                replacement_bytes,
                snapshot,
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
        except BaseException:
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

    @staticmethod
    def _executed(result: SupersessionResult) -> SupersessionResult:
        return replace(result, dry_run=False)

    @staticmethod
    def _blocked(
        replacement_id: str, obsolete_id: str, reason: str
    ) -> SupersessionResult:
        return SupersessionResult("blocked", reason, replacement_id, obsolete_id)
