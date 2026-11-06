"""A short record of finished work, so another machine or member can pick it up.

A session's own file stays on the machine that ran it, and a product only turns it
into memory later, if at all. So work done this morning cannot be found this
afternoon. Here the worker writes a few lines about a session that has gone idle: its
title, what was asked, where it ended, which files changed. No model is called. The
text comes from the session as written, and anything that looks like a credential is
removed before it reaches a file. The files then sync like any memory, so the usual
screening, revisions and withdrawal apply.

Off until a person enables it, and sessions older than that moment are never read,
because an update must not start sharing what someone said in private. The Hub ranks
these as session summaries: searchable, never injected, not listed in the catalog.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Iterable, Iterator

from .common import atomic_json, atomic_text
from .source import indexable_history, redact, screen

CAPABILITY = "session-digest-v1"
DIRECTORY = "session-digests"
LEDGER = "ledger.json"
SUBDIRECTORY = "session_digests"
IDLE_SECONDS = 1800
RUN_SECONDS = 300
MAX_PARSED_PER_RUN = 20
MAX_KEPT = 300
HEAD_BYTES = 2 * 1024 * 1024
TAIL_BYTES = 6 * 1024 * 1024
LINE_BYTES = 1024 * 1024
TITLE_CHARS = 80
ASK_CHARS = 300
FINAL_CHARS = 2000
EARLIER_CHARS = 600
EARLIER_ANSWERS = 2
MIN_FINAL_CHARS = 20
MAX_FILES = 12

_SECRET_LINE = re.compile(
    r"(?i)密码|口令|密钥|私钥|凭据|令牌|验证码|password|passwd|secret|credential"
    r"|\btoken\b|api[ _-]?key|access[ _-]?key|private[ _-]?key|cookie|authorization|bearer"
)
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_TOKEN = re.compile(r"[!-~]{8,}")
_TAGGED = re.compile(r"\A\s*</?[a-zA-Z][\w-]*[ >]")
_NOT_A_REQUEST = (
    "Caveat:",
    "[Request interrupted",
    "This session is being continued",
    "# AGENTS.md instructions",
    "# Context from my IDE",
)
_PATCHED = re.compile(r"^\*\*\* (?:Add|Update) File: (.+)$", re.M)
_SESSION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_CLAUDE_EDITS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}


def enabled(config: dict[str, Any]) -> bool:
    return bool((config.get("session_digests") or {}).get("enabled"))


def _opaque(token: str) -> bool:
    """A long run mixing letters, digits and symbols is more likely a value than a word."""
    bare = token.strip("`\"'.,;:()[]{}<>*_~|#")
    if "/" in bare or "\\" in bare or bare.count(".") >= 2 or len(bare) < 8:
        return False
    # Separators make it a name: Qwen3.7-Max, CLAUDE.md, feature_v2.
    if re.fullmatch(r"[A-Za-z0-9]+(?:[._:-][A-Za-z0-9]+)+", bare) and len(bare) < 40:
        return False
    symbol = bool(re.search(r"[^A-Za-z0-9_.:-]", bare))
    classes = sum(bool(re.search(p, bare)) for p in (r"[a-z]", r"[A-Z]", r"\d")) + symbol
    return classes >= 3 or (classes == 2 and symbol)


def clean(text: str, home: str = "") -> str:
    """What may leave the machine. Masking alone misses a bare value next to its label."""
    text = redact(indexable_history(text))
    lines = []
    for line in text.splitlines():
        if _SECRET_LINE.search(line) or "已隐去" in line:
            continue
        line = _TOKEN.sub(lambda m: "…" if _opaque(m.group()) else m.group(), line)
        line = _IPV4.sub("…", line)
        if home:
            line = line.replace(home, "~")
        lines.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _read(path: Path) -> Iterator[dict[str, Any]]:
    """Both ends of the file. A session can reach hundreds of megabytes; this never does."""
    size = path.stat().st_size

    def lines(handle: Any, stop: int) -> Iterator[bytes]:
        while handle.tell() < stop:
            line = handle.readline(LINE_BYTES)
            if not line:
                return
            if not line.endswith(b"\n") and len(line) >= LINE_BYTES:
                while True:  # An oversized record carries no words worth keeping.
                    rest = handle.readline(LINE_BYTES)
                    if not rest or rest.endswith(b"\n"):
                        break
                continue
            yield line

    def parse(raw: bytes) -> dict[str, Any] | None:
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    with path.open("rb") as handle:
        head_end = min(size, HEAD_BYTES)
        for raw in lines(handle, head_end):
            value = parse(raw)
            if value is not None:
                yield value
        reached = handle.tell()
        if reached < size:
            start = max(reached, size - TAIL_BYTES)
            if start > reached:
                handle.seek(start)
                handle.readline(LINE_BYTES)  # Very likely mid-record.
            for raw in lines(handle, size):
                value = parse(raw)
                if value is not None:
                    yield value


def _when(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _is_request(text: str) -> bool:
    return bool(text.strip()) and not _TAGGED.match(text) and not text.startswith(_NOT_A_REQUEST)


def _blank(session_id: str) -> dict[str, Any]:
    return {
        "id": session_id,
        "title": "",
        "started": None,
        "ended": None,
        "cwd": "",
        "branch": "",
        "turns": 0,
        "first": "",
        "last": "",
        "answers": [],
        "files": [],
        "interactive": True,
    }


def _stamp(found: dict[str, Any], value: Any) -> None:
    moment = _when(value)
    if moment is not None:
        found["started"] = moment if found["started"] is None else min(found["started"], moment)
        found["ended"] = moment if found["ended"] is None else max(found["ended"], moment)


def _ask(found: dict[str, Any], text: str) -> None:
    # Codex records a request twice, as a message and as an event.
    if _is_request(text) and text != found["last"]:
        found["turns"] += 1
        found["first"] = found["first"] or text
        found["last"] = text


def _answer(found: dict[str, Any], text: str) -> None:
    if len(text.strip()) >= MIN_FINAL_CHARS and text not in found["answers"][-1:]:
        # A long session's last reply is often a side question, so keep a little before it.
        found["answers"] = (found["answers"] + [text])[-(EARLIER_ANSWERS + 1) :]


def _changed(found: dict[str, Any], path: Any) -> None:
    if isinstance(path, str) and path and path not in found["files"]:
        found["files"].append(path)


def claude_session(records: Iterable[dict[str, Any]], session_id: str) -> dict[str, Any]:
    found = _blank(session_id)
    for record in records:
        kind = record.get("type")
        if kind == "ai-title" and isinstance(record.get("aiTitle"), str):
            found["title"] = record["aiTitle"]
            continue
        if kind not in ("user", "assistant") or record.get("isSidechain"):
            continue
        if record.get("entrypoint") == "sdk-cli":
            found["interactive"] = False
        _stamp(found, record.get("timestamp"))
        found["cwd"] = record.get("cwd") or found["cwd"]
        found["branch"] = record.get("gitBranch") or found["branch"]
        content = (record.get("message") or {}).get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                if kind == "assistant":
                    if not record.get("isApiErrorMessage"):
                        _answer(found, block["text"])
                elif not (record.get("isMeta") or record.get("isCompactSummary")):
                    _ask(found, block["text"])
            elif (
                kind == "assistant"
                and block.get("type") == "tool_use"
                and block.get("name") in _CLAUDE_EDITS
            ):
                _changed(found, (block.get("input") or {}).get("file_path"))
    return found


def codex_session(records: Iterable[dict[str, Any]], session_id: str) -> dict[str, Any]:
    found = _blank(session_id)
    for record in records:
        kind = record.get("type")
        payload = record.get("payload")
        _stamp(found, record.get("timestamp"))
        if not isinstance(payload, dict):
            continue
        if kind == "session_meta":
            _stamp(found, payload.get("timestamp"))
            found["cwd"] = payload.get("cwd") or found["cwd"]
            found["branch"] = (payload.get("git") or {}).get("branch") or found["branch"]
            source = payload.get("source")
            # A subagent's source is an object; `exec` is a script, not a person.
            if isinstance(source, dict) or source == "exec":
                found["interactive"] = False
        elif kind == "event_msg" and payload.get("type") == "user_message":
            if isinstance(payload.get("message"), str):
                _ask(found, payload["message"])
        elif kind == "turn_context":
            found["cwd"] = payload.get("cwd") or found["cwd"]
        elif kind == "event_msg" and payload.get("type") == "agent_message":
            if isinstance(payload.get("message"), str) and payload.get("phase") != "commentary":
                _answer(found, payload["message"])
        elif kind == "event_msg" and payload.get("type") == "task_complete":
            if isinstance(payload.get("last_agent_message"), str):
                _answer(found, payload["last_agent_message"])
        elif kind == "response_item" and payload.get("type") == "message":
            role = payload.get("role")
            for block in payload.get("content") or []:
                if not isinstance(block, dict) or not isinstance(block.get("text"), str):
                    continue
                if role == "user":
                    _ask(found, block["text"])
                elif role == "assistant" and payload.get("phase") != "commentary":
                    _answer(found, block["text"])
        elif kind == "response_item" and payload.get("type") in (
            "custom_tool_call",
            "function_call",
        ):
            patch = str(payload.get("input") or payload.get("arguments") or "")
            if "apply_patch" in str(payload.get("name")) or "*** Begin Patch" in patch:
                for match in _PATCHED.findall(patch.replace("\\n", "\n")):
                    _changed(found, match.strip())
    return found


def _shown_file(path: str, cwd: str, home: str) -> str | None:
    if path.startswith(("/tmp/", "/private/", "/var/")):
        return None
    if cwd and path.startswith(cwd.rstrip("/") + "/"):
        path = path[len(cwd.rstrip("/")) + 1 :]
    return clean(path, home) or None


def render(session: dict[str, Any], *, home: str = "") -> str | None:
    """The document, or None when there is too little to be worth anyone's search."""
    answers = session["answers"]
    final = _short(clean(answers[-1], home), FINAL_CHARS) if answers else ""
    first = _short(clean(session["first"], home), ASK_CHARS)
    if len(final) < MIN_FINAL_CHARS or not first:
        return None
    files = [
        shown
        for shown in (_shown_file(path, session["cwd"], home) for path in session["files"])
        if shown
    ][-MAX_FILES:]
    if session["turns"] < 2 and not files:
        return None
    title = " ".join(clean(session["title"] or first, home).split())[:TITLE_CHARS] or "未命名会话"
    project = Path(session["cwd"]).name if session["cwd"] else ""
    moment = session["ended"] or session["started"]
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(moment)) if moment else ""
    last = _short(clean(session["last"], home), ASK_CHARS)
    facts = [part for part in (when, project, session["branch"]) if part]
    parts = [
        "---",
        "name: session-" + session["id"][:8],
        "description: " + json.dumps(title, ensure_ascii=False),
        "metadata:",
        "  type: session-digest",
        "---",
        "",
        "# " + title,
        "",
        "自动摘录，未经人工确认，可能已过时。会话 " + session["id"],
        "· ".join(facts),
        "",
        "## 要做的事",
        first,
        "",
    ]
    if last and last != first:
        parts += ["## 最后的要求", last, ""]
    parts += ["## 结束时的进展", final, ""]
    earlier = [_short(clean(text, home), EARLIER_CHARS) for text in answers[:-1]]
    if any(earlier):
        parts += ["## 更早的进展", *(text + "\n" for text in reversed(earlier) if text)]
    if files:
        parts += ["## 改动的文件", *("- " + file for file in files), ""]
    return "\n".join(parts)


def _candidates(home: Path) -> Iterator[tuple[str, Path, str]]:
    for path in (home / ".claude/projects").glob("*/*.jsonl"):
        yield "claude-code", path, path.stem
    for path in (home / ".codex/sessions").rglob("rollout-*.jsonl"):
        match = _SESSION_ID.search(path.name)
        if match:
            yield "codex-cli", path, match.group()


def _root(state: Path, product: str) -> Path:
    return state / DIRECTORY / product


def ledger_path(state: Path) -> Path:
    return state / DIRECTORY / LEDGER


def load_ledger(state: Path) -> dict[str, Any]:
    try:
        value = json.loads(ledger_path(state).read_text())
    except (OSError, ValueError):
        value = {}
    return {
        "sessions": value.get("sessions") or {},
        "added": value.get("added") or [],
        "forgotten": value.get("forgotten") or [],
    }


def run(
    state: Path,
    config: dict[str, Any],
    register: Any,
    *,
    home: Path | None = None,
    now: float | None = None,
) -> dict[str, int]:
    """Write what has gone idle since the last pass. `register` binds a product's folder."""
    home = home or Path.home()
    now = time.time() if now is None else now
    since = float((config.get("session_digests") or {}).get("since") or now)
    ledger = load_ledger(state)
    seen = ledger["sessions"]
    counts = {"written": 0, "removed": 0, "skipped": 0}
    parsed = 0
    bound: set[str] = set()
    candidates = []
    for product, path, session_id in _candidates(home):
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime < since and session_id not in ledger["added"]:
            continue  # Older than consent, and cheap to rule out without opening it.
        candidates.append((stat.st_mtime, product, path, session_id, stat))
    for _, product, path, session_id, stat in sorted(candidates, key=lambda row: -row[0]):
        if session_id in ledger["forgotten"] or now - stat.st_mtime < IDLE_SECONDS:
            continue
        mark = [stat.st_size, stat.st_mtime_ns]
        if (seen.get(session_id) or {}).get("mark") == mark:
            continue
        if parsed >= MAX_PARSED_PER_RUN:
            break
        parsed += 1
        read = claude_session if product == "claude-code" else codex_session
        session = read(_read(path), session_id)
        seen[session_id] = {"mark": mark}
        allowed = session_id in ledger["added"] or (session["started"] or 0) >= since
        body = render(session, home=str(home)) if allowed and session["interactive"] else None
        root = _root(state, product)
        target = root / SUBDIRECTORY / (session_id + ".md")
        if body is not None and screen(body)["risk"] != "clean":
            body = None  # Nobody reads these before they go, so "sensitive" is enough.
        if body is None:
            counts["skipped"] += 1
            if target.exists():
                target.unlink()
                counts["removed"] += 1
            continue
        if product not in bound:
            (root / SUBDIRECTORY).mkdir(parents=True, exist_ok=True, mode=0o700)
            register(root, product)
            bound.add(product)
        if not target.exists() or target.read_text() != body:
            atomic_text(target, body)
            counts["written"] += 1
    counts["removed"] += _prune(state)
    atomic_json(ledger_path(state), ledger)
    return counts


def _prune(state: Path) -> int:
    removed = 0
    for product_root in (state / DIRECTORY).glob("*/" + SUBDIRECTORY):
        files = sorted(product_root.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in files[MAX_KEPT:]:
            path.unlink()
            removed += 1
    return removed


def listing(state: Path) -> list[dict[str, Any]]:
    rows = []
    for path in (state / DIRECTORY).glob("*/" + SUBDIRECTORY + "/*.md"):
        match = re.search(r"^# (.+)$", path.read_text(), re.M)
        rows.append(
            {
                "session": path.stem,
                "product": path.parent.parent.name,
                "title": match.group(1) if match else "",
                "written": path.stat().st_mtime,
            }
        )
    return sorted(rows, key=lambda row: -row["written"])


def _update(state: Path, key: str, session: str) -> None:
    ledger = load_ledger(state)
    if session not in ledger[key]:
        ledger[key].append(session)
    ledger["sessions"].pop(session, None)
    ledger_path(state).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_json(ledger_path(state), ledger)


def add(state: Path, session: str) -> None:
    """One older session, named by a person, may be included after all."""
    _update(state, "added", session)
    ledger = load_ledger(state)
    if session in ledger["forgotten"]:
        ledger["forgotten"].remove(session)
        atomic_json(ledger_path(state), ledger)


def forget(state: Path, session: str) -> int:
    """Withdraw it: the deleted file tombstones on the next sync, and it is not written again."""
    targets = [row["session"] for row in listing(state)] if session == "all" else [session]
    removed = 0
    for target in targets:
        _update(state, "forgotten", target)
        ledger = load_ledger(state)
        if target in ledger["added"]:
            ledger["added"].remove(target)
            atomic_json(ledger_path(state), ledger)
        for path in (state / DIRECTORY).glob("*/" + SUBDIRECTORY + "/" + target + ".md"):
            path.unlink()
            removed += 1
    return removed


__all__ = ["CAPABILITY", "RUN_SECONDS", "add", "enabled", "forget", "listing", "run"]
