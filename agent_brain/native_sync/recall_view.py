"""What the prompt hook sends, whether it should, and how a recall is shown.

Three pure helpers, no I/O:

* ``normalize`` keeps the member's own words: host-injected reminder blocks,
  earlier organization context and image placeholders are dropped, the
  member's text goes first and pasted material after it, capped for search.
* ``admit`` skips acknowledgements ("1", "好的", "继续") that cannot carry a
  topic. Ported from the local hook's admission list and extended for Chinese.
* ``render`` builds the call line, the parameters and a table of every
  recalled memory with its rank, routes, similarity and delivery state.
  Hosts that print a hook message as plain text (Claude Code's transcript,
  Cowork) get a box-drawn table padded by display width; Markdown is kept for
  context the model reads.
"""

from __future__ import annotations

import os
import re
import subprocess
import unicodedata
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from .common import PRODUCTS, source_product

QUERY_CHARS = 1500
DISPLAY_MODES = ("table", "summary", "off")
DISPLAY_KEY = "recall_display"

_BLOCKS = [
    re.compile(r"<system-reminder\b[^>]*>.*?(?:</system-reminder>|$)", re.S | re.I),
    re.compile(r"\[AMH_ORG_CONTEXT_V1\].*?(?:\[/AMH_ORG_CONTEXT_V1\]|$)", re.S),
    re.compile(r"\[AMH_RECALL_FEEDBACK_V1\].*?(?:\[/AMH_RECALL_FEEDBACK_V1\]|$)", re.S),
    re.compile(r"<agent[_-]brain\w*\b[^>]*>.*?(?:</agent[_-]brain\w*>|$)", re.S | re.I),
    re.compile(r"amh-native-recall:[0-9a-f]{24}"),
]
_NOISY_LINES = ("available mcp servers:", "available tools:", "current workspace may include")
_PASTED = re.compile(r"<pasted_content[^>]*>(.*?)</pasted_content[^>]*>", re.S | re.I)
_IMAGE = re.compile(r"\[(?:Image|Audio|Video|PDF|Document) #\d+\]", re.I)
_FILE_URI = re.compile(r"file://\S+", re.I)
# Acknowledgements and control words with no topic of their own. Compared
# after removing whitespace and punctuation and folding case.
# Too short to carry a topic: at most two characters, or three Latin letters
# ("好的", "ok", "yes"). Everything longer goes to the relevance gate, which
# refuses "好的，发吧" on its own (53 former list words measured against two org
# stores: one injection in 106 runs). No word list to keep growing.
NO_TOPIC_CHARS = 2
NO_TOPIC_LATIN = 3
_BACKGROUND = (
    "<task-notification",
    "<local-command-",
    "<bash-stdout",
    "<bash-stderr",
    "<bash-input",
    "<command-",
    "[Request interrupted",
)
_CONTROL = re.compile(r"^/(remember|goal|compact|clear|help|model|config|status)\b", re.I)


def normalize(prompt: str) -> str:
    """The member's intent for search; never longer than QUERY_CHARS."""
    text = (prompt or "").replace("\r\n", "\n").replace("\r", "\n")
    for pattern in _BLOCKS:
        text = pattern.sub(" ", text)
    pasted = [m.group(1) for m in _PASTED.finditer(text)]
    own = _PASTED.sub(" ", text)
    own = "\n".join(
        line for line in own.split("\n") if not line.strip().lower().startswith(_NOISY_LINES)
    )
    own = _IMAGE.sub(" ", own)
    own = _FILE_URI.sub(lambda m: " " + (urlsplit(m.group(0)).path.rsplit("/", 1)[-1]) + " ", own)
    parts = [re.sub(r"[ \t]+", " ", own).strip()]
    parts += [re.sub(r"\s+", " ", p).strip() for p in pasted]
    return "\n".join(p for p in parts if p)[:QUERY_CHARS].strip()


def _folded(text: str) -> str:
    kept = [
        ch
        for ch in unicodedata.normalize("NFKC", text).casefold()
        if not unicodedata.category(ch).startswith(("P", "Z", "S", "C"))
    ]
    return "".join(kept)


FOLLOW_UP_CHARS = 16
_TECHNICAL = re.compile(r"[A-Za-z0-9_./-]{3,}")


def follow_up(query: str) -> bool:
    """A short prompt with no topic of its own ("那第二个呢", "发布了告诉我"):
    what it is about lives in the previous prompt."""
    if len(_folded(query)) > FOLLOW_UP_CHARS:
        return False
    return not any(
        any(ch.isdigit() for ch in token) or any(ch in token for ch in "_./-")
        for token in _TECHNICAL.findall(query)
    )


# A terminal's own reply to a capability query, leaked into the prompt box:
# "?62;22;52c>1;10;0c". Never something a member typed.
_TERMINAL_REPLY = re.compile(r"(?:\x1b?\[?[?>]?\d+(?:;\d+)*[A-Za-z~]\s*)+")


def admit(query: str) -> tuple[bool, str]:
    """Whether a normalized prompt can carry a topic worth searching for."""
    if not query.strip():
        return False, "empty"
    folded = _folded(query)
    if not folded:
        return False, "punctuation_only"
    stripped = query.strip()
    if _TERMINAL_REPLY.fullmatch(stripped) and (";" in stripped or stripped[0] in "?>\x1b["):
        return False, "terminal_reply"
    if _CONTROL.match(query.strip()):
        return False, "control_command"
    if query.lstrip().startswith(_BACKGROUND):
        # A host's own background event (a finished task, a monitor line)
        # arrives as a prompt but carries no question from the member.
        return False, "background_event"
    if (
        len(folded) <= NO_TOPIC_CHARS
        or (folded.isascii() and folded.isalpha() and len(folded) <= NO_TOPIC_LATIN)
        or (folded.isdigit() and len(folded) <= 3)
    ):
        return False, "acknowledgement"
    return True, "topic"


SKIP_TEXT = {
    "empty": "空提示",
    "punctuation_only": "只有标点",
    "control_command": "宿主控制命令",
    "acknowledgement": "太短，没有可检索的主题",
    "background_event": "宿主的后台事件通知，不是成员的提问",
    "terminal_reply": "终端自动回传的控制应答，不是成员的提问",
}
STATE_TEXT = {
    "packed": "已注入",
    "covered": "本会话已注入过",
    "budget": "超出注入预算",
    "empty": "无正文",
    "hint": "只给标题",
}
REASON_TEXT = {
    "semantic_model_unavailable": "Hub 语义模型不可用",
    "semantic_index_incomplete_rebuild_required": "语义索引未建完",
    "semantic_index_not_built": "语义索引未建",
    "semantic_model_not_configured": "Hub 未配置语义模型",
    "semantic_index_model_mismatch_rebuild_required": "语义索引待按新模型重建",
    "semantic_query_failed": "语义检索出错",
    "gate_background_pending": "门控背景未就绪",
    "hook_lexical_guard": "只注入含提问中标识的记忆",
}


def _reason(code: str) -> str:
    """A degraded-state reason in words; unknown codes stay as they are."""
    return REASON_TEXT.get(code.split(":", 1)[0], code)


MODE_TEXT = {"hybrid": "混合（词法+语义）", "lexical": "仅词法"}
VIA_TEXT = {"service": "常驻服务（热连接）", "worker": "子进程", "disabled": "已关闭"}


def _cell(value: Any, limit: int) -> str:
    text = " ".join(str(value if value is not None else "").split()).replace("|", "/")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _producer(record: dict[str, Any]) -> str:
    name = record.get("producer_name")
    if name and not record.get("producer_unknown"):
        return str(name)
    # Historical content the member chose to share: the uploader stands as
    # its author (the trace keeps the distinction).
    uploader = record.get("uploader_name")
    return str(uploader) if uploader else "未知"


def _routes(record: dict[str, Any]) -> str:
    trace = record.get("recall")
    routes = trace.get("routes") if isinstance(trace, dict) else None
    ranks = routes if isinstance(routes, dict) else record.get("retrieval_ranks") or {}
    labels = (("lexical", "词法"), ("semantic", "语义"), ("hopfield", "联想"))
    parts = [f"{label}{ranks[key]}" for key, label in labels if isinstance(ranks.get(key), int)]
    return "·".join(parts) or "—"


def _similarity(record: dict[str, Any]) -> str:
    """The learned relevance probability when the Hub has one, else raw similarity."""
    trace = record.get("recall")
    chance = trace.get("relevance") if isinstance(trace, dict) else None
    if isinstance(chance, (int, float)) and 0 <= chance <= 1:
        return f"{round(chance * 100)}%"
    match = record.get("semantic_match") or {}
    value = match.get("similarity") if isinstance(match, dict) else None
    return f"{value:.2f}" if isinstance(value, (int, float)) else "—"


def _usage(record: dict[str, Any]) -> str:
    """How often this memory was used when injected: share and number of judged turns."""
    trace = record.get("recall")
    usage = trace.get("usage") if isinstance(trace, dict) else None
    if not isinstance(usage, dict):
        return "—"
    used, judged = usage.get("used"), usage.get("judged")
    if not isinstance(used, int) or not isinstance(judged, int) or judged <= 0:
        return "—"
    return f"{round(100 * used / judged)}%·{judged}次"


def _updated(record: dict[str, Any]) -> str:
    for key in ("source_mtime", "observed_at", "updated_at", "created_at"):
        value = record.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return datetime.fromtimestamp(value).strftime("%m-%d")
    return "—"


def _title(record: dict[str, Any]) -> str:
    """Navigation files share generic titles; name the file and the part instead."""
    path = str(record.get("path") or "")
    name = path.rsplit("/", 1)[-1]
    title = str(record.get("title") or "")
    if not title or title.lower() in {"memory", "memory_summary", "claude", "agents"}:
        title = name or title
    if name.lower() == "skill.md" and "/" in path:
        title = path.rsplit("/", 2)[-2]  # a skill is named by its directory
    count, index = record.get("part_count"), record.get("part_index")
    if isinstance(count, int) and count > 1 and isinstance(index, int):
        title += f" §{index + 1}/{count}"
    return title


_FRONT = re.compile(r"\A\s*---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.S)
_DESCRIPTION = re.compile(r"^description:\s*(.+)$", re.M)
_TASK_GROUP = re.compile(r"^#\s*Task Group:\s*(.+)$", re.M)
_INDEX_LINK = re.compile(r"^\s*-\s*\[([^\]]+)\]\(", re.M)
_HEADING = re.compile(r"^#{1,4}\s+(.+)$", re.M)
_MARKUP = re.compile(r"\*\*|__|`|\[\[|\]\]|^\s*[-*>]\s+|^\s*\d+\.\s+", re.M)


# What an aggregated note carries beside its content: file lists and keyword lines.
_BOOKKEEPING = re.compile(r"rollout_summaries/|rollout_path=|thread_id=|^(applies_to|scope):", re.I)


def _section_gist(text: str, query: str) -> str:
    """The sentence of a matched section that shows what it says: most words in common
    with the prompt, else the first real sentence. Never a heading or a file list."""
    from .relevance import query_terms, terms

    wanted = query_terms(query)
    best, best_score, heading, keywords = "", -1, "", False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("《"):
            continue
        marked = _HEADING.match(line)
        if marked:
            heading = marked.group(1).strip()
            keywords = heading.casefold() == "keywords"
            continue
        if keywords or _BOOKKEEPING.search(line) or line.startswith(("```", "|", "---")):
            continue
        score = len(wanted & terms(line))
        if score > best_score:
            line = _MARKUP.sub("", line)
            best, best_score = (f"{heading}：{line}" if heading else line), score
    return _one_line(best) if best else ""


def _why(record: dict[str, Any], query: str) -> str:
    """Why this memory came up, in the member's terms: its ranks and the words it shares."""
    from .relevance import query_terms, terms

    trace = record.get("recall")
    routes = trace.get("routes") if isinstance(trace, dict) else None
    ranks = routes if isinstance(routes, dict) else {}
    parts = []
    if isinstance(ranks.get("semantic"), int):
        parts.append(f"语义相近排第 {ranks['semantic']}")
    if isinstance(ranks.get("lexical"), int):
        parts.append(f"关键词排第 {ranks['lexical']}")
    body = record.get("body")
    shared = query_terms(query) & terms(body) if isinstance(body, str) and query else set()
    if shared:
        words = sorted(shared, key=lambda word: (-len(word), word))[:4]
        parts.append("共同词 " + "、".join(words))
    return "，".join(parts)


def _summary(record: dict[str, Any], query: str = "") -> str:
    """What the memory says, in one line: the matched section's telling sentence, its own
    description, the task groups of an index chunk, the entries of an index, or its first
    heading/sentence."""
    if record.get("deleted"):
        return ""  # a retired memory shows nothing of what it said
    body, index_text = record.get("body"), record.get("index_text")
    text: str = body if isinstance(body, str) and body.strip() else ""
    if not text and isinstance(index_text, str):
        text = index_text
    if not text.strip():
        return ""
    if record.get("section"):
        gist = _section_gist(text, query)
        if gist:
            return gist
    front = _FRONT.match(text)
    if front:
        described = _DESCRIPTION.search(front.group(1))
        if described:
            return _one_line(described.group(1).strip().strip("'\""))
        text = text[front.end() :]
    groups = [g.strip() for g in _TASK_GROUP.findall(text)]
    if groups:
        return _one_line("任务组：" + "；".join(groups[:3]) + ("等" if len(groups) > 3 else ""))
    entries = [e.strip() for e in _INDEX_LINK.findall(text)]
    if len(entries) >= 2:
        return _one_line(f"索引 {len(entries)} 条：" + "、".join(entries[:5]))
    heading = _HEADING.search(text)
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "```", "|", "---"))
    ]
    first = _MARKUP.sub("", lines[0]) if lines else ""
    if heading and first:
        return _one_line(heading.group(1).strip() + "：" + first)
    return _one_line(heading.group(1) if heading else first)


def _one_line(text: str, limit: int = 160) -> str:
    text = " ".join(_MARKUP.sub("", text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _kind(record: dict[str, Any]) -> str:
    kind = {"memory": "记忆", "rule": "规则", "skill": "技能", "transcript": "会话"}.get(
        str(record.get("kind")), str(record.get("kind") or "")
    )
    role = {"navigation": "索引", "topic": "主题", "conversation_summary": "摘要"}.get(
        str(record.get("role") or ""), ""
    )
    return kind + ("·" + role if role else "")


STATE_SHORT = {
    "packed": "已注入",
    "covered": "已注入过",
    "budget": "超预算",
    "empty": "无正文",
    "hint": "只给标题",
}


def _width(text: str) -> int:
    """Terminal columns: wide and fullwidth characters take two, combining marks none."""
    total = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        total += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return total


def _fit(text: str, width: int) -> str:
    """Clip to ``width`` columns (marking the cut with "..") and pad with spaces.

    Only ASCII marks are added, since East Asian "ambiguous" characters such as
    "…" or "·" are one column in some terminals and two in others.
    """
    text = " ".join(str(text).split())
    if _width(text) > width:
        kept, used = [], 0
        for ch in text:
            step = _width(ch)
            if used + step > width - 2:
                break
            kept.append(ch)
            used += step
        text = "".join(kept) + ".."
    return text + " " * (width - _width(text))


def _box(
    headers: list[str],
    rows: list[list[str]],
    widths: list[int],
    details: list[str] | None = None,
) -> list[str]:
    """A ruled table; a detail line spans every column but the first under its row."""

    def line(left: str, mid: str, right: str) -> str:
        return left + mid.join("─" * (w + 2) for w in widths) + right

    def cells(values: list[str]) -> str:
        return "│" + "│".join(" " + _fit(v, w) + " " for v, w in zip(values, widths)) + "│"

    head = "─" * (widths[0] + 2)
    rest = [("─" * (w + 2)) for w in widths[1:]]
    span = sum(w + 2 for w in widths[1:]) + len(widths) - 2  # merged columns, inner width
    out = [line("┌", "┬", "┐"), cells(headers)]
    previous_detail = False
    for index, row in enumerate(rows):
        if previous_detail:
            out.append("├" + head + "┼" + "┬".join(rest) + "┤")
        else:
            out.append(line("├", "┼", "┤"))
        out.append(cells(row))
        detail = details[index] if details else ""
        previous_detail = bool(detail)
        if detail:
            out.append("│" + " " * (widths[0] + 2) + "├" + "┴".join(rest) + "┤")
            for part in detail.split("\n"):
                out.append("│" + " " * (widths[0] + 2) + "│ " + _fit(part, span - 2) + " │")
    if previous_detail:
        out.append("└" + head + "┴" + "─" * span + "┘")
    else:
        out.append(line("└", "┴", "┘"))
    return out


def _plain(text: str) -> str:
    """Box cells avoid ambiguous-width marks."""
    return text.replace("·", " ").replace("—", "-").replace("§", "#").replace("…", "..")


def render(
    *,
    hub: str,
    query: str,
    asking: dict[str, Any],
    budget: float,
    elapsed_ms: int,
    via: str,
    mode: str,
    retrieval: dict[str, Any] | None,
    trace_id: str | None,
    hits: list[dict[str, Any]],
    states: dict[str, str],
    display: str,
    style: str = "markdown",
    hints: list[dict[str, Any]] | None = None,
    context: str = "",
) -> str:
    """Call line, parameters and the recalled-memory table.

    ``style`` is "markdown" for context the model reads and "box" for a hook
    message the host prints verbatim.
    """
    base = hub.rstrip("/") or "Hub"
    retrieval = retrieval or {}
    packed = sum(1 for s in states.values() if s == "packed")
    if mode == "remote":
        how = MODE_TEXT.get(str(retrieval.get("mode")), "Hub 检索")
        reasons = list(dict.fromkeys(_reason(str(r)) for r in retrieval.get("reasons") or []))[:3]
        if retrieval.get("mode") == "lexical" and reasons:
            how += "（降级：" + "、".join(reasons) + "）"
    else:
        how = {
            "cache": "本机缓存（Hub 不可达）",
            "unavailable": "未取到（Hub 不可达或超时）",
            "denied": "设备被拒绝",
        }.get(mode, mode)
    lines = [
        f"**组织记忆召回** · {how} · 命中 {len(hits)} 条 · 注入 {packed} 条 · "
        + (f"只给标题 {len(hints)} 条 · " if hints else "")
        + f"耗时 {elapsed_ms} ms / 预算 {round(budget * 1000)} ms · {VIA_TEXT.get(via, via)}",
    ]
    if display == "summary":
        return lines[0]
    lines.append(
        f"调用：`POST {base}/native/v1/recall` · 通道 hook · 范围 memory+rule · 上限 8 条"
        + (f" · trace `{trace_id}`" if trace_id else "")
    )
    lines.append(
        "入参：查询「"
        + _cell(query, 40 if style == "box" else 80)
        + f"」（{len(query)} 字）· 产品 {asking.get('product') or '未知'} · 项目 "
        + (asking.get("project") or "未登记")
        + f" · 会话 {asking.get('session_digest') or '—'}"
    )
    if context:
        lines.append(
            "上文补充：这句较短，检索时带上了上一轮提问「" + _cell(context, 40) + "」，结果只给标题"
        )
    tail = _hint_line(hints or [])
    if not hits:
        lines += [tail] if tail else []
        return _as_plain("\n".join(lines)) if style == "box" else "\n".join(lines)
    if style == "box":
        return (
            _as_plain("\n".join(lines))
            + "\n"
            + "\n".join(_box_table(hits, states, terminal_columns(), query))
            + ("\n" + tail if tail else "")
        )
    lines += [
        "",
        "| # | 记忆 | 内容 | 类型 | 来源 | 作者 | 项目 | 版本 | 更新 | 命中路由 | 相关性 | 使用率 | 本轮 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for rank, record in enumerate(hits, 1):
        from .recall import coverage_key

        title = _title(record)
        product = source_product(str(record.get("product") or ""))
        lines.append(
            "| "
            + " | ".join(
                [
                    str(rank),
                    _cell(title, 28),
                    _cell(_summary(record, query) or "—", 60),
                    _kind(record),
                    _cell(PRODUCTS.get(product, product), 14),
                    _cell(_producer(record), 16),
                    _cell(record.get("project") or "—", 16),
                    f"r{record.get('revision', '?')}",
                    _updated(record),
                    _routes(record),
                    _similarity(record),
                    _usage(record),
                    STATE_TEXT.get(states.get(coverage_key(record), ""), "—"),
                ]
            )
            + " |"
        )
    if tail:
        lines += ["", tail]
    return "\n".join(lines)


def _hint_line(hints: list[dict[str, Any]]) -> str:
    """The title-only tier in one line: named for the model, bodies not injected."""
    if not hints:
        return ""
    names = []
    for hint in hints:
        title = _cell(hint.get("title") or hint.get("path") or hint.get("asset_id"), 28)
        probability = hint.get("probability")
        names.append(
            f"《{title}》"
            + (f"（{round(probability * 100)}%）" if isinstance(probability, (int, float)) else "")
        )
    return "只给标题（未注入正文，模型需要时自行读取）：" + "、".join(names)


def render_skip(reason: str, query: str) -> str:
    return f"组织记忆召回 · 跳过：{SKIP_TEXT.get(reason, reason)}（「{_cell(query, 24)}」）"


def _as_plain(text: str) -> str:
    """Header lines without Markdown emphasis or code marks."""
    return text.replace("**", "").replace("`", "")


# Columns of the box table, in order: key, header, width.
_COLUMNS: tuple[tuple[str, str, int], ...] = (
    ("rank", "#", 1),
    ("title", "记忆", 18),
    ("kind", "类型", 4),
    ("source", "来源", 11),
    ("author", "作者", 10),
    ("project", "项目", 10),
    ("version", "版本 更新", 9),
    ("routes", "命中路由", 13),
    ("relevance", "相关性", 6),
    ("usage", "使用率", 8),
    ("state", "本轮", 8),
)
# A narrow terminal first tightens columns that hold spare room, then gives up
# whole columns, least useful first. A wrapped box is unreadable; a missing
# column is not.
_TIGHTER = {"title": 17, "author": 8}
_DROP_ORDER = ("version", "project", "source", "kind", "usage", "author", "routes")
# The host indents a hook message and needs a spare column at the right edge.
_HOST_MARGIN = 6
_UNKNOWN_COLUMNS = 126


def terminal_columns() -> int | None:
    """The member's terminal width, from the host's controlling terminal.

    A hook's own stdio are pipes, so walk up to the first ancestor that has a
    terminal and ask that device. Anything unexpected means "unknown".
    """
    try:
        import fcntl
        import struct
        import termios

        pid = os.getppid()
        for _ in range(8):
            fields = subprocess.run(
                ["ps", "-o", "ppid=,tty=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=0.3,
            ).stdout.split()
            if len(fields) != 2:
                break
            parent, tty = fields
            if tty.strip("?-"):
                device = os.open("/dev/" + tty, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
                try:
                    size = fcntl.ioctl(device, termios.TIOCGWINSZ, b"\0" * 8)
                finally:
                    os.close(device)
                columns = struct.unpack("hhhh", size)[1]
                if columns > 0:
                    return int(columns)
                break
            pid = int(parent)
    except (ImportError, OSError, ValueError, subprocess.SubprocessError):
        pass
    try:
        columns = int(os.environ.get("COLUMNS") or 0)
    except ValueError:
        return None
    return columns if columns > 0 else None


def _layout(with_project: bool, columns: int | None) -> list[tuple[str, str, int]]:
    """The columns that fit: a wrapped right border breaks every line of the box."""
    available = (columns or _UNKNOWN_COLUMNS) - _HOST_MARGIN
    chosen = [c for c in _COLUMNS if with_project or c[0] != "project"]

    def total(layout: list[tuple[str, str, int]]) -> int:
        return sum(width + 3 for _, _, width in layout) + 1

    if total(chosen) > available:
        chosen = [(key, head, _TIGHTER.get(key, width)) for key, head, width in chosen]
    for key in _DROP_ORDER:
        if total(chosen) <= available:
            break
        chosen = [column for column in chosen if column[0] != key]
    return chosen


def _box_title(record: dict[str, Any]) -> str:
    """A part of a split file reads as such: "MEMORY.md 第7/8段"."""
    return re.sub(r" §(\d+/\d+)$", r" 第\1段", _title(record)).replace("§", "#")


def _box_table(
    hits: list[dict[str, Any]],
    states: dict[str, str],
    columns: int | None = None,
    query: str = "",
) -> list[str]:
    from .recall import coverage_key

    layout = _layout(any(record.get("project") for record in hits), columns)
    rows: list[list[str]] = []
    details: list[str] = []
    for rank, record in enumerate(hits, 1):
        product = source_product(str(record.get("product") or ""))
        role = str(record.get("role") or "")
        kind = {"navigation": "索引", "conversation_summary": "摘要"}.get(role) or _kind(
            record
        ).split("·")[0]
        values = {
            "rank": str(rank),
            "title": _plain(_box_title(record)),
            "kind": kind,
            "source": PRODUCTS.get(product, product),
            "author": _producer(record),
            "project": str(record.get("project") or "-"),
            "version": f"r{record.get('revision', '?')} {_plain(_updated(record))}",
            "routes": _plain(_routes(record)),
            "relevance": _plain(_similarity(record)),
            "usage": _plain(_usage(record)),
            "state": STATE_SHORT.get(states.get(coverage_key(record), ""), "-"),
        }
        rows.append([values[key] for key, _, _ in layout])
        summary = _plain(_summary(record, query))
        reason = _plain(_why(record, query))
        details.append(
            "\n".join(
                part
                for part in (
                    "内容：" + summary if summary else "",
                    "原因：" + reason if reason else "",
                )
                if part
            )
        )
    return _box([head for _, head, _ in layout], rows, [width for _, _, width in layout], details)
