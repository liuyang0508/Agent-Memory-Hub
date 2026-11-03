"""Insight page: where recall happens, what actually gets used, and who reuses whose memory.

Every figure comes from the recall ledger and its daily rollup. Stages a
product surface cannot report are shown as not observable rather than as
zero. The page renders counts, identifiers and titles only — never memory
text — and shares the Feishu browser session with the administrator page; a
host-issued token remains the explicit operator fallback.
"""

from __future__ import annotations

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>组织记忆 · 记忆洞察</title>
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
  h2 small { color: var(--muted); font-weight: 400; margin-left: 8px; }
  .card { background: #fff; border: 1px solid var(--line); border-radius: 14px; padding: 18px; overflow-x: auto; }
  .grid { display: grid; gap: 14px; }
  .kpis { grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); }
  .kpi .label { color: var(--muted); font-size: 12.5px; }
  .kpi .value { font-size: 26px; font-weight: 700; margin-top: 4px; font-variant-numeric: tabular-nums; }
  .kpi .hint { color: var(--muted); font-size: 12px; margin-top: 2px; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { color: var(--muted); font-size: 12.5px; font-weight: 600; white-space: nowrap; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .pill { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 12px; background: #eef2ff; color: #3730a3; margin: 1px 4px 1px 0; white-space: nowrap; }
  .pill.ok { background: #ecfdf5; color: #065f46; }
  .pill.warn { background: #fffbeb; color: #92400e; }
  .pill.off { background: #f1f5f9; color: var(--muted); }
  .muted { color: var(--muted); }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; }
  .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  input, button, select { font: inherit; color: var(--ink); padding: 7px 11px; border-radius: 8px; border: 1px solid var(--line); background: #fff; }
  button { cursor: pointer; }
  button.primary { border-color: var(--a1); color: var(--a1); font-weight: 600; }
  #ops { display: none; }
  .funnel .frow { display: grid; grid-template-columns: 170px 1fr 120px; gap: 12px; align-items: center; padding: 5px 0; }
  .funnel .fbar { height: 14px; background: #f1f5f9; border-radius: 7px; overflow: hidden; }
  .funnel .fbar div { height: 100%; background: linear-gradient(90deg, var(--a2), var(--a1)); border-radius: 7px; }
  .funnel .fnum { text-align: right; font-variant-numeric: tabular-nums; }
  .spark { display: flex; align-items: flex-end; gap: 3px; height: 120px; padding-top: 8px; }
  .spark div { flex: 1; background: linear-gradient(180deg, var(--a2), var(--a1)); border-radius: 3px 3px 0 0; position: relative; }
  .spark div span { position: absolute; top: -20px; left: 50%; transform: translateX(-50%); font-size: 11px; color: var(--muted); }
  .legend { font-size: 12px; color: var(--muted); margin-top: 6px; }
  .empty { color: var(--muted); padding: 18px 0; text-align: center; }
  .two { grid-template-columns: 1.4fr 1fr; }
  .note { color: var(--muted); font-size: 12.5px; margin: 8px 0 0; }
  @media (max-width: 900px) { .two { grid-template-columns: 1fr; } .funnel .frow { grid-template-columns: 120px 1fr 90px; } }
</style>
</head>
<body>
<div class="wrap">
  <h1>组织记忆 · 记忆洞察</h1>
  <p class="sub">召回在哪些环节发生、哪些记忆真的被用到、谁的贡献被谁复用。数字全部来自召回账本的实际记录；拿不到的环节标「不可观测」，不记成零；不含记忆正文。
    <a href="/native/v1/admin/dashboard">数据看板</a> · <a href="/native/v1/admin/recalls">召回明细</a></p>

  <div id="login" class="card">
    <p><a href="/native/v1/admin/login?target=insights" onclick="beginAdminLogin('insights'); return false;">使用飞书登录</a></p>
    <p class="muted" id="loginError" role="status"></p>
    <details><summary>主机管理员运维入口</summary>
    <div class="row">
      <input id="token" type="password" placeholder="管理令牌" size="44" autocomplete="off">
      <button class="primary" onclick="saveToken()">进入</button>
    </div>
    <p class="muted" style="font-size:12.5px;margin:10px 0 0">
      在 Hub 主机执行 <code>amh-native admin --db &lt;库&gt; --label 你的名字</code> 取得令牌，只显示一次。
    </p>
    </details>
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
      <span class="muted" id="opsError"></span>
      <button onclick="logout()" style="margin-left:auto">退出</button>
    </div>

    <div class="grid kpis" id="kpis"></div>

    <h2>采集范围与可观测性</h2>
    <div class="card">
      <p id="scope" style="margin:0 0 10px"></p>
      <table>
        <thead id="matrixHead"></thead>
        <tbody id="matrix"></tbody>
      </table>
      <p class="note">「送达（回执）」指下一次 Hook 在转录里看到了投放标记，说明内容进了模型上下文，不等于用户确认采用；「引用」目前只有显式反馈，没有人打就是零，不代表没被引用。</p>
    </div>

    <h2>投放漏斗 <small>按候选条目计，各级累计</small></h2>
    <div class="card funnel" id="funnel"></div>

    <div class="grid two">
      <div>
        <h2>每日召回次数</h2>
        <div class="card"><div class="spark" id="spark"></div><div class="legend" id="sparkLegend"></div></div>
      </div>
      <div>
        <h2>按通道</h2>
        <div class="card"><table>
          <thead><tr><th>通道</th><th class="num">召回</th><th class="num">无结果</th><th class="num">已注入</th><th class="num">回执</th><th class="num">引用</th></tr></thead>
          <tbody id="channels"></tbody>
        </table></div>
      </div>
    </div>

    <h2>按成员 <small>仅管理员可见；召回次数不代表工作量</small></h2>
    <div class="card"><table>
      <thead><tr><th>成员</th><th class="num">召回</th><th class="num">无结果</th><th class="num">已注入</th><th class="num">回执</th><th class="num">引用</th><th class="num">设备</th></tr></thead>
      <tbody id="members"></tbody>
    </table></div>

    <div class="grid two">
      <div>
        <h2>检索模式与降级原因</h2>
        <div class="card"><table>
          <thead><tr><th>项</th><th class="num">次数</th></tr></thead>
          <tbody id="modes"></tbody>
        </table></div>
      </div>
      <div>
        <h2>候选被丢弃的原因</h2>
        <div class="card"><table>
          <thead><tr><th>原因</th><th class="num">条目</th></tr></thead>
          <tbody id="dropped"></tbody>
        </table></div>
      </div>
    </div>

    <h2>被用到的记忆 <small>按引用、回执、注入排序</small></h2>
    <div class="card"><table>
      <thead><tr><th>记忆</th><th>产品 / 项目</th><th>原生产者</th><th class="num">命中</th><th class="num">入选</th><th class="num">已注入</th><th class="num">回执</th><th class="num">引用</th><th class="num">注入到几位成员</th><th class="num">跨成员</th></tr></thead>
      <tbody id="assets"></tbody>
    </table></div>

    <h2>贡献与复用 <small>按原生产者汇总；「消费者」不含本人</small></h2>
    <div class="card"><table>
      <thead><tr><th>成员</th><th class="num">命中过的记忆</th><th class="num">被注入过的记忆</th><th class="num">注入次数</th><th class="num">回执</th><th class="num">引用</th><th class="num">消费者</th><th class="num">跨成员注入</th></tr></thead>
      <tbody id="producers"></tbody>
    </table></div>

    <h2>知识缺口 <small>没有任何候选入选的提问</small></h2>
    <div class="grid two">
      <div class="card">
        <p class="muted" style="margin:0 0 8px" id="gapsNote"></p>
        <table>
          <thead><tr><th>重复出现的问题</th><th class="num">次数</th><th>最近</th></tr></thead>
          <tbody id="repeated"></tbody>
        </table>
      </div>
      <div class="card"><table>
        <thead><tr><th>时间</th><th>通道</th><th>成员</th><th>问题</th></tr></thead>
        <tbody id="recent"></tbody>
      </table></div>
    </div>
  </div>
</div>

<script>
/* AMH_ADMIN_SESSION */
let token = sessionStorage.getItem("amh_dash") || sessionStorage.getItem("amh_admin") || "";
const fmtTime = (t) => t ? new Date(t * 1000).toLocaleString("zh-CN") : "—";
const esc = (v) => String(v ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const rows = (el, html, span) => document.getElementById(el).innerHTML = html || `<tr><td colspan="${span}" class="empty">暂无数据</td></tr>`;
const pct = (n, base) => base ? Math.round(n / base * 100) + "%" : "—";
const VALUE_CLASS = { observed: "ok", explicit: "ok", inferred: "warn", not_recorded: "off", unobservable: "off", unavailable: "off", "n/a": "off" };

function saveToken() {
  const v = document.getElementById("token").value.trim();
  if (!v) return;
  token = v; sessionStorage.setItem("amh_dash", v); load();
}
function logout() { return signOutAdmin(); }

async function load() {
  if (!token && !await ensureAdminSession("loginError", "insights")) return;
  const days = document.getElementById("days").value;
  let r;
  try {
    r = await fetch("/native/v1/admin/insights/data?days=" + days, { credentials: "same-origin", cache: "no-store", headers: token ? { "Authorization": "Bearer " + token } : {} });
  } catch (e) { document.getElementById("loginError").textContent = "网络错误"; return; }
  if (r.status === 401) { document.getElementById("ops").style.display = "none"; document.getElementById("login").style.display = "block"; document.getElementById("loginError").textContent = token ? "令牌无效或已撤销" : "登录已过期或权限已撤销，请重新登录。"; return; }
  if (!r.ok) { document.getElementById("opsError").textContent = "读取失败（HTTP " + r.status + "）"; return; }
  let d;
  try { d = await r.json(); } catch (e) { document.getElementById("opsError").textContent = "响应无法解析"; return; }
  document.getElementById("login").style.display = "none";
  document.getElementById("ops").style.display = "block";
  document.getElementById("opsError").textContent = "";
  document.getElementById("stamp").textContent = "数据时间 " + fmtTime(d.generated_at);
  render(d);
}

function render(d) {
  const t = d.totals;
  const kpis = [
    ["召回次数", t.traces, "最近 " + d.window_days + " 天"],
    ["无结果", t.no_match, "占 " + pct(t.no_match, t.traces)],
    ["已注入条目", t.emitted, "进入了产品上下文"],
    ["已送达（回执）", t.confirmed, "转录里看到了投放标记"],
    ["已引用", t.cited, "显式反馈，未打则为零"],
    ["活跃成员", t.members_active, "窗口内发起过召回"],
    ["被用到的记忆", t.assets_used + " / " + t.assets_hit, "注入过 / 命中过"],
    ["跨成员复用", t.cross_member_emitted, "注入给非作者的次数"],
  ];
  document.getElementById("kpis").innerHTML = kpis.map(([l, v, h]) =>
    `<div class="card kpi"><div class="label">${esc(l)}</div><div class="value">${esc(v)}</div><div class="hint">${esc(h)}</div></div>`
  ).join("");

  document.getElementById("scope").innerHTML =
    (d.collection_since ? `采集起始 <b>${esc(d.collection_since)}</b>，之前的使用情况不可观测。` : "尚无召回记录。") +
    ` 原始明细保留 ${d.trace_ttl_days} 天，之后只剩按天汇总（已冻结 ${d.rollup.frozen_days} 天，近 ${d.rollup.live_days} 天仍随投递与反馈实时重算）。` +
    (d.preview_enabled ? "" : " 问题预览已关闭，缺口只显示摘要哈希。");
  const ob = d.observability;
  document.getElementById("matrixHead").innerHTML = `<tr><th>接入方式</th>${ob.stages.map(s => `<th>${esc(s.label)}</th>`).join("")}<th>说明</th></tr>`;
  rows("matrix", ob.rows.map(r => `<tr><td>${esc(r.label)}</td>${ob.stages.map(s => {
    const v = r.stages[s.key];
    return `<td><span class="pill ${VALUE_CLASS[v] || ""}">${esc(ob.values[v] || v)}</span></td>`;
  }).join("")}<td class="muted">${esc(r.note)}</td></tr>`).join(""), ob.stages.length + 2);

  const base = Math.max(1, d.funnel.length ? d.funnel[0].count : 0);
  document.getElementById("funnel").innerHTML = d.funnel.map(f =>
    `<div class="frow"><div>${esc(f.label)}</div><div class="fbar"><div style="width:${Math.round(f.count / base * 100)}%"></div></div><div class="fnum">${f.count}<span class="muted"> · ${pct(f.count, base)}</span></div></div>`
  ).join("") || `<div class="empty">暂无数据</div>`;

  const spark = d.by_day;
  const sMax = Math.max(1, ...spark.map(s => s.traces));
  document.getElementById("spark").innerHTML = spark.map(s =>
    `<div style="height:${s.traces ? "max(3px," + Math.round(s.traces / sMax * 100) + "%)" : "0"}" title="${s.day}: ${s.traces} 次，无结果 ${s.no_match}，已注入 ${s.emitted}"><span>${s.traces || ""}</span></div>`
  ).join("");
  document.getElementById("sparkLegend").textContent =
    spark.length ? spark[0].day + " → " + spark[spark.length - 1].day + "，峰值 " + sMax + " 次/天" : "暂无数据";

  const counts = (r) => `<td class="num">${r.traces}</td><td class="num">${r.no_match}</td><td class="num">${r.emitted}</td><td class="num">${r.confirmed}</td><td class="num">${r.cited}</td>`;
  rows("channels", d.by_channel.map(c => `<tr><td>${esc(c.channel)}</td>${counts(c)}</tr>`).join(""), 6);
  rows("members", d.by_member.map(m => `<tr><td>${esc(m.name)}</td>${counts(m)}<td class="num">${m.devices}</td></tr>`).join(""), 7);

  rows("modes", d.modes.map(m => `<tr><td>检索模式 · ${esc(m.name)}</td><td class="num">${m.count}</td></tr>`).join("") +
    d.reasons.map(m => `<tr><td>降级 · ${esc(m.name)}</td><td class="num">${m.count}</td></tr>`).join(""), 2);
  rows("dropped", d.dropped.map(m => `<tr><td>${esc(m.name)}</td><td class="num">${m.count}</td></tr>`).join(""), 2);

  rows("assets", d.top_assets.map(a => `<tr>
    <td>${esc(a.title || a.asset_id)}<br><span class="muted mono">${esc(a.asset_id)}</span></td>
    <td>${esc(a.product || "")}${a.project ? " / " + esc(a.project) : ""}</td>
    <td>${esc(a.producer)}</td>
    <td class="num">${a.candidate}</td><td class="num">${a.selected}</td><td class="num">${a.emitted}</td>
    <td class="num">${a.confirmed}</td><td class="num">${a.cited}</td>
    <td class="num">${a.members_emitted}</td><td class="num">${a.cross_member}</td></tr>`).join(""), 10);

  rows("producers", d.by_producer.map(p => `<tr><td>${esc(p.name)}</td>
    <td class="num">${p.assets_hit}</td><td class="num">${p.assets_used}</td><td class="num">${p.emitted}</td>
    <td class="num">${p.confirmed}</td><td class="num">${p.cited}</td><td class="num">${p.consumers}</td><td class="num">${p.cross_member}</td></tr>`).join(""), 8);

  const g = d.gaps;
  document.getElementById("gapsNote").textContent =
    `窗口内无结果 ${g.no_match_traces} 次；下面两张表只看最近 ${g.scanned_days} 天的原始明细。`;
  const question = (x) => x.preview ? esc(x.preview) : `<span class="muted">（未保存问题文本）</span> <span class="mono muted">${esc(x.query_digest)}</span>`;
  rows("repeated", g.repeated.map(x => `<tr><td>${question(x)}</td><td class="num">${x.count}</td><td class="muted">${fmtTime(x.last)}</td></tr>`).join(""), 3);
  rows("recent", g.recent.map(x => `<tr><td class="muted">${fmtTime(x.created)}</td><td>${esc(x.channel)}</td><td>${esc(x.member)}</td><td>${question(x)}</td></tr>`).join(""), 4);
}

load();
</script>
</body>
</html>
"""


def html() -> str:
    """Return the insight page; it carries no secret and no memory content."""
    from .admin_ui import SCRIPT

    return PAGE.replace("/* AMH_ADMIN_SESSION */", SCRIPT)
