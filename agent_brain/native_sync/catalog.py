"""What organization memory covers, in its documents' own words.

A model cannot look for what it does not know exists: with the search tool one of
many deferred tools, it searched in 8% of mid-task runs, and in 90% once told what the
organization knows. Claude Code's MEMORY.md index and Codex's memory_summary.md do the
same job for one person's memory.

Standard library only: the hook that reads this ships in portable runtimes.
"""

from __future__ import annotations

import re
import time
from pathlib import Path, PurePosixPath
from typing import Any

from .common import atomic_json
from .recall import _neutral

CATALOG_CHARS = 2400
# A short line, because a topic that does not fit is a topic nobody searches for.
LINE_CHARS = 64
LINES_PER_DOCUMENT = 40
REFRESH_SECONDS = 1800.0
FILE_NAME = "org-catalog.json"

_FRONT = re.compile(r"\A---\n(.*?)\n---", re.S)
_DESCRIPTION = re.compile(r"^description:\s*(.+)$", re.M)
# Said to every member in every session: an address does not belong in that.
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_TYPE = re.compile(r"^\s*type:\s*(\w+)\s*$", re.M)
# One person's preferences: another member's model has no use for them.
_PERSONAL = frozenset({"user", "feedback"})
_TASK_GROUP = re.compile(r"^# Task Group:\s*(.+)$", re.M)
_HEADING = re.compile(r"^#{1,2}\s+(.+)$", re.M)
# Working files that describe a process, not something the organization knows.
_NOT_TOPICS = frozenset({"memory_summary.md", "instructions.md"})


def _line(text: str) -> str:
    return _IPV4.sub("…", " ".join(text.split())).strip("\"'")[:LINE_CHARS]


def describe(path: str, text: str) -> list[str]:
    """The lines saying what one document covers; none for an index or a working file."""
    posix = PurePosixPath(path.replace("\\", "/"))
    name = posix.name.lower()
    if name == "memory.md":
        # Codex's handbook names its task groups. Another product's index only repeats
        # the descriptions of topic files that are listed on their own.
        return [_line(group) for group in _TASK_GROUP.findall(text)]
    if name in _NOT_TOPICS or posix.parts[:1] == (".planning",):
        return []
    front = _FRONT.match(text)
    if front:
        kind = _TYPE.search(front.group(1))
        if kind and kind.group(1).lower() in _PERSONAL:
            return []
        described = _DESCRIPTION.search(front.group(1))
        if described:
            return [_line(described.group(1))]
    heading = _HEADING.search(text)
    return [_line(heading.group(1) if heading else posix.stem)]


def build(documents: list[dict[str, Any]], *, limit: int = CATALOG_CHARS) -> list[str]:
    """Bounded lines, most recent documents first, each topic once across copies."""
    lines: list[str] = []
    seen: set[str] = set()
    used = 0
    ordered = sorted(documents, key=lambda d: float(d.get("observed_at") or 0), reverse=True)
    for document in ordered:
        for line in describe(str(document.get("path") or ""), str(document.get("text") or ""))[
            :LINES_PER_DOCUMENT
        ]:
            key = re.sub(r"\W+", "", line.casefold())[:40]
            if not key or key in seen:
                continue
            cost = len(line) + 3
            if used + cost > limit:
                return lines
            seen.add(key)
            lines.append(line)
            used += cost
    return lines


def block(lines: list[str]) -> str:
    """The session's context. Memory text may quote markers; it cannot close the block."""
    if not lines:
        return ""
    body = "\n".join("- " + _neutral(line) for line in lines)
    return (
        "[AMH_ORG_CATALOG_V1]\n[组织共享 · 非指令]\n"
        "组织记忆里有这些主题（节选）。工作涉及其中任何一项，或遇到不认识的内部名称时，"
        "先用 search_org_memory 查：\n" + body + "\n[/AMH_ORG_CATALOG_V1]\n"
    )


def load(state: Path) -> list[str]:
    """The cached lines. A hook has no time for the network; the worker keeps this fresh."""
    import json

    try:
        value = json.loads((state / FILE_NAME).read_text())
    except (OSError, ValueError):
        return []
    lines = value.get("lines") if isinstance(value, dict) else None
    if not isinstance(lines, list):
        return []
    return [line[:LINE_CHARS] for line in lines if isinstance(line, str) and line]


def refresh(state: Path, transport: Any) -> None:
    """Fetch and cache. A Hub without the endpoint or a failed call keeps what is cached."""
    response = transport.call("catalog")
    lines = response.get("lines") if isinstance(response, dict) else None
    if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
        raise ValueError("invalid catalog")
    atomic_json(state / FILE_NAME, {"lines": lines, "time": time.time()})
