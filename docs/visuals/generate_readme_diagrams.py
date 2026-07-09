from __future__ import annotations

import base64
from collections.abc import Callable
from math import ceil
from pathlib import Path
from xml.sax.saxutils import escape


OUT = Path(__file__).resolve().parent

FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", Arial, "PingFang SC", "Microsoft YaHei", sans-serif'
MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace'

PALETTE = {
    "green": ("#edf9ef", "#2f7d32", "#a8d8b5"),
    "blue": ("#edf6ff", "#0969da", "#a8cff2"),
    "red": ("#fff1eb", "#d1242f", "#efbda9"),
    "purple": ("#f5efff", "#8250df", "#cfb9f3"),
    "gray": ("#f6f8fa", "#57606a", "#c9d1d9"),
    "yellow": ("#fff7cc", "#8a6200", "#e7c34e"),
    "teal": ("#e7fbf8", "#1f6f78", "#95dcd6"),
    "orange": ("#fff1df", "#bc6c00", "#efb770"),
    "indigo": ("#f1efff", "#6639ba", "#c0b0ee"),
    "pink": ("#fff0f6", "#bf3989", "#efb4d7"),
}

COLORS = list(PALETTE)


def data_uri_for_svg(path: str) -> str:
    raw = (OUT / path).read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def write_svg(name: str, title: str, desc: str, body: str, width: int = 1280, height: int = 760) -> None:
    qoder_background = name.endswith(".svg")
    qoder_defs = ""
    if qoder_background:
        qoder_defs = """
    <linearGradient id="qoderPageBg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#fffdfb"/>
      <stop offset="48%" stop-color="#fbfaf8"/>
      <stop offset="100%" stop-color="#f7fbff"/>
    </linearGradient>
    <linearGradient id="qoderSheetBg" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#fff3fa"/>
      <stop offset="45%" stop-color="#fbfdff"/>
      <stop offset="100%" stop-color="#f0f8e9"/>
    </linearGradient>
    <filter id="qoderSheetShadow" x="-8%" y="-8%" width="116%" height="116%">
      <feDropShadow dx="0" dy="26" stdDeviation="30" flood-color="#2a231c" flood-opacity="0.085"/>
    </filter>
"""
    bg_fill = "url(#qoderPageBg)" if qoder_background else "#fbfaf7"
    sheet_fill = "url(#qoderSheetBg)" if qoder_background else "#fffdf8"
    sheet_filter = "url(#qoderSheetShadow)" if qoder_background else "url(#paper)"
    sheet_rx = 34 if qoder_background else 28
    sheet_extra = ""
    if qoder_background:
        sheet_extra = (
            f'  <rect x="28" y="28" width="{width - 56}" height="{height - 56}" rx="36" '
            'fill="#ffffff" opacity="0.62"/>\n'
        )
    sheet_stroke = ' stroke="#ffffff" stroke-opacity="0.86" stroke-width="1.2"' if qoder_background else ""
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc" style="max-width:100%;height:auto;">
  <title id="title">{escape(title)}</title>
  <desc id="desc">{escape(desc)}</desc>
  <defs>
{qoder_defs}
    <marker id="arrow" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
      <path d="M0 0 L10 5 L0 10 z" fill="#57606a"/>
    </marker>
    <filter id="paper" x="-8%" y="-8%" width="116%" height="116%">
      <feTurbulence type="fractalNoise" baseFrequency="0.78" numOctaves="2" seed="11" result="noise"/>
      <feColorMatrix type="saturate" values="0"/>
      <feComponentTransfer>
        <feFuncA type="table" tableValues="0 0.035"/>
      </feComponentTransfer>
      <feBlend in="SourceGraphic" mode="multiply"/>
    </filter>
    <filter id="softShadow" x="-15%" y="-15%" width="130%" height="130%">
      <feDropShadow dx="0" dy="8" stdDeviation="12" flood-color="#1f2328" flood-opacity="0.08"/>
    </filter>
    <style>
      .bg {{ fill: {bg_fill}; }}
      .sheet {{ fill: {sheet_fill}; filter: {sheet_filter}; }}
      .ink {{ fill: #24292f; }}
      .muted {{ fill: #57606a; }}
      .title {{ font-family: {FONT}; font-size: 34px; font-weight: 850; fill: #24292f; }}
      .subtitle {{ font-family: {FONT}; font-size: 17px; fill: #57606a; }}
      .section {{ font-family: {FONT}; font-size: 17px; font-weight: 800; fill: #57606a; text-transform: uppercase; }}
      .card-title {{ font-family: {FONT}; font-size: 20px; font-weight: 820; fill: #24292f; }}
      .card-line {{ font-family: {FONT}; font-size: 15px; fill: #24292f; }}
      .small {{ font-family: {FONT}; font-size: 13px; fill: #57606a; }}
      .tiny {{ font-family: {FONT}; font-size: 10px; fill: #6e7781; }}
      .num {{ font-family: {FONT}; font-size: 22px; font-weight: 900; }}
      .mono {{ font-family: {MONO}; font-size: 13px; fill: #24292f; }}
      .mono-small {{ font-family: {MONO}; font-size: 12px; fill: #57606a; }}
      .panel {{ fill: #ffffff; stroke: #d8dee4; stroke-width: 1.4; filter: url(#softShadow); }}
      .card {{ stroke-width: 1.8; filter: url(#softShadow); }}
      .thin-card {{ stroke-width: 1.2; }}
      .arrow {{ fill: none; stroke: #57606a; stroke-width: 2.1; stroke-linecap: round; stroke-linejoin: round; marker-end: url(#arrow); }}
      .soft-arrow {{ fill: none; stroke: #8c959f; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; stroke-dasharray: 6 7; marker-end: url(#arrow); }}
      .flow-line {{ stroke-dasharray: 14 10; animation: flowDash 1.8s linear infinite; }}
      .planned-line {{ stroke-dasharray: 8 8; }}
      .flow-dot {{ fill: #1f6f78; filter: url(#softShadow); }}
      .breath {{ animation: breath 2.8s ease-in-out infinite; transform-box: fill-box; transform-origin: center; }}
      .breath-slow {{ animation: breath 4.2s ease-in-out infinite; transform-box: fill-box; transform-origin: center; }}
      .blink {{ animation: blink 1.7s ease-in-out infinite; }}
      .rail {{ fill: none; stroke: #d0d7de; stroke-width: 1.3; stroke-dasharray: 5 8; }}
      .branch {{ fill: none; stroke: #d0d7de; stroke-width: 1.3; stroke-linecap: round; stroke-linejoin: round; }}
      .icon {{ fill: none; stroke-width: 3.5; stroke-linecap: round; stroke-linejoin: round; }}
      @keyframes flowDash {{
        to {{ stroke-dashoffset: -24; }}
      }}
      @keyframes breath {{
        0%, 100% {{ opacity: 0.88; transform: scale(1); }}
        50% {{ opacity: 1; transform: scale(1.035); }}
      }}
      @keyframes blink {{
        0%, 100% {{ opacity: 0.42; }}
        50% {{ opacity: 1; }}
      }}
    </style>
  </defs>
  <rect class="bg" width="{width}" height="{height}"/>
{sheet_extra}  <rect class="sheet" x="20" y="20" width="{width - 40}" height="{height - 40}" rx="{sheet_rx}"{sheet_stroke}/>
{body}
</svg>
"""
    (OUT / name).write_text(svg, encoding="utf-8")


def t(x: int, y: int, value: str, cls: str = "card-line", fill: str | None = None) -> str:
    fill_attr = f' fill="{fill}"' if fill else ""
    return f'  <text class="{cls}" x="{x}" y="{y}"{fill_attr}>{escape(value)}</text>'


def html_inline(value: str) -> str:
    parts = value.split("`")
    rendered: list[str] = []
    for index, part in enumerate(parts):
        escaped = escape(part)
        if index % 2:
            rendered.append(f"<code>{escaped}</code>")
        else:
            rendered.append(escaped)
    return "".join(rendered)


def lines(x: int, y: int, values: list[str], cls: str = "card-line", gap: int = 20) -> str:
    return "\n".join(t(x, y + i * gap, value, cls) for i, value in enumerate(values))


def text_units(value: str) -> float:
    total = 0.0
    for ch in value:
        if ch.isspace():
            total += 0.35
        elif ord(ch) < 128:
            total += 0.56
        else:
            total += 1.0
    return total


def estimated_text_px(value: str, *, font_size: int = 13, mono: bool = False) -> int:
    total = 0.0
    for ch in value:
        code = ord(ch)
        if ch.isspace():
            total += font_size * 0.34
        elif mono and code < 128:
            total += font_size * 0.64
        elif code < 128:
            total += font_size * 0.58
        else:
            total += font_size * 1.08
    return ceil(total)


def chip_width(text: str, *, min_width: int = 76, padding: int = 44, font_size: int = 13) -> int:
    return max(min_width, estimated_text_px(text, font_size=font_size) + padding)


def wrap_text(value: str, max_units: float) -> list[str]:
    if text_units(value) <= max_units:
        return [value]
    wrapped: list[str] = []
    line = ""
    line_units = 0.0
    break_chars = set(" /-_，、；;：:,|")
    last_break_at: int | None = None
    for ch in value:
        unit = text_units(ch)
        if line and line_units + unit > max_units:
            if last_break_at and last_break_at >= max(2, len(line) // 2):
                wrapped.append(line[:last_break_at].rstrip())
                carry = line[last_break_at:].lstrip()
                line = carry + ch
                line_units = text_units(line)
            else:
                wrapped.append(line.rstrip())
                line = ch.lstrip()
                line_units = text_units(line)
            last_break_at = None
            for idx, existing in enumerate(line):
                if existing in break_chars:
                    last_break_at = idx + 1
            continue
        line += ch
        line_units += unit
        if ch in break_chars:
            last_break_at = len(line)
    if line.strip():
        wrapped.append(line.rstrip())
    return wrapped


def wrapped_lines(
    x: int,
    y: int,
    values: list[str],
    cls: str = "card-line",
    gap: int = 20,
    *,
    max_units: float = 22,
    max_total_lines: int | None = None,
) -> str:
    out: list[str] = []
    line_no = 0
    for value in values:
        for wrapped in wrap_text(value, max_units):
            if max_total_lines is not None and line_no >= max_total_lines:
                return "\n".join(out)
            out.append(t(x, y + line_no * gap, wrapped, cls))
            line_no += 1
    return "\n".join(out)


def curved_arrow(x1: int, y1: int, x2: int, y2: int, soft: bool = False) -> str:
    cls = "soft-arrow" if soft else "arrow"
    dx = abs(x2 - x1)
    c1 = x1 + max(40, dx // 2)
    c2 = x2 - max(40, dx // 2)
    return f'  <path class="{cls}" d="M{x1} {y1} C {c1} {y1}, {c2} {y2}, {x2} {y2}"/>'


def straight_arrow(x1: int, y1: int, x2: int, y2: int, soft: bool = False) -> str:
    cls = "soft-arrow" if soft else "arrow"
    return f'  <path class="{cls}" d="M{x1} {y1} L{x2} {y2}"/>'


def flow_arrow(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    soft: bool = False,
    planned: bool = False,
    curve: bool = True,
) -> str:
    cls = "soft-arrow" if soft or planned else "arrow"
    extra = " flow-line"
    if planned:
        extra += " planned-line"
    if curve:
        dx = abs(x2 - x1)
        c1 = x1 + max(40, dx // 2)
        c2 = x2 - max(40, dx // 2)
        d = f"M{x1} {y1} C {c1} {y1}, {c2} {y2}, {x2} {y2}"
    else:
        d = f"M{x1} {y1} L{x2} {y2}"
    duration = "2.2s" if planned else "1.8s"
    return f"""  <path class="{cls}{extra}" d="{d}">
    <animate attributeName="stroke-dashoffset" values="0;-24" dur="{duration}" repeatCount="indefinite"/>
  </path>"""


def status_chip(x: int, y: int, text: str, color: str, *, dashed: bool = False) -> str:
    fill, ink, stroke = PALETTE[color]
    dash = ' stroke-dasharray="5 5"' if dashed else ""
    width = chip_width(text, min_width=88, padding=52)
    return f"""  <g>
    <rect x="{x}" y="{y}" width="{width}" height="28" rx="14" fill="{fill}" stroke="{stroke}" stroke-width="1.1"{dash}/>
    <circle class="blink" cx="{x + 15}" cy="{y + 14}" r="4" fill="{ink}"/>
    <text class="small" x="{x + 27}" y="{y + 18}" fill="{ink}">{escape(text)}</text>
  </g>"""


def boundary_card(
    x: int,
    y: int,
    w: int,
    h: int,
    title: str,
    detail: list[str],
    color: str,
    icon_name: str,
    *,
    dashed: bool = False,
    badge: str | None = None,
) -> str:
    fill, ink, stroke = PALETTE[color]
    dash = ' stroke-dasharray="8 8"' if dashed else ""
    badge_text = ""
    title_x = x + 84
    if badge:
        badge_w = chip_width(badge, min_width=76, padding=38)
        badge_x = x + w - badge_w - 16
        badge_y = y + h - 42
        badge_text = status_chip(badge_x, badge_y, badge, color, dashed=dashed)
    title_width = x + w - title_x - 18
    title_units = max(3.0, title_width / 19)
    title_lines = wrap_text(title, title_units)
    detail_y = y + 37 + len(title_lines) * 23
    detail_units = max(7.0, (x + w - title_x - 20) / 15)
    bottom_reserved = 50 if badge else 14
    max_detail_lines = max(1, (y + h - detail_y - bottom_reserved) // 19)
    duration = "4.2s" if dashed else "2.8s"
    return f"""  <g class="{'breath-slow' if dashed else 'breath'}">
    <animate attributeName="opacity" values="0.92;1;0.92" dur="{duration}" repeatCount="indefinite"/>
    <rect class="card" x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{fill}" stroke="{stroke}" stroke-width="1.8"{dash}/>
    <circle cx="{x + 44}" cy="{y + 44}" r="27" fill="#ffffff" opacity="0.62"/>
    {icon(icon_name, x + 21, y + 21, ink)}
{lines(title_x, y + 34, title_lines, "card-title", 23)}
{wrapped_lines(title_x, detail_y, detail, "card-line", 19, max_units=detail_units, max_total_lines=max_detail_lines)}
{badge_text}
  </g>"""


def pill(x: int, y: int, w: int, h: int, text: str, color: str) -> str:
    fill, ink, stroke = PALETTE[color]
    width = max(w, chip_width(text, min_width=w, padding=32))
    return f"""  <g>
    <rect x="{x}" y="{y}" width="{width}" height="{h}" rx="{h // 2}" fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>
    <text class="small" x="{x + 14}" y="{y + h // 2 + 4}" fill="{ink}">{escape(text)}</text>
  </g>"""


def icon(kind: str, x: int, y: int, color: str, scale: int = 1) -> str:
    size = 46 * scale
    cx = x + size // 2
    cy = y + size // 2
    if kind == "install":
        return f'<path class="icon" stroke="{color}" d="M{x+7} {cy}h{size-14}M{cx} {y+7}v{size-14}M{cx} {y+7}l-12 12M{cx} {y+7}l12 12"/>'
    if kind == "memory":
        return f'<path class="icon" stroke="{color}" d="M{x+8} {y+14}c0-9 {size-16}-9 {size-16} 0v20c0 9-{size-16} 9-{size-16} 0zM{x+8} {y+14}c0 9 {size-16} 9 {size-16} 0M{x+8} {y+27}c0 9 {size-16} 9 {size-16} 0"/>'
    if kind == "recall":
        return f'<path class="icon" stroke="{color}" d="M{x+8} {y+10}h15v15H{x+8}zM{x+28} {y+10}h15v15H{x+28}zM{x+15} {y+25}v12h20M{x+35} {y+37}h9"/>'
    if kind == "loading":
        return f'<path class="icon" stroke="{color}" d="M{x+8} {y+10}h28v18H{x+8}zM{x+14} {y+37}h26M{x+28} {y+28}v9M{x+17} {y+16}h15M{x+17} {y+22}h9"/>'
    if kind == "adapter":
        return f'<path class="icon" stroke="{color}" d="M{x+13} {y+12}v10M{x+33} {y+12}v10M{x+10} {y+22}h26v8c0 8-5 14-13 14s-13-6-13-14zM{x+23} {y+44}v-9M{x+36} {y+30}h8"/>'
    if kind == "govern":
        return f'<path class="icon" stroke="{color}" d="M{cx} {y+7}l17 7v11c0 13-7 22-17 27-10-5-17-14-17-27V{y+14}zM{x+15} {y+29}l7 7 13-15"/>'
    if kind == "observe":
        return f'<path class="icon" stroke="{color}" d="M{x+8} {y+12}h34v24H{x+8}zM{x+17} {y+44}h16M{x+25} {y+36}v8M{x+14} {y+25}h7l4-7 5 13 4-6h6"/>'
    if kind == "evidence":
        return f'<path class="icon" stroke="{color}" d="M{x+11} {y+8}h18l8 8v17H{x+11}zM{x+29} {y+8}v10h8M{x+17} {y+22}h12M{x+28} {y+36}a8 8 0 1 0 0.1 0M{x+34} {y+42}l8 8"/>'
    if kind == "docs":
        return f'<path class="icon" stroke="{color}" d="M{x+13} {y+7}h21l9 9v30H{x+13}zM{x+34} {y+7}v11h9M{x+19} {y+28}h17M{x+19} {y+36}h17"/>'
    if kind == "result":
        return f'<path class="icon" stroke="{color}" d="M{x+11} {y+13}l10 10 20-18M{x+12} {y+33}h28M{x+12} {y+41}h18"/>'
    if kind == "store":
        return f'<path class="icon" stroke="{color}" d="M{x+9} {y+12}h31v27H{x+9}zM{x+15} {y+18}h19M{x+15} {y+26}h19M{x+15} {y+34}h12"/>'
    if kind == "agent":
        return f'<path class="icon" stroke="{color}" d="M{x+10} {y+22}c0-10 28-10 28 0v11c0 10-28 10-28 0zM{x+18} {y+21}h1M{x+30} {y+21}h1M{x+21} {y+32}h7M{x+24} {y+8}v7"/>'
    return f'<circle cx="{cx}" cy="{cy}" r="{size//3}" fill="none" stroke="{color}" stroke-width="3.5"/>'


def module_card(
    x: int,
    y: int,
    w: int,
    h: int,
    number: str,
    title: str,
    detail: list[str],
    color: str,
    icon_name: str,
) -> str:
    fill, ink, stroke = PALETTE[color]
    return f"""  <g>
    <rect class="card" x="{x + 4}" y="{y + 5}" width="{w}" height="{h}" rx="18" fill="#ffffff" stroke="#d8dee4" opacity="0.55"/>
    <rect class="card" x="{x}" y="{y}" width="{w}" height="{h}" rx="18" fill="{fill}" stroke="{stroke}"/>
    <circle cx="{x + 54}" cy="{y + h // 2}" r="31" fill="#ffffff" opacity="0.58"/>
    {icon(icon_name, x + 31, y + h // 2 - 23, ink)}
    <text class="num" x="{x + 104}" y="{y + 34}" fill="{ink}">{escape(number)}</text>
    <text class="card-title" x="{x + 154}" y="{y + 32}">{escape(title)}</text>
{lines(x + 104, y + 58, detail, "card-line", 18)}
  </g>"""


def compact_card(x: int, y: int, w: int, h: int, title: str, detail: list[str], color: str, icon_name: str) -> str:
    fill, ink, stroke = PALETTE[color]
    title_x = x + 78
    title_lines = wrap_text(title, max(5.0, (x + w - title_x - 16) / 17))
    detail_y = y + 38 + len(title_lines) * 23
    detail_units = max(7.0, (x + w - title_x - 18) / 15)
    max_detail_lines = max(1, (y + h - detail_y - 12) // 19)
    return f"""  <g>
    <rect class="card" x="{x}" y="{y}" width="{w}" height="{h}" rx="18" fill="{fill}" stroke="{stroke}"/>
    <circle cx="{x + 42}" cy="{y + 42}" r="26" fill="#ffffff" opacity="0.58"/>
    {icon(icon_name, x + 19, y + 19, ink)}
{lines(title_x, y + 34, title_lines, "card-title", 23)}
{wrapped_lines(title_x, detail_y, detail, "card-line", 19, max_units=detail_units, max_total_lines=max_detail_lines)}
  </g>"""


def band(x: int, y: int, w: int, h: int, title: str, detail: list[str], color: str) -> str:
    fill, ink, stroke = PALETTE[color]
    detail_units = max(12.0, (w - 48) / 14)
    max_detail_lines = max(1, (h - 52) // 19)
    return f"""  <g>
    <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>
    <text class="card-title" x="{x + 24}" y="{y + 34}" fill="{ink}">{escape(title)}</text>
{wrapped_lines(x + 24, y + 62, detail, "card-line", 19, max_units=detail_units, max_total_lines=max_detail_lines)}
  </g>"""


def columns_band(x: int, y: int, w: int, h: int, title: str, detail: list[str], color: str, cols: int = 2) -> str:
    fill, ink, stroke = PALETTE[color]
    col_w = (w - 48) // cols
    rows = (len(detail) + cols - 1) // cols
    parts = [
        '  <g>',
        f'    <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>',
        f'    <text class="card-title" x="{x + 24}" y="{y + 34}" fill="{ink}">{escape(title)}</text>',
    ]
    for idx, item in enumerate(detail):
        col = idx // rows
        row = idx % rows
        tx = x + 24 + col * col_w
        ty = y + 62 + row * 19
        parts.append(t(tx, ty, f"- {item}", "small"))
    parts.append("  </g>")
    return "\n".join(parts)


def lane_card(x: int, y: int, w: int, h: int, title: str, detail: list[str], color: str) -> str:
    fill, ink, stroke = PALETTE[color]
    title_lines = wrap_text(title, max(5.0, (w - 32) / 17))
    detail_y = y + 34 + len(title_lines) * 22
    detail_units = max(8.0, (w - 32) / 14)
    max_detail_lines = max(1, (y + h - detail_y - 12) // 18)
    return f"""  <g>
    <rect class="card" x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{fill}" stroke="{stroke}"/>
{lines(x + 16, y + 32, title_lines, "card-title", 22)}
{wrapped_lines(x + 16, detail_y, detail, "card-line", 18, max_units=detail_units, max_total_lines=max_detail_lines)}
  </g>"""


def header(title: str, desc: str) -> list[str]:
    return [t(70, 76, title, "title"), t(72, 105, desc, "subtitle")]


def structure_map(zh: bool) -> tuple[str, str, str, int, int]:
    if zh:
        title = "可信上下文操作系统能力地图"
        desc = "左侧是真实源码 ownership，右侧是 capture → write → project → retrieve → pack → firewall → govern → verify → adapt 的能力链路。"
        root = "agent-memory-hub/"
        modules = [
            ("01", "事实面", "gray", "observe", ["MCP 28 tools、Web 91 locked routes。", "16 adapters：11 verified / 4 install-ready / 1 wip。"]),
            ("02", "证据采集", "teal", "evidence", ["原始对话、资源和抽取先进入 evidence。", "记录 path / offset / hash / tier。"]),
            ("03", "写入漏斗", "blue", "memory", ["核心写入口经 WriteService。", "bulk import / 治理内部写入仍需收敛。"]),
            ("04", "事实源", "green", "store", ["items/mem-*.md 是 source of truth。", "resources/extractions 是 evidence sidecar。"]),
            ("05", "索引投影", "green", "store", ["index.db 投影 meta / FTS / vec / graph。", "派生层可重建，不当权威事实源。"]),
            ("06", "召回解释", "orange", "recall", ["BM25 + vector -> RRF + trace。", "decay / feedback / freshness 控权。"]),
            ("07", "上下文经济", "purple", "loading", ["可逆 context_pack + detail_uri。", "Headroom 式压缩要过 benchmark gate。"]),
            ("08", "防火墙治理", "yellow", "govern", ["范围、过期、敏感、低证据先过滤。", "drift / maturity / review / evolve 闭环。"]),
            ("09", "Loop Contract 治理", "red", "observe", ["goal / verifier / human gate 契约。", "质量门禁不直接改默认链路。"]),
            ("10", "多智能体复用", "indigo", "adapter", ["hooks / MCP / file / provider 分层。", "verified 必须有真实 runtime evidence。"]),
        ]
        tree_entries = [
            ("README.zh.md + source/test evidence/", "事实声明", ["28 MCP / 91 Web baseline / 16 adapters", "tests locked / verified=11 local"]),
            ("agent_brain/memory/evidence/", "采集与证据", ["conversation_store.py / harvest/", "resource_store.py / import_service.py"]),
            ("agent_brain/memory/store/", "记忆写入", ["write_service.py / items_store.py", "item_markdown.py / quality.py"]),
            ("~/.agent-memory-hub/", "本地事实源", ["items/mem-*.md", "resources/*.json / extractions/*.json"]),
            ("agent_brain/platform/indexing/", "索引投影", ["index.db / sqlite_vec.py", "FTS / refs_graph / pending repair"]),
            ("agent_brain/memory/recall/", "召回排序", ["retrieval.py / retrieval_fusion.py", "retrieval_mmr.py / retrieval_value.py"]),
            ("agent_brain/memory/context/", "上下文经济", ["context_packing.py / adaptive_compression.py", "context_firewall.py / injection_feedback.py"]),
            ("agent_brain/memory/governance/", "记忆治理", ["audit/ / governance/ / evolve/", "review_queue.py / outcome_feedback.py"]),
            ("agent_brain/memory/loops/ + evaluation/", "Loop Contract 治理", ["loop_contract.py / loop_store.py", "retrieval/compression/release gates"]),
            ("agent_runtime_kit/ + agent_brain/agent_integrations/", "多智能体适配", ["hooks/ / mcp/ / provider tools", "codex / claude / qoder / open*"]),
        ]
    else:
        title = "Trusted Context OS Capability Map"
        desc = "The repo tree is organized as a closed loop: capture → write → project → retrieve → pack → firewall → govern → verify → adapt."
        root = "agent-memory-hub/"
        modules = [
            ("01", "Fact surface", "gray", "observe", ["MCP 28 tools, Web 91 locked routes.", "16 adapters: 11 verified / 4 install-ready / 1 wip."]),
            ("02", "Evidence capture", "teal", "evidence", ["Raw conversations, resources, and extractions land as evidence.", "Keep path / offset / hash / tier."]),
            ("03", "Write funnel", "blue", "memory", ["External writes go through WriteService.", "Bulk import/internal governance paths still need convergence."]),
            ("04", "Truth source", "green", "store", ["items/mem-*.md is source of truth.", "resources/extractions are evidence sidecars."]),
            ("05", "Index projection", "green", "store", ["index.db projects meta / FTS / vec / graph.", "Derived layer rebuilds from Markdown."]),
            ("06", "Explainable recall", "orange", "recall", ["BM25 + vector -> RRF + trace.", "Decay / feedback / freshness shape rank."]),
            ("07", "Context economy", "purple", "loading", ["Reversible context_pack + detail_uri.", "Headroom-style compression is gate-checked."]),
            ("08", "Firewall governance", "yellow", "govern", ["Scope, stale, sensitivity, low evidence get filtered.", "drift / maturity / review / evolve loop."]),
            ("09", "Loop Contract governance", "red", "observe", ["goal / verifier / human gate contract.", "Quality gates cannot change defaults directly."]),
            ("10", "Agent reuse", "indigo", "adapter", ["hooks / MCP / file / provider are separate.", "verified requires runtime evidence."]),
        ]
        tree_entries = [
            ("README.zh.md + source/test evidence/", "Truth claims", ["28 MCP / 91 Web baseline / 16 adapters", "tests locked / verified=11 local"]),
            ("agent_brain/memory/evidence/", "Ingestion and evidence", ["conversation_store.py / harvest/", "resource_store.py / import_service.py"]),
            ("agent_brain/memory/store/", "Memory write", ["write_service.py / items_store.py", "item_markdown.py / quality.py"]),
            ("~/.agent-memory-hub/", "Local truth", ["items/mem-*.md", "resources/*.json / extractions/*.json"]),
            ("agent_brain/platform/indexing/", "Index projection", ["index.db / sqlite_vec.py", "FTS / refs_graph / pending repair"]),
            ("agent_brain/memory/recall/", "Retrieval rank", ["retrieval.py / retrieval_fusion.py", "retrieval_mmr.py / retrieval_value.py"]),
            ("agent_brain/memory/context/", "Context economy", ["context_packing.py / adaptive_compression.py", "context_firewall.py / injection_feedback.py"]),
            ("agent_brain/memory/governance/", "Memory governance", ["audit/ / governance/ / evolve/", "review_queue.py / outcome_feedback.py"]),
            ("agent_brain/memory/loops/ + evaluation/", "Loop Contract governance", ["loop_contract.py / loop_store.py", "retrieval/compression/release gates"]),
            ("agent_runtime_kit/ + agent_brain/agent_integrations/", "Agent reuse", ["hooks/ / mcp/ / provider tools", "codex / claude / qoder / open*"]),
        ]

    body = header(title, desc)
    left_x, left_y, left_w, left_h = 54, 132, 620, 948
    right_x, card_w = 760, 640
    body.append(f'  <rect class="panel" x="{left_x}" y="{left_y}" width="{left_w}" height="{left_h}" rx="24"/>')
    body.append(t(90, 178, root, "title"))
    body.append(t(92, 206, "capability-aligned engineering tree" if not zh else "按能力拆分的工程目录层级", "subtitle"))
    body.append(t(right_x, 176, "capability modules" if not zh else "能力模块", "title"))
    body.append(t(right_x + 2, 204, "goals and deliverables for each layer" if not zh else "每个能力层的目标、任务与交付物", "subtitle"))

    def tree_icon(row_type: str, x: int, y: int, color: str) -> str:
        fill, ink, _stroke = PALETTE[color]
        if row_type == "folder":
            return f'<path fill="{fill}" stroke="{ink}" stroke-width="1.25" d="M{x} {y-8}h10l4 4h17a3 3 0 0 1 3 3v13a3 3 0 0 1-3 3H{x+3}a3 3 0 0 1-3-3z"/>'
        return f'<path fill="#ffffff" stroke="{ink}" stroke-width="1.25" d="M{x+5} {y-10}h10l5 5v19H{x+5}zM{x+15} {y-10}v6h6"/>'

    number_color = {number: color for number, _label, color, _icon_name, _detail in modules}

    def number_badge(x: int, y: int, number: str) -> str:
        color = number_color[number]
        fill, ink, stroke = PALETTE[color]
        return f"""  <g>
    <circle cx="{x}" cy="{y}" r="11" fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>
    <text class="tiny" x="{x - 6}" y="{y + 4}" fill="{ink}">{escape(number)}</text>
  </g>"""

    def connector(x1: int, y1: int, x2: int, y2: int) -> str:
        dx = abs(x2 - x1)
        c1 = x1 + max(30, dx // 2)
        c2 = x2 - max(30, dx // 2)
        return f'  <path fill="none" stroke="#8c959f" stroke-width="1.25" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="5 8" opacity="0.66" d="M{x1} {y1} C {c1} {y1}, {c2} {y2}, {x2} {y2}"/>'

    def capability_card(
        x: int,
        y: int,
        number: str,
        label_text: str,
        color: str,
        icon_name: str,
        detail: list[str],
    ) -> str:
        fill, ink, stroke = PALETTE[color]
        small_icon = f'<g transform="translate({x + 28},{y + 24}) scale(0.5)">{icon(icon_name, 0, 0, ink)}</g>'
        return f"""  <g>
    <rect x="{x + 4}" y="{y + 5}" width="{card_w}" height="78" rx="16" fill="#ffffff" stroke="#d8dee4" stroke-width="1.1" opacity="0.45"/>
    <rect x="{x}" y="{y}" width="{card_w}" height="78" rx="16" fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>
    <circle cx="{x + 50}" cy="{y + 39}" r="23" fill="#ffffff" opacity="0.65"/>
    {small_icon}
    <text class="num" x="{x + 96}" y="{y + 33}" fill="{ink}">{escape(number)}</text>
    <text class="card-title" x="{x + 142}" y="{y + 32}">{escape(label_text)}</text>
{lines(x + 96, y + 56, detail, "card-line", 17)}
  </g>"""

    tree_top = 230
    group_gap = 80
    trunk_x = 104
    icon_x = 122
    badge_x = left_x + left_w - 38
    card_centers: dict[str, int] = {}
    anchors: list[tuple[str, int]] = []
    body.append(f'  <path class="branch" d="M{trunk_x} {tree_top - 10} V{tree_top + (len(tree_entries) - 1) * group_gap + 45}"/>')
    for idx, (folder_name, folder_label, children) in enumerate(tree_entries):
        number, _label_text, color, _icon_name, _detail = modules[idx]
        y = tree_top + idx * group_gap
        body.append(f'  <path class="branch" d="M{trunk_x} {y} H{icon_x - 8}"/>')
        body.append(f'  {tree_icon("folder", icon_x, y, color)}')
        body.append(t(icon_x + 42, y + 4, f"{folder_name} {folder_label}", "mono"))
        for child_idx, child in enumerate(children[:2]):
            child_y = y + 24 + child_idx * 18
            body.append(f'  <path class="branch" d="M{icon_x + 12} {child_y - 9} V{child_y} H{icon_x + 43}"/>')
            body.append(f'  {tree_icon("file", icon_x + 46, child_y, color)}')
            body.append(t(icon_x + 76, child_y + 4, child, "mono-small"))
        center_y = y + 18
        anchors.append((number, center_y))
        body.append(number_badge(badge_x, center_y, number))

    for idx, (number, label_text, color, icon_name, detail) in enumerate(modules):
        card_y = 226 + idx * 82
        card_centers[number] = card_y + 39

    for idx, (number, label_text, color, icon_name, detail) in enumerate(modules):
        card_y = 226 + idx * 82
        body.append(capability_card(right_x, card_y, number, label_text, color, icon_name, detail))

    line_parts: list[str] = []
    for number, y in anchors:
        if number in card_centers:
            line_parts.append(connector(badge_x + 13, y, right_x - 14, card_centers[number]))
    body[6:6] = line_parts

    filename = "readme-structure-map.zh.svg" if zh else "readme-structure-map.svg"
    return filename, title, "\n".join(body), 1460, 1120


def product_architecture(zh: bool) -> tuple[str, str, str, int, int]:
    if zh:
        title = "产品架构图：从协作痛点到可信上下文"
        desc = "从多智能体协作中的重复说明、上下文断裂和过期事实误导开始，落到证据、记忆、召回、注入、复核和治理。"
        actors = ("多智能体协作入口", ["Codex / Claude / Qoder", "Wukong / Hermes / Open*", "CLI / MCP / hooks / Web"], "blue", "adapter")
        problem = ("上下文断裂", ["任务换 Agent 会丢", "原始 transcript 太长", "过期事实会误导"], "red", "observe")
        product_loop = [
            ("捕获证据", ["提示与对话", "资源与抽取"], "teal", "evidence"),
            ("提炼记忆", ["MemoryItem", "frontmatter + body"], "green", "memory"),
            ("可信召回", ["BM25 / 向量 / RRF", "trace 可解释"], "orange", "recall"),
            ("分层注入", ["locator / overview / detail", "firewall + context_pack"], "purple", "loading"),
        ]
        governance = [
            ("反馈记录", ["adopted / rejected", "ignored 不升温"], "yellow", "govern"),
            ("验证门禁", ["benchmark / truth contract", "adapter verified evidence"], "pink", "observe"),
        ]
        outcomes = [
            ("少重复交代", ["下一次 Agent 接得住"], "green", "result"),
            ("可审计复盘", ["证据、轨迹、来源可查"], "blue", "docs"),
            ("Loop 门禁", ["verifier ready", "human gate 生命周期"], "orange", "observe"),
        ]
        rule = "产品边界：AMH 不是聊天记录仓库；human gate 生命周期只记录门禁事实，不替用户批准；只有经过写入漏斗的 MemoryItem 才是长期知识。"
    else:
        title = "Product Architecture: From Collaboration Pain To Trusted Context"
        desc = "The product promise: agents stop losing task context; important facts can be captured, retrieved, injected, reviewed, and governed."
        actors = ("Multi-agent entry", ["Codex / Claude / Qoder", "Wukong / Hermes / Open*", "CLI / MCP / hooks / Web"], "blue", "adapter")
        problem = ("Context fracture", ["agent switches lose task state", "raw transcript is too large", "stale facts mislead"], "red", "observe")
        product_loop = [
            ("Capture evidence", ["prompt / transcript", "resource / extraction"], "teal", "evidence"),
            ("Distill memory", ["MemoryItem", "frontmatter + body"], "green", "memory"),
            ("Trusted recall", ["BM25 / vector / RRF", "explainable trace"], "orange", "recall"),
            ("Layered inject", ["locator / overview / detail", "firewall + context_pack"], "purple", "loading"),
        ]
        governance = [
            ("Feedback loop", ["adopted / rejected", "ignored does not heat up"], "yellow", "govern"),
            ("Verification gate", ["benchmark / truth contract", "adapter verified evidence"], "pink", "observe"),
        ]
        outcomes = [
            ("Less repetition", ["the next agent can continue"], "green", "result"),
            ("Auditable replay", ["evidence, trace, source"], "blue", "docs"),
            ("Loop gates", ["verifier ready", "human gate lifecycle"], "orange", "observe"),
        ]
        rule = "Product boundary: AMH is not a chat archive; human gate lifecycle records governance facts but does not approve for the user; only funneled MemoryItems become durable knowledge."

    body = header(title, desc)
    body.append(t(80, 152, "01", "section"))
    body.append(t(116, 152, "Pain + entry" if not zh else "痛点与入口", "section"))
    body.append(t(404, 152, "02", "section"))
    body.append(t(440, 152, "Product path" if not zh else "产品链路", "section"))
    body.append(t(1040, 152, "03", "section"))
    body.append(t(1076, 152, "Governance + outcome" if not zh else "治理与结果", "section"))

    body.append(compact_card(70, 188, 284, 146, actors[0], actors[1], actors[2], actors[3]))
    body.append(compact_card(70, 396, 284, 146, problem[0], problem[1], problem[2], problem[3]))

    loop_card_w = 190
    loop_positions = [(420, 164), (700, 164), (700, 420), (420, 420)]
    for (x, y), (card_title, detail, color, icon_name) in zip(loop_positions, product_loop):
        body.append(compact_card(x, y, loop_card_w, 138, card_title, detail, color, icon_name))

    body.append(flow_arrow(610, 233, 700, 233, curve=False))
    body.append(flow_arrow(795, 302, 795, 420, curve=False))
    body.append(flow_arrow(700, 489, 610, 489, curve=False))
    body.append(flow_arrow(515, 420, 515, 302, soft=True, curve=False))
    body.append(curved_arrow(354, 260, 430, 233))
    body.append(curved_arrow(354, 469, 430, 489, True))

    body.append(compact_card(1012, 178, 260, 126, governance[0][0], governance[0][1], governance[0][2], governance[0][3]))
    body.append(compact_card(1012, 354, 260, 126, governance[1][0], governance[1][1], governance[1][2], governance[1][3]))
    for idx, outcome in enumerate(outcomes):
        body.append(compact_card(1340, 152 + idx * 150, 190, 116, outcome[0], outcome[1], outcome[2], outcome[3]))
    body.append(curved_arrow(890, 233, 1012, 241))
    body.append(curved_arrow(890, 489, 1012, 417, True))
    body.append(curved_arrow(1272, 241, 1340, 208))
    body.append(curved_arrow(1272, 417, 1340, 508))
    body.append(curved_arrow(1435, 500, 515, 558, True))
    body.append(band(262, 650, 1096, 68, "产品边界" if zh else "Product boundary", [rule], "gray"))
    return ("product-architecture.zh.svg" if zh else "product-architecture.svg", title, "\n".join(body), 1580, 820)


def technical_architecture(zh: bool) -> tuple[str, str, str, int, int]:
    if zh:
        title = "技术架构图：接入、运行时、脑内核与本地数据"
        desc = "按真实 ownership 分层：接入适配、运行时外壳、核心服务、派生读模型、治理门禁和本地事实源。"
        layers = [
            ("Agent Integrations", [("Codex / Claude / Qoder", "hook/MCP/file/provider"), ("Wukong / Hermes / Open*", "install / doctor / verify")], "blue"),
            ("Agent Runtime Kit", [("hooks + tools", "shell fallback + context inject"), ("MCP launcher", "portable startup surface")], "teal"),
            ("Agent Brain Core", [("WriteService", "single external write funnel"), ("RetrievalService", "BM25/vector/RRF + trace"), ("ContextService", "firewall + context_pack")], "green"),
            ("Read Models", [("HubIndex", "meta / FTS / vector / graph"), ("ResourceStore", "resources / extractions sidecar"), ("Runtime ledger", "adapter events + feedback")], "orange"),
            ("Governance Gates", [("Review candidates", "approve 才写入"), ("Compression / ML advisory", "benchmark gate"), ("Truth contract", "docs + tests lock")], "purple"),
            ("Local Storage", [("Markdown items", "authoritative truth"), ("index.db", "rebuildable projection"), ("jsonl sidecars", "review / pending / runtime")], "yellow"),
        ]
        rule = "技术不变量：Markdown item 是权威事实源；index.db、vector、runtime、review、resource 都是派生层或证据旁路，不能反向覆盖事实源。"
    else:
        title = "Technical Architecture: Integrations, Runtime, Brain Core, Local Data"
        desc = "A true ownership map: agent integrations, runtime shell, core services, derived read models, governance gates, and local truth."
        layers = [
            ("Agent Integrations", [("Codex / Claude / Qoder", "hook/MCP/file/provider"), ("Wukong / Hermes / Open*", "install / doctor / verify")], "blue"),
            ("Agent Runtime Kit", [("hooks + tools", "shell fallback + context inject"), ("MCP launcher", "portable startup surface")], "teal"),
            ("Agent Brain Core", [("WriteService", "single external write funnel"), ("RetrievalService", "BM25/vector/RRF + trace"), ("ContextService", "firewall + context_pack")], "green"),
            ("Read Models", [("HubIndex", "meta / FTS / vector / graph"), ("ResourceStore", "resources / extractions sidecar"), ("Runtime ledger", "adapter events + feedback")], "orange"),
            ("Governance Gates", [("Review candidates", "approval writes only through funnel"), ("Compression / ML advisory", "benchmark gate"), ("Truth contract", "docs + tests lock")], "purple"),
            ("Local Storage", [("Markdown items", "authoritative truth"), ("index.db", "rebuildable projection"), ("jsonl sidecars", "review / pending / runtime")], "yellow"),
        ]
        rule = "Technical invariant: Markdown items are authoritative; index.db, vectors, runtime, review, and resources are derived projections or evidence sidecars."

    body = header(title, desc)
    y = 138
    layer_h = 92
    layer_gap = 70
    for row_idx, (layer_title, cells, color) in enumerate(layers):
        fill, ink, stroke = PALETTE[color]
        body.append(f'  <rect x="64" y="{y}" width="1152" height="{layer_h}" rx="20" fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>')
        body.append(t(92, y + 36, layer_title, "card-title", ink))
        body.append(t(92, y + 61, f"L{row_idx + 1}", "mono-small", ink))
        cell_w = 282 if len(cells) == 3 else 420
        x = 322
        for cell_title, cell_text in cells:
            body.append(f'  <rect x="{x}" y="{y + 15}" width="{cell_w}" height="62" rx="14" fill="#ffffff" stroke="{stroke}" stroke-width="1.1"/>')
            body.append(t(x + 18, y + 39, cell_title, "card-title"))
            body.append(t(x + 18, y + 61, cell_text, "small"))
            x += cell_w + 22
        if row_idx < len(layers) - 1:
            body.append(flow_arrow(640, y + layer_h, 640, y + layer_h + layer_gap, curve=False))
        y += layer_h + layer_gap
    body.append(band(150, y + 10, 980, 66, "事实源边界" if zh else "Truth boundary", [rule], "gray"))
    return ("technical-architecture.zh.svg" if zh else "technical-architecture.svg", title, "\n".join(body), 1280, y + 104)


def sequence_diagram(zh: bool, retrieval: bool) -> tuple[str, str, str, int, int]:
    def stage_card(
        x: int,
        y: int,
        w: int,
        h: int,
        number: str,
        title_value: str,
        detail: list[str],
        color: str,
        icon_name: str,
    ) -> str:
        fill, ink, stroke = PALETTE[color]
        title_x = x + 76
        title_lines = wrap_text(title_value, max(4.0, (w - 104) / 18))
        detail_y = y + 56 + len(title_lines) * 19
        detail_units = max(6.0, (w - 96) / 14)
        max_detail_lines = max(1, (h - detail_y + y - 14) // 18)
        return f"""  <g class="breath-slow">
    <rect class="card" x="{x}" y="{y}" width="{w}" height="{h}" rx="18" fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>
    <circle cx="{x + 41}" cy="{y + 42}" r="26" fill="#ffffff" opacity="0.66"/>
    {icon(icon_name, x + 18, y + 19, ink)}
    <rect x="{x + w - 52}" y="{y + 16}" width="34" height="24" rx="12" fill="#ffffff" stroke="{stroke}" stroke-width="1.1"/>
    <text class="tiny" x="{x + w - 43}" y="{y + 32}" fill="{ink}">{escape(number)}</text>
{lines(title_x, y + 35, title_lines, "card-title", 20)}
{wrapped_lines(title_x, detail_y, detail, "small", 18, max_units=detail_units, max_total_lines=max_detail_lines)}
  </g>"""

    def card_connector(
        current: tuple[int, int],
        nxt: tuple[int, int],
        *,
        w: int,
        h: int,
        soft: bool = False,
    ) -> str:
        x1, y1 = current
        x2, y2 = nxt
        if y1 == y2:
            if x2 > x1:
                return flow_arrow(x1 + w + 10, y1 + h // 2, x2 - 10, y2 + h // 2, soft=soft, curve=False)
            return flow_arrow(x1 - 10, y1 + h // 2, x2 + w + 10, y2 + h // 2, soft=soft, curve=False)
        return flow_arrow(x1 + w // 2, y1 + h + 8, x2 + w // 2, y2 - 8, soft=soft, curve=False)

    if retrieval:
        name = "memory-retrieval-sequence.zh.svg" if zh else "memory-retrieval-sequence.svg"
        title = "召回时序链路图" if zh else "Retrieval Sequence Chain"
        desc = "维护产物 MemoryItem/Index/Runtime Ledger 被读取后，用户问题才进入过滤、并行召回、RRF、治理排序、防火墙和分层 context_pack。"
        stages = [
            ("01", "用户问题" if zh else "User question", ["prompt / search call", "project / adapter / time"], "teal", "agent"),
            ("02", "元数据过滤" if zh else "Metadata filter", ["type / project", "tags / tenant"], "blue", "govern"),
            ("03", "并行召回" if zh else "Parallel recall", ["BM25/FTS", "vector + graph sidecar"], "green", "recall"),
            ("04", "RRF 融合" if zh else "RRF fusion", ["rank fusion", "第一候选池"], "orange", "recall"),
            ("05", "可信排序" if zh else "Trust ranking", ["rerank / confidence", "decay / feedback"], "yellow", "observe"),
            ("06", "过期替代过滤" if zh else "Freshness gates", ["temporal state", "supersession filter"], "red", "govern"),
            ("07", "关联扩展" if zh else "Associative expand", ["MMR / Hopfield", "refs_graph 可选增强"], "purple", "recall"),
            ("08", "防火墙" if zh else "Firewall", ["topic / scope", "safety / budget" if not zh else "安全 / 预算"], "pink", "govern"),
            ("09", "分层装载" if zh else "Layered load", ["locator 默认", "detail 按需"], "indigo", "loading"),
            ("10", "上下文包" if zh else "context_pack", ["detail_uri", "CCR / 注入" if zh else "CCR / inject"], "gray", "store"),
        ]
        note = "读取起点是维护链路留下的 MemoryItem + Index Projection + Runtime Ledger；召回不等于注入，防火墙和 context_loading 决定 locator/overview/detail。" if zh else "The read starts from MemoryItem + Index Projection + Runtime Ledger produced by maintenance; retrieved does not mean injected."
        card_w, card_h = 190, 136
        row1_xs = [60, 345, 630, 915, 1200]
        positions = [(x, 160) for x in row1_xs] + [(x, 414) for x in reversed(row1_xs)]
    else:
        name = "memory-maintenance-sequence.zh.svg" if zh else "memory-maintenance-sequence.svg"
        title = "维护时序链路图" if zh else "Maintenance Sequence Chain"
        desc = "先把证据变成可信 MemoryItem，再投影索引和运行账本；召回只能读取这个交接面，不能直接读原始噪声。"
        stages = [
            ("01", "证据信号" if zh else "Evidence signal", ["write / hook / harvest", "task outcome / source"], "teal", "evidence"),
            ("02", "候选隔离" if zh else "Candidate isolation", ["proactive / semantic", "needs-review"], "purple", "govern"),
            ("03", "写入漏斗" if zh else "Write funnel", ["WriteService", "audit + enrich"], "orange", "memory"),
            ("04", "事实源" if zh else "Truth source", ["items/mem-*.md", "MemoryItem"], "green", "store"),
            ("05", "索引投影" if zh else "Index projection", ["FTS / vector / graph", "dirty id / reindex"], "blue", "recall"),
            ("06", "运行账本" if zh else "Runtime ledger", ["adapter events", "feedback/status"], "yellow", "observe"),
            ("07", "治理建议" if zh else "Governance advice", ["maturity / drift / TTL", "supersession"], "red", "govern"),
            ("08", "复核出口" if zh else "Review exit", ["safe_apply low risk", "archive/merge review"], "indigo", "govern"),
        ]
        note = "交接面：MemoryItem 是事实，Index Projection 是可重建读模型，Runtime Ledger 是排序/反馈证据；召回读取交接面，不直接注入 raw transcript。" if zh else "Handoff surface: MemoryItem is truth, Index Projection is rebuildable read model, Runtime Ledger is ranking/feedback evidence."
        card_w, card_h = 260, 140
        row1_xs = [70, 420, 770, 1120]
        positions = [(x, 164) for x in row1_xs] + [(x, 416) for x in reversed(row1_xs)]

    body = header(title, desc)
    body.append(t(72, 134, "solid = current implemented path" if not zh else "实线 = 当前已实现路径", "small"))
    body.append(t(330, 134, "dashed / review = gated side path" if not zh else "review / gate = 候选或高风险路径", "small"))
    for idx, stage in enumerate(stages):
        body.append(stage_card(*positions[idx], card_w, card_h, *stage))
    for idx in range(len(positions) - 1):
        soft = idx in {4, 5, 6} if retrieval else idx in {4, 5, 6}
        body.append(card_connector(positions[idx], positions[idx + 1], w=card_w, h=card_h, soft=soft))
    note_y = 610 if retrieval else 612
    body.append(band(190, note_y, 1120, 72, "关键策略" if zh else "Key policy", [note], "gray"))
    return name, title, "\n".join(body), 1500, note_y + 118


def operating_loop(zh: bool) -> tuple[str, str, str, int, int]:
    if zh:
        title = "AMH 总控图：可信上下文操作回路"
        desc = "AMH 的主线从意图进入，经查询信号、证据、记忆、索引、算法排序、防火墙、上下文包，再回到反馈和治理。"
        stages = [
            ("01", "意图入口", ["Agent / User Intent", "cwd / project"], "blue", "agent"),
            ("02", "查询信号", ["Query Signal / SearchFilter", "弱意图阻断 / type / tags"], "teal", "govern"),
            ("03", "证据采集", ["Evidence Capture", "prompt / transcript"], "pink", "evidence"),
            ("04", "记忆治理", ["Memory Curation", "confidence / maturity"], "yellow", "memory"),
            ("05", "长期事实", ["MemoryItem Truth", "locator / overview / detail"], "green", "store"),
            ("06", "索引投影", ["Index Projection", "FTS/BM25 / vector / refs_graph"], "blue", "store"),
            ("07", "召回排序", ["Retrieval Ranking", "RRF / rerank / decay"], "orange", "recall"),
            ("08", "上下文防火墙", ["Context Firewall", "scope / stale"], "red", "govern"),
            ("09", "上下文注入", ["ContextPack", "locator / overview"], "purple", "loading"),
            ("10", "反馈治理 Loop", ["Feedback / Governance", "adopted / rejected"], "indigo", "observe"),
        ]
        algorithm_items = [
            "BM25 + vector parallel",
            "RRF",
            "metadata phrase boost",
            "rerank",
            "confidence",
            "forgetting curve / decay coefficient",
            "feedback weight",
            "runtime/status",
            "temporal stale / supersession",
            "MMR / Hopfield / refs_graph",
        ]
        boundaries = [
            "MemoryItem 才是长期事实；Evidence 只是证据。",
            "Index Projection 可重建；RetrievedItem 不是注入内容。",
            "ContextFirewall 决定 include / demote / exclude。",
            "maturity 是治理/分层信号，不是默认 live rank multiplier。",
        ]
    else:
        title = "AMH Control Diagram: Trusted Context Operating Loop"
        desc = "The master flow is not just recall: intent becomes query signal, evidence, memory, index, ranking, firewall, context pack, feedback, and governance."
        stages = [
            ("01", "Agent / User Intent", ["user question / task goal", "cwd / project / adapter"], "blue", "agent"),
            ("02", "Query Signal / SearchFilter", ["weak-intent blocking", "type / tags / tenant"], "teal", "govern"),
            ("03", "Evidence Capture", ["prompt / transcript", "resource / extraction"], "pink", "evidence"),
            ("04", "Memory Curation", ["confidence", "maturity / retention"], "yellow", "memory"),
            ("05", "MemoryItem Truth", ["Markdown source of truth", "locator / overview / detail / refs"], "green", "store"),
            ("06", "Index Projection", ["FTS/BM25 / vector", "metadata / refs_graph"], "blue", "store"),
            ("07", "Retrieval Ranking", ["RRF / rerank", "decay / feedback / graph"], "orange", "recall"),
            ("08", "Context Firewall", ["scope / safety / stale", "sensitivity / token budget"], "red", "govern"),
            ("09", "ContextPack Injection", ["locator -> overview -> detail_uri", "Headroom / CCR"], "purple", "loading"),
            ("10", "Feedback / Governance / Loop", ["adopted / rejected / ignored", "drift / TTL / benchmark"], "indigo", "observe"),
        ]
        algorithm_items = [
            "BM25 + vector parallel",
            "RRF",
            "metadata phrase boost",
            "rerank",
            "confidence",
            "forgetting curve / decay coefficient",
            "feedback weight",
            "runtime/status",
            "temporal stale / supersession",
            "MMR / Hopfield / refs_graph",
        ]
        boundaries = [
            "MemoryItem is durable truth; Evidence is support.",
            "Index Projection is rebuildable; RetrievedItem is not injected content.",
            "ContextFirewall decides include / demote / exclude.",
            "maturity is governance metadata, not a live rank multiplier.",
        ]

    body = header(title, desc)
    solid_label = "solid = default path" if not zh else "实线 = 默认主路径"
    sidecar_label = "sidecar = governance / algorithm factors" if not zh else "旁路 = 治理 / 算法因子"
    solid_w = chip_width(solid_label, min_width=88, padding=52)
    body.append(status_chip(70, 126, solid_label, "green"))
    body.append(status_chip(70 + solid_w + 22, 126, sidecar_label, "orange", dashed=True))

    card_w, card_h = 220, 136
    top_positions = [(60, 172), (350, 172), (640, 172), (930, 172), (1220, 172)]
    bottom_positions = [(1220, 442), (930, 442), (640, 442), (350, 442), (60, 442)]
    positions = top_positions + bottom_positions
    for (x, y), (num, name, detail, color, icon_name) in zip(positions, stages):
        body.append(boundary_card(x, y, card_w, card_h, f"{num} {name}", detail, color, icon_name))

    for idx in range(4):
        x, y = top_positions[idx]
        nx, ny = top_positions[idx + 1]
        body.append(flow_arrow(x + card_w, y + card_h // 2, nx, ny + card_h // 2, curve=False))
    body.append(flow_arrow(1220 + card_w // 2, 172 + card_h, 1220 + card_w // 2, 442, curve=False))
    for idx in range(4):
        x, y = bottom_positions[idx]
        nx, ny = bottom_positions[idx + 1]
        body.append(flow_arrow(x, y + card_h // 2, nx + card_w, ny + card_h // 2, curve=False))
    body.append(flow_arrow(60 + card_w // 2, 442, 60 + card_w // 2, 308, soft=True, planned=True, curve=True))

    body.append(columns_band(300, 650, 900, 142, "排序算法栈" if zh else "Ranking algorithm stack", algorithm_items, "orange", cols=2))
    body.append(flow_arrow(1020, 578, 750, 664, soft=True, planned=True, curve=True))
    body.append(columns_band(300, 820, 900, 96, "边界与降级" if zh else "Boundaries and degradation", boundaries, "gray", cols=2))
    return ("amh-operating-loop.zh.svg" if zh else "amh-operating-loop.svg", title, "\n".join(body), 1500, 980)


def loop_layered_architecture(zh: bool) -> tuple[str, str, str, int, int]:
    if zh:
        title = "AMH 可信上下文生命周期图：接入、维护、召回、治理与评估"
        headline = ["可信上下文", "生命周期全景"]
        subtitle = [
            "记忆先接入，再维护成事实，再召回成候选，再治理成资产，最后用评估做门禁。",
            "评估分两轨：AMH 本地 system benchmark 已完成；MemoryData 外部横评需要独立 loop。",
            "用户反馈、Agent 反馈、访问次数、成熟度和评测指标共同决定下一轮 loop 能信什么。",
        ]
        lead_text = "把不同 AI Agent 产生的事实、决策、经验、产物与交接，沉淀成同一层本地可信上下文。"
        capability_tiles = [
            ("共享事实层", "多 Agent / 多角色 / 多会话"),
            ("证据维护", "live prompt / transcript | WriteService"),
            ("召回排序", "BM25 / 向量 / RRF | MMR-Hopfield"),
            ("分层装载", "Firewall / locator | overview / detail"),
            ("反馈治理", "support / contradict | gain / maturity"),
            ("接入生态", "Adapter / hooks / MCP | CLI / SDK / Web"),
            ("Loop 账本", "goal / budget / verifier | artifact"),
            ("评估门禁", "doctor / benchmark | docs lock / screenshots"),
        ]
        layers = [
            (
                "记忆接入层",
                "Agent入口",
                [
                    "Claude Code",
                    "Codex",
                    "Cursor",
                    "Hermes Agent",
                    "OpenClaw",
                    "OpenHuman",
                    "OpenSquilla",
                    "Aone Copilot",
                    "Qoder",
                    "Qoder Work",
                    "Wukong",
                    "MuleRun",
                    "WorkBuddy",
                    "…",
                ],
                ["接入层只表达入口顺序；是否通过验证由自检、运行证据和上下文有效性证明。"],
            ),
            (
                "记忆维护层",
                "证据成事实",
                [
                    "入口证据",
                    "候选提炼",
                    "写入审计",
                    "MemoryItem",
                    "来源/资源库",
                    "抽取库",
                    "索引/向量投影",
                    "运行/反馈账本",
                    "层级Sidecar",
                    "Profile派生",
                    "待重放",
                    "时效/废止",
                ],
                [
                    "原始对话只是证据；长期事实是 MemoryItem，索引、向量、反馈和运行时都是可重建投影。",
                    "维护层负责提炼、审计、重放、时效和废止，不直接决定 prompt 注入。",
                ],
            ),
            (
                "记忆召回层",
                "候选排序",
                [
                    "查询/结构过滤",
                    "词频/BM25投影",
                    "向量召回",
                    "RRF融合",
                    "元数据短语提升",
                    "模型重排",
                    "遗忘曲线",
                    "衰减系数",
                    "反馈价值乘子",
                    "运行证据加权",
                    "时效过滤",
                    "废止过滤",
                    "多样性重排",
                    "联想扩展",
                    "图谱扩展",
                    "防火墙/分层注入",
                ],
                [
                    "RRF=Σ权重/(k+排名+1) · 有效分=分数×置信度×衰减系数 · 反馈价值∈[0.25,2.0]",
                    "MMR=λ相关性-(1-λ)相似度 · Hopfield吸引子→邻居 · 可选扩展需过评测门禁",
                ],
            ),
            (
                "记忆治理层",
                "反馈与演化",
                [
                    "用户/Agent反馈",
                    "访问次数",
                    "采用/拒绝/忽略",
                    "支持/反驳/收益",
                    "成熟度",
                    "重复/冲突审计",
                    "漂移检测",
                    "会话冷热分层",
                    "主动候选",
                    "维护计划",
                    "自动治理",
                    "演化控制",
                    "演化复核闭环",
                ],
                [
                    "ignored 不是负反馈；access_count、adopt/reject、support/contradict/gain 共同影响治理建议。",
                    "成熟度是 raw/consolidated/skill 的资产建议，不是绕过召回和防火墙的 live multiplier。",
                ],
            ),
            (
                "记忆评估层",
                "测试与门禁",
                [
                    "AMH本地基准",
                    "MemoryData",
                    "外部横评",
                    "弱意图阻断",
                    "可注入识别",
                    "Recall@K",
                    "MRR",
                    "防火墙/可逆",
                    "Token预算",
                    "发布门禁",
                    "压缩门禁",
                    "ML/DL门禁",
                    "系统Few-shot",
                    "文档契约",
                    "截图验收",
                ],
                [
                    "本地 system benchmark 已完成；MemoryData / AgentMemory-Bench 横评未完成前只展示 loop，不写外部结论。",
                    "指标证明当前样本集表现，不承诺未来所有 prompt 都一定正确。",
                ],
            ),
        ]
        tabs = [
            ("维护事实", "证据 → 记忆项 → 投影"),
            ("召回排序", "BM25 / 向量 / RRF / 遗忘曲线 / Hopfield / MMR"),
            ("分层注入", "防火墙 → 定位/概览/详情"),
            ("反馈治理", "访问 / 用户 / Agent 反馈"),
            ("评估门禁", "本地基准 / MemoryData loop"),
        ]
    else:
        title = "AMH Trusted Context Lifecycle: Access, Maintenance, Retrieval, Governance, Evaluation"
        headline = ["Trusted Context", "Lifecycle Panorama"]
        subtitle = [
            "Memory enters through agents, becomes maintained truth, ranks as candidates, evolves through governance, and is gated by evaluation.",
            "BM25, RRF, forgetting curve, feedback value, MMR, Hopfield, and graph expansion stay inside retrieval ranking.",
            "User feedback, agent feedback, access counts, maturity, and metrics decide what the next loop can trust.",
        ]
        lead_text = "Turns facts, decisions, experience, artifacts, and handoffs from AI agents into one local trusted context layer."
        capability_tiles = [
            ("Shared facts", "multi-agent / roles / sessions"),
            ("Evidence care", "live prompt / transcript | WriteService"),
            ("Recall rank", "BM25 / vector / RRF | MMR-Hopfield"),
            ("Layered pack", "Firewall / locator | overview / detail"),
            ("Governance", "support / contradict | gain / maturity"),
            ("Surfaces", "adapter / hooks / MCP | CLI / SDK / Web"),
            ("Loop ledger", "goal / budget / verifier | artifact"),
            ("Eval gates", "doctor / benchmark | docs lock / screenshots"),
        ]
        layers = [
            (
                "Memory Access",
                "agent entry",
                [
                    "Claude Code",
                    "Codex",
                    "Cursor",
                    "Hermes Agent",
                    "OpenClaw",
                    "OpenHuman",
                    "OpenSquilla",
                    "Aone Copilot",
                    "Qoder",
                    "Qoder Work",
                    "Wukong",
                    "MuleRun",
                    "WorkBuddy",
                    "...",
                ],
                [
                    "Access order is separate from verified/install-ready/wip evidence.",
                ],
            ),
            (
                "Memory Maintenance",
                "truth maintenance",
                ["Evidence", "Candidate", "Write audit", "MemoryItem", "Sources", "Extractions", "Index/vector", "Runtime log", "Hierarchy", "Profile", "Pending", "Temporal"],
                ["Raw transcript is evidence; MemoryItem is durable truth, while indexes, vectors, feedback, and runtime are rebuildable projections."],
            ),
            (
                "Memory Retrieval",
                "candidate ranking",
                [
                    "Query/filter",
                    "FTS/BM25",
                    "Vector",
                    "RRF",
                    "Phrase boost",
                    "Rerank",
                    "Forgetting",
                    "Decay coef",
                    "Feedback",
                    "Runtime proof",
                    "Freshness",
                    "Supersede",
                    "MMR diversity",
                    "Hopfield assoc",
                    "Graph hop",
                    "Firewall/pack",
                ],
                [
                    "RRF=Σw/(k+rank+1) · effective=score×confidence×coefficient · feedback∈[0.25,2.0]",
                    "MMR=max(λ·rel-(1-λ)·sim) · Hopfield=attractor→neighbors · optional expansions stay benchmark-gated",
                ],
            ),
            (
                "Memory Governance",
                "feedback evolution",
                ["Feedback", "Access count", "Adopt/reject", "Support/gain", "Maturity", "Conflict", "Drift", "Hot/warm tier", "Proactive", "Plan", "Auto govern", "Evolution", "Review+loop"],
                ["ignored is not negative feedback; maturity=raw/consolidated/skill is governance advice, not a live rank bypass."],
            ),
            (
                "Memory Evaluation",
                "metrics gates",
                ["AMH local", "MemoryData", "External loop", "Weak block", "Inject detect", "Recall@K", "MRR", "Firewall/pack", "Token budget", "Release gate", "Compression gate", "ML/DL gate", "Few-shot", "Docs lock", "Screenshots"],
                ["AMH local benchmark is complete; MemoryData / AgentMemory-Bench needs a separate loop before publishing external results."],
            ),
        ]
        tabs = [
            ("Truth maintenance", "evidence -> MemoryItem -> projection"),
            ("Retrieval ranking", "BM25 / vector / RRF / forgetting / Hopfield / MMR"),
            ("Layered injection", "firewall -> locator/overview/detail"),
            ("Feedback governance", "access / user / agent feedback"),
            ("Evaluation gates", "local benchmark / MemoryData loop"),
        ]

    def big_text(x: int, y: int, value: str, *, size: int, weight: int = 850, fill: str = "#24292f") -> str:
        return f'  <text x="{x}" y="{y}" font-family=\'{FONT}\' font-size="{size}" font-weight="{weight}" fill="{fill}">{escape(value)}</text>'

    def small_text(x: int, y: int, value: str, *, size: int = 15, fill: str = "#57606a", weight: int = 500) -> str:
        return f'  <text x="{x}" y="{y}" font-family=\'{FONT}\' font-size="{size}" font-weight="{weight}" fill="{fill}">{escape(value)}</text>'

    blue = "#0969da"
    blue_dark = "#0a4f9e"
    blue_light = "#eef6ff"
    blue_side = "#d9ecff"
    grid = "#c9d8eb"

    def mini_card(x: int, y: int, w: int, h: int, label: str, *, core: bool = False, font_size: int = 12) -> str:
        fill = blue if core else "#fffdf8"
        stroke = "#0757b8" if core else blue
        ink = "#ffffff" if core else "#24292f"
        opacity = "1" if core else "0.72"
        if h <= 24:
            units = sum(0.55 if ord(ch) < 128 else 1.0 for ch in label)
            line_size = min(font_size, max(6.8, (w - 12) / max(units, 1)))
            label_lines = [label]
            line_gap = 10
            start_y = y + h / 2 + 3.5
        else:
            max_units = max(4.2, (w - 16) / max(font_size, 1) / 1.08)
            label_lines = wrap_text(label, max_units)
            if len(label_lines) > 2:
                label_lines = [label_lines[0], "".join(label_lines[1:])]
            line_size = min(font_size, 8 if len(label_lines) > 1 and h <= 22 else font_size)
            line_gap = 12
            start_y = y + h / 2 + 4 - (len(label_lines) - 1) * line_gap / 2
        text = "\n".join(
            f'    <text x="{x + w / 2:.0f}" y="{start_y + idx * line_gap:.1f}" text-anchor="middle" font-family=\'{FONT}\' font-size="{line_size:.1f}" font-weight="760" fill="{ink}">{escape(line)}</text>'
            for idx, line in enumerate(label_lines)
        )
        return f"""  <g>
    <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}" stroke-opacity="{opacity}" stroke-width="1.25"/>
{text}
  </g>"""

    def slab(y: int, layer_name: str, layer_desc: str, items: list[str], notes: list[str]) -> str:
        left, width, plate_h, thick, skew = 530, 840, 92, 14, 50
        right = left + width
        top_y = y
        front_y = y + plate_h
        bottom_y = top_y + thick
        bottom_front_y = front_y + thick
        top_pts = [
            (left + skew, top_y),
            (right - skew, top_y),
            (right, front_y),
            (left, front_y),
        ]
        bottom_pts = [(x, py + thick) for x, py in top_pts]
        top = " ".join(f"{x},{py}" for x, py in top_pts)
        bottom = " ".join(f"{x},{py}" for x, py in bottom_pts)
        back_face = " ".join(f"{x},{py}" for x, py in [top_pts[0], top_pts[1], bottom_pts[1], bottom_pts[0]])
        right_face = " ".join(f"{x},{py}" for x, py in [top_pts[1], top_pts[2], bottom_pts[2], bottom_pts[1]])
        front_face = " ".join(f"{x},{py}" for x, py in [top_pts[3], top_pts[2], bottom_pts[2], bottom_pts[3]])
        left_face = " ".join(f"{x},{py}" for x, py in [top_pts[0], top_pts[3], bottom_pts[3], bottom_pts[0]])
        label_y = y + plate_h // 2 + thick // 2
        label_dot_x = right + 4
        label_line_end = right + 28
        label_text_x = right + 38
        grid_lines = "\n".join(
            f'    <line x1="{left + skew + offset}" y1="{top_y + 7}" x2="{left + skew + offset - 38}" y2="{front_y - 7}" stroke="{grid}" stroke-opacity="0.32" stroke-width="0.75"/>'
            for offset in range(50, 620, 90)
        )
        gap = 6
        cols = 8 if len(items) >= 15 else (7 if len(items) > 7 else len(items))
        rows = ceil(len(items) / cols)
        card_h = 22 if rows > 1 else 28
        card_w = int((width - 84 - (cols - 1) * gap) / cols)
        card_w = min(112, max(72, card_w))
        font_size = 9 if rows > 1 else (10 if card_w < 90 or any(len(item) > 8 for item in items) else 11)
        total_w = cols * card_w + (cols - 1) * gap
        start_x = left + (width - total_w) / 2
        highlighted = {
            "系统基准",
            "ContextPack可逆",
            "System benchmark",
            "Pack reversible",
        }
        card_parts = []
        for idx, item in enumerate(items):
            row = idx // cols
            col = idx % cols
            card_parts.append(
                mini_card(
                    int(start_x + col * (card_w + gap)),
                    y + 14 + row * (card_h + 5),
                    card_w,
                    card_h,
                    item,
                    core=(item in highlighted),
                    font_size=font_size,
                )
            )
        cards = "\n".join(card_parts)
        note_base_y = y + 64 if rows == 1 else y + 14 + rows * card_h + (rows - 1) * 5 + 14
        note_lines = "\n".join(
            f'    <text x="{left + 36}" y="{note_base_y + idx * 10}" font-family=\'{FONT}\' font-size="8" font-weight="650" fill="#57606a">{escape(note)}</text>'
            for idx, note in enumerate(notes[:2])
        )
        stage_labels = {
            "记忆接入层": "L1 记忆接入",
            "记忆维护层": "L2 事实维护",
            "记忆召回层": "L3 候选召回",
            "记忆治理层": "L4 反馈治理",
            "记忆评估层": "L5 评估门禁",
            "Memory Access": "L1 ACCESS",
            "Memory Maintenance": "L2 MAINTAIN",
            "Memory Retrieval": "L3 RECALL",
            "Memory Governance": "L4 GOVERN",
            "Memory Evaluation": "L5 EVALUATE",
        }
        stage_label = stage_labels.get(layer_name, "LIFECYCLE")
        label = f"""  <g>
    <line x1="{label_dot_x}" y1="{label_y}" x2="{label_line_end}" y2="{label_y}" stroke="{blue}" stroke-width="1.25"/>
    <circle cx="{label_dot_x}" cy="{label_y}" r="4" fill="{blue}"/>
    <text x="{label_text_x}" y="{label_y - 6}" font-family='{FONT}' font-size="16" font-weight="850" fill="{blue_dark}">{escape(layer_name)}</text>
    <text x="{label_text_x}" y="{label_y + 14}" font-family='{FONT}' font-size="12" fill="#57606a">{escape(layer_desc)}</text>
  </g>"""
        return f"""  <g>
    <polygon points="{back_face}" fill="{blue_side}" fill-opacity="0.46" stroke="{blue}" stroke-opacity="0.32" stroke-width="1"/>
    <polygon points="{left_face}" fill="{blue_side}" fill-opacity="0.54" stroke="{blue}" stroke-opacity="0.38" stroke-width="1"/>
    <polygon points="{right_face}" fill="{blue_side}" fill-opacity="0.54" stroke="{blue}" stroke-opacity="0.38" stroke-width="1"/>
    <polygon points="{front_face}" fill="{blue_side}" fill-opacity="0.62" stroke="{blue}" stroke-opacity="0.5" stroke-width="1"/>
    <polygon points="{bottom}" fill="none" stroke="{blue}" stroke-opacity="0.5" stroke-width="1.1"/>
    <polygon points="{top}" fill="{blue_light}" stroke="{blue}" stroke-opacity="0.82" stroke-width="1.4" filter="url(#softShadow)"/>
{grid_lines}
{cards}
{note_lines}
    <text x="{left + 18}" y="{bottom_front_y - 4}" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="10" font-weight="760" fill="#6e7781">{escape(stage_label)}</text>
  </g>
{label}"""

    def nav_icon(x: int, y: int, index: int) -> str:
        common = f'fill="none" stroke="{blue}" stroke-width="1.55" stroke-linecap="round" stroke-linejoin="round"'
        if index == 0:
            return f"""    <g>
      <rect x="{x + 8}" y="{y + 8}" width="28" height="28" rx="4" {common}/>
      <path d="M{x + 14} {y + 16} H{x + 30} M{x + 14} {y + 22} H{x + 26} M{x + 14} {y + 28} H{x + 22} M{x + 25} {y + 31} l4 4 l8 -10" {common}/>
    </g>"""
        if index == 1:
            return f"""    <g>
      <circle cx="{x + 15}" cy="{y + 17}" r="8.5" {common}/>
      <path d="M{x + 21} {y + 23} l7 7 M{x + 31} {y + 11} H{x + 40} M{x + 31} {y + 19} H{x + 38} M{x + 31} {y + 27} H{x + 35}" {common}/>
      <circle cx="{x + 27}" cy="{y + 11}" r="1.2" fill="{blue}"/>
      <circle cx="{x + 27}" cy="{y + 19}" r="1.2" fill="{blue}"/>
      <circle cx="{x + 27}" cy="{y + 27}" r="1.2" fill="{blue}"/>
    </g>"""
        if index == 2:
            return f"""    <g>
      <rect x="{x + 4}" y="{y + 8}" width="17" height="6" rx="2" {common}/>
      <rect x="{x + 4}" y="{y + 18}" width="17" height="6" rx="2" {common}/>
      <rect x="{x + 4}" y="{y + 28}" width="17" height="6" rx="2" {common}/>
      <path d="M{x + 24} {y + 21} H{x + 34} m-4 -5 l5 5 -5 5 M{x + 38} {y + 9} h5 v25 h-5" {common}/>
    </g>"""
        if index == 3:
            return f"""    <g>
      <path d="M{x + 8} {y + 10} h26 a5 5 0 0 1 5 5 v15 a5 5 0 0 1 -5 5 H{x + 20} l-7 6 v-6 H{x + 13} a5 5 0 0 1 -5 -5 V{y + 15} a5 5 0 0 1 5 -5" {common}/>
      <path d="M{x + 15} {y + 18} H{x + 30} M{x + 15} {y + 24} H{x + 24} M{x + 25} {y + 29} l3 3 l7 -9" {common}/>
    </g>"""
        return f"""    <g>
      <path d="M{x + 7} {y + 35} V{y + 13} H{x + 37} V{y + 35} M{x + 7} {y + 22} H{x + 37} M{x + 22} {y + 13} V{y + 35}" {common}/>
      <circle cx="{x + 31}" cy="{y + 17}" r="7" fill="#fffdf8" stroke="{blue}" stroke-width="1.55"/>
      <path d="M{x + 27} {y + 17} l3 3 l6 -7" {common}/>
    </g>"""

    def stage_connector(x: int, y1: int, y2: int) -> str:
        mid = (y1 + y2) / 2
        bar_h = min(24, max(16, (y2 - y1) * 0.42))
        return f"""  <g opacity="0.78">
    <line x1="{x - 5}" y1="{mid - bar_h / 2:.1f}" x2="{x - 5}" y2="{mid + bar_h / 2:.1f}" stroke="{blue_dark}" stroke-width="2.1" stroke-opacity="0.62" stroke-linecap="round"/>
    <line x1="{x + 5}" y1="{mid - bar_h / 2:.1f}" x2="{x + 5}" y2="{mid + bar_h / 2:.1f}" stroke="{blue_dark}" stroke-width="2.1" stroke-opacity="0.62" stroke-linecap="round"/>
  </g>"""

    def brand_lockup(x: int, y: int) -> str:
        logo_uri = data_uri_for_svg("agent-memory-hub-logo-lockup-a-plus-candidate.svg")
        slogan = "让每一次沉淀，成为下一次出发。" if zh else "Make every memory the next starting point."
        return f"""  <g>
    <image href="{logo_uri}" x="{x}" y="{y}" width="252" height="65" preserveAspectRatio="xMinYMid meet"/>
    <text x="{x + 17}" y="{y + 82}" font-family='{FONT}' font-size="12" font-weight="760" fill="{blue_dark}">{escape(slogan)}</text>
  </g>"""

    def capability_panel(x: int, y: int, w: int, items: list[tuple[str, str]]) -> str:
        title_text = "核心能力地图" if zh else "Core capability map"
        kicker = "不是聊天仓库，而是跨 Agent 可复用的上下文闭环。" if zh else "A reusable context loop across agents, not a chat archive."
        mono_label = "SHARED SECOND BRAIN" if zh else "TRUSTED CONTEXT LAYER"
        panel_h = 326
        tile_w = int((w - 36 - 10) / 2)
        tile_h = 55
        gap_x = 10
        gap_y = 8
        parts = [
            f"""  <g>
    <rect x="{x}" y="{y}" width="{w}" height="{panel_h}" rx="12" fill="#fffdf8" stroke="#c9d8eb" stroke-width="1.05"/>
    <path d="M{x} {y + 42} H{x + w}" stroke="#dbeafe" stroke-width="1"/>
    <text x="{x + 18}" y="{y + 24}" font-family='{FONT}' font-size="16" font-weight="880" fill="#24292f">{escape(title_text)}</text>
    <text x="{x + w - 18}" y="{y + 24}" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="7.5" font-weight="720" fill="#8c959f" text-anchor="end">{escape(mono_label)}</text>
    <text x="{x + 18}" y="{y + 58}" font-family='{FONT}' font-size="10.5" font-weight="680" fill="#57606a">{escape(kicker)}</text>"""
        ]
        for idx, (name, detail) in enumerate(items):
            row = idx // 2
            col = idx % 2
            tx = x + 18 + col * (tile_w + gap_x)
            ty = y + 76 + row * (tile_h + gap_y)
            parts.append(
                f"""    <g>
      <rect x="{tx}" y="{ty}" width="{tile_w}" height="{tile_h}" rx="9" fill="#f8fbff" stroke="#b8d6fb" stroke-width="1"/>
      <rect x="{tx + 10}" y="{ty + 10}" width="24" height="15" rx="7.5" fill="#e7f1ff" stroke="{blue}" stroke-width="0.9"/>
      <text x="{tx + 22}" y="{ty + 21}" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="7.5" font-weight="800" fill="{blue}" text-anchor="middle">{idx + 1:02d}</text>
      <text x="{tx + 42}" y="{ty + 22}" font-family='{FONT}' font-size="11.2" font-weight="850" fill="#24292f">{escape(name)}</text>"""
            )
            detail_lines = detail.split(" | ") if " | " in detail else wrap_text(detail, 18)
            for line_idx, line in enumerate(detail_lines[:2]):
                parts.append(
                    f"""      <text x="{tx + 12}" y="{ty + 38 + line_idx * 10}" font-family='{FONT}' font-size="8" font-weight="650" fill="#57606a">{escape(line)}</text>"""
                )
            parts.append("    </g>")
        parts.append("  </g>")
        return "\n".join(parts)

    body: list[str] = []
    body.append(brand_lockup(70, 50))
    for idx, line in enumerate(headline):
        fill = blue if idx == len(headline) - 1 else "#24292f"
        body.append(big_text(70, 188 + idx * 56, line, size=45, weight=880, fill=fill))
    for j, wrapped_line in enumerate(wrap_text(lead_text, 31)[:2]):
        body.append(small_text(72, 328 + j * 20, wrapped_line, size=15, fill="#57606a", weight=650))
    body.append(capability_panel(70, 376, 370, capability_tiles))

    guide_label = "MEMORY LIFECYCLE: ACCESS -> MAINTAIN -> RECALL -> GOVERN -> EVALUATE" if not zh else "记忆生命周期五层：接入 -> 维护 -> 召回 -> 治理 -> 评估"
    frame_top = 74
    layer_start_y = 80
    layer_stride = 148
    slab_visual_h = 106
    ys = [layer_start_y + idx * layer_stride for idx in range(len(layers))]
    frame_bottom = ys[-1] + slab_visual_h + 12
    frame_left = 516
    frame_right = 1422

    body.append(f"""  <g opacity="0.55">
    <line x1="{frame_left}" y1="{frame_top}" x2="{frame_right}" y2="{frame_top}" stroke="#c9d8eb" stroke-width="0.9"/>
    <line x1="{frame_left}" y1="{frame_bottom}" x2="{frame_right}" y2="{frame_bottom}" stroke="#c9d8eb" stroke-width="0.9"/>
    <line x1="{frame_left}" y1="{frame_top}" x2="{frame_left}" y2="{frame_bottom}" stroke="#c9d8eb" stroke-width="0.9"/>
    <line x1="{frame_right}" y1="{frame_top}" x2="{frame_right}" y2="{frame_bottom}" stroke="#c9d8eb" stroke-width="0.9"/>
    <text x="530" y="34" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="9.5" font-weight="700" fill="#8c959f">{escape(guide_label)}</text>
    <text x="1370" y="{frame_bottom + 13}" font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" font-size="9" fill="#8c959f">LOCAL-FIRST</text>
  </g>""")

    for y, layer in zip(ys, layers):
        body.append(slab(y, *layer))
    for idx in range(len(ys) - 1):
        body.append(stage_connector(968, ys[idx] + slab_visual_h, ys[idx + 1]))

    canvas_width = 1500 if zh else 1660
    tab_y = frame_bottom + 48
    canvas_height = tab_y + 90
    tab_w = canvas_width - 124
    tab_font_size = 16 if zh else 14
    body.append(f'  <rect x="62" y="{tab_y}" width="{tab_w}" height="66" rx="0" fill="#fffdf8" stroke="#d0d7de" stroke-width="1.1"/>')
    for idx, (name, _detail) in enumerate(tabs):
        cell_w = tab_w // len(tabs)
        x = 62 + idx * cell_w
        if idx:
            body.append(f'  <line x1="{x}" y1="{tab_y}" x2="{x}" y2="{tab_y + 66}" stroke="#d0d7de" stroke-width="1"/>')
        body.append(f"""  <g>
{nav_icon(x + 30, tab_y + 14, idx)}
    <text x="{x + 86}" y="{tab_y + 40}" font-family='{FONT}' font-size="{tab_font_size}" font-weight="850" fill="#24292f">{escape(name)}</text>
  </g>""")
    return ("amh-loop-layered-architecture.zh.svg" if zh else "amh-loop-layered-architecture.svg", title, "\n".join(body), canvas_width, canvas_height)


def memory_lifecycle_sequence(zh: bool) -> tuple[str, str, str, int, int]:
    if zh:
        title = "维护 + 召回一体时序链路图"
        desc = "上泳道先维护可信事实源；下泳道读取维护交接面后才召回、排序、防火墙和注入。"
        maint = [
            ("证据", "Evidence / prompt"),
            ("候选", "Candidate / review"),
            ("写入服务", "WriteService / audit"),
            ("长期事实", "MemoryItem"),
            ("索引投影", "Index Projection"),
            ("运行账本", "Runtime Ledger"),
        ]
        recall = [
            ("用户问题", "intent / cwd"),
            ("搜索过滤", "SearchFilter"),
            ("BM25+向量", "并行召回"),
            ("RRF+增强", "phrase / status"),
            ("排序因子", "rerank / confidence / decay"),
            ("新鲜度", "stale / supersession"),
            ("MMR+图", "Hopfield / refs_graph"),
            ("防火墙", "Pack / inject"),
        ]
        handoff = "交接面：MemoryItem + Index Projection + Runtime Ledger"
        failure = "失败/降级：候选未通过不写入；索引失败不否定 Markdown 事实；召回候选未过 firewall 不进入 ContextPack。"
    else:
        title = "Maintenance + Retrieval Sequence"
        desc = "The upper lane maintains trusted truth first; the lower lane reads the handoff surface before ranking, firewalls, and injection."
        maint = [
            ("Evidence", "prompt / transcript / resource"),
            ("Candidate", "isolate / review / denoise"),
            ("WriteService", "schema + audit + enrich"),
            ("MemoryItem", "Markdown truth"),
            ("Index Projection", "FTS / vector / graph"),
            ("Runtime Ledger", "feedback / status / events"),
        ]
        recall = [
            ("User Query", "intent + cwd + adapter"),
            ("SearchFilter", "type / tags / tenant"),
            ("BM25 + Vector", "parallel recall"),
            ("RRF + Boost", "phrase / status"),
            ("Rank Factors", "rerank / confidence / decay / feedback"),
            ("Freshness", "stale / supersession"),
            ("MMR + Graph", "Hopfield / refs_graph"),
            ("Firewall + Pack", "scope / safety / locator-detail"),
        ]
        handoff = "Handoff surface: MemoryItem + Index Projection + Runtime Ledger"
        failure = "Failure/degrade: unapproved candidates do not write; index failure does not invalidate Markdown truth; firewall rejects are not injected."

    body = header(title, desc)
    body.append(t(70, 142, "Lane A" if not zh else "泳道 A", "section"))
    body.append(t(136, 142, "Maintenance before recall" if not zh else "先维护可信事实源", "section"))
    body.append(t(70, 480, "Lane B" if not zh else "泳道 B", "section"))
    body.append(t(136, 480, "Retrieval reads the handoff surface" if not zh else "召回读取维护交接面", "section"))

    maint_xs = [58, 306, 554, 802, 1050, 1298]
    maint_w = 176
    colors = ["teal", "pink", "orange", "green", "blue", "yellow"]
    for idx, (x, (name, detail)) in enumerate(zip(maint_xs, maint)):
        body.append(compact_card(x, 178, maint_w, 118, name, [detail], colors[idx], ["evidence", "govern", "memory", "store", "recall", "observe"][idx]))
        if idx < len(maint_xs) - 1:
            body.append(flow_arrow(x + maint_w, 237, maint_xs[idx + 1], 237, curve=False))

    body.append(band(438, 340, 724, 72, "Handoff" if not zh else "维护交接面", [handoff], "gray"))
    body.append(flow_arrow(890, 296, 800, 340, soft=True, curve=True))
    body.append(flow_arrow(1138, 296, 800, 340, soft=True, curve=True))
    body.append(flow_arrow(1386, 296, 800, 340, soft=True, curve=True))

    recall_xs = [58, 246, 434, 622, 810, 998, 1186, 1374]
    recall_w = 118
    recall_colors = ["teal", "blue", "green", "orange", "yellow", "red", "purple", "indigo"]
    for idx, (x, (name, detail)) in enumerate(zip(recall_xs, recall)):
        body.append(lane_card(x, 516, recall_w, 126, name, [detail], recall_colors[idx]))
        if idx < len(recall_xs) - 1:
            body.append(flow_arrow(x + recall_w, 579, recall_xs[idx + 1], 579, curve=False))

    body.append(flow_arrow(800, 412, 835, 516, curve=False))
    body.append(band(286, 700, 1028, 70, "Failure / degradation" if not zh else "失败与降级", [failure], "red"))
    return ("memory-lifecycle-sequence.zh.svg" if zh else "memory-lifecycle-sequence.svg", title, "\n".join(body), 1500, 820)


def data_flow(zh: bool) -> tuple[str, str, str, int, int]:
    if zh:
        title = "数据链路图：Evidence 到 ContextPack"
        desc = "这条链路把对象边界分开：证据不是记忆，索引不是事实，召回候选不是注入内容，反馈事件会反哺治理。"
        chain = [
            ("证据", ["Evidence", "prompt / transcript", "resource refs"], "teal", "evidence"),
            ("长期记忆", ["MemoryItem", "body + meta", "validity"], "green", "memory"),
            ("索引投影", ["Index Projection", "FTS + vector", "refs_graph"], "blue", "store"),
            ("召回候选", ["Retrieved item", "rank / trace", "confidence"], "orange", "recall"),
            ("排序候选", ["Ranked item", "RRF / rerank", "feedback"], "yellow", "observe"),
            ("防火墙候选", ["Firewalled item", "allow demote", "scope stale"], "red", "govern"),
            ("上下文包", ["Context pack", "locator", "overview"], "purple", "loading"),
            ("反馈事件", ["Feedback event", "adopt/reject", "governance"], "indigo", "observe"),
        ]
        sidecars = [
            ("运行账本", ["Runtime Ledger", "adapter events", "adopt/reject feedback"], "yellow", "observe"),
            ("复核队列", ["Review Queue", "candidate approve", "evolve proposals"], "pink", "govern"),
            ("治理回路", ["Governance Loop", "maturity / TTL", "drift / duplicate"], "indigo", "govern"),
        ]
        principle = "分层原则：Evidence 支撑判断，MemoryItem 才是长期事实；Index Projection 可重建；RetrievedItem/RankedItem 只是候选；ContextPack 才是注入包。"
    else:
        title = "Data Flow: Evidence To ContextPack"
        desc = "Every object has a boundary: evidence is not memory, index is not truth, recalled candidate is not injected content, and feedback returns to governance."
        chain = [
            ("Evidence", ["prompt / transcript", "resource / extraction", "path + hash + refs"], "teal", "evidence"),
            ("MemoryItem", ["frontmatter + body", "summary / overview", "validity / sensitivity"], "green", "memory"),
            ("Index Projection", ["FTS / BM25", "vector locator+overview", "refs_graph / metadata"], "blue", "store"),
            ("RetrievedItem", ["rank + trace", "confidence / decay", "feedback / freshness"], "orange", "recall"),
            ("RankedItem", ["RRF + rerank", "feedback + runtime", "temporal state"], "yellow", "observe"),
            ("FirewalledItem", ["include / demote / exclude", "scope / stale / budget", "sensitivity"], "red", "govern"),
            ("ContextPack", ["locator / overview / detail", "detail_uri / CCR", "adapter injection"], "purple", "loading"),
            ("FeedbackEvent", ["adopted / rejected / ignored", "governance loop", "next recall signal"], "indigo", "observe"),
        ]
        sidecars = [
            ("Runtime Ledger", ["adapter events", "adopt/reject feedback"], "yellow", "observe"),
            ("Review Queue", ["candidate approve", "evolve proposals"], "pink", "govern"),
            ("Governance Loop", ["maturity / TTL", "drift / duplicate"], "indigo", "govern"),
        ]
        principle = "Layering: Evidence supports judgment; MemoryItem is durable truth; Index Projection is rebuildable; RetrievedItem/RankedItem is candidate; ContextPack is injected payload."

    body = header(title, desc)
    body.append(t(72, 136, "solid = object transformation" if not zh else "实线 = 对象变形", "small"))
    body.append(t(324, 136, "dashed = governance / feedback sidecar" if not zh else "虚线 = 治理 / 反馈旁路", "small"))

    top_positions = [(54, 168), (284, 168), (514, 168), (744, 168)]
    bottom_positions = [(744, 430), (974, 430), (1204, 430), (1434, 430)]
    positions = top_positions + bottom_positions
    card_w, card_h = 160, 146
    for (x, y), (name, detail, color, icon_name) in zip(positions, chain):
        body.append(compact_card(x, y, card_w, card_h, name, detail, color, icon_name))
    for idx in range(len(top_positions) - 1):
        x, y = top_positions[idx]
        nx, ny = top_positions[idx + 1]
        body.append(flow_arrow(x + card_w, y + card_h // 2, nx, ny + card_h // 2, curve=False))
    body.append(flow_arrow(744 + card_w // 2, 168 + card_h, 744 + card_w // 2, 430, curve=False))
    for idx in range(len(bottom_positions) - 1):
        x, y = bottom_positions[idx]
        nx, ny = bottom_positions[idx + 1]
        body.append(flow_arrow(x + card_w, y + card_h // 2, nx, ny + card_h // 2, curve=False))

    side_positions = [(216, 540), (560, 540), (904, 540)]
    for (x, y), (name, detail, color, icon_name) in zip(side_positions, sidecars):
        body.append(compact_card(x, y, 220, 132, name, detail, color, icon_name))

    body.append(flow_arrow(824, 314, 326, 540, soft=True, planned=True, curve=True))
    body.append(flow_arrow(326, 540, 824, 314, soft=True, planned=True, curve=True))
    body.append(flow_arrow(364, 314, 674, 540, soft=True, planned=True, curve=True))
    body.append(flow_arrow(674, 540, 364, 314, soft=True, planned=True, curve=True))
    body.append(flow_arrow(1514, 576, 1014, 540, soft=True, planned=True, curve=True))
    body.append(flow_arrow(1014, 540, 1514, 576, soft=True, planned=True, curve=True))

    body.append(band(320, 742, 1040, 66, "数据分层" if zh else "Layered flow", [principle], "gray"))
    return ("data-flow.zh.svg" if zh else "data-flow.svg", title, "\n".join(body), 1660, 860)


def retrieval_algorithm_stack(zh: bool) -> tuple[str, str, str, int, int]:
    if zh:
        title = "检索算法栈图：从召回到注入许可"
        desc = "把每个排序因子放在同一条可复算链路上：RRF 只是候选融合，防火墙才决定是否能注入。"
        steps = [
            ("01", "BM25投影", ["FTS / keyword", "rank_bm25"], "green", "recall"),
            ("02", "向量召回", ["locator / overview", "semantic nearest"], "blue", "recall"),
            ("03", "RRF", ["Σ w/(k+rank)", "候选融合"], "orange", "recall"),
            ("04", "元数据短语", ["metadata phrase", "短语命中"], "teal", "observe"),
            ("05", "Rerank", ["optional model", "cross-encoder"], "purple", "observe"),
            ("06", "置信度", ["source quality", "item conf"], "yellow", "govern"),
            ("07", "遗忘衰减", ["retention", "decay coeff"], "red", "govern"),
            ("08", "反馈价值", ["adopted / rejected", "ignored 不升温"], "pink", "observe"),
            ("09", "运行证据", ["handoff / signal", "runtime evidence"], "indigo", "observe"),
            ("10", "时效/废止", ["stale", "supersession"], "red", "govern"),
            ("11", "多样性/联想", ["MMR / Hopfield", "refs_graph expansion"], "purple", "recall"),
            ("12", "防火墙/装载", ["准入 / 降级 / 拒绝", "定位 / 概览 / 详情"], "gray", "loading"),
        ]
        sample = [
            "A README artifact: RRF 0.03252 -> phrase 3.60 -> conf 0.70 -> decay 0.96 -> feedback 1.12 -> include overview",
            "E stale preview: phrase 2.90 -> conf 0.55 -> decay 0.41 -> supersession/stale demote or filter",
            "D unrelated package: SearchFilter removes it before ranking",
            "maturity: governance/context signal, not default live score multiplier",
        ]
    else:
        title = "Retrieval Algorithm Stack: From Recall To Injection Permission"
        desc = "Every ranking factor sits on one recomputable chain: RRF fuses candidates; firewall decides injection permission."
        steps = [
            ("01", "BM25 rank", ["FTS / keyword", "rank_bm25"], "green", "recall"),
            ("02", "Vector rank", ["locator + overview", "semantic nearest"], "blue", "recall"),
            ("03", "RRF", ["Σ w/(k+rank)", "candidate fusion"], "orange", "recall"),
            ("04", "Phrase boost", ["metadata phrase", "phrase hit"], "teal", "observe"),
            ("05", "Rerank", ["optional cross-encoder", "can be disabled"], "purple", "observe"),
            ("06", "Confidence", ["source quality", "item confidence"], "yellow", "govern"),
            ("07", "Forgetting / Decay", ["time_retention", "decay coefficient"], "red", "govern"),
            ("08", "Feedback", ["adopted / rejected", "ignored does not heat up"], "pink", "observe"),
            ("09", "Runtime / Status", ["handoff / signal", "runtime evidence"], "indigo", "observe"),
            ("10", "Freshness gates", ["stale", "supersession"], "red", "govern"),
            ("11", "Diversity / Association", ["MMR / Hopfield", "refs_graph expansion"], "purple", "recall"),
            ("12", "Firewall + Loading", ["include/demote/exclude", "locator/overview/detail"], "gray", "loading"),
        ]
        sample = [
            "A README artifact: RRF 0.03252 -> phrase 3.60 -> conf 0.70 -> decay 0.96 -> feedback 1.12 -> include overview",
            "E stale preview: phrase 2.90 -> conf 0.55 -> decay 0.41 -> supersession/stale demote or filter",
            "D unrelated package: SearchFilter removes it before ranking",
            "maturity: governance/context signal, not default live score multiplier",
        ]

    body = header(title, desc)
    positions = [(54 + (idx % 6) * 274, 164 + (idx // 6) * 230) for idx in range(12)]
    for idx, ((x, y), (num, name, detail, color, icon_name)) in enumerate(zip(positions, steps)):
        body.append(lane_card(x, y, 202, 132, f"{num} {name}", detail, color))
        if idx not in {5, 11}:
            nx, ny = positions[idx + 1]
            if idx == 5:
                continue
            body.append(flow_arrow(x + 202, y + 66, nx, ny + 66, curve=False))
    body.append(flow_arrow(54 + 5 * 274 + 101, 164 + 132, 54 + 101, 394, curve=True))
    body.append(band(230, 664, 1040, 104, "A-E 样例得分链路" if zh else "A-E sample scoring chain", sample, "gray"))
    return ("retrieval-algorithm-stack.zh.svg" if zh else "retrieval-algorithm-stack.svg", title, "\n".join(body), 1660, 820)


def retrieval_complete_flow(zh: bool) -> tuple[str, str, str, int, int]:
    if zh:
        title = "召回完整链路图：从问题到 ContextPack"
        desc = "把执行时序和算法分数合并到一张图：召回只是候选排序，防火墙和分层装载才决定注入内容。"
        sequence = [
            ("01 用户问题", ["prompt / search call"], "teal"),
            ("02 过滤", ["project / type / tags"], "blue"),
            ("03 并行召回", ["BM25 / Vector"], "green"),
            ("04 RRF 融合", ["第一候选池"], "orange"),
            ("05 可信排序", ["rerank / confidence"], "yellow"),
            ("06 时效门禁", ["stale / supersession"], "red"),
            ("07 关联扩展", ["MMR / Hopfield / graph"], "purple"),
            ("08 防火墙", ["include / demote / exclude"], "pink"),
            ("09 上下文包", ["locator / overview", "detail 按需"], "gray"),
        ]
        factors = [
            ("BM25", "FTS 字面排名", "green"),
            ("Vector", "语义邻居", "blue"),
            ("RRF", "Σw/(k+rank)", "orange"),
            ("Phrase", "metadata 短语", "teal"),
            ("Rerank", "可选 cross-encoder", "purple"),
            ("Decay", "0.5^(Δt/h)", "red"),
            ("Feedback", "adopt / reject / gain", "pink"),
            ("MMR", "相关性 + 多样性", "purple"),
            ("Hopfield", "语义吸引子", "indigo"),
            ("Firewall", "scope / safety / budget", "gray"),
        ]
        formula_title = "遗忘曲线"
        formula_lines = [
            "time_retention = 0.5 ^ (days_since_reference / half_life)",
            "decay_coefficient = clamp(retention * access * support * gain * contradiction, 0.01, 1.35)",
            "effective_score = candidate_score * confidence * decay_coefficient",
        ]
        policy = "读取起点是 MemoryItem + Index Projection + Runtime Ledger；最终注入由 ContextFirewall + context_loading 决定。"
    else:
        title = "Complete Retrieval Flow: From Question To ContextPack"
        desc = "Execution order and scoring factors in one diagram: retrieval ranks candidates; firewall and layered loading decide injection."
        sequence = [
            ("01 Question", ["prompt / search call"], "teal"),
            ("02 Filter", ["project / type / tags"], "blue"),
            ("03 Parallel recall", ["BM25 / Vector"], "green"),
            ("04 RRF fusion", ["first candidate pool"], "orange"),
            ("05 Trust ranking", ["rerank / confidence"], "yellow"),
            ("06 Freshness gates", ["stale / supersession"], "red"),
            ("07 Association", ["MMR / Hopfield / graph"], "purple"),
            ("08 Firewall", ["include / demote / exclude"], "pink"),
            ("09 Pack", ["locator / overview", "detail on demand"], "gray"),
        ]
        factors = [
            ("BM25", "FTS lexical rank", "green"),
            ("Vector", "semantic neighbor", "blue"),
            ("RRF", "Σw/(k+rank)", "orange"),
            ("Phrase", "metadata phrase", "teal"),
            ("Rerank", "optional cross-encoder", "purple"),
            ("Decay", "0.5^(Δt/h)", "red"),
            ("Feedback", "adopt / reject / gain", "pink"),
            ("MMR", "relevance + diversity", "purple"),
            ("Hopfield", "semantic attractor", "indigo"),
            ("Firewall", "scope / safety / budget", "gray"),
        ]
        formula_title = "Forgetting curve"
        formula_lines = [
            "time_retention = 0.5 ^ (days_since_reference / half_life)",
            "decay_coefficient = clamp(retention * access * support * gain * contradiction, 0.01, 1.35)",
            "effective_score = candidate_score * confidence * decay_coefficient",
        ]
        policy = "Read starts from MemoryItem + Index Projection + Runtime Ledger; injection is decided by ContextFirewall + context_loading."

    def mini_card(x: int, y: int, w: int, h: int, name: str, detail: list[str], color: str) -> str:
        fill, ink, stroke = PALETTE[color]
        return f"""  <g>
    <rect class="card" x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{fill}" stroke="{stroke}"/>
{lines(x + 16, y + 31, wrap_text(name, max(5.0, (w - 32) / 17)), "card-title", 21)}
{wrapped_lines(x + 16, y + 60, detail, "small", 17, max_units=max(8.0, (w - 32) / 13), max_total_lines=2)}
  </g>"""

    body = header(title, desc)
    body.append(t(74, 138, "1. 执行链路" if zh else "1. Execution chain", "section"))
    seq_w, seq_h = 136, 92
    seq_y = 168
    seq_xs = [58 + i * 160 for i in range(len(sequence))]
    for idx, (x, (name, detail, color)) in enumerate(zip(seq_xs, sequence)):
        body.append(mini_card(x, seq_y, seq_w, seq_h, name, detail, color))
        if idx < len(sequence) - 1:
            body.append(flow_arrow(x + seq_w + 8, seq_y + seq_h // 2, seq_xs[idx + 1] - 8, seq_y + seq_h // 2, curve=False, soft=idx >= 5))

    body.append(t(74, 318, "2. 排序 / 治理因子" if zh else "2. Ranking and governance factors", "section"))
    factor_w, factor_h = 134, 82
    row1 = [(60 + i * 157, 348) for i in range(5)]
    row2 = [(60 + i * 157, 498) for i in range(5)]
    positions = row1 + list(reversed(row2))
    for idx, ((x, y), (name, detail, color)) in enumerate(zip(positions, factors)):
        body.append(lane_card(x, y, factor_w, factor_h, f"{idx + 1:02d} {name}", [detail], color))
        if idx < len(positions) - 1:
            nx, ny = positions[idx + 1]
            if y == ny:
                if nx > x:
                    body.append(flow_arrow(x + factor_w + 8, y + factor_h // 2, nx - 8, ny + factor_h // 2, curve=False, soft=idx >= 5))
                else:
                    body.append(flow_arrow(x - 8, y + factor_h // 2, nx + factor_w + 8, ny + factor_h // 2, curve=False, soft=True))
            else:
                body.append(flow_arrow(x + factor_w // 2, y + factor_h + 10, nx + factor_w // 2, ny - 10, curve=True, soft=True))

    body.append(band(910, 342, 560, 138, formula_title, formula_lines, "gray"))
    body.append(band(260, 642, 980, 72, "关键边界" if zh else "Key boundary", [policy], "gray"))
    return ("retrieval-complete-flow.zh.svg" if zh else "retrieval-complete-flow.svg", title, "\n".join(body), 1560, 760)


def retrieval_scoring_pipeline(zh: bool) -> tuple[str, str, str, int, int]:
    if zh:
        title = "检索评分管线"
        desc = "从 Markdown/raw evidence 到派生索引，再到 BM25/vector/RRF/decay/graph/context loading。"
        cards = [
            ("事实源", ["items/*.md", "raw conversation sidecar", "resources / extractions"], "blue", "store"),
            ("派生索引", ["FTS: title + locator + overview", "Vector: locator + overview", "refs_graph / metadata"], "green", "memory"),
            ("查询信号", ["prompt", "project / agent / time", "scope filters"], "teal", "agent"),
            ("S0 并行召回", ["BM25 + Vector", "RRF: w/(k + rank + 1)", "metadata filters"], "orange", "recall"),
            ("S1 治理排序", ["confidence * retention", "feedback / status / runtime", "freshness guard"], "yellow", "govern"),
            ("注入前治理", ["graph / MMR", "context firewall", "locator / overview / detail"], "purple", "loading"),
        ]
        note = "原始对话和资源是 evidence sidecar；向量派生文本只使用 locator + overview，detail/body 按需读取。"
    else:
        title = "Retrieval Scoring Pipeline"
        desc = "From Markdown/raw evidence to derived indexes, then BM25/vector/RRF/decay/graph/context loading."
        cards = [
            ("Truth sources", ["items/*.md", "raw conversation sidecar", "resources / extractions"], "blue", "store"),
            ("Derived index", ["FTS: title + locator + overview", "Vector: locator + overview", "refs_graph / metadata"], "green", "memory"),
            ("Query signal", ["prompt", "project / agent / time", "scope filters"], "teal", "agent"),
            ("S0 parallel recall", ["BM25 + Vector", "RRF: w/(k + rank + 1)", "metadata filters"], "orange", "recall"),
            ("S1 governed rank", ["confidence * retention", "feedback / status / runtime", "freshness guard"], "yellow", "govern"),
            ("Pre-injection gates", ["graph / MMR", "context firewall", "locator / overview / detail"], "purple", "loading"),
        ]
        note = "Raw conversations and resources are evidence sidecars; vector text uses locator + overview, while detail/body loads on demand."

    body = header(title, desc)
    positions = [(70, 170), (480, 170), (890, 170), (70, 430), (480, 430), (890, 430)]
    for idx, (label, detail, color, icon_name) in enumerate(cards):
        x, y = positions[idx]
        body.append(compact_card(x, y, 320, 152, label, detail, color, icon_name))
    body.extend([
        straight_arrow(390, 246, 480, 246),
        straight_arrow(800, 246, 890, 246),
        curved_arrow(1050, 322, 230, 430),
        straight_arrow(390, 506, 480, 506),
        straight_arrow(800, 506, 890, 506),
    ])
    body.append(band(210, 650, 860, 64, "边界" if zh else "Boundary", [note], "gray"))
    return (
        "retrieval-scoring-pipeline.zh.svg" if zh else "retrieval-scoring-pipeline.svg",
        title,
        "\n".join(body),
        1280,
        760,
    )


def retrieval_score_waterfall(zh: bool) -> tuple[str, str, str, int, int]:
    if zh:
        title = "检索评分瀑布图"
        desc = "可解释评分从 RRF 基础分开始，经治理因子逐步得到最终排序分。"
        formulas = [
            "RRF(d)=Σs ws/(k+rank_s(d)+1)",
            "S_effective=S_rrf×confidence×decay_coefficient",
            "decay_coefficient=clamp(retention×access×support×gain×contradiction,0.01,1.35)",
            "maturity 是治理/分层元数据，不是 SearchEngine 实时排序乘子",
        ]
        labels = ["S0 RRF", "置信度", "遗忘衰减", "反馈价值", "状态/运行时", "过滤门"]
        note = "示例：相关候选先排序，再被 temporal/supersession/filter/firewall 过滤或降级；maturity 由治理链路计算和写回。"
    else:
        title = "Retrieval Score Waterfall"
        desc = "Explainable ranking starts from RRF and applies governance factors step by step."
        formulas = [
            "RRF(d)=Σs ws/(k+rank_s(d)+1)",
            "S_effective=S_rrf×confidence×decay_coefficient",
            "decay_coefficient=clamp(retention×access×support×gain×contradiction,0.01,1.35)",
            "maturity is governance/context metadata, not a SearchEngine rank multiplier",
        ]
        labels = ["S0 RRF", "confidence", "decay", "feedback", "status/runtime", "filters"]
        note = "Example: relevant candidates are ranked, then temporal/supersession/filter/firewall gates include, demote, or exclude them."

    body = header(title, desc)
    body.append(band(90, 142, 1100, 126, "Ranking formula" if not zh else "排序公式", formulas, "gray"))
    base_y = 610
    x = 128
    widths = [130, 112, 94, 126, 154, 96]
    heights = [310, 278, 248, 292, 328, 260]
    colors = ["blue", "green", "teal", "orange", "purple", "yellow"]
    running_x = x
    for idx, label in enumerate(labels):
        fill, ink, stroke = PALETTE[colors[idx]]
        w = widths[idx]
        h = heights[idx]
        y = base_y - h
        body.append(f'  <rect x="{running_x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>')
        body.append(t(running_x + 16, y + 32, label, "card-title", ink))
        body.append(t(running_x + 18, y + h - 18, f"{h / 320:.2f}x", "mono-small", ink))
        if idx < len(labels) - 1:
            body.append(straight_arrow(running_x + w + 8, y + h // 2, running_x + w + 90, base_y - heights[idx + 1] // 2))
        running_x += w + 100
    body.append(band(210, 656, 1000, 82, "治理边界" if zh else "Governance boundary", [note], "gray"))
    return (
        "retrieval-score-waterfall.zh.svg" if zh else "retrieval-score-waterfall.svg",
        title,
        "\n".join(body),
        1420,
        760,
    )


def retrieval_ablation_matrix(zh: bool) -> tuple[str, str, str, int, int]:
    if zh:
        title = "检索消融矩阵"
        desc = "每个排序轴都可以被 benchmark 单独打开、关闭和比较。"
        cols = ["BM25", "向量", "RRF", "衰减", "图", "MMR", "CF"]
        rows = [
            ("bm25_only", [1, 0, 0, 0, 0, 0, 0], "精确关键词基线"),
            ("vector_only", [0, 1, 0, 0, 0, 0, 0], "语义近邻基线"),
            ("rrf", [1, 1, 1, 0, 0, 0, 0], "默认融合骨架"),
            ("rrf_decay", [1, 1, 1, 1, 0, 0, 0], "时间/保留因子"),
            ("rrf_graph", [1, 1, 1, 0, 1, 0, 0], "显式 refs 关联"),
            ("rrf_mmr", [1, 1, 1, 0, 0, 1, 0], "去冗余多样性"),
            ("rrf_context_firewall", [1, 1, 1, 0, 0, 0, 1], "注入前安全治理"),
        ]
        note = "指标：MRR / P@5 / R@10 / NDCG@10 / token cost / stale hit rate"
    else:
        title = "Retrieval Ablation Matrix"
        desc = "Every ranking axis must be independently switchable and measurable by benchmark."
        cols = ["BM25", "Vector", "RRF", "Decay", "Graph", "MMR", "CF"]
        rows = [
            ("bm25_only", [1, 0, 0, 0, 0, 0, 0], "exact lexical baseline"),
            ("vector_only", [0, 1, 0, 0, 0, 0, 0], "semantic-neighbor baseline"),
            ("rrf", [1, 1, 1, 0, 0, 0, 0], "default fusion skeleton"),
            ("rrf_decay", [1, 1, 1, 1, 0, 0, 0], "time/retention factor"),
            ("rrf_graph", [1, 1, 1, 0, 1, 0, 0], "explicit refs association"),
            ("rrf_mmr", [1, 1, 1, 0, 0, 1, 0], "diversity reranking"),
            ("rrf_context_firewall", [1, 1, 1, 0, 0, 0, 1], "pre-injection safety gate"),
        ]
        note = "Metrics: MRR / P@5 / R@10 / NDCG@10 / token cost / stale hit rate"

    body = header(title, desc)
    table_x, table_y = 82, 160
    label_w, cell_w, row_h = 190, 88, 62
    body.append(f'  <rect class="panel" x="{table_x}" y="{table_y}" width="1116" height="520" rx="22"/>')
    body.append(t(table_x + 30, table_y + 50, "variant", "section"))
    for idx, col in enumerate(cols):
        body.append(t(table_x + label_w + idx * cell_w + 32, table_y + 50, col, "section"))
    body.append(t(table_x + label_w + len(cols) * cell_w + 34, table_y + 50, "purpose", "section"))
    for ridx, (name, flags, purpose) in enumerate(rows):
        y = table_y + 76 + ridx * row_h
        body.append(f'  <path class="branch" d="M{table_x + 20} {y - 22} H{table_x + 1090}"/>')
        body.append(t(table_x + 30, y + 5, name, "mono"))
        for cidx, enabled in enumerate(flags):
            color = "green" if enabled else "gray"
            fill, ink, stroke = PALETTE[color]
            cx = table_x + label_w + cidx * cell_w + 54
            body.append(f'  <circle cx="{cx}" cy="{y}" r="15" fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>')
            body.append(t(cx - 5, y + 5, "1" if enabled else "0", "tiny", ink))
        body.append(t(table_x + label_w + len(cols) * cell_w + 30, y + 5, purpose, "card-line"))
    body.append(band(170, 672, 940, 82, "Benchmark gate" if not zh else "评测门禁", [note], "gray"))
    return (
        "retrieval-ablation-matrix.zh.svg" if zh else "retrieval-ablation-matrix.svg",
        title,
        "\n".join(body),
        1280,
        760,
    )


def retrieval_token_curve(zh: bool) -> tuple[str, str, str, int, int]:
    if zh:
        title = "上下文分层与 token 曲线"
        desc = "检索命中后先给 locator，再按策略升到 overview/detail，避免把大脑变成上下文垃圾场。"
        levels = [
            ("locator", "L0/raw", "最小定位信息", "低成本 / 中等召回", 92, "green"),
            ("overview", "L1/consolidated", "可复用概览", "中成本 / 更高召回", 210, "blue"),
            ("detail", "evidence/body", "按需读取正文证据", "高成本 / 最高证据", 430, "orange"),
        ]
        note = "raw / consolidated / skill 是成熟度治理层；locator / overview / detail 是上下文装载层，二者相关但不是一回事。"
    else:
        title = "Context Layers And Token Curve"
        desc = "After recall, load locator first, escalate to overview/detail by policy, and avoid context dumping."
        levels = [
            ("locator", "L0/raw", "minimal positioning", "low cost / medium recall", 92, "green"),
            ("overview", "L1/consolidated", "reusable summary", "medium cost / higher recall", 210, "blue"),
            ("detail", "evidence/body", "body evidence on demand", "high cost / highest evidence", 430, "orange"),
        ]
        note = "raw / consolidated / skill is maturity governance; locator / overview / detail is context loading. They are related, not identical."

    body = header(title, desc)
    axis_x, axis_y = 126, 610
    body.append(f'  <path class="branch" d="M{axis_x} {axis_y} H1120"/>')
    body.append(f'  <path class="branch" d="M{axis_x} {axis_y} V170"/>')
    body.append(t(82, 176, "tokens", "small"))
    body.append(t(1060, 642, "context depth" if not zh else "上下文深度", "small"))
    body.append('  <path fill="none" stroke="#0969da" stroke-width="4" stroke-linecap="round" d="M160 560 C 360 535, 520 450, 690 322 C 820 224, 960 190, 1100 180"/>')
    points = [(250, 528), (595, 372), (990, 196)]
    for idx, (label, layer, detail, quality, token, color) in enumerate(levels):
        x, y = points[idx]
        fill, ink, stroke = PALETTE[color]
        body.append(f'  <circle cx="{x}" cy="{y}" r="18" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
        body.append(compact_card(x - 120, y + 36, 260, 128, label, [layer, detail, quality, f"~{token} token units"], color, "loading"))
    body.append(band(202, 656, 876, 82, "分层边界" if zh else "Layer boundary", [note], "gray"))
    return (
        "retrieval-token-curve.zh.svg" if zh else "retrieval-token-curve.svg",
        title,
        "\n".join(body),
        1280,
        760,
    )


def amh_engineering_capability_map(zh: bool) -> tuple[str, str, str, int, int]:
    if zh:
        title = "工程能力链路地图"
        desc = "从事实声明到证据、写入、投影、召回、压缩、防火墙、门禁、adapter verified gate 和交付文档。"
        modules = [
            ("01", "事实面", ["MCP 28 / Web 91 locked", "16 adapters / verified=11 local"], "gray", "observe"),
            ("02", "证据采集", ["conversation snapshot", "resources / extractions"], "teal", "evidence"),
            ("03", "写入漏斗", ["WriteService + audit", "bulk/import 待收敛"], "blue", "memory"),
            ("04", "事实源", ["items/mem-*.md", "refs + context views"], "green", "store"),
            ("05", "索引投影", ["FTS / vec / graph", "可从 Markdown 重建"], "green", "store"),
            ("06", "召回解释", ["BM25 + vector + RRF", "trace + resource context"], "orange", "recall"),
            ("07", "上下文经济", ["context_pack + detail_uri", "Headroom / CCR"], "purple", "loading"),
            ("08", "治理防火墙", ["scope / stale / sensitivity", "review / evolve"], "red", "govern"),
            ("09", "质量门禁", ["retrieval/compression", "ML advisory / release gate"], "pink", "observe"),
            ("10", "Agent 复用", ["hooks / MCP / file / provider", "adapter verify evidence"], "indigo", "adapter"),
        ]
    else:
        title = "Engineering Capability Loop"
        desc = "The real loop from truth claims to evidence, writes, projections, retrieval, compression, firewall, gates, adapter verified evidence, and docs."
        modules = [
            ("01", "Fact surface", ["MCP 28 / Web 91 locked", "16 adapters / verified=11 local"], "gray", "observe"),
            ("02", "Evidence capture", ["conversation snapshot", "resources / extractions"], "teal", "evidence"),
            ("03", "Write funnel", ["WriteService + audit", "bulk/import convergence gap"], "blue", "memory"),
            ("04", "Truth source", ["items/mem-*.md", "refs + context views"], "green", "store"),
            ("05", "Index projection", ["FTS / vec / graph", "rebuildable from Markdown"], "green", "store"),
            ("06", "Retrieval trace", ["BM25 + vector + RRF", "trace + resource context"], "orange", "recall"),
            ("07", "Context economy", ["context_pack + detail_uri", "Headroom / CCR"], "purple", "loading"),
            ("08", "Governance firewall", ["scope / stale / sensitivity", "review / evolve"], "red", "govern"),
            ("09", "Quality gates", ["retrieval/compression", "ML advisory / release gate"], "pink", "observe"),
            ("10", "Agent reuse", ["hooks / MCP / file / provider", "adapter verify evidence"], "indigo", "adapter"),
        ]

    body = header(title, desc)
    x0, y0 = 44, 150
    card_w, card_h = 228, 132
    gap_x, gap_y = 70, 86
    for idx, (number, name, detail, color, icon_name) in enumerate(modules):
        row = idx // 5
        col = idx % 5
        x = x0 + col * (card_w + gap_x)
        y = y0 + row * (card_h + gap_y)
        body.append(boundary_card(x, y, card_w, card_h, f"{number} {name}", detail, color, icon_name))
        if col < 4:
            body.append(flow_arrow(x + card_w, y + card_h // 2, x + card_w + gap_x, y + card_h // 2, curve=False))
        elif row == 0:
            body.append(flow_arrow(x + card_w // 2, y + card_h, x0 + card_w // 2, y + card_h + gap_y, soft=True, curve=True))
    return (
        "amh-engineering-capability-map.zh.svg" if zh else "amh-engineering-capability-map.svg",
        title,
        "\n".join(body),
        1500,
        640,
    )


def amh_dynamic_architecture(zh: bool) -> tuple[str, str, str, int, int]:
    if zh:
        title = "可信上下文操作系统总览"
        desc = "原始对话和资源留证据，长期结论进 MemoryItem；索引可重建，检索可解释，压缩可逆，注入要过防火墙。"
        labels = {
            "native": "原生会话",
            "native_lines": ["Codex / Claude / Qoder", "各自保留 transcript", "AMH 记 path/offset/hash"],
            "bridge": "证据与来源桥",
            "bridge_lines": ["会话导入 / 快照", "path / offset / hash", "消息冷热分层"],
            "conv": "原始对话证据",
            "conv_lines": ["sources/conversations/*", "messages.jsonl + metadata", "默认不进入注入上下文"],
            "resource": "资料与抽取旁路",
            "resource_lines": ["resources/*.json", "extractions/*.json", "证据 sidecar，不是事实源"],
            "write": "写入漏斗",
            "write_lines": ["CLI / MCP / Web", "approve -> WriteService", "bulk/import 待收敛"],
            "item": "长期记忆",
            "item_lines": ["items/mem-*.md", "frontmatter + body", "locator / overview / detail"],
            "index": "派生索引",
            "index_lines": ["index.db：meta / FTS / vec", "refs_graph 显式关联", "可从 Markdown 重建"],
            "recall": "召回与 trace",
            "recall_lines": ["BM25 + vector -> RRF", "resource context", "decay / feedback / fresh"],
            "context": "context_pack 注入",
            "context_lines": ["locator / overview / detail", "Headroom / CCR", "firewall 通过才注入"],
            "runtime": "运行账本",
            "runtime_lines": ["适配器事件", "adapter verify", "verified=11 local"],
            "product": "读模型与门禁",
            "product_lines": ["Cockpit / onboarding", "memory candidates", "compression / ML gate"],
            "solid": "实线：已落地",
            "dash": "虚线：可选 / 待补强 / 样例依赖",
        }
    else:
        title = "AMH Trusted Context OS"
        desc = "Raw conversations/resources stay evidence; conclusions become MemoryItems; indexes rebuild; retrieval explains ranking; injection is firewalled."
        labels = {
            "native": "Native sessions",
            "native_lines": ["Codex / Claude / Qoder", "native transcript stays", "AMH keeps path/hash"],
            "bridge": "Evidence / provenance bridge",
            "bridge_lines": ["conversation ingest", "path / offset / sha256", "message retention tier"],
            "conv": "raw conversation evidence",
            "conv_lines": ["sources/conversations/*", "messages.jsonl + metadata", "not injected by default"],
            "resource": "resource / extraction sidecar",
            "resource_lines": ["resources/*.json", "extractions/*.json", "evidence sidecar, not truth"],
            "write": "write funnel",
            "write_lines": ["CLI / MCP / Web", "approve -> WriteService", "bulk/import gap"],
            "item": "MemoryItem",
            "item_lines": ["items/mem-*.md", "frontmatter + body", "locator / overview / detail"],
            "index": "derived index",
            "index_lines": ["index.db: meta / FTS / vec", "refs_graph from refs", "rebuildable from Markdown"],
            "recall": "retrieval + trace",
            "recall_lines": ["BM25 + vector -> RRF", "resource context", "decay / feedback / fresh"],
            "context": "context_pack injection",
            "context_lines": ["locator / overview / detail", "Headroom / CCR", "firewall before inject"],
            "runtime": "runtime ledger",
            "runtime_lines": ["adapter-events.jsonl", "adapter verify", "verified=11 local"],
            "product": "read models + gates",
            "product_lines": ["Cockpit / onboarding", "memory candidates", "compression / ML gate"],
            "solid": "solid: implemented",
            "dash": "dashed: optional / planned / sample-dependent",
        }

    body = header(title, desc)
    solid_w = chip_width(labels["solid"], min_width=88, padding=52)
    body.append(status_chip(64, 126, labels["solid"], "green"))
    body.append(status_chip(64 + solid_w + 22, 126, labels["dash"], "red", dashed=True))
    body.append(boundary_card(50, 168, 250, 160, labels["native"], labels["native_lines"], "gray", "docs", badge="source"))
    body.append(boundary_card(380, 168, 250, 160, labels["bridge"], labels["bridge_lines"], "teal", "evidence", badge="shipped"))
    body.append(boundary_card(710, 126, 250, 158, labels["conv"], labels["conv_lines"], "orange", "store", badge="shipped"))
    body.append(boundary_card(710, 322, 250, 138, labels["resource"], labels["resource_lines"], "red", "evidence", dashed=True, badge="sidecar"))
    body.append(boundary_card(50, 482, 250, 150, labels["write"], labels["write_lines"], "yellow", "install", badge="write"))
    body.append(boundary_card(380, 462, 250, 170, labels["item"], labels["item_lines"], "green", "memory", badge="truth"))
    body.append(boundary_card(710, 482, 250, 150, labels["index"], labels["index_lines"], "blue", "store", badge="derived"))
    body.append(boundary_card(1040, 482, 220, 150, labels["recall"], labels["recall_lines"], "purple", "recall", badge="rank"))
    body.append(boundary_card(1040, 214, 220, 168, labels["context"], labels["context_lines"], "indigo", "loading", badge="policy"))
    body.append(boundary_card(50, 712, 250, 96, labels["runtime"], labels["runtime_lines"], "pink", "observe", badge="ledger"))
    body.append(boundary_card(380, 712, 250, 96, labels["product"], labels["product_lines"], "teal", "observe", badge="shipped"))
    body.append(flow_arrow(300, 248, 380, 248, curve=False))
    body.append(flow_arrow(630, 230, 710, 205, curve=True))
    body.append(flow_arrow(630, 266, 710, 391, planned=True, curve=True))
    body.append(flow_arrow(300, 557, 380, 547, curve=False))
    body.append(flow_arrow(630, 547, 710, 557, curve=False))
    body.append(flow_arrow(960, 557, 1040, 557, curve=False))
    body.append(flow_arrow(1150, 482, 1150, 382, curve=False))
    body.append(flow_arrow(175, 632, 175, 712, soft=True, curve=False))
    body.append(flow_arrow(505, 632, 505, 712, soft=True, curve=False))
    body.append(
        f"""  <circle class="flow-dot" r="7">
    <animateMotion dur="5.5s" repeatCount="indefinite" path="M300 248 L380 248 C 520 248, 600 230, 710 205"/>
  </circle>"""
    )
    body.append(
        f"""  <circle class="flow-dot" r="6" fill="#0969da">
    <animateMotion dur="6.2s" repeatCount="indefinite" path="M300 557 L710 557 L1040 557 C 1150 557, 1150 482, 1150 382"/>
  </circle>"""
    )
    return (
        "amh-dynamic-architecture.zh.svg" if zh else "amh-dynamic-architecture.svg",
        title,
        "\n".join(body),
        1380,
        880,
    )


def amh_storage_lifecycle(zh: bool) -> tuple[str, str, str, int, int]:
    if zh:
        title = "存储介质、旁路与投影"
        desc = "把 Markdown 事实源、resource evidence、review sidecar、runtime ledger、pending queue 和 index.db 的责任拆开。"
        cards = {
            "entry": ("外部写入口", ["CLI / MCP / Web / hooks", "候选 approve", "Shell fallback"], "blue", "adapter"),
            "service": ("WriteService", ["schema + audit", "field enrichment", "single funnel target"], "yellow", "govern"),
            "items": ("Markdown 事实源", ["items/mem-*.md", "refs / validity / retention", "detail 按需读取"], "green", "memory"),
            "index": ("索引投影库", ["meta / FTS / vec", "refs_graph", "可从事实源重建"], "blue", "store"),
            "pending": ("兜底队列", ["核心或索引不可用时缓冲", "不丢写入", "后续可回放修复"], "orange", "observe"),
            "runtime": ("运行/验证账本", ["runtime/*.jsonl", "adapter events", "verified evidence gate"], "pink", "observe"),
            "conversation": ("原始对话证据", ["messages.jsonl", "path / offset / hash", "默认不注入"], "teal", "evidence"),
            "resource": ("Resource sidecar", ["resources/*.json", "extractions/*.json", "diagnostics/progressive read"], "red", "evidence"),
            "graph": ("review / refs 图", ["review candidates", "refs.mems / commits", "provenance 不是自动触发"], "purple", "recall"),
            "boundary": ("待治理边界", ["bulk import/internal writes", "需继续收敛 WriteService", "虚线表达旁路/待补强"], "gray", "docs"),
        }
    else:
        title = "Storage Media, Sidecars, Projections"
        desc = "Separates Markdown truth, resource evidence, review sidecars, runtime ledger, pending queue, and index.db responsibilities."
        cards = {
            "entry": ("External write entry", ["CLI / MCP / Web / hooks", "candidate approval", "Shell fallback"], "blue", "adapter"),
            "service": ("WriteService", ["schema + audit", "field enrichment", "single funnel target"], "yellow", "govern"),
            "items": ("Markdown truth", ["items/mem-*.md", "refs / validity / retention", "detail on demand"], "green", "memory"),
            "index": ("Index projection", ["meta / FTS / vec", "refs_graph", "rebuildable from truth"], "blue", "store"),
            "pending": ("pending/*.jsonl", ["durable fallback", "core/index unavailable", "repair can replay"], "orange", "observe"),
            "runtime": ("runtime/verify ledger", ["runtime/*.jsonl", "adapter events", "verified evidence gate"], "pink", "observe"),
            "conversation": ("conversation evidence", ["messages.jsonl", "path / offset / hash", "not injected by default"], "teal", "evidence"),
            "resource": ("Resource sidecar", ["resources/*.json", "extractions/*.json", "diagnostics/progressive read"], "red", "evidence"),
            "graph": ("review / refs graph", ["review candidates", "refs.mems / commits", "provenance is not trigger"], "purple", "recall"),
            "boundary": ("Governance boundary", ["bulk import/internal writes", "need WriteService convergence", "dashed means sidecar/gap"], "gray", "docs"),
        }

    body = header(title, desc)
    body.append(boundary_card(50, 150, 250, 142, *cards["entry"]))
    body.append(boundary_card(380, 150, 250, 142, *cards["service"]))
    body.append(boundary_card(710, 132, 250, 160, *cards["items"], badge="truth"))
    body.append(boundary_card(1040, 132, 240, 160, *cards["index"], badge="derived"))
    body.append(boundary_card(50, 405, 250, 150, *cards["conversation"], badge="shipped"))
    body.append(boundary_card(380, 405, 250, 150, *cards["resource"], dashed=True, badge="sidecar"))
    body.append(boundary_card(710, 405, 250, 150, *cards["graph"], badge="mixed"))
    body.append(boundary_card(1040, 405, 240, 150, *cards["runtime"], badge="ledger"))
    body.append(flow_arrow(300, 221, 380, 221, curve=False))
    body.append(flow_arrow(630, 221, 710, 212, curve=False))
    body.append(flow_arrow(960, 212, 1040, 212, curve=False))
    body.append(flow_arrow(175, 292, 175, 405, soft=True, planned=True, curve=True))
    body.append(flow_arrow(505, 292, 505, 405, soft=True, planned=True, curve=True))
    body.append(flow_arrow(835, 292, 835, 405, soft=True, curve=True))
    body.append(flow_arrow(960, 480, 1040, 480, soft=True, curve=False))
    body.append(band(210, 600, 960, 62, "Storage boundary" if not zh else "存储边界", [
        "Conversation/resource evidence can support future MemoryItems, but only approved MemoryItems become default injectable knowledge."
        if not zh
        else "原始对话和资源 evidence 可以支持未来 MemoryItem，但只有通过审批/写漏斗的 MemoryItem 才进入默认可注入知识。"
    ], "gray"))
    return (
        "amh-storage-lifecycle.zh.svg" if zh else "amh-storage-lifecycle.svg",
        title,
        "\n".join(body),
        1380,
        720,
    )


def amh_retrieval_context_pipeline(zh: bool) -> tuple[str, str, str, int, int]:
    if zh:
        title = "检索、压缩与注入门禁"
        desc = "不是“搜到就塞进上下文”：候选要可解释，正文要按需装载，压缩要可逆，注入要过防火墙。"
        cards = [
            ("用户问题", ["prompt/API", "project/cwd", "q_signal"], "teal", "agent"),
            ("元数据过滤", ["type/proj/tags", "tenant/since", "allowed_ids"], "gray", "govern"),
            ("全文召回", ["BM25 / FTS", "title + locator", "overview"], "green", "recall"),
            ("向量召回", ["locator+ovw", "BM25 fallback", "resource sidecar"], "blue", "recall"),
            ("RRF + rerank", ["RRF fusion", "optional rerank", "trace"], "orange", "recall"),
            ("治理排序", ["conf x decay", "feedback", "runtime/stale"], "yellow", "govern"),
            ("关联扩展", ["MMR", "Hopfield", "refs_graph"], "red", "observe"),
            ("分层装载", ["firewall", "loc/ovw/detail", "detail_uri/CCR"], "purple", "loading"),
            ("注入出口", ["context_pack", "hooks 或 MCP", "feedback ledger"], "indigo", "adapter"),
        ]
        notes = [
            "RRF -> S_effective(score) -> ContextFirewall(include/demote/exclude)；compression/ML 仍走 gate/advisory。",
        ]
    else:
        title = "Retrieval, Compression, Injection Gates"
        desc = "Candidates must be explainable, body text loads on demand, compression must be reversible, and injection is firewalled."
        cards = [
            ("User question", ["prompt/API", "project/cwd", "q_signal"], "teal", "agent"),
            ("Metadata filter", ["type/proj/tags", "tenant/since", "allowed_ids"], "gray", "govern"),
            ("Full-text recall", ["BM25 / FTS", "title + locator", "overview"], "green", "recall"),
            ("Vector recall", ["locator+ovw", "BM25 fallback", "resource sidecar"], "blue", "recall"),
            ("RRF + rerank", ["RRF fusion", "optional rerank", "trace"], "orange", "recall"),
            ("Governed rank", ["conf x decay", "feedback", "runtime/stale"], "yellow", "govern"),
            ("Associative expand", ["MMR", "Hopfield", "refs_graph"], "red", "observe"),
            ("Layered loading", ["firewall", "loc/ovw/detail", "detail_uri/CCR"], "purple", "loading"),
            ("Injection exit", ["context_pack", "hooks or MCP", "feedback ledger"], "indigo", "adapter"),
        ]
        notes = [
            "RRF -> S_effective(score) -> ContextFirewall(include/demote/exclude); compression/ML stay behind gates/advisory.",
        ]

    body = header(title, desc)
    positions = [
        (50, 150), (370, 150), (690, 96), (690, 284), (1030, 190),
        (50, 500), (370, 500), (690, 500), (1030, 500),
    ]
    for (x, y), (label, detail, color, icon_name) in zip(positions, cards):
        dashed = label in {"ML advisory 旁路", "ML advisory sidecar"}
        card_w = 240 if x < 1030 else 260
        body.append(boundary_card(x, y, card_w, 142, label, detail, color, icon_name, dashed=dashed, badge="optional" if dashed else None))
    body.append(flow_arrow(290, 221, 370, 221, curve=False))
    body.append(flow_arrow(610, 205, 690, 167, curve=True))
    body.append(flow_arrow(610, 237, 690, 355, curve=True))
    body.append(flow_arrow(930, 167, 1030, 261, curve=True))
    body.append(flow_arrow(930, 355, 1030, 261, curve=True))
    body.append(flow_arrow(1160, 332, 165, 500, curve=True))
    body.append(flow_arrow(290, 571, 370, 571, planned=True, curve=False))
    body.append(flow_arrow(610, 571, 690, 571, curve=False))
    body.append(flow_arrow(930, 571, 1030, 571, curve=False))
    body.append(
        f"""  <circle class="flow-dot" r="7">
    <animateMotion dur="4.8s" repeatCount="indefinite" path="M50 221 L370 221 C 520 221, 590 167, 690 167 C 860 167, 950 261, 1030 261"/>
  </circle>"""
    )
    body.append(band(195, 658, 980, 88, "Scoring skeleton" if not zh else "评分骨架", notes, "gray"))
    return (
        "amh-retrieval-context-pipeline.zh.svg" if zh else "amh-retrieval-context-pipeline.svg",
        title,
        "\n".join(body),
        1380,
        780,
    )


def amh_adapter_capability_boundary(zh: bool) -> tuple[str, str, str, int, int]:
    if zh:
        title = "智能体接入边界"
        desc = "不同 Agent 的接入能力不相同：hooks、MCP、file context、provider、verified evidence 需要分层展示。"
        rows = [
            ("Codex CLI", ["AGENTS.md", "hooks.json"], "green", "adapter", "hooks+MCP", False, "hooks + MCP"),
            ("Claude Code", ["7 个生命周期 hooks", "settings.json"], "yellow", "adapter", "install-ready", False, "hooks + MCP"),
            ("Qoder", ["hooks settings", "CLI 未登录待验证"], "yellow", "adapter", "install-ready", False, "hooks"),
            ("QoderWork", ["awareness/main + MCP", "GUI context 已证"], "green", "adapter", "verified", False, "hooks+MCP"),
            ("OpenClaw", ["MCP registry", "CLI doctor"], "yellow", "adapter", "install-ready", False, "MCP registry"),
            ("Wukong", ["wukong-cli MCP", "brain_context sidecar"], "yellow", "adapter", "install-ready", False, "scoped MCP"),
            ("Hermes", ["记忆 provider", "search + remember"], "blue", "recall", "provider", False, "provider"),
            ("OpenHuman", ["agentmemory 后端", "config.toml"], "green", "adapter", "verified", False, "agentmemory"),
            ("OpenSquilla", ["TOML MCP server", "config.toml"], "green", "adapter", "verified", False, "TOML MCP"),
        ]
        foot = [
            "当前 AMH 自有生命周期钩子：SessionStart、UserPromptSubmit、Stop、PreCompact、PostCompact、SubagentStart、SubagentStop。",
            "PreToolUse、PostToolUse、PermissionRequest 不是当前 Codex / Claude Code 安装能力。",
            "16 个 adapter 中 11 verified、4 install-ready、1 wip；QoderWork verified 需要 GUI context-effective AMH 证据。",
        ]
    else:
        title = "Adapter Hook Capability Boundary"
        desc = "Agent capabilities are not the same thing: hooks, MCP, file context, provider tools, and verified evidence are separate layers."
        rows = [
            ("Codex CLI", ["AGENTS.md", "hooks.json"], "green", "adapter", "hooks+MCP", False, "hooks + MCP"),
            ("Claude Code", ["7 lifecycle hooks", "settings.json"], "yellow", "adapter", "install-ready", False, "hooks + MCP"),
            ("Qoder", ["hooks settings", "CLI login pending"], "yellow", "adapter", "install-ready", False, "hooks"),
            ("QoderWork", ["awareness/main + MCP", "GUI context passed"], "green", "adapter", "verified", False, "hooks+MCP"),
            ("OpenClaw", ["MCP registry", "CLI doctor"], "yellow", "adapter", "install-ready", False, "MCP registry"),
            ("Wukong", ["wukong-cli MCP", "brain_context sidecar"], "yellow", "adapter", "install-ready", False, "scoped MCP"),
            ("Hermes", ["memory provider", "search + remember"], "blue", "recall", "provider", False, "provider"),
            ("OpenHuman", ["agentmemory backend", "config.toml"], "green", "adapter", "verified", False, "agentmemory"),
            ("OpenSquilla", ["TOML MCP server", "config.toml"], "green", "adapter", "verified", False, "TOML MCP"),
        ]
        foot = [
            "Current AMH-owned lifecycle hooks: SessionStart, UserPromptSubmit, Stop, PreCompact, PostCompact, SubagentStart, SubagentStop.",
            "PreToolUse / PostToolUse / PermissionRequest are not current Codex/Claude Code install capabilities.",
            "16 adapters: 11 verified, 4 install-ready, 1 wip; QoderWork verified requires context-effective AMH evidence.",
        ]

    body = header(title, desc)
    card_w = 310
    card_h = 136
    positions = [
        (50, 130), (435, 130), (820, 130),
        (50, 310), (435, 310), (820, 310),
        (50, 490), (435, 490), (820, 490),
    ]
    for (x, y), (name, detail, color, icon_name, badge, dashed, mode) in zip(positions, rows):
        body.append(boundary_card(x, y, card_w, card_h, name, detail, color, icon_name, dashed=dashed, badge=badge))
        center = x + card_w // 2
        chip_w = chip_width(mode, min_width=96, padding=32)
        chip_x = center - chip_w // 2
        chip_y = y + card_h + 16
        body.append(pill(chip_x, chip_y, chip_w, 28, mode, color))
    hook_y = 714
    hook_label = "AMH 自有 hook 词表" if zh else "AMH-owned hook vocabulary"
    hooks = ["SessionStart", "UserPromptSubmit", "Stop", "PreCompact", "PostCompact", "SubagentStart", "SubagentStop"]
    hook_widths = [chip_width(hook, min_width=82, padding=32) for hook in hooks]
    body.append(f"""  <g>
    <rect x="50" y="680" width="1080" height="74" rx="18" fill="#ffffff" stroke="#d0d7de" stroke-width="1.2" opacity="0.92"/>
    <text class="small" x="72" y="702" fill="#57606a">{escape(hook_label)}</text>
  </g>""")
    x = 62
    for idx, (hook, hook_w) in enumerate(zip(hooks, hook_widths)):
        color = ["green", "blue", "orange", "purple", "teal", "yellow", "pink"][idx]
        body.append(pill(x, hook_y, hook_w, 32, hook, color))
        x += hook_w + 12
    body.append(band(80, 780, 1020, 82, "Truth contract" if not zh else "真实性合同", foot, "gray"))
    return (
        "amh-adapter-capability-boundary.zh.svg" if zh else "amh-adapter-capability-boundary.svg",
        title,
        "\n".join(body),
        1180,
        900,
    )


def write_preview_html() -> None:
    sections = [
        ("00", "AMH 可信上下文生命周期图：接入、维护、召回、治理与评估", "amh-loop-layered-architecture.zh.svg", "1500 / 880"),
        ("01", "AMH 总控图：可信上下文操作回路", "amh-operating-loop.zh.svg", "1500 / 980"),
        ("02", "产品架构图：从协作痛点到可信上下文", "product-architecture.zh.svg", "1500 / 820"),
        ("03", "技术架构图：接入、运行时、脑内核与本地数据", "technical-architecture.zh.svg", "1280 / 950"),
        ("04", "维护 + 召回一体时序链路图", "memory-lifecycle-sequence.zh.svg", "1500 / 820"),
        ("05", "数据链路图：Evidence 到 ContextPack", "data-flow.zh.svg", "1500 / 860"),
        ("06", "召回完整链路图：从问题到 ContextPack", "retrieval-complete-flow.zh.svg", "1560 / 760"),
        ("07", "可信上下文操作系统能力地图", "readme-structure-map.zh.svg", "1460 / 1120"),
        ("08", "检索评分公式", "retrieval-score-waterfall.zh.svg", "1280 / 760"),
        ("09", "智能体接入边界", "amh-adapter-capability-boundary.zh.svg", "1180 / 900"),
    ]
    facts = [
        (
            "接口能力面",
            "`MCP 28 tools`、`Web API/WS 91 locked routes`",
            "Web route surface lock 包含 Cockpit、data-flow、memory-lineage、adapter onboarding、memory candidates、Headroom/CCR、compression gate 和 ML/DL advisory gate。",
        ),
        (
            "写入与证据边界",
            "`WriteService` + `ResourceStore`",
            "MemoryItem 走写漏斗；conversation/resource/extraction 是证据旁路，不能画成默认注入事实。",
        ),
        (
            "候选记忆链路",
            "`review/proactive-candidates.jsonl`",
            "generate 只建候选；approve 经 WriteService 写入；reject 只更新 review sidecar。",
        ),
        (
            "适配器真实性",
            "`16 adapters / 11 verified / 4 install-ready / 1 wip`",
            "Claude Code、OpenClaw、Qoder、Wukong 仍为 install-ready；QoderWork 已有 GUI context-effective 证据；MuleRun 仍为 wip。",
        ),
        (
            "检索与上下文经济",
            "`retrieval trace`、`context_pack`、`Headroom/CCR`、`context firewall`",
            "压缩和 ML/DL advisory 都走门禁或旁路，不能直接改默认注入链路。",
        ),
    ]
    facts_html = "\n".join(
        f"""<tr>
<td>{escape(title)}</td>
<td>{html_inline(signal)}</td>
<td>{html_inline(detail)}</td>
</tr>"""
        for title, signal, detail in facts
    )
    index_html = "\n".join(
        f"""<tr>
<td>{escape(num)}</td>
<td>{escape(title)}</td>
<td><a href="./{escape(file)}">{escape(file)}</a></td>
<td><code>{escape(ratio)}</code></td>
</tr>"""
        for num, title, file, ratio in sections
    )
    section_html = "\n".join(
        f"""<h3 id="diagram-{escape(num)}">{escape(num)} {escape(title)}</h3>
<p><a href="./{escape(file)}">{escape(file)}</a> · <code>{escape(ratio)}</code></p>
<figure>
  <img src="./{escape(file)}" alt="{escape(title)}">
  <figcaption>{escape(title)}，可编辑 SVG 源文件。</figcaption>
</figure>"""
        for num, title, file, ratio in sections
    )
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agent Memory Hub 动态架构图预览</title>
  <link rel="stylesheet" href="readme-preview.css">
  <style>
    .diagram-index td:first-child,
    .diagram-index td:last-child {{
      white-space: nowrap;
    }}
    figcaption {{
      margin-top: -8px;
      color: var(--muted);
      font-size: 14px;
      text-align: center;
    }}
  </style>
</head>
<body>
<h1>Agent Memory Hub</h1>

<blockquote>
  <p><strong>让每一次智能协作，都沉淀为下一次出发。</strong></p>
  <p>一个本地优先、可追溯、可治理的跨智能体可信上下文操作系统。 给 Claude Code、Codex CLI、Cursor、Hermes、Qoder、Wukong、GitHub Copilot 和任何支持 MCP / CLI / hooks 的智能体工具共用。</p>
</blockquote>

<p><a href="../../README.md">English</a> | <a href="../../STRATEGY.md">战略</a> | <a href="../../ROADMAP.md">路线图</a> | <a href="./agent-memory-hub-architecture-map.html">架构图谱</a> | <a href="../architecture.md">架构说明</a></p>

<p><a href="../../LICENSE"><img src="https://img.shields.io/badge/%E8%AE%B8%E5%8F%AF%E8%AF%81-Apache%202.0-blue.svg" alt="许可证：Apache 2.0"></a> <a href="../../.github/workflows/python-tests.yml"><img src="https://img.shields.io/github/actions/workflow/status/%3Cowner%3E/agent-memory-hub/python-tests.yml?branch=main&amp;label=%E6%B5%8B%E8%AF%95" alt="测试"></a> <a href="../../pyproject.toml"><img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python"></a> <a href="https://modelcontextprotocol.io"><img src="https://img.shields.io/badge/%E5%8D%8F%E8%AE%AE-MCP-purple" alt="协议：MCP"></a> <a href="../../README.zh.md#data-model"><img src="https://img.shields.io/badge/%E5%AD%98%E5%82%A8-%E6%9C%AC%E5%9C%B0%20Markdown-green" alt="本地优先"></a></p>

<h2>动态架构图谱</h2>
<p>这个 HTML 页面按 README 中文预览的首屏和排版节奏展示可编辑 SVG 图谱；图谱源文件仍在 <code>docs/visuals/</code> 下维护。</p>

<h3>本轮事实核准</h3>
<table>
<thead>
<tr><th>事实</th><th>证据/对象</th><th>图中表达边界</th></tr>
</thead>
<tbody>
{facts_html}
</tbody>
</table>

<h3>图谱索引</h3>
<table class="diagram-index">
<thead>
<tr><th>序号</th><th>图谱</th><th>源文件</th><th>画布</th></tr>
</thead>
<tbody>
{index_html}
</tbody>
</table>

<h2>图谱预览</h2>
{section_html}

<h2>维护边界</h2>
<ul>
  <li>图谱不把系统画成原始 transcript 仓库；原始对话、证据指针、MemoryItem、索引和上下文注入分层表达。</li>
  <li><code>resources/</code> 与 <code>extractions/</code> 是已实现 sidecar store；图中表达 storage 能力，不用旧样例数量充当当前事实。</li>
  <li><code>refs.commits</code> 只表达 schema/governance provenance，不表达自动维护能力。</li>
  <li>Claude Code, OpenClaw, Qoder, and Wukong remain install-ready until their current blockers clear; QoderWork is verified only because GUI context-effective AMH evidence exists in the current snapshot.</li>
</ul>
</body>
</html>
"""
    (OUT / "amh-animated-diagrams-preview.html").write_text(html, encoding="utf-8")


def write_architecture_map_html() -> None:
    sections = [
        (
            "layered",
            "01",
            "生命周期图",
            "接入、维护、召回、治理与评估",
            "用蓝图式层板解释 AMH 的真实上下文生命周期：记忆接入层按指定 Agent 顺序铺开，维护层沉淀事实，召回层排序候选，治理层处理反馈、访问和成熟度，评估层区分 AMH 本地基准与 MemoryData 外部横评 loop。",
            "amh-loop-layered-architecture.zh.svg",
        ),
        (
            "operating-loop",
            "02",
            "总控图",
            "AMH 总控图：可信上下文操作回路",
            "AMH 的操作链路：Intent 进入 Query Signal，证据被提炼成 MemoryItem，索引投影支撑召回，算法栈产出候选，防火墙决定是否注入，反馈再回到治理和 Loop。",
            "amh-operating-loop.zh.svg",
        ),
        (
            "product",
            "03",
            "产品架构图",
            "从协作痛点到可信上下文",
            "从使用问题出发：多智能体协作中的重复说明、上下文断裂、过期事实误导，最后落到证据、记忆、召回、注入、反馈和验证这条产品链路。",
            "product-architecture.zh.svg",
        ),
        (
            "technical",
            "04",
            "技术架构图",
            "接入、运行时、脑内核与本地数据",
            "说明代码边界：agent_integrations、agent_runtime_kit、agent_brain、读模型、治理门禁和本地存储各自承担不同责任，不能跨层混画。",
            "technical-architecture.zh.svg",
        ),
        (
            "sequence",
            "05",
            "时序链路图",
            "维护 + 召回一体时序",
            "维护泳道先生成 MemoryItem + Index Projection + Runtime Ledger，召回泳道读取这个交接面后才进入过滤、排序、防火墙和 context_pack。",
            "memory-lifecycle-sequence.zh.svg",
        ),
        (
            "flows",
            "06",
            "数据链路图",
            "Evidence 到 ContextPack",
            "对象变形按这条线走：Evidence -> MemoryItem -> Index Projection -> RetrievedItem -> RankedItem -> FirewalledItem -> ContextPack -> FeedbackEvent。每一步都是不同对象，不应混成一个“召回结果”。",
            "data-flow.zh.svg",
        ),
        (
            "algorithm",
            "07",
            "召回完整链路图",
            "从问题到 ContextPack",
            "把执行时序和候选分数合到同一张图里：用户问题先经过过滤，再进入 BM25/vector、RRF、metadata phrase、rerank、confidence、遗忘曲线、feedback、MMR/Hopfield、ContextFirewall 和 ContextPack。",
            "retrieval-complete-flow.zh.svg",
        ),
    ]
    support = [
        ("结构地图", "readme-structure-map.zh.svg", "主生命周期总览。"),
        ("检索算法栈拆分图", "retrieval-algorithm-stack.zh.svg", "只看排序因子链路。"),
        ("检索评分管线", "retrieval-scoring-pipeline.zh.svg", "算法阶段和分数变化。"),
        ("检索分数瀑布", "retrieval-score-waterfall.zh.svg", "RRF、decay、feedback 的单独解释。"),
        ("适配器边界", "amh-adapter-capability-boundary.zh.svg", "hook、MCP、文件旁路和验证边界。"),
        ("三层能力地图", "amh-metrics-governance-collaboration-map.html", "指标、治理、协作能力视角。"),
    ]
    nav_html = "\n".join(
        f'<a href="#{escape(anchor)}"><span class="index">{escape(number)}</span><span>{escape(short)}</span></a>'
        for anchor, number, short, *_ in sections
    )
    section_html = "\n".join(
        f"""<section id="{escape(anchor)}" class="diagram-section">
  <div class="section-head">
    <div>
      <span class="eyebrow">{escape(number)} {escape(short)}</span>
      <h2>{escape(title)}</h2>
      <p>{escape(desc)}</p>
    </div>
    <a class="open-link" href="./{escape(file)}">打开 SVG</a>
  </div>
  <figure>
    <img src="./{escape(file)}" alt="{escape(title)}">
    <figcaption>{escape(file)} · 可编辑 SVG 源文件</figcaption>
  </figure>
</section>"""
        for anchor, number, short, title, desc, file in sections
    )
    support_html = "\n".join(
        f"""<tr>
  <td>{escape(name)}</td>
  <td><a href="./{escape(file)}">{escape(file)}</a></td>
  <td>{escape(desc)}</td>
</tr>"""
        for name, file, desc in support
    )
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agent Memory Hub 架构图谱</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f4ed;
      --paper: #fffdf8;
      --ink: #20242a;
      --muted: #5d6670;
      --line: #d8dee4;
      --accent: #1f6f78;
      --soft: #eef6f5;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, "PingFang SC", "Microsoft YaHei", sans-serif;
      line-height: 1.58;
    }}
    .layout {{
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr);
      min-height: 100vh;
    }}
    aside {{
      position: sticky;
      top: 0;
      height: 100vh;
      padding: 28px 22px;
      border-right: 1px solid var(--line);
      background: rgba(255, 253, 248, 0.9);
      overflow: auto;
    }}
    .brand-mark {{
      display: inline-grid;
      place-items: center;
      width: 54px;
      height: 54px;
      border-radius: 16px;
      background: var(--accent);
      color: white;
      font-weight: 900;
      letter-spacing: 0;
    }}
    h1 {{ margin: 18px 0 8px; font-size: 26px; line-height: 1.15; }}
    .brand p, .side-note {{ color: var(--muted); font-size: 14px; }}
    nav {{ display: grid; gap: 8px; margin-top: 28px; }}
    nav a {{
      display: grid;
      grid-template-columns: 40px 1fr;
      gap: 10px;
      align-items: center;
      padding: 10px 12px;
      border: 1px solid var(--line);
      border-radius: 12px;
      color: var(--ink);
      text-decoration: none;
      background: #fff;
    }}
    .index {{
      color: var(--accent);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-weight: 800;
    }}
    main {{ padding: 36px clamp(18px, 4vw, 64px) 72px; }}
    .hero, .diagram-section, .facts {{
      max-width: 1320px;
      margin: 0 auto 28px;
      padding: clamp(22px, 3vw, 38px);
      border: 1px solid var(--line);
      border-radius: 24px;
      background: var(--paper);
      box-shadow: 0 18px 42px rgba(31, 35, 40, 0.06);
    }}
    .hero h2 {{ margin: 0 0 14px; font-size: clamp(34px, 5vw, 64px); line-height: 1.02; letter-spacing: 0; }}
    .hero p {{ max-width: 84ch; color: var(--muted); font-size: 18px; }}
    .fact-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-top: 24px;
    }}
    .fact {{
      padding: 14px 16px;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: var(--soft);
    }}
    .fact strong {{ display: block; font-size: 26px; }}
    .fact span {{ color: var(--muted); font-size: 13px; }}
    .section-head {{
      display: flex;
      justify-content: space-between;
      gap: 20px;
      align-items: flex-start;
      margin-bottom: 18px;
    }}
    .eyebrow {{
      color: var(--accent);
      font-size: 13px;
      font-weight: 850;
    }}
    h2 {{ margin: 6px 0 8px; font-size: clamp(24px, 3vw, 38px); line-height: 1.12; }}
    .section-head p {{ max-width: 84ch; margin: 0; color: var(--muted); }}
    .open-link {{
      flex: 0 0 auto;
      color: var(--accent);
      text-decoration: none;
      border: 1px solid #95dcd6;
      border-radius: 999px;
      padding: 8px 14px;
      background: #e7fbf8;
      font-size: 14px;
      font-weight: 750;
    }}
    figure {{ margin: 0; }}
    img {{
      width: 100%;
      height: auto;
      display: block;
      border-radius: 18px;
      border: 1px solid var(--line);
      background: #fff;
    }}
    figcaption {{ margin-top: 10px; color: var(--muted); font-size: 14px; text-align: center; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 14px; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 12px 10px; text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-size: 13px; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    footer {{ max-width: 1320px; margin: 26px auto 0; color: var(--muted); font-size: 14px; }}
    @media (max-width: 900px) {{
      .layout {{ display: block; }}
      aside {{ position: static; height: auto; }}
      .fact-grid {{ grid-template-columns: 1fr 1fr; }}
      .section-head {{ display: block; }}
      .open-link {{ display: inline-block; margin-top: 12px; }}
    }}
    @media (max-width: 520px) {{
      .fact-grid {{ grid-template-columns: 1fr; }}
      main {{ padding-inline: 12px; }}
      .hero, .diagram-section, .facts {{ border-radius: 18px; }}
    }}
  </style>
</head>
<body>
  <div class="layout">
    <aside>
      <div class="brand">
        <div class="brand-mark">AMH</div>
        <h1>可信上下文图谱</h1>
        <p>Agent Memory Hub<br>总控图、产品、技术、时序、数据与算法</p>
      </div>
      <nav aria-label="页面导航">
        {nav_html}
      </nav>
      <div class="side-note">
        最近刷新：2026-06-29。此页由 <code>docs/visuals/generate_readme_diagrams.py</code> 生成；实现证据以代码、测试、CLI 输出和能力真值账本为准。
      </div>
    </aside>
    <main>
      <section class="hero" id="overview">
        <h2>一张生命周期图，一张总控图，五张放大图。</h2>
        <p>这套图谱先用 Loop Engineering 视角解释 AMH 的可信上下文生命周期，再沿着实际链路展开：Intent -> Query Signal/SearchFilter -> Evidence -> Memory Curation -> MemoryItem Truth -> Index Projection -> Retrieval Ranking -> ContextFirewall -> ContextPack -> Feedback/Governance/Loop。</p>
        <div class="fact-grid">
          <div class="fact"><strong>28</strong><span>模型上下文协议 28 个工具</span></div>
          <div class="fact"><strong>91</strong><span>91 条接口/通信路由，含驾驶舱 / 引导 / 候选 / 轨迹</span></div>
          <div class="fact"><strong>16</strong><span>16 个适配器记录：11 个已验证、4 个安装就绪、1 个开发中</span></div>
          <div class="fact"><strong>1546</strong><span>本轮全量测试通过，verified=11 仍需按本机证据理解</span></div>
        </div>
      </section>
      {section_html}
      <section class="facts" id="boundary">
        <h2>事实边界</h2>
        <p>Markdown MemoryItem 是事实源；raw transcript/resource 是证据旁路；index/vector/runtime/review 都是派生或复核层。算法栈解释候选排序；召回不等于注入，注入内容由 ContextFirewall 和 ContextPack 处理。</p>
        <table>
          <thead><tr><th>辅助图</th><th>文件</th><th>用途</th></tr></thead>
          <tbody>
            {support_html}
          </tbody>
        </table>
      </section>
      <footer>
        文件位置：docs/visuals/agent-memory-hub-architecture-map.html。该页面为生成物，修改源在 docs/visuals/generate_readme_diagrams.py。
      </footer>
    </main>
  </div>
</body>
</html>
"""
    (OUT / "agent-memory-hub-architecture-map.html").write_text(html, encoding="utf-8")


def main() -> None:
    factories: list[Callable[[bool], tuple[str, str, str, int, int]]] = [
        structure_map,
        loop_layered_architecture,
        operating_loop,
        amh_engineering_capability_map,
        amh_dynamic_architecture,
        amh_storage_lifecycle,
        amh_retrieval_context_pipeline,
        amh_adapter_capability_boundary,
        product_architecture,
        technical_architecture,
        memory_lifecycle_sequence,
        lambda lang: sequence_diagram(lang, retrieval=False),
        lambda lang: sequence_diagram(lang, retrieval=True),
        data_flow,
        retrieval_complete_flow,
        retrieval_algorithm_stack,
        retrieval_scoring_pipeline,
        retrieval_score_waterfall,
        retrieval_ablation_matrix,
        retrieval_token_curve,
    ]
    for zh in (True, False):
        for factory in factories:
            name, title, body, width, height = factory(zh)
            write_svg(name, title, title, body, width=width, height=height)
    write_preview_html()
    write_architecture_map_html()


if __name__ == "__main__":
    main()
