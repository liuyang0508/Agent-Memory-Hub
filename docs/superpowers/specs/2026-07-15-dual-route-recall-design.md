# 双通道召回与 Hook 治理设计

## 背景

Agent Memory Hub 当前的自动注入链路把“是否值得召回”“如何提取检索词”和“是否允许注入”
耦合在同一个 `QuerySignal` 上：

```text
原始 Prompt
  -> query_signal 提取关键词
  -> 关键词为空则 hook 直接退出
  -> 关键词字符串作为唯一 SEARCH_QUERY
  -> BM25 / vector RRF
  -> Context Firewall / answerability
  -> 注入
```

这会产生不可逆的 false negative：只要规则提词遗漏、误切或错误判断为弱查询，完整原始问题
就不会进入候选生成；后续 RRF、rerank、Context Firewall 和 answerability 再强，也无法恢复一个
从未进入候选集的正确条目。

主动召回还存在同类治理缺口。Agent 可能根据工作纪律自行生成：

```bash
memory brief --project smart-badge 2>/dev/null \
  || search-memory.sh "智能工牌 百分制 服务记录 绑定" --project smart-badge
```

这里的项目判断和四组查询词由 Agent 主观概括；`memory brief` 在零条目时仍返回退出码 0，
因此右侧 fallback 不会执行。`search-memory.sh` 又默认设置
`MEMORY_HUB_EMBEDDING_OFFLINE=1`，这条 fallback 在默认环境实际是 BM25-only。

本设计把候选生成改为高召回的多路过程，把 Injection Gateway、Context Firewall 和
answerability 保留为高精度的后置注入闸门。核心原则是：

> 候选生成追求高召回，进入 Prompt 追求高精度；关键词只能是一条证据通道，不能成为唯一入口。

## 依赖与分支边界

本设计依赖已完成的统一 Prompt Injection Gateway。Gateway 是进入模型 Prompt 前的安全治理
边界，不是常驻向量服务。

- 依赖分支：`codex/p0-injection-gateway`
- 设计分支：`codex/dual-route-recall`
- 当前 stacked base：`bb9128a`
- Gateway 合入后，设计分支 rebase 到最新 `main`，不复制 Gateway 实现。

另有 `codex/staged-agent-recall-governance` 负责限制自动召回只返回
`locator / overview` 并治理有界深读。该工作与本设计互补但独立：本设计解决“候选从哪里来”，
前者解决“候选以多深的上下文进入 Agent”。两者不混合提交。

## 目标

1. 完整规范化 Prompt 始终保留，不被关键词字符串覆盖。
2. 原始问题语义召回与关键词 BM25 召回独立生成候选，再统一融合。
3. 关键词为空或提取失败时，只关闭关键词通道，不终止整个召回。
4. 语义 Provider 不可用时，hooks 离线退化到原始 Prompt BM25 与关键词 BM25，且不阻塞输入。
5. Context Firewall、answerability 和所有后续排序阶段使用完整原始问题。
6. prompt-facing 候选继续强制经过 Injection Gateway；任何异常不得回退 raw hits。
7. hook 与 CLI 使用结构化结果，不再解析 `no matches`、首行文本或空字符串猜状态。
8. 修复 `brief || search` 假降级契约，并更新 Agent 工作纪律。
9. 现有 `Retriever.search(query)`、普通 CLI、SDK、MCP 和 raw diagnostics 保持兼容。

## 非目标

- 第一阶段不处理“继续、确认、是、1”等会话延续召回；它需要可靠的 session 状态，单独设计。
- 不新增跨平台常驻向量 daemon、Unix socket 或 Windows named pipe 服务。
- 不在 hook 提交路径下载、冷加载或自动安装 embedding 模型。
- 不用 LLM 充当在线查询规划器或强依赖外部模型服务。
- 不修改 `MemoryItem` schema，不强制迁移或 reindex 用户数据。
- 不改变 Gateway 的 sensitivity、review、supersession、scope、evidence 和预算安全规则。
- 不把本设计与 staged context depth、Qoder doctor 或安装健康度 hotfix 混成一个 PR。

## 方案比较与决策

### 方案 A：原始问题与关键词拼成一次查询

改动最小，但长句噪声会污染 BM25，重复关键词也会改变语义向量，且无法解释命中来自原始
语义还是规则词面。拒绝。

### 方案 B：双通道候选生成（采用）

完整原始问题只进入 `semantic_raw`，规则词只进入 `lexical_terms`，两路结果做 route-aware
RRF。语义通道不可用时，用 `lexical_raw_fallback` 保住原始问题信号。职责清晰、可独立降级、
可解释、可评测。

### 方案 C：LLM 查询规划器

让模型在线生成多条查询并选择通道，理论能力更强，但引入额外延迟、成本、非确定性和离线
失败面。第一阶段拒绝。

## 总体架构

```text
原始 Prompt
  -> Prompt normalization
  -> Recall Admission（只判断是否值得尝试）
  -> Candidate Router
       ├─ semantic_raw：完整问题 -> vector
       ├─ lexical_terms：规则词 -> BM25
       └─ lexical_raw_fallback：完整问题 -> BM25，仅语义不可用时启用
  -> route-aware RRF
  -> 现有候选排序流水线
  -> Injection Gateway
  -> Context Firewall / answerability
  -> ContextPack
  -> adapter prompt
```

三个职责必须保持分离：

1. `RecallAdmission` 决定是否值得支付召回成本。
2. Candidate Router 负责高召回候选生成，不决定哪些内容能进入 Prompt。
3. Injection Gateway 负责高精度授权和打包，不为候选不足做 raw fallback。

## RecallRequest 与结果模型

新增内部不可变请求类型：

```python
@dataclass(frozen=True)
class RecallRequest:
    raw_query: str
    normalized_query: str
    lexical_terms: tuple[str, ...]
    project_scope: ProjectScope | None
    cwd: str | None
    adapter: str
    session_id: str | None
```

原始问题必须作为显式字段沿调用链传递。不得再依赖
`AGENT_MEMORY_HUB_RAW_QUERY` 这类环境变量旁路来恢复真实用户意图。

```python
@dataclass(frozen=True)
class RouteTrace:
    route: str
    status: str
    latency_ms: float
    candidate_count: int
    reason: str | None


@dataclass(frozen=True)
class RouteEvidence:
    routes: tuple[str, ...]
    semantic_score: float | None
    semantic_rank: int | None
    lexical_terms_rank: int | None
    lexical_raw_rank: int | None


@dataclass(frozen=True)
class RoutedSearchResult:
    hits: list[RetrievedItem]
    routes: tuple[RouteTrace, ...]
    admission: RecallAdmission
    evidence_by_id: Mapping[str, RouteEvidence]
```

`RouteTrace.reason` 使用固定枚举，不承载用户文本、关键词正文或 MemoryItem 内容。

### Project scope 来源与强度

项目名也可能被 Agent 主观猜错。若把模型推断的 `smart-badge` 直接变成 SQL hard filter，正确的
跨项目或未标 project 记忆会在候选生成前被永久删除。因此 project scope 必须携带来源：

```python
@dataclass(frozen=True)
class ProjectScope:
    value: str
    source: Literal["explicit", "cwd", "agent_inferred"]
    hard_filter: bool
```

规则如下：

- 用户或调用方显式指定的 `--project` 可以作为 hard filter；
- hook 从 cwd/repo 确定性解析出的项目默认作为 scope/ranking 信号，不做候选硬过滤；
- Agent 根据自然语言推断的项目只能作为 soft hint，不能直接缩小 SQL allowed IDs；
- 无可靠来源时使用 `project_scope=None`，宁可让 Gateway 后置过滤，也不提前制造 false negative；
- `memory brief --project` 仍保留精确项目恢复语义，但 Agent-facing 指导要求只在用户明确项目或
  当前 cwd 已确定映射时传该参数。

project source、是否 hard filter 可以进入聚合 trace，但不记录原始 Prompt。

## Recall Admission

### 原则

Admission 采用“保守拒绝、默认尝试”，只拦截明确无召回价值的输入：

- 空输入或纯标点；
- adapter 控制命令；
- 明确弱确认，例如“是、OK、确认、继续、1”；
- 无法取得任何文本的多模态输入，继续记录 extraction gap。

除此之外默认允许尝试召回。必须满足：

```text
lexical_terms 为空 != 不允许召回
QuerySignal.injectable=False != 自动终止所有候选通道
```

`QuerySignal` 继续提供关键词、anchors、specificity 和诊断，但不再同时垄断 cohort
Admission 权限。

### 与 Injection Gateway 的一致性

Gateway 当前会对 noninjectable `QuerySignal` fail-close。如果 routed path 先允许召回，Gateway
随后又按旧的“关键词不足”结论拒绝整个 cohort，双通道改造将形同虚设。因此新增显式
`RecallAdmission` 集成点：

- routed path 必须把核心分析器生成的不可变 `RecallAdmission` 交给 Gateway；
- Gateway 的 cohort eligibility 以该 Admission 为准，不能重新运行 legacy term gate 得到冲突结论；
- `QuerySignal` 仍作为词面诊断和候选证据输入，不得覆盖 Admission；
- Admission 缺失、构造异常或状态不合法时，Gateway 继续 fail-closed；
- legacy search 未传 Admission 时，维持现有 QuerySignal 行为，保证兼容。

Gateway 的 item-level 安全规则不变。该集成是授权输入的显式化，不是绕过 Gateway。

### 与 answerability 的一致性

只改 cohort Admission 仍然不够。现有 deterministic answerability 会读取
`QuerySignal.injectable` 和 `QuerySignal.terms`：当 terms 为空时，它可能把已经由 raw route
正确召回的候选全部判为 `query_mismatch`；如果简单跳过 answerability，又会降低注入精度。

因此 routed path 使用显式的 `InjectionQueryContext`，把授权、词面信号和候选来源证据一起
交给 Gateway：

```python
@dataclass(frozen=True)
class InjectionQueryContext:
    raw_query: str
    admission: RecallAdmission
    query_signal: QuerySignal
    evidence_by_id: Mapping[str, RouteEvidence]
```

Gateway 和 Context Firewall 的 routed 行为如下：

1. cohort 是否可注入由 `admission` 决定；
2. Admission 已通过时必须执行 item-level answerability，不能再以
   `query_signal.injectable=False` 提前跳过或提前拒绝；
3. 有可用 strong terms 时，沿用既有 primary-anchor 规则，但其 eligibility 来自 Admission；
4. terms 为空或 legacy term gate 与 Admission 冲突时，不得自动放行，也不得自动全拒；
5. 此时逐候选执行 route-aware answerability：
   - `semantic_raw` 候选必须保留原始 vector similarity，并达到由标注集校准的最低阈值；
   - `lexical_raw_fallback` 候选必须覆盖完整 raw query 中至少一个非噪声词组，并达到最低
     raw-query coverage；
   - 只有 `lexical_terms` 且没有可验证 raw-route 证据时，维持现有 term answerability；
   - 没有任何合格证据时拒绝为 `route_answerability_insufficient`；
6. semantic verifier 若启用，只能进一步拒绝或解释，不得把 deterministic fail 改成通过；
7. legacy path 未提供 `InjectionQueryContext` 时，保持现有 answerability 行为。

所有目前以 `QuerySignal.injectable` 分支的 routed query-level 规则都必须审计，包括 cohort gate、
topic-recency gate 和 item answerability。routed path 的 eligibility 统一读取 Admission，词面覆盖仍
读取 QuerySignal；不得只修其中一个调用点。

route-aware answerability 使用完整 `raw_query` 和原始 route score/rank，不能用融合后的 RRF
分数冒充语义相似度。阈值不凭经验拍定，先通过标注集校准，再固化为带回归测试的内部配置。

## 候选通道与运行时策略

### `semantic_raw`

- 查询文本：完整 `normalized_query`；
- 索引：vector only；
- 不接受关键词替换或拼接；
- 只有 Semantic Provider 对当前 surface 已处于 ready 状态时启用。

“ready”不能仅凭 `sentence-transformers` 可 import 判断。对 hook 而言，它意味着不需要在
Prompt 提交路径下载或冷加载模型，并且 Provider 能在当前 deadline 内完成。

### `lexical_terms`

- 查询文本：`lexical_terms`；
- 索引：BM25 only；
- terms 为空时以 `skipped / lexical_terms_empty` 结束，不影响其他通道。

### `lexical_raw_fallback`

- 查询文本：完整 `normalized_query`；
- 索引：BM25 only；
- 只在 `semantic_raw` 不可用时启用；
- 目的不是模拟语义，而是确保规则提词不再是离线召回的唯一入口。

### Surface 矩阵

| Surface | Semantic 策略 | 降级行为 |
|---|---|---|
| 长期运行的 MCP / SDK 进程 | Provider 已初始化且 ready 时启用 | raw BM25 + term BM25 |
| 人工 CLI | 沿用现有显式 embedding 配置 | raw BM25 + term BM25 |
| UserPromptSubmit hook | 不冷加载；只使用已就绪的快速 Provider | 立即 raw BM25 + term BM25 |

第一阶段不新增常驻 embedding sidecar。若后续要让所有短生命周期 hooks 默认获得低延迟语义
召回，应单独设计服务生命周期、权限、安装、升级、跨平台 IPC、崩溃恢复和 doctor。

### 失败隔离

- 单个 route timeout/error 只丢弃该 route；
- 已完成的 lexical 结果不能因 semantic 故障被清空；
- hook 总预算继续默认 2 秒；
- Gateway、策略初始化或 cohort authorization 异常仍然返回空，不回退 raw hit；
- route timeout 与整体 timeout 分开记录。

## 融合与既有排序流水线

各候选通道沿用现有 `rrf_k=60`，第一版统一权重 1.0：

```text
semantic_raw          weight=1.0
lexical_terms         weight=1.0
lexical_raw_fallback  weight=1.0，只在 semantic_raw 不可用时出现
```

第一版不引入未经标注评测验证的复杂权重。一个条目同时被两路召回时，自然获得两次 RRF
贡献。每个候选必须在 RRF 前保留 `RouteEvidence`，包括原始 route rank 和可用的 vector
similarity；融合后的 RRF 分数不能覆盖这些证据。不修改 `MemoryItem` 持久化结构。

融合后继续执行既有候选流水线，后续阶段的 query 统一使用 `normalized_query`：

```text
route RRF
  -> metadata phrase boost
  -> status/handoff supplement
  -> optional cross-encoder rerank
  -> decay
  -> feedback value
  -> status/handoff boost
  -> adapter runtime evidence
  -> stale-state filter
  -> supersession filter
  -> optional MMR / Hopfield / graph
  -> Injection Gateway
  -> Context Firewall
  -> ContextPack
```

## Retriever 与公共兼容边界

- 保留 `Retriever.search(query)` 及其公共行为；
- 新增 `Retriever.search_routed(request)`；
- 普通 `memory search` 默认行为不变；
- `memory search --routed-recall` 使用新路径；
- MCP/SDK 可显式采用 routed path，不强制改变 raw diagnostics；
- 不新增 schema migration，不要求 reindex。

route-aware trace 可以扩展最终放行 `RetrievedItem` 的 explain 信息，但旧客户端必须能够忽略
新增字段。现有 `bm25_rank`、`vector_rank` 和 stage trace 继续可用。

## Hook 调用链与结构化协议

shell hook 不再自行完成关键词 gate、拼 `SEARCH_QUERY`、设置 raw-query 环境变量并解析 CLI
文本。目标调用形态：

```text
inject-context.sh
  -> memory search "<完整原始问题>"
       --routed-recall
       --context-firewall
       --format hook-json
```

Python 进程一次完成 normalization、Admission、关键词分析、route execution、fusion、Gateway
和 ContextPack。`hook-json` 是内部 adapter 协议，不是普通人类 CLI 输出：

```json
{
  "status": "injected|empty|timeout|error",
  "reason": "admission_rejected|no_candidates|all_rejected|overall_timeout|internal_error",
  "context": "...",
  "routes": [
    {
      "route": "lexical_terms",
      "status": "ok|skipped|timeout|error",
      "candidate_count": 5,
      "reason": null
    }
  ]
}
```

约束：

- 某一路 timeout 但其他路成功时，整体 status 仍由最终注入结果决定；
- shell 只负责 adapter-specific 包装和 fail-safe 输出；
- shell 不再匹配 `no matches`、首行或空字符串；
- `context` 只用于当前 adapter 注入，不进入 telemetry；
- malformed JSON、CLI 非零异常或 Gateway error 均 fail-closed。

## `memory brief` 与主动召回治理

`brief` 是项目恢复摘要，`search` 是任务相关性召回，二者不是互相替代关系。

- Agent-facing 文档禁止继续推荐 `brief || search`；
- 恢复项目先 brief；面对具体任务，再以完整任务描述调用 search；
- 不要求 Agent 必须先主观提炼 3–5 个关键词才能搜索；
- CLI `memory brief` 增加 `--fail-empty`；零条目时返回专用退出码 3；
- 省略 `--fail-empty` 时维持现有零条目退出码 0，避免破坏脚本；
- MCP `brief_memory` 保持 `total_shown` 结构化字段，调用方检查字段而非进程成功与否。

## 可观测性与隐私

新增聚合字段：

```text
admission.decision
admission.reason
route.enabled
route.status
route.latency_ms
route.candidate_count
fusion.candidate_count
gateway.included_count
gateway.excluded_reason_counts
```

固定隐私边界：

- 不记录原始 Prompt；
- 不记录关键词正文；
- 不记录被 Gateway 拒绝的条目内容或 ID；
- 只有最终允许注入的候选可在显式 explain 模式看到 route 来源；
- access reinforcement 只能发生在 Gateway 最终放行后；
- 正常 telemetry 只记录 route 名、状态、计数、耗时和固定原因枚举。

## 测试策略

严格按 TDD 实施，每项行为先有失败测试，再写最小实现。

### 1. Admission 单元测试

- 关键词为空但问题有完整语义：允许召回；
- “网关呢”这类短而具体的问题：允许召回；
- 空输入、纯标点、控制命令、确认/继续类弱输入：拒绝；
- Admission 结果不受关键词提取成败控制；
- routed Admission 传入 Gateway 后不会被 legacy term gate 覆盖；
- Admission 缺失或异常时 Gateway fail-closed；
- terms 为空时，route-aware answerability 不会自动放行或自动全拒；
- semantic/raw lexical 证据不足时返回 `route_answerability_insufficient`；
- semantic 阈值使用原始 vector score，不使用 RRF score。

### 2. 路由与融合测试

- `semantic_raw` 使用完整规范化问题；
- `lexical_terms` 只使用提取词；
- semantic 不可用时启用 `lexical_raw_fallback`；
- semantic timeout/error 时 BM25 结果仍返回；
- terms 为空只跳过 lexical_terms；
- 双路共同命中的条目获得两路 RRF 贡献；
- RRF 后仍保留各 route 的原始 rank 与 semantic score；
- explicit project 可以 hard filter，cwd/agent-inferred project 只能提供 soft scope；
- 后续 rerank/decay/status 阶段接收完整问题；
- legacy `Retriever.search()` 行为不变。

### 3. Gateway 与安全回归

- private、secret、needs-review、superseded、scope 不匹配条目不能注入；
- 全部候选被拒绝时返回 `all_rejected`，不回退 raw hit；
- Gateway 异常不泄露 raw hit；
- access reinforcement 只记录最终放行条目；
- MCP、SDK、CLI、Hook 对相同候选保持一致的安全集合。

### 4. Hook 与 CLI 合同测试

- hook 不再因 `lexical_terms=[]` 提前退出；
- hook 不再设置或依赖 raw-query 环境变量旁路；
- hook 不再解析 `no matches` 文本；
- `hook-json` 对 injected、empty、timeout、error 都有稳定结构；
- 单路 timeout 不丢失其他路候选；
- `memory brief --fail-empty` 零条目返回 3，默认仍返回 0；
- Agent-facing 文档和模板不推荐 `brief || search`；
- 普通 CLI、SDK、MCP 和 raw diagnostics 合同不变。

### 5. 人工标注评测集

至少建立 40 条人工标注查询，覆盖：

- 语义改写但无词面重合；
- 中文、英文和中英混合；
- 关键词提取遗漏或提错；
- 精确实体、命令、错误码；
- 弱确认和无召回价值输入。

每类至少 8 条；其中“语义改写”和“关键词提取遗漏/提错”共同构成 false-negative 目标子集。
评测同时记录 candidate recall 和最终 injection decision，避免只看“搜到了”却忽略注入污染。

## 验收标准

1. 标注集 Candidate Recall@10 不低于现有基线。
2. “语义改写/关键词失误”子集至少修复 3 条既有 false negative，且其他类别不得新增已标注
   false negative。
3. 禁止注入安全集保持 100% 拦截。
4. terms 为空的有效问题仍能通过 raw route 取得候选。
5. terms 为空的候选只有在 route-aware answerability 证据充足时才能进入 Prompt。
6. semantic 故障不能让 lexical 结果丢失。
7. 离线 hooks 不超过现有 2 秒硬预算。
   独立性能验收连续运行至少 30 次，p95 相对旧链路的增量不超过 150ms；CI 不以脆弱的墙钟
   断言替代 deadline/fake-clock 单元测试。
8. Hook 不再包含关键词为空即退出、文本状态解析和 raw-query 环境变量旁路。
9. Gateway 安全矩阵、adapter hook tests、targeted tests、全仓测试、ruff 和
   `git diff --check` 全部通过。
10. 现有 `Retriever.search()`、普通 `memory search`、SDK/MCP raw diagnostics 合同不变。
11. 无 MemoryItem schema migration，无强制 reindex。

## 发布与回滚

- routed recall 默认启用，提供 `AGENT_MEMORY_HUB_ROUTED_RECALL=0` 紧急回滚开关；
- 回滚只切回旧候选生成，不允许关闭 Injection Gateway；
- `memory doctor` 增加 routed recall、Gateway、semantic provider、offline fallback 四项状态；
- 安装器识别旧 hook fingerprint，升级包后提示或自动 repair adapter；
- 旧用户不升级时维持旧行为；要获得新召回链必须升级包并刷新 adapter hooks；
- PR 依赖 Injection Gateway，Gateway 合入后 rebase 到最新 `main`；
- 发布后只监控 route 可用率、超时率、空候选率、Gateway 全拒绝率和实际注入率；
- 若离线 hook p95 越过预算、禁止注入集回归或空候选率显著恶化，使用候选生成开关回滚并保留
  Gateway 安全边界。

## 后续阶段

1. Session continuation：为“继续、确认、是、1”建立可靠 session 状态与上轮任务指针。
2. 常驻 Semantic Provider：单独设计跨平台进程生命周期、IPC、权限、升级与崩溃恢复。
3. 权重学习与查询规划：只有在标注集和线上聚合指标足够后，再评估 route 权重或 LLM planner。
4. 与 staged context depth 治理集成：候选来源治理稳定后，再统一验证 locator/overview 与有界深读链。
