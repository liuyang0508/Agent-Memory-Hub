"""Runtime field enrichment for newly written MemoryItem records."""
from __future__ import annotations

import os
import platform
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem


@dataclass(frozen=True)
class GitScope:
    repo: str | None = None
    branch: str | None = None


def enrich_memory_item(
    item: MemoryItem,
    *,
    cwd: str | None = None,
    adapter: str | None = None,
) -> MemoryItem:
    """Fill provable runtime defaults before a MemoryItem is persisted.

    The function does not invent transcript provenance. It only fills fields
    that are either already present on the item, passed by the caller, available
    in AMH runtime environment variables, or mechanically derivable from a git
    worktree.
    """
    created_at = _aware(item.created_at)
    runtime_cwd = cwd or item.validity.cwd or os.environ.get("AGENT_MEMORY_HUB_CWD")
    runtime_adapter = (
        adapter
        or item.validity.adapter
        or os.environ.get("AGENT_MEMORY_HUB_ADAPTER")
        or item.agent
    )
    git_scope = _git_scope(runtime_cwd)

    retention = item.retention.model_copy(update={
        "last_accessed": item.retention.last_accessed or created_at,
    })
    validity = item.validity.model_copy(update={
        "observed_at": item.validity.observed_at or created_at,
        "cwd": item.validity.cwd or runtime_cwd,
        "repo": item.validity.repo or git_scope.repo,
        "branch": item.validity.branch or git_scope.branch,
        "os": item.validity.os or platform.system().lower(),
        "adapter": item.validity.adapter or runtime_adapter,
    })
    return item.model_copy(update={
        "created_at": created_at,
        "retention": retention,
        "validity": validity,
    })


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _git_scope(cwd: str | None) -> GitScope:
    if not cwd:
        return GitScope()
    path = Path(cwd).expanduser()
    if not path.exists():
        return GitScope()
    root = _git_stdout(path, "rev-parse", "--show-toplevel")
    branch = _git_stdout(path, "branch", "--show-current")
    repo = Path(root).name if root else None
    return GitScope(repo=repo, branch=branch or None)


def _git_stdout(cwd: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(cwd), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


__all__ = ["GitScope", "enrich_memory_item"]
