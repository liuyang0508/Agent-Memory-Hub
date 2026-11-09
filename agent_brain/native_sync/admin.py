"""Operator page: members, devices and enterprise login settings.

Served by the same process as the Hub so a self-hosted instance needs no extra
service. Feishu sessions use HttpOnly cookies; an explicit host-issued recovery
token remains available through the collapsed operator entry.
"""

from __future__ import annotations

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>组织记忆 Hub 管理</title>
<style>
  /* AMH_BASE */
  #main { display: none; }
  pre { white-space: pre-wrap; word-break: break-all; background: var(--wash); padding: 10px; border-radius: 6px; }
  .row { margin: 10px 0; }
</style>
</head>
<body>
<!-- AMH_BAR -->
<div class="wrap">
<h1>成员与设备</h1>
<p class="sub">本页只列出成员与设备，不含任何记忆正文。</p>

<div id="token-box">
  <p>使用飞书身份进入管理后台，系统将核验你的 Hub 管理员权限。</p>
  <p><a href="/native/v1/admin/login" onclick="beginAdminLogin('admin'); return false;">使用飞书登录</a></p>
  <details><summary>主机管理员运维入口</summary>
  <p class="muted">请在 Hub 主机执行 <span class="mono">amh-native admin --db &lt;库&gt; --label 你的名字</span> 取得管理令牌，然后粘贴到这里。令牌只在本次浏览器会话内保存。</p>
  <div class="row">
    <input id="token" type="password" placeholder="管理令牌" size="44" autocomplete="off">
    <button class="primary" onclick="saveToken()">进入</button>
  </div>
  </details>
  <p id="token-error" class="muted" role="status"></p>
</div>

<div id="main">
  <div class="row">
    <button onclick="logout()">退出</button>
    <span id="who" class="muted"></span>
  </div>

  <h2>成员</h2>
  <table><thead><tr><th>姓名</th><th>成员 ID</th><th>设备</th></tr></thead><tbody id="members"></tbody></table>

  <h2>设备</h2>
  <table><thead><tr><th>设备</th><th>设备 ID</th><th>所属成员</th><th>状态</th><th></th></tr></thead><tbody id="devices"></tbody></table>

  <h2>组织做梦</h2>
  <p class="muted">组织维基只由这里填的设备整理并上传（<span class="mono">amh-native dream --publish</span>），别的设备会被拒；同一时间只有一台在跑。设备 ID 见上表，多个用逗号分隔，留空表示谁也不能上传。</p>
  <div class="row"><input id="s-dream-devices" placeholder="允许做梦并上传维基的设备 ID" size="60"></div>
  <div class="row"><button class="primary" onclick="saveDream()">保存</button><span id="dream-msg" class="muted"></span></div>
  <p class="muted">默认不会自动做梦，也不会自动上传。要现在整理并上传一次，点下面的按钮：指定设备上的后台进程一分钟内领取，跑完把结果写回这里。要每晚自动跑，在指定设备上执行 <span class="mono">amh-native dream --schedule on</span>。</p>
  <div class="row"><button onclick="dreamNow()">现在做梦</button><span id="dream-now" class="muted"></span></div>
  <p id="dream-status" class="muted" role="status"></p>

  <h2>飞书登录</h2>
  <p class="muted">成员用飞书账号登录即可加入，没有邀请码。App Secret 保存后不会再显示。</p>
  <div class="row"><input id="s-app-id" placeholder="App ID（cli_ 开头）" size="34"></div>
  <div class="row"><input id="s-app-secret" type="password" placeholder="App Secret（留空表示不修改）" size="34"></div>
  <div class="row"><input id="s-public-url" placeholder="本站公网地址，如 https://amh.example.com" size="44"></div>
  <div class="row"><input id="s-tenant-key" placeholder="允许登录的飞书企业 tenant_key（必填）" size="44"></div>
  <div class="row"><input id="s-tenant" placeholder="组织标识，如 linkcrux" size="24"></div>
  <div class="row"><button class="primary" onclick="saveSettings()">保存</button><span id="settings-msg" class="muted"></span></div>
  <p class="muted">回调地址请填：<span class="mono" id="callback"></span>（同时登记在飞书开发者后台的安全设置里）</p>
</div>

</div>
<script>
/* AMH_ADMIN_SESSION */
let token = sessionStorage.getItem("amh_admin") || sessionStorage.getItem("amh_dash") || "";
const api = async (path, body) => {
  const r = await fetch("/native/v1/admin" + path, {
    method: body ? "POST" : "GET",
    headers: { ...(token ? {"Authorization": "Bearer " + token} : {}), "X-AMH-CSRF": adminCsrf, "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401) { throw new Error("登录已过期或权限已撤销，请重新登录。"); }
  if (!r.ok) throw new Error(await r.text());
  return r.json();
};
const esc = (v) => String(v ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const when = (t) => t ? new Date(t * 1000).toLocaleString("zh-CN") : "—";

function logout() { return signOutAdmin(); }
function saveToken() {
  token = document.getElementById("token").value.trim();
  if (!token) return;
  sessionStorage.setItem("amh_admin", token);
  load();
}

async function load() {
  if (!token && !await ensureAdminSession("token-error", "admin")) return;
  let data;
  try { data = await api("/overview"); }
  catch (e) { document.getElementById("main").style.display = "none"; document.getElementById("token-box").style.display = "block";
              document.getElementById("token-error").textContent = e.message; return; }
  document.getElementById("token-box").style.display = "none";
  document.getElementById("main").style.display = "block";
  document.getElementById("who").textContent = (token ? "运维令牌" : "管理员：" + adminMemberName) + " · " + location.host;

  document.getElementById("members").innerHTML = data.members.map(m =>
    `<tr><td>${esc(m.name)}</td><td class="mono">${esc(m.id)}</td><td>${m.devices}</td></tr>`).join("")
    || `<tr><td colspan="3" class="muted">还没有成员</td></tr>`;

  document.getElementById("devices").innerHTML = data.devices.map(d =>
    `<tr><td>${esc(d.name)}</td><td class="mono">${esc(d.id)}</td><td>${esc(d.member_name)}</td>
     <td>${d.revoked ? "已撤销" : "正常"}</td>
     <td>${d.revoked ? "" : `<button onclick="revoke('${esc(d.id)}')">撤销</button>`}</td></tr>`).join("")
    || `<tr><td colspan="5" class="muted">还没有设备</td></tr>`;

  // No invitation list: joining is done with a Feishu account.

  const s = data.settings || {};
  document.getElementById("s-app-id").value = s.feishu_app_id || "";
  document.getElementById("s-public-url").value = s.amh_hub_public_url || "";
  document.getElementById("s-dream-devices").value = s.dream_devices || "";
  document.getElementById("s-tenant").value = s.feishu_tenant || "";
  document.getElementById("s-tenant-key").value = s.feishu_tenant_key || "";
  updateCallback();
  loadDream();
}

function updateCallback() {
  const base = document.getElementById("s-public-url").value.trim().replace(/\\/+$/, "");
  document.getElementById("callback").textContent = base ? base + "/native/v1/auth/feishu/callback" : "（先填公网地址）";
}
document.getElementById("s-public-url").addEventListener("input", updateCallback);

async function revoke(id) {
  if (!confirm("撤销后该设备立即失去访问权限，确定？")) return;
  await api("/revoke", { device_id: id });
  load();
}

async function saveDream() {
  const msg = document.getElementById("dream-msg");
  try {
    await api("/settings", { key: "dream_devices", value: document.getElementById("s-dream-devices").value.trim() });
    msg.textContent = "已保存。";
    load();
  } catch (e) { msg.textContent = "保存失败：" + e.message; }
}

const OUTCOMES = {
  published: "已整理并上传", nothing_new: "没有新内容，维基没动", reviewed: "只整理未上传", failed: "失败",
  leak: "检出泄漏，未上传", not_designated: "设备未被指定", lease_held: "另一台正在做梦",
  hub_too_old: "Hub 版本太旧", no_request: "没有待处理请求", request_taken: "被另一台领走",
};
let dreamTimer = null;
function dreamText(d) {
  const parts = [];
  if (d.running) parts.push(`${d.running.device} 正在做梦（${when(d.running.since)} 开始）`);
  const r = d.request;
  if (!r) parts.push("还没有从这里触发过。");
  else if (r.pending && !r.device) parts.push(`已请求（${when(r.requested)}，${r.requested_by || "管理员"}），等待指定设备领取`);
  else if (r.pending) parts.push(`${r.device} 已领取（${when(r.claimed)}），正在跑`);
  else parts.push(`上次：${when(r.finished)} · ${OUTCOMES[r.outcome] || r.outcome}${r.message ? "：" + r.message : ""}`);
  if (!d.devices.length) parts.push("还没有指定做梦设备，先在上面填设备 ID。");
  return parts.join(" · ");
}
async function loadDream() {
  const box = document.getElementById("dream-status");
  try {
    const d = await api("/dream");
    box.textContent = dreamText(d);
    const busy = Boolean(d.running) || Boolean(d.request && d.request.pending);
    if (busy && !dreamTimer) dreamTimer = setInterval(loadDream, 10000);
    if (!busy && dreamTimer) { clearInterval(dreamTimer); dreamTimer = null; }
  } catch (e) { box.textContent = "做梦状态读取失败：" + e.message; }
}
async function dreamNow() {
  const msg = document.getElementById("dream-now");
  if (!confirm("指定设备会在一分钟内开始整理并上传组织维基，本次花费上限 12 美元等价，走该设备的 Claude 订阅。确定？")) return;
  try { await api("/dream/run", {}); msg.textContent = "已请求。"; loadDream(); }
  catch (e) { msg.textContent = "请求失败：" + e.message; }
}

async function saveSettings() {
  const msg = document.getElementById("settings-msg");
  try {
    const appId = document.getElementById("s-app-id").value.trim();
    const secret = document.getElementById("s-app-secret").value.trim();
    const url = document.getElementById("s-public-url").value.trim();
    const tenant = document.getElementById("s-tenant").value.trim();
    if (appId) await api("/settings", { key: "feishu_app_id", value: appId });
    if (url) await api("/settings", { key: "amh_hub_public_url", value: url });
    if (tenant) await api("/settings", { key: "feishu_tenant", value: tenant });
    await api("/settings", { key: "feishu_tenant_key", value: document.getElementById("s-tenant-key").value.trim() });
    if (secret) await api("/secret", { app_secret: secret });
    document.getElementById("s-app-secret").value = "";
    msg.textContent = "已保存。飞书登录是否生效可用 /native/v1/auth/feishu 检查。";
    load();
  } catch (e) { msg.textContent = "保存失败：" + e.message; }
}
load();
</script>
</body>
</html>"""


def html() -> str:
    """Return the operator page; it carries no secret and no inlined state."""
    from .admin_ui import BASE, SCRIPT, top_bar

    return (
        PAGE.replace("/* AMH_BASE */", BASE)
        .replace("<!-- AMH_BAR -->", top_bar("admin"))
        .replace("/* AMH_ADMIN_SESSION */", SCRIPT)
    )
