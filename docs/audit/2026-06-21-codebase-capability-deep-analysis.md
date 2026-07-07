# Agent Memory Hub 全代码能力深度审计

日期：2026-06-21  
审计基线：`main@523f0f1`
审计方式：源码阅读、CLI/MCP/Web introspection、adapter capability JSON、测试合同、文档事实合同、可视化事实口径核对。

## 0. 一句话判断

Agent Memory Hub 现在不应再被表述为“记忆库”或“向量 RAG”。它更准确的定位是：

> 本地优先的跨智能体可信上下文操作系统。

它做的不是把聊天记录塞进 prompt，而是把多 Agent 协作拆成一条可审计链路：

`证据进入 -> 记忆写入 -> 索引投影 -> 混合召回 -> 上下文压缩 -> 注入防火墙 -> 反馈治理 -> 质量门禁 -> 多 Agent 复用`

这个链路才是 AMH 的核心优势。任何 README、Web、架构图和对外说明都应该围绕它展开，而不是堆功能名词。

## 1. 当前真实基线

| 项 | 当前事实 | 说明 |
|---|---:|---|
| 核心包规模 | `agent_brain/` 281 files，278 Python files，31499 Python lines | 业务核心、接口、治理、检索、证据、adapter、产品层。 |
| Web Admin | `web/` 49 files，40 Python files，4026 Python lines | FastAPI 本地管理面和单页 dashboard。 |
| Runtime kit | `agent_runtime_kit/` 28 files | hooks、shell tools、schema、MCP wrapper、记忆纪律。 |
| 测试规模 | `tests/` 223 files，174 Python files，26776 Python lines | unit、conformance、perf、docs truth、surface lock。 |
| Benchmark | `benchmarks/` 7 files，3 Python files，1240 Python lines | retrieval、compression、ML advisory、release gate。 |
| MCP surface | 28 registered tools | 27 operation tools + 1 onboarding guide tool。 |
| Web surface | 89 API/WS routes | Cockpit、data-flow、memory-lineage、adapter onboarding、candidate review、Headroom、ML gate 等已进入 Web 面。 |
| Adapter registry | 16 adapter records | 13 verified，2 install-ready，1 wip。 |
| 最近全量测试证据 | 1483 passed，2 skipped | P1/P2、安装验证、链路追踪和多模态治理闭环后在本 worktree 完成全量验证；本轮仍需按最新改动刷新验证。 |

## 2. AMH 已经具备什么

### 2.1 可信事实源

已具备：

- `MemoryItem` 是结构化长期记忆合同，不是随手写的文本片段。
- `items/mem-*.md` 是 source of truth，SQLite、向量、图谱、runtime ledger 都是可重建投影。
- schema 覆盖 type、project、tenant、auth、sensitivity、refs、retention、maturity、context views、validity、evolution。
- `refs` 不只记录文件和 URL，也能指向 memory、commit、resource、extraction。
- `ItemsStore` 支持 BOM/CRLF 容错、archived 默认跳过、单条坏 Markdown 不拖垮全局扫描。

价值：

- 用户能打开 Markdown 直接审计记忆，不被黑盒数据库锁死。
- SQLite/FTS/vector 失效时可以重建，不会丢事实源。
- 每条重要记忆天然适合 provenance、治理和长期维护。

### 2.2 单一核心写漏斗

已具备：

- CLI `write_memory`、MCP `write_memory`、SDK `MemoryClient.write`、Web create/clone、Hermes remember/import、proactive candidate approve 都走 `WriteService`。
- `WriteService` 执行 schema 后的 audit gate、review marking、字段补全、质量 warning、Markdown write、best-effort index。
- critical/high audit finding 默认阻断，除非显式 `allow_unsafe`。
- index/embedder 失败时 Markdown 仍写入成功，同时标记 dirty index。
- hook shell 层失败时会落 pending queue，之后通过 `WriteService` replay。

仍需诚实标注：

- CLI/MCP bulk import 的 `import_service` 仍直接写 `ItemsStore`，然后 best-effort index。
- 少量治理内部路径，如 consolidation/compressor/evolve executor，仍直接 `store.write`。这些是内部改写路径，不应被 README 写成“所有写入无例外统一”。
- 下一步应增加 write-funnel conformance：外部入口不得绕过 `WriteService`，内部治理写入要明确 allowlist 或迁移。

### 2.3 证据层和长期知识分层

已具备：

- 原始对话保存在 `sources/conversations/*/messages.jsonl`，包含 message id、hash、source offset、agent、session、project、cwd、sensitivity、tier。
- raw conversation 默认不直接进入 prompt，只作为证据和候选来源。
- `ResourceStore` 支持 `resources/*.json` 与 `extractions/*.json`。
- `ResourceReader` 支持 search、summary、outline、segment、exact、context packing。
- Web/SDK 搜索已经能暴露 resource context / resource results 诊断。

价值：

- AMH 不把 transcript 当长期记忆。
- 证据、抽取、结论分层，能解释“这条记忆从哪里来”。
- 未来接 PDF、网页、图片、音频时不需要污染 MemoryItem 主合同。

仍需增强：

- live brain resource 样例仍不足。
- resource/extraction 还没有成为与 MemoryItem 完全等价的主召回源。
- 需要产品化导入链路：PDF/Web/Image/Audio -> resource/extraction -> MemoryItem candidate -> review -> WriteService。

### 2.4 可重建索引投影

已具备：

- `HubIndex` 使用 SQLite WAL + busy_timeout，支持多 agent 共享 brain pool。
- `items_meta` 存 metadata、confidence、retention、feedback、supersession、maturity、context views。
- `items_fts` 提供 FTS5 全文检索。
- `items_vec` 使用 sqlite-vec 提供向量近邻。
- `refs_graph` 提供显式 memory graph。
- reindex/verify/index drift repair 能从 Markdown 事实源恢复派生索引。

价值：

- 读取速度和检索能力来自投影，可信事实来自 Markdown。
- 投影可以坏、可以删、可以重建，事实源不能丢。

### 2.5 可解释召回与排序

已具备：

- CJK segmentation 与 query expansion。
- BM25 + vector 双路召回。
- RRF 融合，避免单一路径误判。
- degraded embedder 时自动退回 BM25-only，避免低质向量污染召回。
- 可选 cross-encoder rerank。
- confidence、retention、feedback、freshness、scope-risk、status/handoff boost、adapter runtime evidence boost。
- stale temporal filter 与 supersession filter。
- 可选 MMR、graph expansion、Hopfield associative expansion。
- `Retriever.search(..., explain=True)`、CLI `--explain`、MCP/SDK/Web trace。

价值：

- 不是“向量相似就塞进去”，而是多阶段、可解释、可调试。
- 用户能看到为什么召回、为什么没召回、哪一阶段影响了排序。

### 2.6 上下文经济和 Headroom 式压缩

已具备：

- locator / overview / detail 分层加载。
- `context_pack` 只把压缩 prompt view 放进上下文，同时携带 `detail_uri`、retrieve hint、token 估算和 CCR sidecar 信息。
- Headroom 启发式内容路由已内化到 AMH 本地链路，覆盖 search results、build logs、git diff、JSON array、plain text。
- 可选外部 `headroom` Python 包或 CLI provider，但默认不依赖它。
- `memory benchmark compression`、Web `/api/compression-gate`、release gate 会检查 anchor recall、must-drop 噪声、token savings、reversibility。

价值：

- 压缩不是“丢正文”，而是“可逆的预算控制”。
- Few-shot gate 证明压缩没有把关键文件、错误栈、commit、路径等证据压没。

### 2.7 注入前防火墙

已具备：

- source required、sensitivity、review tag、query mismatch、temporal scope、stale signal/handoff、negative feedback、duplicate cluster、budget、cohort coverage。
- injection cohorts 和 injection feedback 记录真实采用/拒绝反馈。
- recall gap 和 task outcome feedback 用于发现“该召回没召回”。

价值：

- AMH 的默认目标不是多塞，而是少而准地塞。
- 防火墙让过期、跨范围、低置信、已替代、敏感或不匹配的记忆无法轻易污染上下文。

### 2.8 治理、演化和反漂移

已具备：

- Skill audit、outbound audit、write-path fail-close。
- duplicate/noise/TTL/quality governance pipeline。
- staleness、citation rot、drift clusters、contradictions、decision pattern extraction。
- review queue、maturity scoring、tiering、auto-governance。
- Evolve proposal + audit gate。
- semantic contradiction baseline：规则 + embedding advisory，LLM judge 仍 gated。

价值：

- 记忆不是写完就结束，而是持续成熟、淘汰、合并、降权、复核。
- 自动治理保守执行，archive/delete/consolidate/supersede/skill synthesis 保持 review-required。

### 2.9 多智能体接入边界

真实状态：

| 状态 | 数量 | 名单 |
|---|---:|---|
| `verified` | 12 | aider、aone_copilot、claude_code、cline、codex、continue_dev、cursor、github_copilot、hermes_agent、openhuman、opensquilla、wukong |
| `install-ready` | 3 | openclaw、qoder、qoder_work |
| `wip` | 1 | mulerun |

边界判断：

- 钩子类适配器需要运行账本事件；模型上下文协议类适配器需要 doctor + 主动 AMH 工具面探测；文件旁路类适配器需要安装/doctor 交易证据。
- `verified` 必须由 `adapter verify` 写入 passed 记录；失败记录和 runtime event 不会自动升级。
- OpenClaw、Qoder 仍为 install-ready，不再是规划中；QoderWork 已通过真实 QoderWork CLI smoke 证明模型看见 `<agent_brain>`，并有同 session AMH injection cohort。
- 不同 Agent 能力边界不同：有的能 hooks，有的能 MCP，有的只能文件上下文，有的走 provider tools。

价值：

- AMH 不把所有 Agent 画成同一种能力。
- adapter truth contract 防止 README 和图谱把“写了安装器”夸成“真实客户端验证通过”。

### 2.10 产品层和 Web 管理面

已具备：

- Web 89 API/WS routes。
- Cockpit summary：handoff pack、key decisions、open signals、trust risks、adapter health、memory candidates、timeline。
- Adapter onboarding：capabilities、doctor、install、verify、install-verify、uninstall。
- DataFlowLedger / `/api/data-flow`：近三天 adapter events、verification、Loop events、recall gaps、task outcomes、injection cohorts 的脱敏流转视图。
- MemoryLineage / `/api/memory-lineage`：按 Agent 串起写入维护、读取注入、存储介质、检索流水、成熟度、遗忘曲线、衰减系数、Hopfield 和分层加载。
- Proactive memory candidates：generate、semantic generate、approve、reject。
- Hierarchical memory sidecar：L2/L3 deterministic projection，不篡改 canonical items。
- Retrieval/compression/ML advisory gates 在 Web 暴露。
- Headroom status/compress/retrieve 在 Web 暴露。
- `handdrawn` UI brand 已进入 dashboard 主题体系。

价值：

- AMH 已有产品读模型，不只是 CLI 工具箱。
- Web 可以成为“今天该接什么、该信什么、哪里有风险”的工作台。

仍需增强：

- Review queue 还需要更强的批量 UI、证据引用和审计记录。
- Cockpit 需要更多真实工作流 dogfooding，不应写成企业平台已成型。
- `memory api-docs` 已改为从 `web.app` 动态枚举，与实际 Web 89 routes 同步。

### 2.11 ML/DL 边界

已具备：

- `memory benchmark ml-advisory` 和 Web `/api/ml-advisory-gate`。
- release gate 同时检查 retrieval quality、compression few-shot gate、ML advisory gate。
- policy 明确阻断 `candidate_mode=default`，即使模型 delta 高，也只能进入 advisory/experiment 或人工 release decision。

价值：

- AMH 能借鉴 ML/DL，但不让黑盒模型直接接管默认写入、检索、压缩或注入。
- 机器学习先当评估器和候选生成器，不当默认事实裁判。

## 3. 最值得强调的产品优势

### 3.1 它不是“记忆”，是协作连续性

Claude Code 写下的项目边界，Codex 下一轮能继续用；Qoder/Wukong/Hermes 可以按各自边界读取同一个脑池。用户换 Agent 不等于换脑。

### 3.2 它不是“自动总结”，是证据和结论分离

raw conversation、resource、extraction、MemoryItem 是不同层。证据可以长期保留，结论可以被治理，注入可以被防火墙过滤。

### 3.3 它不是“向量库”，是可信上下文装载

BM25/vector/RRF/rerank/decay/feedback/graph 只是召回机制。真正的价值在 context pack、detail_uri、retrieval trace、firewall 和 benchmark gate。

### 3.4 它不是“多工具插件”，是 adapter truth contract

AMH 明确区分 file、hook、MCP、provider tools，明确区分 install-ready 和 verified。这个克制是产品可信度的一部分。

### 3.5 它不是“越智能越好”，是 gate-first

Headroom、ML/DL、GraphRAG、Hopfield、semantic contradiction 都必须先证明不丢证据、不升风险、不破坏默认链路。

## 4. README 重构依据

README 应该按以下顺序表达：

1. 首屏说清定位：跨智能体可信上下文操作系统。
2. 直接给硬指标：28 MCP tools、89 Web routes、16 adapters、13 verified、2 install-ready、1 wip、1483 tests。
3. 用一条链解释价值：capture -> write -> project -> retrieve -> pack -> firewall -> govern -> verify -> adapt。
4. 明确“不是”：不是 transcript 垃圾场，不是单纯向量 RAG，不是所有 adapter 都 verified，不是 ML/DL 默认接管。
5. 把核心优势写成“为什么难”：事实源、写漏斗、证据分层、可逆压缩、防火墙、治理、质量门禁。
6. 保留快速安装、常用命令、架构图谱索引、adapter 支持矩阵、真实边界。
7. 对尚未完成的能力诚实：OpenClaw/Qoder 尚未 verified、bulk import 写漏斗仍需收敛、resource 主链路仍需更强、L2/L3 是 foundation/planned。

## 5. 仍需增强什么

### P0：正确性和可信声明

1. 把 CLI/MCP bulk import 和治理内部直接写入路径迁移到 `WriteService`，或建立严格 allowlist。
2. 将 Codex / Claude Code 优先推进到 verified，并把 verification record 写入 capability matrix。
3. 持续用测试锁住 `memory api-docs`、README、架构图谱与实际 Web 89 routes 的一致性。

### P1：产品化诊断

1. Resource sidecar 进入更完整的 search/context/read-hint 工作流。
2. Web 继续增强 retrieval trace、context pack、firewall、recall gap、resource evidence 和三日数据流转的可视化。
3. Review queue 变成真正的日常工作面：批量 approve/reject、引用证据、记录操作原因。
4. Adapter onboarding 增加“如何从 install-ready 到 verified”的分步证据采集。

### P2：平台化和 ML/DL

1. L2 team brain：同步、冲突、权限、多人审计。
2. L3 enterprise：RBAC、policy plane、deployment model、audit admin。
3. ML/DL 作为评估器、聚类器、候选生成器、离线 rerank 对比器，默认链路仍需 gate。
4. Benchmark/release gate 从可执行工具升级为发布前固定流程。

## 6. 最终结论

AMH 当前已经具备 L1 个人多智能体共享大脑的核心闭环：写、存、搜、读、注入、防火墙、反馈、治理、演化、审计、Web 管理和多 adapter 接入。

下一阶段最重要的不是继续堆新词，而是把“可信上下文操作系统”这个内核打磨到极致：

- 每次写入都有同一漏斗。
- 每次召回都有解释。
- 每次压缩都能还原。
- 每次注入都过防火墙。
- 每次增强都过 benchmark gate。
- 每个 adapter 都用真实证据从 install-ready 推进到 verified。

README 的新表达必须服务这个判断：AMH 的革命性不在于“记住更多”，而在于让多个智能体共享同一个可治理、可追溯、可验证的工作记忆，并且让下一次协作能从真实上下文继续出发。
