"""Structured cross-agent handoff creation and resume selection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.recall.brief import build_brief
from agent_brain.memory.store.items_store import ItemsStore


@dataclass(frozen=True)
class GitSnapshot:
    repo: str
    branch: str
    head: str
    status: tuple[str, ...]
    recent_commits: tuple[str, ...]


def capture_git_snapshot(repo: Path, *, status_limit: int = 50) -> GitSnapshot:
    """Capture bounded Git state without requiring the directory to be a repo."""

    root = _git(repo, "rev-parse", "--show-toplevel") or str(repo.resolve())
    branch = _git(repo, "branch", "--show-current") or "DETACHED_OR_NOT_GIT"
    head = _git(repo, "rev-parse", "--short", "HEAD") or "NO_COMMIT"
    status = tuple(_git_lines(repo, "status", "--short")[:status_limit])
    commits = tuple(_git_lines(repo, "log", "--oneline", "-5"))
    return GitSnapshot(
        repo=root,
        branch=branch,
        head=head,
        status=status,
        recent_commits=commits,
    )


def render_code_handoff(
    *,
    objective: str,
    snapshot: GitSnapshot,
    completed: list[str],
    pending: list[str],
    decisions: list[str],
    next_actions: list[str],
    verification: list[str],
    blockers: list[str],
    source_agent: str,
    target_agent: str,
) -> str:
    """Render the canonical, bounded code-resume handoff body."""

    return "\n".join(
        [
            f"# Handoff: {objective}",
            "",
            "## 1. Objective",
            "",
            objective,
            "",
            "## 2. Current State",
            "",
            f"- source agent: `{source_agent}`",
            f"- target agent: `{target_agent}`",
            f"- repo: `{snapshot.repo}`",
            f"- branch: `{snapshot.branch}`",
            f"- HEAD: `{snapshot.head}`",
            "- working tree:",
            *_indented_code(snapshot.status or ("clean or unavailable",)),
            "- recent commits:",
            *_indented_code(snapshot.recent_commits or ("none or unavailable",)),
            "",
            "**已完成**",
            *_numbered(completed),
            "",
            "**未完成**",
            *_numbered(pending),
            "",
            "## 3. Decisions",
            "",
            *_numbered(decisions or ["无非显然决策"]),
            "",
            "## 4. Next Actions",
            "",
            *_numbered(next_actions),
            "",
            "## 5. Verification Expectations",
            "",
            *_bulleted(verification),
            "",
            "## 6. Files Touched",
            "",
            *_bulleted(snapshot.status or ["working tree clean or unavailable"]),
            "",
            "## 7. Blockers",
            "",
            *_bulleted(blockers or ["无"]),
            "",
        ]
    )


def latest_resumable_handoff(
    store: ItemsStore,
    *,
    project: str | None = None,
    query: str | None = None,
) -> tuple[MemoryItem, str] | None:
    """Return the latest gateway-approved handoff and its full canonical body."""

    brief = build_brief(store, project=project, budget_tokens=1500, query=query)
    tier = next((value for value in brief.tiers if value.name == "recent_handoffs"), None)
    if tier is None or not tier.shown:
        return None
    return store.get(tier.shown[0].id)


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_lines(repo: Path, *args: str) -> list[str]:
    value = _git(repo, *args)
    return value.splitlines() if value else []


def _numbered(values: list[str]) -> list[str]:
    return [f"{index}. {value}" for index, value in enumerate(values, start=1)]


def _bulleted(values: list[str] | tuple[str, ...]) -> list[str]:
    return [f"- {value}" for value in values]


def _indented_code(values: tuple[str, ...]) -> list[str]:
    return ["  ```", *(f"  {value}" for value in values), "  ```"]


__all__ = [
    "GitSnapshot",
    "capture_git_snapshot",
    "latest_resumable_handoff",
    "render_code_handoff",
]
