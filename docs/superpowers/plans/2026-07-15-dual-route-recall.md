# Dual-Route Recall Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将自动召回从“规则关键词单入口”升级为“完整原始问题语义路由 + 规则词面路由 + 离线原文 BM25 降级”，并确保所有候选仍由统一 Injection Gateway 以 fail-closed 方式授权后才能进入 Prompt。

**Architecture:** 新增不可变 `RecallAdmission`、`RecallRequest`、route trace/evidence 与 `InjectionQueryContext`。`Retriever.search_routed()` 独立生成 `semantic_raw`、`lexical_terms`、`lexical_raw_fallback` 候选，做 route-aware RRF 后复用既有排序流水线；CLI 以结构化 `hook-json` 把 routed 结果交给 hook；Gateway、Firewall、answerability 在 routed path 使用 Admission 与原始 route evidence，legacy path 保持兼容。

**Tech Stack:** Python 3.11+、dataclasses、SQLite FTS/BM25、现有 embedding/vector index、Typer CLI、POSIX shell hooks、pytest、Ruff。

---

## 实施边界与文件地图

本计划实现 [双通道召回与 Hook 治理设计](../specs/2026-07-15-dual-route-recall-design.md)，基于统一 Prompt Injection Gateway 分支继续开发。不要混入 `codex/staged-agent-recall-governance` 的 locator/overview 深度治理，也不要新增常驻向量 daemon、schema migration 或强制 reindex。

新建：

- `agent_brain/memory/recall/admission.py`：独立召回准入分析。
- `agent_brain/memory/recall/routed_types.py`：项目作用域、请求、route trace/evidence、结果类型。
- `agent_brain/memory/recall/routed_fusion.py`：route-aware RRF。
- `agent_brain/memory/context/injection_query_context.py`：Gateway 的 routed 查询上下文。
- `agent_brain/interfaces/cli/routed_query.py`：CLI routed orchestration 与 `hook-json` 协议。
- `tests/unit/test_recall_admission.py`
- `tests/unit/test_routed_retrieval.py`
- `tests/unit/test_routed_answerability.py`
- `tests/unit/test_routed_cli.py`
- `tests/fixtures/dual_route_recall_cases.json`：不少于 40 条人工标注用例。
- `tests/system/test_dual_route_recall_matrix.py`
- `scripts/benchmark-dual-route-hook.py`：独立 30 次 hook 性能验收。

修改：

- `agent_brain/memory/recall/retrieval.py`
- `agent_brain/platform/indexing/metadata_index.py`
- `agent_brain/memory/context/answerability.py`
- `agent_brain/memory/context/context_firewall.py`
- `agent_brain/memory/context/context_firewall_types.py`
- `agent_brain/memory/context/injection_gateway.py`
- `agent_brain/interfaces/cli/commands/query.py`
- `agent_brain/interfaces/cli/_shared.py`
- `agent_runtime_kit/hooks/inject-context.sh`
- `agent_brain/interfaces/cli/commands/insight.py`
- `agent_runtime_kit/AGENT_MEMORY_DISCIPLINE.md`
- `agent_brain/interfaces/mcp/onboarding.py`
- `agent_brain/interfaces/mcp/tools/search_tools.py`
- `agent_brain/agent_integrations/awareness.py`
- `agent_brain/product/adapter_onboarding.py`
- `agent_brain/platform/doctor.py`
- `agent_brain/interfaces/cli/doctor_offline.py`
- 相关 CLI、Gateway、hook、doctor、adapter、文档契约测试。

所有测试命令都在仓库根目录执行。macOS 全仓测试前先执行 `ulimit -n 8192`，避免默认 fd soft limit 256 造成伪失败。

### Task 1: 建立独立 Admission 与不可变 routed 请求契约

**Files:**
- Create: `tests/unit/test_recall_admission.py`
- Create: `agent_brain/memory/recall/admission.py`
- Create: `agent_brain/memory/recall/routed_types.py`
- Test: `tests/unit/test_query_signal.py`

- [ ] **Step 1: 写 Admission 与 ProjectScope 的失败测试**

创建 `tests/unit/test_recall_admission.py`，至少包含：

```python
import pytest

from agent_brain.memory.recall.admission import analyze_recall_admission
from agent_brain.memory.recall.routed_types import ProjectScope, build_recall_request


@pytest.mark.parametrize("query", ["网关呢", "hooks 为什么没有召回记忆", "semantic paraphrase"])
def test_admission_allows_meaningful_query_even_without_terms(query, monkeypatch):
    monkeypatch.setattr(
        "agent_brain.memory.recall.routed_types.analyze_injection_query",
        lambda _: type("Signal", (), {"terms": (), "injectable": False})(),
    )
    request = build_recall_request(query, adapter="codex")
    assert request.admission.allowed is True
    assert request.lexical_terms == ()
    assert request.normalized_query


@pytest.mark.parametrize("query", ["", "...", "确认", "继续", "是", "OK", "1"])
def test_admission_rejects_only_known_low_value_inputs(query):
    admission = analyze_recall_admission(query)
    assert admission.allowed is False
    assert admission.reason in {
        "empty_query",
        "punctuation_only",
        "control_command",
        "weak_acknowledgement",
    }


def test_project_scope_strength_is_explicit():
    assert ProjectScope("smart-badge", "explicit", True).hard_filter is True
    assert ProjectScope("smart-badge", "cwd", False).hard_filter is False
    assert ProjectScope("smart-badge", "agent_inferred", False).hard_filter is False


def test_soft_project_scope_cannot_be_constructed_as_hard_filter():
    with pytest.raises(ValueError, match="soft project source"):
        ProjectScope("smart-badge", "agent_inferred", True)
```

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/unit/test_recall_admission.py
```

Expected: collection 因 `agent_brain.memory.recall.admission` 或 `routed_types` 不存在而失败。

- [ ] **Step 3: 实现最小 Admission 与类型契约**

在 `admission.py` 定义：

```python
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from agent_brain.memory.context.prompt_normalization import normalize_hook_prompt_for_recall

AdmissionReason = Literal[
    "allowed",
    "empty_query",
    "punctuation_only",
    "control_command",
    "weak_acknowledgement",
]

_WEAK_ACKS = frozenset({"是", "确认", "继续", "ok", "okay", "1"})
_CONTROL_PREFIXES = ("/remember", "/compact", "/clear")


@dataclass(frozen=True)
class RecallAdmission:
    allowed: bool
    reason: AdmissionReason


def analyze_recall_admission(raw_query: str) -> RecallAdmission:
    normalized = normalize_hook_prompt_for_recall(raw_query or "").strip()
    if not normalized:
        return RecallAdmission(False, "empty_query")
    folded = normalized.casefold()
    if folded in _WEAK_ACKS:
        return RecallAdmission(False, "weak_acknowledgement")
    if folded.startswith(_CONTROL_PREFIXES):
        return RecallAdmission(False, "control_command")
    if not re.search(r"[\w\u3400-\u9fff]", normalized):
        return RecallAdmission(False, "punctuation_only")
    return RecallAdmission(True, "allowed")
```

在 `routed_types.py` 定义 `ProjectScope`、`RecallRequest`、`RouteTrace`、`RouteEvidence`、`RoutedSearchResult`。`RouteEvidence` 字段名使用 `semantic_similarity`，明确它是规范化 cosine similarity，不是 vector backend 的负距离，也不是 RRF 分数：

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

from agent_brain.memory.context.prompt_normalization import normalize_hook_prompt_for_recall
from agent_brain.memory.context.query_signal import QuerySignal, analyze_injection_query
from agent_brain.memory.recall.admission import RecallAdmission, analyze_recall_admission
from agent_brain.memory.recall.retrieval_types import RetrievedItem

ProjectSource = Literal["explicit", "cwd", "agent_inferred"]


@dataclass(frozen=True)
class ProjectScope:
    value: str
    source: ProjectSource
    hard_filter: bool = False

    def __post_init__(self) -> None:
        if self.hard_filter and self.source != "explicit":
            raise ValueError("soft project source cannot be a hard filter")


@dataclass(frozen=True)
class RecallRequest:
    raw_query: str
    normalized_query: str
    lexical_terms: tuple[str, ...]
    admission: RecallAdmission
    query_signal: QuerySignal
    project_scope: ProjectScope | None = None
    cwd: str | None = None
    adapter: str = "unknown"
    session_id: str | None = None


@dataclass(frozen=True)
class RouteTrace:
    route: str
    status: Literal["ok", "skipped", "timeout", "error"]
    latency_ms: float
    candidate_count: int
    reason: Literal[
        "admission_rejected",
        "lexical_terms_empty",
        "semantic_not_ready",
        "route_timeout",
        "route_error",
    ] | None = None


@dataclass(frozen=True)
class RouteEvidence:
    routes: tuple[str, ...]
    semantic_similarity: float | None = None
    semantic_rank: int | None = None
    lexical_terms_rank: int | None = None
    lexical_raw_rank: int | None = None


@dataclass(frozen=True)
class RoutedSearchResult:
    hits: list[RetrievedItem]
    routes: tuple[RouteTrace, ...]
    admission: RecallAdmission
    evidence_by_id: Mapping[str, RouteEvidence]


def build_recall_request(
    raw_query: str,
    *,
    adapter: str,
    project_scope: ProjectScope | None = None,
    cwd: str | None = None,
    session_id: str | None = None,
) -> RecallRequest:
    normalized = normalize_hook_prompt_for_recall(raw_query or "").strip()
    signal = analyze_injection_query(normalized)
    return RecallRequest(
        raw_query=raw_query,
        normalized_query=normalized,
        lexical_terms=tuple(signal.terms[:6]),
        admission=analyze_recall_admission(raw_query),
        query_signal=signal,
        project_scope=project_scope,
        cwd=cwd,
        adapter=adapter,
        session_id=session_id,
    )
```

如果现有 normalization 函数返回结构而非字符串，按其真实返回合同取规范化文本；不要复制第二套 normalization。

- [ ] **Step 4: 运行 targeted tests 确认 GREEN**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/unit/test_recall_admission.py \
  tests/unit/test_query_signal.py
```

Expected: 两个文件全部通过；现有 `QuerySignal` 测试不改语义。

- [ ] **Step 5: 提交 Admission 契约**

```bash
git add agent_brain/memory/recall/admission.py \
  agent_brain/memory/recall/routed_types.py \
  tests/unit/test_recall_admission.py
git commit -m "feat: add routed recall admission contract"
```

### Task 2: 实现 route-aware RRF 与 `Retriever.search_routed()`

**Files:**
- Create: `tests/unit/test_routed_retrieval.py`
- Create: `agent_brain/memory/recall/routed_fusion.py`
- Modify: `agent_brain/memory/recall/retrieval.py`
- Modify: `agent_brain/platform/indexing/metadata_index.py`
- Test: `tests/unit/test_retrieval.py`

- [ ] **Step 1: 写路由、降级、证据保留和项目范围测试**

在 `tests/unit/test_routed_retrieval.py` 使用 fake index/embedder 覆盖以下断言：

```python
def test_routed_search_keeps_raw_and_term_routes_separate(retriever, spy_index):
    request = make_request(
        raw_query="hooks 为什么没有召回记忆",
        normalized_query="hooks 为什么没有召回记忆",
        lexical_terms=("hooks", "召回", "记忆"),
    )
    result = retriever.search_routed(request, limit=10)
    assert spy_index.vector_queries == ["hooks 为什么没有召回记忆"]
    assert spy_index.bm25_queries == ["hooks 召回 记忆"]
    assert {trace.route for trace in result.routes} == {"semantic_raw", "lexical_terms"}


def test_semantic_failure_enables_raw_bm25_without_dropping_term_hits(retriever, spy_index):
    spy_index.vector_error = TimeoutError("deadline")
    result = retriever.search_routed(make_request(), limit=10)
    assert spy_index.bm25_queries == ["hooks 召回 记忆", "hooks 为什么没有召回记忆"]
    assert {hit.id for hit in result.hits} >= {"term-hit", "raw-hit"}
    traces = {trace.route: trace for trace in result.routes}
    assert traces["semantic_raw"].status == "timeout"
    assert traces["lexical_raw_fallback"].status == "ok"


def test_empty_terms_skip_only_term_route(retriever):
    result = retriever.search_routed(make_request(lexical_terms=()), limit=10)
    assert result.routes[0].reason == "lexical_terms_empty"
    assert any(trace.route == "semantic_raw" for trace in result.routes)


def test_route_fusion_preserves_cosine_and_each_original_rank(retriever):
    result = retriever.search_routed(make_request(), limit=10)
    evidence = result.evidence_by_id["shared-hit"]
    assert evidence.routes == ("semantic_raw", "lexical_terms")
    assert evidence.semantic_rank == 1
    assert evidence.lexical_terms_rank == 2
    assert evidence.semantic_similarity == pytest.approx(0.8)
    assert result.hits[0].score != evidence.semantic_similarity


def test_only_explicit_project_is_hard_filtered(retriever, spy_index):
    retriever.search_routed(make_request(project_scope=ProjectScope("p", "explicit", True)))
    assert spy_index.allowed_ids_calls == [("p",)]
    retriever.search_routed(make_request(project_scope=ProjectScope("p", "agent_inferred", False)))
    assert spy_index.allowed_ids_calls == [("p",)]
```

再增加一个阶段 spy，断言 metadata phrase、reranker、decay/status 等后续阶段收到 `request.normalized_query`，以及 legacy `Retriever.search()` 输出不变。

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/unit/test_routed_retrieval.py \
  tests/unit/test_retrieval.py
```

Expected: routed tests 因 `search_routed` 与 `routed_fusion` 缺失失败；legacy 测试继续通过。

- [ ] **Step 3: 实现 route-aware RRF**

在 `routed_fusion.py` 实现纯函数：

```python
from collections import defaultdict
from collections.abc import Mapping, Sequence

from agent_brain.memory.recall.routed_types import RouteEvidence
from agent_brain.memory.recall.retrieval_types import RetrievedItem


def fuse_routes(
    route_hits: Mapping[str, Sequence[RetrievedItem]],
    *,
    semantic_similarity_by_id: Mapping[str, float],
    rrf_k: int = 60,
) -> tuple[list[RetrievedItem], dict[str, RouteEvidence]]:
    scores: dict[str, float] = defaultdict(float)
    ranks: dict[str, dict[str, int]] = defaultdict(dict)
    for route, hits in route_hits.items():
        for rank, hit in enumerate(hits, start=1):
            scores[hit.id] += 1.0 / (rrf_k + rank)
            ranks[hit.id][route] = rank
    ordered = sorted(scores, key=lambda item_id: (-scores[item_id], item_id))
    fused = [
        RetrievedItem(
            id=item_id,
            score=scores[item_id],
            bm25_rank=ranks[item_id].get("lexical_terms")
            or ranks[item_id].get("lexical_raw_fallback"),
            vector_rank=ranks[item_id].get("semantic_raw"),
        )
        for item_id in ordered
    ]
    evidence = {
        item_id: RouteEvidence(
            routes=tuple(route for route in route_hits if route in ranks[item_id]),
            semantic_similarity=semantic_similarity_by_id.get(item_id),
            semantic_rank=ranks[item_id].get("semantic_raw"),
            lexical_terms_rank=ranks[item_id].get("lexical_terms"),
            lexical_raw_rank=ranks[item_id].get("lexical_raw_fallback"),
        )
        for item_id in ordered
    }
    return fused, evidence
```

- [ ] **Step 4: 实现 routed 检索与失败隔离**

在 `Retriever` 增加 `search_routed(request, limit=10, ...) -> RoutedSearchResult`，按以下固定顺序实现：

1. Admission 拒绝时直接返回空结果和 `admission_rejected` trace，不调用任何 index。
2. `lexical_terms` 非空时调用 `index.bm25_search(" ".join(terms), allowed_ids=...)`；为空写 `skipped/lexical_terms_empty`。
3. 仅当当前 embedder/provider 已 ready 且不会冷加载时，用完整 `normalized_query` 生成 query embedding 并调用 `vector_search`。
4. 用 query embedding 与 `index.get_embeddings(ids)` 通过 `retrieval_mmr._cosine_sim` 计算 `semantic_similarity_by_id`；不得阈值判断 raw `Hit.score`。
5. semantic route 未 ready、timeout 或 error 时，调用完整 `normalized_query` 的 BM25 作为 `lexical_raw_fallback`；term BM25 已完成结果必须保留。
6. `fuse_routes()` 后复用现有 metadata phrase、handoff、reranker、decay、feedback、runtime evidence、temporal、supersession、MMR/Hopfield/graph 流水线，统一传 `normalized_query`。
7. 最终只保留仍在 hits 中的 `evidence_by_id`，但不要把 evidence 塞进 `RetrievedItem`，避免现有阶段重建 dataclass 时静默丢字段。

在 `metadata_index.py` 新增最小 `get_projects(ids: Sequence[str]) -> dict[str, str | None]`。explicit hard scope 才进入 allowed IDs；cwd/agent-inferred scope 仅做小幅、稳定、带测试的排序 boost，例如命中 project 时乘 `1.05`，不得删候选。

- [ ] **Step 5: 运行检索回归确认 GREEN**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/unit/test_routed_retrieval.py \
  tests/unit/test_retrieval.py \
  tests/unit/test_retrieval_fusion.py \
  tests/unit/test_retrieval_mmr.py
```

Expected: 全部通过；semantic timeout 用例仍返回 lexical hits。

- [ ] **Step 6: 提交 routed 检索**

```bash
git add agent_brain/memory/recall/routed_fusion.py \
  agent_brain/memory/recall/retrieval.py \
  agent_brain/platform/indexing/metadata_index.py \
  tests/unit/test_routed_retrieval.py
git commit -m "feat: add dual-route candidate retrieval"
```

### Task 3: 让 Gateway、Firewall、answerability 使用 routed 查询证据

**Files:**
- Create: `agent_brain/memory/context/injection_query_context.py`
- Create: `tests/unit/test_routed_answerability.py`
- Modify: `agent_brain/memory/context/answerability.py`
- Modify: `agent_brain/memory/context/context_firewall.py`
- Modify: `agent_brain/memory/context/context_firewall_types.py`
- Modify: `agent_brain/memory/context/injection_gateway.py`
- Modify: `tests/unit/test_injection_gateway.py`
- Test: `tests/unit/test_answerability_verifier.py`

- [ ] **Step 1: 写 Admission/Gateway 一致性与 route-aware answerability 失败测试**

创建 `tests/unit/test_routed_answerability.py`，覆盖：

```python
def test_admitted_routed_query_is_not_overridden_by_legacy_term_gate(candidate):
    context = routed_context(
        raw_query="为什么 hooks 没有带回以前的知识",
        allowed=True,
        signal_injectable=False,
        evidence=semantic_evidence(candidate, similarity=0.82),
    )
    result = evaluate_injection_candidates([candidate], query_context=context)
    assert [d.candidate.item.id for d in result.included] == [candidate.item.id]


def test_routed_query_without_admission_fails_closed(candidate):
    with pytest.raises(ValueError, match="query_context"):
        InjectionQueryContext(
            raw_query="hooks memory",
            admission=None,
            query_signal=signal(),
            evidence_by_id={},
        )


@pytest.mark.parametrize("similarity", [None, 0.2, 0.59])
def test_weak_semantic_evidence_is_rejected(candidate, similarity):
    result = evaluate_injection_candidates(
        [candidate],
        query_context=routed_context(evidence=semantic_evidence(candidate, similarity)),
    )
    assert result.included == []
    assert "route_answerability_insufficient" in result.excluded[0].reasons


def test_raw_lexical_fallback_requires_query_coverage(candidate):
    context = routed_context(
        raw_query="hooks 为什么没有召回记忆",
        evidence=raw_lexical_evidence(candidate),
    )
    rejected = evaluate_injection_candidates(
        [replace(candidate, body="完全无关的安装说明")], query_context=context,
    )
    accepted = evaluate_injection_candidates(
        [replace(candidate, body="hooks 召回记忆的排障说明")], query_context=context,
    )
    assert rejected.included == []
    assert len(accepted.included) == 1


def test_rrf_score_cannot_substitute_for_semantic_similarity(candidate):
    candidate = replace(candidate, score=999.0)
    context = routed_context(evidence=semantic_evidence(candidate, similarity=None))
    result = evaluate_injection_candidates([candidate], query_context=context)
    assert result.included == []
```

在 `tests/unit/test_injection_gateway.py` 增加 routed Gateway 异常不回退 raw candidate、最终全拒绝保持空、access reinforcement 仅记录 included ID 的测试。

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/unit/test_routed_answerability.py \
  tests/unit/test_injection_gateway.py \
  tests/unit/test_answerability_verifier.py
```

Expected: routed tests 因 `InjectionQueryContext` 和新参数缺失失败；legacy answerability 测试仍通过。

- [ ] **Step 3: 添加不可变 `InjectionQueryContext` 与配置阈值**

创建 `injection_query_context.py`：

```python
from dataclasses import dataclass
from typing import Mapping

from agent_brain.memory.context.query_signal import QuerySignal
from agent_brain.memory.recall.admission import RecallAdmission
from agent_brain.memory.recall.routed_types import RouteEvidence


@dataclass(frozen=True)
class InjectionQueryContext:
    raw_query: str
    admission: RecallAdmission
    query_signal: QuerySignal
    evidence_by_id: Mapping[str, RouteEvidence]

    def __post_init__(self) -> None:
        if not isinstance(self.admission, RecallAdmission):
            raise ValueError("query_context requires RecallAdmission")
```

在 `ContextFirewallConfig` 增加内部可校准参数：

```python
semantic_route_min_similarity: float = 0.60
raw_route_min_coverage: float = 0.50
```

0.60 是初始受测阈值，不是最终性能结论；Task 8 标注集校准若需要调整，必须同时更新固定用例和设计验收记录。

- [ ] **Step 4: 审计并改造 Gateway/Firewall/answerability 全部分支**

给 `evaluate_injection_candidates()`、`build_injection_context()`、`ContextFirewall.filter()` 增加可选 `query_context: InjectionQueryContext | None = None`：

- legacy path 未传 context 时完全保留 `QuerySignal.injectable` 行为；
- routed path cohort eligibility 只读 `query_context.admission`；Admission 不允许或上下文非法时 fail-closed；
- topic-recency gate、cohort gate、item answerability 三处都必须从同一 helper（如 `_routed_query_allowed`）读取 eligibility，不能只修一处；
- strong terms 存在时继续执行 primary-anchor 覆盖规则，但不得再因 `signal.injectable=False` 提前返回；
- terms 为空或 signal/admission 冲突时调用 `verify_routed_candidate_answerability()`；
- semantic route 使用 `evidence.semantic_similarity >= config.semantic_route_min_similarity`；
- raw lexical route 对 `raw_query` 做现有 normalization/分词，按非噪声 token 覆盖率判断；
- 只有 terms route 时沿用现有 term answerability；无可验证 route evidence 返回固定原因 `route_answerability_insufficient`；
- semantic verifier 仅在 deterministic pass 后运行，不能把 deterministic fail 改成 include；
- 把新 reason 加入 Gateway 的 closed reason set。

禁止将 `candidate.score`、`RetrievedItem.score` 或 RRF rank 当作 semantic similarity。

- [ ] **Step 5: 运行安全回归确认 GREEN**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/unit/test_routed_answerability.py \
  tests/unit/test_injection_gateway.py \
  tests/unit/test_answerability_verifier.py \
  tests/unit/test_context_firewall.py \
  tests/unit/test_prompt_injection_gateway_contract.py \
  tests/unit/test_prompt_surface_injection_parity.py
```

Expected: 全部通过；private/secret/review/superseded/scope 安全集仍 100% 拦截。

- [ ] **Step 6: 提交 routed 注入治理**

```bash
git add agent_brain/memory/context/injection_query_context.py \
  agent_brain/memory/context/answerability.py \
  agent_brain/memory/context/context_firewall.py \
  agent_brain/memory/context/context_firewall_types.py \
  agent_brain/memory/context/injection_gateway.py \
  tests/unit/test_routed_answerability.py \
  tests/unit/test_injection_gateway.py
git commit -m "feat: govern routed recall at injection gateway"
```

### Task 4: 增加 CLI routed 模式、结构化 hook 协议与回滚开关

**Files:**
- Create: `agent_brain/interfaces/cli/routed_query.py`
- Create: `tests/unit/test_routed_cli.py`
- Modify: `agent_brain/interfaces/cli/commands/query.py`
- Modify: `agent_brain/interfaces/cli/_shared.py`
- Modify: `tests/unit/test_cli_smoke.py`

- [ ] **Step 1: 写 CLI 合同失败测试**

测试必须覆盖 `injected/empty/timeout/error` 四种稳定 JSON，完整原文传入 request，不读 `AGENT_MEMORY_HUB_RAW_QUERY`，普通 `memory search` 行为不变，feature flag 只回滚候选生成且仍调用 Gateway：

```python
def test_hook_json_uses_full_positional_query(cli_runner, monkeypatch):
    monkeypatch.setenv("AGENT_MEMORY_HUB_RAW_QUERY", "wrong side channel")
    result = cli_runner.invoke(
        app,
        ["search", "hooks 为什么没有召回记忆", "--routed-recall", "--format", "hook-json"],
    )
    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert payload["status"] in {"injected", "empty"}
    assert captured_request.raw_query == "hooks 为什么没有召回记忆"


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("injected", "included"),
        ("empty", "no_candidates"),
        ("timeout", "overall_timeout"),
        ("error", "internal_error"),
    ],
)
def test_hook_json_schema_is_stable(cli_runner, routed_outcome, status, reason):
    payload = invoke_hook_json(cli_runner, routed_outcome(status, reason))
    assert set(payload) == {"status", "reason", "context", "routes"}
    assert payload["status"] == status
    assert payload["reason"] == reason


def test_feature_flag_legacy_candidates_still_cross_gateway(cli_runner, monkeypatch):
    monkeypatch.setenv("AGENT_MEMORY_HUB_ROUTED_RECALL", "0")
    invoke_hook_json(cli_runner, safe_legacy_hit())
    assert gateway_spy.call_count == 1


def test_hook_json_never_constructs_the_default_prod_embedder(cli_runner, monkeypatch):
    monkeypatch.setattr(
        "agent_brain.interfaces.cli._shared.get_default_embedder",
        lambda: pytest.fail("hook path must not cold-load the production model"),
    )
    result = cli_runner.invoke(
        app,
        ["search", "hooks memory", "--routed-recall", "--format", "hook-json"],
    )
    assert result.exit_code == 0
```

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/unit/test_routed_cli.py \
  tests/unit/test_cli_smoke.py
```

Expected: `--routed-recall` 或 `hook-json` 不存在而失败。

- [ ] **Step 3: 实现纯 Python routed orchestration**

在 `routed_query.py` 定义固定协议类型和唯一执行入口：

```python
from dataclasses import asdict, dataclass
from typing import Literal

HookStatus = Literal["injected", "empty", "timeout", "error"]


@dataclass(frozen=True)
class HookSearchPayload:
    status: HookStatus
    reason: str
    context: str
    routes: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
```

执行函数必须一次完成：`build_recall_request` → `search_routed` → hydrate candidates → `build_injection_context(query_context=...)` → render context。所有异常映射为固定 reason；不得把 raw hits 放进异常结果。route trace 仅包含 route/status/latency/candidate_count/reason，不包含 query、terms、item ID 或正文。

在 `_shared.py` 增加 `_open_hook_components()`，它只构造本地 store/index 与显式 degraded 的 `HashingEmbedder`，从而让 `Retriever.search_routed()` 选择 raw BM25 fallback，绝不调用 `get_default_embedder()`：

```python
def _open_hook_components() -> tuple[ItemsStore, HubIndex, Retriever]:
    brain = _brain_dir()
    store = ItemsStore(items_dir=brain / "items")
    embedder = HashingEmbedder()
    embedder.degraded = True
    index = HubIndex(db_path=brain / "index.db", embedding_dim=embedder.dim)
    return store, index, Retriever(index=index, embedder=embedder)
```

`hook-json` 必须调用这个入口。长期 MCP/SDK 进程若已有真实 embedder，可直接给 `search_routed()` 传其现有 Retriever，启用 `semantic_raw`；短生命周期 hook 第一阶段预期稳定走 raw BM25 + term BM25。测试用 injected ready embedder 单独验证 semantic route，不得为了 hook 语义召回而引入模型下载或冷加载。

在 `query.py` 增加 `--routed-recall` 与 `hook-json` 格式分支。`hook-json` 必须自动启用 Context Firewall；普通 text/table/json 与 legacy `Retriever.search()` 不变。删除 routed path 对 `AGENT_MEMORY_HUB_RAW_QUERY` 的读取，但暂不删除 legacy 兼容读取，直到 hook 切换完成。

`AGENT_MEMORY_HUB_ROUTED_RECALL=0` 时只选 legacy candidate generator，之后仍走统一 Gateway；测试中明确禁止绕过 Gateway。

- [ ] **Step 4: 运行 CLI 回归确认 GREEN**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/unit/test_routed_cli.py \
  tests/unit/test_cli_smoke.py \
  tests/unit/test_prompt_injection_gateway_contract.py
```

Expected: 全部通过，所有 `hook-json` 输出均可被 `json.loads()` 解析。

- [ ] **Step 5: 提交 CLI 协议**

```bash
git add agent_brain/interfaces/cli/routed_query.py \
  agent_brain/interfaces/cli/commands/query.py \
  agent_brain/interfaces/cli/_shared.py \
  tests/unit/test_routed_cli.py \
  tests/unit/test_cli_smoke.py
git commit -m "feat: expose routed recall hook protocol"
```

### Task 5: 迁移 shell hook，移除关键词早退与文本解析

**Files:**
- Modify: `agent_runtime_kit/hooks/inject-context.sh`
- Modify: `tests/unit/test_adapter_runtime_events.py`
- Modify: `tests/unit/test_write_shim_fallback.py`
- Modify: `tests/unit/test_adapters.py`
- Test: `tests/unit/test_prompt_surface_injection_parity.py`

- [ ] **Step 1: 先把旧 hook 行为写成必红契约**

新增/修改测试，静态与行为两层同时断言：

```python
def test_hook_delegates_full_prompt_to_routed_cli(hook_text):
    assert "--routed-recall" in hook_text
    assert "--format hook-json" in hook_text
    assert "AGENT_MEMORY_HUB_RAW_QUERY" not in hook_text
    assert '[[ -z "$KEYWORDS" ]]' not in hook_text
    assert "no matches" not in hook_text.lower()


def test_hook_does_not_exit_when_term_extraction_is_empty(run_hook, fake_memory_cli):
    fake_memory_cli.return_hook_json(
        status="injected",
        reason="included",
        context="[fact] routed raw recall",
        routes=[{"route": "semantic_raw", "status": "ok", "candidate_count": 1, "reason": None}],
    )
    result = run_hook(prompt="为什么之前那个方案没有生效", extracted_terms=[])
    assert result.injected_context == "[fact] routed raw recall"


def test_hook_fails_closed_on_malformed_json(run_hook, fake_memory_cli):
    fake_memory_cli.stdout = "not-json"
    result = run_hook(prompt="hooks memory")
    assert result.injected_context == ""
    assert result.exit_code == 0
```

更新旧的 `test_write_shim_fallback.py`：不再要求 hook 用 query-signal gate，而是要求统一 routed CLI/Gateway 合同。

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/unit/test_adapter_runtime_events.py \
  tests/unit/test_write_shim_fallback.py \
  tests/unit/test_adapters.py
```

Expected: 旧 hook 仍含关键词 early exit、raw env side channel 或 text parsing，新增断言失败。

- [ ] **Step 3: 最小迁移 `inject-context.sh`**

保留现有 payload extraction、multimodal evidence capture、2 秒外层 timeout 与各 adapter 输出包装，只替换 query gate/search orchestration：

```sh
SEARCH_ARGS=(
  "$SEARCH_TOOL"
  "$RECALL_PROMPT"
  "--top-k" "$TOP_K"
  "--prefer-type" "$PREFER_TYPES"
  "--routed-recall"
  "--context-firewall"
  "--format" "hook-json"
  "--record-injection-cohort"
  "--record-recall-gap"
  "--adapter" "${AGENT_MEMORY_HUB_ADAPTER:-unknown}"
  "--session" "$SESSION_ID"
  "--cwd" "$CWD"
)

RESULTS=""
SEARCH_STATUS=0
set +e
if [ -n "$TIMEOUT_BIN" ]; then
  RESULTS=$("$TIMEOUT_BIN" "$SEARCH_TIMEOUT_SECONDS" "${SEARCH_ARGS[@]}" 2>/dev/null)
  SEARCH_STATUS=$?
else
  RESULTS=$(AGENT_MEMORY_HUB_SEARCH_TIMEOUT_SECONDS="$SEARCH_TIMEOUT_SECONDS" \
    "$MEMORY_PYTHON" - "${SEARCH_ARGS[@]}" <<'PY' 2>/dev/null
import os
import subprocess
import sys

try:
    proc = subprocess.run(
        sys.argv[1:],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=float(os.environ["AGENT_MEMORY_HUB_SEARCH_TIMEOUT_SECONDS"]),
    )
except subprocess.TimeoutExpired:
    raise SystemExit(124)
sys.stdout.write(proc.stdout)
raise SystemExit(proc.returncode)
PY
  )
  SEARCH_STATUS=$?
fi
set -e

if [ "$SEARCH_STATUS" -ne 0 ]; then
  RESULTS='{"status":"error","reason":"internal_error","context":"","routes":[]}'
fi

CONTEXT="$(printf '%s' "$RESULTS" | "$MEMORY_PYTHON" -c '
import json
import sys

payload = json.load(sys.stdin)
if payload.get("status") == "injected":
    sys.stdout.write(str(payload.get("context") or ""))
' 2>/dev/null || true)"

if [ -z "$CONTEXT" ]; then
  exit 0
fi
```

实际实现将上述 `SEARCH_ARGS` 嵌入现有 timeout 分支并保留 latency 事件，不新增第二套 timeout。JSON 只解析一次；不得重新引入首行、空串、`no matches` 启发式。

- [ ] **Step 4: 更新 hook fingerprint/adapter fixture 并运行合同测试**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/unit/test_adapter_runtime_events.py \
  tests/unit/test_write_shim_fallback.py \
  tests/unit/test_adapters.py \
  tests/unit/test_prompt_surface_injection_parity.py
```

Expected: 全部通过；hook-json malformed/error/timeout 均空注入且 hook 自身安全退出。

- [ ] **Step 5: 提交 hook 迁移**

```bash
git add agent_runtime_kit/hooks/inject-context.sh \
  tests/unit/test_adapter_runtime_events.py \
  tests/unit/test_write_shim_fallback.py \
  tests/unit/test_adapters.py
git commit -m "fix: route hooks through structured recall gateway"
```

### Task 6: 修复 `brief || search` 契约并更新 Agent-facing 指导

**Files:**
- Modify: `agent_brain/interfaces/cli/commands/insight.py`
- Modify: `agent_runtime_kit/AGENT_MEMORY_DISCIPLINE.md`
- Modify: `agent_brain/interfaces/mcp/onboarding.py`
- Modify: `agent_brain/interfaces/mcp/tools/search_tools.py`
- Modify: `agent_brain/agent_integrations/awareness.py`
- Modify: `agent_brain/product/adapter_onboarding.py`
- Modify: `tests/unit/test_brief.py`
- Modify: `tests/unit/test_brief_mcp.py`
- Modify: `tests/unit/test_docs_truth_contract.py`

- [ ] **Step 1: 写 `--fail-empty` 与文档真值失败测试**

```python
def test_brief_fail_empty_exits_three(cli_runner, empty_brain):
    result = cli_runner.invoke(app, ["brief", "--fail-empty"])
    assert result.exit_code == 3


def test_brief_empty_keeps_legacy_zero_exit(cli_runner, empty_brain):
    result = cli_runner.invoke(app, ["brief"])
    assert result.exit_code == 0


def test_agent_docs_do_not_teach_keyword_only_or_shell_or_fallback(repo_root):
    surfaces = load_agent_facing_recall_guidance(repo_root)
    forbidden = ("brief ||", "3-5 keywords", "3–5 keywords", "提取 3-5 个关键词")
    for surface, text in surfaces.items():
        assert not any(term in text for term in forbidden), surface
    assert all("完整任务描述" in text or "full task description" in text for text in surfaces.values())
```

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/unit/test_brief.py \
  tests/unit/test_brief_mcp.py \
  tests/unit/test_docs_truth_contract.py
```

Expected: CLI 不认识 `--fail-empty`，且至少一个文档仍建议 `3-5 keywords`。

- [ ] **Step 3: 实现兼容退出码并统一文档语义**

在 Typer `brief()` 增加：

```python
fail_empty: bool = typer.Option(
    False,
    "--fail-empty",
    help="Exit with code 3 when the brief contains no active memory items.",
)
```

生成 brief 后，仅当 `total_shown == 0 and fail_empty` 时 `raise typer.Exit(3)`；默认空结果仍 exit 0，MCP `brief_memory` 的 `total_shown` 结构不变。

统一 Agent-facing 话术：

- `brief` 用于恢复项目全貌；
- `search` 用于当前具体任务相关性；
- 禁止推荐 `brief || search`；
- search 输入完整任务描述，不要求 Agent 先主观切 3–5 个关键词；
- `--project` 只在用户显式指定或 cwd 映射确定时使用；自然语言推断项目不得当 hard filter。

- [ ] **Step 4: 运行文档与 CLI 回归确认 GREEN**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/unit/test_brief.py \
  tests/unit/test_brief_mcp.py \
  tests/unit/test_docs_truth_contract.py \
  tests/unit/test_system_fewshot_matrix.py
```

Expected: 全部通过，默认 brief 兼容性不变。

- [ ] **Step 5: 提交主动召回治理**

```bash
git add agent_brain/interfaces/cli/commands/insight.py \
  agent_runtime_kit/AGENT_MEMORY_DISCIPLINE.md \
  agent_brain/interfaces/mcp/onboarding.py \
  agent_brain/interfaces/mcp/tools/search_tools.py \
  agent_brain/agent_integrations/awareness.py \
  agent_brain/product/adapter_onboarding.py \
  tests/unit/test_brief.py \
  tests/unit/test_brief_mcp.py \
  tests/unit/test_docs_truth_contract.py
git commit -m "docs: govern agent initiated memory recall"
```

### Task 7: 扩展 doctor 与旧 hook 升级 repair 验证

**Files:**
- Modify: `agent_brain/platform/doctor.py`
- Modify: `agent_brain/interfaces/cli/doctor_offline.py`
- Modify if required by failing test: `agent_brain/platform/install_repair.py`
- Modify: `tests/unit/test_doctor_offline.py`
- Modify: `tests/unit/test_update_repair_cli.py`
- Modify: `tests/unit/test_adapters.py`

- [ ] **Step 1: 写运行状态与旧 fingerprint repair 失败测试**

doctor 至少返回四个独立事实，而不是一个笼统“embedding import 成功”：

```python
def test_doctor_reports_routed_recall_gateway_and_offline_fallback(doctor_report):
    checks = doctor_report.checks
    assert checks["recall.routed.status"] in {"enabled", "rollback"}
    assert checks["security.injection_gateway.available"] is True
    assert checks["recall.semantic_provider.status"] in {
        "fast_ready",
        "not_fast_ready",
        "unavailable",
    }
    assert checks["recall.lexical_raw_fallback.status"] == "ready"


def test_repair_replaces_old_keyword_gate_hook(tmp_home, old_hook_fixture):
    install_old_hook(tmp_home, old_hook_fixture)
    result = run_repair(tmp_home)
    installed = read_installed_hook(tmp_home)
    assert result.changed is True
    assert "--routed-recall" in installed
    assert "AGENT_MEMORY_HUB_RAW_QUERY" not in installed
```

- [ ] **Step 2: 运行测试确认 RED**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/unit/test_doctor_offline.py \
  tests/unit/test_update_repair_cli.py \
  tests/unit/test_adapters.py
```

Expected: doctor 缺少 routed/fast-ready/fallback 行，旧 hook repair 合同至少一项失败。

- [ ] **Step 3: 实现 doctor 状态并只在测试证明需要时修改 repair**

doctor 规则：

- `routed_recall`: 环境变量为 `0` 显示 rollback，否则 enabled；
- `injection_gateway`: import + closed exclusion reasons 校验成功才 ready；
- `semantic_provider`: 区分“依赖已安装”和“当前 surface 无冷加载即可 fast-ready”；禁止仅靠 import 宣称 hook ready；
- `lexical_raw_fallback`: FTS/BM25 可用且 routed CLI 已安装才 ready。

`doctor_offline.py` 渲染固定四行。先运行现有 repair 流程测试；若它已按 source fingerprint 更新 hook，只补回归 fixture，不改 production。只有测试证明旧 hook 无法更新时，才最小修改 `install_repair.py`，避免无依据重写安装器。

- [ ] **Step 4: 运行 doctor/repair 回归确认 GREEN**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/unit/test_doctor_offline.py \
  tests/unit/test_update_repair_cli.py \
  tests/unit/test_adapters.py
```

Expected: 全部通过；旧用户升级包并执行 repair 后获得 routed hook。

- [ ] **Step 5: 提交 doctor 与升级健康度**

```bash
git add agent_brain/platform/doctor.py \
  agent_brain/interfaces/cli/doctor_offline.py \
  agent_brain/platform/install_repair.py \
  tests/unit/test_doctor_offline.py \
  tests/unit/test_update_repair_cli.py \
  tests/unit/test_adapters.py
git commit -m "feat: diagnose and repair routed recall hooks"
```

如果 `install_repair.py` 未修改，不要把它加入该提交。

### Task 8: 建立 40 条标注评测、安全矩阵与确定性 deadline 测试

**Files:**
- Create: `tests/fixtures/dual_route_recall_cases.json`
- Create: `tests/system/test_dual_route_recall_matrix.py`
- Create: `scripts/benchmark-dual-route-hook.py`
- Modify: `tests/unit/test_system_benchmark.py`
- Modify: `tests/unit/test_system_fewshot_matrix.py`

- [ ] **Step 1: 写固定标注集与 schema test**

JSON 每条必须含：

```json
{
  "id": "semantic-zh-01",
  "category": "semantic_paraphrase",
  "query": "为什么输入问题后没有带回以前的知识",
  "expected_item_ids": ["fixture-hook-recall"],
  "expect_admission": true,
  "expect_injection": true,
  "legacy_false_negative": true,
  "prohibited_item_ids": []
}
```

固定五类，每类至少 8 条：`semantic_paraphrase`、`multilingual`、`keyword_extraction_error`、`exact_entity`、`weak_or_no_value`。测试断言总数不少于 40、每类不少于 8、所有 ID 唯一、至少 3 条标记为既有 false negative。

- [ ] **Step 2: 写端到端矩阵与安全失败测试**

`test_dual_route_recall_matrix.py` 对同一 fixture brain 同时运行 legacy baseline 与 routed path，分别记录 Candidate Recall@10 和最终 injection decision。断言：

```python
assert routed_candidate_recall_at_10 >= legacy_candidate_recall_at_10
assert fixed_legacy_false_negatives >= 3
assert new_false_negatives_outside_target == []
assert prohibited_injection_rate == 0.0
```

安全条目必须覆盖 private、secret、needs-review、superseded、scope mismatch 和 Gateway exception。再用 fake clock/deadline 覆盖 semantic timeout、整体 timeout、lexical 已完成结果保留；CI 不用墙钟断言 150ms。

- [ ] **Step 3: 运行评测确认 RED 并记录真实失败原因**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/system/test_dual_route_recall_matrix.py \
  tests/unit/test_system_benchmark.py \
  tests/unit/test_system_fewshot_matrix.py
```

Expected: 首次运行若阈值、coverage 或 fixture 命中不足，应给出具体 case ID 与 route evidence；不得通过降低禁止注入约束来转绿。

- [ ] **Step 4: 仅用标注结果校准 answerability 阈值**

调整范围只限：

- `semantic_route_min_similarity`；
- `raw_route_min_coverage`；
- 可解释的停用词/噪声 token 表；
- 明确有证据的 ProjectScope soft boost。

每次调整都运行整个 40-case matrix。禁止为单条 case 写 item ID 特判、查询字符串特判或关闭 Gateway 规则。

- [ ] **Step 5: 增加独立 30 次性能验收脚本**

`scripts/benchmark-dual-route-hook.py` 必须：

- 接受旧/新 hook 命令、fixture payload、重复次数，默认 30；
- 先各 warm-up 3 次；
- 输出旧 p50/p95、新 p50/p95、p95 增量、超时数；
- 新链路任一次超过 2 秒或 p95 增量超过 150ms 时 exit 1；
- 不输出原始 Prompt、关键词或候选内容。

本脚本属于发布前独立验收，不放进普通 CI 墙钟断言。

- [ ] **Step 6: 运行矩阵确认 GREEN**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/system/test_dual_route_recall_matrix.py \
  tests/unit/test_system_benchmark.py \
  tests/unit/test_system_fewshot_matrix.py
```

Expected: Candidate Recall@10 不降、至少修复 3 条目标 false negative、其他类别无新增 false negative、禁止注入 100% 拦截。

- [ ] **Step 7: 提交评测与校准**

```bash
git add tests/fixtures/dual_route_recall_cases.json \
  tests/system/test_dual_route_recall_matrix.py \
  scripts/benchmark-dual-route-hook.py \
  tests/unit/test_system_benchmark.py \
  tests/unit/test_system_fewshot_matrix.py \
  agent_brain/memory/context/context_firewall_types.py
git commit -m "test: calibrate dual-route recall governance"
```

仅在阈值实际变化时加入 `context_firewall_types.py`。

### Task 9: 全面回归、静态旁路审计与发布记录

**Files:**
- Modify: `docs/architecture.md`（若当前架构文档有召回链路图）
- Modify: `CHANGELOG.md` 或仓库实际 release note 文件
- Test: 全仓测试与静态扫描

- [ ] **Step 1: 更新架构与升级说明**

文档必须说明：

- Gateway 是逻辑安全边界，不是常驻 semantic service；
- hooks 不冷加载/下载模型，semantic 不 ready 时用 raw BM25 + term BM25；
- 旧用户需要升级 package 并 refresh/repair adapter；
- `AGENT_MEMORY_HUB_ROUTED_RECALL=0` 只回滚候选生成，不关闭 Gateway；
- `brief` 与 `search` 的职责不同；
- 第一阶段明确不处理“继续/确认/是/1”的 session continuation。

- [ ] **Step 2: 运行静态旁路与 placeholder 扫描**

Run:

```bash
rg -n 'AGENT_MEMORY_HUB_RAW_QUERY|brief[[:space:]]*\|\||3[-–]5 keywords|no matches' \
  agent_runtime_kit agent_brain tests docs
rg -n 'TODO|TBD|FIXME|XXX|\.\.\.' \
  agent_brain/memory/recall \
  agent_brain/memory/context \
  agent_brain/interfaces/cli/routed_query.py \
  tests/unit/test_routed_retrieval.py \
  tests/unit/test_routed_answerability.py \
  tests/system/test_dual_route_recall_matrix.py
```

Expected: 第一条只允许出现在明确的 legacy 兼容测试或迁移说明中；hook 与 Agent-facing 指导无命中。第二条在本次新增/修改实现和测试中无命中。

- [ ] **Step 3: 运行 targeted suite、Ruff 与 diff check**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/unit/test_recall_admission.py \
  tests/unit/test_routed_retrieval.py \
  tests/unit/test_routed_answerability.py \
  tests/unit/test_routed_cli.py \
  tests/unit/test_injection_gateway.py \
  tests/unit/test_adapter_runtime_events.py \
  tests/unit/test_brief.py \
  tests/unit/test_doctor_offline.py \
  tests/system/test_dual_route_recall_matrix.py
.venv/bin/ruff check agent_brain tests scripts
git diff --check
```

Expected: 全部通过，无 lint 或 whitespace error。

- [ ] **Step 4: 运行全仓测试**

Run:

```bash
ulimit -n 8192
PYTHONPATH=. .venv/bin/python -m pytest -q
```

Expected: 全仓通过；允许既有显式 skip，不允许新 failure/error。若未提升 fd 限制出现 `OSError: [Errno 24] Too many open files`，先确认是环境伪失败再按同一命令重跑，不修改业务代码掩盖。

- [ ] **Step 5: 运行独立 hook 性能验收**

先用当前 base hook 保存为本地 baseline command，再运行：

```bash
PYTHONPATH=. .venv/bin/python scripts/benchmark-dual-route-hook.py \
  --baseline-command /tmp/amh-baseline-inject-context.sh \
  --candidate-command agent_runtime_kit/hooks/inject-context.sh \
  --runs 30
```

Expected: candidate 无单次超过 2 秒，p95 增量不超过 150ms。把聚合数字写入 PR/交付说明，不提交包含用户 Prompt 的原始 benchmark 日志。

- [ ] **Step 6: 自审兼容性、隐私与变更范围**

逐项确认：

- `Retriever.search()`、普通 `memory search`、SDK/MCP raw diagnostics 未改变；
- routed path 的 Admission、Gateway、answerability 三层语义一致；
- semantic similarity 来自 cosine 原始证据，未误用 RRF score；
- explicit project 才 hard filter；cwd/agent-inferred 只 soft hint；
- telemetry 无 raw query、terms、拒绝 item ID/正文；
- Gateway error/all_rejected 不回退 raw hit；
- 无 schema migration、无 reindex、无 daemon；
- `codex/staged-agent-recall-governance` 文件未被意外带入。

- [ ] **Step 7: 提交发布文档与最终验证修正**

```bash
git add docs/architecture.md CHANGELOG.md
git commit -m "docs: document dual-route recall rollout"
```

如果仓库实际 release note 文件不是 `CHANGELOG.md`，只加入真实存在且符合项目惯例的文件；不要为此凭空创建第二套发布体系。

- [ ] **Step 8: 输出最终证据快照**

Run:

```bash
git status --short --branch
git log --oneline --decorate -10
git diff --stat origin/codex/p0-injection-gateway...HEAD
```

Expected: worktree clean；提交按 Admission、retrieval、Gateway、CLI、hook、docs/brief、doctor、evaluation、release 的独立责任分层；最终交付说明包含测试数、skip 数、40-case 指标、安全拦截率和 30-run p95。
