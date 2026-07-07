# Agent Memory Hub 能力地图

日期：2026-06-10
更新：2026-06-18 增补原始对话证据层和 conversation MCP 工具
Owner：Codex
状态：当前仓库事实梳理，不是发布承诺

## 证据来源

本梳理基于当前 `main` 的只读检查：

- `git status --short --branch`：`## main`
- `python -m agent_brain.cli --help`
- `python -m agent_brain.cli adapter list --format json`
- `python -m agent_brain.cli api-docs`
- `tests/conformance/test_public_surface_lock.py`
- `tests/conformance/test_web_surface_lock.py`
- `docs/architecture.md`
- `tests/unit/test_docs_truth_contract.py`

## 能力分级

| 等级 | 含义 |
|---|---|
| 稳定核心 | 有代码入口、测试锁定或 RC 验证证据，适合作为当前产品能力描述 |
| install-ready | 安装路径和测试存在，但真实客户端端到端 verified 证据不足 |
| 实验/PoC | 有实现或命令入口，但更适合标为演进能力，不应包装成稳定主路径 |
| wip | 只有 adapter stub、规划或未完成路径 |
| docs-only | 有文档配置示例，当前 adapter registry 未把它作为可安装能力 |

## 一句话定位

Agent Memory Hub 是一个本地优先的多 Agent 共享大脑：用 Markdown memory item
做源事实，派生 SQLite/FTS/vector 索引，通过 CLI、MCP、hook、Web Admin 和多 Agent
adapter 让 Claude Code、Codex、Qoder、Wukong 等工具共享记忆、交接上下文和治理状态。

## 核心数据能力

| 能力 | 当前状态 | 说明 |
|---|---|---|
| Markdown brain pool | 稳定核心 | `~/.agent-memory-hub/items/mem-*.md` 是 durable source of truth |
| 6 种 memory type | 稳定核心 | `fact` / `episode` / `decision` / `artifact` / `signal` / `handoff` |
| 统一 schema/frontmatter | 稳定核心 | 支持 id、agent、session、project、tags、sensitivity、refs、tenant/RBAC 预留字段 |
| 单一写入漏斗 | 稳定核心 | MCP、CLI、hook、pending replay、harvest 最终都经 `WriteService` |
| 写前 audit gate | 稳定核心 | critical/high 风险默认 fail-closed，需显式 `--allow-unsafe` |
| Markdown 写入为唯一成功判据 | 稳定核心 | index/embedder 失败只降级，不否定已落盘的 Markdown 写入 |
| pending 缓冲 | 稳定核心 | core 写路径不可用时落到 pending queue，后续 `sync-pending` 回放 |
| malformed item repair/skip | 稳定核心 | 扫描异常 item 时记录并隔离，不让坏 item 拖垮全池 |
| 原始对话证据层 | 稳定核心 | `sources/conversations/*/messages.jsonl` 保存 message 级证据，`items/` 仍只放长期知识结论 |
| 原始对话冷热治理 | 稳定核心 | `memory conversation rebalance` 用 half-life、访问次数、importance 和时间衰减计算 hot/warm/cold/frozen tier |

## 检索与上下文能力

| 能力 | 当前状态 | 说明 |
|---|---|---|
| BM25 + vector RRF 搜索 | 稳定核心 | CLI `search` 和 MCP `search_memory` 使用混合检索 |
| CJK/ASCII token 处理 | 稳定核心 | 面向中英文混合 memory 的 FTS query/token helper |
| query synonym expansion | 稳定核心 | 局部同义词扩展，避免把所有语义硬编码到 prompt |
| graph-neighbor expansion | 稳定核心 | 利用 `refs.mems`、manual link、wiki link 等关联增强检索 |
| decay / confidence 排序 | 稳定核心 | 用 retention decay 与 confidence 辅助排序和健康判断 |
| MMR 多样性 rerank | 稳定核心 | 减少 top-K 结果重复 |
| optional cross-encoder rerank | 实验/可选 | 模型可用时启用，不作为离线基本路径 |
| tag suggestion | 稳定核心 | 基于相似 item 和 indexed tag metadata 推荐标签 |
| brief resume | 稳定核心 | token-budgeted resume briefing，适合新会话接手前先读 |
| adapter runtime evidence boost | 稳定核心 | 仅在 query 同时包含 adapter 名称与 runtime/hook/event 语义时触发 |

## 自动注入与工作流能力

| 层 | 能力 | 当前状态 |
|---|---|---|
| L1 | SessionStart 注入记忆纪律和活跃 signal | Claude Code 路径稳定；Codex 通过 AGENTS/hooks 适配 |
| L2 读 | UserPromptSubmit 按关键词注入 top-K 相关 memory | 稳定核心 |
| L2 写 | Stop hook 写 session-end signal | 可选兜底，不等同智能归档 |
| L3 | `/remember [hint]` 智能扫描、列计划、确认后写入 | Claude Code slash 路径可用 |
| 主动写 | Agent 遇到 decision/fact/signal/episode/artifact 触发器主动写 | 依赖 discipline 和 agent 执行纪律 |

## CLI 能力

当前 CLI 是 Typer app，主要能力族：

| 能力族 | 命令 |
|---|---|
| 图谱链接 | `link` / `unlink` / `graph` |
| 查询读取 | `read` / `search` / `list-recent` / `tag-suggest` / `brief` |
| 写入变更 | `write` / `update` / `delete` / `confirm` / `batch-confirm` / `batch-archive` |
| 维护修复 | `doctor` / `reindex` / `verify` / `sync-pending` / `gc` / `migrate` |
| transcript / conversation 导入与治理 | `harvest` / `conversation ingest` / `conversation list` / `conversation read` / `conversation rebalance` |
| 治理演进 | `govern auto` / `govern maturity` / `consolidate` / `evolve` / `dream` / `decay-status` / `anti-drift` |
| 导入导出 | `export` / `import` / `obsidian-export` / `obsidian-import` |
| 可观测性 | `stats` / `health` / `inspect` / `version` / `api-docs` |
| 服务端 | `serve` |
| 子命令组 | `audit` / `govern` / `tier` / `entity` / `adapter` |

## MCP 能力

MCP operation surface 当前被 conformance test 锁定为 27 个操作工具；实际注册工具数为 28，
额外 1 个是 `get_usage_guide` onboarding fallback：

| Tier | Count | Tools |
|---|---:|---|
| core | 10 | `write_memory` / `tag_suggest` / `search_memory` / `read_memory` / `list_recent` / `delete_memory` / `update_memory` / `confirm_memory` / `brain_stats` / `brief_memory` |
| governance | 6 | `audit_skill` / `audit_outbound` / `drift_check` / `govern` / `batch_confirm` / `batch_archive` |
| io | 5 | `export_memory` / `import_memory` / `obsidian_export` / `obsidian_import` / `gc_memory` |
| graph | 3 | `graph_memory` / `link_memories` / `unlink_memories` |
| evolve | 1 | `evolve_memory` |
| conversation | 2 | `list_conversations` / `read_conversation` |

## Web Admin / API 能力

`python -m agent_brain.cli api-docs` 当前列出 55 个 endpoint；surface lock 测试
覆盖 68 个 API/WS route 组合。能力可以按域理解：

| 域 | 能力 |
|---|---|
| Auth | 初始化管理员、登录、注册、用户列表、当前用户、API key rotate |
| Items | 列表、创建、详情、字段更新、正文更新、删除、touch、clone、pin、history |
| Batch | batch delete、batch confirm、batch tag、batch update、merge |
| Search | semantic search、full-text body search、related items |
| Metadata | projects、tags、tag rename、tag delete |
| Graph | full graph、item neighbors、manual link/unlink |
| Governance | stats、decay-status、activity、health-detail、audit、outbound audit、audit scan |
| IO | JSON export/import、CSV export、Markdown ZIP export、Obsidian import/export |
| Ops | backup/list/restore、reindex、gc、evolve |
| Realtime | SSE `/api/events`、WebSocket `/ws/events`、webhooks |
| Adapter view | `/api/adapters/capabilities` 暴露 truth-contract records |

## Adapter 能力

adapter truth-contract 当前按 `verified` / `install-ready` / `wip` 分层；runtime
事件只作为证据增强，不会自动把 adapter 升级为 `verified`。

| Adapter | 当前等级 | Modes | 主要边界 |
|---|---|---|---|
| `aider` | install-ready | file | 本地 Aider config 缺失，runtime 未验证 |
| `claude_code` | install-ready | command / hook / mcp | 真实 config doctor 通过，runtime hook event 未记录 |
| `cline` | install-ready | mcp | 本地 Cline MCP config 缺失，runtime 未验证 |
| `codex` | install-ready | file / hook / mcp | 已观测到 hook runtime event，但 verified 仍为 false |
| `continue_dev` | install-ready | mcp | 官方全局 `config.yaml` 路径实现，runtime 未验证 |
| `cursor` | install-ready | mcp / hook | 本地 Cursor MCP config malformed，runtime 未验证 |
| `github_copilot` | install-ready | file | 仓库级 custom instructions 安装器实现，runtime 未验证 |
| `qoder` | install-ready | file / hook | 官方 hooks settings 与 awareness 路径实现；AMH 上下文有效性未验证 |
| `wukong` | install-ready | file / hook | 本地 Wukong context 缺失，runtime 未验证 |
| `aone_copilot` | install-ready | file | IntelliJ IDEA plugin awareness sidecar 已实现；插件运行时工具桥未验证 |
| `qoder_work` | verified | file / hook | QoderWork CLI smoke 证明模型看见 `<agent_brain>`，且同一 session 有 AMH injection cohort |

文档里还存在 Claude Desktop、Goose 等 MCP 配置示例，但它们不是当前 adapter
registry 的 install-ready 记录，应按 `docs-only` 对待。

## 治理与演进能力

| 能力 | 当前状态 | 说明 |
|---|---|---|
| governance pipeline | 稳定核心 | 检查 duplicate、noise、TTL、quality 等问题 |
| drift detection | 稳定核心 | stale、contradiction、citation rot 等风险发现 |
| maturity scoring | 稳定核心 | `memory govern maturity` 基于来源、支持、复用、图引用、反馈、矛盾和 stale scope 给出 raw/consolidated/skill 推荐 |
| auto-governance cycle | 稳定核心 | `memory govern auto` 把 maturity、governance issues、drift、evolve proposals、conversation rebalance 和 index drift 汇总为 dry-run/apply 计划；`--apply` 只执行 maturity、conversation tier 和 index repair 等 safe actions |
| conflict resolver | 实验/演进 | 支持 keep-newer、keep-higher-confidence、mark-contested、merge-resolution 等策略 |
| consolidation | 实验/演进 | L0 raw fact 到 L1 consolidated item，非破坏式 |
| semantic compression | 实验/演进 | 候选发现、LLM/机械压缩、L2 写回与 source supersession |
| tiering | 稳定核心 | hot/warm/cold 分层与 rebalance |
| active recall / preference inference | 实验/演进 | 从历史 item 推断偏好、上下文和 recall payload |
| dreaming cycle | 实验/演进 | pattern -> policy -> skill 的后台整合流程 |
| skill synthesis proposal | 实验/演进 | 从成熟策略或重复模式提出 skill 化建议，不等于自动上线技能 |
| retrieval ablation gate | 稳定核心 | `benchmark_relevance.py --ablation` 比较 bm25/vector/RRF/decay/graph/MMR/context-firewall 变体，并输出 token cost/stale hit rate；Hopfield/HRR 等研究排序改动必须先过评测 |

## Harvest / 导入导出 / 集成能力

| 能力 | 当前状态 | 说明 |
|---|---|---|
| Claude Code transcript harvest | 稳定核心 | offset watermark、span hash dedup、secret redaction、offline-first |
| optional LLM enrichment | 可选增强 | 模型不可用时 clean no-op，不阻断 raw record |
| JSON/JSONL export/import | 稳定核心 | CLI、MCP、Web 均有入口 |
| Obsidian export/import | 稳定核心 | 支持 vault markdown 互通 |
| Markdown ZIP / CSV export | Web 稳定核心 | Web API 支持 CSV 和 Markdown ZIP |
| Backup/restore | Web 稳定核心 | 管理员 API 创建、列出、恢复备份 |

## SDK / Hermes / Reasoning 能力

| 域 | 能力 |
|---|---|
| Python SDK | `MemoryClient` facade：write/search/read/feedback/confirm/stats/brief |
| Hermes provider | 兼容 Hermes 工具注册、search、remember、governance、import/export、item tools |
| Knowledge graph | manual link、frontmatter refs、wiki-link edge、neighbors 查询 |
| Causal reasoning | 显式因果、隐式时间因果、相关 decision、likelihood scoring、cross-session trace |

## 当前验证快照

v1.1 Polish RC 收口时的关键验证：

- `git diff --check`：通过
- `python -m compileall -q agent_brain`：通过
- 扩展 pytest 集合：`306 passed, 12 warnings`
- retrieval benchmark：`memory_risk.mrr=1.0`、`runtime_evidence.mrr=0.5`、`mean_recall_at_10=1.0`
- adapter truth-contract：12 个 adapter；未验证的不冒充 verified

## 明确边界

- 当前没有 GitHub remote，不应建议 push/release/上架。
- 不声明“无任何 bug”；只能声明在已列验证门槛下未发现阻塞问题。
- `install-ready` 不等于真实客户端端到端 verified。
- Aone Copilot 和 QoderWork 已是 verified；OpenClaw、Qoder 仍是 install-ready；MuleRun 仍是 wip。
- 文档版本号和 README 历史措辞可能滞后；能力判断以当前代码、测试和 RC 验证为准。
- Web Admin API 有 surface lock 和单元验证，但本轮没有做浏览器视觉回归。
- 论文思想、Hopfield/HRR 等目前主要落在评估门槛和窄 boost，不应描述成完整新记忆算法已落地。
