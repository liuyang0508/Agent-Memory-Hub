"""Recall ledger page: what each query retrieved, how it ranked, and what reached the model.

Served beside the dashboard. It renders identifiers, titles, producers, stage
switches and delivery states; never memory text. The operator pastes the
host-minted token and the browser keeps it in sessionStorage only.
"""

from __future__ import annotations

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>组织记忆 · 召回明细</title>
<style>
  :root { color-scheme: light dark; --line: #e2e8f0; --muted: #64748b; --ink: #0f172a; --a1: #2563eb; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 14px/1.6 -apple-system, "PingFang SC", "Segoe UI", sans-serif; color: var(--ink); background: #f8fafc; }
  .wrap { max-width: 1280px; margin: 0 auto; padding: 28px 24px 64px; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sub { color: var(--muted); font-size: 13px; margin: 0 0 22px; }
  .card { background: #fff; border: 1px solid var(--line); border-radius: 14px; padding: 18px; margin-bottom: 16px; overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
  th, td { text-align: left; padding: 7px 9px; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { color: var(--muted); font-size: 12.5px; font-weight: 600; white-space: nowrap; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .pill { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 12px; background: #eef2ff; color: #3730a3; margin: 1px 4px 1px 0; white-space: nowrap; }
  .pill.off { background: #f1f5f9; color: var(--muted); }
  .pill.ok { background: #ecfdf5; color: #065f46; }
  .pill.drop { background: #fff7ed; color: #9a3412; }
  .muted { color: var(--muted); }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; }
  .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  input, button, select { font: inherit; color: var(--ink); padding: 7px 11px; border-radius: 8px; border: 1px solid var(--line); background: #fff; }
  button { cursor: pointer; }
  button.primary { border-color: var(--a1); color: var(--a1); font-weight: 600; }
  button.link { border: none; background: none; color: var(--a1); padding: 0; }
  #ops { display: none; }
  details { margin: 6px 0; }
  summary { cursor: pointer; }
  .empty { color: var(--muted); padding: 18px 0; text-align: center; }
  dl { display: grid; grid-template-columns: max-content 1fr; gap: 2px 12px; margin: 6px 0; font-size: 12.5px; }
  dt { color: var(--muted); }
  dd { margin: 0; }
</style>
</head>
<body>
<div class="wrap">
  <h1>组织记忆 · 召回明细</h1>
  <p class="sub">每次召回记录候选的召回方式、排名变化和本轮状态；不含记忆正文。已用飞书登录管理后台的管理员直接可看；否则粘贴 Hub 主机签发的管理令牌。</p>

  <div id="login" class="card">
    <div class="row">
      <input id="token" type="password" placeholder="管理令牌" size="44" autocomplete="off">
      <button class="primary" onclick="saveToken()">进入</button>
      <span class="muted" id="loginError"></span>
    </div>
  </div>

  <div id="ops">
    <div class="row" style="margin-bottom:8px">
      <input id="traceInput" placeholder="trace_id（可选）" size="30" class="mono">
      <button onclick="load()">查询</button>
      <button onclick="document.getElementById('traceInput').value='';load()">最近记录</button>
      <span class="muted" id="stamp"></span>
      <span class="muted" id="opsError"></span>
      <button onclick="logout()" style="margin-left:auto">退出</button>
    </div>
    <div id="content"></div>
  </div>
</div>

<script>
let token = sessionStorage.getItem("amh_dash") || "";
const esc = (v) => String(v ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmtTime = (t) => t ? new Date(t * 1000).toLocaleString("zh-CN") : "未知";
const OFF = "未启用";
const routeText = (r) => Object.entries(r || {}).map(([k, v]) =>
  v === OFF ? `<span class="pill off">${esc(k)}·${OFF}</span>` :
  v === null || v === undefined ? "" : `<span class="pill">${esc(k)}#${esc(v)}</span>`).join("");
const stateText = (s) => s && s.startsWith("dropped:") ? `<span class="pill drop">${esc(s)}</span>` :
  ["packed","emitted","confirmed","cited"].includes(s) ? `<span class="pill ok">${esc(s)}</span>` : `<span class="pill">${esc(s || "candidate")}</span>`;
const timeOf = (c) => {
  const t = c.times || {};
  for (const k of ["confirmed", "content_modified", "content_created"]) if (t[k]) return fmtTime(t[k].value) + "（" + k + "·" + t[k].source + "）";
  return "未知";
};

function saveToken() {
  const v = document.getElementById("token").value.trim();
  if (!v) return;
  token = v; sessionStorage.setItem("amh_dash", v); load();
}
function logout() { sessionStorage.removeItem("amh_dash"); location.reload(); }

// Before login the login card is the only visible place; after login the
// operations row is. A rejected token always brings the login card back.
function fail(message, relogin) {
  const loggedIn = document.getElementById("ops").style.display === "block";
  if (relogin || !loggedIn) {
    document.getElementById("ops").style.display = "none";
    document.getElementById("login").style.display = "";
    document.getElementById("loginError").textContent = message;
    document.getElementById("opsError").textContent = "";
    return;
  }
  document.getElementById("opsError").textContent = message;
}

async function load() {
  const trace = document.getElementById("traceInput").value.trim();
  if (trace && !/^[a-f0-9]{24}$/.test(trace)) { fail("trace_id 格式错误：应为 24 位小写十六进制", false); return; }
  // A Feishu administrator session (HttpOnly cookie) is tried first; an
  // explicit host-minted token is the operator fallback and never mixes with it.
  const init = { credentials: "same-origin", cache: "no-store", headers: {} };
  if (token) init.headers["Authorization"] = "Bearer " + token;
  let r;
  try {
    r = await fetch("/native/v1/admin/recalls/data" + (trace ? "?trace_id=" + encodeURIComponent(trace) : ""), init);
  } catch (e) { fail("网络错误", false); return; }
  if (r.status === 401) { fail(token ? "令牌无效或已撤销" : "未登录：请先在管理后台用飞书登录，或输入管理令牌", true); return; }
  if (r.status === 422) { fail("trace_id 格式错误：应为 24 位小写十六进制", false); return; }
  if (!r.ok) { fail("读取失败（HTTP " + r.status + "）", false); return; }
  let d;
  try { d = await r.json(); } catch (e) { fail("响应无法解析", false); return; }
  document.getElementById("login").style.display = "none";
  document.getElementById("ops").style.display = "block";
  document.getElementById("loginError").textContent = "";
  document.getElementById("opsError").textContent = "";
  document.getElementById("stamp").textContent = "数据时间 " + fmtTime(d.generated_at);
  document.getElementById("content").innerHTML = trace ? renderTrace(d.traces[0]) : renderList(d.traces);
}

function renderList(traces) {
  if (!traces.length) return `<div class="card empty">暂无召回记录</div>`;
  return `<div class="card"><table>
    <thead><tr><th>时间</th><th>通道</th><th>问题</th><th class="num">候选</th><th class="num">入选</th><th class="num">已送达</th><th>阶段</th><th>trace</th></tr></thead>
    <tbody>${traces.map(t => `<tr>
      <td class="muted">${fmtTime(t.created)}</td><td>${esc(t.channel)}</td>
      <td>${esc(t.query_preview || "（未保存问题文本）")}</td>
      <td class="num">${t.counts.candidates}</td><td class="num">${(t.counts.selected||0)+(t.counts.packed||0)+(t.counts.emitted||0)+(t.counts.confirmed||0)+(t.counts.cited||0)}</td>
      <td class="num">${(t.counts.emitted||0)+(t.counts.confirmed||0)+(t.counts.cited||0)}</td>
      <td>${Object.entries(t.stages||{}).map(([k,v]) => `<span class="pill ${v===OFF?"off":""}">${esc(k)}·${esc(v)}</span>`).join("")}</td>
      <td><button class="link mono" onclick="document.getElementById('traceInput').value='${esc(t.trace_id)}';load()">${esc(t.trace_id)}</button></td>
    </tr>`).join("")}</tbody></table></div>`;
}

function renderTrace(t) {
  if (!t) return `<div class="card empty">没有这条记录</div>`;
  const head = `<div class="card"><dl>
    <dt>trace</dt><dd class="mono">${esc(t.trace_id)}</dd>
    <dt>时间</dt><dd>${fmtTime(t.created)}</dd>
    <dt>通道</dt><dd>${esc(t.channel)}</dd>
    <dt>问题</dt><dd>${esc(t.query_preview || "（未保存问题文本）")} <span class="muted">· ${t.query_chars} 字</span></dd>
    <dt>阶段</dt><dd>${Object.entries(t.stages||{}).map(([k,v]) => `<span class="pill ${v===OFF?"off":""}">${esc(k)}·${esc(v)}</span>`).join("")}</dd>
    <dt>降级原因</dt><dd>${(t.retrieval && t.retrieval.reasons || []).map(r => esc(r)).join("、") || "无"}</dd>
  </dl></div>`;
  const rows = t.candidates.map(c => `<tr>
    <td class="num">${c.final_rank ?? ""}<span class="muted">/${c.rank ?? "—"}</span></td>
    <td>${esc(c.title || c.path)}</td>
    <td>${esc(c.role)}<br><span class="muted">${esc(c.nature ? c.nature.value + "·" + c.nature.provenance : "")}</span></td>
    <td>${esc(c.product)}</td>
    <td>${esc(c.project || "")}</td>
    <td>${c.producer && c.producer.unknown ? "未知" : esc(c.producer && c.producer.name)}<br><span class="muted">上传 ${esc(c.uploader || "")}</span></td>
    <td>${routeText(c.routes)}</td>
    <td>${stateText(c.state)}${c.state_detail ? `<br><span class="muted">${esc(JSON.stringify(c.state_detail))}</span>` : ""}</td>
    <td class="muted">${timeOf(c)}</td>
    <td><details><summary>展开</summary>
      <dl>
        <dt>资产</dt><dd class="mono">${esc(c.asset_id)} r${esc(c.revision)} 分片 ${c.chunk ? (c.chunk.part_index+1)+"/"+c.chunk.part_count : "1/1"}</dd>
        <dt>路径</dt><dd class="mono">${esc(c.path)}</dd>
        <dt>设备</dt><dd class="mono">${esc(c.device)}</dd>
        <dt>衰减</dt><dd>${c.decay ? `半衰期 ${c.decay.half_life_days} 天 · 年龄 ${c.decay.age_days ?? "未知"} 天 · 系数 ${c.decay.coefficient} · 权重 ${c.decay.weight} · ${esc(c.decay.reason)}` : OFF}</dd>
        <dt>信号</dt><dd>${c.signals ? esc(JSON.stringify(c.signals)) : "无"}</dd>
        <dt>时间</dt><dd>${Object.entries(c.times||{}).map(([k,v]) => `${esc(k)}: ${v ? fmtTime(v.value) + "（" + esc(v.source) + "）" : "未知"}`).join("<br>")}</dd>
        <dt>阶段</dt><dd><table>${(c.stages||[]).map(s => `<tr><td>${esc(s.stage)}</td><td class="num">${s.before_rank ?? "—"} → ${s.after_rank ?? "—"}</td><td class="num mono">${s.before_score ?? "—"} → ${s.after_score ?? "—"}</td><td class="muted">${esc(s.effect || "")}</td></tr>`).join("")}</table></dd>
        <dt>事件</dt><dd>${(c.events||[]).map(e => `${esc(e.event)} @ ${fmtTime(e.at)}`).join("<br>") || "无"}</dd>
      </dl></details></td>
  </tr>`).join("");
  return head + `<div class="card"><table>
    <thead><tr><th>排名</th><th>记忆标题</th><th>内容形态</th><th>来源产品</th><th>组织/项目</th><th>原生产者</th><th>召回方式</th><th>本轮状态</th><th>时间</th><th></th></tr></thead>
    <tbody>${rows || `<tr><td colspan="10" class="empty">没有候选</td></tr>`}</tbody></table></div>`;
}

load();
</script>
</body>
</html>
"""


def html() -> str:
    """Return the recall ledger page; it carries no secret and no memory content."""
    return PAGE
