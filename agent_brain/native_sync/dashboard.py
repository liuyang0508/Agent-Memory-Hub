"""Activity dashboard for a self-hosted Hub.

Answers the questions an operator actually asks: who is contributing, from which
products and devices, how much, and whether anything has gone quiet. It renders
counts and metadata only — never memory text — so the page is safe to leave open
on a shared screen.

The page holds no credential of its own: the operator pastes the token minted on
the host, and the browser keeps it in sessionStorage only.
"""

from __future__ import annotations

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>组织记忆 · 数据看板</title>
<style>
  :root {
    color-scheme: light dark;
    --line: #e2e8f0; --muted: #64748b; --ink: #0f172a;
    --a1: #2563eb; --a2: #0ea5e9; --a3: #10b981; --a4: #f59e0b; --a5: #ec4899;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font: 15px/1.6 -apple-system, "PingFang SC", "Segoe UI", sans-serif; color: var(--ink); background: #f8fafc; }
  .wrap { max-width: 1280px; margin: 0 auto; padding: 28px 24px 64px; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sub { color: var(--muted); font-size: 13px; margin: 0 0 22px; }
  h2 { font-size: 15px; margin: 30px 0 12px; letter-spacing: .02em; }
  .card { background: #fff; border: 1px solid var(--line); border-radius: 14px; padding: 18px; }
  .grid { display: grid; gap: 14px; }
  .kpis { grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); }
  .kpi .label { color: var(--muted); font-size: 12.5px; }
  .kpi .value { font-size: 26px; font-weight: 700; margin-top: 4px; font-variant-numeric: tabular-nums; }
  .kpi .hint { color: var(--muted); font-size: 12px; margin-top: 2px; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th, td { text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--line); }
  th { color: var(--muted); font-size: 12.5px; font-weight: 600; white-space: nowrap; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .rank { color: var(--muted); font-variant-numeric: tabular-nums; width: 34px; }
  td.who { white-space: nowrap; }
  .bar { height: 6px; border-radius: 3px; background: var(--a1); opacity: .85; }
  .bar-cell { width: 160px; }
  .pill { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 12px; background: #eef2ff; color: #3730a3; margin: 1px 4px 1px 0; white-space: nowrap; }
  .muted { color: var(--muted); }
  .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  input, button, select { font: inherit; color: var(--ink); padding: 7px 11px; border-radius: 8px; border: 1px solid var(--line); background: #fff; }
  button { cursor: pointer; }
  button.primary { border-color: var(--a1); color: var(--a1); font-weight: 600; }
  #ops { display: none; }
  .spark { display: flex; align-items: flex-end; gap: 3px; height: 120px; padding-top: 8px; }
  .spark div { flex: 1; background: linear-gradient(180deg, var(--a2), var(--a1)); border-radius: 3px 3px 0 0; position: relative; }
  .spark div span { position: absolute; top: -20px; left: 50%; transform: translateX(-50%); font-size: 11px; color: var(--muted); }
  .hours { display: flex; gap: 2px; align-items: flex-end; height: 60px; }
  .hours div { flex: 1; background: var(--a3); border-radius: 2px; }
  .legend { font-size: 12px; color: var(--muted); margin-top: 6px; }
  .empty { color: var(--muted); padding: 18px 0; text-align: center; }
  .two { grid-template-columns: 1.4fr 1fr; }
  /* A wide table must scroll inside its card rather than be clipped by the
     page. Product pills alone can push the row past a narrow window. */
  .card { overflow-x: auto; }
  @media (max-width: 900px) { .two { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<div class="wrap">
  <h1>组织记忆 · 数据看板</h1>
  <p class="sub">统计口径来自设备实际上传的内容，不含记忆正文。粘贴在 Hub 主机签发的管理令牌后查看。</p>

  <div id="login" class="card">
    <div class="row">
      <input id="token" type="password" placeholder="管理令牌" size="44" autocomplete="off">
      <button class="primary" onclick="saveToken()">进入</button>
      <span class="muted" id="loginError"></span>
    </div>
    <p class="muted" style="font-size:12.5px;margin:10px 0 0">
      在 Hub 主机执行 <code>amh-native admin --db &lt;库&gt; --label 你的名字</code> 取得令牌，只显示一次。
    </p>
  </div>

  <div id="ops">
    <div class="row" style="margin-bottom:8px">
      <select id="days" onchange="load()">
        <option value="7">最近 7 天</option>
        <option value="30" selected>最近 30 天</option>
        <option value="90">最近 90 天</option>
        <option value="365">最近一年</option>
      </select>
      <button onclick="load()">刷新</button>
      <span class="muted" id="stamp"></span>
      <button onclick="logout()" style="margin-left:auto">退出</button>
    </div>

    <div class="grid kpis" id="kpis"></div>

    <h2>贡献排行</h2>
    <div class="card"><table>
      <thead><tr><th class="rank">#</th><th>成员</th><th class="num">条目</th><th class="num">版本</th><th class="num">体积</th><th class="bar-cell">占比</th><th>产品</th><th>最近活跃</th></tr></thead>
      <tbody id="members"></tbody>
    </table></div>

    <div class="grid two">
      <div>
        <h2>每日新增</h2>
        <div class="card"><div class="spark" id="spark"></div><div class="legend" id="sparkLegend"></div></div>
      </div>
      <div>
        <h2>上传时段分布</h2>
        <div class="card"><div class="hours" id="hours"></div><div class="legend">0 时 → 23 时（本地时间）</div></div>
      </div>
    </div>

    <div class="grid two">
      <div>
        <h2>按产品</h2>
        <div class="card"><table>
          <thead><tr><th>产品</th><th class="num">条目</th><th class="num">体积</th><th class="num">版本</th></tr></thead>
          <tbody id="products"></tbody>
        </table></div>
      </div>
      <div>
        <h2>按类型</h2>
        <div class="card"><table>
          <thead><tr><th>类型</th><th class="num">条目</th></tr></thead>
          <tbody id="kinds"></tbody>
        </table></div>
      </div>
    </div>

    <h2>设备贡献</h2>
    <div class="card"><table>
      <thead><tr><th>设备</th><th>成员</th><th class="num">条目</th><th class="num">体积</th><th>状态</th><th>最近活跃</th></tr></thead>
      <tbody id="devices"></tbody>
    </table></div>

    <h2>热门项目</h2>
    <div class="card"><table>
      <thead><tr><th>项目</th><th class="num">条目</th></tr></thead>
      <tbody id="projects"></tbody>
    </table></div>
  </div>
</div>

<script>
let token = sessionStorage.getItem("amh_dash") || "";
const fmtBytes = (n) => n < 1024 ? n + " B" : n < 1048576 ? (n/1024).toFixed(1) + " KB" : (n/1048576).toFixed(1) + " MB";
const fmtTime = (t) => t ? new Date(t * 1000).toLocaleString("zh-CN") : "—";
const esc = (v) => String(v ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const rows = (el, html, span) => document.getElementById(el).innerHTML = html || `<tr><td colspan="${span}" class="empty">暂无数据</td></tr>`;

function saveToken() {
  const v = document.getElementById("token").value.trim();
  if (!v) return;
  token = v; sessionStorage.setItem("amh_dash", v); load();
}
function logout() { sessionStorage.removeItem("amh_dash"); location.reload(); }

async function load() {
  const days = document.getElementById("days").value;
  let r;
  try {
    r = await fetch("/native/v1/admin/dashboard/data?days=" + days, { headers: { "Authorization": "Bearer " + token } });
  } catch (e) { document.getElementById("loginError").textContent = "网络错误"; return; }
  if (r.status === 401) { document.getElementById("loginError").textContent = "令牌无效或已撤销"; return; }
  if (!r.ok) { document.getElementById("loginError").textContent = "读取失败"; return; }
  const d = await r.json();
  document.getElementById("login").style.display = "none";
  document.getElementById("ops").style.display = "block";
  document.getElementById("stamp").textContent = "数据时间 " + fmtTime(d.generated_at);
  render(d);
}

function render(d) {
  const t = d.totals;
  const kpis = [
    ["成员", t.members, "已加入组织"],
    ["在线设备", t.active_devices + " / " + t.devices, "未撤销 / 总数"],
    ["共享条目", t.assets, "当前有效版本"],
    ["内容体积", fmtBytes(t.bytes), "全部正文"],
    ["累计版本", t.revisions, "含历史改写"],
    [("最近 " + d.window_days + " 天新增"), t.in_window, "窗口内上传"],
    ["覆盖项目", t.projects, "有项目归属的条目"],
    ["最近上传", t.last_upload ? fmtTime(t.last_upload) : "—", ""],
  ];
  document.getElementById("kpis").innerHTML = kpis.map(([l, v, h]) =>
    `<div class="card kpi"><div class="label">${esc(l)}</div><div class="value">${esc(v)}</div><div class="hint">${esc(h)}</div></div>`
  ).join("");

  const max = Math.max(1, ...d.by_member.map(m => m.assets));
  rows("members", d.by_member.map((m, i) => `<tr>
    <td class="rank">${i + 1}</td>
    <td class="who">${esc(m.label)}</td>
    <td class="num">${m.assets}</td>
    <td class="num">${m.revisions}</td>
    <td class="num">${fmtBytes(m.bytes)}</td>
    <td class="bar-cell"><div class="bar" style="width:${Math.round(m.assets / max * 100)}%"></div></td>
    <td>${Object.keys(m.products).map(p => `<span class="pill">${esc(p)}</span>`).join("")}</td>
    <td class="muted">${fmtTime(m.last)}</td></tr>`).join(""), 8);

  const spark = d.by_day;
  const sMax = Math.max(1, ...spark.map(s => s.assets));
  document.getElementById("spark").innerHTML = spark.map(s =>
    `<div style="height:${s.assets ? "max(3px," + Math.round(s.assets / sMax * 100) + "%)" : "0"}" title="${s.day}: ${s.assets} 条"><span>${s.assets || ""}</span></div>`
  ).join("");
  document.getElementById("sparkLegend").textContent =
    spark.length ? spark[0].day + " → " + spark[spark.length - 1].day + "，峰值 " + sMax + " 条/天" : "暂无数据";

  const hMax = Math.max(1, ...d.by_hour);
  document.getElementById("hours").innerHTML = d.by_hour.map((v, h) =>
    `<div style="height:${v ? "max(3px," + Math.round(v / hMax * 100) + "%)" : "0"}" title="${h} 时: ${v} 条"></div>`
  ).join("");

  rows("products", d.by_product.map(p => `<tr><td>${esc(p.label)}</td><td class="num">${p.assets}</td>
    <td class="num">${fmtBytes(p.bytes)}</td><td class="num">${p.revisions}</td></tr>`).join(""), 4);

  rows("kinds", d.by_kind.map(k => `<tr><td>${esc(k.kind)}</td><td class="num">${k.assets}</td></tr>`).join(""), 2);

  const devById = Object.fromEntries(d.by_device.map(x => [x.key, x]));
  rows("devices", d.devices.map(dev => {
    const s = devById[dev.id] || { assets: 0, bytes: 0, last: 0 };
    return `<tr><td>${esc(dev.name)}</td><td>${esc(dev.member)}</td>
      <td class="num">${s.assets}</td><td class="num">${fmtBytes(s.bytes)}</td>
      <td>${dev.revoked ? '<span class="muted">已撤销</span>' : '正常'}</td>
      <td class="muted">${fmtTime(s.last)}</td></tr>`;
  }).join(""), 6);

  rows("projects", d.top_projects.map(p => `<tr><td>${esc(p.project)}</td><td class="num">${p.assets}</td></tr>`).join(""), 2);
}

if (token) load();
</script>
</body>
</html>
"""


def html() -> str:
    """Return the dashboard page; it carries no secret and no memory content."""
    return PAGE
