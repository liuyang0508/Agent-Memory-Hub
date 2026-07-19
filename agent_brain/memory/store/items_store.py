from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from agent_brain.memory.store.item_markdown import parse_item_markdown, render_item_markdown
from agent_brain.contracts.memory_item import MemoryItem, is_valid_memory_item_id
from agent_brain.memory.store.durable_fs import (
    SecureDirectory,
    lifecycle_mutation_capability,
    require_lifecycle_mutation_capability,
)


def _atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    create_missing: bool = False,
    require_durable: bool = False,
) -> None:
    if lifecycle_mutation_capability():
        with SecureDirectory.open(path.parent) as directory:
            directory.atomic_write(path.name, data, create_missing=create_missing)
        return
    if require_durable:
        require_lifecycle_mutation_capability()
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


def make_item_id(title: str, when: datetime | None = None, label: str | None = None) -> str:
    """Build a collision-proof item id: ``mem-{date}-{time}-[{label}-]{slug}-{rand}``.

    The timestamp only has 1s resolution, so two same-title writes in the same
    second used to produce an identical id and crash ItemsStore.write with an
    uncaught FileExistsError (common when multiple agents share the pool). A
    short random suffix removes the collision while keeping the id within the
    schema pattern.
    """
    when = when or datetime.now(timezone.utc).astimezone()
    # Strip path separators from the slug: _ID_PATTERN forbids / and \, so an
    # unsanitized title like "fix a/b" would make MemoryItem reject the id with a
    # raw ValueError — and even if it slipped through, the separator would
    # scatter the md file into accidental subdirectories of items_dir. split()
    # already drops whitespace; collapse any remaining / or \ runs to a dash.
    slug = re.sub(r"[/\\]+", "-", "-".join(title.lower().split()))[:30].strip("-") or uuid.uuid4().hex[:6]
    parts = ["mem", when.strftime("%Y%m%d-%H%M%S")]
    if label:
        parts.append(label)
    parts.append(slug)
    # 8 hex chars (32 bits): a 4-char (16-bit) suffix collided ~7% of the time
    # across 100 same-second/same-title writes (birthday paradox over 65536),
    # realistic under multi-agent bursts. 32 bits makes collision negligible.
    parts.append(uuid.uuid4().hex[:8])
    return "-".join(parts)

_log = logging.getLogger(__name__)
_ITEM_LOCKS_GUARD = threading.Lock()
_ITEM_LOCKS: dict[tuple[int, int, str], threading.RLock] = {}
_ITEM_LOCK_STATE = threading.local()


@dataclass
class _HeldItemLock:
    descriptor: int
    depth: int


@dataclass
class _ItemLockToken:
    key: tuple[int, int, str]
    process_lock: threading.RLock


@dataclass(frozen=True)
class PreparedItemMutation:
    """Validated exact bytes for one locked item mutation; never persisted as metadata."""

    item_id: str
    data: bytes = field(repr=False)
    updated_item: MemoryItem = field(repr=False)


@dataclass
class SkipRecord:
    path: Path
    reason: str


@dataclass
class ScanStats:
    skipped: list[SkipRecord] = field(default_factory=list)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)


class ItemsStore:
    """Append-only md frontmatter items store. md is source of truth."""

    def __init__(self, items_dir: Path) -> None:
        self.items_dir = items_dir
        self.items_dir.mkdir(parents=True, exist_ok=True)
        # Records of items skipped during the most recent iter_all sweep.
        # Callers (e.g. governance pipeline) can read this to surface skipped
        # items in their reports rather than silently dropping them.
        self.last_scan: ScanStats = ScanStats()

    def iter_all(self, include_archived: bool = False) -> Iterator[tuple[MemoryItem, str]]:
        """Yield (MemoryItem, body) for every md file in items_dir (recursive).

        Items under ``items/archived/`` (where ``batch_archive`` moves them) are
        skipped by default — otherwise a reindex/governance sweep would pull
        archived items back into the index, silently undoing the archive and
        inflating counts. Pass ``include_archived=True`` to walk them too.

        On parse / validation failure for a single file, the failure is recorded
        in ``self.last_scan.skipped`` and iteration continues. This prevents one
        malformed historical item from breaking the entire governance pipeline.
        """
        self.last_scan = ScanStats()
        for md_path in sorted(self.items_dir.rglob("*.md")):
            if not include_archived:
                rel_parts = md_path.relative_to(self.items_dir).parts
                if "archived" in rel_parts[:-1]:
                    continue
            try:
                yield self._read_one(md_path)
            except Exception as exc:  # noqa: BLE001 — boundary: any parse error
                reason = f"{type(exc).__name__}: {exc}".splitlines()[0][:200]
                self.last_scan.skipped.append(SkipRecord(path=md_path, reason=reason))
                _log.debug("skip %s: %s", md_path.name, reason)

    def get(self, item_id: str) -> tuple[MemoryItem, str]:
        """Read a single item by ID. Raises FileNotFoundError if missing."""
        md_path = self.items_dir / f"{item_id}.md"
        if not md_path.exists():
            raise FileNotFoundError(f"Item {item_id} not found")
        return self._read_one(md_path)

    def get_nofollow(self, item_id: str) -> tuple[MemoryItem, str]:
        """Read one canonical active item without following filesystem links."""
        if not is_valid_memory_item_id(item_id):
            raise ValueError("invalid memory item id")
        if not (
            hasattr(os, "O_DIRECTORY")
            and hasattr(os, "O_NOFOLLOW")
            and os.open in os.supports_dir_fd
        ):
            raise OSError("NOFOLLOW_READ_UNSUPPORTED")
        directory_fd = os.open(
            self.items_dir,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        with SecureDirectory(directory_fd) as items:
            descriptor, _ = items.open_file(f"{item_id}.md", os.O_RDONLY)
            chunks: list[bytes] = []
            try:
                while True:
                    chunk = os.read(descriptor, 65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
            finally:
                os.close(descriptor)
        text = b"".join(chunks).decode("utf-8-sig")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        return cast(tuple[MemoryItem, str], parse_item_markdown(text))

    def write(self, item: MemoryItem, body: str) -> Path:
        """Write item + body to a md file. Returns the file path."""
        out_path = self.items_dir / f"{item.id}.md"
        if out_path.exists():
            raise FileExistsError(
                f"Item {item.id} already exists at {out_path}. "
                "Append-only: write a new item with new id."
            )
        out_path.write_text(render_item_markdown(item, body), encoding="utf-8")
        return out_path

    def update_frontmatter(self, item_id: str, **updates: object) -> MemoryItem:
        """Update specific frontmatter fields in-place. Returns the updated item.

        Supports nested updates for retention sub-fields via dotted keys:
          update_frontmatter(id, confidence=0.9)
          update_frontmatter(id, **{"retention.access_count": 5})
        """
        if lifecycle_mutation_capability():
            with self.locked_items([item_id]) as locked:
                return locked.update_frontmatter(item_id, **updates)
        return self._update_frontmatter_path(item_id, **updates)

    def _update_frontmatter_path(
        self, item_id: str, **updates: object
    ) -> MemoryItem:
        md_path = self.items_dir / f"{item_id}.md"
        if not md_path.exists():
            raise FileNotFoundError(f"Item {item_id} not found at {md_path}")
        item, body = self._read_one(md_path)
        updated_item = self._updated_item(item, updates)
        _atomic_write_text(md_path, render_item_markdown(updated_item, body))
        return updated_item

    @staticmethod
    def _updated_item(item: MemoryItem, updates: dict[str, object]) -> MemoryItem:
        data = item.model_dump(mode="json", exclude_none=False)
        summary_updated = "summary" in updates
        context_views_updated = "context_views" in updates or any(
            key.startswith("context_views.") for key in updates
        )
        for key, value in updates.items():
            if "." in key:
                parts = key.split(".", 1)
                if parts[0] in data and isinstance(data[parts[0]], dict):
                    data[parts[0]][parts[1]] = value
                else:
                    data[key] = value
            else:
                data[key] = value
        if summary_updated and not context_views_updated:
            context_views = dict(data.get("context_views") or {})
            context_views["locator"] = data.get("summary", "")
            data["context_views"] = context_views
        return MemoryItem.model_validate(data)

    def restore_raw(self, item_id: str, data: bytes) -> None:
        """Restore one active item from transaction rollback bytes."""
        if not is_valid_memory_item_id(item_id):
            raise ValueError("invalid memory item id")
        if lifecycle_mutation_capability():
            with self.locked_items([item_id]) as locked:
                locked.restore_raw(item_id, data)
            return
        md_path = self.items_dir / f"{item_id}.md"
        if not md_path.is_file():
            raise FileNotFoundError(f"Item {item_id} not found at {md_path}")
        _atomic_write_bytes(md_path, data)

    def link_mem(self, source_id: str, target_id: str) -> bool:
        """Add target_id to source_id's refs.mems in md frontmatter.

        The md is the source of truth for refs_graph: reindex/upsert rebuilds
        graph edges from refs.mems. Returns True if the md was modified; no-op
        (False) if the source md is missing or the link already exists.
        """
        if lifecycle_mutation_capability():
            with self.locked_items([source_id]) as locked:
                return locked.link_mem(source_id, target_id)
        md_path = self.items_dir / f"{source_id}.md"
        if not md_path.exists():
            return False
        item, _ = self._read_one(md_path)
        if target_id in item.refs.mems:
            return False
        self._update_frontmatter_path(source_id, refs=self._linked_refs(item, target_id))
        return True

    def unlink_mem(self, source_id: str, target_id: str) -> bool:
        # Strip target_id from source_id's refs.mems in the md frontmatter.
        # The md is the source of truth: HubIndex.upsert repopulates refs_graph
        # from refs.mems on every call, so removing only the sqlite edge
        # (idx.remove_ref) lets the edge resurrect on the next upsert/reindex.
        # Callers that remove an edge must also call this to make the unlink
        # durable. Returns True if the md was modified; no-op (False) if the
        # source md is missing or target_id is not currently linked.
        if lifecycle_mutation_capability():
            with self.locked_items([source_id]) as locked:
                return locked.unlink_mem(source_id, target_id)
        md_path = self.items_dir / f"{source_id}.md"
        if not md_path.exists():
            return False
        item, _ = self._read_one(md_path)
        if target_id not in item.refs.mems:
            return False
        self._update_frontmatter_path(source_id, refs=self._unlinked_refs(item, target_id))
        return True

    @contextmanager
    def locked_items(self, item_ids: list[str]) -> Iterator[LockedItemsView]:
        """Hold sorted, process-reentrant item locks and one stable items fd."""
        canonical = sorted(set(item_ids))
        if not canonical or any(not is_valid_memory_item_id(item_id) for item_id in canonical):
            raise ValueError("invalid memory item id")
        with SecureDirectory.open(self.items_dir) as items:
            root = os.fstat(items.fd)
            with items.child(".amh-item-locks", create=True) as lock_directory:
                tokens: list[_ItemLockToken] = []
                try:
                    for item_id in canonical:
                        tokens.append(
                            self._acquire_item_lock(
                                lock_directory,
                                (root.st_dev, root.st_ino, item_id),
                                f"{item_id}.lock",
                            )
                        )
                    yield LockedItemsView(items, frozenset(canonical))
                finally:
                    for token in reversed(tokens):
                        self._release_item_lock(token)

    @staticmethod
    def _acquire_item_lock(
        lock_directory: SecureDirectory,
        key: tuple[int, int, str],
        name: str,
    ) -> _ItemLockToken:
        with _ITEM_LOCKS_GUARD:
            process_lock = _ITEM_LOCKS.setdefault(key, threading.RLock())
        process_lock.acquire()
        held = getattr(_ITEM_LOCK_STATE, "held", None)
        if held is None:
            held = {}
            _ITEM_LOCK_STATE.held = held
        current = held.get(key)
        if current is not None:
            current.depth += 1
            return _ItemLockToken(key, process_lock)
        descriptor = -1
        try:
            descriptor, created = lock_directory.open_or_create_file(name, os.O_RDWR)
            os.fchmod(descriptor, 0o600)
            if created:
                lock_directory.fsync()
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
            held[key] = _HeldItemLock(descriptor, 1)
            return _ItemLockToken(key, process_lock)
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            process_lock.release()
            raise

    @staticmethod
    def _release_item_lock(token: _ItemLockToken) -> None:
        held = _ITEM_LOCK_STATE.held
        current = held[token.key]
        current.depth -= 1
        try:
            if current.depth == 0:
                import fcntl

                try:
                    fcntl.flock(current.descriptor, fcntl.LOCK_UN)
                except BaseException:
                    _log.warning("ITEM_LOCK_HOUSEKEEPING_FAILED")
                try:
                    os.close(current.descriptor)
                except BaseException:
                    _log.warning("ITEM_LOCK_HOUSEKEEPING_FAILED")
                finally:
                    del held[token.key]
        finally:
            token.process_lock.release()

    @staticmethod
    def _linked_refs(item: MemoryItem, target_id: str) -> dict[str, object]:
        return {
            "files": item.refs.files,
            "urls": item.refs.urls,
            "mems": item.refs.mems + [target_id],
            "commits": item.refs.commits,
            "resources": item.refs.resources,
            "extractions": item.refs.extractions,
        }

    @staticmethod
    def _unlinked_refs(item: MemoryItem, target_id: str) -> dict[str, object]:
        return {
            "files": item.refs.files,
            "urls": item.refs.urls,
            "mems": [mem for mem in item.refs.mems if mem != target_id],
            "commits": item.refs.commits,
            "resources": item.refs.resources,
            "extractions": item.refs.extractions,
        }

    @staticmethod
    def _read_one(path: Path) -> tuple[MemoryItem, str]:
        # utf-8-sig strips a leading BOM if present (Notepad / Obsidian /
        # some Windows editors emit one) and behaves like utf-8 otherwise.
        # Normalize CRLF/CR -> LF so the `---\n` frontmatter probe and the
        # split below work regardless of the file's line endings.
        text = path.read_text(encoding="utf-8-sig")
        try:
            return parse_item_markdown(text)
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from exc


class LockedItemsView:
    def __init__(self, directory: SecureDirectory, item_ids: frozenset[str]) -> None:
        self._directory = directory
        self._item_ids = item_ids

    def read_bytes(self, item_id: str) -> bytes:
        self._require_locked(item_id)
        descriptor, _ = self._directory.open_file(f"{item_id}.md", os.O_RDONLY)
        chunks: list[bytes] = []
        try:
            while True:
                chunk = os.read(descriptor, 65536)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
        finally:
            os.close(descriptor)

    def get(self, item_id: str) -> tuple[MemoryItem, str]:
        data = self.read_bytes(item_id)
        text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        return parse_item_markdown(text)

    def update_frontmatter(self, item_id: str, **updates: object) -> MemoryItem:
        prepared = self.prepare_update_frontmatter(item_id, **updates)
        self.apply_prepared(prepared)
        return prepared.updated_item

    def prepare_update_frontmatter(
        self, item_id: str, **updates: object
    ) -> PreparedItemMutation:
        item, body = self.get(item_id)
        updated = ItemsStore._updated_item(item, updates)
        return PreparedItemMutation(
            item_id,
            render_item_markdown(updated, body).encode("utf-8"),
            updated,
        )

    def apply_prepared(self, prepared: PreparedItemMutation) -> None:
        self._require_locked(prepared.item_id)
        self._directory.atomic_write(
            f"{prepared.item_id}.md", prepared.data
        )

    def restore_raw(self, item_id: str, data: bytes) -> None:
        self._require_locked(item_id)
        self._directory.atomic_write(f"{item_id}.md", data)

    def link_mem(self, source_id: str, target_id: str) -> bool:
        prepared = self.prepare_link_mem(source_id, target_id)
        if prepared is None:
            return False
        self.apply_prepared(prepared)
        return True

    def prepare_link_mem(
        self, source_id: str, target_id: str
    ) -> PreparedItemMutation | None:
        item, _ = self.get(source_id)
        if target_id in item.refs.mems:
            return None
        return self.prepare_update_frontmatter(
            source_id, refs=ItemsStore._linked_refs(item, target_id)
        )

    def unlink_mem(self, source_id: str, target_id: str) -> bool:
        prepared = self.prepare_unlink_mem(source_id, target_id)
        if prepared is None:
            return False
        self.apply_prepared(prepared)
        return True

    def prepare_unlink_mem(
        self, source_id: str, target_id: str
    ) -> PreparedItemMutation | None:
        item, _ = self.get(source_id)
        if target_id not in item.refs.mems:
            return None
        return self.prepare_update_frontmatter(
            source_id, refs=ItemsStore._unlinked_refs(item, target_id)
        )

    def _require_locked(self, item_id: str) -> None:
        if item_id not in self._item_ids:
            raise ValueError("item is not locked")
