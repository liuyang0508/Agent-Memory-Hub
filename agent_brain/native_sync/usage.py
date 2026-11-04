"""Did the answer use the memory it was given? Implicit usage feedback, no bodies.

Prompt-time recall only learns if it hears back. Asking members to rate every
injection does not scale, so the turn's own output is the evidence: when the
model's answer (its text and its tool calls) contains distinctive material
that only the injected memory could have supplied, the memory was used; when
the answer contains nothing of it at all, it was not.

"Distinctive" is what keeps this honest. A memory and an answer about the
same project share vocabulary by chance (project names, dates, everyday
phrases), so a mark counts only when it appears neither in the prompt, nor
anywhere earlier in the session, nor in the turn's own tool results. What is
left could only have come from the memory. Measured on real Claude Code
sessions, a single coincidental word is still common, so a memory is "used"
only on a long specific identifier (a path, a command flag, a versioned name)
or on three separate passages; "unused" needs no overlap at all. Anything in
between is left unlabeled: a wrong label teaches the gate the wrong thing,
a missing one only costs time.

Only salted digests of the memory's marks are kept between the prompt and the
end of the turn, never the memory text, and only labels (asset id, revision,
used/unused) leave this machine.
"""

from __future__ import annotations

from datetime import datetime, timezone

import hashlib
import json
import re
from pathlib import Path
from typing import Any

_ASCII = re.compile(r"[a-z0-9][a-z0-9_.:/\-]*[a-z0-9]")
_CJK = re.compile(r"[一-鿿]{4,}")
# Scaffolding the packer and the hook add around every memory.
_SCAFFOLD = re.compile(r"\[片段截断\]|\[/?AMH_ORG_CONTEXT_V1\]|amh-native-recall:[0-9a-f]{24}")
MAX_MARKS = 4000
MIN_OUTPUT_CHARS = 200
STRONG_CHARS = 12
USED_PIECES = 3
TAIL_BYTES = 1_000_000
MAX_TAIL_BYTES = 16_000_000
SEEN_CHARS = 1_500_000
ORG_READ_TOOLS = ("read_org_memory",)


def marks(text: str) -> set[str]:
    """Distinctive spans: identifiers and long words in ASCII, 4-character CJK windows."""
    folded = _SCAFFOLD.sub(" ", text).casefold()
    found = set()
    for token in _ASCII.findall(folded):
        if len(token) >= 4 and (
            len(token) >= 8
            or any(ch.isdigit() for ch in token)
            or any(ch in token for ch in "_./:-")
        ):
            found.add(token)
    for run in _CJK.findall(folded):
        found.update(run[i : i + 4] for i in range(len(run) - 3))
    return found


def strong(mark: str) -> bool:
    """A long, specific identifier: unlikely to be written by chance."""
    if len(mark) < STRONG_CHARS or not mark.isascii() or not any(ch.isalpha() for ch in mark):
        return False
    return any(ch.isdigit() or ch in "/_." for ch in mark) or mark.count("-") >= 2


def _digest(mark: str, salt: str) -> str:
    return hashlib.blake2b(mark.encode(), digest_size=8, key=salt.encode()[:64]).hexdigest()


def fingerprint(text: str, salt: str) -> dict[str, list[str]]:
    """What is kept until the turn ends: digests only, never the text."""
    found = sorted(marks(text))[:MAX_MARKS]
    return {
        "marks": [_digest(m, salt) for m in found],
        "strong": [_digest(m, salt) for m in found if strong(m)],
    }


def _pieces(hits: list[str], output: str) -> int:
    """Separate passages of the output: overlapping spans of one phrase count once."""
    folded = _SCAFFOLD.sub(" ", output).casefold()
    spans = sorted((start, start + len(hit)) for hit in hits if (start := folded.find(hit)) >= 0)
    pieces, end = 0, -1
    for start, stop in spans:
        if start >= end:
            pieces += 1
        end = max(end, stop)
    return pieces


def judge(
    print_: dict[str, list[str]],
    *,
    output: str,
    seen: str,
    salt: str,
    allow_used: bool = True,
) -> tuple[str | None, dict[str, Any]]:
    """("used" | "unused" | None, evidence counts) for one injected memory."""
    if len(output.strip()) < MIN_OUTPUT_CHARS:
        return None, {"reason": "short_output"}
    memory = set(print_.get("marks") or [])
    specific = set(print_.get("strong") or [])
    # Hits are few; testing each against the folded context is far cheaper
    # than digesting every mark of a long session.
    known = _SCAFFOLD.sub(" ", seen).casefold()
    hits = sorted(m for m in marks(output) if _digest(m, salt) in memory and m not in known)
    evidence = {
        "hits": len(hits),
        "pieces": _pieces(hits, output),
        "strong": sum(1 for m in hits if _digest(m, salt) in specific),
    }
    if not hits:
        # It shares specifics with the memory, but all of them were already in
        # the conversation: that neither proves nor disproves use.
        if any(_digest(m, salt) in memory for m in marks(output)):
            return None, {**evidence, "reason": "already_seen"}
        return "unused", evidence
    if allow_used and (evidence["strong"] or evidence["pieces"] >= USED_PIECES):
        return "used", evidence
    return None, evidence


def _flat(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_flat(v) for v in value)
    if isinstance(value, dict):
        kind = value.get("type")
        if kind == "text":
            return str(value.get("text") or "")
        if kind == "tool_use":
            return json.dumps(value.get("input"), ensure_ascii=False)
        if kind == "tool_result":
            return _flat(value.get("content"))
        if "content" in value:
            return _flat(value["content"])
    return ""


def _is_prompt(row: dict[str, Any]) -> bool:
    """A member's prompt; tool results, compaction summaries and meta rows are context."""
    if row.get("type") != "user" or row.get("isMeta") or row.get("isCompactSummary"):
        return False
    content = (row.get("message") or {}).get("content")
    if isinstance(content, str):
        return True
    return isinstance(content, list) and not any(
        isinstance(part, dict) and part.get("type") == "tool_result" for part in content
    )


def _failed_read(text: str) -> bool:
    """A read tool that answered {"ok": false, ...} fetched nothing."""
    try:
        parsed = json.loads(text)
    except ValueError:
        return False
    return isinstance(parsed, dict) and parsed.get("ok") is False


def recent_prompts(path: Path, limit: int = 2, max_age: float | None = None) -> list[str]:
    """The member's last prompts in a Claude Code transcript, oldest first."""
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - TAIL_BYTES))
            raw = stream.read().decode("utf8", errors="ignore")
    except OSError:
        return []
    found: list[str] = []
    now = datetime.now(timezone.utc)
    for line in reversed(raw.splitlines()):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or not _is_prompt(row):
            continue
        stamp = row.get("timestamp")
        if max_age is not None and isinstance(stamp, str):
            try:
                when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                if (now - when).total_seconds() > max_age:
                    break
            except ValueError:
                pass
        text = _flat((row.get("message") or {}).get("content")).strip()
        if text:
            found.append(text)
            if len(found) >= limit:
                break
    return found[::-1]


def claude_turn(path: Path) -> dict[str, Any] | None:
    """The last turn of a Claude Code transcript: what the model saw before, did, and read.

    ``seen`` is everything before the prompt (bounded to the transcript tail),
    ``output`` the model's text and tool inputs, ``results`` its tool results,
    and ``reads`` the organization memories it fetched itself, by asset id.
    """
    # A long agentic turn can write megabytes of tool output after its prompt:
    # widen the window from the end until the prompt is inside it.
    window = TAIL_BYTES
    while True:
        try:
            with path.open("rb") as stream:
                stream.seek(0, 2)
                size = stream.tell()
                stream.seek(max(0, size - window))
                raw = stream.read().decode("utf8", errors="ignore")
        except OSError:
            return None
        rows = []
        for line in raw.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        start = next((i for i in range(len(rows) - 1, -1, -1) if _is_prompt(rows[i])), None)
        if start is not None or window >= size or window >= MAX_TAIL_BYTES:
            break
        window *= 4
    if start is None:
        return None
    seen = [_flat((rows[start].get("message") or {}).get("content"))]
    for row in rows[:start]:
        if row.get("type") in ("user", "assistant"):
            seen.append(_flat((row.get("message") or {}).get("content")))
        elif row.get("type") == "attachment" and isinstance(row.get("attachment"), dict):
            seen.append(_flat(row["attachment"].get("content")))
    output, results, reads, calls = [], [], [], {}
    for row in rows[start + 1 :]:
        content = (row.get("message") or {}).get("content")
        if row.get("type") == "assistant" and isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                output.append(_flat(part))
                if part.get("type") == "tool_use" and str(part.get("name", "")).endswith(
                    ORG_READ_TOOLS
                ):
                    arguments = part.get("input") if isinstance(part.get("input"), dict) else {}
                    calls[str(part.get("id"))] = arguments
        elif row.get("type") == "user" and isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    text = _flat(part.get("content"))
                    arguments = calls.get(str(part.get("tool_use_id")))
                    if arguments is not None and not part.get("is_error") and not _failed_read(text):
                        reads.append({"arguments": arguments, "text": text})
                    else:
                        results.append(text)
    return {
        # The earlier session, bounded: what is older than this is unlikely
        # to be what the answer echoes.
        "seen": "\n".join(seen)[-SEEN_CHARS:],
        "output": "\n".join(output),
        "results": "\n".join(results),
        "reads": reads,
    }


def read_target(arguments: dict[str, Any], text: str) -> tuple[str, int] | None:
    """(asset id, revision) of an organization read, from its arguments or result."""
    asset = arguments.get("asset_id")
    revision = arguments.get("revision")
    if not isinstance(asset, str) or not asset:
        return None
    if not isinstance(revision, int):
        try:
            revision = int(json.loads(text).get("revision"))
        except (ValueError, TypeError, AttributeError):
            revision = 0
    return asset[:256], int(revision)
