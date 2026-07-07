"""Conservative repair actions for malformed Markdown memory items."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from agent_brain.memory.store.items_store import ItemsStore


@dataclass(frozen=True)
class QuarantineAction:
    source: Path
    destination: Path
    reason: str
    applied: bool = False


@dataclass(frozen=True)
class QuarantineReport:
    found: int
    moved: int
    actions: list[QuarantineAction] = field(default_factory=list)


@dataclass(frozen=True)
class RestoreAction:
    source: Path
    destination: Path | None
    reason: str
    valid: bool
    applied: bool = False


@dataclass(frozen=True)
class RestoreReport:
    found: int
    restored: int
    actions: list[RestoreAction] = field(default_factory=list)


def quarantine_malformed_items(items_dir: Path, *, apply: bool = False) -> QuarantineReport:
    """Plan or move malformed item files out of the active store.

    This intentionally does not rewrite item contents. Files that the normal
    ItemsStore parser cannot read are moved to ``items/archived/malformed`` only
    when ``apply=True``; dry-run is the default.
    """
    store = ItemsStore(items_dir)
    sum(1 for _ in store.iter_all())

    quarantine_dir = items_dir / "archived" / "malformed"
    actions: list[QuarantineAction] = []
    moved = 0

    for rec in store.last_scan.skipped:
        destination = _unique_destination(quarantine_dir / rec.path.name)
        if apply:
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(rec.path), str(destination))
            destination.with_suffix(destination.suffix + ".reason.txt").write_text(
                f"{rec.reason}\nsource: {rec.path}\n",
                encoding="utf-8",
            )
            moved += 1
        actions.append(QuarantineAction(
            source=rec.path,
            destination=destination,
            reason=rec.reason,
            applied=apply,
        ))

    return QuarantineReport(found=len(actions), moved=moved, actions=actions)


def restore_malformed_item(
    items_dir: Path,
    archived_name: str,
    *,
    apply: bool = False,
) -> RestoreReport:
    """Plan or restore one manually repaired quarantined memory item.

    The source must be a file under ``items/archived/malformed``. The file is
    parsed as a normal ``MemoryItem`` before it can be restored. This keeps the
    flow explicit: humans repair content, then this helper validates and moves
    the file back into the active item tree without rewriting it.
    """
    archive_dir = items_dir / "archived" / "malformed"
    source = _safe_archived_source(archive_dir, archived_name)
    if not source.exists() or not source.is_file():
        return RestoreReport(
            found=0,
            restored=0,
            actions=[RestoreAction(
                source=source,
                destination=None,
                reason="archived malformed item not found",
                valid=False,
                applied=False,
            )],
        )

    archive_store = ItemsStore(archive_dir)
    try:
        item, _ = archive_store._read_one(source)
    except Exception as exc:  # noqa: BLE001 - report exact validation failure
        reason = f"{type(exc).__name__}: {exc}".splitlines()[0][:200]
        return RestoreReport(
            found=1,
            restored=0,
            actions=[RestoreAction(
                source=source,
                destination=None,
                reason=reason,
                valid=False,
                applied=False,
            )],
        )

    destination = items_dir / f"{item.id}.md"
    if destination.exists():
        return RestoreReport(
            found=1,
            restored=0,
            actions=[RestoreAction(
                source=source,
                destination=destination,
                reason="active item already exists",
                valid=True,
                applied=False,
            )],
        )

    restored = 0
    if apply:
        items_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        reason_file = source.with_suffix(source.suffix + ".reason.txt")
        if reason_file.exists():
            reason_file.unlink()
        restored = 1

    return RestoreReport(
        found=1,
        restored=restored,
        actions=[RestoreAction(
            source=source,
            destination=destination,
            reason="valid memory item",
            valid=True,
            applied=apply,
        )],
    )


def _unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 2
    while True:
        candidate = parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _safe_archived_source(archive_dir: Path, archived_name: str) -> Path:
    source = archive_dir / archived_name
    resolved_archive = archive_dir.resolve()
    resolved_source = source.resolve(strict=False)
    try:
        resolved_source.relative_to(resolved_archive)
    except ValueError as exc:
        raise ValueError("archived_name must stay under items/archived/malformed") from exc
    return source
