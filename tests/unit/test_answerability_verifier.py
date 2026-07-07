from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.context.context_firewall_types import ContextCandidate
from agent_brain.memory.context.query_signal import analyze_injection_query


NOW = datetime(2026, 6, 11, 3, 0, tzinfo=timezone.utc)


def _candidate(body: str) -> ContextCandidate:
    item = MemoryItem.model_validate({
        "id": "mem-20260611-030000-answerability",
        "type": "episode",
        "created_at": NOW.isoformat(),
        "title": "多Agent共享第二大脑召回治理",
        "summary": "修复召回错乱并完成验证。",
        "confidence": 0.8,
        "sensitivity": "internal",
    })
    return ContextCandidate(item=item, body=body, score=1.0)


def test_cross_encoder_answerability_verifier_scores_query_candidate_pair() -> None:
    from agent_brain.memory.context.answerability import (
        CandidateAnswerability,
        CrossEncoderAnswerabilityVerifier,
    )
    from agent_brain.memory.recall.retrieval_rerank import _sigmoid

    class FakeCrossEncoder:
        def __init__(self) -> None:
            self.pairs = []

        def predict(self, pairs):
            self.pairs.extend(pairs)
            return [2.0]

    model = FakeCrossEncoder()
    candidate = _candidate("多Agent共享第二大脑 召回错乱 修复 验证 passed")
    query = "多Agent共享第二大脑召回错乱怎么处理"

    decision = CrossEncoderAnswerabilityVerifier(
        cross_encoder_factory=lambda: model,
        threshold=0.8,
    ).verify(
        query=query,
        candidate=candidate,
        signal=analyze_injection_query(query),
        deterministic=CandidateAnswerability(
            answerable=True,
            required_terms=("多Agent共享第二大脑",),
            covered_terms=("多Agent共享第二大脑",),
            missing_terms=(),
        ),
    )

    assert decision is not None
    assert decision.answerable is True
    assert decision.score == pytest.approx(_sigmoid(2.0))
    assert model.pairs[0][0] == query
    assert "召回错乱" in model.pairs[0][1]


def test_cross_encoder_answerability_verifier_rejects_below_threshold() -> None:
    from agent_brain.memory.context.answerability import (
        CandidateAnswerability,
        CrossEncoderAnswerabilityVerifier,
    )
    from agent_brain.memory.recall.retrieval_rerank import _sigmoid

    class FakeCrossEncoder:
        def predict(self, pairs):
            return [-2.0]

    query = "多Agent共享第二大脑召回错乱怎么处理"
    decision = CrossEncoderAnswerabilityVerifier(
        cross_encoder_factory=FakeCrossEncoder,
        threshold=0.5,
    ).verify(
        query=query,
        candidate=_candidate("多Agent共享第二大脑 架构 概览"),
        signal=analyze_injection_query(query),
        deterministic=CandidateAnswerability(
            answerable=True,
            required_terms=("多Agent共享第二大脑",),
            covered_terms=("多Agent共享第二大脑",),
            missing_terms=(),
        ),
    )

    assert decision is not None
    assert decision.answerable is False
    assert decision.score == pytest.approx(_sigmoid(-2.0))


def test_llm_answerability_verifier_normalizes_structured_judge_payload() -> None:
    from agent_brain.memory.context.answerability import (
        CandidateAnswerability,
        LLMAnswerabilityVerifier,
    )

    seen_payloads = []

    def judge(payload):
        seen_payloads.append(payload)
        return {
            "answerable": False,
            "score": 0.22,
            "reason": "mentions topic but cannot answer completion status",
        }

    query = "多Agent共享第二大脑召回错乱已经完全搞定了吗"
    decision = LLMAnswerabilityVerifier(
        judge=judge,
        threshold=0.6,
    ).verify(
        query=query,
        candidate=_candidate("多Agent共享第二大脑 召回错乱 修复历史记录"),
        signal=analyze_injection_query(query),
        deterministic=CandidateAnswerability(
            answerable=True,
            required_terms=("多Agent共享第二大脑",),
            covered_terms=("多Agent共享第二大脑",),
            missing_terms=(),
        ),
    )

    assert decision is not None
    assert decision.answerable is False
    assert decision.score == 0.22
    assert decision.reason == "mentions topic but cannot answer completion status"
    assert seen_payloads[0]["query"] == query
    assert "candidate_text" in seen_payloads[0]


def test_answerability_verifier_env_factory_is_explicit_opt_in(monkeypatch) -> None:
    from agent_brain.memory.context.answerability import (
        CrossEncoderAnswerabilityVerifier,
        LLMAnswerabilityVerifier,
        answerability_verifier_from_env,
    )

    monkeypatch.delenv("MEMORY_HUB_ANSWERABILITY_VERIFIER", raising=False)
    assert answerability_verifier_from_env() is None

    monkeypatch.setenv("MEMORY_HUB_ANSWERABILITY_VERIFIER", "cross-encoder")
    monkeypatch.setenv("MEMORY_HUB_ANSWERABILITY_THRESHOLD", "0.72")
    cross_encoder = answerability_verifier_from_env()
    assert isinstance(cross_encoder, CrossEncoderAnswerabilityVerifier)
    assert cross_encoder.threshold == 0.72

    monkeypatch.setenv("MEMORY_HUB_ANSWERABILITY_VERIFIER", "llm")
    llm = answerability_verifier_from_env()
    assert isinstance(llm, LLMAnswerabilityVerifier)
    assert llm.threshold == 0.72
