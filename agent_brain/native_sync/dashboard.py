"""The management cockpit for a self-hosted Hub.

Leads with what needs a decision, then a balanced scorecard, then the evidence:
trend and funnel, adoption, knowledge assets, people, knowledge gaps and system
health. It renders counts, names and titles only, never memory text, so it is
safe to leave open on a shared screen.

Feishu browser sessions are shared with the administrator page. A host-issued
recovery token remains an explicit operator fallback. No external asset: a Hub
often runs on an internal network.
"""

from __future__ import annotations

PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>组织记忆 · 经营驾驶舱</title>
<style>
  /* AMH_THEME */
  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; scroll-padding-top: 76px; }
  body { margin: 0; background: var(--page); color: var(--ink); font: 14px/1.65 -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Segoe UI", system-ui, sans-serif; -webkit-font-smoothing: antialiased; }
  a { color: inherit; }
  button, select, input { font: inherit; color: inherit; }
  .shell { max-width: 1360px; margin: 0 auto; padding: 0 32px; }

  /* Top bar */
  .bar { position: sticky; top: 0; z-index: 20; background: color-mix(in srgb, var(--surface) 88%, transparent); backdrop-filter: saturate(160%) blur(14px); border-bottom: 1px solid var(--ring); }
  .bar .shell { display: flex; align-items: center; gap: 22px; height: 60px; }
  .brand { display: flex; align-items: center; gap: 10px; font-weight: 600; letter-spacing: .01em; white-space: nowrap; }
  .brand svg { display: block; }
  .brand small { display: none; }
  .nav { display: flex; gap: 2px; overflow-x: auto; scrollbar-width: none; flex: 1; min-width: 0; }
  .nav a { padding: 6px 8px; border-radius: 7px; text-decoration: none; color: var(--ink-2); font-size: 13px; white-space: nowrap; }
  .nav a:hover { background: var(--wash); color: var(--ink); }
  .tools { display: flex; align-items: center; gap: 8px; white-space: nowrap; }
  .seg { display: inline-flex; padding: 2px; border-radius: 8px; background: var(--wash); }
  .seg button { border: 0; background: none; padding: 4px 11px; border-radius: 6px; font-size: 13px; color: var(--ink-2); cursor: pointer; }
  .seg button[aria-pressed="true"] { background: var(--surface); color: var(--ink); font-weight: 600; box-shadow: 0 0 0 1px var(--ring); }
  .ghost { border: 1px solid var(--ring); background: none; border-radius: 7px; padding: 4px 10px; font-size: 13px; color: var(--ink-2); cursor: pointer; text-decoration: none; }
  .ghost:hover { background: var(--wash); color: var(--ink); }
  :focus-visible { outline: 2px solid var(--s1); outline-offset: 2px; }

  /* Hero */
  .hero { background: linear-gradient(135deg, var(--hero) 0%, var(--hero-2) 100%); color: var(--hero-ink); padding: 40px 0 100px; }
  .hero .shell { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(320px, 1fr); gap: 56px; align-items: end; }
  .eyebrow { font-size: 12.5px; letter-spacing: .08em; color: var(--hero-soft); }
  .hero h1 { margin: 10px 0 18px; font-size: 30px; line-height: 1.3; font-weight: 600; letter-spacing: .005em; }
  .conclusion { margin: 0; padding: 0; list-style: none; color: var(--hero-soft); font-size: 15px; line-height: 1.8; max-width: 62ch; }
  .conclusion li:first-child { color: var(--hero-ink); }
  .north { border-left: 1px solid var(--hero-line); padding-left: 40px; }
  .north .label { font-size: 13px; color: var(--hero-soft); }
  .north .figure { font-size: 68px; line-height: 1.1; font-weight: 600; margin: 4px 0 6px; letter-spacing: -.01em; }
  .north .delta { font-size: 13.5px; color: var(--hero-soft); }
  .north .hint { font-size: 12.5px; color: var(--hero-soft); margin-top: 10px; opacity: .85; }
  .north svg { display: block; margin-top: 16px; width: 100%; height: 44px; overflow: visible; }

  /* Sections */
  main.shell { margin-top: -64px; padding-bottom: 72px; position: relative; }
  section { margin-top: 44px; }
  section:first-child { margin-top: 0; }
  .head { display: flex; align-items: baseline; gap: 12px; margin: 0 0 16px; flex-wrap: wrap; }
  .head h2 { margin: 0; font-size: 20px; font-weight: 600; letter-spacing: .005em; }
  .head p { margin: 0; color: var(--ink-2); font-size: 13.5px; }
  .card { background: var(--surface); border-radius: 12px; box-shadow: var(--shadow), 0 0 0 1px var(--ring); padding: 24px; min-width: 0; }
  .card h3 { margin: 0 0 4px; font-size: 15px; font-weight: 600; }
  .card .sub { margin: 0 0 16px; color: var(--ink-2); font-size: 13px; }
  .grid { display: grid; gap: 18px; }
  .g2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .g3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .g4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .wide { grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); }
  .empty { color: var(--ink-3); padding: 28px 8px; text-align: center; font-size: 13.5px; }
  .note { color: var(--ink-3); font-size: 12.5px; margin: 14px 0 0; }

  /* Decisions */
  .decisions { padding: 8px 24px; }
  .decision { display: grid; grid-template-columns: 84px minmax(0, 1fr) minmax(0, 1fr) auto; gap: 18px; align-items: start; padding: 16px 0; border-top: 1px solid var(--grid); }
  .decision:first-child { border-top: 0; }
  .decision .what { font-weight: 600; }
  .decision .why { color: var(--ink-2); font-size: 13px; margin-top: 2px; }
  .decision .act { font-size: 13.5px; }
  .decision .act b { display: block; font-size: 12px; font-weight: 400; color: var(--ink-3); }
  .decision a { font-size: 13px; color: var(--ink-2); white-space: nowrap; text-underline-offset: 3px; }
  .tag { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; white-space: nowrap; }
  .tag i { width: 16px; height: 16px; border-radius: 50%; display: inline-grid; place-items: center; font: 700 10px/1 -apple-system, system-ui, sans-serif; color: #fff; }
  .tag.good i { background: var(--good); } .tag.warning i { background: var(--warning); color: #0b0b0b; }
  .tag.serious i { background: var(--serious); color: #0b0b0b; } .tag.critical i { background: var(--critical); } .tag.info i { background: var(--axis); }
  .tag.none { color: var(--ink-3); }
  .allclear { display: flex; gap: 10px; align-items: center; padding: 18px 0; }

  /* Scorecard */
  .perspective h3 { display: flex; justify-content: space-between; gap: 8px; align-items: center; }
  .indicator { padding: 14px 0; border-top: 1px solid var(--grid); }
  .indicator:first-of-type { border-top: 0; padding-top: 2px; }
  .indicator .name { display: flex; justify-content: space-between; gap: 8px; align-items: center; color: var(--ink-2); font-size: 13px; }
  .indicator .value { font-size: 25px; line-height: 1.3; font-weight: 600; margin-top: 2px; }
  .indicator .foot { font-size: 12.5px; color: var(--ink-3); display: flex; gap: 8px; flex-wrap: wrap; min-height: 20px; }
  .delta.up { color: var(--up); } .delta.down { color: var(--critical); } .delta.flat { color: var(--ink-3); }

  /* Charts */
  .chart { position: relative; }
  .chart svg { display: block; width: 100%; overflow: visible; }
  .chart text { fill: var(--axis); font-size: 11.5px; }
  .chart .label { fill: var(--ink-2); font-size: 12px; }
  .legend { display: flex; gap: 18px; flex-wrap: wrap; font-size: 12.5px; color: var(--ink-2); margin: 0 0 12px; }
  .legend span { display: inline-flex; align-items: center; gap: 7px; }
  .legend i { width: 14px; height: 3px; border-radius: 2px; display: inline-block; }
  .legend i.box { height: 10px; width: 10px; border-radius: 3px; }
  .tip { position: absolute; z-index: 5; pointer-events: none; background: var(--surface); box-shadow: 0 4px 18px rgba(0,0,0,.14), 0 0 0 1px var(--ring); border-radius: 8px; padding: 8px 11px; font-size: 12.5px; white-space: nowrap; opacity: 0; transition: opacity .08s; }
  .tip .t { color: var(--ink-3); margin-bottom: 3px; }
  .tip .r { display: flex; align-items: center; gap: 7px; }
  .tip .r b { font-weight: 600; min-width: 26px; font-variant-numeric: tabular-nums; }
  .tip .r i { width: 12px; height: 2px; border-radius: 1px; }
  details.table-view { margin-top: 14px; font-size: 13px; }
  details.table-view summary, details.fold summary { cursor: pointer; color: var(--ink-2); }

  /* Funnel */
  .frow { padding: 7px 0; }
  .frow .top { display: flex; justify-content: space-between; gap: 10px; font-size: 13px; color: var(--ink-2); }
  .frow .top b { color: var(--ink); font-weight: 600; font-variant-numeric: tabular-nums; }
  .frow .rate { color: var(--ink-3); font-size: 12px; margin-left: 8px; font-weight: 400; }
  .track { height: 12px; background: var(--track); border-radius: 4px; margin-top: 5px; overflow: hidden; }
  .track div { height: 100%; border-radius: 0 4px 4px 0; min-width: 2px; }
  .frow.leak .top span:first-child::after { content: "最大流失"; margin-left: 8px; font-size: 11.5px; color: var(--ink-2); background: var(--wash); padding: 1px 7px; border-radius: 99px; }

  /* Heatmap */
  .heat { width: 100%; border-collapse: separate; border-spacing: 3px; font-size: 12px; }
  .heat th { font-weight: 400; color: var(--ink-3); padding: 2px 6px; text-align: center; white-space: nowrap; }
  .heat th.left { text-align: left; }
  .heat td { text-align: center; border-radius: 4px; padding: 7px 2px; font-variant-numeric: tabular-nums; min-width: 34px; }
  .heat td.blank { background: none; }

  /* Matrix */
  .matrix-wrap { display: grid; grid-template-columns: 22px minmax(0, 1fr); grid-template-rows: minmax(0, 1fr) 22px; gap: 4px; }
  .axis-y { display: flex; flex-direction: column; justify-content: space-between; align-items: center; font-size: 12px; line-height: 1.25; color: var(--ink-3); text-align: center; }
  .axis-y span { writing-mode: vertical-rl; text-orientation: upright; letter-spacing: .08em; }
  .axis-x { grid-column: 2; text-align: center; font-size: 12px; color: var(--ink-3); }
  .matrix { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 3px; border-radius: 10px; overflow: hidden; background: var(--grid); }
  .cell { background: var(--surface); padding: 18px; min-height: 212px; }
  .cell.star { background: color-mix(in srgb, var(--s1) 9%, var(--surface)); }
  .cell .top { display: flex; justify-content: space-between; align-items: baseline; gap: 10px; }
  .cell .nm { font-weight: 600; }
  .cell .ct { font-size: 28px; font-weight: 600; line-height: 1.1; }
  .cell .mean { color: var(--ink-2); font-size: 12.5px; margin: 3px 0 10px; }
  .cell ul { list-style: none; padding: 0; margin: 0 0 10px; font-size: 12.5px; }
  .cell li { display: flex; justify-content: space-between; gap: 10px; padding: 2px 0; }
  .cell li span:first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cell li span:last-child { color: var(--ink-3); white-space: nowrap; font-variant-numeric: tabular-nums; }
  .cell .do { font-size: 12.5px; border-top: 1px solid var(--grid); padding-top: 8px; color: var(--ink-2); }

  /* Ranked bars */
  .rank { display: grid; grid-template-columns: minmax(0, 40%) minmax(0, 1fr) 52px; gap: 12px; align-items: center; padding: 5px 0; font-size: 13px; }
  .rank .n { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ink-2); }
  .rank .v { text-align: right; font-variant-numeric: tabular-nums; }
  .rank .b { height: 10px; background: var(--track); border-radius: 3px; overflow: hidden; }
  .rank .b div { height: 100%; background: var(--s1); border-radius: 0 3px 3px 0; min-width: 2px; }
  .statement { font-size: 16px; line-height: 1.55; margin: 0 0 14px; }
  .statement b { font-weight: 600; }

  /* Segmented bar */
  .segbar { display: flex; gap: 2px; height: 28px; margin: 4px 0 10px; }
  .segbar div { border-radius: 4px; color: #fff; font-size: 12px; line-height: 28px; padding-left: 8px; overflow: hidden; white-space: nowrap; min-width: 2px; }

  /* Tables */
  .table-wrap { overflow-x: auto; }
  table.data { width: 100%; border-collapse: collapse; font-size: 13.5px; }
  table.data th { text-align: left; font-weight: 400; color: var(--ink-3); font-size: 12.5px; padding: 8px 12px 8px 0; border-bottom: 1px solid var(--base); white-space: nowrap; }
  table.data td { padding: 10px 12px 10px 0; border-bottom: 1px solid var(--grid); vertical-align: middle; }
  table.data tr:last-child td { border-bottom: 0; }
  table.data .num { text-align: right; font-variant-numeric: tabular-nums; padding-left: 12px; }
  .inline-bar { display: inline-block; height: 6px; border-radius: 3px; background: var(--s1); vertical-align: middle; margin-right: 8px; }
  .state { font-size: 12.5px; color: var(--ink-2); white-space: nowrap; }
  .state::before { content: ""; display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px; background: var(--axis); }
  .state.active::before { background: var(--good); } .state.contributor::before { background: var(--s1); }

  /* Tiles */
  .tile .k { color: var(--ink-2); font-size: 12.5px; }
  .tile .val { font-size: 17px; font-weight: 600; margin-top: 2px; overflow-wrap: anywhere; line-height: 1.4; }
  .tile .val small { display: block; font-size: 12.5px; font-weight: 400; color: var(--ink-3); }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 22px; }
  .pill { font-size: 12px; padding: 1px 8px; border-radius: 99px; background: var(--wash); color: var(--ink-2); white-space: nowrap; }
  .pill.ok { color: var(--up); }
  details.fold { margin-top: 18px; font-size: 13px; }

  /* Sign-in */
  #login { max-width: 460px; margin: 88px auto; }
  #login .primary { display: inline-block; background: var(--s1); color: #fff; border-radius: 8px; padding: 9px 22px; text-decoration: none; font-weight: 600; }
  #login input { width: 100%; padding: 8px 10px; border: 1px solid var(--base); border-radius: 7px; background: var(--surface); margin: 8px 0; }
  #app { display: none; }
  #app.loading .shell > *, #app.loading main { transition: opacity .15s; opacity: .55; }
  #error { color: var(--critical); font-size: 13px; }
  footer { color: var(--ink-3); font-size: 12.5px; padding: 0 0 40px; line-height: 1.8; }
  footer p { margin: 0 0 4px; max-width: 110ch; }

  @media (max-width: 1160px) { .g4 { grid-template-columns: repeat(2, minmax(0, 1fr)); } .nav { display: none; } .brand { flex: 1; } .decision { grid-template-columns: 84px minmax(0, 1fr); } .decision .act { grid-column: 2; } .decision a { grid-column: 2; } }
  @media (max-width: 860px) {
    .shell { padding: 0 16px; }
    .hero { padding: 28px 0 80px; } .hero .shell { grid-template-columns: minmax(0, 1fr); gap: 28px; }
    .hero h1 { font-size: 24px; } .north { border-left: 0; padding-left: 0; border-top: 1px solid var(--hero-line); padding-top: 20px; } .north .figure { font-size: 52px; }
    .g2, .g3, .g4, .wide { grid-template-columns: minmax(0, 1fr); }
    .card { padding: 18px; } .brand small { display: none; }
    .bar .shell { flex-wrap: wrap; height: auto; padding-top: 10px; padding-bottom: 10px; gap: 10px; } .tools { flex-wrap: wrap; }
    .cell { min-height: 0; padding: 14px; } .rank { grid-template-columns: minmax(0, 46%) minmax(0, 1fr) 44px; }
  }
  @media (prefers-reduced-motion: reduce) { html { scroll-behavior: auto; } * { transition: none !important; } }
  @media print { .bar .tools, .bar .nav { display: none; } .hero { padding-bottom: 44px; } main { margin-top: 12px; } }
</style>
</head>
<body>
<div id="login" class="card">
  <h3>组织记忆 · 经营驾驶舱</h3>
  <p class="sub">仅组织管理员可见。页面不含记忆正文。</p>
  <p><a class="primary" href="/native/v1/admin/login?target=__TARGET__" onclick="beginAdminLogin('__TARGET__'); return false;">使用飞书登录</a></p>
  <p class="note" id="loginError" role="status"></p>
  <details class="fold"><summary>主机管理员运维入口</summary>
    <input id="token" type="password" placeholder="管理令牌" autocomplete="off">
    <button class="ghost" onclick="saveToken()">进入</button>
    <p class="note">在 Hub 主机执行 <code>amh-native admin --db &lt;库&gt; --label 你的名字</code> 取得令牌，只显示一次。</p>
  </details>
</div>

<div id="app">
  <header class="bar"><div class="shell">
    <div class="brand">
      <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true"><rect width="22" height="22" rx="6" fill="#0d366b"/><path d="M5 15.5 9 9l3.4 4.2L17 6.5" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
      组织记忆<small>经营驾驶舱</small>
    </div>
    <nav class="nav" aria-label="页面分区">
      <a href="#decisions">待决事项</a><a href="#scorecard">平衡计分卡</a><a href="#flow">趋势与漏斗</a><a href="#adoption">采用与留存</a><a href="#assets">知识资产</a><a href="#people">团队协作</a><a href="#gaps">知识缺口</a><a href="#health">系统健康</a>
    </nav>
    <div class="tools">
      <div class="seg" role="group" aria-label="统计窗口" id="windows"></div>
      <a class="ghost" href="/native/v1/admin/recalls">召回明细</a>
      <a class="ghost" href="/native/v1/admin">成员与设备</a>
      <button class="ghost" id="theme" aria-label="切换明暗主题" title="切换明暗主题">◐</button>
      <button class="ghost" onclick="logout()">退出</button>
    </div>
  </div></header>

  <div class="hero"><div class="shell">
    <div>
      <div class="eyebrow" id="period"></div>
      <h1>经营结论</h1>
      <ul class="conclusion" id="summary"></ul>
      <p id="error" role="alert"></p>
    </div>
    <div class="north" id="north"></div>
  </div></div>

  <main class="shell">
    <section id="decisions">
      <div class="card decisions" id="alerts"></div>
    </section>

    <section id="scorecard">
      <div class="head"><h2>平衡计分卡</h2><p>四个视角看组织记忆的经营状况；状态对照默认管理基准，不是组织目标。</p></div>
      <div class="grid g4" id="cards"></div>
      <details class="fold"><summary>指标口径与默认管理基准</summary><div class="table-wrap" id="definitions" style="margin-top:10px"></div></details>
    </section>

    <section id="flow">
      <div class="head"><h2>趋势与漏斗</h2><p>需求有多大，又有多少走到了「被用上」。</p></div>
      <div class="grid wide">
        <div class="card"><h3>每日走势</h3><p class="sub">召回次数、注入条数和有效使用次数，同一刻度。最后一天尚未结束。</p><div id="legend" class="legend"></div><div class="chart" id="trend"></div>
          <details class="table-view"><summary>查看数据表</summary><div class="table-wrap" id="trendTable"></div></details></div>
        <div class="card"><h3>转化漏斗</h3><p class="sub">按候选条目计。门控拦下大部分候选是设计如此，不是流失。</p><div id="funnel"></div></div>
      </div>
    </section>

    <section id="adoption">
      <div class="head"><h2>采用与留存</h2><p>用过一次不算，持续在用才是真的采用。</p></div>
      <div class="grid g2">
        <div class="card"><h3>周活跃成员</h3><p class="sub">最近 8 周，每 7 天一组，标注的是该组最后一天。</p><div class="chart" id="wau"></div></div>
        <div class="card"><h3>留存队列</h3><p class="sub">按首次使用的那一周分组，看之后每周还剩多少比例。</p><div class="table-wrap" id="cohorts"></div></div>
      </div>
    </section>

    <section id="assets">
      <div class="head"><h2>知识资产</h2><p>像管产品组合一样管记忆：哪些该放大，哪些该改写，哪些该归档。</p></div>
      <div class="grid wide">
        <div class="card"><h3>资产组合矩阵</h3><p class="sub" id="matrixNote"></p><div class="matrix-wrap"><div class="axis-y" role="img" aria-label="纵轴是使用率，上高下低"><b style="font-weight:400">高</b><span>使用率</span><b style="font-weight:400">低</b></div><div class="matrix" id="matrix"></div><div class="axis-x">命中频次：低 → 高</div></div></div>
        <div class="grid">
          <div class="card"><h3>集中度</h3><p class="statement" id="pareto"></p><div id="paretoBars"></div></div>
          <div class="card"><h3>保鲜度</h3><p class="sub">按最后更新时间。</p><div id="fresh"></div></div>
        </div>
      </div>
    </section>

    <section id="people">
      <div class="head"><h2>团队协作</h2><p>知识从谁流向谁。用来发现协作机会和关键人风险，不作绩效考核依据。</p></div>
      <div class="card"><div class="table-wrap" id="peopleTable"></div></div>
      <div class="grid g3" style="margin-top:18px">
        <div class="card"><h3>按发问产品</h3><p class="sub">召回从哪里发起。</p><div id="products"></div></div>
        <div class="card"><h3>按发问项目</h3><p class="sub">未登记的目录记为未知。</p><div id="projects"></div></div>
        <div class="card"><h3>按贡献来源</h3><p class="sub">当前资产来自哪个产品。</p><div id="sources"></div></div>
      </div>
    </section>

    <section id="gaps">
      <div class="head"><h2>知识缺口</h2><p>反复问却没有答案的问题，是最值得沉淀的知识。</p></div>
      <div class="card"><p class="sub" id="gapNote"></p><div class="table-wrap" id="gapTable"></div></div>
    </section>

    <section id="health">
      <div class="head"><h2>系统健康</h2><p>支撑上面这些数字的基础设施。</p></div>
      <div class="card"><div class="tiles" id="tiles"></div></div>
      <div id="leaks"></div>
      <div class="grid g2" style="margin-top:18px">
        <div class="card"><h3>检索模式与降级原因</h3><p class="sub">次数。</p><div id="modes"></div></div>
        <div class="card"><h3>候选未注入的原因</h3><p class="sub">条目数。</p><div id="dropped"></div></div>
      </div>
      <div class="card" style="margin-top:18px">
        <details class="fold" style="margin:0"><summary>采集范围与观测口径</summary>
          <p class="note" id="scope"></p>
          <div class="table-wrap" id="matrixObs" style="margin-top:12px"></div>
        </details>
      </div>
    </section>
  </main>
  <footer class="shell">
    <p>数字全部来自召回账本、使用判定和资产库的实际记录。拿不到的环节标「不可观测」，不记成零；页面不估算节省的工时或金额，因为没有数据能支撑。</p>
    <p id="stamp"></p>
  </footer>
</div>

<script>
/* AMH_ADMIN_SESSION */
let token = sessionStorage.getItem("amh_dash") || sessionStorage.getItem("amh_admin") || "";
let days = 30;
let last = null;
const WINDOWS = [[7, "7 天"], [30, "30 天"], [90, "90 天"], [365, "一年"]];
const PRODUCTS = {"claude-code": "Claude Code", "codex-cli": "Codex CLI", "codex-desktop": "Codex 桌面版", "cowork": "Cowork", "chatgpt": "ChatGPT", "unknown": "未知"};
const MODES = {hybrid: "混合（词法 + 语义）", lexical: "仅词法", unknown: "未知"};
const REASONS = {
  semantic_index_not_built: "语义索引未建", semantic_model_not_configured: "未配置语义模型",
  semantic_index_model_mismatch_rebuild_required: "索引与模型不一致", semantic_index_incomplete_rebuild_required: "语义索引未建完",
  gate_background_pending: "门控背景未就绪", hook_lexical_guard: "降级保护生效", recall_trace_not_recorded: "轨迹未记录"
};
const DROPS = {
  relevance_gate: "门控判定不相关", lexical_guard: "降级保护拦截", limit: "超过条数上限", near_duplicate: "近似重复", dedup: "内容重复",
  budget: "超出注入预算", covered: "本会话已注入过", revalidation: "版本已变化", declared_filter: "范围过滤", decay: "时间衰减"
};
const RUNS = {promoted: "已启用新门控", would_promote: "通过，未启用", rejected_gold_regression: "问题集退步，未启用", rejected_worse_on_use: "真实使用变差，未启用", insufficient_feedback: "使用反馈不足", insufficient_gold: "问题集不足", no_learned_gate: "没有可校准的门控", error: "运行出错"};
const STATUS = {good: ["✓", "达标"], warning: ["!", "关注"], serious: ["▲", "重要"], critical: ["✕", "预警"], info: ["i", "提示"]};
const SEVERITY = {critical: ["✕", "紧急"], serious: ["▲", "重要"], warning: ["!", "关注"], info: ["i", "提示"]};

/* All dynamic text goes through textContent: titles and names are untrusted. */
function h(tag, attrs, ...kids) {
  const svg = ["svg","path","line","rect","circle","text","g"].includes(tag);
  const node = svg ? document.createElementNS("http://www.w3.org/2000/svg", tag) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.setAttribute("class", v);
    else if (k === "style") node.style.cssText = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) node.append(kid.nodeType ? kid : String(kid));
  return node;
}
const $ = (id) => document.getElementById(id);
const put = (id, ...kids) => { const n = $(id); n.replaceChildren(...kids.flat().filter(Boolean)); return n; };
const num = (v) => v === null || v === undefined ? "—" : Math.abs(v) >= 100000 ? (v / 10000).toFixed(1) + " 万" : Number(v).toLocaleString("zh-CN");
const pct = (v) => v === null || v === undefined ? "—" : Math.round(v * 100) + "%";
const fmtValue = (v, unit) => v === null || v === undefined ? "—" : unit === "pct" ? pct(v) : unit === "dec" ? Number(v).toFixed(1) : num(v);
const fmtTime = (t) => t ? new Date(t * 1000).toLocaleString("zh-CN", {hour12: false}) : "—";
const ago = (t) => { if (!t) return "—"; const d = (Date.now() / 1000 - t) / 86400; return d < 1 ? "今天" : d < 2 ? "昨天" : Math.floor(d) + " 天前"; };
const empty = (text) => h("div", {class: "empty", text: text || "这段时间还没有数据"});
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function statusTag(kind, table) {
  const entry = (table || STATUS)[kind];
  if (!entry) return null;
  return h("span", {class: "tag " + kind}, h("i", {"aria-hidden": "true", text: entry[0]}), entry[1]);
}

function deltaText(item) {
  if (item.delta_kind === "new") return h("span", {class: "delta flat", text: "上期为零"});
  if (item.delta === null || item.delta === undefined) return null;
  const value = item.delta;
  const small = item.delta_kind === "points" ? Math.abs(value) < 0.005 : Math.abs(value) < 0.005;
  const text = (value > 0 ? "↑ " : value < 0 ? "↓ " : "") + (item.delta_kind === "points"
    ? Math.abs(Math.round(value * 100)) + " 个百分点" : Math.abs(Math.round(value * 100)) + "%");
  if (small) return h("span", {class: "delta flat", text: "持平"});
  const good = item.higher_is_better === null ? null : (value > 0) === item.higher_is_better;
  return h("span", {class: "delta " + (good === null ? "flat" : good ? "up" : "down"), text});
}

function saveToken() {
  const v = $("token").value.trim();
  if (!v) return;
  token = v; sessionStorage.setItem("amh_dash", v); load();
}
function logout() { return signOutAdmin(); }

async function load() {
  if (!token && !await ensureAdminSession("loginError", "__TARGET__")) return;
  $("app").classList.add("loading");
  let r;
  try {
    r = await fetch("/native/v1/admin/cockpit/data?days=" + days, {credentials: "same-origin", cache: "no-store", headers: token ? {"Authorization": "Bearer " + token} : {}});
  } catch (e) { $("loginError").textContent = "网络错误，请重试。"; $("error").textContent = "网络错误，显示的是上一次的数据。"; $("app").classList.remove("loading"); return; }
  $("app").classList.remove("loading");
  if (r.status === 401) { token = ""; sessionStorage.removeItem("amh_dash"); sessionStorage.removeItem("amh_admin"); $("app").style.display = "none"; $("login").style.display = "block"; $("loginError").textContent = "登录已失效，请重新登录。"; return; }
  if (!r.ok) { $("loginError").textContent = "读取失败（HTTP " + r.status + "）"; $("error").textContent = "读取失败（HTTP " + r.status + "）。"; return; }
  let d;
  try { d = await r.json(); } catch (e) { $("error").textContent = "响应无法解析。"; return; }
  $("login").style.display = "none";
  $("app").style.display = "block";
  $("error").textContent = "";
  last = d;
  render(d);
}

function render(d) {
  put("windows", WINDOWS.map(([value, label]) => h("button", {"aria-pressed": String(value === days), onclick: () => { days = value; load(); }, text: label})));
  $("period").textContent = "经营周期 " + d.period.start + " 至 " + d.period.end + (d.period.has_previous ? "，与上一周期对比" : "，上一周期没有数据，不做对比");
  put("summary", d.summary.map((line) => h("li", {text: line})));
  renderNorth(d.north_star, d);
  renderAlerts(d.alerts);
  renderScorecard(d.scorecard);
  renderTrend(d);
  renderFunnel(d);
  renderWeekly(d.weekly);
  renderAssets(d.assets);
  renderPeople(d);
  renderGaps(d);
  renderHealth(d);
  $("stamp").textContent = "数据时间 " + fmtTime(d.generated_at) + " · Hub " + d.health.version;
}

function renderNorth(n, d) {
  const delta = n.previous === null || n.previous === undefined ? "上一周期无数据" :
    n.previous === 0 ? (n.value ? "上一周期为零" : "与上一周期持平") :
    n.value === n.previous ? "与上一周期持平" :
    (n.value > n.previous ? "↑ " : "↓ ") + Math.abs(Math.round((n.value - n.previous) / n.previous * 100)) + "%，上一周期 " + num(n.previous);
  const box = put("north",
    h("div", {class: "label", text: "北极星指标 · " + n.label}),
    h("div", {class: "figure", text: num(n.value)}),
    h("div", {class: "delta", text: delta}),
    spark(n.series),
    h("div", {class: "hint", text: n.hint}));
  return box;
}

function spark(series) {
  const w = 420, ht = 44, max = Math.max(1, ...series), step = w / Math.max(1, series.length - 1);
  const pts = series.map((v, i) => (i * step).toFixed(1) + "," + (ht - 3 - (v / max) * (ht - 8)).toFixed(1));
  if (series.length < 2) return null;
  return h("svg", {viewBox: "0 0 " + w + " " + ht, preserveAspectRatio: "none", role: "img", "aria-label": "周期内每日走势"},
    h("path", {d: "M0," + ht + " L" + pts.join(" L") + " L" + w + "," + ht + " Z", fill: "rgba(255,255,255,.10)"}),
    h("path", {d: "M" + pts.join(" L"), fill: "none", stroke: "#fff", "stroke-width": 1.75, "vector-effect": "non-scaling-stroke", "stroke-linejoin": "round", "stroke-linecap": "round"}));
}

function renderAlerts(alerts) {
  if (!alerts.length) { put("alerts", h("div", {class: "allclear"}, statusTag("good"), h("span", {text: "没有需要决策的异常。"}))); return; }
  put("alerts", alerts.map((a) => h("div", {class: "decision"},
    statusTag(a.severity, SEVERITY),
    h("div", {}, h("div", {class: "what", text: a.title}), h("div", {class: "why", text: a.evidence})),
    h("div", {class: "act"}, h("b", {text: "建议动作"}), a.action),
    h("a", {href: "#" + a.section, text: "查看依据"}))));
}

function renderScorecard(cards) {
  put("cards", cards.map((card) => {
    const states = card.indicators.map((i) => i.status);
    const worst = states.includes("critical") ? "critical" : states.includes("warning") ? "warning" : states.includes("good") ? "good" : null;
    return h("div", {class: "card perspective"},
      h("h3", {}, card.title, worst && statusTag(worst)),
      h("p", {class: "sub", text: card.question}),
      card.indicators.map((item) => h("div", {class: "indicator", title: item.hint},
        h("div", {class: "name"}, h("span", {text: item.label}), item.status !== "none" && statusTag(item.status)),
        h("div", {class: "value", text: fmtValue(item.value, item.unit)}),
        h("div", {class: "foot"}, deltaText(item), item.benchmark && h("span", {text: "基准 " + (item.unit === "pct" ? pct(item.benchmark.good) : item.benchmark.good)})))));
  }));
  put("definitions", h("table", {class: "data"},
    h("thead", {}, h("tr", {}, h("th", {text: "指标"}), h("th", {text: "口径"}), h("th", {class: "num", text: "达标"}), h("th", {class: "num", text: "关注"}))),
    h("tbody", {}, cards.flatMap((card) => card.indicators).map((item) => h("tr", {}, h("td", {text: item.label}), h("td", {text: item.hint}),
      h("td", {class: "num", text: item.benchmark ? "≥ " + pct(item.benchmark.good) : "—"}), h("td", {class: "num", text: item.benchmark ? "≥ " + pct(item.benchmark.warning) : "—"}))))));
}

const LINES = [["traces", "召回次数", "--s1"], ["emitted", "注入条数", "--s2"], ["used", "有效使用", "--s3"]];

function renderTrend(d) {
  const rows = d.trend;
  put("legend", LINES.map(([key, label, color]) => h("span", {}, h("i", {style: "background:var(" + color + ")"}), label + " " + num(rows.reduce((a, r) => a + r[key], 0)))));
  const box = $("trend");
  box.replaceChildren();
  if (!rows.some((r) => r.traces || r.emitted || r.used)) { box.append(empty()); put("trendTable"); return; }
  const width = Math.max(320, box.clientWidth || 640), height = 260, m = {l: 38, r: 12, t: 10, b: 24};
  const max = niceMax(Math.max(1, ...rows.flatMap((r) => LINES.map(([k]) => r[k]))));
  const x = (i) => m.l + (rows.length < 2 ? 0 : i * (width - m.l - m.r) / (rows.length - 1));
  const y = (v) => height - m.b - v / max * (height - m.t - m.b);
  const svg = h("svg", {viewBox: "0 0 " + width + " " + height, height, role: "img", "aria-label": "每日召回、注入与有效使用走势"});
  for (let i = 0; i <= 4; i++) {
    const v = max * i / 4;
    svg.append(h("line", {x1: m.l, x2: width - m.r, y1: y(v), y2: y(v), stroke: cssVar(i ? "--grid" : "--base"), "stroke-width": 1}));
    svg.append(h("text", {x: m.l - 8, y: y(v) + 4, "text-anchor": "end", text: Math.round(v)}));
  }
  const ticks = Math.min(6, rows.length);
  for (let i = 0; i < ticks; i++) {
    const at = ticks < 2 ? 0 : Math.round(i * (rows.length - 1) / (ticks - 1));
    svg.append(h("text", {x: x(at), y: height - 6, "text-anchor": at === 0 ? "start" : at === rows.length - 1 ? "end" : "middle", text: rows[at].day.slice(5)}));
  }
  for (const [key, , color] of LINES) {
    const stroke = cssVar(color);
    if (rows.length === 1) { svg.append(h("circle", {cx: x(0), cy: y(rows[0][key]), r: 4, fill: stroke})); continue; }
    svg.append(h("path", {d: "M" + rows.map((r, i) => x(i).toFixed(1) + "," + y(r[key]).toFixed(1)).join(" L"), fill: "none", stroke, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round"}));
  }
  const cross = h("line", {y1: m.t, y2: height - m.b, stroke: cssVar("--axis"), "stroke-width": 1, visibility: "hidden"});
  const dots = LINES.map(([, , color]) => h("circle", {r: 4, fill: cssVar(color), stroke: cssVar("--surface"), "stroke-width": 2, visibility: "hidden"}));
  svg.append(cross, ...dots);
  const tip = h("div", {class: "tip"});
  box.append(svg, tip);
  const show = (i) => {
    const r = rows[i];
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
    dots.forEach((dot, n) => { dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(r[LINES[n][0]])); dot.setAttribute("visibility", "visible"); });
    tip.replaceChildren(h("div", {class: "t", text: r.day}), ...LINES.map(([key, label, color]) => h("div", {class: "r"}, h("i", {style: "background:var(" + color + ")"}), h("b", {text: num(r[key])}), label)));
    tip.style.opacity = 1;
    const room = box.clientWidth - tip.offsetWidth - 8;
    tip.style.left = Math.max(0, Math.min(room, x(i) * (box.clientWidth / width) + 12)) + "px";
    tip.style.top = "8px";
  };
  const hide = () => { cross.setAttribute("visibility", "hidden"); dots.forEach((dot) => dot.setAttribute("visibility", "hidden")); tip.style.opacity = 0; };
  let focusAt = rows.length - 1;
  svg.setAttribute("tabindex", "0");
  svg.addEventListener("pointermove", (e) => { const rect = svg.getBoundingClientRect(); const px = (e.clientX - rect.left) * width / rect.width; focusAt = Math.max(0, Math.min(rows.length - 1, Math.round((px - m.l) / ((width - m.l - m.r) / Math.max(1, rows.length - 1))))); show(focusAt); });
  svg.addEventListener("pointerleave", hide);
  svg.addEventListener("focus", () => show(focusAt));
  svg.addEventListener("blur", hide);
  svg.addEventListener("keydown", (e) => { if (e.key === "ArrowLeft") focusAt = Math.max(0, focusAt - 1); else if (e.key === "ArrowRight") focusAt = Math.min(rows.length - 1, focusAt + 1); else return; e.preventDefault(); show(focusAt); });
  put("trendTable", h("table", {class: "data"},
    h("thead", {}, h("tr", {}, h("th", {text: "日期"}), LINES.map(([, label]) => h("th", {class: "num", text: label})), h("th", {class: "num", text: "未注入"}))),
    h("tbody", {}, rows.slice().reverse().filter((r) => r.traces || r.emitted || r.used).map((r) => h("tr", {}, h("td", {text: r.day}), LINES.map(([key]) => h("td", {class: "num", text: num(r[key])})), h("td", {class: "num", text: num(r.no_match)}))))));
}

function niceMax(v) {
  const base = Math.pow(10, Math.floor(Math.log10(v)));
  for (const f of [1, 2, 4, 5, 8, 10]) if (v <= f * base) return Math.max(4, f * base);
  return 10 * base;
}

function renderFunnel(d) {
  const STEPS = ["--r250", "--r350", "--r450", "--r500", "--r600", "--r700"];
  const stages = d.funnel.filter((f) => f.stage !== "cited").map((f) => ({label: f.label, count: f.count, stage: f.stage}));
  const used = d.usage.used + d.usage.consulted, judged = used + d.usage.unused;
  // Only some turns are judged, so its rate is against what was judged, not what was delivered.
  if (judged) stages.push({label: "被用上", count: used, stage: "used", rate: "占已判定 " + num(judged) + " 条的 " + pct(used / judged)});
  else stages.push({label: "被用上（尚无判定，不可观测）", count: null, stage: "used"});
  const top = Math.max(1, stages[0].count || 0);
  if (!stages[0].count) { put("funnel", empty()); return; }
  // The gate's refusals are by design; the leak worth a manager's look starts after selection.
  let leak = -1, worst = 1;
  for (let i = 2; i < stages.length; i++) {
    if (stages[i].count === null || !stages[i - 1].count) continue;
    const rate = stages[i].stage === "used" ? used / judged : stages[i].count / stages[i - 1].count;
    if (rate < worst && rate < 0.8) { worst = rate; leak = i; }
  }
  put("funnel", stages.map((s, i) => {
    const previous = i ? stages[i - 1].count : null;
    const rate = s.rate || (previous && s.count !== null ? "转化 " + Math.round(s.count / previous * 100) + "%" : "");
    return h("div", {class: "frow" + (i === leak ? " leak" : "")},
      h("div", {class: "top"}, h("span", {text: s.label}), h("span", {}, h("b", {text: s.count === null ? "—" : num(s.count)}), rate && h("span", {class: "rate", text: rate}))),
      h("div", {class: "track"}, s.count !== null && h("div", {style: "width:" + Math.max(s.count ? 1 : 0, s.count / top * 100) + "%;background:var(" + STEPS[i] + ")"})));
  }));
}

function renderWeekly(w) {
  const box = $("wau");
  box.replaceChildren();
  if (!w.active.some(Boolean)) box.append(empty()); else {
    const width = Math.max(300, box.clientWidth || 520), height = 200, m = {l: 8, r: 8, t: 20, b: 24};
    const max = Math.max(1, ...w.active), n = w.active.length, slot = (width - m.l - m.r) / n, bar = Math.min(44, slot * .56);
    const svg = h("svg", {viewBox: "0 0 " + width + " " + height, height, role: "img", "aria-label": "最近八周每周活跃成员"});
    svg.append(h("line", {x1: m.l, x2: width - m.r, y1: height - m.b, y2: height - m.b, stroke: cssVar("--base")}));
    w.active.forEach((v, i) => {
      const hh = v / max * (height - m.t - m.b), cx = m.l + slot * i + slot / 2, top = height - m.b - hh;
      const isLast = i === n - 1;
      svg.append(h("path", {d: v ? "M" + (cx - bar / 2) + "," + (height - m.b) + " V" + (top + 4) + " Q" + (cx - bar / 2) + "," + top + " " + (cx - bar / 2 + 4) + "," + top + " H" + (cx + bar / 2 - 4) + " Q" + (cx + bar / 2) + "," + top + " " + (cx + bar / 2) + "," + (top + 4) + " V" + (height - m.b) + " Z" : "M0,0", fill: cssVar(isLast ? "--s1" : "--r250")}));
      svg.append(h("text", {x: cx, y: top - 6, "text-anchor": "middle", class: "label", text: v}));
      svg.append(h("text", {x: cx, y: height - 7, "text-anchor": "middle", text: w.weeks[i]}));
    });
    box.append(svg);
  }
  if (!w.cohorts.length) { put("cohorts", empty("还没有可比较的队列")); return; }
  const cols = w.weeks.length;
  put("cohorts", h("table", {class: "heat"},
    h("thead", {}, h("tr", {}, h("th", {class: "left", text: "首次使用"}), h("th", {text: "人数"}), Array.from({length: cols}, (_, i) => h("th", {text: i ? "+" + i + " 周" : "当周"})))),
    h("tbody", {}, w.cohorts.map((c) => h("tr", {},
      h("th", {class: "left", text: c.week}), h("th", {text: c.size}),
      Array.from({length: cols}, (_, i) => {
        if (i >= c.kept.length) return h("td", {class: "blank"});
        const v = c.kept[i];
        const mix = Math.round(10 + v * 80);
        return h("td", {style: "background:color-mix(in srgb, var(--s1) " + mix + "%, var(--surface));color:" + (v > .55 ? "#fff" : "var(--ink)"), title: c.week + " 队列 " + (i ? "+" + i + " 周" : "当周") + "：" + pct(v), text: pct(v)});
      }))))));
}

function renderAssets(a) {
  $("matrixNote").textContent = "共 " + num(a.total) + " 条记忆和规则；命中至少 " + a.high_demand + " 次算高频，使用率 50% 以上算高。" + (a.unjudged ? a.unjudged + " 条被选中但还没有使用判定，暂未归类。" : "");
  const order = ["potential", "star", "dormant", "rework"];
  const byKey = Object.fromEntries(a.matrix.map((m) => [m.key, m]));
  put("matrix", order.map((key) => {
    const m = byKey[key];
    return h("div", {class: "cell " + key},
      h("div", {class: "top"}, h("span", {class: "nm", text: m.name}), h("span", {class: "ct", text: num(m.count)})),
      h("div", {class: "mean", text: m.meaning}),
      m.top.length ? h("ul", {}, m.top.map((t) => h("li", {}, h("span", {text: t.title, title: t.title}), h("span", {text: t.usage_rate === null ? "命中 " + t.selected : pct(t.usage_rate)})))) : null,
      h("div", {class: "do", text: "建议：" + m.action}));
  }));
  const p = a.pareto;
  if (p.assets) {
    put("pareto", "前 ", h("b", {text: p.head + " 条"}), "（20%）记忆贡献了 ", h("b", {text: pct(p.head_share)}), " 的注入。共 " + p.assets + " / " + a.total + " 条在本期被注入过。");
    const top = Math.max(1, ...p.top.map((t) => t.emitted));
    put("paretoBars", p.top.slice(0, 6).map((t) => rankRow(t.title, t.emitted, top)));
  } else { put("pareto", "本期还没有记忆被注入。"); put("paretoBars"); }
  const f = a.freshness, total = f.fresh + f.recent + f.stale;
  if (!total) { put("fresh", empty("还没有记忆")); return; }
  const parts = [["30 天内", f.fresh, "--r600"], ["31–90 天", f.recent, "--r450"], ["90 天以上", f.stale, "--r250"]];
  put("fresh",
    h("div", {class: "segbar", role: "img", "aria-label": parts.map(([n, v]) => n + " " + v + " 条").join("，")}, parts.filter(([, v]) => v).map(([n, v, c]) => h("div", {style: "flex:" + v + " 1 0;background:var(" + c + ");" + (c === "--r250" ? "color:#0b0b0b" : ""), title: n + "：" + v + " 条", text: v / total > .14 ? pct(v / total) : ""}))),
    h("div", {class: "legend"}, parts.map(([n, v, c]) => h("span", {}, h("i", {class: "box", style: "background:var(" + c + ")"}), n + " " + v))));
}

function rankRow(name, value, top, label) {
  return h("div", {class: "rank"}, h("div", {class: "n", text: name, title: name}), h("div", {class: "b"}, h("div", {style: "width:" + (value / top * 100) + "%"})), h("div", {class: "v", text: label || num(value)}));
}
function ranked(id, rows, name, value, text) {
  const list = rows.filter((r) => value(r));
  if (!list.length) { put(id, empty(text)); return; }
  const top = Math.max(...list.map(value));
  put(id, list.slice(0, 8).map((r) => rankRow(name(r), value(r), top)));
}

function renderPeople(d) {
  const rows = d.people;
  if (!rows.length) put("peopleTable", empty("还没有成员"));
  else {
    const maxAssets = Math.max(1, ...rows.map((r) => r.assets));
    const STATES = {active: ["active", "在用"], contributor: ["contributor", "只贡献"], silent: ["silent", "未使用"]};
    put("peopleTable", h("table", {class: "data"},
      h("thead", {}, h("tr", {}, ["成员", "本期状态", "贡献记忆"].map((t) => h("th", {text: t})), ["被他人复用", "受益成员", "发起任务", "有效使用"].map((t) => h("th", {class: "num", text: t})), h("th", {class: "num", text: "最近上传"}))),
      h("tbody", {}, rows.map((r) => h("tr", {},
        h("td", {text: r.name}),
        h("td", {}, h("span", {class: "state " + STATES[r.state][0], text: STATES[r.state][1]})),
        h("td", {}, h("span", {class: "inline-bar", style: "width:" + Math.round(r.assets / maxAssets * 90) + "px"}), num(r.assets)),
        [r.reused, r.consumers, r.tasks, r.used].map((v) => h("td", {class: "num", text: num(v)})),
        h("td", {class: "num", text: ago(r.last_upload)}))))));
  }
  const productName = (r) => PRODUCTS[r.name] || r.name;
  ranked("products", d.products, productName, (r) => r.traces);
  ranked("projects", d.projects, (r) => r.name, (r) => r.traces);
  ranked("sources", d.contribution.products, productName, (r) => r.assets);
}

function renderGaps(d) {
  const g = d.gaps;
  $("gapNote").textContent = "本期有 " + num(g.no_match_traces) + " 次提问没有注入记忆，其中大多是日常提问，门控不注入是正常的；值得看的是下面反复出现的问题（只看最近 " + g.scanned_days + " 天的明细）。";
  const question = (x) => x.preview || "（未保存问题文本）" + x.query_digest;
  if (!g.repeated.length) { put("gapTable", empty("没有反复出现的无答案问题")); return; }
  put("gapTable", h("table", {class: "data"},
    h("thead", {}, h("tr", {}, h("th", {text: "问题"}), h("th", {class: "num", text: "次数"}), h("th", {class: "num", text: "最近"}))),
    h("tbody", {}, g.repeated.map((x) => h("tr", {}, h("td", {text: question(x)}), h("td", {class: "num", text: num(x.count)}), h("td", {class: "num", text: ago(x.last)}))))));
}

function renderHealth(d) {
  const x = d.health, t = x.autotune || {}, run = t.last_run || null, labels = t.labels_30d || {};
  const judged = (labels.used || 0) + (labels.unused || 0) + (labels.consulted || 0);
  const ready = x.semantic_state === "ready";
  const tile = (k, val, small) => h("div", {class: "tile"}, h("div", {class: "k", text: k}), h("div", {class: "val"}, val, small && h("small", {text: small})));
  put("tiles",
    tile("语义检索", statusTag(ready ? "good" : "critical") , ready ? (x.semantic_model || "") : (x.semantic_reason || x.semantic_state)),
    tile("相关性门控", x.gate || "未运行", ready ? "每周按使用反馈校准" : "语义可用后才工作"),
    tile("自动校准", run ? (RUNS[run.status] || run.status) : "尚未运行", run && run.at ? fmtTime(run.at) : "问题集 " + num(t.gold_cases || 0) + " 题"),
    tile("使用反馈（30 天）", num(judged) + " 条", "用上 " + num((labels.used || 0) + (labels.consulted || 0)) + " · 没用上 " + num(labels.unused || 0)),
    tile("设备", num(x.active_devices) + " / " + num(x.devices), "在用 / 全部"),
    tile("最近上传", ago(x.last_upload), fmtTime(x.last_upload)));
  const leaked = x.suspected_secrets || [];
  put("leaks", leaked.length ? h("div", {class: "card", style: "margin-top:18px"},
    h("h3", {}, statusTag("critical"), " 疑似含密钥的记忆"),
    h("p", {class: "sub", text: "只列名称和类别，不显示内容。修好源文件后，用 amh-native purge-asset --db <库> --asset-id <编号> --yes 彻底清除。"}),
    h("div", {class: "table-wrap"}, h("table", {class: "data"},
      h("thead", {}, h("tr", {}, h("th", {text: "记忆"}), h("th", {text: "类别"}), h("th", {text: "编号"}))),
      h("tbody", {}, leaked.map((row) => h("tr", {}, h("td", {text: row.title || "（无标题）"}), h("td", {text: row.reasons.join("、")}), h("td", {text: row.asset_id}))))))) : null);
  const modes = d.modes.map((m) => ({name: MODES[m.name] || m.name, count: m.count})).concat(d.reasons.map((m) => ({name: "降级：" + (REASONS[m.name.split(":")[0]] || m.name), count: m.count})));
  ranked("modes", modes, (r) => r.name, (r) => r.count);
  ranked("dropped", d.dropped.map((m) => ({name: DROPS[m.name] || m.name, count: m.count})), (r) => r.name, (r) => r.count);
  $("scope").textContent = (d.period.collection_since ? "采集起始 " + d.period.collection_since + "，之前的使用情况不可观测。" : "尚无召回记录。") +
    " 原始明细保留 " + d.trace_ttl_days + " 天，之后只留按天汇总（已冻结 " + d.rollup.frozen_days + " 天，近 " + d.rollup.live_days + " 天仍在重算）。" +
    (d.preview_enabled ? "" : " 问题预览已关闭，知识缺口只显示摘要。") + " 「送达」指下一次提问时在会话记录里看到投放标记，不等于用上；「被用上」由使用判定得出。";
  const ob = d.observability;
  const VALUE = {observed: "ok", explicit: "ok"};
  put("matrixObs", h("table", {class: "data"},
    h("thead", {}, h("tr", {}, h("th", {text: "接入方式"}), ob.stages.map((s) => h("th", {text: s.label})), h("th", {text: "说明"}))),
    h("tbody", {}, ob.rows.map((row) => h("tr", {}, h("td", {text: row.label}),
      ob.stages.map((s) => h("td", {}, h("span", {class: "pill " + (VALUE[row.stages[s.key]] || ""), text: ob.values[row.stages[s.key]] || row.stages[s.key]}))),
      h("td", {text: row.note}))))));
}

function setTheme(next) {
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("amh_theme", next); } catch (e) { /* the page works without it */ }
  if (last) render(last);
}
try { const saved = localStorage.getItem("amh_theme"); if (saved) document.documentElement.setAttribute("data-theme", saved); } catch (e) { /* no storage */ }
$("theme").addEventListener("click", () => {
  const current = document.documentElement.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  setTheme(current === "dark" ? "light" : "dark");
});
let resizeTimer;
addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (last) { renderTrend(last); renderWeekly(last.weekly); } }, 120); });
load();
</script>
</body>
</html>
"""


def html(target: str = "dashboard") -> str:
    """Return the cockpit; it carries no secret and no memory content."""
    from .admin_ui import SCRIPT, TOKENS

    return (
        PAGE.replace("/* AMH_THEME */", TOKENS)
        .replace("/* AMH_ADMIN_SESSION */", SCRIPT)
        .replace("__TARGET__", target)
    )
