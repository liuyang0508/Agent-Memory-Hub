from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from agent_brain.memory.context.query_model_fallback import (
    LLMQueryConstraintResolver,
    ModelConstraintResolution,
    apply_optional_query_model_fallback,
    validate_model_constraint_response,
)
from agent_brain.memory.context.query_signal import analyze_injection_query
from agent_brain.memory.recall.admission import build_recall_request, build_routed_query_signal


def _resolved_payload(
    *,
    confidence: float = 0.96,
    evidence: str = "不能不停机",
) -> dict[str, object]:
    return {
        "ambiguous": False,
        "confidence": confidence,
        "constraints": [
            {
                "predicate": "停机",
                "polarity": True,
                "evidence": evidence,
                "confidence": confidence,
            }
        ],
        "alternatives": [],
    }


def test_default_routed_recall_keeps_model_fallback_disabled(monkeypatch) -> None:
    monkeypatch.delenv("MEMORY_HUB_QUERY_MODEL_FALLBACK", raising=False)

    signal = build_routed_query_signal("关于数据库迁移，不能不停机")

    assert not signal.injectable
    assert signal.reason == "ambiguous_constraint"
    assert signal.constraint_resolution == "ambiguous"
    assert signal.model_fallback_status == "disabled"
    assert "model_fallback:disabled" in signal.trace


def test_model_fallback_resolves_grounded_high_confidence_constraint() -> None:
    resolver = LLMQueryConstraintResolver(judge=lambda _: _resolved_payload())
    signal = apply_optional_query_model_fallback(
        analyze_injection_query("关于数据库迁移，不能不停机"),
        "关于数据库迁移，不能不停机",
        resolver=resolver,
    )

    assert signal.injectable
    assert signal.reason == "ok"
    assert signal.constraint_resolution == "model_fallback"
    assert signal.model_fallback_status == "resolved"
    assert signal.parse_confidence == 0.96
    assert signal.constraints[0].predicate == "停机"
    assert signal.constraints[0].polarity is True
    assert signal.constraints[0].kind == "requirement"
    assert "停机" in signal.terms
    assert "model_fallback:resolved" in signal.trace


def test_model_fallback_rejects_evidence_not_copied_from_query() -> None:
    resolver = LLMQueryConstraintResolver(
        judge=lambda _: _resolved_payload(evidence="必须停机"),
    )
    signal = apply_optional_query_model_fallback(
        analyze_injection_query("关于数据库迁移，不能不停机"),
        "关于数据库迁移，不能不停机",
        resolver=resolver,
    )

    assert not signal.injectable
    assert signal.reason == "ambiguous_constraint"
    assert signal.model_fallback_status == "invalid"


def test_model_fallback_rejects_low_confidence_result() -> None:
    resolution = validate_model_constraint_response(
        "关于数据库迁移，不能不停机",
        _resolved_payload(confidence=0.82),
        threshold=0.9,
    )

    assert resolution.status == "low_confidence"
    assert not resolution.resolved


def test_model_fallback_keeps_model_declared_ambiguity_blocked() -> None:
    resolver = LLMQueryConstraintResolver(
        judge=lambda _: {
            "ambiguous": True,
            "confidence": 0.94,
            "constraints": [],
            "alternatives": [
                {"predicate": "停机", "polarity": True},
                {"predicate": "停机", "polarity": False},
            ],
        }
    )
    signal = apply_optional_query_model_fallback(
        analyze_injection_query("关于数据库迁移，不能不停机"),
        "关于数据库迁移，不能不停机",
        resolver=resolver,
    )

    assert not signal.injectable
    assert signal.model_fallback_status == "ambiguous"
    assert signal.model_fallback_confidence == 0.94


def test_provider_specific_timeout_fails_closed_with_timeout_status() -> None:
    class ProviderTimeoutError(RuntimeError):
        pass

    def timeout(_: dict[str, object]) -> dict[str, object]:
        raise ProviderTimeoutError

    resolver = LLMQueryConstraintResolver(judge=timeout)
    signal = apply_optional_query_model_fallback(
        analyze_injection_query("关于数据库迁移，不能不停机"),
        "关于数据库迁移，不能不停机",
        resolver=resolver,
    )

    assert not signal.injectable
    assert signal.model_fallback_status == "timeout"


def test_model_is_not_called_for_deterministically_clear_query() -> None:
    calls: list[dict[str, object]] = []
    resolver = LLMQueryConstraintResolver(
        judge=lambda payload: calls.append(payload) or _resolved_payload()
    )

    signal = apply_optional_query_model_fallback(
        analyze_injection_query("关于数据库迁移，不要停机"),
        "关于数据库迁移，不要停机",
        resolver=resolver,
    )

    assert signal.injectable
    assert signal.model_fallback_status == "not_attempted"
    assert calls == []


def test_technical_alias_cannot_bypass_unresolved_ambiguity(monkeypatch) -> None:
    monkeypatch.delenv("MEMORY_HUB_QUERY_MODEL_FALLBACK", raising=False)

    signal = build_routed_query_signal("query_signal.py 不能不删除")

    assert "query_signal.py" in signal.terms
    assert not signal.injectable
    assert signal.reason == "ambiguous_constraint"


def test_opt_in_litellm_call_is_bounded_and_records_usage(monkeypatch) -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(_resolved_payload(), ensure_ascii=False))
            )
        ],
        usage=SimpleNamespace(prompt_tokens=181, completion_tokens=47),
    )
    completion = MagicMock(return_value=response)
    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(completion=completion))
    monkeypatch.setenv("MEMORY_HUB_QUERY_MODEL_FALLBACK", "1")
    monkeypatch.setenv("MEMORY_HUB_QUERY_MODEL_FALLBACK_MODEL", "test/query-parser")

    signal = build_routed_query_signal("关于数据库迁移，不能不停机")

    assert signal.injectable
    assert signal.model_fallback_input_tokens == 181
    assert signal.model_fallback_output_tokens == 47
    kwargs = completion.call_args.kwargs
    assert kwargs["model"] == "test/query-parser"
    assert kwargs["temperature"] == 0
    assert kwargs["max_tokens"] <= 512
    assert kwargs["timeout"] <= 30
    assert set(json.loads(kwargs["messages"][1]["content"])) == {"query"}
    assert "candidate" not in kwargs["messages"][1]["content"]


def test_no_model_master_switch_prevents_opt_in_call(monkeypatch) -> None:
    completion = MagicMock()
    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(completion=completion))
    monkeypatch.setenv("MEMORY_HUB_QUERY_MODEL_FALLBACK", "1")
    monkeypatch.setenv("MEMORY_HUB_NO_MODEL", "1")

    signal = build_routed_query_signal("关于数据库迁移，不能不停机")

    assert not signal.injectable
    assert signal.model_fallback_status == "disabled"
    completion.assert_not_called()


class _ResolvedDowntime:
    def resolve(self, query: str) -> ModelConstraintResolution:
        return validate_model_constraint_response(query, _resolved_payload())


def test_routed_retrieval_uses_resolved_polarity_to_filter_candidates(tmp_path: Path) -> None:
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType
    from agent_brain.memory.recall.retrieval import Retriever, SearchFilter
    from agent_brain.platform.indexing.index import HubIndex

    class DegradedEmbedder:
        degraded = True

    index = HubIndex(tmp_path / "model-fallback.db", embedding_dim=2)
    required = MemoryItem(
        id="mem-20260726-130000-offline-migration",
        type=MemoryType.decision,
        created_at=datetime.now(UTC),
        title="数据库迁移需要停机",
        summary="必须安排停机窗口后执行离线迁移。",
    )
    prohibited = MemoryItem(
        id="mem-20260726-130001-online-migration",
        type=MemoryType.decision,
        created_at=datetime.now(UTC),
        title="数据库迁移无需停机",
        summary="使用双写完成在线迁移。",
    )
    try:
        index.upsert(required, required.summary, embedding=None)
        index.upsert(prohibited, prohibited.summary, embedding=None)
        request = build_recall_request(
            "关于数据库迁移，不能不停机",
            adapter="codex",
            constraint_model_resolver=_ResolvedDowntime(),
        )
        result = Retriever(
            index=index,
            embedder=DegradedEmbedder(),  # type: ignore[arg-type]
            rerank=False,
            apply_decay=False,
            record_access=False,
        ).search_routed(
            request,
            filters=SearchFilter(include_superseded=True, include_stale_state=True),
            explain=True,
        )

        assert request.query_signal.constraint_resolution == "model_fallback"
        assert [hit.id for hit in result.hits] == [required.id]
    finally:
        index.close()
