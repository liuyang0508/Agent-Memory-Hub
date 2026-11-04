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
  /* AMH_BASE */
  #ops { display: none; }
</style>
</head>
<body>
<!-- AMH_BAR -->
<div class="wrap">
  <h1>召回明细</h1>
  <p class="sub">每次召回记录候选的召回方式、排名变化和本轮状态；不含记忆正文。已用飞书登录管理后台的管理员直接可看；否则粘贴 Hub 主机签发的管理令牌。</p>

  <div id="login" class="card">
    <p><a href="/native/v1/admin/login?target=recalls">使用飞书登录</a></p>
    <div class="row">
      <input id="token" type="password" placeholder="管理令牌" size="44" autocomplete="off">
      <button class="primary" onclick="saveToken()">进入</button>
      <span class="muted" id="loginError"></span>
    </div>
  </div>

  <div id="ops">
    <div class="row" style="margin-bottom:8px">
      <input id="traceInput" placeholder="trace_id（可选）" size="30" class="mono">
      <select id="member" onchange="load()"><option value="">全部成员</option></select>
      <select id="channel" onchange="load()">
        <option value="">全部通道</option>
        <option value="hook">hook（Claude Code / Cowork）</option>
        <option value="tool">tool（MCP）</option>
        <option value="cli">cli</option>
        <option value="benchmark">benchmark</option>
      </select>
      <input id="day" type="date" onchange="load()">
      <button onclick="load()">查询</button>
      <button onclick="resetFilters()">最近记录</button>
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
const STAGE_NAMES = {lexical: "词法", semantic: "语义", hopfield: "联想", rerank: "重排", declared_filter: "范围过滤", decay: "时间衰减", mmr: "多样性", relevance_gate: "门控"};
const STATE_NAMES = {candidate: "候选", selected: "入选", packed: "已打包", emitted: "已注入", confirmed: "已送达", cited: "已引用", hinted: "只给标题"};
const DROP_NAMES = {relevance_gate: "门控判定不相关", lexical_guard: "降级保护拦截", limit: "超过条数上限", near_duplicate: "近似重复", dedup: "内容重复", budget: "超出注入预算", covered: "本会话已注入过", revalidation: "版本已变化"};
// A row shows what ran; the detail view also shows what was off.
const stagePills = (stages, all) => Object.entries(stages || {}).filter(([, v]) => all || v !== OFF).map(([k, v]) =>
  `<span class="pill ${v === OFF ? "off" : ""}" title="${esc(k)}">${esc(STAGE_NAMES[k] || k)}${v === "on" ? "" : " · " + esc(v)}</span>`).join("");
const stateText = (s) => s && s.startsWith("dropped:") ? `<span class="pill drop">未注入：${esc(DROP_NAMES[s.slice(8)] || s.slice(8))}</span>` :
  ["packed","emitted","confirmed","cited"].includes(s) ? `<span class="pill ok">${esc(STATE_NAMES[s])}</span>` : `<span class="pill">${esc(STATE_NAMES[s] || s || "候选")}</span>`;
let memberNames = {};
const memberName = (id) => memberNames[id] || (id ? String(id).slice(0, 8) + "…" : "未知");
const askingText = (a) => {
  if (!a || (!a.product && !a.project)) return `<span class="muted">未报</span>`;
  return esc(a.product || "？") + (a.project ? ` <span class="muted">/ ${esc(a.project)}</span>` : "");
};
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
function resetFilters() {
  for (const id of ["traceInput", "member", "channel", "day"]) document.getElementById(id).value = "";
  load();
}
function fillMembers(members) {
  const select = document.getElementById("member");
  if (!members) return;
  memberNames = Object.fromEntries(members.map(m => [m.id, m.name]));
  if (select.options.length > 1) return;
  for (const m of members) {
    const option = document.createElement("option");
    option.value = m.id; option.textContent = m.name;
    select.appendChild(option);
  }
}

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
  const params = new URLSearchParams();
  if (trace) params.set("trace_id", trace);
  for (const key of ["member", "channel", "day"]) {
    const value = document.getElementById(key).value;
    if (value && !trace) params.set(key, value);
  }
  const query = params.toString();
  let r;
  try {
    r = await fetch("/native/v1/admin/recalls/data" + (query ? "?" + query : ""), init);
  } catch (e) { fail("网络错误", false); return; }
  if (r.status === 401) { fail(token ? "令牌无效或已撤销" : "未登录：请先在管理后台用飞书登录，或输入管理令牌", true); return; }
  if (r.status === 422) { fail(trace ? "trace_id 格式错误：应为 24 位小写十六进制" : "筛选条件格式错误", false); return; }
  if (!r.ok) { fail("读取失败（HTTP " + r.status + "）", false); return; }
  let d;
  try { d = await r.json(); } catch (e) { fail("响应无法解析", false); return; }
  document.getElementById("login").style.display = "none";
  document.getElementById("ops").style.display = "block";
  document.getElementById("loginError").textContent = "";
  document.getElementById("opsError").textContent = "";
  document.getElementById("stamp").textContent = "数据时间 " + fmtTime(d.generated_at);
  fillMembers(d.members);
  document.getElementById("content").innerHTML = trace ? renderTrace(d.traces[0]) : renderList(d.traces);
}

function renderList(traces) {
  if (!traces.length) return `<div class="card empty">暂无召回记录</div>`;
  return `<div class="card"><table>
    <thead><tr><th>时间</th><th>通道</th><th>成员</th><th>发问自</th><th>问题</th><th class="num">候选</th><th class="num">入选</th><th class="num">已送达</th><th>阶段</th><th>trace</th></tr></thead>
    <tbody>${traces.map(t => `<tr>
      <td class="muted">${fmtTime(t.created)}</td><td>${esc(t.channel)}</td>
      <td>${esc(memberName(t.member))}</td>
      <td>${askingText(t.asking)}</td>
      <td>${esc(t.query_preview || "（未保存问题文本）")}</td>
      <td class="num">${t.counts.candidates}</td><td class="num">${(t.counts.selected||0)+(t.counts.packed||0)+(t.counts.emitted||0)+(t.counts.confirmed||0)+(t.counts.cited||0)}</td>
      <td class="num">${(t.counts.emitted||0)+(t.counts.confirmed||0)+(t.counts.cited||0)}</td>
      <td>${stagePills(t.stages)}</td>
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
    <dt>发问自</dt><dd>${askingText(t.asking)}${t.asking && t.asking.session_digest ? ` <span class="muted mono">任务 ${esc(t.asking.session_digest)}</span>` : ""}</dd>
    <dt>阶段</dt><dd>${stagePills(t.stages, true)}</dd>
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
    from .admin_ui import BASE, top_bar

    return PAGE.replace("/* AMH_BASE */", BASE).replace("<!-- AMH_BAR -->", top_bar("recalls"))
