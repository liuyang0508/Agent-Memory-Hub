"""Shared browser session UI for the operator page and activity dashboard."""

SCRIPT = r'''
let adminCsrf = "";
let adminMemberName = "";
const adminLoginMessages = {
  denied: "该飞书账号尚未获得本组织的 Hub 管理员权限，请联系管理员。",
  expired: "登录已过期或浏览器会话不一致，请在当前浏览器重新登录。",
  cancelled: "已取消飞书授权，可以重新登录。",
  unavailable: "飞书登录暂时不可用，请稍后重试；主机管理员可使用运维入口。",
  logout: "已退出管理后台。",
  manual: "主机管理员可在运维入口使用管理令牌。"
};
function beginAdminLogin(target) {
  sessionStorage.removeItem("amh_admin");
  sessionStorage.removeItem("amh_dash");
  location.assign("/native/v1/admin/login?target=" + target);
}
async function ensureAdminSession(errorId, target) {
  const message = document.getElementById(errorId);
  try {
    const r = await fetch("/native/v1/admin/session", {credentials:"same-origin", cache:"no-store"});
    if (r.ok) {
      const s = await r.json();
      adminCsrf = s.csrf;
      adminMemberName = s.name;
      return true;
    }
    if (r.status !== 401) { message.textContent = "暂时无法检查登录状态，请稍后重试。"; return false; }
    const reason = new URLSearchParams(location.search).get("login");
    if (reason) {
      message.textContent = adminLoginMessages[reason] || "请使用飞书登录。";
      return false;
    }
    message.textContent = "正在前往飞书确认身份…";
    location.replace("/native/v1/admin/login?target=" + target);
  } catch (e) { message.textContent = "网络连接失败，请刷新重试。"; }
  return false;
}
async function signOutAdmin() {
  try {
    const s = await fetch("/native/v1/admin/session", {credentials:"same-origin", cache:"no-store"});
    if (s.ok) adminCsrf = (await s.json()).csrf;
    const r = await fetch("/native/v1/admin/logout", {
      method:"POST", credentials:"same-origin", headers:{"X-AMH-CSRF":adminCsrf}
    });
    if (!r.ok) throw new Error("logout failed");
    sessionStorage.removeItem("amh_admin");
    sessionStorage.removeItem("amh_dash");
    location.replace("/native/v1/admin?login=logout");
  } catch (e) { alert("退出未完成，请检查网络后重试。"); }
}
'''
