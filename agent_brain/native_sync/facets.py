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
from collections.abc import Collection, Mapping
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
# Epoch bounds shared by the client (mtime capture) and the Hub (parse_time):
# 2100-01-01 is the upper bound; a 13-digit value is read as milliseconds.
MAX_EPOCH = 4102444800
MILLISECOND_EPOCH = 1e12
# Body cues only count near the top of a document: the title and the first
# lines after front matter, where a writer states what the note is.
CUE_LINES = 5
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
_STATE_CUES = re.compile(
    r"待验证|待确认|进行中|阻塞|尚未|\bblocked\b|\bin progress\b|\btodo\b|\bwip\b|\bpending\b", re.I
)
_DECISION_CUES = re.compile(r"决定|决策|决议|\bdecision\b|\bdecided\b", re.I)
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
_WIKILINK = re.compile(r"\[\[([^\]\[|#\n]{1,256})(?:[#|][^\]\n]*)?\]\]")
_HEADING = re.compile(r"^#{2,3}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.M)
_FENCE = re.compile(r"^ {0,3}(?:`{3,}|~{3,}).*?^ {0,3}(?:`{3,}|~{3,})[ \t]*$", re.M | re.S)
_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
# A calendar date with optional single-digit month/day and either separator,
# followed by whatever time part the writer added.
_LOOSE_DATE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})(.*)", re.S)
_DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}")


def _scalar(value: str) -> str | None:
    """A quoted or bare scalar; flow sequences/mappings (``[a, b]``, ``{k: v}``) are not scalars."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    elif value[:1] in "[{":
        return None
    return value[:DECLARED_VALUE_CHARS]


def frontmatter(text: str) -> dict[str, str]:
    """Flatten a leading YAML-like block: top-level scalars plus one nested level.

    This is deliberately not a YAML parser: lists (block or flow), anchors,
    multi-line values and anything nested deeper than one level are ignored.
    A nested key never overrides a top-level key of the same name. A leading
    byte-order mark is tolerated.
    """
    match = _FRONTMATTER.match(text.lstrip("﻿"))
    if not match:
        return {}
    top: dict[str, str] = {}
    nested: dict[str, str] = {}
    nested_indent: int | None = None
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, sep, value = line.strip().partition(":")
        if indent == 0:
            nested_indent = None
        elif nested_indent is None or indent < nested_indent:
            nested_indent = indent
        elif indent > nested_indent:
            continue  # third level and deeper
        if not sep or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", key):
            continue
        scalar = _scalar(value) if value.strip() else None
        if scalar is None:
            continue
        if indent == 0:
            top[key] = scalar
        else:
            nested.setdefault(key, scalar)
    return {**nested, **top}


def wikilinks(text: str) -> list[str]:
    """``[[target]]`` references outside fenced code, in document order, deduplicated."""
    found = _WIKILINK.findall(_FENCE.sub("", text))
    return list(dict.fromkeys(link.strip() for link in found if link.strip()))[:MAX_LINKS]


def sections(text: str) -> list[str]:
    """Second/third-level headings outside fenced code; a bounded table of contents."""
    found = [heading.strip()[:80] for heading in _HEADING.findall(_FENCE.sub("", text))]
    return list(dict.fromkeys(h for h in found if h))[:MAX_SECTIONS]


def _normalize_date(text: str) -> str:
    """``2026-11-3`` / ``2026/1/3`` → ``2026-11-03``; anything else is returned as is."""
    match = _LOOSE_DATE.fullmatch(text)
    if not match:
        return text
    year, month, day, rest = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d}{rest}"


def parse_time(value: Any, *, end_of_day: bool = False) -> float | None:
    """Epoch seconds from ISO-8601, a calendar date or a numeric epoch; otherwise None.

    Dates accept single-digit month/day and ``/`` separators. A 13-digit number
    is read as milliseconds. With ``end_of_day`` a date without a time part
    means the last second of that day in UTC (for ``expires``-style bounds).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except OverflowError:
            return None
        if not math.isfinite(number):
            return None
        if number >= MILLISECOND_EPOCH:
            number /= 1000
        return number if 0 < number < MAX_EPOCH else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if re.fullmatch(r"\d{9,13}(\.\d+)?", text):
        return parse_time(float(text))
    text = _normalize_date(text)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    stamp = parsed.timestamp()
    if end_of_day and _DATE_ONLY.fullmatch(text):
        stamp += 86399
    return stamp


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
    # The same bounds the Hub's parse_time enforces; a bad clock (year 2103)
    # must not get the whole upload refused for one hint.
    if mtime is not None and math.isfinite(mtime) and 0 < mtime < MAX_EPOCH:
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
        if (
            isinstance(mtime, bool)
            or not isinstance(mtime, (int, float))
            or parse_time(mtime) is None
        ):
            raise ValueError("invalid source metadata")
        result["mtime"] = float(mtime)
    if "declared" in value:
        block = value["declared"]
        if not isinstance(block, dict) or not set(block) <= set(DECLARED_KEYS):
            raise ValueError("invalid source metadata")
        if not all(isinstance(v, str) and len(v) <= DECLARED_VALUE_CHARS for v in block.values()):
            raise ValueError("invalid source metadata")
        if block:
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
            if items:
                result[key] = list(items)
    # Empty containers carry nothing: {"links": []} is the same as no metadata,
    # so it neither changes the content hash nor bumps a revision.
    return result


def _declared_of(record: dict[str, Any]) -> dict[str, str]:
    meta = record.get("source_meta")
    block = meta.get("declared") if isinstance(meta, dict) else None
    return block if isinstance(block, dict) else {}


def superseded_target(record: dict[str, Any]) -> str:
    """The declared ``superseded_by`` asset id (bounded), or an empty string."""
    return str(_declared_of(record).get("superseded_by", "")).strip()[:128]


def _cue_head(title: Any, body: str) -> str:
    """Where body cues may count: the title plus the first lines after front matter."""
    text = _FRONTMATTER.sub("", body.lstrip("﻿")[:4000], count=1)
    lines = [line for line in text.splitlines() if line.strip()][:CUE_LINES]
    return (str(title or "") + "\n" + "\n".join(lines))[:4000]


def nature(record: dict[str, Any], role: str, *, body: str | None = None) -> dict[str, str]:
    """Knowledge nature with its provenance tier; declared beats parsed beats inferred.

    Body cues (``进行中``, ``decided`` …) are weak evidence: they only count in
    the title or the first CUE_LINES non-blank lines of the body, never from a
    passing mention deep inside an otherwise stable document.
    """
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
        sample = _cue_head(record.get("title"), text)
        if _STATE_CUES.search(sample):
            return {"value": "project_state", "provenance": "inferred"}
        if _DECISION_CUES.search(sample):
            return {"value": "decision", "provenance": "inferred"}
    return {"value": UNKNOWN, "provenance": UNKNOWN}


def times(record: dict[str, Any], signals: dict[str, Any] | None = None) -> dict[str, Any]:
    """The trusted-time set. Each entry is ``{"value": epoch, "source": tier}`` or None.

    ``content_modified`` is declared only (``modified`` / ``updated``). The file
    system mtime is its own entry, ``file_mtime``: it is often a clone or copy
    time, so it must not masquerade as when the content was written. A pure
    ``expires`` date runs to the last second of that day (UTC).
    """
    block = _declared_of(record)
    meta = record.get("source_meta") if isinstance(record.get("source_meta"), dict) else {}
    signals = signals or {}

    def pick(*candidates: tuple[Any, str], end_of_day: bool = False) -> dict[str, Any] | None:
        for raw, source in candidates:
            value = parse_time(raw, end_of_day=end_of_day)
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
        ),
        "file_mtime": pick(((meta or {}).get("mtime"), "parsed")),
        "observed": pick((record.get("observed_at"), "server")),
        "last_delivered": pick((signals.get("last_delivered"), "server")),
        "last_cited": pick((signals.get("last_cited"), "server")),
        "confirmed": pick((signals.get("confirmed_at"), "server")),
        "expires": pick(
            (block.get("expires"), "declared"),
            (block.get("valid_until"), "declared"),
            end_of_day=True,
        ),
    }


def reference_time(times_set: dict[str, Any]) -> tuple[float | None, str]:
    """Decay reference, most trusted first: confirmed > declared modification >
    creation (declared or file-name date) > file mtime. Never the Hub's observation."""
    for key in ("confirmed", "content_modified", "content_created", "file_mtime"):
        entry = times_set.get(key)
        if entry:
            return float(entry["value"]), key
    return None, UNKNOWN


def declared_filter(
    record: dict[str, Any],
    *,
    now: float,
    existing: Mapping[str, str | None] | Collection[str] | None = None,
) -> dict[str, Any] | None:
    """Only an explicit declaration retires a memory: ``superseded_by`` or a past ``expires``.

    Returns None (keep), a drop verdict ``{"reason": "superseded" | "expired", …}``,
    or a kept note with ``"drop": False`` when a ``superseded_by`` cannot be
    honoured. ``existing`` is the set of declared targets the Hub confirmed to
    exist (undeleted) in this organization; as a mapping it also carries each
    target's own ``superseded_by`` so an A↔B cycle can be recognized. Without
    it the declaration is taken at its word, as before.
    """
    target = superseded_target(record)
    note: dict[str, Any] | None = None
    if target:
        if target == str(record.get("asset_id", "")):
            note = {"reason": "superseded_self", "by": target, "drop": False}
        elif existing is not None and target not in existing:
            note = {"reason": "superseded_target_missing", "by": target, "drop": False}
        elif (
            isinstance(existing, Mapping)
            and existing.get(target)
            and existing[target] == record.get("asset_id")
        ):
            note = {"reason": "superseded_cycle", "by": target, "drop": False}
        else:
            return {"reason": "superseded", "by": target}
    expiry = times(record)["expires"]
    if expiry and expiry["value"] < now:
        return {
            "reason": "expired",
            "at": datetime.fromtimestamp(expiry["value"], timezone.utc).isoformat(),
        }
    return note


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

    weight = 1 + 0.05·tanh(ln coefficient) with the coefficient clamped to
    [0.01, e]. Actual range: at most −5% downward; upward at most about +3.8%
    in principle, but the multipliers' product is naturally ≤ 1.83 (1.35 ×
    1.18 × 1.15) and retention ≤ 1, so a real memory gains at most about +2.7%.
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
    coefficient = _clamp(coefficient, 0.01, math.e)
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
