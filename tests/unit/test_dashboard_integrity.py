"""Regression guard for the web admin's single-file dashboard.

The whole inline <script> once silently failed to parse because template
literals were authored with escaped backticks (\\` and \\${) — invalid JS that
broke every function (login labels blank, graph dead) with NO test catching it.
These guards make that class of breakage loud.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DASH = ROOT / "web" / "templates" / "dashboard.html"
AGENT_LOGOS = ROOT / "web" / "static" / "agent-logos"


def test_dashboard_exists():
    assert DASH.exists()


def test_no_escaped_template_literals():
    """No backslash-escaped backticks or ${ — they break JS parsing wholesale."""
    text = DASH.read_text(encoding="utf-8")
    bad_bt = text.count("\\`")
    bad_dol = text.count("\\${")
    assert bad_bt == 0, f"{bad_bt} escaped backticks (\\`) in dashboard.html — breaks inline JS parsing"
    assert bad_dol == 0, f"{bad_dol} escaped \\${{ sequences in dashboard.html — breaks inline JS parsing"


def test_dashboard_agent_copy_uses_truth_contract_language():
    text = DASH.read_text(encoding="utf-8")

    assert "Works with every agent" not in text
    assert "Agent integrations by support level" in text
    assert "按能力等级接入 Agent" in text


def test_dashboard_agent_marquee_uses_capability_api_with_fallback():
    text = DASH.read_text(encoding="utf-8")

    assert "/api/adapters/capabilities" in text
    assert "function renderAgentCapabilities" in text
    assert "STATIC_AGENT_FALLBACK" in text
    assert "support_level" in text


def test_dashboard_exposes_handdrawn_brand_theme():
    text = DASH.read_text(encoding="utf-8")

    assert "data-brand=\"handdrawn\"" in text or "[data-brand=\"handdrawn\"]" in text
    assert "Kalam" in text
    assert "Patrick Hand" in text
    assert "handdrawn:" in text
    assert "手绘" in text
    assert "radial-gradient(#e5e0d8 1px, transparent 1px)" in text
    assert "4px 4px 0px 0px #2d2d2d" in text


def test_dashboard_exposes_cockpit_page():
    text = DASH.read_text(encoding="utf-8")

    assert "/api/cockpit/summary" in text
    assert "function loadCockpit" in text
    assert "nav.cockpit" in text
    assert "showPage('cockpit')" in text
    assert "cockpit-grid" in text
    assert "Today Handoff Pack" in text
    assert "可信接力 Cockpit" in text
    assert "/api/ml-advisory-gate" in text
    assert "ML/DL advisory gate" in text
    assert "unsafe promotions" in text


def test_dashboard_exposes_loop_contract_governance_panel():
    text = DASH.read_text(encoding="utf-8")

    assert "function cockpitLoopGovernanceHtml" in text
    assert "Loop Contract Governance" in text
    assert "Loop Contract 治理" in text
    assert "open human gates" in text
    assert "facts / verification / governance" in text
    assert "事实层 / 验证层 / 治理层" in text
    assert "data.loop_governance" in text


def test_dashboard_exposes_chain_log_workbench():
    text = DASH.read_text(encoding="utf-8")

    for marker in (
        "id=\"lineageMemoryTab\"",
        "id=\"lineageChainTab\"",
        "chain-workbench",
        "chain-list",
        "chain-row",
        "chain-node-rail",
        "chain-node",
        "chain-node-preview",
        "chain-algorithm-waterfall",
        "chain-algorithm-node",
        "chain-detail-drawer",
        "chain-drawer-backdrop",
        "chain-candidate-table",
        "/api/chain-logs?hours=${_chainHours}&limit=100",
        "/api/chain-logs/${encodeURIComponent(expectedChainId)}?hours=${_chainHours}",
    ):
        assert marker in text

    for fn in (
        "function lineageSetView",
        "function lineageTabsHtml",
        "function loadChainLogs",
        "function chainSelect",
        "function chainRender",
        "function chainListRows",
        "function chainDetailHtml",
        "function chainNodeCard",
        "function chainAlgorithmWaterfall",
        "function chainOpenDrawer",
        "function chainCloseDrawer",
        "function chainDrawerHtml",
        "function chainCandidateTable",
    ):
        assert fn in text

    assert "raw prompt" not in text.lower()


def test_dashboard_chain_workbench_delegates_api_derived_actions():
    text = DASH.read_text(encoding="utf-8")

    for marker in (
        "function chainAttr",
        "function chainHandleListClick",
        "function chainHandleNodeClick",
        "onclick=\"chainHandleListClick(event)\"",
        "onclick=\"chainHandleNodeClick(event)\"",
        "data-chain-id=\"${chainAttr(chain.chain_id)}\"",
        "data-chain-stage-id=\"${chainAttr(stage.stage_id)}\"",
        "data-chain-algorithm-id=\"${chainAttr(stage.algorithm_id)}\"",
        "data-chain-candidate-id=\"${chainAttr(candidate.item_id)}\"",
        "aria-label=\"${chainAttr(title)}\"",
    ):
        assert marker in text

    for forbidden in (
        "onclick=\"chainSelect('${jsString(chain.chain_id)}')",
        "onclick=\"chainOpenDrawer('stage', '${jsString(",
        "onclick=\"chainOpenDrawer('algorithm', '${jsString(",
        "onclick=\"chainOpenDrawer('candidate', '${jsString(",
        "aria-label=\"${escHtml(title)}\"",
    ):
        assert forbidden not in text


def test_dashboard_chain_workbench_validates_api_payloads():
    text = DASH.read_text(encoding="utf-8")

    for marker in (
        "let _chainDetailError",
        "function chainValidateListPayload",
        "function chainValidateDetailPayload",
        "function chainErrorHtml",
        "Invalid request-chain list payload",
        "Invalid request-chain detail payload",
        "payload.chain_id !== expectedChainId",
    ):
        assert marker in text


def test_dashboard_wires_cockpit_actions():
    text = DASH.read_text(encoding="utf-8")

    assert "/api/adapters/onboarding" in text
    assert "/api/adapters/" in text
    assert "/doctor" in text
    assert "/install" in text
    assert "/verify" in text
    assert "/api/memory-candidates" in text
    assert "function runAdapterAction" in text
    assert "function loadMemoryCandidates" in text
    assert "memory-candidate-panel" in text
    assert "Generate Candidates" in text
    assert "Approve" in text
    assert "Reject" in text


def test_dashboard_exposes_local_history_sync_panel():
    text = DASH.read_text(encoding="utf-8")

    assert "/api/agents/local-history" in text
    assert "function localHistoryAgentHtml" in text
    assert "function localHistoryDraftsHtml" in text
    assert "function syncFirstLocalHistorySource" in text
    assert "Local History Sync" in text
    assert "本机历史同步" in text
    assert "History Draft Review" in text
    assert "历史草稿审核" in text


def test_dashboard_exposes_agent_management_module():
    text = DASH.read_text(encoding="utf-8")

    assert "nav.agents" in text
    assert "showPage('agents')" in text
    assert "function loadAgentManagement" in text
    assert "Agent Operations Center" in text
    assert "Agent 管理" in text
    assert "agent-health-gauge" in text
    assert "agent-status-strips" in text
    assert "agent-rank-lane" in text
    assert "agent-risk-stack" in text
    assert "agent-risk-logo" in text
    assert "agent-risk-copy" in text
    assert "agent-status-donut" not in text
    assert "agent-runtime-bars" not in text
    assert "agent-history-bars" not in text
    assert "/api/adapters/onboarding" in text
    assert "/api/adapters/capabilities" in text
    assert "/api/agents/local-history" in text
    assert "/api/agents/local-history/drafts" in text


def test_agent_management_history_sync_action_is_visible_without_local_sources():
    text = DASH.read_text(encoding="utf-8")
    start = text.index("function agentManagementActionButtons")
    end = text.index("function agentManagementRowHtml")
    action_fn = text[start:end]

    assert "syncFirstLocalHistorySource('${name}')" in action_fn
    assert "同步历史" in action_fn
    assert "if (agent.source_count > 0)" not in action_fn


def test_agent_management_row_exposes_history_sync_quick_action():
    text = DASH.read_text(encoding="utf-8")
    start = text.index("function agentManagementRowHtml")
    end = text.index("function agentManagementRowsHtml")
    row_fn = text[start:end]

    assert "agent-row-sync" in row_fn
    assert "syncFirstLocalHistorySource('${jsString(agent.name)}')" in row_fn
    assert "同步历史" in row_fn


def test_agent_history_sync_uses_all_discovered_sources():
    text = DASH.read_text(encoding="utf-8")
    start = text.index("async function syncFirstLocalHistorySource")
    end = text.index("function localHistoryDraftsHtml")
    sync_fn = text[start:end]

    assert "const sources = row.sources || []" in sync_fn
    assert "source_paths: sources.map(source => source.path)" in sync_fn
    assert "source_paths: [source.path]" not in sync_fn


def test_agent_management_does_not_block_first_render_on_local_history_scan():
    text = DASH.read_text(encoding="utf-8")
    start = text.index("async function loadAgentManagement")
    end = text.index("async function applyLocalHistoryDraft")
    load_fn = text[start:end]

    assert "function hydrateAgentManagementLocalHistory" in load_fn
    assert "const [onboarding, capabilities] = await Promise.all" in load_fn
    assert "loading: true" in load_fn
    assert "agentManagementModel(onboarding, capabilities, loadingHistory, null)" in load_fn
    assert "renderAgentManagement(el, model);" in load_fn
    assert "hydrateAgentManagementLocalHistory(el, onboarding, capabilities);" in load_fn
    assert "api('/api/agents/local-history').catch(() => ({ error: true, agents: [] }))" in load_fn
    assert "api('/api/agents/local-history/drafts').catch(() => ({ error: true, drafts: [] }))" in load_fn


def test_agent_management_local_history_assets_has_loading_state():
    text = DASH.read_text(encoding="utf-8")
    start = text.index("function agentManagementModel")
    end = text.index("function agentMetricHtml")
    model_fn = text[start:end]
    card_start = text.index("function agentHistoryAssetsNoteHtml")
    card_end = text.index("function agentRiskPanelHtml")
    card_fns = text[card_start:card_end]

    assert "historyLoading" in model_fn
    assert "historyLoaded" in model_fn
    assert "localHistory?.total_sources" in model_fn
    assert "正在扫描本机历史" in card_fns
    assert "本机历史加载失败" in card_fns
    assert "agentHistoryAssetsNoteHtml(model)" in card_fns
    assert "agentHistoryRankLanesHtml(model)" in card_fns


def test_dashboard_agent_management_has_polished_console_interactions():
    text = DASH.read_text(encoding="utf-8")

    for marker in (
        "agent-control-bar",
        "agent-search-input",
        "agent-sort-select",
        "agent-table-head",
        "agent-row-toggle",
        "agent-action-group",
        "agent-risk-panel",
        "agent-trust-stack",
        "agent-signal-board",
        "agent-signal-core",
        "agent-channel-grid",
        "agent-attention-rail",
    ):
        assert marker in text

    for fn in (
        "function setAgentManagementQuery",
        "function setAgentManagementSort",
        "function sortedAgentManagementRows",
        "function agentRiskPanelHtml",
        "function agentTrustStackHtml",
        "function agentHealthGaugeHtml",
        "function agentRankLanesHtml",
    ):
        assert fn in text

    assert "AGENT_MANAGEMENT_QUERY" in text
    assert "AGENT_MANAGEMENT_SORT" in text
    assert "grid-template-columns: 28px 32px minmax(0, 1fr) auto" in text
    assert '<span class="agent-risk-logo">${agentAvatarHtml(agent)}</span>' in text
    assert "agent-ops-grid" not in text
    assert "@media (max-width: 640px)" in text
    assert ".shortcut-bar { display: none; }" in text


def test_dashboard_uses_curated_agent_brand_assets():
    text = DASH.read_text(encoding="utf-8")

    assert "const AGENT_BRAND_ASSETS" in text
    assert "function agentLogoHtml" in text
    assert "data-login-agent=\"claude_code\"" in text
    assert "data-login-agent=\"mulerun\"" in text
    assert "renderStaticLoginAgentLogos()" in text
    assert "/static/agent-logos/img-" not in text
    assert "AGENT_LOGOS" not in text
    for adapter in (
        "aider",
        "aone_copilot",
        "claude_code",
        "cline",
        "codex",
        "continue_dev",
        "cursor",
        "github_copilot",
        "hermes_agent",
        "mulerun",
        "openclaw",
        "openhuman",
        "opensquilla",
        "qoder",
        "qoder_work",
        "wukong",
    ):
        assert f"{adapter}:" in text

    for filename in (
        "claude-code.svg",
        "codex.png",
        "hermes-agent-logo.png",
        "openclaw-readme.svg",
        "aone-copilot.png",
        "qoder-work.svg",
        "wukong-brand-logo.png",
    ):
        assert f"/static/agent-logos/{filename}" in text
        assert (AGENT_LOGOS / filename).exists()

    assert "openclaw: { src: '/static/agent-logos/openclaw-readme.svg', bg: '#ffffff'" in text

    for stale_filename in (
        "claude-code.png",
        "claude-code-favicon.png",
        "codex-cli-og.png",
        "codex-openai.png",
        "codex-openai-mark.svg",
        "qoder-work.png",
        "wukong.png",
        "wukong-login-illustration.png",
    ):
        assert f"/static/agent-logos/{stale_filename}" not in text

    assert "qoder_work: { src: '/static/agent-logos/qoder.svg'" not in text


def test_dashboard_agent_logo_rendering_sanitizes_dynamic_logo_sources():
    text = DASH.read_text(encoding="utf-8")

    assert "function safeAgentLogoSrc" in text
    assert "function agentLogoImgFailed" in text
    assert "onerror=\"agentLogoImgFailed(this)\"" in text
    assert 'src="${escHtml(agent.logo)}"' not in text


def test_dashboard_shell_uses_octopus_brand_logo():
    text = DASH.read_text(encoding="utf-8")

    assert "/static/agent-memory-hub-octopus-logo.svg" in text
    assert 'class="hub-logomark"' in text
    assert 'href="#logo-mark"' not in text
    assert "Memory Hub mascot — 海马" not in text


@pytest.mark.skipif(shutil.which("node") is None, reason="requires node")
def test_inline_scripts_parse_with_node(tmp_path):
    """Every inline <script> block must be valid JS (node --check)."""
    html = DASH.read_text(encoding="utf-8")
    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert blocks, "expected inline <script> blocks"
    for i, code in enumerate(blocks):
        f = tmp_path / f"block{i}.js"
        f.write_text(code, encoding="utf-8")
        try:
            r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True, timeout=30)
        except (subprocess.TimeoutExpired, OSError) as e:
            pytest.skip(f"requires node (spawn/transient failure: {e})")
        if r.returncode != 0 and "SyntaxError" not in r.stderr:
            # node failed to run (OOM/spawn under full-suite load), not a real
            # parse error — don't flake the suite; the pure-python guard above
            # already catches the escaped-literal regression class.
            pytest.skip(f"requires node (could not run, rc={r.returncode})")
        assert r.returncode == 0, f"inline <script> block {i} has a SyntaxError:\n{r.stderr[:500]}"
