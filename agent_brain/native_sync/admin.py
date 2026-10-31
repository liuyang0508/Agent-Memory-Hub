"""Minimal operator page: members, devices, invitations and login settings.

Served by the same process as the Hub so a self-hosted instance needs no extra
service. The page holds no credential of its own: the operator pastes the token
minted on the host, and the browser keeps it only in sessionStorage.
"""

from __future__ import annotations

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>组织记忆 Hub 管理</title>
<style>
  :root { color-scheme: light dark; --line: #d8dee9; --muted: #667085; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 15px/1.6 -apple-system, "PingFang SC", "Segoe UI", sans-serif; padding: 28px; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  h2 { font-size: 16px; margin: 32px 0 10px; }
  .sub { color: var(--muted); margin: 0 0 20px; font-size: 13px; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); }
  th { font-weight: 600; color: var(--muted); font-size: 13px; }
  input, button { font: inherit; padding: 7px 10px; border-radius: 6px; border: 1px solid var(--line); }
  button { cursor: pointer; }
  button.primary { border-color: #2563eb; }
  .row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin: 10px 0; }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; }
  .muted { color: var(--muted); font-size: 13px; }
  #token-box, #main { display: none; }
  pre { white-space: pre-wrap; word-break: break-all; background: rgba(127,127,127,.12); padding: 10px; border-radius: 6px; }
</style>
</head>
<body>
<h1>组织记忆 Hub</h1>
<p class="sub">本页只列出成员与设备，不含任何记忆正文。</p>

<div id="token-box">
  <p class="muted">请在 Hub 主机执行 <span class="mono">amh-native admin --db &lt;库&gt; --label 你的名字</span> 取得管理令牌，然后粘贴到这里。令牌只在本次浏览器会话内保存。</p>
  <div class="row">
    <input id="token" type="password" placeholder="管理令牌" size="44" autocomplete="off">
    <button class="primary" onclick="saveToken()">进入</button>
  </div>
  <p id="token-error" class="muted"></p>
</div>

<div id="main">
  <div class="row">
    <button onclick="logout()">退出</button>
    <span id="who" class="muted"></span>
  </div>

  <h2>成员</h2>
  <table><thead><tr><th>姓名</th><th>成员 ID</th><th>设备</th></tr></thead><tbody id="members"></tbody></table>

  <h2>设备</h2>
  <table><thead><tr><th>设备</th><th>所属成员</th><th>状态</th><th></th></tr></thead><tbody id="devices"></tbody></table>

  <h2>飞书登录</h2>
  <p class="muted">成员用飞书账号登录即可加入，没有邀请码。App Secret 保存后不会再显示。</p>
  <div class="row"><input id="s-app-id" placeholder="App ID（cli_ 开头）" size="34"></div>
  <div class="row"><input id="s-app-secret" type="password" placeholder="App Secret（留空表示不修改）" size="34"></div>
  <div class="row"><input id="s-public-url" placeholder="本站公网地址，如 https://amh.example.com" size="44"></div>
  <div class="row"><input id="s-tenant" placeholder="组织标识，如 linkcrux" size="24"></div>
  <div class="row"><button class="primary" onclick="saveSettings()">保存</button><span id="settings-msg" class="muted"></span></div>
  <p class="muted">回调地址请填：<span class="mono" id="callback"></span>（同时登记在飞书开发者后台的安全设置里）</p>
</div>

<script>
let token = sessionStorage.getItem("amh_admin") || "";
const api = async (path, body) => {
  const r = await fetch("/native/v1/admin" + path, {
    method: body ? "POST" : "GET",
    headers: { "Authorization": "Bearer " + token, "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401) { logout(); throw new Error("令牌无效或已撤销"); }
  if (!r.ok) throw new Error(await r.text());
  return r.json();
};
const esc = (v) => String(v ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const when = (t) => t ? new Date(t * 1000).toLocaleString("zh-CN") : "—";

function logout() { sessionStorage.removeItem("amh_admin"); token = ""; location.reload(); }
function saveToken() {
  token = document.getElementById("token").value.trim();
  if (!token) return;
  sessionStorage.setItem("amh_admin", token);
  load();
}

async function load() {
  if (!token) { document.getElementById("token-box").style.display = "block"; return; }
  let data;
  try { data = await api("/overview"); }
  catch (e) { document.getElementById("token-box").style.display = "block";
              document.getElementById("token-error").textContent = e.message; return; }
  document.getElementById("token-box").style.display = "none";
  document.getElementById("main").style.display = "block";
  document.getElementById("who").textContent = "已连接 " + location.host;

  document.getElementById("members").innerHTML = data.members.map(m =>
    `<tr><td>${esc(m.name)}</td><td class="mono">${esc(m.id)}</td><td>${m.devices}</td></tr>`).join("")
    || `<tr><td colspan="3" class="muted">还没有成员</td></tr>`;

  document.getElementById("devices").innerHTML = data.devices.map(d =>
    `<tr><td>${esc(d.name)}</td><td>${esc(d.member_name)}</td>
     <td>${d.revoked ? "已撤销" : "正常"}</td>
     <td>${d.revoked ? "" : `<button onclick="revoke('${esc(d.id)}')">撤销</button>`}</td></tr>`).join("")
    || `<tr><td colspan="4" class="muted">还没有设备</td></tr>`;

  // No invitation list: joining is done with a Feishu account.

  const s = data.settings || {};
  document.getElementById("s-app-id").value = s.feishu_app_id || "";
  document.getElementById("s-public-url").value = s.amh_hub_public_url || "";
  document.getElementById("s-tenant").value = s.feishu_tenant || "";
  updateCallback();
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
    return PAGE
