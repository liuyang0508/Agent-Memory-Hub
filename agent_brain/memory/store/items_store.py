from __future__ import annotations

import logging
import os
import re
import stat
import tempfile
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agent_brain.memory.store.item_markdown import parse_item_markdown, render_item_markdown
from agent_brain.contracts.memory_item import MemoryItem, is_valid_memory_item_id


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    parent_descriptor = _open_parent_directory(path)
    fd = -1
    temp_path: Path | None = None
    try:
        target_stat = path.lstat()
        if not stat.S_ISREG(target_stat.st_mode):
            raise OSError("UNSAFE_ATOMIC_WRITE_TARGET")
        original_mode = stat.S_IMODE(target_stat.st_mode)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temp_path = Path(temp_name)
        os.fchmod(fd, original_mode)
        handle = os.fdopen(fd, "wb")
        fd = -1
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        os.fsync(parent_descriptor)
    finally:
        if fd >= 0:
            os.close(fd)
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        os.close(parent_descriptor)


def _open_parent_directory(path: Path) -> int:
    """Preflight and hold the exact parent directory used for durability."""
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if os.name == "nt" or no_follow is None:
        raise OSError("DIRECTORY_FSYNC_UNSUPPORTED")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | no_follow
    parent = path.parent
    resolved_parent = parent.resolve(strict=True)
    if path.resolve(strict=True).parent != resolved_parent:
        raise OSError("UNSAFE_ATOMIC_WRITE_TARGET")
    descriptor = os.open(parent, flags)
    try:
        opened = os.fstat(descriptor)
        current = parent.lstat()
        if (
            not stat.S_ISDIR(opened.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise OSError("DIRECTORY_FSYNC_UNSUPPORTED")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


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
        md_path = self.items_dir / f"{item_id}.md"
        if not md_path.exists():
            raise FileNotFoundError(f"Item {item_id} not found at {md_path}")
        item, body = self._read_one(md_path)
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
        updated_item = MemoryItem.model_validate(data)
        _atomic_write_text(md_path, render_item_markdown(updated_item, body))
        return updated_item

    def restore_raw(self, item_id: str, data: bytes) -> None:
        """Restore one active item from transaction rollback bytes."""
        if not is_valid_memory_item_id(item_id):
            raise ValueError("invalid memory item id")
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
        md_path = self.items_dir / f"{source_id}.md"
        if not md_path.exists():
            return False
        item, _ = self._read_one(md_path)
        if target_id in item.refs.mems:
            return False
        self.update_frontmatter(
            source_id,
            refs={
                "files": item.refs.files,
                "urls": item.refs.urls,
                "mems": item.refs.mems + [target_id],
                "commits": item.refs.commits,
                "resources": item.refs.resources,
                "extractions": item.refs.extractions,
            },
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
        md_path = self.items_dir / f"{source_id}.md"
        if not md_path.exists():
            return False
        item, _ = self._read_one(md_path)
        if target_id not in item.refs.mems:
            return False
        new_mems = [m for m in item.refs.mems if m != target_id]
        self.update_frontmatter(
            source_id,
            refs={
                "files": item.refs.files,
                "urls": item.refs.urls,
                "mems": new_mems,
                "commits": item.refs.commits,
                "resources": item.refs.resources,
                "extractions": item.refs.extractions,
            },
        )
        return True

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
