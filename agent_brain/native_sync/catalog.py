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
# The organization wiki a dreaming pass keeps (see dream.py). Its index is the curated
# top layer, so it goes first, and a line carries the page's short name as a search term.
WIKI = "org-wiki"
WIKI_LINE_CHARS = 96
WIKI_INDEX = "_index.md"
WIKI_INDEX_CHARS = 6000  # about 60 index lines: the whole top layer, still small
_SOURCES = re.compile(r"^\s*sources:\s*\[([^\]]*)\]", re.M)
_WIKI_INDEX_LINE = re.compile(
    r"^\s*[-*]?\s*\[\[([a-z0-9][a-z0-9-]{0,79})\]\]\s*[—–:：-]+\s*(.+?)\s*$", re.M
)


def _line(text: str) -> str:
    return _IPV4.sub("…", " ".join(text.split())).strip("\"'")[:LINE_CHARS]


def _wiki_line(slug: str, text: str) -> str:
    return (slug + "：" + _IPV4.sub("…", " ".join(text.split())).strip("\"'"))[:WIKI_LINE_CHARS]


def is_wiki(path: str) -> bool:
    return PurePosixPath(path.replace("\\", "/")).parts[:1] == (WIKI,)


def describe(path: str, text: str) -> list[str]:
    """The lines saying what one document covers; none for an index or a working file."""
    posix = PurePosixPath(path.replace("\\", "/"))
    name = posix.name.lower()
    if posix.parts[:1] == (WIKI,):
        if name == "_index.md":
            return [_wiki_line(slug, line) for slug, line in _WIKI_INDEX_LINE.findall(text)]
        # A page speaks for itself only until its index reaches the Hub.
        front = _FRONT.match(text)
        described = _DESCRIPTION.search(front.group(1)) if front else None
        return [_wiki_line(posix.stem, described.group(1))] if described else []
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


def build(
    documents: list[dict[str, Any]],
    *,
    limit: int = CATALOG_CHARS,
    index_limit: int = WIKI_INDEX_CHARS,
) -> list[str]:
    """Bounded lines: the whole wiki index first, then the most recent documents; a topic once.

    The index is the organization's top layer and gets its own budget, so a page it
    lists is never cut off by older topics; a wiki page the index already names is not
    repeated. Everything else shares ``limit``, most recent first.
    """
    lines: list[str] = []
    seen: set[str] = set()
    listed: set[str] = set()
    used = 0
    by_age = sorted(documents, key=lambda d: -float(d.get("observed_at") or 0))
    # A wiki page names the assets it was written from; a document the wiki already
    # covers is not a second topic. Documents newer than the last dreaming pass remain.
    covered: set[str] = set()
    for document in by_age:
        if is_wiki(str(document.get("path") or "")):
            for found in _SOURCES.finditer(str(document.get("text") or "")[:4000]):
                covered.update(
                    part.strip().strip("\"'").split("@", 1)[0]
                    for part in found.group(1).split(",")
                    if part.strip()
                )
    for document in by_age:
        path = str(document.get("path") or "")
        if not is_wiki(path) or PurePosixPath(path.replace("\\", "/")).name != WIKI_INDEX:
            continue
        for line in describe(path, str(document.get("text") or "")):
            key = re.sub(r"\W+", "", line.casefold())[:40]
            cost = len(line) + 3
            if not key or key in seen or used + cost > index_limit:
                continue
            seen.add(key)
            listed.add(line.split("：", 1)[0])
            lines.append(line)
            used += cost
    used = 0
    for document in sorted(by_age, key=lambda d: 0 if is_wiki(str(d.get("path") or "")) else 1):
        path = str(document.get("path") or "")
        posix = PurePosixPath(path.replace("\\", "/"))
        if is_wiki(path) and (posix.name == WIKI_INDEX or posix.stem in listed):
            continue
        if covered and covered & {str(a) for a in document.get("assets") or ()}:
            continue
        for line in describe(path, str(document.get("text") or ""))[:LINES_PER_DOCUMENT]:
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
        "先用 search_org_memory 查；条目开头的英文短名可直接作为搜索词。"
        "查到的内容里标着「推断」或「冲突」的，先核实再用：\n" + body + "\n[/AMH_ORG_CATALOG_V1]\n"
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
    return [line[:WIKI_LINE_CHARS] for line in lines if isinstance(line, str) and line]


def refresh(state: Path, transport: Any) -> None:
    """Fetch and cache. A Hub without the endpoint or a failed call keeps what is cached."""
    response = transport.call("catalog")
    lines = response.get("lines") if isinstance(response, dict) else None
    if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
        raise ValueError("invalid catalog")
    atomic_json(state / FILE_NAME, {"lines": lines, "time": time.time()})
