from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys
from types import SimpleNamespace

import pytest

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.context.context_firewall_types import (
    ContextCandidate,
    ContextFirewallConfig,
)
from agent_brain.memory.context.query_signal import QuerySignal
from agent_brain.memory.recall.admission import RecallAdmission
from agent_brain.memory.recall.routed_types import RouteEvidence


NOW = datetime(2026, 7, 15, tzinfo=timezone.utc)


def _signal(
    *,
    terms: tuple[str, ...] = (),
    strong_terms: tuple[str, ...] = (),
    injectable: bool = False,
) -> QuerySignal:
    return QuerySignal(
        terms=terms,
        strong_terms=strong_terms,
        weak_terms=(),
        injectable=injectable,
        reason="too_weak" if not injectable else "ok",
        specificity=0.0 if not injectable else 1.0,
        decision="block" if not injectable else "inject_allowed",
    )


def _item(
    suffix: str,
    *,
    title: str = "Atlas routed recall",
    summary: str = "Atlas runtime migration evidence",
    type_: str = "episode",
    sensitivity: str = "internal",
    tags: list[str] | None = None,
    superseded_by: str | None = None,
    days_ago: int = 0,
    validity: dict[str, str] | None = None,
) -> MemoryItem:
    refs = {"urls": ["https://example.test/atlas"]} if type_ in {"fact", "decision"} else {}
    return MemoryItem.model_validate({
        "id": f"mem-20260715-000000-{suffix}",
        "type": type_,
        "created_at": (NOW - timedelta(days=days_ago)).isoformat(),
        "title": title,
        "summary": summary,
        "confidence": 0.9,
        "sensitivity": sensitivity,
        "tags": tags or [],
        "superseded_by": superseded_by,
        "validity": validity or {},
        "refs": refs,
    })


def _candidate(value: MemoryItem, *, body: str = "", score: float = 1.0) -> ContextCandidate:
    return ContextCandidate(value, body=body, score=score)


def _evidence(
    *routes: str,
    similarity: float | None = None,
    semantic_rank: int | None = None,
    lexical_terms_rank: int | None = None,
    lexical_raw_rank: int | None = None,
) -> RouteEvidence:
    return RouteEvidence(
        routes=routes,
        semantic_similarity=similarity,
        semantic_rank=(
            semantic_rank
            if semantic_rank is not None
            else (1 if "semantic_raw" in routes else None)
        ),
        lexical_terms_rank=(
            lexical_terms_rank
            if lexical_terms_rank is not None
            else (1 if "lexical_terms" in routes else None)
        ),
        lexical_raw_rank=(
            lexical_raw_rank
            if lexical_raw_rank is not None
            else (1 if "lexical_raw_fallback" in routes else None)
        ),
    )


def _context(
    *,
    raw_query: str = "what changed in atlas runtime migration",
    signal: QuerySignal | None = None,
    evidence_by_id: dict[str, RouteEvidence] | None = None,
    allowed: bool = True,
):
    from agent_brain.memory.context.injection_query_context import InjectionQueryContext

    return InjectionQueryContext(
        raw_query=raw_query,
        admission=RecallAdmission(
            allowed,
            "meaningful_query" if allowed else "weak_confirmation",
        ),
        query_signal=signal or _signal(),
        evidence_by_id=evidence_by_id or {},
    )


def test_query_context_requires_real_admission_and_defensively_copies_evidence() -> None:
    from agent_brain.memory.context.injection_query_context import InjectionQueryContext

    evidence = {"item": _evidence("semantic_raw", similarity=0.82)}
    context = _context(evidence_by_id=evidence)
    evidence.clear()

    assert context.raw_query == "what changed in atlas runtime migration"
    assert context.evidence_by_id["item"].semantic_similarity == 0.82
    with pytest.raises(TypeError):
        context.evidence_by_id["other"] = _evidence("semantic_raw", similarity=0.9)  # type: ignore[index]
    with pytest.raises((TypeError, ValueError)):
        InjectionQueryContext(
            raw_query="sensitive raw query sentinel",
            admission=None,  # type: ignore[arg-type]
            query_signal=_signal(),
            evidence_by_id={},
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("semantic_route_min_similarity", -0.01),
        ("semantic_route_min_similarity", 1.01),
        ("semantic_route_direct_min_similarity", -0.01),
        ("semantic_route_direct_min_similarity", 1.01),
        ("semantic_route_direct_max_items", 0),
        ("semantic_route_direct_max_items", 11),
        ("semantic_route_min_margin", -0.01),
        ("semantic_route_min_margin", 1.01),
        ("semantic_route_anchor_rescue_min_similarity", -0.01),
        ("semantic_route_anchor_rescue_min_similarity", 1.01),
        ("raw_route_min_coverage", -0.01),
        ("raw_route_min_coverage", 1.01),
    ],
)
def test_route_answerability_thresholds_are_probabilities(field: str, value: float) -> None:
    with pytest.raises(ValueError):
        ContextFirewallConfig(**{field: value})


def test_semantic_route_allows_admitted_candidate_despite_empty_blocked_signal() -> None:
    from agent_brain.memory.context.context_firewall import ContextFirewall

    value = _item("semantic-pass")
    candidate = _candidate(value, body="semantic-only candidate")
    context = _context(
        evidence_by_id={value.id: _evidence("semantic_raw", similarity=0.82)},
    )

    result = ContextFirewall(now=NOW).filter([candidate], query_context=context)

    assert [decision.candidate.item.id for decision in result.included] == [value.id]
    assert result.cohort_reasons == ()


@pytest.mark.parametrize("similarity", [None, 0.249, 1.01])
def test_semantic_route_requires_real_similarity_not_fused_candidate_score(
    similarity: float | None,
) -> None:
    from agent_brain.memory.context.context_firewall import ContextFirewall

    value = _item("semantic-fail")
    context = _context(
        evidence_by_id={value.id: _evidence("semantic_raw", similarity=similarity)},
    )

    result = ContextFirewall(now=NOW).filter(
        [_candidate(value, body="semantic candidate", score=999.0)],
        query_context=context,
    )

    assert result.included == []
    assert result.excluded[0].reasons == ("route_answerability_insufficient",)


def test_semantic_route_accepts_frozen_model_calibrated_similarity_floor() -> None:
    from agent_brain.memory.context.context_firewall import ContextFirewall

    value = _item("semantic-frozen-model-floor")
    context = _context(
        evidence_by_id={value.id: _evidence("semantic_raw", similarity=0.25)},
    )

    result = ContextFirewall(now=NOW).filter(
        [_candidate(value, body="semantic candidate")],
        query_context=context,
    )

    assert [decision.candidate.item.id for decision in result.included] == [value.id]


def test_semantic_preselection_rejects_redundant_direct_runner_up() -> None:
    from agent_brain.memory.context.context_firewall import ContextFirewall

    winner = _item("direct-rank-one", title="Relevant memory")
    runner_up = _item("direct-rank-two", title="Related but weaker memory")
    context = _context(
        evidence_by_id={
            winner.id: _evidence(
                "semantic_raw",
                similarity=0.72,
                semantic_rank=1,
            ),
            runner_up.id: _evidence(
                "semantic_raw",
                similarity=0.61,
                semantic_rank=2,
            ),
        },
    )

    result = ContextFirewall(now=NOW).filter(
        [_candidate(winner), _candidate(runner_up)],
        query_context=context,
    )

    assert [decision.candidate.item.id for decision in result.included] == [winner.id]
    assert result.excluded[0].reasons == ("route_answerability_insufficient",)


def test_direct_semantic_cohort_keeps_two_answerable_atlas_memories_only() -> None:
    from agent_brain.memory.context.context_firewall import ContextFirewall
    from agent_brain.memory.context.injection_query_context import InjectionQueryContext
    from agent_brain.memory.recall.admission import build_recall_request

    query = (
        "What were the rollout decision and the verification result for Atlas migration?"
    )
    request = build_recall_request(query, adapter="codex")
    decision = _item(
        "atlas-rollout-decision",
        title="Atlas migration rollout decision",
        summary="The rollout decision was a staged migration.",
        type_="decision",
    )
    verification = _item(
        "atlas-verification-result",
        title="Atlas migration verification result",
        summary="The verification result passed all rollback checks.",
    )
    unrelated = _item(
        "atlas-unrelated-high-score",
        title="Atlas migration cafeteria menu",
        summary="Lunch catering choices for the Atlas office.",
    )
    context = InjectionQueryContext(
        raw_query=query,
        admission=request.admission,
        query_signal=request.query_signal,
        evidence_by_id={
            decision.id: _evidence(
                "semantic_raw",
                similarity=0.72,
                semantic_rank=1,
            ),
            verification.id: _evidence(
                "semantic_raw",
                similarity=0.70,
                semantic_rank=2,
            ),
            unrelated.id: _evidence(
                "semantic_raw",
                similarity=0.69,
                semantic_rank=3,
            ),
        },
    )

    result = ContextFirewall(now=NOW).filter(
        [
            _candidate(decision, body="Decision evidence: staged rollout."),
            _candidate(verification, body="Verification evidence: checks passed."),
            _candidate(unrelated, body="Catering schedule only."),
        ],
        query_context=context,
    )

    assert [entry.candidate.item.id for entry in result.included] == [
        decision.id,
        verification.id,
    ]
    assert [entry.candidate.item.id for entry in result.excluded] == [unrelated.id]
    assert result.excluded[0].reasons == ("route_answerability_insufficient",)


def test_direct_semantic_cohort_preserves_resolution_intent_per_candidate() -> None:
    from agent_brain.memory.context.context_firewall import ContextFirewall
    from agent_brain.memory.context.injection_query_context import InjectionQueryContext
    from agent_brain.memory.recall.admission import build_recall_request

    query = "多Agent共享第二大脑 召回错乱怎么处理"
    request = build_recall_request(query, adapter="codex")
    topic_only = _item(
        "topic-only",
        title="多Agent共享第二大脑架构概览",
        summary="多Agent共享第二大脑模块边界和信息架构说明。",
        type_="artifact",
        tags=["多agent共享第二大脑"],
    )
    resolution = _item(
        "resolution",
        title="多Agent共享第二大脑召回错乱修复",
        summary="修复 scope-only 召回污染并验证回归通过。",
        tags=["多agent共享第二大脑", "recall"],
    )
    context = InjectionQueryContext(
        raw_query=query,
        admission=request.admission,
        query_signal=request.query_signal,
        evidence_by_id={
            topic_only.id: _evidence(
                "semantic_raw",
                similarity=0.72,
                semantic_rank=1,
            ),
            resolution.id: _evidence(
                "semantic_raw",
                similarity=0.70,
                semantic_rank=2,
            ),
        },
    )

    result = ContextFirewall(now=NOW).filter(
        [
            _candidate(topic_only, body="多Agent共享第二大脑 架构 概览 模块 信息"),
            _candidate(resolution, body="召回错乱 修复 验证 passed"),
        ],
        query_context=context,
    )

    assert [entry.candidate.item.id for entry in result.included] == [resolution.id]
    assert [entry.candidate.item.id for entry in result.excluded] == [topic_only.id]
    assert result.excluded[0].reasons == ("route_answerability_insufficient",)


def test_direct_semantic_cohort_respects_configured_bound() -> None:
    from agent_brain.memory.context.context_firewall import ContextFirewall

    values = [
        _item(f"bounded-{index}", title=f"Atlas facet{index}")
        for index in range(1, 6)
    ]
    context = _context(
        raw_query="Atlas facet1 facet2 facet3 facet4 facet5",
        evidence_by_id={
            value.id: _evidence(
                "semantic_raw",
                similarity=0.80 - index / 100,
                semantic_rank=index,
            )
            for index, value in enumerate(values, start=1)
        },
    )

    result = ContextFirewall(
        ContextFirewallConfig(
            semantic_route_direct_max_items=2,
            semantic_route_anchor_rescue_min_similarity=1.0,
        ),
        now=NOW,
    ).filter(
        [_candidate(value) for value in values],
        query_context=context,
    )

    assert [entry.candidate.item.id for entry in result.included] == [
        values[0].id,
        values[1].id,
    ]
    assert all(
        entry.reasons == ("route_answerability_insufficient",)
        for entry in result.excluded
    )


def test_semantic_gray_zone_requires_a_safe_rank_one_margin() -> None:
    from agent_brain.memory.context.context_firewall import ContextFirewall

    winner = _item("gray-rank-one", title="Relevant memory")
    runner_up = _item("gray-rank-two", title="Related but weaker memory")
    context = _context(
        evidence_by_id={
            winner.id: _evidence(
                "semantic_raw",
                similarity=0.45,
                semantic_rank=1,
            ),
            runner_up.id: _evidence(
                "semantic_raw",
                similarity=0.30,
                semantic_rank=2,
            ),
        },
    )

    result = ContextFirewall(now=NOW).filter(
        [_candidate(winner), _candidate(runner_up)],
        query_context=context,
    )

    assert [decision.candidate.item.id for decision in result.included] == [winner.id]


def test_semantic_gray_zone_fails_closed_when_margin_is_ambiguous() -> None:
    from agent_brain.memory.context.context_firewall import ContextFirewall

    winner = _item("ambiguous-rank-one", title="Possible memory")
    runner_up = _item("ambiguous-rank-two", title="Another possible memory")
    context = _context(
        evidence_by_id={
            winner.id: _evidence(
                "semantic_raw",
                similarity=0.45,
                semantic_rank=1,
            ),
            runner_up.id: _evidence(
                "semantic_raw",
                similarity=0.42,
                semantic_rank=2,
            ),
        },
    )

    result = ContextFirewall(now=NOW).filter(
        [_candidate(winner), _candidate(runner_up)],
        query_context=context,
    )

    assert result.included == []
    assert all(
        decision.reasons == ("route_answerability_insufficient",)
        for decision in result.excluded
    )


def test_unique_raw_anchor_can_rescue_semantic_rank_two() -> None:
    from agent_brain.memory.context.context_firewall import ContextFirewall

    semantic_only = _item(
        "anchor-rank-one",
        title="Shared brain introduction",
        summary="General product introduction",
    )
    anchored = _item(
        "anchor-rank-two",
        title="Recall formula documentation",
        summary="Algorithm explanation revision",
    )
    context = _context(
        raw_query="深度叙事和算法解释二次打磨",
        evidence_by_id={
            semantic_only.id: _evidence(
                "semantic_raw",
                similarity=0.64,
                semantic_rank=1,
            ),
            anchored.id: _evidence(
                "semantic_raw",
                similarity=0.51,
                semantic_rank=2,
            ),
        },
    )

    result = ContextFirewall(now=NOW).filter(
        [
            _candidate(semantic_only, body="Product scope and collaboration model."),
            _candidate(anchored, body="算法解释补充变量定义和使用边界。"),
        ],
        query_context=context,
    )

    assert [decision.candidate.item.id for decision in result.included] == [anchored.id]


def test_multiple_fully_anchored_lexical_memories_are_not_collapsed_to_rank_one() -> None:
    from agent_brain.memory.context.context_firewall import ContextFirewall

    first = _item(
        "multi-answer-first",
        title="Atlas migration rollout decision",
        summary="First independently useful migration record",
    )
    second = _item(
        "multi-answer-second",
        title="Atlas migration rollout verification",
        summary="Second independently useful migration record",
    )
    signal = _signal(
        terms=("atlas", "migration", "rollout"),
        strong_terms=("atlas", "migration", "rollout"),
        injectable=True,
    )
    context = _context(
        raw_query="atlas migration rollout",
        signal=signal,
        evidence_by_id={
            first.id: _evidence(
                "lexical_terms",
                "semantic_raw",
                similarity=0.78,
                semantic_rank=1,
                lexical_terms_rank=1,
            ),
            second.id: _evidence(
                "lexical_terms",
                "semantic_raw",
                similarity=0.73,
                semantic_rank=2,
                lexical_terms_rank=2,
            ),
        },
    )

    result = ContextFirewall(now=NOW).filter(
        [_candidate(first), _candidate(second)],
        query_context=context,
    )

    assert {decision.candidate.item.id for decision in result.included} == {
        first.id,
        second.id,
    }


def test_technical_alias_route_fails_closed_when_multiple_items_cover_all_anchors() -> None:
    from agent_brain.memory.context.context_firewall import ContextFirewall
    from agent_brain.memory.recall.admission import build_recall_request

    first = _item(
        "recall-cache-ttl",
        title="Recall cache TTL",
        summary="Recall cache entries expire after ten minutes",
    )
    second = _item(
        "dns-cache-ttl",
        title="DNS cache TTL",
        summary="DNS cache entries expire after sixty seconds",
    )
    request = build_recall_request("कैश का टीटीएल कितना है", adapter="codex")
    context = _context(
        raw_query=request.raw_query,
        signal=request.query_signal,
        evidence_by_id={
            first.id: _evidence("lexical_terms", lexical_terms_rank=1),
            second.id: _evidence("lexical_terms", lexical_terms_rank=2),
        },
    )

    result = ContextFirewall(now=NOW).filter(
        [_candidate(first), _candidate(second)],
        query_context=context,
    )

    assert result.included == []
    assert all(
        decision.reasons == ("route_answerability_insufficient",)
        for decision in result.excluded
    )


def test_raw_lexical_fallback_uses_full_query_coverage() -> None:
    from agent_brain.memory.context.context_firewall import ContextFirewall

    related = _item("raw-related")
    unrelated = _item("raw-unrelated", title="Cooking notes", summary="Pasta recipe")
    context = _context(
        raw_query="atlas runtime migration",
        evidence_by_id={
            related.id: _evidence("lexical_raw_fallback"),
            unrelated.id: _evidence("lexical_raw_fallback"),
        },
    )

    result = ContextFirewall(now=NOW).filter(
        [
            _candidate(related, body="Atlas runtime migration completed"),
            _candidate(unrelated, body="tomato pasta sauce"),
        ],
        query_context=context,
    )

    assert [decision.candidate.item.id for decision in result.included] == [related.id]
    assert "route_answerability_insufficient" in result.excluded[0].reasons


def test_raw_lexical_fallback_ignores_conversational_query_noise() -> None:
    from agent_brain.memory.context.context_firewall import ContextFirewall

    value = _item("raw-noisy-query")
    context = _context(
        raw_query="请继续排查 atlas runtime",
        evidence_by_id={value.id: _evidence("lexical_raw_fallback")},
    )

    result = ContextFirewall(now=NOW).filter(
        [_candidate(value, body="Atlas runtime details")],
        query_context=context,
    )

    assert [decision.candidate.item.id for decision in result.included] == [value.id]


def test_routed_answerability_ignores_cjk_completion_question_noise_term() -> None:
    from agent_brain.memory.context.context_firewall import ContextFirewall
    from agent_brain.memory.recall.admission import build_recall_request

    value = _item(
        "routed-cjk-completion-noise",
        title="AMH README 深度叙事和算法解释二次打磨",
        summary="多智能体共享第二大脑文档改造",
    )
    request = build_recall_request(
        "关于多智能体共享第二单的深度叙事和算法解释二次打磨，都做了什么",
        adapter="codex",
    )
    context = _context(
        raw_query=request.raw_query,
        signal=request.query_signal,
        evidence_by_id={value.id: _evidence("semantic_raw", similarity=0.91)},
    )

    result = ContextFirewall(now=NOW).filter(
        [_candidate(value, body="深度叙事 算法解释 二次打磨")],
        query_context=context,
    )

    assert [decision.candidate.item.id for decision in result.included] == [value.id]


def test_raw_route_threshold_accepts_labeled_long_cjk_paraphrase_coverage() -> None:
    from agent_brain.memory.context.context_firewall import ContextFirewall

    value = _item(
        "raw-long-cjk-calibration",
        title="AMH README 深度叙事和算法解释二次打磨",
        summary="README 中文版维护链路和召回链路",
    )
    context = _context(
        raw_query="关于多智能体共享第二单的深度叙事和算法解释二次打磨，都做了什么",
        evidence_by_id={value.id: _evidence("lexical_raw_fallback")},
    )

    result = ContextFirewall(now=NOW).filter(
        [_candidate(value, body="深度叙事 算法解释 二次打磨")],
        query_context=context,
    )

    assert [decision.candidate.item.id for decision in result.included] == [value.id]


@pytest.mark.parametrize(
    ("raw_query", "body"),
    [
        ("go api", "mango api"),
        ("go api", "go migration api"),
        ("E0583", "XE0583 is unrelated"),
    ],
)
def test_raw_coverage_rejects_ascii_substrings_and_discontinuous_phrases(
    raw_query: str,
    body: str,
) -> None:
    from agent_brain.memory.context.answerability import raw_query_candidate_coverage

    coverage, phrases = raw_query_candidate_coverage(
        raw_query,
        _candidate(_item("ascii-boundary-negative"), body=body),
    )

    assert coverage < 1.0 or not phrases
    assert phrases == ()


@pytest.mark.parametrize(
    ("raw_query", "body"),
    [
        ("go api", "The go api migration is complete"),
        ("E0583", "Rust compiler error E0583"),
        ("请继续排查 go api", "The go api migration is complete"),
    ],
)
def test_raw_coverage_accepts_bounded_ascii_phrases(
    raw_query: str,
    body: str,
) -> None:
    from agent_brain.memory.context.answerability import raw_query_candidate_coverage

    coverage, phrases = raw_query_candidate_coverage(
        raw_query,
        _candidate(_item("ascii-boundary-positive"), body=body),
    )

    assert coverage == 1.0
    assert phrases


def test_repeated_ascii_tokens_do_not_invent_nonadjacent_phrase() -> None:
    from agent_brain.memory.context.answerability import raw_query_candidate_coverage

    coverage, phrases = raw_query_candidate_coverage(
        "api error api timeout",
        _candidate(
            _item("repeated-ascii-no-invented-phrase"),
            body="error timeout",
        ),
    )

    assert coverage == pytest.approx(2 / 3)
    assert phrases == ()


def test_repeated_ascii_tokens_preserve_real_later_phrase() -> None:
    from agent_brain.memory.context.answerability import raw_query_candidate_coverage

    coverage, phrases = raw_query_candidate_coverage(
        "api error api timeout",
        _candidate(
            _item("repeated-ascii-real-phrase"),
            body="api timeout",
        ),
    )

    assert coverage == pytest.approx(2 / 3)
    assert phrases == ("api timeout",)


def test_raw_coverage_rejects_distributed_cjk_characters() -> None:
    from agent_brain.memory.context.answerability import raw_query_candidate_coverage

    coverage, phrases = raw_query_candidate_coverage(
        "北京大学",
        _candidate(
            _item("cjk-distributed", title="北京天气", summary="大学申请"),
            body="北京天气和大学申请",
        ),
    )

    assert coverage == 0.0
    assert phrases == ()


@pytest.mark.parametrize(
    ("raw_query", "body"),
    [
        ("库", "数据库迁移"),
        ("库的迁", "数据库迁移"),
    ],
)
def test_raw_coverage_rejects_single_cjk_units_after_noise_split(
    raw_query: str,
    body: str,
) -> None:
    from agent_brain.memory.context.answerability import raw_query_candidate_coverage

    coverage, phrases = raw_query_candidate_coverage(
        raw_query,
        _candidate(_item("single-cjk-negative"), body=body),
    )

    assert coverage == 0.0
    assert phrases == ()


@pytest.mark.parametrize(
    ("raw_query", "body"),
    [
        ("鹿鸣", "鹿鸣项目验收"),
        ("数据库", "数据库迁移完成"),
    ],
)
def test_raw_coverage_keeps_contiguous_two_and_three_cjk_units(
    raw_query: str,
    body: str,
) -> None:
    from agent_brain.memory.context.answerability import raw_query_candidate_coverage

    coverage, phrases = raw_query_candidate_coverage(
        raw_query,
        _candidate(_item("cjk-minimum-positive"), body=body),
    )

    assert coverage == 1.0
    assert phrases


@pytest.mark.parametrize(
    ("raw_query", "body"),
    [
        ("北京大学", "北京大学招生信息"),
        ("请继续排查北京大学 go api", "北京大学 go api 联调记录"),
    ],
)
def test_raw_coverage_accepts_contiguous_cjk_and_mixed_phrases(
    raw_query: str,
    body: str,
) -> None:
    from agent_brain.memory.context.answerability import raw_query_candidate_coverage

    coverage, phrases = raw_query_candidate_coverage(
        raw_query,
        _candidate(_item("cjk-contiguous"), body=body),
    )

    assert coverage == 1.0
    assert phrases


def test_raw_coverage_rejects_distributed_mixed_query() -> None:
    from agent_brain.memory.context.answerability import raw_query_candidate_coverage

    coverage, phrases = raw_query_candidate_coverage(
        "请继续排查北京大学 go api",
        _candidate(
            _item("mixed-distributed", title="北京天气", summary="大学申请"),
            body="mango migration api",
        ),
    )

    assert coverage < 0.5
    assert phrases == ()


def test_mixed_raw_coverage_requires_its_ascii_phrase_even_when_cjk_matches() -> None:
    from agent_brain.memory.context.answerability import raw_query_candidate_coverage

    coverage, phrases = raw_query_candidate_coverage(
        "北京大学 go api",
        _candidate(
            _item("mixed-ascii-phrase-required"),
            body="北京大学 mango api",
        ),
    )

    assert coverage == 0.75
    assert phrases == ()


def test_lexical_terms_route_keeps_existing_primary_anchor_rule() -> None:
    from agent_brain.memory.context.context_firewall import ContextFirewall

    anchored = _item("term-anchored", title="Atlas rollout", summary="Rollout evidence")
    unrelated = _item("term-unrelated", title="Runtime rollout", summary="No project anchor")
    signal = _signal(terms=("atlas", "rollout"), strong_terms=("atlas",), injectable=False)
    context = _context(
        signal=signal,
        evidence_by_id={
            anchored.id: _evidence("lexical_terms"),
            unrelated.id: _evidence("lexical_terms"),
        },
    )

    result = ContextFirewall(now=NOW).filter(
        [_candidate(anchored), _candidate(unrelated)],
        query_context=context,
    )

    assert [decision.candidate.item.id for decision in result.included] == [anchored.id]
    assert "query_mismatch" in result.excluded[0].reasons


def test_lexical_terms_without_terms_or_route_evidence_fails_closed() -> None:
    from agent_brain.memory.context.context_firewall import ContextFirewall

    route_only = _item("term-route-empty")
    supplement = _item("pipeline-supplement")
    context = _context(
        evidence_by_id={route_only.id: _evidence("lexical_terms")},
    )

    result = ContextFirewall(now=NOW).filter(
        [_candidate(route_only), _candidate(supplement)],
        query_context=context,
    )

    assert result.included == []
    assert all(
        decision.reasons == ("route_answerability_insufficient",)
        for decision in result.excluded
    )


def test_strong_terms_still_reject_pipeline_supplement_without_route_evidence() -> None:
    from agent_brain.memory.context.context_firewall import ContextFirewall

    supplement = _item("strong-pipeline-supplement")
    context = _context(
        signal=_signal(terms=("atlas",), strong_terms=("atlas",), injectable=False),
    )

    result = ContextFirewall(now=NOW).filter(
        [_candidate(supplement, body="Atlas evidence")],
        query_context=context,
    )

    assert result.included == []
    assert result.excluded[0].reasons == ("route_answerability_insufficient",)


def test_routed_admission_false_rejects_entire_cohort() -> None:
    from agent_brain.memory.context.context_firewall import ContextFirewall

    value = _item("admission-block")
    context = _context(
        allowed=False,
        evidence_by_id={value.id: _evidence("semantic_raw", similarity=0.99)},
    )

    result = ContextFirewall(now=NOW).filter([_candidate(value)], query_context=context)

    assert result.included == []
    assert result.cohort_reasons == ("query_not_injectable",)
    assert "query_not_injectable" in result.excluded[0].reasons


def test_routed_admission_false_never_calls_legacy_answerability_verifier() -> None:
    from agent_brain.memory.context.answerability import SemanticAnswerabilityDecision
    from agent_brain.memory.context.context_firewall import ContextFirewall

    class SpyVerifier:
        call_count = 0

        def verify(self, **_kwargs):
            self.call_count += 1
            return SemanticAnswerabilityDecision(True, 0.99, "allow")

    verifier = SpyVerifier()
    value = _item("admission-block-verifier")
    context = _context(
        allowed=False,
        signal=_signal(terms=("atlas",), strong_terms=("atlas",), injectable=True),
        evidence_by_id={value.id: _evidence("lexical_terms")},
    )

    result = ContextFirewall(
        now=NOW,
        answerability_verifier=verifier,
    ).filter([_candidate(value, body="Atlas evidence")], query_context=context)

    assert result.included == []
    assert "query_not_injectable" in result.excluded[0].reasons
    assert verifier.call_count == 0


@pytest.mark.parametrize(
    "routes",
    [
        (),
        ("unknown_route",),
        ("lexical_terms", "unknown_route"),
    ],
)
def test_strong_terms_require_closed_route_provenance(routes: tuple[str, ...]) -> None:
    from agent_brain.memory.context.context_firewall import ContextFirewall

    value = _item("strong-invalid-route")
    context = _context(
        signal=_signal(terms=("atlas",), strong_terms=("atlas",), injectable=False),
        evidence_by_id={value.id: _evidence(*routes)},
    )

    result = ContextFirewall(now=NOW).filter(
        [_candidate(value, body="Atlas evidence")],
        query_context=context,
    )

    assert result.included == []
    assert result.excluded[0].reasons == ("route_answerability_insufficient",)


@pytest.mark.parametrize(
    "route",
    ["semantic_raw", "lexical_terms", "lexical_raw_fallback"],
)
def test_strong_terms_accept_each_recognized_route(route: str) -> None:
    from agent_brain.memory.context.context_firewall import ContextFirewall

    value = _item(f"strong-valid-{route}")
    context = _context(
        signal=_signal(terms=("atlas",), strong_terms=("atlas",), injectable=False),
        evidence_by_id={
            value.id: _evidence(
                route,
                similarity=0.82 if route == "semantic_raw" else None,
            )
        },
    )

    result = ContextFirewall(now=NOW).filter(
        [_candidate(value, body="Atlas evidence")],
        query_context=context,
    )

    assert [decision.candidate.item.id for decision in result.included] == [value.id]


def test_routed_topic_recency_uses_admission_not_signal_injectability() -> None:
    from agent_brain.memory.context.context_firewall import ContextFirewall

    old = _item(
        "topic-old",
        type_="fact",
        title="Realbox API endpoint path",
        summary="Realbox API endpoint is /v1/search",
        days_ago=2,
    )
    new = _item(
        "topic-new",
        type_="fact",
        title="Realbox API endpoint path",
        summary="Realbox API endpoint is /v2/search",
    )
    context = _context(
        signal=_signal(terms=("realbox",), strong_terms=("realbox",), injectable=False),
        evidence_by_id={
            old.id: _evidence("lexical_terms"),
            new.id: _evidence("lexical_terms"),
        },
    )

    result = ContextFirewall(now=NOW).filter(
        [_candidate(old, score=2.0), _candidate(new, score=1.0)],
        query_context=context,
    )

    assert [decision.candidate.item.id for decision in result.included] == [new.id]
    old_decision = next(
        decision for decision in result.excluded
        if decision.candidate.item.id == old.id
    )
    assert "topic_recency_newer" in old_decision.reasons


def test_semantic_verifier_cannot_override_route_deterministic_failure() -> None:
    from agent_brain.memory.context.answerability import SemanticAnswerabilityDecision
    from agent_brain.memory.context.context_firewall import ContextFirewall

    class AllowingVerifier:
        called = False

        def verify(self, **_kwargs):
            self.called = True
            return SemanticAnswerabilityDecision(True, 0.99, "allow")

    verifier = AllowingVerifier()
    value = _item("verifier-cannot-override")
    context = _context(
        evidence_by_id={value.id: _evidence("semantic_raw", similarity=0.1)},
    )

    result = ContextFirewall(
        now=NOW,
        answerability_verifier=verifier,
    ).filter([_candidate(value, score=999.0)], query_context=context)

    assert result.included == []
    assert result.excluded[0].reasons == ("route_answerability_insufficient",)
    assert verifier.called is False


def test_routed_verifier_exception_is_private_and_fails_closed(caplog) -> None:
    from agent_brain.memory.context.context_firewall import ContextFirewall

    raw_sentinel = "SECRET_RAW_QUERY_SENTINEL"
    body_sentinel = "SECRET_CANDIDATE_BODY_SENTINEL"
    value = _item("SECRET_ITEM_ID_SENTINEL")

    class FailingVerifier:
        def verify(self, **_kwargs):
            raise RuntimeError(f"{raw_sentinel} {body_sentinel} {value.id}")

    context = _context(
        raw_query=raw_sentinel,
        evidence_by_id={value.id: _evidence("semantic_raw", similarity=0.82)},
    )
    result = ContextFirewall(
        now=NOW,
        answerability_verifier=FailingVerifier(),
    ).filter(
        [_candidate(value, body=body_sentinel)],
        query_context=context,
    )

    assert result.included == []
    assert "semantic_answerability_mismatch" in result.excluded[0].reasons
    for sentinel in (raw_sentinel, body_sentinel, value.id):
        assert sentinel not in caplog.text


def test_legacy_verifier_exception_log_is_private_and_keeps_legacy_fallback(caplog) -> None:
    from agent_brain.memory.context.answerability import verify_candidate_answerability

    raw_sentinel = "SECRET_LEGACY_RAW_SENTINEL"
    body_sentinel = "SECRET_LEGACY_BODY_SENTINEL"
    value = _item("SECRET_LEGACY_ITEM_SENTINEL")

    class FailingVerifier:
        def verify(self, **_kwargs):
            raise RuntimeError(f"{raw_sentinel} {body_sentinel} {value.id}")

    decision = verify_candidate_answerability(
        _candidate(value, body=f"Atlas {body_sentinel}"),
        _signal(terms=("atlas",), strong_terms=("atlas",), injectable=True),
        query=raw_sentinel,
        verifier=FailingVerifier(),
    )

    assert decision.answerable is True
    for sentinel in (raw_sentinel, body_sentinel, value.id):
        assert sentinel not in caplog.text


def test_llm_provider_exception_log_is_private(monkeypatch, caplog) -> None:
    from agent_brain.memory.context.answerability import (
        CandidateAnswerability,
        LLMAnswerabilityVerifier,
    )

    sentinel = "SECRET_LLM_PROVIDER_SENTINEL"

    def fail_completion(**_kwargs):
        raise RuntimeError(sentinel)

    monkeypatch.setitem(
        sys.modules,
        "litellm",
        SimpleNamespace(completion=fail_completion),
    )
    candidate = _candidate(_item("llm-provider-private"), body="Atlas evidence")
    decision = LLMAnswerabilityVerifier().verify(
        query="Atlas query",
        candidate=candidate,
        signal=_signal(terms=("atlas",), strong_terms=("atlas",), injectable=True),
        deterministic=CandidateAnswerability(True, ("atlas",), ("atlas",), ()),
    )

    assert decision is not None
    assert decision.answerable is False
    assert decision.execution_failed is True
    assert decision.reason == "llm_answerability_execution_failed"
    assert sentinel not in caplog.text


def test_routed_llm_provider_exception_fails_closed(monkeypatch, caplog) -> None:
    from agent_brain.memory.context.answerability import LLMAnswerabilityVerifier
    from agent_brain.memory.context.context_firewall import ContextFirewall

    sentinel = "SECRET_ROUTED_LLM_PROVIDER_SENTINEL"

    def fail_completion(**_kwargs):
        raise RuntimeError(sentinel)

    monkeypatch.setitem(
        sys.modules,
        "litellm",
        SimpleNamespace(completion=fail_completion),
    )
    value = _item("routed-llm-provider")
    context = _context(
        raw_query="Atlas provider query",
        evidence_by_id={value.id: _evidence("semantic_raw", similarity=0.82)},
    )
    result = ContextFirewall(
        now=NOW,
        answerability_verifier=LLMAnswerabilityVerifier(),
    ).filter([_candidate(value, body="Atlas provider evidence")], query_context=context)

    assert result.included == []
    assert "semantic_answerability_mismatch" in result.excluded[0].reasons
    assert sentinel not in caplog.text


def test_legacy_llm_provider_exception_keeps_deterministic_allow(
    monkeypatch,
    caplog,
) -> None:
    from agent_brain.memory.context.answerability import LLMAnswerabilityVerifier
    from agent_brain.memory.context.context_firewall import ContextFirewall

    sentinel = "SECRET_LEGACY_LLM_PROVIDER_SENTINEL"

    def fail_completion(**_kwargs):
        raise RuntimeError(sentinel)

    monkeypatch.setitem(
        sys.modules,
        "litellm",
        SimpleNamespace(completion=fail_completion),
    )
    value = _item("legacy-llm-provider")
    result = ContextFirewall(
        now=NOW,
        answerability_verifier=LLMAnswerabilityVerifier(),
    ).filter(
        [_candidate(value, body="Atlas provider evidence")],
        query="Atlas provider query",
        query_signal=_signal(
            terms=("atlas",),
            strong_terms=("atlas",),
            injectable=True,
        ),
    )

    assert [decision.candidate.item.id for decision in result.included] == [value.id]
    assert sentinel not in caplog.text


@pytest.mark.parametrize(
    ("item_kwargs", "scope", "reason"),
    [
        ({"sensitivity": "private"}, None, "sensitivity_not_allowed"),
        ({"sensitivity": "secret"}, None, "sensitivity_not_allowed"),
        ({"tags": ["needs-review"]}, None, "requires_review"),
        ({"tags": ["unverified-boundary"]}, None, "requires_review"),
        ({"superseded_by": "mem-new"}, None, "superseded"),
        ({"tags": ["state"], "validity": {"cwd": "/expected"}}, {"cwd": "/other"}, "scope_mismatch"),
    ],
)
def test_routed_path_preserves_safety_gates(
    item_kwargs: dict[str, object],
    scope: dict[str, str] | None,
    reason: str,
) -> None:
    from agent_brain.memory.context.context_firewall import ContextFirewall

    value = _item("unsafe", **item_kwargs)  # type: ignore[arg-type]
    context = _context(
        evidence_by_id={value.id: _evidence("semantic_raw", similarity=0.99)},
    )

    result = ContextFirewall(now=NOW).filter(
        [_candidate(value)],
        query_context=context,
        current_scope=scope,
    )

    assert result.included == []
    assert reason in result.excluded[0].reasons
