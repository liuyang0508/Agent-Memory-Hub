"""Shared browser session UI for the operator page and activity dashboard."""

# One look across the management pages: the data-viz palette and its surfaces.
TOKENS = r"""  :root {
    color-scheme: light;
    --page: #f6f5f1; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --ink-3: #6f6d68;
    --axis: #898781; --grid: #e1e0d9; --base: #c3c2b7; --ring: rgba(11,11,11,.09); --wash: rgba(11,11,11,.04);
    --hero: #0d2f5c; --hero-2: #0d366b; --hero-ink: #f7f8fa; --hero-soft: #c4d5ec; --hero-line: rgba(255,255,255,.18);
    --s1: #2a78d6; --s2: #eb6834; --s3: #1baf7a;
    --r250: #86b6ef; --r350: #5598e7; --r450: #2a78d6; --r500: #256abf; --r600: #184f95; --r700: #0d366b;
    --good: #0ca30c; --warning: #fab219; --serious: #ec835a; --critical: #d03b3b; --up: #006300;
    --track: #e8eef6; --shadow: 0 1px 2px rgba(13,47,92,.05), 0 8px 24px rgba(13,47,92,.05);
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --ink-3: #a9a8a0;
    --axis: #898781; --grid: #2c2c2a; --base: #383835; --ring: rgba(255,255,255,.10); --wash: rgba(255,255,255,.05);
    --hero: #10233f; --hero-2: #142c4f; --hero-line: rgba(255,255,255,.14);
    --s1: #3987e5; --s2: #d95926; --s3: #199e70; --r250: #184f95; --r350: #1c5cab; --r450: #256abf; --r500: #2a78d6; --r600: #3987e5; --r700: #5598e7;
    --up: #0ca30c; --track: #24282e; --shadow: none;
  }
  @media (prefers-color-scheme: dark) {
    :root:where(:not([data-theme="light"])) {
      color-scheme: dark;
      --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --ink-3: #a9a8a0;
      --axis: #898781; --grid: #2c2c2a; --base: #383835; --ring: rgba(255,255,255,.10); --wash: rgba(255,255,255,.05);
      --hero: #10233f; --hero-2: #142c4f; --hero-line: rgba(255,255,255,.14);
      --s1: #3987e5; --s2: #d95926; --s3: #199e70; --r250: #184f95; --r350: #1c5cab; --r450: #256abf; --r500: #2a78d6; --r600: #3987e5; --r700: #5598e7;
      --up: #0ca30c; --track: #24282e; --shadow: none;
    }
  }
"""

# The quieter pages share the cockpit's chrome.
BASE = TOKENS + r"""
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--page); color: var(--ink); font: 14px/1.65 -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Segoe UI", system-ui, sans-serif; -webkit-font-smoothing: antialiased; }
  a { color: var(--s1); text-underline-offset: 3px; }
  .topbar { background: var(--surface); border-bottom: 1px solid var(--ring); }
  .topbar .in { max-width: 1360px; margin: 0 auto; padding: 0 32px; height: 60px; display: flex; align-items: center; gap: 22px; }
  .topbar .brand { display: flex; align-items: center; gap: 10px; font-weight: 600; white-space: nowrap; color: var(--ink); text-decoration: none; }
  .topbar nav { display: flex; gap: 2px; }
  .topbar nav a { padding: 6px 10px; border-radius: 7px; text-decoration: none; color: var(--ink-2); font-size: 13.5px; white-space: nowrap; }
  .topbar nav a:hover { background: var(--wash); color: var(--ink); }
  .topbar nav a[aria-current="page"] { color: var(--ink); font-weight: 600; background: var(--wash); }
  .wrap { max-width: 1360px; margin: 0 auto; padding: 32px 32px 72px; }
  h1 { font-size: 24px; font-weight: 600; margin: 0 0 4px; }
  h2 { font-size: 17px; font-weight: 600; margin: 34px 0 12px; }
  .sub { color: var(--ink-2); font-size: 13.5px; margin: 0 0 22px; }
  .muted { color: var(--ink-3); }
  .card { background: var(--surface); border-radius: 12px; box-shadow: var(--shadow), 0 0 0 1px var(--ring); padding: 22px; margin-bottom: 18px; overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
  th, td { text-align: left; padding: 9px 12px 9px 0; border-bottom: 1px solid var(--grid); vertical-align: top; }
  th { color: var(--ink-3); font-size: 12.5px; font-weight: 400; white-space: nowrap; border-bottom-color: var(--base); }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  input, button, select { font: inherit; color: var(--ink); padding: 6px 11px; border-radius: 7px; border: 1px solid var(--ring); background: var(--surface); }
  button { cursor: pointer; }
  button:hover { background: var(--wash); }
  button.primary { background: var(--s1); border-color: var(--s1); color: #fff; font-weight: 600; }
  button.link { border: 0; background: none; color: var(--s1); padding: 0; }
  :focus-visible { outline: 2px solid var(--s1); outline-offset: 2px; }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; }
  .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .pill { display: inline-block; padding: 1px 8px; border-radius: 99px; font-size: 12px; background: var(--wash); color: var(--ink-2); margin: 1px 4px 1px 0; white-space: nowrap; }
  .pill.ok { color: var(--up); } .pill.off { color: var(--ink-3); } .pill.drop { color: var(--ink-2); box-shadow: inset 0 0 0 1px var(--ring); }
  .empty { color: var(--ink-3); padding: 22px 0; text-align: center; }
  details { margin: 6px 0; } summary { cursor: pointer; color: var(--ink-2); }
  dl { display: grid; grid-template-columns: max-content 1fr; gap: 2px 12px; margin: 6px 0; font-size: 12.5px; } dt { color: var(--ink-3); } dd { margin: 0; }
  @media (max-width: 860px) { .topbar .in, .wrap { padding-left: 16px; padding-right: 16px; } .topbar .in { height: auto; padding-top: 10px; padding-bottom: 10px; flex-wrap: wrap; gap: 8px; } }
"""


def top_bar(current: str) -> str:
    """The shared header of the quieter management pages."""
    links = (
        ("dashboard", "/native/v1/admin/dashboard", "经营驾驶舱"),
        ("recalls", "/native/v1/admin/recalls", "召回明细"),
        ("admin", "/native/v1/admin", "成员与设备"),
    )
    items = "".join(
        f'<a href="{href}"' + (' aria-current="page"' if key == current else "") + f">{label}</a>"
        for key, href, label in links
    )
    return (
        '<header class="topbar"><div class="in"><a class="brand" href="/native/v1/admin/dashboard">'
        '<svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true"><rect width="22" height="22" rx="6" fill="#0d366b"/>'
        '<path d="M5 15.5 9 9l3.4 4.2L17 6.5" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
        f"组织记忆</a><nav>{items}</nav></div></header>"
    )


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
