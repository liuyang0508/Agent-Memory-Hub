"""Cursor local history readers.

Cursor does not expose Claude/Codex-style JSONL transcripts locally. The stable
local surfaces we can safely harvest are plan Markdown files under
``~/.cursor/plans`` and workspace-scoped VS Code state databases that contain
``composer.composerData`` summaries.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote, urlparse

from agent_brain.memory.evidence.harvest.transcript_reader import TranscriptSpan


def read_cursor_plan_spans(path: Path) -> Iterator[TranscriptSpan]:
    plan = Path(path)
    text = plan.read_text(encoding="utf-8-sig", errors="ignore").strip()
    if not text:
        return
    yield TranscriptSpan(
        text=f"Cursor plan file: {plan.name}\n\n{text}",
        start_offset=0,
        end_offset=len(text.encode("utf-8")),
        role="cursor_plan",
    )


def count_cursor_composers(path: Path) -> int:
    return len(_cursor_composers(path))


def read_cursor_composer_spans(path: Path) -> Iterator[TranscriptSpan]:
    db = Path(path)
    project = cursor_workspace_project(db.parent)
    for index, composer in enumerate(_cursor_composers(db)):
        text = _composer_summary_text(composer, project=project, db=db)
        if not text.strip():
            continue
        yield TranscriptSpan(
            text=text,
            start_offset=index,
            end_offset=index + 1,
            role="cursor_composer",
        )


def cursor_workspace_project(workspace_dir: Path) -> str | None:
    workspace = Path(workspace_dir) / "workspace.json"
    if not workspace.exists():
        return None
    try:
        data = json.loads(workspace.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return None
    folder = data.get("folder") if isinstance(data, dict) else None
    if not isinstance(folder, str) or not folder:
        return None
    parsed = urlparse(folder)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path)).name or unquote(parsed.path)
    return folder


def _cursor_composers(path: Path) -> list[dict[str, Any]]:
    db = Path(path)
    if not db.exists():
        return []
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as con:
            row = con.execute(
                "select value from ItemTable where key = ?",
                ("composer.composerData",),
            ).fetchone()
    except sqlite3.Error:
        return []
    if not row:
        return []
    value = row[0]
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return []
    composers = data.get("allComposers") if isinstance(data, dict) else None
    if not isinstance(composers, list):
        return []
    return [composer for composer in composers if isinstance(composer, dict)]


def _composer_summary_text(composer: dict[str, Any], *, project: str | None, db: Path) -> str:
    def _s(value: Any) -> str:
        return value if isinstance(value, str) else ""

    active_branch = composer.get("activeBranch")
    branch = ""
    if isinstance(active_branch, dict):
        branch = _s(active_branch.get("branchName"))
    branch = branch or _s(composer.get("createdOnBranch")) or _s(composer.get("committedToBranch"))
    referenced_plans = composer.get("referencedPlans")
    plan_count = len(referenced_plans) if isinstance(referenced_plans, list) else 0
    lines = [
        f"Cursor composer: {_s(composer.get('name')) or composer.get('composerId') or db.parent.name}",
        f"Project: {project or db.parent.name}",
        f"Composer ID: {_s(composer.get('composerId'))}",
        f"Subtitle: {_s(composer.get('subtitle'))}",
        f"Branch: {branch}",
        f"Created at: {composer.get('createdAt') or ''}",
        f"Last updated at: {composer.get('lastUpdatedAt') or ''}",
        (
            "Code changes: "
            f"+{composer.get('totalLinesAdded') or 0} "
            f"-{composer.get('totalLinesRemoved') or 0}; "
            f"files changed={composer.get('filesChangedCount') or 0}"
        ),
        f"Referenced plans: {plan_count}",
    ]
    return "\n".join(line for line in lines if line.strip())
