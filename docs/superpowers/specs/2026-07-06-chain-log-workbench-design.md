# 链路日志工作台设计

日期：2026-07-06

## 背景

现有 Web 后管已经有「链路追踪」模块，主要回答“某条 memory 被哪个 Agent 写入、读取、注入过”。这个视角适合追踪记忆资产本身，但不足以排查一次用户请求为什么召回了错误记忆、为什么没有注入、为什么某条候选被某个算法推高或压低。

本设计新增「请求链路日志工作台」：按一次请求聚合 runtime 账本，把 hook、query gate、召回、算法重排、防火墙、加载、注入、反馈串成完整链路，并在 Retrieval 内部展开算法子链路。

## 目标

- 一次请求必须能看到完整流水线：Hook -> Prompt Frame -> Query Gate -> Retrieval -> Firewall -> Packing -> Injection -> Feedback。
- Retrieval 不能只显示一个粗节点，必须展开 BM25、Vector、RRF、Cross-Encoder、遗忘曲线、衰减系数、Feedback、Runtime/Status、Temporal/Supersession、MMR、Hopfield、Graph Expansion 等算法环节。
- 每个节点支持 hover 预览和 click 详情，点击后保持链路上下文，便于连续排障。
- 未启用、跳过或缺证据的算法环节不能隐藏，必须显示 `not_enabled`、`skipped` 或 `not_observed`，并给出原因。
- Web 展示只使用脱敏字段，不暴露原始 prompt、query、memory body 或 tool arguments。

## 非目标

- 不替换现有「按 Agent / 行为 / 记忆」的 lineage 视图。
- 不在 P0 做大屏式动态图谱；图谱适合后续作为辅助视图。
- 不把 raw prompt 或 memory 正文放进 Web read model。
- 不在这个模块里自动改写 memory 或自动调参；这里只做观测、解释和排障入口。

## 当前数据基础

已有 runtime 账本可作为聚合来源：

- `runtime/adapter-events.jsonl`：SessionStart、UserPromptSubmit、Stop 等 hook 事件。
- `runtime/hook-latency.jsonl`：hook 耗时。
- `runtime/injection-cohorts.jsonl`：注入 cohort、item ids、query hash、pack metrics。
- `runtime/recall-gaps.jsonl`：query gate / firewall / recall gap 记录。
- `runtime/task-outcomes.jsonl`：任务结果反馈。
- `runtime/task-outcome-feedback.jsonl`：反馈应用情况。
- `items/*.md` 与 `index.db`：候选元数据、反馈统计、refs graph、context views。

现有 `/api/memory-lineage` 继续服务记忆资产视角。新增请求链路工作台不应把所有逻辑塞进 `memory_lineage.py`，而应新增独立 read model，避免两个视角互相污染。

## 信息架构

后管新增一个「链路日志」工作台，可以放在现有「链路追踪」导航下的第二个 tab：

- `记忆链路`：保留现有 Agent -> 行为 -> 记忆列表 -> 记忆详情。
- `请求链路`：新增 Request Chain Workbench。

请求链路工作台采用三栏布局：

- 左栏：请求列表和筛选。
- 中栏：完整流程节点轨道。
- 右栏：点击节点后的详情抽屉。

## 左栏：请求列表

筛选条件：

- 时间范围：近 1h、6h、24h、72h。
- Adapter：codex、claude-code、qoder、wukong、unknown。
- Session ID。
- CWD / project。
- Query Gate 结果：inject、block、status_only、skipped、not_observed。
- Injection 结果：injected、empty、blocked、partial、not_observed。
- Gap reason：query_not_injectable、all_candidates_rejected、partial_candidates_rejected、multimodal_gap 等。

请求列表每行显示：

- 时间、adapter、session 短 ID、cwd 尾部。
- 链路完整度，例如 `7/9 observed`。
- 最终状态：injected、blocked、gap、partial、not_observed。
- 注入数量、拒绝数量、候选数量。
- 主要阻断原因。

## 中栏：主链路节点

主链路固定九个阶段：

| stage_id | 节点 | 状态来源 | 说明 |
|---|---|---|---|
| hook_capture | Hook 捕获 | adapter-events、hook-latency | 记录事件名、adapter、session、cwd、耗时 |
| prompt_frame | Prompt Frame | query_signal diagnostics、multimodal gap | 判断输入是否可检索，是否有附件/图片证据缺口 |
| query_gate | Query Gate | query_signal diagnostics、recall-gaps | 判断是否允许检索/注入 |
| retrieval | Retrieval | retrieval trace、injection pack metrics | 候选召回和算法流水线 |
| context_firewall | Context Firewall | recall-gaps、context pack metrics | 候选 include / reject 决策 |
| context_loading | Context Loading | context views、pack metrics | locator / overview / detail 选择 |
| packing | Packing | injection-cohorts pack_metrics | token 预算、packed count、detail_uri |
| injection | Injection | injection-cohorts | 实际注入 item ids 和 query hash |
| feedback | Feedback / Gap | recall-gaps、task-outcomes、feedback applications | 记录 gap、adopt/reject、后续治理入口 |

每个主节点有五种状态：

- `passed`：该阶段有明确成功证据。
- `blocked`：该阶段明确阻断后续链路。
- `partial`：部分候选通过，部分被拒绝。
- `skipped`：该阶段被配置或输入条件跳过。
- `not_observed`：没有观测到账本证据。

`not_observed` 不等同于失败；UI 必须区分“系统没走”和“没有证据”。

## Retrieval 算法子链路

点击主链路 `Retrieval` 后，中栏下方展开算法瀑布流。算法节点固定排序，未启用也显示：

| algorithm_id | 算法节点 | 必须展示 |
|---|---|---|
| metadata_filter | Metadata Filter | type/project/tags/tenant/since_days/superseded 过滤条件 |
| bm25 | BM25 | 输入 query hash、召回数、top rank、是否降级 |
| vector | Vector | embedding 是否可用、召回数、top rank、降级原因 |
| rrf | RRF Fusion | BM25 rank、vector rank、融合公式、融合后分数 |
| cross_encoder | Cross-Encoder Rerank | 是否启用、模型可用性、rerank 前后排名变化 |
| retention | 遗忘曲线 | decay_class、days_since_reference、half_life、retention |
| decay_coefficient | 衰减系数 | retention、access_mul、support_mul、gain_mul、contradiction_mul、bounded coef |
| feedback_value | Feedback Value | support_count、contradict_count、gain_score、score multiplier |
| runtime_status | Runtime / Status Boost | recent use、handoff、signal、stale、runtime evidence |
| temporal_supersession | Temporal / Supersession | temporal conflict、superseded、newer state |
| mmr | MMR | lambda、max similarity、diversity penalty、前后排序 |
| hopfield | Hopfield | attractor、association ids、temperature、top associations |
| graph_expansion | Graph Expansion | refs_graph neighbor、depth、source edge、expanded ids |
| budget_trim | Budget Trim | token budget、trimmed ids、trim reason |

算法节点的状态：

- `applied`：算法实际执行并改变或确认候选集。
- `no_change`：算法执行但候选排序/集合未变化。
- `not_enabled`：配置关闭。
- `skipped`：输入条件不满足，例如没有 embedding、候选数不足、top_k 不足。
- `not_observed`：当前账本没有该算法证据。

## 候选分数流水

右侧抽屉的核心是 candidate table。每行是一条候选 memory，每列是一段算法后的状态：

- `item_id`、title、type、project、maturity。
- `bm25_rank`、`vector_rank`、`rrf_score`。
- `cross_encoder_score` 和 rank delta。
- `retention`、`decay_coef`、`feedback_multiplier`。
- `runtime_boost`、`temporal_penalty`、`supersession_status`。
- `mmr_score`、`hopfield_assoc_score`、`graph_expanded_from`。
- `final_score`、`final_rank`。
- `firewall_action`：include、exclude、defer。
- `firewall_reasons`：query_mismatch、scope_mismatch、answerability、missing_source、stale_state、budget 等。
- `loaded_view`：locator、overview、detail。

表格支持：

- 按任意算法列排序。
- 只看 included / rejected / expanded / trimmed。
- 点击候选行展开 score delta：从初始 rank 到最终 action 的完整变化。
- 对比两个候选：解释为什么 A 进了、B 没进。

## Hover 与 Click 交互

Hover 只展示短摘要，避免遮挡主链路：

- 状态。
- 输入/输出数量。
- top 变化。
- 主要原因。
- 耗时。

Click 打开右侧详情抽屉，而不是默认居中大弹窗。抽屉保持链路上下文，适合连续点击多个节点。

抽屉 tabs：

- `摘要`：人能快速读懂的节点结论。
- `候选`：候选分数流水和 include/reject 表。
- `证据`：runtime sidecar、item ids、query hash、pack metrics。
- `原始 JSON`：脱敏后的原始记录。

只有在查看大 JSON 或超长 evidence 时再打开 modal。

## 数据模型

新增 read model：`agent_brain/product/chain_log.py`。

核心结构：

```python
@dataclass(frozen=True)
class ChainLogReport:
    filters: dict[str, Any]
    summary: dict[str, Any]
    chains: tuple[ChainSummary, ...]

@dataclass(frozen=True)
class ChainDetail:
    chain_id: str
    adapter: str
    session_id: str | None
    cwd: str | None
    started_at: str
    completed_at: str | None
    completeness: dict[str, Any]
    stages: tuple[ChainStage, ...]
    algorithm_trace: tuple[AlgorithmStage, ...]
    candidates: tuple[CandidateTrace, ...]
    evidence: tuple[EvidenceRef, ...]
    boundaries: tuple[str, ...]
```

`chain_id` 初版使用稳定派生，不引入迁移成本：

- 优先：`session_id + query_sha256 + nearest UserPromptSubmit timestamp`。
- 无 query hash：`session_id + adapter + UserPromptSubmit timestamp`。
- 只存在 gap：`gap_id` 作为链路锚点。
- 只存在 injection：`cohort_id` 作为链路锚点。

后续如果 hook 直接写入 request_id，可无缝替换为显式 ID。

## API

新增 API：

- `GET /api/chain-logs?hours=24&limit=100&adapter=&session_id=&cwd=&status=&gate=&q=`
  - 返回请求链路列表和摘要。
- `GET /api/chain-logs/{chain_id}`
  - 返回完整主链路、算法子链路、候选分数流水。
- `GET /api/chain-logs/{chain_id}/nodes/{stage_id}`
  - 可选，用于后续懒加载大节点详情。

API 边界：

- 不返回 raw prompt、raw query、memory body。
- query 只返回 `query_sha256`、`has_query`、`query_terms_count`、query gate reason。
- candidate 只返回 item id、标题、摘要级元数据和分数/原因。
- 原始 JSON 必须走 `_sanitize`，过滤 `prompt`、`query`、`question`、`body`、`content`、`tool_arguments`。

## 前端落点

首选在 `web/templates/dashboard.html` 的 lineage 模块内新增请求链路 tab，复用现有视觉语言：

- 不做 landing / hero。
- 不做装饰性图形。
- 节点尺寸固定，避免 hover 或动态文本导致布局跳动。
- 主色按状态区分，不使用单一紫蓝色堆叠。
- 移动端降级为纵向节点轨道，抽屉变为底部 sheet。

新增 CSS 命名建议：

- `.chain-workbench`
- `.chain-list`
- `.chain-node-rail`
- `.chain-node`
- `.chain-algorithm-waterfall`
- `.chain-detail-drawer`
- `.chain-candidate-table`

## 完整性规则

每条链路都计算完整性：

- `observed_stage_count`
- `expected_stage_count`
- `missing_stage_ids`
- `blocked_stage_id`
- `final_outcome`
- `evidence_quality`

UI 顶部显示：

- `完整度 7/9`
- `阻断在 Query Gate`
- `Retrieval 算法证据 9/14`
- `候选流水 12 条`

这能直接暴露“链路真的完整”还是“只看到了部分日志”。

## 错误与边界

- 同一 session 多个 prompt：按 UserPromptSubmit 时间窗口和 query hash 归并，避免串链。
- 没有 query hash：仍展示 hook/gap/injection 证据，但标记 `weak_correlation`。
- 只有老数据：链路可展示 `not_observed`，不伪造算法 trace。
- 算法未写 trace：显示为 `not_observed`，并提示需要后续 instrumentation。
- 脱敏后字段不足：优先保留 item id、状态、reason、count、hash、latency。

## 实施分期

P0：请求链路工作台可用

- 新增 `chain_log.py` read model。
- 新增 `/api/chain-logs` 和 `/api/chain-logs/{chain_id}`。
- Web 新增「请求链路」tab、请求列表、主链路节点、右侧详情抽屉。
- Retrieval 算法子链路先从已有 trace / pack metrics / gap evidence / item metadata 推导，缺失显示 `not_observed`。
- 单元测试覆盖链路聚合、缺失节点、脱敏、API。

P1：算法 trace 完整化

- 扩展 Retriever trace，记录每个算法节点的 before/after rank、score delta、skip reason。
- 记录 MMR、Hopfield、Graph Expansion 的启用状态和候选变化。
- 把 ContextFirewall include/reject reason 结构化到 chain detail。

P2：排障增强

- 候选对比。
- 两次请求对比。
- 一键生成 recall gap case。
- 导出脱敏链路 JSON。

## 验证计划

单元测试：

- `test_chain_log_groups_events_by_session_and_query_hash`
- `test_chain_log_marks_missing_stages_not_observed`
- `test_chain_log_keeps_disabled_algorithms_visible`
- `test_chain_log_sanitizes_prompt_query_and_body`
- `test_chain_log_candidate_trace_explains_include_and_reject`

API 测试：

- `/api/chain-logs` 返回列表、summary、filters。
- `/api/chain-logs/{chain_id}` 返回 stages、algorithm_trace、candidates。
- 权限依旧走 `get_current_user`。

前端测试：

- dashboard integrity 不因新增 tab 破坏现有页面。
- 请求链路 tab 能渲染空态、列表态、详情态。
- 节点 hover 字段来自摘要，不触发布局跳动。
- 点击节点打开详情抽屉，ESC / 关闭按钮可关闭。

手工验收：

- 用真实 `~/.agent-memory-hub/runtime/*.jsonl` 能看到最近 72 小时链路。
- 对一条有 injection 的请求，能从 Hook 走到 Injection。
- 对一条 query gate block 的请求，能看到阻断节点和原因。
- Retrieval 内部至少显示固定 14 个算法节点，未观测的节点明确标记。

## 成功标准

- 用户能在 30 秒内回答：这次请求有没有检索、有没有注入、阻断在哪、哪些算法影响了排序。
- 用户能解释任意一条候选 memory：为什么被召回、为什么被降权、为什么被拒绝或注入。
- 旧数据不伪造完整 trace，新数据能逐步补齐算法证据。
- 后续误召回问题可以直接从链路工作台导出 evidence，而不是翻多个 jsonl 和日志。
