from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_brain.contracts.memory_item import MemoryItem, MemoryType


NOW = datetime(2026, 6, 11, 3, 0, tzinfo=timezone.utc)


def _item(
    suffix: str,
    type_: str,
    title: str,
    summary: str,
    *,
    days_ago: int = 0,
    refs: dict | None = None,
    tags: list[str] | None = None,
    confidence: float = 0.8,
    sensitivity: str = "internal",
    validity: dict | None = None,
    abstraction: str = "L0",
    support_count: int = 0,
    contradict_count: int = 0,
    gain_score: float = 0.0,
    context_views: dict | None = None,
) -> MemoryItem:
    return MemoryItem.model_validate({
        "id": f"mem-20260611-030000-{suffix}",
        "type": type_,
        "created_at": (NOW - timedelta(days=days_ago)).isoformat(),
        "title": title,
        "summary": summary,
        "refs": refs or {},
        "tags": tags or [],
        "confidence": confidence,
        "sensitivity": sensitivity,
        "validity": validity or {},
        "abstraction": abstraction,
        "support_count": support_count,
        "contradict_count": contradict_count,
        "gain_score": gain_score,
        "context_views": context_views or {},
    })


def _decision_by_id(result, item_id: str):
    return next(d for d in result.decisions if d.candidate.item.id == item_id)


def test_invalid_retrieval_score_is_excluded_before_other_firewall_gates() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    value = _item(
        "invalid-score",
        "episode",
        "Invalid retrieval score boundary",
        "Invalid retrieval score boundary",
        sensitivity="secret",
    )

    result = ContextFirewall(now=NOW).filter([
        ContextCandidate(value, score=float("nan")),
    ])

    decision = _decision_by_id(result, value.id)
    assert result.included == []
    assert decision.reasons == ("invalid_candidate_score",)
    assert decision.score == 0.0
    assert decision.effective_score == 0.0


def test_validate_cohort_rechecks_coverage_without_item_evaluation() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall
    from agent_brain.memory.context.query_signal import analyze_injection_query

    item = _item(
        "cohort-only",
        "episode",
        "Alpha implementation",
        "Alpha implementation detail",
    )
    firewall = ContextFirewall(now=NOW)
    decision = firewall.filter([ContextCandidate(item, score=1.0)]).included[0]

    result = firewall.validate_cohort(
        [decision],
        query_signal=analyze_injection_query("alpha beta"),
    )

    assert result.included == []
    assert result.reasons == ("cohort_strong_anchor_undercovered",)
    assert "cohort_strong_anchor_undercovered" in result.excluded[0].reasons


def test_missing_source_fact_is_excluded_but_sourced_fact_is_included() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    unsourced = _item("unsourced", "fact", "Runtime mode", "Uses local mode")
    sourced = _item(
        "sourced",
        "fact",
        "Runtime source",
        "Uses local mode from docs",
        refs={"urls": ["https://example.test/runtime"]},
    )

    result = ContextFirewall(now=NOW).filter([
        ContextCandidate(unsourced, score=9.0),
        ContextCandidate(sourced, score=8.0),
    ])

    assert [d.candidate.item.id for d in result.included] == [sourced.id]
    excluded = _decision_by_id(result, unsourced.id)
    assert excluded.action == "exclude"
    assert "missing_source" in excluded.reasons


def test_stale_signal_is_excluded_before_injection() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    stale = _item("stale-signal", "signal", "Old blocker", "Waiting on an old review", days_ago=30)
    fresh = _item("fresh-signal", "signal", "Fresh blocker", "Waiting on current review", days_ago=2)

    result = ContextFirewall(now=NOW).filter([
        ContextCandidate(stale, score=10.0),
        ContextCandidate(fresh, score=4.0),
    ])

    assert [d.candidate.item.id for d in result.included] == [fresh.id]
    excluded = _decision_by_id(result, stale.id)
    assert excluded.action == "exclude"
    assert "stale_signal" in excluded.reasons


def test_stale_negative_state_is_excluded_before_injection() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    item = _item(
        "old-browser-limited",
        "signal",
        "Browser was limited",
        "Ghostty Operation not permitted; browser unavailable",
        days_ago=5,
        tags=["permission", "browser"],
    )

    result = ContextFirewall(now=NOW).filter(
        [ContextCandidate(item, score=10.0)],
        query="browser",
    )

    assert result.included == []
    excluded = _decision_by_id(result, item.id)
    assert excluded.action == "exclude"
    assert "stale_negative_state" in excluded.reasons


def test_recent_negative_state_can_still_be_injected() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    item = _item(
        "recent-browser-limited",
        "signal",
        "Browser currently limited",
        "Ghostty Operation not permitted",
        days_ago=0,
        tags=["permission", "browser"],
    )

    result = ContextFirewall(now=NOW).filter(
        [ContextCandidate(item, score=10.0)],
        query="browser",
    )

    assert [d.candidate.item.id for d in result.included] == [item.id]


def test_temporal_state_conflict_keeps_newer_runtime_state() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    old_negative = _item(
        "browser-old-negative",
        "signal",
        "Browser unavailable",
        "Browser permission denied in current repo",
        days_ago=1,
        tags=["browser", "runtime"],
    )
    newer_positive = _item(
        "browser-newer-positive",
        "signal",
        "Browser fixed",
        "Browser available in current repo",
        days_ago=0,
        tags=["browser", "runtime"],
    )

    result = ContextFirewall(now=NOW).filter(
        [
            ContextCandidate(old_negative, score=10.0),
            ContextCandidate(newer_positive, score=4.0),
        ],
        query="browser",
        max_items=1,
    )

    assert [d.candidate.item.id for d in result.included] == [newer_positive.id]
    excluded = _decision_by_id(result, old_negative.id)
    assert excluded.action == "exclude"
    assert "temporal_state_conflict_newer" in excluded.reasons


def test_topic_recency_conflict_keeps_newer_same_topic_fact() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    old = _item(
        "api-endpoint-old",
        "fact",
        "Realbox API endpoint path",
        "Realbox API endpoint is /v1/search",
        days_ago=8,
        refs={"urls": ["https://example.test/api-v1"]},
        tags=["realbox", "api", "endpoint"],
    )
    newer = _item(
        "api-endpoint-new",
        "fact",
        "Realbox API endpoint path",
        "Realbox API endpoint is /v2/search",
        days_ago=0,
        refs={"urls": ["https://example.test/api-v2"]},
        tags=["realbox", "api", "endpoint"],
    )

    result = ContextFirewall(now=NOW).filter(
        [
            ContextCandidate(old, score=10.0),
            ContextCandidate(newer, score=3.0),
        ],
        query="realbox api endpoint",
    )

    assert [d.candidate.item.id for d in result.included] == [newer.id]
    excluded = _decision_by_id(result, old.id)
    assert excluded.action == "exclude"
    assert "topic_recency_newer" in excluded.reasons


def test_topic_recency_conflict_requires_specific_overlap() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    timeout = _item(
        "api-timeout",
        "fact",
        "Realbox API timeout",
        "Realbox API timeout is 30 seconds",
        days_ago=4,
        refs={"urls": ["https://example.test/api-timeout"]},
        tags=["realbox", "api", "timeout"],
    )
    endpoint = _item(
        "api-endpoint",
        "fact",
        "Realbox API endpoint path",
        "Realbox API endpoint is /v2/search",
        days_ago=0,
        refs={"urls": ["https://example.test/api-endpoint"]},
        tags=["realbox", "api", "endpoint"],
    )

    result = ContextFirewall(now=NOW).filter(
        [
            ContextCandidate(timeout, score=10.0),
            ContextCandidate(endpoint, score=3.0),
        ],
        query="realbox api endpoint timeout",
    )

    assert [d.candidate.item.id for d in result.included] == [timeout.id, endpoint.id]
    assert _decision_by_id(result, timeout.id).action == "include"


def test_scope_mismatch_state_is_excluded_before_injection() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    item = _item(
        "other-cwd-browser",
        "signal",
        "Browser currently limited",
        "Browser unavailable in another repo",
        days_ago=0,
        tags=["browser", "runtime"],
        validity={"cwd": "/repo/other", "adapter": "codex"},
    )

    result = ContextFirewall(now=NOW).filter(
        [ContextCandidate(item, score=10.0)],
        query="browser",
        current_scope={"cwd": "/repo/current", "adapter": "codex"},
    )

    assert result.included == []
    excluded = _decision_by_id(result, item.id)
    assert excluded.action == "exclude"
    assert "scope_mismatch" in excluded.reasons


def test_contested_and_low_confidence_items_are_demoted() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    contested = _item(
        "contested",
        "decision",
        "Queue choice",
        "Kafka may be replaced",
        refs={"urls": ["https://example.test/adr"]},
        tags=["contested"],
        confidence=0.9,
    )
    low_confidence = _item(
        "low-confidence",
        "fact",
        "Cache limit",
        "Cache size may be 2GB",
        refs={"urls": ["https://example.test/cache"]},
        confidence=0.3,
    )

    result = ContextFirewall(now=NOW).filter([
        ContextCandidate(contested, score=10.0),
        ContextCandidate(low_confidence, score=9.0),
    ])

    contested_decision = _decision_by_id(result, contested.id)
    low_decision = _decision_by_id(result, low_confidence.id)
    assert contested_decision.action == "demote"
    assert "contested" in contested_decision.reasons
    assert contested_decision.effective_score < contested_decision.score
    assert low_decision.action == "demote"
    assert "low_confidence" in low_decision.reasons
    assert low_decision.effective_score < low_decision.score


def test_repeatedly_rejected_items_are_excluded_before_injection() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    rejected = _item(
        "repeatedly-rejected",
        "episode",
        "Old browser workaround",
        "Use stale workaround that user rejected repeatedly",
        confidence=0.25,
        contradict_count=3,
        gain_score=-0.6,
    )

    result = ContextFirewall(now=NOW).filter([
        ContextCandidate(rejected, score=10.0),
    ])

    assert result.included == []
    excluded = _decision_by_id(result, rejected.id)
    assert excluded.action == "exclude"
    assert "negative_feedback" in excluded.reasons


def test_supported_items_with_some_rejections_are_not_feedback_excluded() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    contested_but_supported = _item(
        "supported-with-rejections",
        "episode",
        "Useful workflow with some caveats",
        "Still useful because more task outcomes adopted it",
        confidence=0.75,
        support_count=4,
        contradict_count=3,
        gain_score=-0.6,
    )

    result = ContextFirewall(now=NOW).filter([
        ContextCandidate(contested_but_supported, score=10.0),
    ])

    assert [d.candidate.item.id for d in result.included] == [contested_but_supported.id]


def test_l0_without_direct_evidence_is_demoted_behind_l1() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    raw_observation = _item(
        "raw-browser-note",
        "episode",
        "Browser permission note",
        "A single raw observation said browser permissions were limited",
        abstraction="L0",
    )
    consolidated = _item(
        "l1-browser-policy",
        "fact",
        "Browser permission policy",
        "Browser permission memories must be revalidated before injection",
        abstraction="L1",
        refs={"mems": [raw_observation.id]},
    )

    result = ContextFirewall(now=NOW).filter(
        [
            ContextCandidate(raw_observation, score=10.0),
            ContextCandidate(consolidated, score=3.0),
        ],
        max_items=1,
    )

    assert [d.candidate.item.id for d in result.included] == [consolidated.id]
    raw_decision = _decision_by_id(result, raw_observation.id)
    assert raw_decision.action == "exclude"
    assert "l0_evidence_only" in raw_decision.reasons
    assert "max_items_exceeded" in raw_decision.reasons


def test_l0_with_direct_source_evidence_can_compete_for_injection() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    sourced_raw = _item(
        "raw-with-file-evidence",
        "fact",
        "SDK source access",
        "git.example.internal source can be cloned through SSH",
        abstraction="L0",
        refs={"files": ["/tmp/fliggy-memory-sdk/src/client.ts"]},
    )
    consolidated = _item(
        "l1-source-access",
        "fact",
        "Source access policy",
        "Prefer source code over README when analyzing SDKs",
        abstraction="L1",
        refs={"mems": [sourced_raw.id]},
    )

    result = ContextFirewall(now=NOW).filter(
        [
            ContextCandidate(sourced_raw, score=10.0),
            ContextCandidate(consolidated, score=3.0),
        ],
        max_items=1,
    )

    assert [d.candidate.item.id for d in result.included] == [sourced_raw.id]
    sourced_decision = _decision_by_id(result, sourced_raw.id)
    assert sourced_decision.action == "include"
    assert "l0_evidence_only" not in sourced_decision.reasons


def test_needs_review_candidate_is_excluded_before_injection() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    candidate = _item(
        "needs-review",
        "episode",
        "Browser might work",
        "User said the browser issue might be fixed but no verification exists",
        tags=["needs-review", "unverified-boundary"],
        confidence=0.7,
    )

    result = ContextFirewall(now=NOW).filter([
        ContextCandidate(candidate, score=10.0),
    ])

    assert result.included == []
    excluded = _decision_by_id(result, candidate.id)
    assert excluded.action == "exclude"
    assert "requires_review" in excluded.reasons


def test_review_rejected_candidate_is_excluded_before_injection() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    candidate = _item(
        "review-rejected",
        "episode",
        "Rejected inference",
        "User rejected this inferred memory",
        tags=["review-rejected"],
        confidence=0.7,
    )

    result = ContextFirewall(now=NOW).filter([
        ContextCandidate(candidate, score=10.0),
    ])

    assert result.included == []
    excluded = _decision_by_id(result, candidate.id)
    assert excluded.action == "exclude"
    assert "requires_review" in excluded.reasons


def test_private_and_secret_items_are_excluded_by_default() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    private = _item(
        "private",
        "episode",
        "Private note",
        "Contains user-only context",
        sensitivity="private",
    )
    public = _item(
        "public",
        MemoryType.episode.value,
        "Public note",
        "Safe context",
        sensitivity="public",
    )

    result = ContextFirewall(now=NOW).filter([
        ContextCandidate(private, score=10.0),
        ContextCandidate(public, score=1.0),
    ])

    assert [d.candidate.item.id for d in result.included] == [public.id]
    excluded = _decision_by_id(result, private.id)
    assert excluded.action == "exclude"
    assert "sensitivity_not_allowed" in excluded.reasons


def test_duplicate_cluster_keeps_highest_effective_score() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    older = _item(
        "duplicate-old",
        "fact",
        "Same fact",
        "Same summary",
        refs={"urls": ["https://example.test/old"]},
    )
    newer = _item(
        "duplicate-new",
        "fact",
        "Same fact",
        "Same summary",
        refs={"urls": ["https://example.test/new"]},
    )

    result = ContextFirewall(now=NOW).filter([
        ContextCandidate(older, score=1.0),
        ContextCandidate(newer, score=5.0),
    ])

    assert [d.candidate.item.id for d in result.included] == [newer.id]
    excluded = _decision_by_id(result, older.id)
    assert excluded.action == "exclude"
    assert "duplicate_cluster" in excluded.reasons


def test_token_budget_keeps_highest_scored_packed_candidates() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    packed_text = "packed budget token " * 8
    high = _item(
        "budget-high",
        "artifact",
        "Important handoff",
        packed_text,
    )
    low = _item(
        "budget-low",
        "artifact",
        "Secondary handoff",
        packed_text,
    )
    body = "full body token " * 120

    result = ContextFirewall(now=NOW).filter(
        [
            ContextCandidate(high, body=body, score=10.0),
            ContextCandidate(low, body=body, score=9.0),
        ],
        budget_tokens=45,
    )

    assert [d.candidate.item.id for d in result.included] == [high.id]
    excluded = _decision_by_id(result, low.id)
    assert excluded.action == "exclude"
    assert "pack_budget_exceeded" in excluded.reasons


def test_firewall_budget_counts_packed_context_not_full_body() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    item = _item(
        "pack-budget",
        "fact",
        "Packed budget fact",
        "packed budget locator",
        abstraction="L1",
        refs={"urls": ["https://example.test/packed-budget"]},
        context_views={
            "locator": "packed budget locator",
            "overview": "short packed overview",
            "detail_uri": "memory://items/mem-20260611-030000-pack-budget/body",
        },
    )
    candidate = ContextCandidate(
        item=item,
        body="full body token " * 200,
        score=0.95,
    )

    result = ContextFirewall(now=NOW).filter([candidate], budget_tokens=8)

    assert [decision.candidate.item.id for decision in result.included] == [item.id]
    assert result.excluded == []


def test_query_match_compacts_spaced_tool_names() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall
    from agent_brain.memory.context.query_signal import QuerySignal

    item = _item(
        "claudecode-awareness",
        "fact",
        "Claude Code CLAUDE.md awareness",
        "Claude Code adapter awareness block is installed",
        refs={"files": ["CLAUDE.md"]},
        tags=["claude-code", "adapter"],
    )
    signal = QuerySignal(
        terms=("claudecode",),
        strong_terms=("claudecode",),
        weak_terms=(),
        injectable=True,
        reason="ok",
        specificity=1.0,
        decision="inject_allowed",
    )

    result = ContextFirewall(now=NOW).filter(
        [ContextCandidate(item, score=1.0)],
        query_signal=signal,
    )

    assert [decision.candidate.item.id for decision in result.included] == [item.id]


def test_query_mismatch_is_excluded_even_with_high_score_and_source() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    unrelated = _item(
        "unrelated-source",
        "fact",
        "Portable updater owner",
        "Yunhan maintains portable updater",
        refs={"commits": ["0857b50c"]},
    )
    related = _item(
        "related-dws",
        "episode",
        "DWS runtime verification",
        "DWS validation passed on linuxsync AppImage",
    )

    result = ContextFirewall(now=NOW).filter(
        [
            ContextCandidate(unrelated, body="Portable updater implementation", score=10.0),
            ContextCandidate(related, body="DWS 验证 Linux AppImage runtime", score=2.0),
        ],
        query="dws|验证",
    )

    assert [d.candidate.item.id for d in result.included] == [related.id]
    excluded = _decision_by_id(result, unrelated.id)
    assert excluded.action == "exclude"
    assert "query_mismatch" in excluded.reasons


def test_generic_query_term_does_not_override_missing_strong_anchor() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    generic_only = _item(
        "generic-validation",
        "episode",
        "Portable updater validation",
        "之前已经验证过 updater path",
    )
    anchored = _item(
        "anchored-dws",
        "episode",
        "DWS validation",
        "DWS 已经验证过",
    )

    result = ContextFirewall(now=NOW).filter(
        [
            ContextCandidate(generic_only, body="之前已经验证过 updater", score=10.0),
            ContextCandidate(anchored, body="DWS 验证", score=1.0),
        ],
        query="dws|验证",
    )

    assert [d.candidate.item.id for d in result.included] == [anchored.id]
    excluded = _decision_by_id(result, generic_only.id)
    assert excluded.action == "exclude"
    assert "query_mismatch" in excluded.reasons


def test_precise_short_tech_anchor_excludes_generic_install_matches() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall
    from agent_brain.memory.context.query_signal import QuerySignal

    generic_install = _item(
        "qoder-install",
        "episode",
        "Qoder MCP 配置修复",
        "Qoder 三处配置同步并重新安装 MCP command",
    )
    go_setup = _item(
        "go-env",
        "artifact",
        "本机 Go 1.24 环境已配置",
        "Go binary is available on the configured toolchain path.",
    )
    signal = QuerySignal(
        terms=("go", "配置", "安装"),
        strong_terms=("go",),
        weak_terms=(),
        injectable=True,
        reason="ok",
        specificity=1.7,
        decision="inject_allowed",
    )

    result = ContextFirewall(now=NOW).filter(
        [
            ContextCandidate(generic_install, body="Qoder 配置同步并重新安装", score=10.0),
            ContextCandidate(go_setup, body="go version go1.24.13 darwin/arm64", score=2.0),
        ],
        query_signal=signal,
    )

    assert [d.candidate.item.id for d in result.included] == [go_setup.id]
    excluded = _decision_by_id(result, generic_install.id)
    assert excluded.action == "exclude"
    assert "query_mismatch" in excluded.reasons


def test_query_term_coverage_breaks_close_retrieval_score_ties() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall
    from agent_brain.memory.context.query_signal import QuerySignal

    broad_go_project = _item(
        "go-project-report",
        "artifact",
        "智能工牌阶段性工程接盘报告",
        "当前已克隆 delivery-admin-go 和 iot-go 两个仓库",
    )
    exact_go_setup = _item(
        "go-env",
        "artifact",
        "智能工牌本机 Go 1.24 环境已配置",
        "GOPATH uses the configured workspace cache, and Go binary is on the toolchain path.",
    )
    signal = QuerySignal(
        terms=("go", "配置", "安装"),
        strong_terms=("go",),
        weak_terms=(),
        injectable=True,
        reason="ok",
        specificity=1.7,
        decision="inject_allowed",
    )

    result = ContextFirewall(now=NOW).filter(
        [
            ContextCandidate(broad_go_project, score=0.017),
            ContextCandidate(exact_go_setup, score=0.016),
        ],
        query_signal=signal,
    )

    assert [d.candidate.item.id for d in result.included] == [
        exact_go_setup.id,
        broad_go_project.id,
    ]


def test_metadata_backed_cjk_anchor_allows_relevant_cjk_memory() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall
    from agent_brain.memory.context.query_signal import QuerySignal

    alpha_plan = _item(
        "synthetic-migration-plan",
        "artifact",
        "甲项迁移规划主表",
        "合成项目迁移规划表。",
        tags=["甲项迁移", "迁移规划"],
    )
    signal = QuerySignal(
        terms=("甲项迁移", "迁移规划"),
        strong_terms=("甲项迁移", "迁移规划"),
        weak_terms=(),
        injectable=True,
        reason="ok",
        specificity=2.0,
        decision="inject_allowed",
    )

    result = ContextFirewall(now=NOW).filter(
        [ContextCandidate(alpha_plan, body="迁移规划表，服务模块筛选", score=1.0)],
        query_signal=signal,
    )

    assert [d.candidate.item.id for d in result.included] == [alpha_plan.id]


def test_cohort_gate_excludes_results_when_query_is_not_injectable() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall
    from agent_brain.memory.context.query_signal import analyze_injection_query

    candidate = _item(
        "generic-memory",
        "episode",
        "Memory tuning",
        "Memory search notes",
    )
    signal = analyze_injection_query("memory")

    result = ContextFirewall(now=NOW).filter(
        [ContextCandidate(candidate, body="Memory search notes", score=10.0)],
        query="memory",
        query_signal=signal,
    )

    assert result.included == []
    assert "query_not_injectable" in result.cohort_reasons
    excluded = _decision_by_id(result, candidate.id)
    assert excluded.action == "exclude"
    assert "query_not_injectable" in excluded.reasons


def test_cohort_gate_requires_all_strong_anchors_to_be_covered() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    only_dws = _item(
        "only-dws",
        "episode",
        "DWS verification",
        "DWS 已经验证过",
    )

    result = ContextFirewall(now=NOW).filter(
        [ContextCandidate(only_dws, body="DWS 验证通过", score=10.0)],
        query="dws linux 验证",
    )

    assert result.included == []
    assert "cohort_strong_anchor_undercovered" in result.cohort_reasons
    excluded = _decision_by_id(result, only_dws.id)
    assert excluded.action == "exclude"
    assert "cohort_strong_anchor_undercovered" in excluded.reasons


def test_cohort_gate_keeps_results_when_strong_anchors_are_covered() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    dws = _item(
        "dws",
        "episode",
        "DWS verification",
        "DWS 已经验证过",
    )
    linux = _item(
        "linux",
        "episode",
        "Linux package",
        "Linux AppImage includes DWS resources",
    )

    result = ContextFirewall(now=NOW).filter(
        [
            ContextCandidate(dws, body="DWS 验证通过", score=10.0),
            ContextCandidate(linux, body="Linux AppImage package", score=8.0),
        ],
        query="dws linux 验证",
    )

    assert [d.candidate.item.id for d in result.included] == [dws.id, linux.id]
    assert result.cohort_reasons == ()


def test_answerability_rejects_scope_only_candidate_before_injection() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    generic_linux = _item(
        "generic-linux",
        "episode",
        "Linux package validation",
        "Linux AppImage package was verified.",
    )
    dws_linux = _item(
        "dws-linux",
        "episode",
        "DWS Linux runtime verification",
        "DWS validation passed on Linux AppImage.",
    )

    result = ContextFirewall(now=NOW).filter(
        [
            ContextCandidate(generic_linux, body="Linux AppImage package verified", score=10.0),
            ContextCandidate(dws_linux, body="DWS runtime verified on Linux", score=2.0),
        ],
        query="dws linux 验证",
    )

    assert [d.candidate.item.id for d in result.included] == [dws_linux.id]
    excluded = _decision_by_id(result, generic_linux.id)
    assert excluded.action == "exclude"
    assert "answerability_mismatch" in excluded.reasons
    assert "query_mismatch" not in excluded.reasons


def test_answerability_rejects_topic_only_candidate_for_resolution_query() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    topic_only = _item(
        "second-brain-overview",
        "artifact",
        "多Agent共享第二大脑架构概览",
        "多Agent共享第二大脑模块边界和信息架构说明。",
    )
    resolution = _item(
        "second-brain-recall-fix",
        "episode",
        "多Agent共享第二大脑召回错乱修复",
        "修复 weak prompt 和 scope-only 召回污染，验证 query gate 与 recall hallucination gate passed。",
    )

    result = ContextFirewall(now=NOW).filter(
        [
            ContextCandidate(topic_only, body="多Agent共享第二大脑 架构 概览 模块 信息", score=10.0),
            ContextCandidate(resolution, body="多Agent共享第二大脑 召回错乱 修复 验证 passed", score=2.0),
        ],
        query="多Agent共享第二大脑 召回错乱怎么处理",
    )

    assert [d.candidate.item.id for d in result.included] == [resolution.id]
    excluded = _decision_by_id(result, topic_only.id)
    assert excluded.action == "exclude"
    assert "answerability_mismatch" in excluded.reasons


def test_semantic_verifier_can_reject_surface_matching_candidate() -> None:
    from agent_brain.memory.context.answerability import SemanticAnswerabilityDecision
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    class RejectingVerifier:
        def verify(self, **_kwargs):
            return SemanticAnswerabilityDecision(
                answerable=False,
                score=0.18,
                reason="does_not_answer_completion_status",
            )

    surface_match = _item(
        "second-brain-past-fix",
        "episode",
        "多Agent共享第二大脑召回错乱修复记录",
        "修复 weak prompt 召回污染并完成回归验证。",
    )

    result = ContextFirewall(
        now=NOW,
        answerability_verifier=RejectingVerifier(),
    ).filter(
        [
            ContextCandidate(
                surface_match,
                body="多Agent共享第二大脑 召回错乱 修复 验证 passed",
                score=10.0,
            ),
        ],
        query="多Agent共享第二大脑 召回错乱 已经完全搞定了吗",
    )

    assert result.included == []
    excluded = _decision_by_id(result, surface_match.id)
    assert excluded.action == "exclude"
    assert "semantic_answerability_mismatch" in excluded.reasons


def test_semantic_verifier_allows_candidate_after_structural_gate_passes() -> None:
    from agent_brain.memory.context.answerability import SemanticAnswerabilityDecision
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    class AllowingVerifier:
        def verify(self, **_kwargs):
            return SemanticAnswerabilityDecision(
                answerable=True,
                score=0.91,
                reason="answers_completion_status",
            )

    item = _item(
        "second-brain-current-status",
        "episode",
        "多Agent共享第二大脑召回治理状态",
        "修复 weak prompt 和 scope-only 污染，验证通过。",
    )

    result = ContextFirewall(
        now=NOW,
        answerability_verifier=AllowingVerifier(),
    ).filter(
        [ContextCandidate(item, body="多Agent共享第二大脑 召回错乱 修复 验证 passed", score=1.0)],
        query="多Agent共享第二大脑 召回错乱怎么处理",
    )

    assert [d.candidate.item.id for d in result.included] == [item.id]


def test_semantic_verifier_failure_falls_back_to_structural_answerability() -> None:
    from agent_brain.memory.context.context_firewall import ContextCandidate, ContextFirewall

    class FailingVerifier:
        def verify(self, **_kwargs):
            raise RuntimeError("model unavailable")

    item = _item(
        "second-brain-offline-status",
        "episode",
        "多Agent共享第二大脑召回治理状态",
        "修复 weak prompt 和 scope-only 污染，验证通过。",
    )

    result = ContextFirewall(
        now=NOW,
        answerability_verifier=FailingVerifier(),
    ).filter(
        [ContextCandidate(item, body="多Agent共享第二大脑 召回错乱 修复 验证 passed", score=1.0)],
        query="多Agent共享第二大脑 召回错乱怎么处理",
    )

    assert [d.candidate.item.id for d in result.included] == [item.id]
