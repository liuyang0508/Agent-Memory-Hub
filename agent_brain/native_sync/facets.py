"""Knowledge facets: declared metadata, nature, trusted times and decay. Stdlib only.

Shared by the client (capturing ``source_meta`` at upload) and the Hub (ranking).
Every derived value carries its provenance tier so a reader can tell a declared
fact from a parsed location rule or a guess. An unknown time stays unknown: the
Hub's observation time is never substituted for when content was written.
"""

from __future__ import annotations

import math
import posixpath
import re
from datetime import datetime, timezone
from typing import Any

NATURES = ("rule", "preference", "project_state", "fact", "decision", "reference", "episode")
UNKNOWN = "unknown"
PROVENANCE = ("declared", "parsed", "inferred", "confirmed", UNKNOWN)
# Days until a memory's ranking weight halves. Rules and preferences stay useful
# for a long time; a project's temporary state goes stale within weeks.
HALF_LIFE_DAYS: dict[str, int] = {
    "rule": 365,
    "preference": 180,
    "reference": 180,
    "decision": 90,
    "fact": 60,
    UNKNOWN: 60,
    "episode": 30,
    "project_state": 21,
}
DECLARED_KEYS = (
    "name",
    "description",
    "type",
    "nature",
    "status",
    "created",
    "date",
    "modified",
    "updated",
    "expires",
    "valid_until",
    "superseded_by",
    "confidence",
)
DECLARED_VALUE_CHARS = 200
MAX_LINKS = 64
MAX_SECTIONS = 32
# Claude Code auto-memory ``metadata.type`` values, mapped onto knowledge nature.
TYPE_TO_NATURE = {
    "user": "preference",
    "feedback": "rule",
    "project": "project_state",
    "reference": "reference",
    **{value: value for value in NATURES},
}
STATUS_TO_NATURE = {
    "temporary": "project_state",
    "wip": "project_state",
    "draft": "project_state",
    "临时": "project_state",
    "进行中": "project_state",
    "confirmed": "fact",
    "verified": "fact",
    "已确认": "fact",
    "已核实": "fact",
}
# Weak body cues may only suggest the two natures a reader would otherwise
# mistake for stable knowledge. They never override a declaration.
_STATE_CUES = re.compile(r"待验证|待确认|进行中|阻塞|尚未|\bblocked\b|\bin progress\b|\btodo\b|\bwip\b|\bpending\b", re.I)
_DECISION_CUES = re.compile(r"决定|决策|决议|\bdecision\b|\bdecided\b", re.I)
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
_WIKILINK = re.compile(r"\[\[([^\]\[|#\n]{1,256})(?:[#|][^\]\n]*)?\]\]")
_HEADING = re.compile(r"^#{2,3}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.M)
_FENCE = re.compile(r"^ {0,3}(?:`{3,}|~{3,}).*?^ {0,3}(?:`{3,}|~{3,})[ \t]*$", re.M | re.S)
_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")


def _scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value[:DECLARED_VALUE_CHARS]


def frontmatter(text: str) -> dict[str, str]:
    """Flatten a leading YAML-like block: top-level scalars plus one nested level.

    This is deliberately not a YAML parser: lists, anchors and multi-line values
    are ignored. A nested key never overrides a top-level key of the same name.
    """
    match = _FRONTMATTER.match(text)
    if not match:
        return {}
    top: dict[str, str] = {}
    nested: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, sep, value = line.strip().partition(":")
        if not sep or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", key):
            continue
        if indent == 0:
            if value.strip():
                top[key] = _scalar(value)
        elif value.strip():
            nested.setdefault(key, _scalar(value))
    return {**nested, **top}


def wikilinks(text: str) -> list[str]:
    """``[[target]]`` references outside fenced code, in document order, deduplicated."""
    found = _WIKILINK.findall(_FENCE.sub("", text))
    return list(dict.fromkeys(link.strip() for link in found if link.strip()))[:MAX_LINKS]


def sections(text: str) -> list[str]:
    """Second/third-level headings outside fenced code; a bounded table of contents."""
    found = [heading.strip()[:80] for heading in _HEADING.findall(_FENCE.sub("", text))]
    return list(dict.fromkeys(h for h in found if h))[:MAX_SECTIONS]


def parse_time(value: Any) -> float | None:
    """Epoch seconds from ISO-8601, ``YYYY-MM-DD`` or a numeric epoch; otherwise None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) and 0 < value < 4102444800 else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if re.fullmatch(r"\d{9,10}(\.\d+)?", text):
        return parse_time(float(text))
    if re.fullmatch(r"\d{4}/\d{2}/\d{2}", text):
        text = text.replace("/", "-")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def filename_date(path: str) -> float | None:
    """A ``YYYY-MM-DD`` in the file name, as the content's own dating convention."""
    match = _DATE.search(posixpath.basename(path.replace("\\", "/")))
    return parse_time("-".join(match.groups())) if match else None


def declared(text: str) -> dict[str, str]:
    """The allowlisted subset of front matter a client may forward as a declaration."""
    block = frontmatter(text)
    return {key: block[key] for key in DECLARED_KEYS if key in block and block[key]}


def source_meta(text: str, *, mtime: float | None) -> dict[str, Any]:
    """What a client captures beside the body; the Hub treats all of it as untrusted hints."""
    result: dict[str, Any] = {}
    if mtime is not None and math.isfinite(mtime) and mtime > 0:
        result["mtime"] = float(mtime)
    block = declared(text)
    if block:
        result["declared"] = block
    links = wikilinks(text)
    if links:
        result["links"] = links
    headings = sections(text)
    if headings:
        result["sections"] = headings
    return result


def validate_source_meta(value: Any) -> dict[str, Any]:
    """Server-side bound of a client's ``source_meta``; anything else is rejected."""
    if value is None:
        return {}
    if not isinstance(value, dict) or not set(value) <= {"mtime", "declared", "links", "sections"}:
        raise ValueError("invalid source metadata")
    result: dict[str, Any] = {}
    if "mtime" in value:
        mtime = value["mtime"]
        if isinstance(mtime, bool) or not isinstance(mtime, (int, float)) or parse_time(mtime) is None:
            raise ValueError("invalid source metadata")
        result["mtime"] = float(mtime)
    if "declared" in value:
        block = value["declared"]
        if not isinstance(block, dict) or not set(block) <= set(DECLARED_KEYS):
            raise ValueError("invalid source metadata")
        if not all(isinstance(v, str) and len(v) <= DECLARED_VALUE_CHARS for v in block.values()):
            raise ValueError("invalid source metadata")
        result["declared"] = dict(block)
    for key, limit, chars in (("links", MAX_LINKS, 256), ("sections", MAX_SECTIONS, 80)):
        if key in value:
            items = value[key]
            if (
                not isinstance(items, list)
                or len(items) > limit
                or not all(isinstance(i, str) and 0 < len(i) <= chars for i in items)
            ):
                raise ValueError("invalid source metadata")
            result[key] = list(items)
    return result


def _declared_of(record: dict[str, Any]) -> dict[str, str]:
    meta = record.get("source_meta")
    block = meta.get("declared") if isinstance(meta, dict) else None
    return block if isinstance(block, dict) else {}


def nature(record: dict[str, Any], role: str, *, body: str | None = None) -> dict[str, str]:
    """Knowledge nature with its provenance tier; declared beats parsed beats inferred."""
    block = _declared_of(record)
    for key in ("nature", "type"):
        value = TYPE_TO_NATURE.get(str(block.get(key, "")).strip().lower())
        if value:
            return {"value": value, "provenance": "declared"}
    status = STATUS_TO_NATURE.get(str(block.get("status", "")).strip().lower())
    if status:
        return {"value": status, "provenance": "declared"}
    kind = record.get("kind")
    if kind == "rule":
        return {"value": "rule", "provenance": "parsed"}
    if kind in ("skill", "config") or role == "navigation":
        return {"value": "reference", "provenance": "parsed"}
    if kind == "transcript" or role in ("conversation_summary", "raw_memory"):
        return {"value": "episode", "provenance": "parsed"}
    text = body if body is not None else record.get("body")
    if isinstance(text, str) and text:
        sample = text[:4000]
        if _STATE_CUES.search(sample):
            return {"value": "project_state", "provenance": "inferred"}
        if _DECISION_CUES.search(sample):
            return {"value": "decision", "provenance": "inferred"}
    return {"value": UNKNOWN, "provenance": UNKNOWN}


def times(record: dict[str, Any], signals: dict[str, Any] | None = None) -> dict[str, Any]:
    """The trusted-time set. Each entry is ``{"value": epoch, "source": tier}`` or None."""
    block = _declared_of(record)
    meta = record.get("source_meta") if isinstance(record.get("source_meta"), dict) else {}
    signals = signals or {}

    def pick(*candidates: tuple[Any, str]) -> dict[str, Any] | None:
        for raw, source in candidates:
            value = parse_time(raw)
            if value is not None:
                return {"value": value, "source": source}
        return None

    return {
        "content_created": pick(
            (block.get("created"), "declared"),
            (block.get("date"), "declared"),
            (filename_date(str(record.get("path", ""))), "parsed"),
        ),
        "content_modified": pick(
            (block.get("modified"), "declared"),
            (block.get("updated"), "declared"),
            (meta.get("mtime"), "parsed"),
        ),
        "observed": pick((record.get("observed_at"), "server")),
        "last_delivered": pick((signals.get("last_delivered"), "server")),
        "last_cited": pick((signals.get("last_cited"), "server")),
        "confirmed": pick((signals.get("confirmed_at"), "server")),
        "expires": pick((block.get("expires"), "declared"), (block.get("valid_until"), "declared")),
    }


def reference_time(times_set: dict[str, Any]) -> tuple[float | None, str]:
    """Decay reference: confirmation, else content modification, else creation; never observation."""
    for key in ("confirmed", "content_modified", "content_created"):
        entry = times_set.get(key)
        if entry:
            return float(entry["value"]), key
    return None, UNKNOWN


def declared_filter(record: dict[str, Any], *, now: float) -> dict[str, str] | None:
    """Only an explicit declaration retires a memory: ``superseded_by`` or a past ``expires``."""
    block = _declared_of(record)
    target = str(block.get("superseded_by", "")).strip()
    if target:
        return {"reason": "superseded", "by": target[:128]}
    expiry = times(record)["expires"]
    if expiry and expiry["value"] < now:
        return {"reason": "expired", "at": datetime.fromtimestamp(expiry["value"], timezone.utc).isoformat()}
    return None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def decay(
    record: dict[str, Any],
    role: str,
    signals: dict[str, Any] | None,
    *,
    now: float,
    body: str | None = None,
) -> dict[str, Any]:
    """Legacy AMH forgetting-curve coefficient, re-ranking only (weight within ±5%).

    retention = 0.5 ** (age / half_life); access/support/gain/contradiction
    multipliers follow agent_brain/memory/recall/retrieval_decay.py. Without a
    trusted reference time retention is a neutral 0.5 and the reason says so.
    """
    signals = signals or {}
    kind = nature(record, role, body=body)
    half_life = HALF_LIFE_DAYS.get(kind["value"], HALF_LIFE_DAYS[UNKNOWN])
    reference, basis = reference_time(times(record, signals))
    delivered = max(0, int(signals.get("delivered") or 0))
    support = max(0, int(signals.get("support") or 0))
    contradict = max(0, int(signals.get("contradict") or 0))
    gain = float(signals.get("gain") or 0.0)
    multipliers = {
        "access": 1 + min(0.35, math.log1p(delivered) * 0.08),
        "support": 1 + min(0.18, support * 0.03),
        "gain": 1 + _clamp(gain * 0.12, -0.15, 0.15),
        "contradiction": 1 - min(0.45, contradict * 0.08),
    }
    if reference is None:
        # No trusted time: neither fresh nor stale. One half-life's retention
        # keeps a dated recent memory ahead and a dated stale one behind.
        age_days = None
        retention = 0.5
        reason = "time_unknown_neutral"
    else:
        age_days = max(0.0, (now - reference) / 86400)
        retention = 0.5 ** (age_days / half_life)
        reason = "decayed_from_" + basis
    coefficient = retention
    for factor in multipliers.values():
        coefficient *= factor
    coefficient = _clamp(coefficient, 0.01, 1.35)
    weight = 1 + 0.05 * math.tanh(math.log(coefficient))
    return {
        "nature": kind,
        "half_life_days": half_life,
        "reference": None if reference is None else {"value": reference, "basis": basis},
        "age_days": None if age_days is None else round(age_days, 2),
        "retention": round(retention, 4),
        "multipliers": {k: round(v, 4) for k, v in multipliers.items()},
        "coefficient": round(coefficient, 4),
        "weight": round(weight, 5),
        "reason": reason,
    }
