# Agent 分层召回治理设计

## 背景

Agent Memory Hub 已提供 `locator / overview / detail` 三层上下文视图，并通过
`detail_uri`、`retrieve_hint` 和 `read_memory` 支持可逆深读。但当前公共
`select_context_view(..., requested="auto")` 会把 `raw + L0 + direct evidence`
的条目自动提升到 `detail`。MCP `search_memory` 随后会把完整 `body` 放进命中结果，
CLI/hook、SDK 和 Web API 也会复用同一选择策略；SDK/Web 搜索还会通过
`body[:200]` 的 `snippet` 旁路带入正文。

因此，虽然文档要求“先搜索压缩视图，再按需深读”，运行时仍可能在一次 Top-K
搜索中把多条正文全部注入 Agent 上下文。该问题属于公共召回契约缺口，不属于
Codex、Claude Code、Qoder、Wukong 或任一 adapter 的单点问题。

## 目标

建立可测试、可审计的 Agent 分层召回契约：

1. Agent 的自动召回和普通发现阶段只返回 `locator / overview`。
2. Agent 从候选中选择真正需要的 1–3 条，再通过
   `read_memory(id, head=2000, view="detail")` 深读。
3. 显式 `verbosity="detail"` 继续原样返回正文，不删除、不降级、不改变兼容语义。
4. 宽泛显式 detail 搜索可被发现和治理，但治理信号不得阻断或改写正文结果。
5. 所有复用公共召回能力的 Agent 工具和 adapter 遵循同一契约，不做客户端特判。
6. 修复 Qoder adapter doctor 对非对象 JSON transcript 记录调用 `.get()` 的崩溃。

## 非目标

- 不移除 `detail` verbosity。
- 不禁止人工管理、备份、迁移、导出或单条全文读取。
- 不给 `read_memory` 增加跨调用的强状态锁或 cohort token。
- 不改变 BM25、向量检索、RRF、MMR、ContextFirewall 的相关性排序。
- 不把 Web 管理端 `/api/search/fulltext` 的人工正文检索改造成 Agent 召回入口。

## 统一契约

### 1. 视图选择

`requested="auto"` 的输出域固定为 `{locator, overview}`：

- 有可用 overview，且条目类型、证据边界、有效期或 firewall 决策需要更多上下文时，
  选择 overview。
- 没有可用 overview 时选择 locator。
- `raw + L0 + direct evidence` 不再自动升级为 detail；该证据形态只作为优先选择
  overview 的理由，overview 缺失时回退 locator。
- `requested="detail"` 仍选择 detail，并保留 `explicit_detail` load reason。

### 2. 搜索与深读

Agent 标准调用顺序为：

```text
brief_memory / search_memory(auto)
  -> inspect locator/overview + detail_uri/retrieve_hint
  -> select 1–3 relevant item ids
  -> read_memory(id, head=2000, view="detail")
  -> only widen head when the bounded text is insufficient
```

`search_memory(auto)` 不得出现顶层 `body`，`context_pack.text` 也不得包含正文。
显式 `search_memory(..., verbosity="detail")` 保持现有正文返回行为。

### 3. 正文旁路

- SDK `SearchResult.snippet` 和 Web `/api/search` 的 `snippet` 在 locator/overview/auto
  模式下不得使用 `body[:200]`，应复用选中的压缩视图。
- 显式 detail 模式下，既有 detail context pack 和正文语义保持不变。
- `include_resources` 属于显式证据展开能力，不在默认/auto 路径启用；其行为暂不删除，
  但 Agent onboarding 不得把它当作普通搜索默认值。
- `/api/search/fulltext` 是人工管理端的显式全文检索能力，不纳入 Agent 自动注入契约。

### 4. 显式 detail 治理

显式 detail 是兼容的高级逃生口，而不是普通发现入口：

- 返回正文的逻辑不变。
- 当显式 detail 与 `top_k > 3` 组合时，产生非阻断治理信号，提示调用方优先采用
  `auto -> read_memory(1–3)`。
- 治理信号应进入结构化诊断或既有 runtime evidence，不写入正文，不改变结果排序，
  不让正常的单条或少量显式 detail 调用产生噪声。
- `load_reason=explicit_detail` 保留为审计依据。

具体落点优先复用一个公共 policy helper，供 MCP、SDK、Web API 和 CLI 使用，避免
各入口自行判断 `top_k` 和 verbosity。

## 入口矩阵

| 入口 | 普通/auto 行为 | 显式 detail | 深读方式 |
|---|---|---|---|
| MCP `search_memory` | locator/overview，不返回 body | 保持返回 body | MCP `read_memory` |
| CLI `memory search --context-firewall` | locator/overview | 保持 detail 文本输出 | `memory read --head 2000 --view detail` |
| UserPromptSubmit hook | 只注入 locator/overview | 不主动请求 detail | 后续 Agent 调 MCP/CLI read |
| Python SDK `MemoryClient.search` | 压缩 context pack，snippet 不取正文 | 保持 detail context pack | `MemoryClient.read(head=2000, view="detail")` |
| Web `/api/search` | 压缩 context pack，snippet 不取正文 | 保持 detail context pack | `/api/items/{id}?head=2000&view=detail` |
| Adapter awareness/bootstrap | 明确 staged recall 顺序 | 仅说明高级显式用途 | 统一要求选择 1–3 条 |

## Adapter 治理

所有 adapter 共用的 Awareness Channel、MCP onboarding、Agent Memory Discipline 和
各客户端 bootstrap/bridge 文案必须表达同一规则。实现时优先修改公共渲染模板；
只有无法复用模板的 Qoder/QoderWork/Wukong 等原生 bridge 才做定点同步。

新增 truth-contract 测试扫描 Agent-facing 指导内容：

- 必须包含“先搜索/brief，再读 1–3 条”的顺序。
- 不得建议普通任务直接使用 `search_memory(..., verbosity="detail")`。
- fallback CLI 示例使用 `--verbosity auto`，深读示例使用 `memory read`。

## Qoder doctor 健壮性修复

`QoderAdapter._transcript_observed_time` 当前逐行执行 `json.loads` 后直接调用
`record.get("timestamp")`。合法 JSON 行可以是 string、list、number、boolean 或 null，
因此 doctor 会在这些记录上抛出 `AttributeError`。

修复方案：

- `json.loads` 成功后先判断 `isinstance(record, dict)`；非 dict 记录跳过。
- 保留现有无效 JSON 跳过逻辑和 timestamp 解析逻辑。
- 增加 string/list/scalar/null 混合 transcript 回归测试，并验证后续合法对象时间戳仍被读取。

该修复独立于召回策略，不改变 Qoder 的 MCP、hook 或 native memory 边界。

## 测试策略

按 TDD 分两条红绿链实施。

### 分层召回

1. 公共选择器：raw L0 direct-evidence 的 auto 从 detail 改为 overview/locator；显式 detail
   仍返回 detail。
2. MCP：auto Top-K 不含 body；显式 detail 仍含 body；宽泛 detail 产生非阻断治理信号。
3. CLI/hook：自动注入输出不含正文，保留 retrieve hint；显式 detail CLI 仍输出正文。
4. SDK/Web：auto 的 context pack 和 snippet 不含正文；显式 detail 仍可读取正文。
5. Adapter/docs truth contract：所有 Agent-facing 文案遵循 staged recall。
6. 系统 benchmark：context pack 可逆性、预算和 firewall 行为保持通过。

### Qoder doctor

1. 构造含 JSON string/list/scalar/null 的 transcript，复现 doctor 崩溃。
2. 类型守卫后 doctor 完成扫描并读取后续合法 timestamp。
3. 运行 Qoder adapter、CLI adapter 和 runtime evidence 相关测试。

## 兼容与迁移

- `verbosity` 枚举不变，调用签名不删除参数。
- 显式 detail 的返回正文语义不变。
- auto 过去返回 detail 的调用将收到 overview/locator；这是有意的安全和上下文预算修正。
- `context_pack.detail_uri`、MCP/CLI retrieve hint、token 估算继续保留。
- SDK/Web `snippet` 字段保留，只把 auto/default 下的来源从正文切换为压缩视图。
- SDK `MemoryClient.read` 和 Web item read 新增可选 `head / view` 参数；省略参数时继续返回
  既有完整正文，保证兼容，Agent 指导和示例统一使用有界参数。
- 新增治理诊断字段时必须保持旧客户端可忽略，不改变现有必填字段。

## 验收标准

1. 对带直接证据的 raw L0 条目执行 Top 5 auto 搜索，所有命中均不出现 detail/body。
2. 对同一条目显式执行 `verbosity="detail"`，正文仍完整返回。
3. Codex、Claude Code、Qoder、QoderWork、Wukong 等复用的自动注入链只产生
   locator/overview context pack。
4. SDK 和 `/api/search` 的普通 snippet 不包含只存在于 body 的标记文本。
5. SDK/Web 的有界单条读取可返回前 2000 字符和截断元数据；旧的无参数读取保持全文。
6. Agent-facing 文案明确限制为选择 1–3 条后深读。
7. 宽泛显式 detail 可审计但不被阻断。
8. Qoder doctor 可跳过所有非对象 JSON transcript 行，不再崩溃。
9. 目标测试、完整测试、ruff 和 `git diff --check` 均通过。
