from __future__ import annotations

import logging
import os
import re
import stat
import tempfile
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agent_brain.memory.store.item_markdown import parse_item_markdown, render_item_markdown
from agent_brain.contracts.memory_item import MemoryItem, is_valid_memory_item_id
from agent_brain.memory.store.durable_fs import (
    SecureDirectory,
    lifecycle_mutation_capability,
    require_lifecycle_mutation_capability,
)
from agent_brain.platform.secure_io import (
    close_descriptor,
    open_child_directory,
    open_directory_path_without_symlinks,
    open_regular_file_at,
    secure_dir_fd_io_supported,
)

_MAX_FALLBACK_ITEM_BYTES = 64 * 1024 * 1024


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


def _is_windows() -> bool:
    return os.name == "nt"


def _atomic_create_bytes_fallback(path: Path, data: bytes) -> None:
    """Publish new bytes without following or replacing an existing target."""

    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    committed = False
    pending_error: BaseException | None = None
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            temp_identity = os.fstat(handle.fileno())
        try:
            os.link(temp_path, path, follow_symlinks=False)
        except (TypeError, NotImplementedError):
            os.link(temp_path, path)
        target_identity = os.lstat(path)
        temp_key = (temp_identity.st_dev, temp_identity.st_ino)
        target_key = (target_identity.st_dev, target_identity.st_ino)
        if (
            not stat.S_ISREG(target_identity.st_mode)
            or not all(temp_key)
            or temp_key != target_key
        ):
            raise OSError("ITEM_CREATE_IDENTITY_MISMATCH")
        committed = True
        try:
            directory = os.open(
                path.parent,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            if not _is_windows():
                raise
            _log.warning("ITEM_DIRECTORY_FSYNC_UNAVAILABLE")
    except BaseException as error:
        pending_error = error
        raise
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            if committed:
                _log.warning("ITEM_TEMP_CLEANUP_FAILED")
            elif pending_error is not None:
                pending_error.add_note("ITEM_TEMP_CLEANUP_FAILED")
            else:
                raise


def _read_descriptor_bytes(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 65536)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _read_regular_file_fallback(path: Path) -> bytes:
    """Best-effort no-symlink regular read when dir-fd traversal is unavailable."""
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode):
        raise OSError("secure read target is not a regular file")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise OSError("secure read target is not a regular file")
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise OSError("secure read target changed before open")
        if opened.st_size > _MAX_FALLBACK_ITEM_BYTES:
            raise OSError("secure read target exceeds size limit")
        chunks: list[bytes] = []
        remaining = _MAX_FALLBACK_ITEM_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        if len(data) > _MAX_FALLBACK_ITEM_BYTES:
            raise OSError("secure read target exceeds size limit")
        if (
            (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or len(data) != after.st_size
        ):
            raise OSError("secure read target changed during read")
        return data
    finally:
        os.close(descriptor)


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
_CATALOG_LOCKS_GUARD = threading.Lock()
_CATALOG_LOCKS: dict[str, threading.RLock] = {}
_CATALOG_LOCK_STATE = threading.local()


@dataclass
class _HeldItemLock:
    descriptor: int
    depth: int


@dataclass
class _ItemLockToken:
    key: tuple[int, int, str]
    process_lock: threading.RLock


@dataclass
class _HeldCatalogLock:
    descriptor: int
    depth: int
    kind: str


@dataclass
class _CatalogLockToken:
    key: str
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
        # The configured store path is trusted. Resolve it once so legitimate
        # platform aliases such as macOS /tmp -> /private/tmp work, while every
        # later POSIX read remains anchored to a canonical, no-follow path.
        self.items_dir = Path(items_dir).expanduser().resolve(strict=False)
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
        try:
            entries = self._iter_nofollow_markdown()
            for md_path, data in entries:
                if not include_archived:
                    rel_parts = md_path.relative_to(self.items_dir).parts
                    if "archived" in rel_parts[:-1]:
                        continue
                try:
                    text = data.decode("utf-8-sig")
                    text = text.replace("\r\n", "\n").replace("\r", "\n")
                    yield parse_item_markdown(text)
                except Exception as exc:  # noqa: BLE001 - parse boundary.
                    self._record_skip(md_path, exc)
        except Exception as exc:  # noqa: BLE001 - traversal boundary.
            self._record_skip(self.items_dir, exc)

    def _iter_nofollow_markdown(self) -> Iterator[tuple[Path, bytes]]:
        """Yield descriptor-anchored regular markdown files in path order."""
        if not secure_dir_fd_io_supported():
            yield from self._iter_fallback_markdown()
            return
        root_descriptor = open_directory_path_without_symlinks(self.items_dir)
        stack: list[tuple[int, tuple[str, ...], Iterator[os.DirEntry[str]]]] = []
        try:
            root_entries = iter(sorted(os.scandir(root_descriptor), key=lambda row: row.name))
            stack.append((root_descriptor, (), root_entries))
            root_descriptor = -1
            while stack:
                directory, relative, entries = stack[-1]
                try:
                    entry = next(entries)
                except StopIteration:
                    close_descriptor(directory)
                    stack.pop()
                    continue
                if not relative and entry.name == ".amh-item-locks":
                    continue
                path = self.items_dir.joinpath(*relative, entry.name)
                try:
                    if entry.is_dir(follow_symlinks=False):
                        child = open_child_directory(directory, entry.name)
                        try:
                            child_entries = iter(
                                sorted(os.scandir(child), key=lambda row: row.name)
                            )
                        except BaseException:
                            close_descriptor(child)
                            raise
                        stack.append((child, (*relative, entry.name), child_entries))
                        continue
                    if Path(entry.name).suffix != ".md":
                        continue
                    descriptor = open_regular_file_at(directory, entry.name)
                    try:
                        yield path, _read_descriptor_bytes(descriptor)
                    finally:
                        close_descriptor(descriptor)
                except Exception as exc:  # noqa: BLE001 - per-entry boundary.
                    self._record_skip(path, exc)
        finally:
            if root_descriptor >= 0:
                close_descriptor(root_descriptor)
            while stack:
                directory, _relative, _entries = stack.pop()
                close_descriptor(directory)

    def _iter_fallback_markdown(self) -> Iterator[tuple[Path, bytes]]:
        """Yield ordinary files with lstat/open/fstat race checks."""
        stack = [self.items_dir]
        while stack:
            directory = stack.pop()
            try:
                entries = sorted(os.scandir(directory), key=lambda row: row.name)
            except Exception as exc:  # noqa: BLE001 - traversal boundary.
                self._record_skip(directory, exc)
                continue
            child_directories: list[Path] = []
            for entry in entries:
                path = directory / entry.name
                if directory == self.items_dir and entry.name == ".amh-item-locks":
                    continue
                try:
                    opened = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(opened.st_mode):
                        child_directories.append(path)
                    elif path.suffix == ".md":
                        yield path, _read_regular_file_fallback(path)
                except Exception as exc:  # noqa: BLE001 - per-entry boundary.
                    self._record_skip(path, exc)
            stack.extend(reversed(child_directories))

    def _record_skip(self, path: Path, error: BaseException) -> None:
        reason = f"{type(error).__name__}: {error}".splitlines()[0][:200]
        self.last_scan.skipped.append(SkipRecord(path=path, reason=reason))
        _log.debug("skip %s: %s", path.name, reason)

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
        if secure_dir_fd_io_supported():
            directory_fd = open_directory_path_without_symlinks(self.items_dir)
            try:
                descriptor = open_regular_file_at(directory_fd, f"{item_id}.md")
                try:
                    data = _read_descriptor_bytes(descriptor)
                finally:
                    close_descriptor(descriptor)
            finally:
                close_descriptor(directory_fd)
        else:
            data = _read_regular_file_fallback(self.items_dir / f"{item_id}.md")
        text = data.decode("utf-8-sig")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        return parse_item_markdown(text)

    def write(self, item: MemoryItem, body: str) -> Path:
        """Write item + body to a md file. Returns the file path."""
        out_path = self.items_dir / f"{item.id}.md"
        data = render_item_markdown(item, body).encode("utf-8")
        with self.locked_catalog():
            if lifecycle_mutation_capability():
                with self.locked_items([item.id]) as locked:
                    locked.create(item.id, data)
            else:
                _atomic_create_bytes_fallback(out_path, data)
        return out_path

    def update_frontmatter(self, item_id: str, **updates: object) -> MemoryItem:
        """Update specific frontmatter fields in-place. Returns the updated item.

        Supports nested updates for retention sub-fields via dotted keys:
          update_frontmatter(id, confidence=0.9)
          update_frontmatter(id, **{"retention.access_count": 5})
        """
        with self.locked_catalog():
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
        with self.locked_catalog():
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
        with self.locked_catalog():
            if lifecycle_mutation_capability():
                with self.locked_items([source_id]) as locked:
                    return locked.link_mem(source_id, target_id)
            md_path = self.items_dir / f"{source_id}.md"
            if not md_path.exists():
                return False
            item, _ = self._read_one(md_path)
            if target_id in item.refs.mems:
                return False
            self._update_frontmatter_path(
                source_id, refs=self._linked_refs(item, target_id)
            )
            return True

    def unlink_mem(self, source_id: str, target_id: str) -> bool:
        # Strip target_id from source_id's refs.mems in the md frontmatter.
        # The md is the source of truth: HubIndex.upsert repopulates refs_graph
        # from refs.mems on every call, so removing only the sqlite edge
        # (idx.remove_ref) lets the edge resurrect on the next upsert/reindex.
        # Callers that remove an edge must also call this to make the unlink
        # durable. Returns True if the md was modified; no-op (False) if the
        # source md is missing or target_id is not currently linked.
        with self.locked_catalog():
            if lifecycle_mutation_capability():
                with self.locked_items([source_id]) as locked:
                    return locked.unlink_mem(source_id, target_id)
            md_path = self.items_dir / f"{source_id}.md"
            if not md_path.exists():
                return False
            item, _ = self._read_one(md_path)
            if target_id not in item.refs.mems:
                return False
            self._update_frontmatter_path(
                source_id, refs=self._unlinked_refs(item, target_id)
            )
            return True

    def delete(self, item_id: str) -> bool:
        """Delete one active item under catalog -> item locking."""

        if not is_valid_memory_item_id(item_id):
            raise ValueError("invalid memory item id")
        with self.locked_catalog():
            if lifecycle_mutation_capability():
                with self.locked_items([item_id]) as locked:
                    return locked.delete(item_id)
            path = self.items_dir / f"{item_id}.md"
            try:
                opened = os.lstat(path)
            except FileNotFoundError:
                return False
            if not stat.S_ISREG(opened.st_mode):
                raise OSError("UNSAFE_ITEM_DELETE_TARGET")
            path.unlink()
            try:
                directory = os.open(
                    self.items_dir,
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_DIRECTORY", 0),
                )
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except OSError:
                if not _is_windows():
                    raise
                _log.warning("ITEM_DIRECTORY_FSYNC_UNAVAILABLE")
            return True

    @contextmanager
    def locked_catalog(self) -> Iterator[None]:
        """Hold the brain-wide write catalog lock, reentrant across store instances."""

        key = str(self.items_dir.parent.resolve(strict=False))
        with _CATALOG_LOCKS_GUARD:
            process_lock = _CATALOG_LOCKS.setdefault(key, threading.RLock())
        process_lock.acquire()
        held = getattr(_CATALOG_LOCK_STATE, "held", None)
        if held is None:
            held = {}
            _CATALOG_LOCK_STATE.held = held
        current = held.get(key)
        if current is not None:
            current.depth += 1
            try:
                yield
            finally:
                current.depth -= 1
                process_lock.release()
            return

        descriptor = -1
        try:
            descriptor, kind = self._acquire_catalog_file_lock()
            held[key] = _HeldCatalogLock(descriptor=descriptor, depth=1, kind=kind)
            descriptor = -1
            try:
                yield
            finally:
                current = held[key]
                current.depth -= 1
                if current.depth == 0:
                    self._release_catalog_file_lock(current)
                    del held[key]
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            process_lock.release()

    def _acquire_catalog_file_lock(self) -> tuple[int, str]:
        descriptor = -1
        try:
            if lifecycle_mutation_capability():
                with SecureDirectory.open(self.items_dir.parent) as brain:
                    with brain.child("runtime", create=True) as runtime:
                        with runtime.child("locks", create=True) as locks:
                            with locks.child("catalog", create=True) as catalog:
                                descriptor, created = catalog.open_or_create_file(
                                    "write.lock", os.O_RDWR
                                )
                                os.fchmod(descriptor, 0o600)
                                if os.fstat(descriptor).st_size == 0:
                                    os.write(descriptor, b"\0")
                                    os.fsync(descriptor)
                                if created:
                                    catalog.fsync()
            else:
                current = self.items_dir.parent
                for component in ("runtime", "locks", "catalog"):
                    candidate = current / component
                    try:
                        os.mkdir(candidate, 0o700)
                    except FileExistsError:
                        pass
                    opened_directory = os.lstat(candidate)
                    if not stat.S_ISDIR(opened_directory.st_mode) or bool(
                        int(getattr(opened_directory, "st_file_attributes", 0) or 0)
                        & 0x0400
                    ):
                        raise OSError("UNSAFE_CATALOG_LOCK_DIRECTORY")
                    current = candidate
                lock_path = current / "write.lock"
                descriptor = os.open(
                    lock_path,
                    os.O_RDWR
                    | os.O_CREAT
                    | getattr(os, "O_BINARY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                opened = os.fstat(descriptor)
                path_identity = os.lstat(lock_path)
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or not stat.S_ISREG(path_identity.st_mode)
                    or (opened.st_dev, opened.st_ino)
                    != (path_identity.st_dev, path_identity.st_ino)
                ):
                    raise OSError("UNSAFE_CATALOG_LOCK_FILE")
                if opened.st_size == 0:
                    os.write(descriptor, b"\0")
                    os.fsync(descriptor)

            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                getattr(msvcrt, "locking")(
                    descriptor, getattr(msvcrt, "LK_LOCK"), 1
                )
                return descriptor, "msvcrt"
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
            return descriptor, "fcntl"
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            raise

    @staticmethod
    def _release_catalog_file_lock(current: _HeldCatalogLock) -> None:
        try:
            if current.kind == "msvcrt":
                import msvcrt

                os.lseek(current.descriptor, 0, os.SEEK_SET)
                getattr(msvcrt, "locking")(
                    current.descriptor, getattr(msvcrt, "LK_UNLCK"), 1
                )
            else:
                import fcntl

                fcntl.flock(current.descriptor, fcntl.LOCK_UN)
        except BaseException:
            _log.warning("CATALOG_LOCK_HOUSEKEEPING_FAILED")
        try:
            os.close(current.descriptor)
        except BaseException:
            _log.warning("CATALOG_LOCK_HOUSEKEEPING_FAILED")

    @contextmanager
    def locked_items(self, item_ids: list[str]) -> Iterator[LockedItemsView]:
        """Hold sorted, process-reentrant item locks and one stable items fd."""
        canonical = sorted(set(item_ids))
        if not canonical or any(not is_valid_memory_item_id(item_id) for item_id in canonical):
            raise ValueError("invalid memory item id")
        with self.locked_catalog():
            with SecureDirectory.open(self.items_dir.parent) as brain:
                with brain.child("runtime", create=True) as runtime:
                    with runtime.child("locks", create=True) as locks:
                        with locks.child("items", create=True) as lock_directory:
                            with SecureDirectory.open(self.items_dir) as items:
                                root = os.fstat(items.fd)
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
        return self.read_version(item_id)[0]

    def read_version(self, item_id: str) -> tuple[bytes, tuple[int, int]]:
        """Read exact regular bytes plus stable device/inode under the item lock."""
        self._require_locked(item_id)
        descriptor, _ = self._directory.open_file(
            f"{item_id}.md", os.O_RDONLY | os.O_NONBLOCK
        )
        try:
            before = os.fstat(descriptor)
            data = _read_descriptor_bytes(descriptor)
            after = os.fstat(descriptor)
            if (
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or len(data) != after.st_size
            ):
                raise OSError("ITEM_CHANGED_DURING_READ")
            return data, (after.st_dev, after.st_ino)
        finally:
            os.close(descriptor)

    def get(self, item_id: str) -> tuple[MemoryItem, str]:
        data = self.read_bytes(item_id)
        text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        return parse_item_markdown(text)

    def create(self, item_id: str, data: bytes) -> None:
        """Durably create one locked item without following unsafe targets."""

        self._require_locked(item_id)
        self._directory.atomic_create(
            f"{item_id}.md",
            data,
        )

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

    def delete(self, item_id: str) -> bool:
        self._require_locked(item_id)
        name = f"{item_id}.md"
        try:
            opened = self._directory.stat(name)
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(opened.st_mode):
            raise OSError("UNSAFE_ITEM_DELETE_TARGET")
        self._directory.unlink(name)
        self._directory.fsync()
        return True

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
