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

import re
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
    re.compile(r"<agent[_-]brain\w*\b[^>]*>.*?(?:</agent[_-]brain\w*>|$)", re.S | re.I),
    re.compile(r"amh-native-recall:[0-9a-f]{24}"),
]
_NOISY_LINES = ("available mcp servers:", "available tools:", "current workspace may include")
_PASTED = re.compile(r"<pasted_content[^>]*>(.*?)</pasted_content[^>]*>", re.S | re.I)
_IMAGE = re.compile(r"\[(?:Image|Audio|Video|PDF|Document) #\d+\]", re.I)
_FILE_URI = re.compile(r"file://\S+", re.I)
# Acknowledgements and control words with no topic of their own. Compared
# after removing whitespace and punctuation and folding case.
_ACKS = frozenset(
    {
        "1",
        "2",
        "3",
        "y",
        "n",
        "ok",
        "okay",
        "yes",
        "no",
        "go",
        "sure",
        "thanks",
        "thx",
        "continue",
        "next",
        "done",
        "是",
        "否",
        "对",
        "好",
        "行",
        "嗯",
        "嗯嗯",
        "确认",
        "继续",
        "可以",
        "好的",
        "好了",
        "行吧",
        "收到",
        "明白",
        "了解",
        "谢谢",
        "感谢",
        "没问题",
        "继续吧",
        "开始吧",
        "下一步",
        "搞定了吗",
        "好了吗",
        "完成了吗",
        "做完了吗",
        "怎么样了",
        "结果呢",
        "然后呢",
        "还有吗",
        "同意",
        "批准",
        "可以的",
        "好的继续",
        "继续做",
        "接着做",
        "辛苦了",
    }
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


def admit(query: str) -> tuple[bool, str]:
    """Whether a normalized prompt can carry a topic worth searching for."""
    if not query.strip():
        return False, "empty"
    folded = _folded(query)
    if not folded:
        return False, "punctuation_only"
    if _CONTROL.match(query.strip()):
        return False, "control_command"
    if folded in _ACKS or folded.isdigit() and len(folded) <= 3:
        return False, "acknowledgement"
    return True, "topic"


SKIP_TEXT = {
    "empty": "空提示",
    "punctuation_only": "只有标点",
    "control_command": "宿主控制命令",
    "acknowledgement": "简短确认语，没有可检索的主题",
}
STATE_TEXT = {
    "packed": "已注入",
    "covered": "本会话已注入过",
    "budget": "超出注入预算",
    "empty": "无正文",
}
MODE_TEXT = {"hybrid": "混合（词法+语义）", "lexical": "仅词法"}
VIA_TEXT = {"service": "常驻服务（热连接）", "worker": "子进程", "disabled": "已关闭"}


def _cell(value: Any, limit: int) -> str:
    text = " ".join(str(value if value is not None else "").split()).replace("|", "/")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _producer(record: dict[str, Any]) -> str:
    name = record.get("producer_name")
    if name and not record.get("producer_unknown"):
        return str(name)
    uploader = record.get("uploader_name")
    return f"未知（{uploader}上传）" if uploader else "未知"


def _routes(record: dict[str, Any]) -> str:
    trace = record.get("recall")
    routes = trace.get("routes") if isinstance(trace, dict) else None
    ranks = routes if isinstance(routes, dict) else record.get("retrieval_ranks") or {}
    labels = (("lexical", "词法"), ("semantic", "语义"), ("hopfield", "联想"))
    parts = [f"{label}{ranks[key]}" for key, label in labels if isinstance(ranks.get(key), int)]
    return "·".join(parts) or "—"


def _similarity(record: dict[str, Any]) -> str:
    match = record.get("semantic_match") or {}
    value = match.get("similarity") if isinstance(match, dict) else None
    return f"{value:.2f}" if isinstance(value, (int, float)) else "—"


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
    count, index = record.get("part_count"), record.get("part_index")
    if isinstance(count, int) and count > 1 and isinstance(index, int):
        title += f" §{index + 1}/{count}"
    return title


def _kind(record: dict[str, Any]) -> str:
    kind = {"memory": "记忆", "rule": "规则", "skill": "技能", "transcript": "会话"}.get(
        str(record.get("kind")), str(record.get("kind") or "")
    )
    role = {"navigation": "索引", "topic": "主题", "conversation_summary": "摘要"}.get(
        str(record.get("role") or ""), ""
    )
    return kind + ("·" + role if role else "")


STATE_SHORT = {"packed": "已注入", "covered": "已注入过", "budget": "超预算", "empty": "无正文"}


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


def _box(headers: list[str], rows: list[list[str]], widths: list[int]) -> list[str]:
    def line(left: str, mid: str, right: str) -> str:
        return left + mid.join("─" * (w + 2) for w in widths) + right

    def cells(values: list[str]) -> str:
        return "│" + "│".join(" " + _fit(v, w) + " " for v, w in zip(values, widths)) + "│"

    out = [line("┌", "┬", "┐"), cells(headers), line("├", "┼", "┤")]
    out += [cells(row) for row in rows]
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
        reasons = [str(r) for r in retrieval.get("reasons") or []][:3]
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
        f"耗时 {elapsed_ms} ms / 预算 {round(budget * 1000)} ms · {VIA_TEXT.get(via, via)}",
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
    if not hits:
        return _as_plain("\n".join(lines)) if style == "box" else "\n".join(lines)
    if style == "box":
        return _as_plain("\n".join(lines)) + "\n" + "\n".join(_box_table(hits, states))
    lines += [
        "",
        "| # | 记忆 | 类型 | 来源 | 作者 | 项目 | 版本 | 更新 | 命中路由 | 相似度 | 本轮 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
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
                    _kind(record),
                    _cell(PRODUCTS.get(product, product), 14),
                    _cell(_producer(record), 16),
                    _cell(record.get("project") or "—", 16),
                    f"r{record.get('revision', '?')}",
                    _updated(record),
                    _routes(record),
                    _similarity(record),
                    STATE_TEXT.get(states.get(coverage_key(record), ""), "—"),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def render_skip(reason: str, query: str) -> str:
    return f"组织记忆召回 · 跳过：{SKIP_TEXT.get(reason, reason)}（「{_cell(query, 24)}」）"


def _as_plain(text: str) -> str:
    """Header lines without Markdown emphasis or code marks."""
    return text.replace("**", "").replace("`", "")


def _box_table(hits: list[dict[str, Any]], states: dict[str, str]) -> list[str]:
    from .recall import coverage_key

    with_project = any(record.get("project") for record in hits)
    headers = ["#", "记忆", "类型", "来源", "作者"]
    widths = [1, 22, 4, 11, 10]
    if with_project:
        headers.append("项目")
        widths.append(10)
    headers += ["版本 更新", "命中路由", "相似", "本轮"]
    widths += [9, 13, 4, 8]
    rows = []
    for rank, record in enumerate(hits, 1):
        product = source_product(str(record.get("product") or ""))
        role = str(record.get("role") or "")
        kind = {"navigation": "索引", "conversation_summary": "摘要"}.get(role) or _kind(
            record
        ).split("·")[0]
        producer = _producer(record)
        if producer.startswith("未知（") and producer.endswith("上传）"):
            producer = producer[3:-1]  # "敖丙上传": the uploader, the author unknown
        row = [
            str(rank),
            _plain(_title(record)).replace(" #", " "),
            kind,
            PRODUCTS.get(product, product),
            producer,
        ]
        if with_project:
            row.append(str(record.get("project") or "-"))
        row += [
            f"r{record.get('revision', '?')} {_plain(_updated(record))}",
            _plain(_routes(record)),
            _plain(_similarity(record)),
            STATE_SHORT.get(states.get(coverage_key(record), ""), "-"),
        ]
        rows.append(row)
    return _box(headers, rows, widths)
