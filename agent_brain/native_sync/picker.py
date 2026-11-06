"""Local selection page for the pre-install history import.

A terminal list does not scale: a real machine holds a thousand-odd files, and
choosing among them with "1,3,5-9" is not a decision anyone can actually make.
This serves the same rows as a page on 127.0.0.1, with filters and per-row
checkboxes, and returns exactly what the member ticked.

Nothing here talks to the Hub. The page is served over loopback, holds no
credential, and the server shuts down as soon as the member confirms.
"""

from __future__ import annotations

import html
import http.server
import json
import threading
from typing import Any, cast
import webbrowser

from .common import display_name

MAX_PORT_ATTEMPTS = 5
# The page sends a short list of row indexes; anything larger is not a selection.
MAX_BODY_BYTES = 64 * 1024

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>选择要共享的记忆</title>
<style>
  :root { color-scheme: light dark; --line:#d8dee9; --muted:#667085; --accent:#2563eb; }
  * { box-sizing:border-box; }
  body { margin:0; font:14px/1.6 -apple-system,"PingFang SC","Segoe UI",sans-serif; padding:20px 24px 96px; }
  h1 { font-size:19px; margin:0 0 4px; }
  .sub { color:var(--muted); font-size:13px; margin:0 0 18px; }
  .bar { position:fixed; left:0; right:0; bottom:0; display:flex; gap:12px; align-items:center;
         padding:14px 24px; background:Canvas; border-top:1px solid var(--line); z-index:5; }
  .row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin:8px 0 14px; }
  input,select,button { font:inherit; padding:6px 10px; border-radius:6px; border:1px solid var(--line); background:Canvas; color:inherit; }
  button { cursor:pointer; }
  button.primary { border-color:var(--accent); color:var(--accent); font-weight:600; }
  table { width:100%; border-collapse:collapse; }
  th,td { text-align:left; padding:7px 9px; border-bottom:1px solid var(--line); vertical-align:top; }
  th { font-size:12px; color:var(--muted); font-weight:600; position:sticky; top:0; background:Canvas; }
  tr.group td { background:rgba(127,127,127,.07); font-weight:600; font-size:13px; }
  .name { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.5px; word-break:break-all; }
  .risk { font-size:12px; padding:1px 7px; border-radius:999px; white-space:nowrap; }
  .risk.sensitive { background:#fff4e5; color:#9a5b00; }
  .risk.blocked { background:#fdeaea; color:#a30000; }
  .risk.clean { background:#eaf5ea; color:#2b6b2b; }
  .muted { color:var(--muted); font-size:12.5px; }
  #count { font-weight:600; }
</style>
</head>
<body>
<h1>选择要共享的记忆</h1>
<p class="sub">本页用于新增历史选择。勾选内容会上传到组织 Hub，由有权限的成员检索；未勾选的历史不会因此新增上传。已有选择继续生效，取消本页勾选或跳过不会撤销它们。</p>

__EMPTY__
__SCOPE__
<p class="muted">隐私标记仅是自动提示，不代表完整审查。含密钥文件不可选。选择当前未启用的类型后，终端会要求确认该类型的后续自动同步。</p>

<div class="row">
  <input id="q" placeholder="按文件名或标题筛选" size="28">
  <select id="risk">
    <option value="">全部风险等级</option>
    <option value="clean">仅无标记</option>
    <option value="sensitive">仅带隐私标记</option>
  </select>
  <select id="product"><option value="">全部产品</option></select>
  <select id="kind"><option value="">全部类型</option></select>
  <label class="muted" for="since">修改时间从</label>
  <input id="since" type="date">
  <label class="muted" for="until">到</label>
  <input id="until" type="date">
  <button onclick="pick('clean')">选中可见无标记</button>
  <button onclick="pick('all')">全选可见项（不含密钥）</button>
  <button onclick="pick('none')">取消本次全部勾选</button>
</div>

<table>
  <thead><tr><th style="width:34px"></th><th>文件</th><th>标题</th><th style="width:90px">大小</th><th style="width:150px">修改时间</th><th style="width:150px">风险</th></tr></thead>
  <tbody id="rows"></tbody>
</table>

<div class="bar">
  <span>本次新增选择 <span id="count">0</span> 个文件</span>
  <span class="muted" id="note"></span>
  <span style="flex:1"></span>
  <button onclick="submitSelection(false)">跳过新增选择</button>
  <button class="primary" onclick="submitSelection(true)">确认并继续</button>
</div>

<script>
const ROWS = __ROWS__;
const SELECTED = new Set();
const fmt = (t) => new Date(t * 1000).toLocaleString("zh-CN");
const esc = (v) => String(v ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;"}[c]));

function uniq(key) { return [...new Set(ROWS.map(r => r[key]))]; }
for (const p of uniq("product_name")) {
  const o = document.createElement("option"); o.value = p; o.textContent = p; document.getElementById("product").append(o);
}
for (const k of uniq("kind_name")) {
  const o = document.createElement("option"); o.value = k; o.textContent = k; document.getElementById("kind").append(o);
}

// A date input is day-grained local time; the row carries epoch seconds, so
// compare against the start of the chosen day and the end of the chosen one.
function dayStart(value) { return value ? new Date(value + "T00:00:00").getTime() / 1000 : null; }
function dayEnd(value) { return value ? new Date(value + "T23:59:59.999").getTime() / 1000 : null; }

function visible() {
  const q = document.getElementById("q").value.trim().toLowerCase();
  const risk = document.getElementById("risk").value;
  const product = document.getElementById("product").value;
  const kind = document.getElementById("kind").value;
  const from = dayStart(document.getElementById("since").value);
  const to = dayEnd(document.getElementById("until").value);
  return ROWS.filter(r =>
    (!q || (r.name + " " + r.title).toLowerCase().includes(q)) &&
    (!risk || r.risk === risk) &&
    (!product || r.product_name === product) &&
    (!kind || r.kind_name === kind) &&
    (from === null || r.mtime >= from) &&
    (to === null || r.mtime <= to));
}

function render() {
  const tbody = document.getElementById("rows");
  const rows = visible();
  let last = "";
  const parts = [];
  for (const r of rows) {
    const group = r.product_name + " · " + r.kind_name;
    if (group !== last) { parts.push(`<tr class="group"><td colspan="6">${esc(group)}</td></tr>`); last = group; }
    const badge = r.risk === "clean" ? "" :
      `<span class="risk ${r.risk}">${r.risk === "blocked" ? "含密钥，不会上传" : esc(r.reasons.join("、"))}</span>`;
    parts.push(`<tr>
      <td><input type="checkbox" data-i="${r.i}" ${r.risk === "blocked" ? "disabled" : ""} ${SELECTED.has(r.i) ? "checked" : ""} onchange="toggleSelection(this)"></td>
      <td class="name">${esc(r.name)}</td>
      <td>${esc(r.title)}${r.previously_selected ? '<div class="muted">已有选择（本次取消不撤销）</div>' : ""}${r.scope_enabled === false ? '<div class="muted">类型当前暂停</div>' : ""}</td>
      <td class="muted">${(r.bytes/1024).toFixed(0)} KB</td>
      <td class="muted">${fmt(r.mtime)}</td>
      <td>${badge}</td></tr>`);
  }
  tbody.innerHTML = parts.join("") || `<tr><td colspan="6" class="muted">没有匹配的文件</td></tr>`;
  update();
}

function toggleSelection(cb) {
  const i = Number(cb.dataset.i);
  if (cb.checked) SELECTED.add(i); else SELECTED.delete(i);
  update();
}

function pick(mode) {
  if (mode === "none") SELECTED.clear();
  else for (const r of visible()) {
    if (r.scope_enabled !== false && r.risk !== "blocked" && (mode === "all" || r.risk === "clean")) SELECTED.add(r.i);
  }
  render();
}

function update() {
  document.getElementById("count").textContent = SELECTED.size;
  const risky = [...SELECTED].filter(i => ROWS[i].risk === "sensitive").length;
  const visibleIds = new Set(visible().map(r => r.i));
  const hidden = [...SELECTED].filter(i => !visibleIds.has(i)).length;
  document.getElementById("note").textContent = `其中 ${risky} 个带隐私标记，${hidden} 个在当前筛选之外（仍保留）`;
}

async function submitSelection(withSelection) {
  const picked = withSelection ? [...SELECTED] : [];
  const response = await fetch("/submit", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ picked }) });
  if (!response.ok) { alert("提交失败，请重试；本次勾选仍保留。"); return; }
  document.body.innerHTML = "<h1>已提交，可以关闭此页面回到终端。</h1>";
}

document.getElementById("q").addEventListener("input", render);
for (const id of ["risk", "product", "kind", "since", "until"]) document.getElementById(id).addEventListener("change", render);
render();
</script>
</body>
</html>
"""


def _payload(rows: list[dict[str, Any]]) -> str:
    """Rows for the browser: no absolute paths and no file bodies."""
    slim = [
        {
            "i": index,
            "name": row["name"],
            "title": row["title"],
            "product_name": row["product_name"],
            "kind_name": display_name(row["kind"]),
            "bytes": row["bytes"],
            "mtime": row["mtime"],
            "risk": row["risk"],
            "reasons": list(row.get("reasons") or []),
            **{key: row[key] for key in ("previously_selected", "scope_enabled") if key in row},
        }
        for index, row in enumerate(rows)
    ]
    return json.dumps(slim, ensure_ascii=False).replace("</", "<\\/")


def _empty_note(products: list[str] | None) -> str:
    if not products:
        return ""
    names = "、".join(products)
    return (
        '<p class="muted" style="margin:10px 0 0">'
        f"{html.escape(names)} 已检测到，但本机还没有可导入的记忆文件。"
        "在该产品里实际用一段时间、产生了记忆之后，重跑导入即可看到。"
        "</p>"
    )


def _scope_note(scope: dict[str, Any] | None) -> str:
    if scope is None:
        return '<p class="muted">新安装默认仅同步记忆；其他类型需明确启用。</p>'
    names = "、".join(display_name(k) for k in scope["enabled_kinds"]) or "全部暂停"
    return (
        "<p><strong>当前自动同步类型："
        + html.escape(names)
        + "</strong><br>"
        + html.escape(scope["note"])
        + "<br>"
        + html.escape(scope["preservation"])
        + "</p>"
    )


def serve(
    rows: list[dict[str, Any]],
    *,
    open_browser: bool = True,
    timeout: float = 1800,
    empty_products: list[str] | None = None,
    scope: dict[str, Any] | None = None,
    port: int = 0,
) -> list[dict[str, Any]]:
    """Show the checklist and return the ticked rows; empty means the member skipped.

    empty_products names products that were detected but have nothing importable
    yet, so their absence from the filters reads as "nothing here" rather than
    "not supported".
    """
    holder: dict[str, Any] = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            # Order matters: fill the static notice before injecting row data,
            # otherwise a file literally named __EMPTY__ would be replaced too.
            page = (
                PAGE.replace("__SCOPE__", _scope_note(scope))
                .replace("__EMPTY__", _empty_note(empty_products))
                .replace("__ROWS__", _payload(rows))
                .encode()
            )
            self._send(200, page, "text/html; charset=utf-8")

        def do_POST(self) -> None:
            if self.path != "/submit":
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
            # Any open tab can reach a loopback port, and a text/plain POST skips
            # the CORS preflight entirely. Require the content type this page
            # sends and refuse an Origin that is not ours.
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            origin = self.headers.get("Origin")
            expected = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
            if ctype != "application/json" or (origin and origin not in expected):
                self._send(403, b"forbidden", "text/plain; charset=utf-8")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 <= length <= MAX_BODY_BYTES:
                self._send(400, b"bad request", "text/plain; charset=utf-8")
                return
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
                # A list or a bare string parsed fine but then crashed on .get,
                # which dropped the connection and left the CLI waiting out its
                # full timeout.
                if not isinstance(data, dict):
                    raise TypeError("payload must be an object")
                raw = data.get("picked", [])
                if not isinstance(raw, list) or any(type(i) is not int for i in raw):
                    raise TypeError("picked must contain integer indexes")
                picked = list(dict.fromkeys(raw))
            except (ValueError, TypeError):
                self._send(400, b"bad request", "text/plain; charset=utf-8")
                return
            holder["picked"] = [
                rows[i] for i in picked if 0 <= i < len(rows) and rows[i]["risk"] != "blocked"
            ]
            self._send(200, b'{"ok":true}', "application/json")
            done.set()

        def log_message(self, *args: Any) -> None:
            return

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = "http://127.0.0.1:%d/" % port
        if open_browser:
            webbrowser.open(url)
        print("在浏览器勾选要共享的记忆：%s" % url, flush=True)
        if not done.wait(timeout):
            raise TimeoutError("selection page timed out")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    return cast(list[dict[str, Any]], holder.get("picked", []))
