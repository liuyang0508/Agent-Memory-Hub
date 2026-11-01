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
from typing import Any
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
<p class="sub">勾选的内容会上传到组织 Hub，全组织成员可检索正文。未勾选的文件一个字节都不会离开这台机器。</p>

__EMPTY__

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
  <button onclick="pick('clean')">选中全部无标记</button>
  <button onclick="pick('all')">全选（不含含密钥）</button>
  <button onclick="pick('none')">全部取消</button>
</div>

<table>
  <thead><tr><th style="width:34px"></th><th>文件</th><th>标题</th><th style="width:90px">大小</th><th style="width:150px">修改时间</th><th style="width:150px">风险</th></tr></thead>
  <tbody id="rows"></tbody>
</table>

<div class="bar">
  <span>已选 <span id="count">0</span> 个文件</span>
  <span class="muted" id="note"></span>
  <span style="flex:1"></span>
  <button onclick="submitSelection(false)">不上传，跳过</button>
  <button class="primary" onclick="submitSelection(true)">确认并继续</button>
</div>

<script>
const ROWS = __ROWS__;
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
      <td><input type="checkbox" data-i="${r.i}" ${r.risk === "blocked" ? "disabled" : ""} onchange="update()"></td>
      <td class="name">${esc(r.name)}</td>
      <td>${esc(r.title)}</td>
      <td class="muted">${(r.bytes/1024).toFixed(0)} KB</td>
      <td class="muted">${fmt(r.mtime)}</td>
      <td>${badge}</td></tr>`);
  }
  tbody.innerHTML = parts.join("") || `<tr><td colspan="6" class="muted">没有匹配的文件</td></tr>`;
  update();
}

function pick(mode) {
  const ids = new Set(visible().filter(r => r.risk !== "blocked").map(r => r.i));
  document.querySelectorAll("#rows input[type=checkbox]").forEach(cb => {
    const i = Number(cb.dataset.i);
    const row = ROWS[i];
    if (!ids.has(i)) { if (mode === "none") cb.checked = false; return; }
    cb.checked = mode === "all" || (mode === "clean" && row.risk === "clean");
    if (mode === "none") cb.checked = false;
  });
  update();
}

function update() {
  const boxes = [...document.querySelectorAll("#rows input[type=checkbox]")].filter(c => c.checked);
  document.getElementById("count").textContent = boxes.length;
  const risky = boxes.filter(c => ROWS[Number(c.dataset.i)].risk === "sensitive").length;
  document.getElementById("note").textContent = risky ? `其中 ${risky} 个带隐私标记` : "";
}

async function submitSelection(withSelection) {
  const picked = withSelection
    ? [...document.querySelectorAll("#rows input[type=checkbox]")].filter(c => c.checked).map(c => Number(c.dataset.i))
    : [];
  await fetch("/submit", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ picked }) });
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


def serve(
    rows: list[dict[str, Any]],
    *,
    open_browser: bool = True,
    timeout: float = 1800,
    empty_products: list[str] | None = None,
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
                PAGE.replace("__EMPTY__", _empty_note(empty_products))
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
            expected = "http://127.0.0.1:%d" % self.server.server_address[1]
            if ctype != "application/json" or (origin and origin != expected):
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
                picked = [int(i) for i in data.get("picked") or []]
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

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
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
    return holder.get("picked", [])
