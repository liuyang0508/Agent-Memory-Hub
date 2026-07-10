# P0-A 统一 Prompt Injection Gateway 设计

## 背景

Agent Memory Hub 已经具备 `Retriever`、`ContextFirewall`、`ContextPack` 和分层上下文加载，但当前 prompt-facing surface 没有统一强制边界：

- MCP `search_memory` 在 raw hit 上直接调用 `build_context_pack`。
- MCP `brief_memory` 在 `build_brief` 中直接选择 MemoryItem。
- SDK、CLI 与 Hook 各自决定是否启用 ContextFirewall。
- `ContextFirewall.filter()` 在只收到 query 且 query 不可注入时会把 `QuerySignal` 清空，导致 cohort fail-close 失效。

结果是 `private/secret`、`needs-review`、`superseded` 或弱查询候选可能从部分 surface 进入模型上下文。本设计对应审计项 `AMH-CORE-001`，同时收口 `AMH-CORE-006` 在 prompt-facing 路径上的表现。

## 目标

建立一个强制、可复用、fail-closed 的 Injection Gateway，使所有准备进入模型 prompt 的候选按同一顺序执行：

```text
Retriever raw hits
  -> hydrate MemoryItem/body
  -> InjectionGateway
  -> ContextFirewall
  -> ContextPack
  -> MCP / SDK / CLI / Hook prompt-facing output
```

## 非目标

- 不把 ContextFirewall 下沉到 Retriever；后台和人类 CLI 仍需要显式 raw retrieval 诊断能力。
- 不修改 `MemoryItem` schema，也不迁移用户数据。
- 不在本子项目中给 `read_memory(id)`、raw conversation、export/delete 等显式工具增加 principal/capability 授权；这些属于后续 P1 capability profile。
- 不处理 harvest audit/quarantine 和底层 store path containment；它们分别由 P0-B、P0-C 子项目完成。
- 不改变 BM25/vector/RRF/MMR/graph 的相关性算法。

## 方案比较与决策

### 方案 A：统一 Injection Gateway（采用）

保留 raw Retriever，在 context 层新增唯一 Gateway，集中执行 query analysis、ContextFirewall 和 ContextPack。它保持“召回”和“允许注入”两个概念分离，同时消除 MCP、SDK、CLI、Hook 的策略漂移。

### 方案 B：逐 surface 补 ContextFirewall（拒绝）

改动较少，但每个 surface 仍需自行完成 hydrate、query signal、budget 和 pack。下一次新增 prompt surface 时仍可能旁路。

### 方案 C：Retriever 永远返回已过滤结果（拒绝）

边界看似简单，但会让 Web/CLI 的 raw retrieval diagnostics 消失，并把相关性检索和安全授权耦合在一起。

## 组件设计

新增 `agent_brain/memory/context/injection_gateway.py`，公开两个函数和一个结果类型。

```python
@dataclass(frozen=True)
class InjectionResult:
    included: list[PackedDecision]
    excluded: list[FirewallDecision]
    cohort_reasons: tuple[str, ...]
    used_tokens: int
    full_tokens: int


def evaluate_injection_candidates(
    candidates: list[ContextCandidate],
    *,
    query: str | None = None,
    query_signal: QuerySignal | None = None,
    max_items: int | None = None,
    current_scope: Mapping[str, str] | None = None,
) -> FirewallResult:
    ...


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
    ...
```

设计约束：

1. `evaluate_injection_candidates` 在调用方没有显式传 `query_signal` 时只分析一次 query。
2. 不可注入的 `QuerySignal` 必须原样传给 ContextFirewall，使 cohort 得到 `query_not_injectable`；不得再转换成 `None`。
3. ContextFirewall 负责 sensitivity、review、supersession、source、validity/scope、feedback、冲突和 `max_items`。
4. `pack_decisions` 负责 verbosity 选择和最终 token budget；Gateway 不让 Firewall 重复计算 pack budget。
5. `InjectionResult.excluded` 合并 firewall exclusions 与 pack-budget exclusions。
6. Gateway 是 prompt-facing 代码唯一可以调用 `pack_decisions`/`build_context_pack` 的入口；底层 context 模块自己的实现和单元测试除外。

## Surface 迁移

### MCP `search_memory`

- raw retrieval 使用最多 `top_k * 3` 个候选，最终最多返回 `top_k` 个通过项。
- 候选上限仍受调用参数约束；oversampling 不改变最终响应数量。
- 只序列化 `InjectionResult.included`。
- 保留现有列表响应、`context_pack`、`selected_view`、`load_reason`、`locator/overview/body/snippet` 和 retrieval trace 字段。
- 全部被拒绝时返回空列表，不回退 raw hits。
- 正常响应不包含被拒绝项的 ID、标题、摘要、正文或逐项拒绝原因。

### MCP `brief_memory`

- 在 tier 选择和 token budget 之前执行 `evaluate_injection_candidates`。
- 每个 MemoryItem 以 confidence 作为候选基础 score；不改变 tier 的类型/时间排序规则。
- `query=None` 时执行 item-level gates，但不构造虚假 query；传 query 时同时执行 query/cohort gates。
- footer 可以报告聚合 withheld 数量，但不得披露被拒绝内容。

### SDK、CLI 与 Hook

- SDK `MemoryClient.search` 的 `context_firewall` 默认值由 `False` 改为 `True`，默认搜索成为安全的 prompt-context 搜索。
- 调用方仍可显式传 `context_firewall=False` 做 raw diagnostics；该模式保留 raw metadata/snippet，但 `context_pack=None`，避免把未经授权的 raw hit伪装成可注入包。
- SDK `MemoryClient.brief` 与 MCP/CLI brief 共用经过 Gateway eligibility 过滤的 `build_brief`，不存在独立旁路。
- CLI `--context-firewall` 改用 Gateway；默认人类 raw CLI search 保持现状。
- Hook 的自动注入链路改用 Gateway；不得自行复制 firewall/packing 顺序。
- 相同候选、query、scope、max_items 和 budget 在 MCP、SDK、CLI、Hook 上必须产生一致的 include/exclude 集合。

## 失败策略

- Gateway、策略初始化或 cohort-level ContextFirewall 异常：prompt-facing 调用失败或返回空结果，不允许 raw fallback。
- 单个候选 hydrate 失败：该候选视为不可注入，内部记为 `hydrate_error`。
- 单个候选 pack 失败：该候选视为不可注入，内部记为 `pack_error`；其他安全候选可以继续。
- malformed MemoryItem：沿用 ItemsStore scan diagnostics，并视为不可注入。
- 非 injectable query：正常返回空集合，不视作系统异常。
- 非法 verbosity 等调用参数：维持现有 `ValueError` / MCP tool error 合同。
- 本安全修复不提供关闭 Gateway 的 feature flag。

## 隐私与可观测性

Gateway telemetry 只能包含：

- raw candidate 数、included 数、excluded reason 聚合计数；
- 总耗时、packed/full token 数；
- surface 名和不含用户内容的配置维度。

不得记录 raw query、MemoryItem title/summary/body、secret/private 内容或被拒绝 item ID。`include_trace` 只对通过项返回 retrieval/firewall trace。Lineage/doctor 增加静态 truth-contract：prompt-facing surface 不得直接调用 `build_context_pack`。

## 兼容性

- MCP `search_memory` 继续返回 list，安全条目的字段形状不变。
- `brief_memory` 的 tier/summary 结构不变，只减少不允许注入的条目并增加 withheld 计数。
- SDK/CLI raw search 和 explicit `read_memory` 继续存在；SDK raw search 不再生成 `context_pack`，CLI raw search 的文本诊断输出保持现状。
- SDK `MemoryClient.search` 的默认值从 raw 改为 firewall-on；这是有意的安全默认收紧。依赖旧 raw 行为的调用方必须显式传 `context_firewall=False`。
- 不新增或修改持久化字段，不需要 reindex。
- 安全修复可能减少历史上错误出现的结果；这是有意的行为收紧，不提供旧行为兼容开关。

## 测试策略

严格按 TDD 实施，每个行为先看到测试因当前旁路而失败。

### Gateway 单元测试

- safe item 被 pack，pack shape 和 retrieve hint 保持完整。
- `private`、`secret`、`needs-review`、`unverified-boundary`、`superseded` 被排除。
- noninjectable query 产生空 included 和 `query_not_injectable`。
- `current_scope`、max_items、token budget 的结果与现有 firewall/packing 语义一致。
- 单项 pack failure 不泄露内容且不影响其他安全项。

### Surface 合同测试

- MCP search 对上述五类禁止项返回空或仅返回安全项。
- MCP brief tier 中不存在禁止项，withheld 计数准确。
- 同一 fixture 经过 MCP、SDK firewall 模式、CLI firewall 模式后 include ID 集一致。
- SDK 默认搜索执行 firewall；显式 raw 模式仍可诊断 raw hit，但 `context_pack` 必须为 `None`。
- MCP、SDK、CLI 的 brief 对同一 store 产生一致的 eligible ID 集。
- 被拒绝项的 title/summary/body 不出现在序列化响应中。
- 安全 fixture 的 `context_pack` 字段与现有合同一致。

### 回归验证

- `tests/unit/test_context_firewall.py`
- `tests/unit/test_context_loading_views.py`
- `tests/unit/test_brief_mcp.py`
- `tests/unit/test_sdk_client.py`
- 新增 Gateway/MCP security contract tests
- 相关 MCP、CLI、Hook targeted suites

## 验收标准

1. MCP `search_memory` 和 `brief_memory` 无法返回 private/secret、needs-review 或 superseded 内容。
2. 不可注入 query 在所有 prompt-facing surface 上 fail-close。
3. 安全条目的 MCP/SDK 公共字段合同不变。
4. prompt-facing 业务模块不再直接调用 `build_context_pack`。
5. Targeted tests 全绿，且每个新增回归测试有记录的 RED→GREEN 证据。
6. 无 schema migration、无 reindex、无 raw CLI 行为变化。

## 后续子项目边界

- P0-B：自动 harvest/pending/history-sync 禁止 `allow_unsafe`，统一 audit normalization 与 quarantine。
- P0-C：ItemsStore、ConversationStore、ResourceStore 的 canonical ID、resolved containment 与 symlink 防护。

这两个子项目单独形成设计与实施计划，不与 P0-A 代码混合提交。
