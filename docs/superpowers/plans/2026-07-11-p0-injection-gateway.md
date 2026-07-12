# P0-A Unified Prompt Injection Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立唯一、强制、fail-closed 的 prompt injection gateway，阻止 MCP search/brief、SDK、Hook 和 Web prompt search 将 private/secret、待审核、已废止或弱查询候选直接装入模型上下文。

**Architecture:** Retriever 保留 raw hits；新增 `agent_brain.memory.context.injection_gateway` 统一执行 query-signal、ContextFirewall 和 ContextPack。MCP prompt tools、SDK firewall 模式、CLI `--context-firewall`、brief 与 Web prompt search 全部依赖该入口；显式 raw diagnostics 保留但不生成未经防火墙授权的 `context_pack`。

**Tech Stack:** Python 3.11+、dataclasses、Pydantic MemoryItem、SQLite/FTS Retriever、Typer CLI、FastMCP、pytest、Ruff。

---

## 文件地图

新建：

- `agent_brain/memory/context/injection_gateway.py`：唯一 prompt eligibility + packing facade。
- `tests/unit/test_injection_gateway.py`：Gateway 单元测试。
- `tests/unit/test_mcp_injection_gateway.py`：MCP 泄漏与弱查询回归。
- `tests/unit/test_prompt_injection_gateway_contract.py`：静态旁路契约。
- `tests/unit/test_prompt_surface_injection_parity.py`：MCP/SDK/CLI 结果集一致性。

修改：

- `agent_brain/interfaces/mcp/tools/search_tools.py`
- `agent_brain/memory/recall/brief.py`
- `tests/unit/test_brief.py`
- `tests/unit/test_brief_mcp.py`
- `agent_brain/interfaces/sdk/query.py`
- `agent_brain/interfaces/sdk/sdk.py`
- `tests/unit/test_sdk_client.py`
- `agent_brain/interfaces/cli/commands/query.py`
- `tests/unit/test_cli_smoke.py`
- `agent_brain/platform/doctor.py`
- `agent_brain/interfaces/cli/doctor_offline.py`
- `agent_brain/product/memory_lineage.py`
- `tests/unit/test_doctor_offline.py`
- `tests/unit/test_memory_lineage.py`
- `docs/architecture.md`

本计划只实现 [P0-A 设计规范](../specs/2026-07-11-p0-injection-gateway-design.md)。Harvest/audit/quarantine 与底层 path containment 留给 P0-B、P0-C。

---

### Task 1: 建立 Gateway 核心与 fail-closed query/packing

**Files:**
- Create: `tests/unit/test_injection_gateway.py`
- Create: `agent_brain/memory/context/injection_gateway.py`

- [x] **Step 1: 写缺失 Gateway 时必红的测试**

创建 `tests/unit/test_injection_gateway.py`：

```python
from datetime import datetime, timezone

import pytest

from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Sensitivity
from agent_brain.memory.context.context_firewall_types import ContextCandidate

NOW = datetime(2026, 7, 11, tzinfo=timezone.utc)


def item(
    suffix,
    *,
    sensitivity=Sensitivity.internal,
    tags=None,
    superseded_by=None,
    validity=None,
    context_views=None,
):
    return MemoryItem(
        id=f"mem-20260711-000000-{suffix}",
        type=MemoryType.episode,
        created_at=NOW,
        title=f"Injection gateway {suffix}",
        summary=f"Gateway boundary {suffix}",
        tags=tags or [],
        sensitivity=sensitivity,
        superseded_by=superseded_by,
        validity=validity or {},
        context_views=context_views or {},
        confidence=0.9,
    )


def candidate(value, score=1.0):
    return ContextCandidate(item=value, body=f"body:{value.id}", score=score)


def test_gateway_keeps_noninjectable_query_signal_fail_closed():
    from agent_brain.memory.context.injection_gateway import evaluate_injection_candidates

    value = item("weak-query")
    result = evaluate_injection_candidates([candidate(value)], query="memory")
    assert result.included == []
    assert "query_not_injectable" in result.cohort_reasons


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (item("private", sensitivity=Sensitivity.private), "sensitivity_not_allowed"),
        (item("secret", sensitivity=Sensitivity.secret), "sensitivity_not_allowed"),
        (item("review", tags=["needs-review"]), "requires_review"),
        (item("unverified", tags=["unverified-boundary"]), "requires_review"),
        (item("superseded", superseded_by="mem-new"), "superseded"),
    ],
)
def test_gateway_excludes_noninjectable_item_states(value, reason):
    from agent_brain.memory.context.injection_gateway import evaluate_injection_candidates

    result = evaluate_injection_candidates([candidate(value)])
    assert result.included == []
    assert reason in result.excluded[0].reasons


def test_gateway_builds_existing_context_pack_contract():
    from agent_brain.memory.context.injection_gateway import build_injection_context

    value = item("safe")
    result = build_injection_context(
        [candidate(value)], query="injection gateway safe", requested="auto", max_items=1,
    )
    pack = result.included[0].pack
    assert pack.item_id == value.id
    assert pack.detail_uri == f"memory://items/{value.id}/body"
    assert pack.retrieve_hint == f"read_memory(id='{value.id}', head=2000, view='detail')"
    assert result.metrics()["packed_tokens"] == pack.packed_tokens


def test_gateway_preserves_scope_and_max_item_gates():
    from agent_brain.memory.context.injection_gateway import evaluate_injection_candidates

    scoped = item(
        "scope-gate",
        tags=["state"],
        validity={"cwd": "/expected"},
    )
    scope_result = evaluate_injection_candidates(
        [candidate(scoped)],
        current_scope={"cwd": "/other"},
    )
    assert scope_result.included == []
    assert "scope_mismatch" in scope_result.excluded[0].reasons

    first = item("max-first")
    second = item("max-second")
    max_result = evaluate_injection_candidates(
        [candidate(first, 2.0), candidate(second, 1.0)],
        max_items=1,
    )
    assert len(max_result.included) == 1
    assert "max_items_exceeded" in max_result.excluded[0].reasons


def test_gateway_applies_final_pack_budget():
    from agent_brain.memory.context.injection_gateway import build_injection_context

    value = item(
        "pack-budget",
        context_views={
            "locator": "pack budget locator",
            "overview": "pack budget overview",
        },
    )
    result = build_injection_context(
        [candidate(value)],
        requested="detail",
        budget_tokens=0,
    )
    assert result.included == []
    assert "pack_budget_exceeded" in result.excluded[0].reasons


def test_gateway_diagnostic_logs_only_aggregate_reason(caplog):
    from agent_brain.memory.context.injection_gateway import _record_injection_diagnostic

    _record_injection_diagnostic(
        surface="mcp-search",
        reason="hydrate_error",
        count=2,
    )
    assert "surface=mcp-search reason=hydrate_error count=2" in caplog.text
    assert "mem-" not in caplog.text


def test_gateway_excludes_one_pack_error_without_dropping_safe_peer(monkeypatch):
    import agent_brain.memory.context.injection_gateway as gateway

    broken = item("broken-pack")
    safe = item("safe-pack")
    real_pack = gateway.pack_decisions

    def conditional_pack(decisions, **kwargs):
        if decisions[0].candidate.item.id == broken.id:
            raise RuntimeError("synthetic pack failure")
        return real_pack(decisions, **kwargs)

    monkeypatch.setattr(gateway, "pack_decisions", conditional_pack)
    result = gateway.build_injection_context(
        [candidate(broken, 2.0), candidate(safe, 1.0)],
        query="injection gateway pack",
        max_items=2,
    )
    assert [entry.decision.candidate.item.id for entry in result.included] == [safe.id]
    rejected = {decision.candidate.item.id: decision for decision in result.excluded}
    assert "pack_error" in rejected[broken.id].reasons
    assert result.metrics()["excluded_reasons"]["pack_error"] == 1
    assert broken.id not in repr(result.metrics())
    assert broken.title not in repr(result.metrics())
```

- [x] **Step 2: 运行测试确认 RED**

Run:

```bash
PYTHONPATH=. .venv/bin/python \
  -m pytest -q tests/unit/test_injection_gateway.py
```

Expected: `ModuleNotFoundError: No module named 'agent_brain.memory.context.injection_gateway'`。

- [x] **Step 3: 写最小 Gateway 实现**

创建 `agent_brain/memory/context/injection_gateway.py`：

```python
"""Single fail-closed boundary for turning retrieved memories into prompt context."""
import logging
from collections import Counter
from dataclasses import dataclass
from typing import Mapping

from agent_brain.memory.context.context_firewall import ContextFirewall
from agent_brain.memory.context.context_firewall_rules import exclude_with
from agent_brain.memory.context.context_firewall_types import (
    ContextCandidate,
    FirewallDecision,
    FirewallResult,
)
from agent_brain.memory.context.context_loading import ContextVerbosity
from agent_brain.memory.context.context_packing import PackedDecision, pack_decisions
from agent_brain.memory.context.query_signal import QuerySignal, analyze_injection_query

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InjectionResult:
    included: list[PackedDecision]
    excluded: list[FirewallDecision]
    cohort_reasons: tuple[str, ...]
    used_tokens: int
    full_tokens: int

    def metrics(self) -> dict[str, object]:
        reason_counts = Counter(
            reason
            for decision in self.excluded
            for reason in set(decision.reasons)
        )
        return {
            "candidate_count": len(self.included) + len(self.excluded),
            "included_count": len(self.included),
            "excluded_count": len(self.excluded),
            "excluded_reasons": dict(sorted(reason_counts.items())),
            "items": [
                {
                    "id": entry.decision.candidate.item.id,
                    "selected_view": entry.pack.selected_view,
                    "packed_tokens": entry.pack.packed_tokens,
                    "full_tokens": entry.pack.full_tokens,
                    "compressed": entry.pack.compressed,
                }
                for entry in self.included
            ],
            "packed_tokens": self.used_tokens,
            "full_tokens": self.full_tokens,
        }


def _record_injection_diagnostic(*, surface: str, reason: str, count: int) -> None:
    """Emit aggregate-only diagnostics without query or candidate content."""
    if count <= 0:
        return
    logger.warning(
        "injection diagnostic surface=%s reason=%s count=%d",
        surface,
        reason,
        count,
    )


def evaluate_injection_candidates(
    candidates: list[ContextCandidate],
    *,
    query: str | None = None,
    query_signal: QuerySignal | None = None,
    max_items: int | None = None,
    current_scope: Mapping[str, str] | None = None,
) -> FirewallResult:
    signal = query_signal
    if signal is None and query:
        signal = analyze_injection_query(query.replace("|", " "))
    return ContextFirewall().filter(
        candidates,
        query=query,
        query_signal=signal,
        max_items=max_items,
        current_scope=current_scope,
    )


def build_injection_context(
    candidates: list[ContextCandidate],
    *,
    query: str | None = None,
    query_signal: QuerySignal | None = None,
    requested: ContextVerbosity = "auto",
    max_items: int | None = None,
    budget_tokens: int | None = None,
    current_scope: Mapping[str, str] | None = None,
) -> InjectionResult:
    firewall = evaluate_injection_candidates(
        candidates,
        query=query,
        query_signal=query_signal,
        max_items=max_items,
        current_scope=current_scope,
    )
    included = []
    excluded = list(firewall.excluded)
    used_tokens = 0
    full_tokens = 0
    for decision in firewall.included:
        remaining = None if budget_tokens is None else max(0, budget_tokens - used_tokens)
        try:
            packed = pack_decisions([decision], requested=requested, budget_tokens=remaining)
        except Exception:
            excluded.append(exclude_with(decision, "pack_error"))
            continue
        included.extend(packed.included)
        excluded.extend(packed.excluded)
        used_tokens += packed.used_tokens
        full_tokens += packed.full_tokens
    return InjectionResult(
        included=included,
        excluded=excluded,
        cohort_reasons=firewall.cohort_reasons,
        used_tokens=used_tokens,
        full_tokens=full_tokens,
    )


__all__ = [
    "InjectionResult",
    "build_injection_context",
    "evaluate_injection_candidates",
]
```

- [x] **Step 4: 验证 GREEN**

```bash
PYTHONPATH=. .venv/bin/python \
  -m pytest -q tests/unit/test_injection_gateway.py tests/unit/test_context_firewall.py \
  tests/unit/test_context_loading_views.py
```

Expected: 全部 PASS，0 warning。

- [x] **Step 5: 提交 Gateway 核心**

```bash
git add agent_brain/memory/context/injection_gateway.py tests/unit/test_injection_gateway.py
git commit -m "fix: add fail-closed injection gateway"
```

#### Task 1 实施审查修订（已落地）

上方代码块是初始 TDD 起点；经规格与代码质量双审后，实际实现已由
`43581a6`、`fe16ddf`、`612f85b`、`46c5ac7` supersede，后续任务以当前源码和
以下不变量为准，不得重新套用初始样例：

- 显式空 query 也 fail-close；查询在 Gateway 中只分析一次。
- 非法 verbosity 在进入逐项 packing 前抛 `ValueError`，不记为 `pack_error`。
- Gateway metrics 仅含聚合计数、原因和 token；不含 included/excluded item ID
  或 title/summary/body。
- `pack_error` / `pack_budget_exceeded` 不占用最终 `max_items` slot；安全候选可补位。
- 每个候选最多执行一次逐项/语义 eligibility 与一次 pack；最终成功集通过
  `ContextFirewall.validate_cohort` 做 cohort-only 复核，不得重复调用语义验证器。
- 最终 Task 1 定向矩阵：`67 passed`；规格审查和代码质量审查均 PASS。

---

### Task 2: 让 MCP `search_memory` 强制经过 Gateway

**Files:**
- Create: `tests/unit/test_mcp_injection_gateway.py`
- Modify: `agent_brain/interfaces/mcp/tools/search_tools.py:5-154`

- [x] **Step 1: 写 MCP 泄漏与弱查询回归测试**

创建 `tests/unit/test_mcp_injection_gateway.py`：

```python
from datetime import datetime, timezone

import pytest

from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Sensitivity
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex


@pytest.fixture(autouse=True)
def close_mcp_components():
    from agent_brain.interfaces.mcp.tools._shared import _components_cache

    for _store, index, _retriever in _components_cache.values():
        index.close()
    _components_cache.clear()
    yield
    for _store, index, _retriever in _components_cache.values():
        index.close()
    _components_cache.clear()


def memory(suffix, *, sensitivity=Sensitivity.internal, tags=None, superseded_by=None):
    return MemoryItem(
        id=f"mem-20260711-010000-{suffix}",
        type=MemoryType.fact,
        created_at=datetime(2026, 7, 11, 1, 0, tzinfo=timezone.utc),
        title=f"Injection gateway boundary {suffix}",
        summary=f"Safe boundary query {suffix}",
        tags=tags or [],
        sensitivity=sensitivity,
        superseded_by=superseded_by,
        refs={"urls": [f"https://example.test/{suffix}"]},
        confidence=0.9,
    )


def seed(brain, items):
    store = ItemsStore(brain / "items")
    embedder = HashingEmbedder()
    index = HubIndex(brain / "index.db", embedding_dim=embedder.dim)
    for value in items:
        body = f"Injection gateway boundary body {value.title}"
        store.write(value, body)
        index.upsert(value, body, embedding=embedder.embed(body))
    index.close()


def test_mcp_search_never_serializes_rejected_memory_content(tmp_brain):
    safe = memory("safe")
    private = memory("private", sensitivity=Sensitivity.private)
    secret = memory("secret", sensitivity=Sensitivity.secret)
    review = memory("review", tags=["needs-review"])
    superseded = memory("superseded", superseded_by=safe.id)
    seed(tmp_brain, [safe, private, secret, review, superseded])

    import agent_brain.interfaces.mcp.server as mcp

    result = mcp.search_memory("injection gateway boundary", top_k=10, verbosity="detail")
    assert [row["id"] for row in result] == [safe.id]
    serialized = repr(result)
    for forbidden in (private, secret, review, superseded):
        assert forbidden.id not in serialized
        assert forbidden.title not in serialized
        assert forbidden.summary not in serialized
    assert result[0]["context_pack"]["item_id"] == safe.id


def test_mcp_search_returns_empty_for_noninjectable_query(tmp_brain):
    value = memory("weak-memory")
    seed(tmp_brain, [value])

    import agent_brain.interfaces.mcp.server as mcp

    assert mcp.search_memory("memory", top_k=10, verbosity="auto") == []


def test_mcp_search_drops_index_hit_that_cannot_be_hydrated(tmp_brain, caplog):
    ghost = memory("hydrate-error")
    embedder = HashingEmbedder()
    index = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    body = "Injection gateway boundary hydrate error"
    index.upsert(ghost, body, embedding=embedder.embed(body))
    index.close()

    import agent_brain.interfaces.mcp.server as mcp

    result = mcp.search_memory("injection gateway boundary hydrate error", top_k=10)
    assert result == []
    assert ghost.id not in repr(result)
    assert "surface=mcp-search reason=hydrate_error count=1" in caplog.text


def test_mcp_search_never_falls_back_to_raw_when_gateway_fails(
    tmp_brain,
    monkeypatch,
):
    value = memory("gateway-failure")
    seed(tmp_brain, [value])

    import agent_brain.interfaces.mcp.server as mcp
    from agent_brain.interfaces.mcp.tools import search_tools

    def fail_closed(*_args, **_kwargs):
        raise RuntimeError("synthetic gateway failure")

    monkeypatch.setattr(search_tools, "build_injection_context", fail_closed)
    with pytest.raises(RuntimeError, match="synthetic gateway failure"):
        mcp.search_memory("injection gateway boundary", top_k=10)
```

- [x] **Step 2: 运行测试确认 RED**

```bash
PYTHONPATH=. .venv/bin/python \
  -m pytest -q tests/unit/test_mcp_injection_gateway.py
```

Expected: FAIL；当前 MCP 返回禁止项，弱查询不会 fail-close。

- [x] **Step 3: 用 Gateway 重写 MCP search**

将 context imports 改为：

```python
from agent_brain.memory.context.context_firewall_types import ContextCandidate
from agent_brain.memory.context.injection_gateway import (
    _record_injection_diagnostic,
    build_injection_context,
)
```

删除 `select_context_view`/`build_context_pack` import。把 raw hits 到响应转换改为：

```python
    raw_top_k = top_k * 3 if top_k > 0 else top_k
    old_record_access = getattr(retriever, "record_access", None)
    if old_record_access is not None:
        retriever.record_access = False
    try:
        hits = retriever.search(
            query,
            top_k=raw_top_k,
            filters=sf,
            explain=include_trace,
        )
    finally:
        if old_record_access is not None:
            retriever.record_access = old_record_access
    _record_injection_diagnostic(
        surface="mcp-search",
        reason="hydrate_error",
        count=sum(1 for hit in hits if hit.id not in items_by_id),
    )
    hit_by_id = {hit.id: hit for hit in hits}
    candidates = [
        ContextCandidate(
            item=items_by_id[hit.id],
            body=bodies_by_id.get(hit.id, ""),
            score=hit.score,
            source="mcp-search",
        )
        for hit in hits
        if hit.id in items_by_id
    ]
    injection = build_injection_context(
        candidates, query=query, requested=verbosity, max_items=top_k,
    )
    results = []
    for entry in injection.included:
        decision = entry.decision
        item = decision.candidate.item
        pack = entry.pack
        hit = hit_by_id.get(item.id)
        result = {
            "id": item.id,
            "title": item.title,
            "type": str(item.type),
            "summary": item.summary,
            "confidence": item.confidence,
            "score": hit.score if hit is not None else decision.score,
            "context_pack": pack.to_dict(),
            "locator": item.context_views.locator,
        }
        if verbosity == "auto":
            result["selected_view"] = pack.selected_view
            result["load_reason"] = list(pack.load_reason)
            if pack.selected_view == "detail":
                result["overview"] = item.context_views.overview
                result["body"] = pack.text
            elif pack.selected_view == "overview":
                result["overview"] = pack.text
            else:
                result["snippet"] = pack.text
        elif verbosity == "overview":
            result["overview"] = pack.text
        elif verbosity == "detail":
            result["overview"] = item.context_views.overview
            result["body"] = pack.text
        else:
            result["snippet"] = pack.text
        if hit is not None and hit.trace is not None:
            result["retrieval_trace"] = hit.trace.to_dict()
        results.append(result)
    return results
```

- [x] **Step 4: 验证 MCP GREEN**

```bash
PYTHONPATH=. .venv/bin/python \
  -m pytest -q tests/unit/test_mcp_injection_gateway.py \
  tests/unit/test_context_loading_views.py tests/unit/test_mcp_onboarding.py
```

Expected: 全部 PASS。

- [x] **Step 5: 提交 MCP 迁移**

```bash
git add agent_brain/interfaces/mcp/tools/search_tools.py tests/unit/test_mcp_injection_gateway.py
git commit -m "fix: gate MCP search context"
```

---

### Task 3: 在 brief tier 选择前执行统一 eligibility

**Files:**
- Modify: `agent_brain/memory/recall/brief.py:68-114`
- Modify: `tests/unit/test_brief.py:1-66`
- Modify: `tests/unit/test_brief_mcp.py`
- Modify: `tests/unit/test_sdk_client.py:284-288`

- [x] **Step 1: 写 brief 禁止项与 withheld RED 测试**

先把 `tests/unit/test_brief_mcp.py` 的 contract import 改为：

```python
from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Sensitivity
```

再追加：

```python
def test_brief_memory_filters_noninjectable_items_before_tiering(tmp_brain):
    store = ItemsStore(tmp_brain / "items")
    now = datetime.now(timezone.utc).astimezone()
    safe = MemoryItem(
        id=make_item_id("safe-signal", when=now),
        type=MemoryType.signal,
        created_at=now,
        title="safe gateway signal",
        summary="safe summary",
        confidence=0.9,
    )
    private = safe.model_copy(update={
        "id": make_item_id("private-signal", when=now),
        "title": "private gateway signal",
        "sensitivity": Sensitivity.private,
    })
    review = safe.model_copy(update={
        "id": make_item_id("review-signal", when=now),
        "title": "review gateway signal",
        "tags": ["needs-review"],
    })
    superseded = safe.model_copy(update={
        "id": make_item_id("superseded-signal", when=now),
        "title": "superseded gateway signal",
        "superseded_by": safe.id,
    })
    for value in (safe, private, review, superseded):
        store.write(value, f"body:{value.title}")

    import agent_brain.interfaces.mcp.server as m

    out = m.brief_memory(budget_tokens=1500)
    serialized = repr(out)
    assert "safe gateway signal" in serialized
    assert "private gateway signal" not in serialized
    assert "review gateway signal" not in serialized
    assert "superseded gateway signal" not in serialized
    assert out["total_shown"] == 1
    assert out["total_withheld"] == 3


def test_brief_memory_noninjectable_query_returns_no_items(tmp_brain):
    store = ItemsStore(tmp_brain / "items")
    now = datetime.now(timezone.utc).astimezone()
    value = MemoryItem(
        id=make_item_id("weak-brief", when=now),
        type=MemoryType.episode,
        created_at=now,
        title="memory",
        summary="memory",
        confidence=0.9,
    )
    store.write(value, "memory")

    import agent_brain.interfaces.mcp.server as m

    out = m.brief_memory(budget_tokens=1500, query="memory")
    assert out["total_shown"] == 0
    assert out["total_withheld"] == 1
```

把 `tests/unit/test_sdk_client.py::TestMemoryClientBrief.test_brief` 收紧为非空合同：

```python
    def test_brief(self, client):
        item_id = client.write(
            type="decision",
            title="Brief gateway test",
            summary="Testing gated brief",
            refs={"urls": ["https://example.test/brief"]},
        )

        payload = client.brief()

        assert payload["total_shown"] == 1
        assert payload["total_withheld"] == 0
        assert payload["tiers"][2]["items"] == [
            {"id": item_id, "title": "Brief gateway test"},
        ]
```

- [x] **Step 2: 运行 brief tests 并确认 RED**

```bash
PYTHONPATH=. .venv/bin/python \
  -m pytest -q tests/unit/test_brief.py tests/unit/test_brief_mcp.py \
  tests/unit/test_sdk_client.py::TestMemoryClientBrief
```

Expected: FAIL；当前 brief 会显示禁止项。

- [x] **Step 3: 重构 `build_brief`**

新增 imports：

```python
from collections import Counter
from agent_brain.memory.context.context_firewall_types import ContextCandidate
from agent_brain.memory.context.injection_gateway import evaluate_injection_candidates
```

以以下 helpers 取代原 `_candidates`：

```python
def _brief_rows(store, *, project):
    now = datetime.now(timezone.utc).astimezone()
    windows = {type_: days for _name, type_, days in _TIERS}
    rows = []
    for item, body in store.iter_all():
        item_type = str(item.type)
        if item_type not in windows:
            continue
        if project is not None and item.project != project:
            continue
        if _NOISE_TAGS & set(item.tags):
            continue
        days = windows[item_type]
        if days is not None and (now - item.created_at).days > days:
            continue
        rows.append((item, body))
    return rows


def _sort_items(items, *, type_, query):
    def score(item):
        haystack = f"{item.title} {item.summary} {' '.join(item.tags)}".lower()
        hits = sum(1 for word in (query or "").lower().split() if word in haystack)
        confidence = item.confidence if type_ == "decision" else 0.0
        return (hits, confidence, item.created_at.timestamp())
    return sorted(items, key=score, reverse=True)
```

用以下完整实现替换 `build_brief`：

```python
def build_brief(
    store: ItemsStore,
    *,
    project: str | None = None,
    budget_tokens: int = 1500,
    query: str | None = None,
) -> Brief:
    rows = _brief_rows(store, project=project)
    firewall = evaluate_injection_candidates(
        [
            ContextCandidate(
                item=item,
                body=body,
                score=item.confidence,
                source="brief",
            )
            for item, body in rows
        ],
        query=query,
    )
    included_by_type: dict[str, list] = {}
    for decision in firewall.included:
        item = decision.candidate.item
        included_by_type.setdefault(str(item.type), []).append(item)
    rejected_by_type = Counter(
        str(decision.candidate.item.type) for decision in firewall.excluded
    )

    budget_chars = max(200, budget_tokens) * _CHARS_PER_TOKEN
    used = 0
    tiers: list[BriefTier] = []
    for name, type_, _since_days in _TIERS:
        tier = BriefTier(name=name, withheld=rejected_by_type[type_])
        for item in _sort_items(
            included_by_type.get(type_, []),
            type_=type_,
            query=query,
        ):
            brief_item = BriefItem(
                type=str(item.type),
                title=item.title,
                id=item.id,
                summary=item.summary or "",
            )
            line_cost = len(brief_item.render()) + 1
            if used + line_cost <= budget_chars:
                tier.shown.append(brief_item)
                used += line_cost
            else:
                tier.withheld += 1
        tiers.append(tier)
    footer = (
        "Read full bodies sparingly: `memory read --full <id>` only for the "
        "1–3 items you actually need."
    )
    return Brief(tiers=tiers, budget_tokens=budget_tokens, footer=footer)
```

Gateway 会对 `decision` 执行现有 source gate；为保留 `tests/unit/test_brief.py`
所验证的 eligible decision 语义，将 `_seed` 中的 `MemoryItem(...)` 增加：

```python
        refs={"urls": [f"https://example.test/{title}"]} if type_ == "decision" else {},
```

这是测试数据的证据边界修正，不是在 brief 中关闭 source gate。

- [x] **Step 4: 验证 MCP/SDK brief GREEN**

```bash
PYTHONPATH=. .venv/bin/python \
  -m pytest -q tests/unit/test_brief.py tests/unit/test_brief_mcp.py \
  tests/unit/test_sdk_client.py::TestMemoryClientBrief
```

Expected: PASS。

- [x] **Step 5: 提交 brief 迁移**

```bash
git add agent_brain/memory/recall/brief.py tests/unit/test_brief.py \
  tests/unit/test_brief_mcp.py tests/unit/test_sdk_client.py
git commit -m "fix: gate resume brief candidates"
```

---

### Task 4: 收紧 SDK 默认值并区分 safe pack 与 raw diagnostics

**Files:**
- Modify: `tests/unit/test_sdk_client.py:140-187`
- Modify: `agent_brain/interfaces/sdk/query.py:25-127`
- Modify: `agent_brain/interfaces/sdk/sdk.py:143-183`

- [x] **Step 1: 写 SDK 默认安全与显式 raw RED 测试**

在 `tests/unit/test_sdk_client.py` 添加：

```python
def test_sdk_search_firewall_default_is_secure():
    import inspect
    assert inspect.signature(MemoryClient.search).parameters["context_firewall"].default is True


class TestMemoryClientSearch:
    def test_search_defaults_to_firewall_and_hides_secret(self, client):
        client.write(
            type="episode",
            title="SDK secret injection gateway",
            summary="SDK secret boundary",
            body="sdk secret body",
            sensitivity="secret",
        )
        assert client.search("SDK secret injection gateway") == []

    def test_explicit_raw_search_keeps_diagnostics_but_has_no_context_pack(self, client):
        client.write(
            type="episode",
            title="SDK raw injection gateway",
            summary="SDK raw boundary",
            body="sdk raw body",
            sensitivity="secret",
        )
        results = client.search("SDK raw injection gateway", context_firewall=False)
        assert len(results) == 1
        assert results[0].snippet == "sdk raw body"
        assert results[0].context_pack is None
        assert results[0].firewall is None

    def test_gateway_failure_never_falls_back_to_raw(self, client, monkeypatch):
        client.write(
            type="episode",
            title="SDK gateway failure boundary",
            summary="SDK gateway failure boundary",
        )
        import agent_brain.memory.context.injection_gateway as gateway_module

        def fail_closed(*_args, **_kwargs):
            raise RuntimeError("synthetic gateway failure")

        monkeypatch.setattr(gateway_module, "build_injection_context", fail_closed)
        with pytest.raises(RuntimeError, match="synthetic gateway failure"):
            client.search("SDK gateway failure boundary")
```

把两个方法放入现有 `TestMemoryClientSearch`，不要创建第二个同名 class。
同时收紧两个旧测试，避免默认 firewall-on 后把“空结果”误当成通过：

```python
    def test_search_finds_written_items(self, client):
        client.write(
            type="decision",
            title="SSE over WebSocket",
            summary="Chose SSE for push",
            tags=["api"],
            refs={"urls": ["https://example.test/sse"]},
        )
        client.write(
            type="fact",
            title="Redis cache TTL",
            summary="TTL set to 300s",
            tags=["cache"],
            refs={"urls": ["https://example.test/redis"]},
        )

        results = client.search("SSE WebSocket push")
        assert results
        assert any("SSE" in result.title for result in results)

    def test_search_returns_search_result_objects(self, client):
        client.write(
            type="episode",
            title="SDK search result object",
            summary="SDK search result object contract",
        )

        results = client.search("SDK search result object")
        assert results
        assert all(isinstance(result, SearchResult) for result in results)
        assert results[0].score > 0
```

- [x] **Step 2: 运行 SDK tests 并确认 RED**

```bash
PYTHONPATH=. .venv/bin/python \
  -m pytest -q tests/unit/test_sdk_client.py
```

Expected: FAIL；默认仍为 raw，secret 可见，raw 仍生成 pack。

- [x] **Step 3: 将 SDK firewall path 改为 Gateway**

在 `search_items` 函数体的现有延迟导入位置使用：

```python
from agent_brain.memory.context.context_firewall_types import ContextCandidate
from agent_brain.memory.context.injection_gateway import (
    _record_injection_diagnostic,
    build_injection_context,
)
```

将 firewall 分支改为：

```python
    packed_by_id = {}
    firewall_by_id = {}
    if context_firewall:
        _record_injection_diagnostic(
            surface="sdk-search",
            reason="hydrate_error",
            count=sum(1 for hit in hits if hit.id not in items_by_id),
        )
        hit_by_id = {hit.id: hit for hit in hits}
        injection = build_injection_context(
            [
                ContextCandidate(
                    item=items_by_id[hit.id][0],
                    body=items_by_id[hit.id][1],
                    score=hit.score,
                    source="sdk-search",
                )
                for hit in hits
                if hit.id in items_by_id
            ],
            query=query,
            requested=_parse_verbosity(verbosity),
            max_items=top_k,
        )
        packed_by_id = {
            entry.decision.candidate.item.id: entry.pack for entry in injection.included
        }
        firewall_by_id = {
            entry.decision.candidate.item.id: entry.decision for entry in injection.included
        }
        hits = [hit_by_id[item_id] for item_id in packed_by_id if item_id in hit_by_id]
    else:
        hits = hits[:top_k]
```

结果构造中使用：

```python
        pack = packed_by_id.get(hit.id)
        firewall_decision = firewall_by_id.get(hit.id)
        results.append(SearchResult(
            id=hit.id,
            title=item.title,
            summary=item.summary,
            score=hit.score,
            type=str(item.type),
            confidence=item.confidence,
            snippet=pack.text if pack is not None else body[:200],
            context_pack=pack.to_dict() if pack is not None else None,
            retrieval_trace=hit.trace.to_dict() if getattr(hit, "trace", None) else None,
            firewall=_firewall_to_dict(firewall_decision) if firewall_decision else None,
            resource_context=_resource_context_for_item(
                brain_dir, item, include_resources=include_resources,
            ),
        ))
```

把 `query.py::search_items` 和 `sdk.py::MemoryClient.search` 的 `context_firewall` 默认值都改为 `True`。Docstring 明确：传 `False` 只用于显式 raw diagnostics，且 raw 没有 `context_pack`。

- [x] **Step 4: 验证 SDK GREEN**

```bash
PYTHONPATH=. .venv/bin/python \
  -m pytest -q tests/unit/test_sdk_client.py tests/unit/test_mcp_injection_gateway.py \
  tests/unit/test_brief_mcp.py
```

Expected: PASS。

- [x] **Step 5: 提交 SDK 安全默认**

```bash
git add agent_brain/interfaces/sdk/query.py agent_brain/interfaces/sdk/sdk.py tests/unit/test_sdk_client.py
git commit -m "fix: secure SDK search context by default"
```

---

### Task 5: 让 CLI/Hook prompt path 复用 Gateway 并禁止直接 pack

**Files:**
- Create: `tests/unit/test_prompt_injection_gateway_contract.py`
- Create: `tests/unit/test_prompt_surface_injection_parity.py`
- Modify: `agent_brain/interfaces/cli/commands/query.py:9-13,167-284,396-435`
- Modify: `tests/unit/test_cli_smoke.py`
- Verify: `agent_runtime_kit/hooks/inject-context.sh`

- [x] **Step 1: 写静态旁路契约与 CLI RED 测试**

创建 `tests/unit/test_prompt_injection_gateway_contract.py`：

```python
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROMPT_SURFACES = (
    "agent_brain/interfaces/mcp/tools/search_tools.py",
    "agent_brain/interfaces/sdk/query.py",
    "agent_brain/interfaces/cli/commands/query.py",
    "agent_brain/memory/recall/brief.py",
)


FORBIDDEN_FROM_IMPORTS = {
    "agent_brain.memory.context.context_packing": {
        "build_context_pack",
        "pack_decisions",
    },
    "agent_brain.memory.context.context_firewall": {"ContextFirewall"},
}


def test_prompt_surfaces_do_not_import_policy_or_pack_bypasses():
    for relative in PROMPT_SURFACES:
        source = (ROOT / relative).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative)
        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in FORBIDDEN_FROM_IMPORTS:
                forbidden = FORBIDDEN_FROM_IMPORTS[node.module]
                violations.extend(
                    alias.name for alias in node.names if alias.name in forbidden
                )
            if isinstance(node, ast.Import):
                violations.extend(
                    alias.name
                    for alias in node.names
                    if alias.name in FORBIDDEN_FROM_IMPORTS
                )
        assert violations == [], f"{relative}: {violations}"


def test_prompt_surfaces_reference_injection_gateway():
    for relative in PROMPT_SURFACES:
        source = (ROOT / relative).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative)
        assert any(
            isinstance(node, ast.ImportFrom)
            and node.module == "agent_brain.memory.context.injection_gateway"
            for node in ast.walk(tree)
        ), relative


def test_hook_selects_the_gateway_backed_cli_mode():
    source = (ROOT / "agent_runtime_kit/hooks/inject-context.sh").read_text(encoding="utf-8")
    assert '"--context-firewall"' in source
```

在 `test_cli_smoke.py` 添加：

```python
def test_cli_gateway_noninjectable_query_never_falls_back_to_raw_hits(tmp_brain):
    store = ItemsStore(tmp_brain / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260711-020000-cli-weak-gateway",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="memory",
        summary="memory",
        confidence=0.9,
    )
    store.write(item, "memory")
    idx.upsert(item, "memory", embedding=embedder.embed("memory"))
    idx.close()

    result = runner.invoke(app, [
        "search", "memory", "--format", "text", "--context-firewall",
    ])
    assert result.exit_code == 0
    assert "no matches" in result.output
    assert item.id not in result.output


def test_cli_gateway_failure_never_falls_back_to_raw_hits(
    tmp_brain,
    monkeypatch,
):
    store = ItemsStore(tmp_brain / "items")
    embedder = HashingEmbedder()
    idx = HubIndex(tmp_brain / "index.db", embedding_dim=embedder.dim)
    item = MemoryItem(
        id="mem-20260711-020001-cli-gateway-failure",
        type=MemoryType.episode,
        created_at=datetime.now(timezone.utc),
        title="CLI gateway failure boundary",
        summary="CLI gateway failure boundary",
        confidence=0.9,
    )
    store.write(item, "CLI gateway failure boundary")
    idx.upsert(
        item,
        "CLI gateway failure boundary",
        embedding=embedder.embed("CLI gateway failure boundary"),
    )
    idx.close()

    from agent_brain.interfaces.cli.commands import query as query_module

    def fail_closed(*_args, **_kwargs):
        raise RuntimeError("synthetic gateway failure")

    monkeypatch.setattr(query_module, "build_injection_context", fail_closed)
    result = runner.invoke(app, [
        "search",
        "CLI gateway failure boundary",
        "--format",
        "text",
        "--context-firewall",
    ])
    assert result.exit_code != 0
    assert item.id not in result.output
```

创建 `tests/unit/test_prompt_surface_injection_parity.py`，对同一 store 强制比对
MCP、SDK、CLI search 的 include ID，并验证三个 brief surface 共用同一
eligibility 结果：

```python
from datetime import datetime, timezone
import re

import pytest
from typer.testing import CliRunner

from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Sensitivity
from agent_brain.interfaces.cli import app
from agent_brain.interfaces.sdk import MemoryClient
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex


runner = CliRunner()


@pytest.fixture(autouse=True)
def close_mcp_components():
    from agent_brain.interfaces.mcp.tools._shared import _components_cache

    for _store, index, _retriever in _components_cache.values():
        index.close()
    _components_cache.clear()
    yield
    for _store, index, _retriever in _components_cache.values():
        index.close()
    _components_cache.clear()


def memory(
    suffix,
    *,
    sensitivity=Sensitivity.internal,
    tags=None,
    superseded_by=None,
):
    return MemoryItem(
        id=f"mem-20260711-030000-surface-parity-{suffix}",
        type=MemoryType.episode,
        created_at=datetime(2026, 7, 11, 3, 0, tzinfo=timezone.utc),
        title=f"Surface parity gateway boundary {suffix}",
        summary=f"Surface parity gateway boundary {suffix}",
        sensitivity=sensitivity,
        tags=tags or [],
        superseded_by=superseded_by,
        confidence=0.9,
    )


def seed(brain, items):
    store = ItemsStore(brain / "items")
    embedder = HashingEmbedder()
    index = HubIndex(brain / "index.db", embedding_dim=embedder.dim)
    for item in items:
        body = f"Surface parity gateway boundary body {item.title}"
        store.write(item, body)
        index.upsert(item, body, embedding=embedder.embed(body))
    index.close()


def fixtures():
    safe = memory("safe")
    return safe, [
        safe,
        memory("private", sensitivity=Sensitivity.private),
        memory("secret", sensitivity=Sensitivity.secret),
        memory("review", tags=["needs-review"]),
        memory("superseded", superseded_by=safe.id),
    ]


def test_mcp_sdk_cli_search_return_same_eligible_ids(tmp_brain):
    safe, items = fixtures()
    seed(tmp_brain, items)
    query = "surface parity gateway boundary"

    import agent_brain.interfaces.mcp.server as mcp

    mcp_ids = {row["id"] for row in mcp.search_memory(query, top_k=10)}
    client = MemoryClient(brain_dir=tmp_brain)
    try:
        sdk_ids = {row.id for row in client.search(query, top_k=10)}
    finally:
        client._components.get_index().close()
    cli = runner.invoke(app, [
        "search", query, "--top-k", "10", "--format", "text", "--context-firewall",
    ])
    assert cli.exit_code == 0, cli.output
    cli_ids = set(re.findall(r"\(id:(mem-[^\s)]+)", cli.output))

    assert mcp_ids == sdk_ids == cli_ids == {safe.id}


def test_mcp_sdk_cli_brief_share_eligible_items(tmp_brain):
    safe, items = fixtures()
    seed(tmp_brain, items)

    import agent_brain.interfaces.mcp.server as mcp

    mcp_payload = mcp.brief_memory(budget_tokens=1500)
    mcp_ids = {
        row["id"]
        for tier in mcp_payload["tiers"]
        for row in tier["items"]
    }
    client = MemoryClient(brain_dir=tmp_brain)
    sdk_payload = client.brief(budget_tokens=1500)
    sdk_ids = {
        row["id"]
        for tier in sdk_payload["tiers"]
        for row in tier["items"]
    }
    cli = runner.invoke(app, ["brief", "--budget-tokens", "1500"])
    assert cli.exit_code == 0, cli.output

    assert mcp_ids == sdk_ids == {safe.id}
    assert safe.title in cli.output
    for forbidden in items[1:]:
        assert forbidden.title not in cli.output
```

- [x] **Step 2: 运行测试并确认 RED**

```bash
PYTHONPATH=. .venv/bin/python \
  -m pytest -q tests/unit/test_prompt_injection_gateway_contract.py \
  tests/unit/test_prompt_surface_injection_parity.py \
  tests/unit/test_cli_smoke.py::test_cli_gateway_noninjectable_query_never_falls_back_to_raw_hits \
  tests/unit/test_cli_smoke.py::test_cli_gateway_failure_never_falls_back_to_raw_hits
```

Expected: 静态契约 FAIL，因为 CLI 仍直接 filter/pack；parity 测试在
Tasks 2–4 后可以已经 PASS，它用于锁定迁移前后的跨 surface 行为契约。

- [x] **Step 3: 将 CLI firewall 分支改为一次 Gateway 调用**

Imports 改为：

```python
from agent_brain.memory.context.context_loading import render_context_view, select_context_view
from agent_brain.memory.context.context_firewall_types import ContextCandidate
from agent_brain.memory.context.context_packing import ContextPack
from agent_brain.memory.context.injection_gateway import (
    InjectionResult,
    _record_injection_diagnostic,
    build_injection_context,
)
```

用 `build_injection_context` 取代 `ContextFirewall().filter`：

```python
    firewall_decisions_by_id = {}
    context_packs_by_id: dict[str, ContextPack] = {}
    injection_result: InjectionResult | None = None
    if context_firewall:
        hit_by_id = {hit.id: hit for hit in hits}
        _record_injection_diagnostic(
            surface="cli-search",
            reason="hydrate_error",
            count=sum(1 for hit in hits if hit.id not in items_by_id),
        )
        candidates = [
            ContextCandidate(
                item=items_by_id[hit.id][0],
                body=items_by_id[hit.id][1],
                score=_context_firewall_candidate_score(
                    hit.score, items_by_id[hit.id][0], type_order,
                ),
                source="cli-search",
            )
            for hit in hits if hit.id in items_by_id
        ]
        current_scope: dict[str, str] = {}
        if cwd:
            current_scope["cwd"] = cwd
        if adapter != "unknown":
            current_scope["adapter"] = adapter
        injection_result = build_injection_context(
            candidates,
            query=answerability_query,
            query_signal=query_signal,
            requested=verbosity,
            max_items=top_k,
            current_scope=current_scope or None,
        )
        included = [entry.decision for entry in injection_result.included]
        included_ids = [decision.candidate.item.id for decision in included]
        firewall_decisions_by_id = {
            decision.candidate.item.id: decision for decision in included
        }
        context_packs_by_id = {
            entry.decision.candidate.item.id: entry.pack for entry in injection_result.included
        }
        hits = [
            hit_by_id[decision.candidate.item.id]
            for decision in included
            if decision.candidate.item.id in hit_by_id
        ]
        if not hits:
            if record_recall_gap:
                _record_search_gap(
                    query=query,
                    reason="all_candidates_rejected",
                    rejected_ids=[
                        decision.candidate.item.id
                        for decision in injection_result.excluded
                    ],
                    evidence=(
                        _prompt_frame_evidence(prompt_frame)
                        + [
                            f"{decision.candidate.item.id}:{','.join(decision.reasons)}"
                            for decision in injection_result.excluded
                        ]
                    ),
                    adapter=adapter,
                    session=session,
                    cwd=cwd,
                )
            typer.echo("no matches")
            return
        if record_recall_gap:
            rejected = _significant_rejected_decisions(injection_result.excluded)
            if rejected:
                _record_search_gap(
                    query=query,
                    reason="partial_candidates_rejected",
                    injected_ids=included_ids,
                    rejected_ids=[
                        decision.candidate.item.id for decision in rejected
                    ],
                    evidence=(
                        _prompt_frame_evidence(prompt_frame)
                        + [
                            f"{decision.candidate.item.id}:{','.join(decision.reasons)}"
                            for decision in rejected
                        ]
                    ),
                    adapter=adapter,
                    session=session,
                    cwd=cwd,
                )
```

删除旧的 `firewall_result` all/partial reject 分支。将 cohort metrics 中的
`pack_decisions(firewall_result.included, ...)` 替换为：

```python
if injection_result is not None:
    pack_metrics.update(injection_result.metrics())
```

给 `_render_text_hit` 增加 `context_pack: ContextPack | None = None`；audit 分支必须使用调用方传入的 pack：

```python
if include_audit_metadata:
    if context_pack is None:
        raise RuntimeError("gateway context pack required for prompt output")
    lines.append(
        "  "
        f"view={context_pack.selected_view} "
        f"packed={context_pack.packed_tokens}/{context_pack.full_tokens}t "
        f"retrieve=\"{context_pack.cli_retrieve_hint}\""
    )
    text = context_pack.text
```

调用处传 `context_pack=context_packs_by_id.get(hit.id)`。

- [x] **Step 4: 验证 CLI/Hook 契约 GREEN**

```bash
PYTHONPATH=. .venv/bin/python \
  -m pytest -q tests/unit/test_prompt_injection_gateway_contract.py \
  tests/unit/test_prompt_surface_injection_parity.py \
  tests/unit/test_cli_smoke.py tests/unit/test_context_loading_views.py \
  tests/unit/test_write_shim_fallback.py
```

Expected: PASS；既有 overfetch、prefer-type、scope、retrieve-hint 与 Hook flag 合同保持。

- [x] **Step 5: 提交 CLI/Hook 统一**

```bash
git add agent_brain/interfaces/cli/commands/query.py tests/unit/test_cli_smoke.py \
  tests/unit/test_prompt_injection_gateway_contract.py \
  tests/unit/test_prompt_surface_injection_parity.py
git commit -m "refactor: route prompt CLI through gateway"
```

---

### Task 6: 增加 doctor/lineage 可观测性并同步架构事实源

**Files:**
- Modify: `tests/unit/test_doctor_offline.py`
- Modify: `agent_brain/platform/doctor.py:127-196`
- Modify: `agent_brain/interfaces/cli/doctor_offline.py:27-40`
- Modify: `tests/unit/test_memory_lineage.py`
- Modify: `agent_brain/product/memory_lineage.py:290-330`
- Modify: `docs/architecture.md:130-160`

- [x] **Step 1: 写 doctor 与 lineage RED 合同**

在 `test_doctor_offline.py` 添加：

```python
def test_doctor_reports_injection_gateway_available(tmp_brain):
    rep = run_doctor(offline=True)
    assert rep.checks["security.injection_gateway.available"] is True
```

在 `test_memory_lineage.py` 新增独立合同测试（该文件当前没有
`DataFlowEvent` fixture，因此显式构造最小 event）：

```python
def test_memory_lineage_names_the_mandatory_injection_gateway():
    from agent_brain.observability.data_flow import DataFlowEvent
    from agent_brain.product.memory_lineage import _method_for_data_flow

    event = DataFlowEvent(
        event_id="injection-gateway-contract",
        timestamp="2026-07-11T03:00:00+00:00",
        source="injection",
        stage="上下文注入",
        summary="inject one safe item",
    )

    assert _method_for_data_flow(event) == (
        "Retriever.search -> InjectionGateway -> ContextFirewall -> ContextPack"
    )
```

- [x] **Step 2: 运行 tests 并确认 RED**

```bash
PYTHONPATH=. .venv/bin/python \
  -m pytest -q tests/unit/test_doctor_offline.py tests/unit/test_memory_lineage.py
```

Expected: doctor 稳定键不存在；lineage 仍返回旧链路。

- [x] **Step 3: 增加 Gateway availability probe**

在 `platform/doctor.py` 添加：

```python
def _probe_injection_gateway_available() -> bool:
    try:
        from agent_brain.memory.context.injection_gateway import (
            build_injection_context,
            evaluate_injection_candidates,
        )
    except Exception:
        return False
    return callable(build_injection_context) and callable(evaluate_injection_candidates)
```

在 `run_doctor` grade 前加入：

```python
rep.checks["security.injection_gateway.available"] = _probe_injection_gateway_available()
```

在 degraded 条件中加入：

```python
or not rep.checks["security.injection_gateway.available"]
```

在 `doctor_offline.py` 的 rows 中加入：

```python
gateway_available = rep.checks["security.injection_gateway.available"]
rows.append((
    "prompt injection gateway",
    "ContextFirewall + ContextPack mandatory boundary",
    "available" if gateway_available else "degraded -> gateway unavailable",
))
```

- [x] **Step 4: 更新 lineage 与架构文档**

将 `_method_for_data_flow` 的 injection 返回值改为：

```python
return "Retriever.search -> InjectionGateway -> ContextFirewall -> ContextPack"
```

将 injection steps 中的注入段替换为：

```python
"候选统一进入 InjectionGateway；任何 prompt-facing surface 都不得直接打包 raw hit。",
"InjectionGateway 先调用 ContextFirewall 做主题、时间、敏感度、审核、废止、证据和 scope 门禁。",
"通过项再由 ContextPack 按预算选择 locator/overview/detail，正文按 detail_uri 延迟读取。",
```

在 `docs/architecture.md` 的 retrieval/injection 事实源中写入：

```text
Retriever raw hits -> InjectionGateway -> ContextFirewall -> ContextPack -> prompt surface
```

并注明 raw CLI diagnostics 不产生注入授权。

- [x] **Step 5: 验证并提交 observability/docs**

```bash
PYTHONPATH=. .venv/bin/python \
  -m pytest -q tests/unit/test_doctor_offline.py tests/unit/test_memory_lineage.py \
  tests/unit/test_docs_truth_contract.py
```

Expected: PASS。

```bash
git add agent_brain/platform/doctor.py agent_brain/interfaces/cli/doctor_offline.py \
  agent_brain/product/memory_lineage.py docs/architecture.md \
  tests/unit/test_doctor_offline.py tests/unit/test_memory_lineage.py
git commit -m "docs: expose injection gateway boundary"
```

---

### Task 7: 完成 P0-A 集成验证与安全旁路扫描

**Files:**
- Verify: Tasks 1-6 的全部修改文件

- [x] **Step 1: 运行精确安全矩阵**

```bash
PYTHONPATH=. .venv/bin/python \
  -m pytest -q \
  tests/unit/test_injection_gateway.py \
  tests/unit/test_mcp_injection_gateway.py \
  tests/unit/test_prompt_injection_gateway_contract.py \
  tests/unit/test_prompt_surface_injection_parity.py \
  tests/unit/test_brief.py \
  tests/unit/test_brief_mcp.py \
  tests/unit/test_sdk_client.py \
  tests/unit/test_cli_smoke.py \
  tests/unit/test_context_firewall.py \
  tests/unit/test_context_loading_views.py \
  tests/unit/test_write_shim_fallback.py \
  tests/unit/test_doctor_offline.py \
  tests/unit/test_memory_lineage.py
```

Expected: 0 failed，0 errors。

- [x] **Step 2: 扫描 prompt surface 旁路**

```bash
rg -n "build_context_pack\(|pack_decisions\(|ContextFirewall\(\)\.filter\(" \
  agent_brain/interfaces/mcp/tools/search_tools.py \
  agent_brain/interfaces/sdk/query.py \
  agent_brain/interfaces/cli/commands/query.py \
  agent_brain/memory/recall/brief.py \
  web/api/routes/item_search.py
```

Expected: 无输出；退出码 1 代表没有命中。

- [x] **Step 3: 运行 Ruff 与 diff hygiene**

```bash
.venv/bin/python -m ruff check \
  agent_brain/memory/context/injection_gateway.py \
  agent_brain/interfaces/mcp/tools/search_tools.py \
  agent_brain/memory/recall/brief.py \
  agent_brain/interfaces/sdk/query.py \
  agent_brain/interfaces/sdk/sdk.py \
  agent_brain/interfaces/cli/commands/query.py \
  agent_brain/platform/doctor.py \
  agent_brain/product/memory_lineage.py \
  tests/unit/test_injection_gateway.py \
  tests/unit/test_mcp_injection_gateway.py \
  tests/unit/test_prompt_injection_gateway_contract.py \
  tests/unit/test_prompt_surface_injection_parity.py
git diff --check
git status --short --branch
```

Expected: Ruff `All checks passed!`；diff check 无输出。

- [x] **Step 4: 运行仓库级回归并记录既有基线**

```bash
PYTHONPATH=. .venv/bin/python -m pytest -x -q
```

Expected baseline on `main@56b1287`: 可能仍先停在已登记的 public-hygiene/readiness 误报。若最早失败属于 Gateway、MCP、SDK、CLI、brief、doctor 或 lineage，本计划不得完成。不要把既有 public-hygiene 或 Python 3.14 FD 问题改写成 P0-A 已通过。

- [x] **Step 5: 核对提交序列与隔离边界**

```bash
git log --oneline --decorate main..HEAD
git status --short --branch
```

Expected: 设计提交加 Tasks 1-6 的小提交；产品修改只存在于 `codex/p0-injection-gateway`。

---

## 最终实施与对抗审计记录（2026-07-11）

### 实际范围扩展

初稿只列 MCP、SDK、CLI/Hook 与 brief。最终对抗审查证明 Web
`/api/search` 也是 prompt-facing surface，且旧实现默认关闭 firewall、直接
`build_context_pack`。因此本次在不改变 P0-B/P0-C 边界的前提下，把 Web
search 纳入同一静态旁路合同，并继续收口审查过程中实证的 tenant 与
observability 泄漏；这些扩展均有独立 RED/GREEN 回归和小提交。

### 关键非显然修复

- raw overfetch 一律 `record_access=False`；只有 Gateway 最终 included hits
  精确 `record_accesses` 一次。显式 raw diagnostics 不生成 ContextPack。
- 统一 overfetch 为 `max(top_k * 4, top_k + 8)`，上限 50；hydrate、pack、
  max-items 和最终 cohort gate 都允许安全候选补位。
- metrics 只接受闭集排除原因和严格 aggregate partition；packing downgrade
  annotation 不再被误算成 exclusion。NaN、无穷值和超 JavaScript 安全范围
  retrieval score 在 packing 前 fail-close。
- Gateway 接收 `brain_dir`，MCP、SDK、CLI、brief 和 Web 对 metadata-backed
  中文 query 使用同一 query-signal 语义，显式 `query_signal` 仍优先且只分析一次。
- Web search 默认 Gateway-on；raw 模式仅 admin 可用且无 pack/resource
  sidecar。ResourceRecord 在排序前按 tenant 与 `public/internal` 过滤，item-ref
  读取再次校验。
- Web 全局诊断与内容面因缺少完整 tenant attribution 统一改为 admin-only；
  History、Graph/Link、Related Items 在读、检索和写入前执行 item visibility，
  hidden peer 不展示也不记 access。
- injection cohort 只被视为 observation。DataFlow、Chain、Lineage 共用有界
  frontmatter 授权索引，不再凭文件名 hydrate private/secret 元数据，也不读取 body。
- JSONL/JSON sidecar 增加单行/单文件、嵌套、数字和总读取预算；坏 UTF-8、
  极端时区、递归炸弹、FIFO、symlink、超大文件和非法 identity 都按单记录
  fail-close，后续合法记录继续。
- secure IO 使用 fd-relative `openat + O_NOFOLLOW + O_NONBLOCK + fstat`；
  item 目录深度上限 32，并使用进程内非阻塞单扫描 gate。并发 follower 返回
  空授权，不等待也不制造 FD 饥饿。
- Chain Log 不再猜测 `loaded_view=overview`；只有严格 per-item pack metrics
  能绑定 ID 时才展示具体 view。同步文件扫描路由交给 FastAPI threadpool。

关键 hardening commits：`ebf4fa5`、`e48e57e`、`b862e9b`、`c9bf8c8`、
`17e3c19`、`b02f6de`、`9cd91ca`、`3303be0`。

### 最终验证证据

- 合并安全矩阵：`661 passed, 1 skipped, 1 warning`。
- 独立最终安全复审矩阵：`421 passed, 1 warning`，原始与新增 P0-P2 finding
  均无法再复现，结论为 Scoped PASS。
- 发布前复审修复 SDK resource sidecar 的 tenant/sensitivity/raw 绕过、
  JSONL ledger 无总预算，以及 public-hygiene 误报门禁；对应定向回归为
  `327 passed, 1 skipped, 1 warning` 与 `15 passed, 1 warning`。
- 最终仓库级回归不再 deselect 任何测试：
  `2246 passed, 6 skipped, 1 warning`，耗时 `280.54s`。
- prompt surface 旁路扫描覆盖 MCP、SDK、CLI、brief 与 Web，无命中。
- `ruff check agent_brain web`、`compileall agent_brain web`、
  `git diff --check` 全部通过。
- 唯一 warning 是 Starlette/httpx 的既有 deprecation；skip 为两个 opt-in
  conformance、两个缺少可选 `reportlab`、两个真实 brain opt-in query-intent 测试。

### 首次实施时的既有基线（发布前复审已闭环）

`tests/conformance/test_public_hygiene.py::test_git_tracked_public_surface_has_no_sensitive_literals`
曾报告 4 条既有命中：

- `.github/workflows/sync-gitee.yml:46`：Gitee SSH user/host 字面量
- `docs/release-publishing.md:81`：同一 Gitee SSH user/host 字面量
- `tests/unit/test_web_admin_security.py:37`：RFC1918 测试地址字面量
- `tests/unit/test_web_admin_security.py:42`：同一 RFC1918 测试地址字面量

`tests/unit/test_governance_readiness.py::test_govern_readiness_json_reports_release_query_and_lifecycle_lanes`
消费同一 public-hygiene 结果，因此是派生失败。发布前复审通过上下文感知的
SCP-style SSH remote 识别，以及将测试 fixture 改为 RFC 5737 文档地址消除了误报；
真实邮箱与 RFC1918 地址仍由回归测试确认会被拦截。

同一轮复审还增加了 JSONL ledger 的 64 MiB / 20,000 物理行总预算、最终文件
no-follow/regular-file 校验和有界 tail 消费；SDK resource sidecar 在读取内容前
复用 Web 的 prompt resource policy，只允许 public/internal 且 tenant 可见的资源。
SDK `context_firewall=True` 默认值已在 `CHANGELOG.md` 标为 next-major breaking change，
raw diagnostics 明确不返回 ContextPack、firewall decision 或 resource sidecar。

### 已知安全取舍

- 不支持安全 dir-fd/no-follow 读取的平台，observability item hydration 返回空，
  不回退到 path-based 不安全扫描。
- 同一进程已有 observable-item scan 时，并发 follower 返回空授权；这是安全优先
  的降级，避免在低 `RLIMIT_NOFILE` 环境中阻塞或耗尽 FD。
- 缺少 tenant attribution 的全局诊断只对 admin 开放；本轮不伪造不完整的
  per-tenant 过滤语义。

---

## 计划自验映射

| 设计要求 | 实施任务 |
|---|---|
| 唯一 Gateway 与 query fail-close | Task 1 |
| MCP search 不泄露禁止项 | Task 2 |
| MCP/SDK/CLI brief 统一 eligibility | Task 3 |
| SDK 默认 firewall-on，raw 无 pack | Task 4 |
| CLI/Hook 复用 Gateway，禁止直接 pack；MCP/SDK/CLI 同集 | Task 5 |
| Doctor、lineage、架构事实源 | Task 6 |
| 安全矩阵、旁路扫描、Ruff、仓库回归 | Task 7 |
| Web Gateway、tenant visibility、secure observability hardening | 最终对抗审计扩展 |

本计划仍不包含 P0-B、P0-C、MCP principal、explicit `read_memory` 授权或
benchmark 修复。Web tenant 仅收口本次对抗审计中已经实证的跨租户读取、写入、
关系图、历史、资源 sidecar 与全局诊断旁路。
