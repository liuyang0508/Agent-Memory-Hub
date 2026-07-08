# AMH 存储生命周期：每个目录存什么、何时存、何时取

这份文档只讲用户数据根目录，默认是：

```bash
~/.agent-memory-hub/
```

如果设置了 `BRAIN_DIR=/path/to/brain`，下面所有路径都换成 `$BRAIN_DIR/...`。

一句话：**原始证据、长期知识、派生索引、运行账本分开存**。这样做的原因是：证据不能丢，长期记忆不能被 transcript 噪声污染，索引坏了可以重建，自动治理不能绕过审计。

## 目录地图

| 路径 | 存什么 | 什么时候写入 | 什么时候读取 | 怎么读取 |
|---|---|---|---|---|
| `items/mem-*.md` | 长期知识条目。包含 frontmatter、正文、refs、validity、retention、maturity、context views。 | Agent 主动写 `write-memory.sh`、CLI/MCP/SDK/Web/Hermes 写入、`memory harvest` 抽取候选后，经 `WriteService` 写入。 | 搜索结果命中后、`memory read`、Web 详情、治理扫描、reindex。 | `memory read <id> --view detail --head 2000`；源码是 `ItemsStore`。 |
| `items/archived/` | 被归档的旧记忆或治理动作产物。默认不进入普通扫描。 | `batch-archive`、evolve/archive、malformed repair。 | 审计、恢复、人工追溯。 | `memory read <id>` 仍可按子目录解析；普通 `iter_all()` 默认跳过 archived。 |
| `sources/conversations/<conv>/messages.jsonl` | 原始对话证据，message 级别保存 role、正文、hash、source path、offset、tier。 | `UserPromptSubmit` 写当前用户 prompt 的 `live-prompt` 防丢记录；`Stop` 如果有 `transcript_path` 则导入完整 transcript，这是权威原始对话；`memory conversation ingest` / `memory harvest` 也会写。 | 查看原始对话、harvest 抽取、conversation rebalance、证据回放。默认不直接注入 prompt。 | `memory conversation list`；`memory conversation read <conv-id> --head 20`。 |
| `sources/writes/<item-id>.json` | 每条长期记忆的写入账本：谁写的、何时写、正文 hash、refs、validity、source kind。 | `WriteService.write()` 成功写入 `items/` 后同步写。 | provenance、Web/诊断、追溯某条 item 从哪个写入入口来。 | 直接读 JSON；也可从 Web/诊断页展示。 |
| `resources/res-*.json` | 外部或本地资料的资源登记：文件、PDF、图片、网页、write input 等资源的 uri、hash、mime、大小、tags。 | `WriteService` 遇到 `refs.files` 会镜像本地文件；没有 extraction 时会把写入正文作为 write-input resource 证据。 | 证据诊断、resource context、Web/SDK include_resources。 | `ResourceStore.iter_resources()`；搜索接口可打开 resource context。 |
| `extractions/ext-*.json` | 从 resource 抽取的文本、OCR/ASR/摘要/片段等证据。 | `WriteService` 从文本文件或 write input 生成；导入服务也可生成。 | 解释某条记忆的证据来源、补充 resource context、后续检索增强。 | `ResourceStore.iter_extractions()`；通过 item 的 `refs.extractions` 追溯。 |
| `index.db` | 派生索引：元数据、全文索引、向量、引用图谱。 | `WriteService` best-effort upsert；`memory reindex` 重建；repair 操作修复。 | `memory search`、hook 自动注入、Web 搜索、MCP/SDK search。 | `memory search "query" --explain --format text`；源码是 `HubIndex` + `Retriever`。 |
| `.index-dirty` | index 更新失败时的 item id 行式日志。 | Markdown 已成功写入，但 index/embedder 不可用或 SQLite 写失败时。 | 后续 `sync-pending` / `reindex` / doctor 修复索引漂移。 | `memory reindex` 或维护命令；人工可直接查看。 |
| `pending/*.jsonl` | 写入口降级缓冲。Python 或完整写入链不可用时，先把待写 item 记录下来。 | hook shim 或写入入口无法调用 `WriteService` 时。 | `memory sync-pending` 重放；失败多次进入 `pending/dead/`。 | `memory sync-pending`；源码是 `PendingQueue.replay()`。 |
| `runtime/adapter-events.jsonl` | hook 是否真实跑过的机械证据。只记录 adapter、event、session、cwd，不存 prompt/body。 | SessionStart、UserPromptSubmit、Stop、PreCompact、PostCompact、SubagentStart、SubagentStop hook 执行时。 | adapter doctor、verified gate、运行状态诊断。 | `memory adapter doctor <adapter> --format json`；源码是 `runtime_events.py`。 |
| `runtime/injection-cohorts.jsonl` | 某次自动注入最终进入上下文的 item id 集合和 pack metrics。不会存 prompt 正文。 | `inject-context.sh` 调 `memory search --record-injection-cohort` 且有结果时。 | 分析哪些记忆被注入、哪些被反馈采纳/拒绝。 | runtime 诊断、Web trace、`latest_injection_cohort()`。 |
| `runtime/recall-gaps.jsonl` | 召回缺口：没有结果、query 太弱、候选被 firewall 拒绝、图片/音频缺少 OCR/ASR 文本等。 | hook/search 开启 `--record-recall-gap` 且没有可注入上下文时。 | 发现“为什么没召回”、训练 benchmark/治理候选。 | `memory hook recent --limit 5` 看最近 hook 结果；`memory recall-drift ...` 做批量治理；源码是 `recall_events.py`。 |
| `runtime/task-outcomes*.jsonl` | 任务结果和注入反馈：哪些记忆有用、哪些误导。 | 用户/系统记录 outcome 或 injection feedback 后。 | 召回 value weighting、负反馈 quarantine、质量报告。 | recall drift/report、feedback tooling。 |
| `.harvest/state.json` | transcript harvest 水位线：每个 transcript 读到哪个 byte offset。 | `memory harvest` 扫 transcript 后更新。 | 下次 harvest 断点续跑，避免重复抽取。 | `WatermarkStore` 自动读写。 |
| `.session-flags/` | Stop hook 去重 flag。 | `session-end-signal.sh` 第一次看到一个 session 时写。 | 防止每个 turn 都写 session-active signal。 | 通常无需手动读；`memory gc` 可清理。 |
| `review/proactive-candidates.jsonl` | 语义记忆候选，还没批准，不是正式长期记忆。 | proactive/semantic 候选生成时。 | Web/CLI review 审核。批准后才经 `WriteService` 写入 `items/`。 | review/candidate UI 或相关 CLI。 |
| `derived/hierarchical-memory.json` | L2/L3 层级摘要 sidecar。可重建，不篡改 `items/`。 | `memory govern hierarchy --apply`。 | Web 层级视图、概览检索、主题导航。 | `memory govern hierarchy --format summary`；Web `/api/hierarchical-memory`。 |
| `playbook/` | 纪律、hooks、rules、skills、SOP 等操作手册素材。 | 安装包或 playbook 导入时。 | onboarding、agent-facing discipline、工具说明。 | `agent_runtime_kit/tools/list-playbook.sh` 或 Web/安装器。 |
| `items.backup-*` / `items.dogfood-*` | 备份、dogfood 快照或迁移前副本。 | 迁移、修复、实验前。 | 回滚、人工审计。 | 直接文件系统查看，不参与默认检索。 |

## 一条模拟对话串完整链路

下面用一次真实工作流说明：谁写、写到哪里、下一次怎么取。

### 0. 会话开始

用户打开 Claude Code 或 Codex，AMH 的 `SessionStart` hook 运行。

AMH 做两件事：

1. 注入记忆纪律：告诉 Agent 什么时候该写长期记忆。
2. 记录运行证据：写一行到 `runtime/adapter-events.jsonl`，证明这个 adapter 的 hook 真跑过。

不会写用户 prompt，也不会写长期记忆。

### 1. 用户第一次提问

用户说：

```text
以后这个项目导出的 CSV 都要兼容 macOS Excel，统一用 utf-8-sig。
```

`UserPromptSubmit` 触发。

AMH 同时走两条链：

1. **防丢链**：把这句用户输入写入 `sources/conversations/<conv>/messages.jsonl`，标签是 `live-prompt`。这只是保险，不是完整 transcript。
2. **召回链**：用这句话检索 `index.db`。如果找到相关 `items/`，会把压缩后的 context pack 注入给 Agent，并把注入过的 item ids 写到 `runtime/injection-cohorts.jsonl`。如果没找到，会把缺口写到 `runtime/recall-gaps.jsonl`。

此时还不会自动写一条长期记忆，因为用户只是说了一句话，Agent 还没有形成可复用结论。

### 2. Agent 做出值得沉淀的决策

Agent 检查代码后发现：

```text
macOS Excel 打开 CSV 时，utf-8 可能乱码；统一 utf-8-sig 能兼容 Excel。
```

这属于“非显然技术决策”，Agent 应该调用：

```bash
echo "**决策**：CSV 导出统一使用 utf-8-sig。

**理由**：macOS Excel 对无 BOM utf-8 CSV 兼容性不稳定，utf-8-sig 可以降低乱码概率。

**改回去的代价**：如果改回 utf-8，需重新验证 Excel 打开行为，并可能影响已有用户导出。" | \
  agent_runtime_kit/tools/write-memory.sh \
  --type decision \
  --title "CSV 导出统一使用 utf-8-sig" \
  --summary "macOS Excel 兼容场景下，项目 CSV 导出统一使用 utf-8-sig。" \
  --tags "csv,encoding,excel" \
  --project "agent-memory-hub" \
  --agent "claude-code" \
  --session "sess-001"
```

这次写入只走一个漏斗：`WriteService`。

`WriteService` 做这些事：

1. 跑 schema 和 audit gate。
2. 补字段、质量 warning、边界 review tag。
3. 生成 resource/extraction sidecar：如果没有外部证据，至少把 write input 存成 `resources/` + `extractions/` 证据。
4. 写 `items/mem-*.md`，这是长期知识事实源。
5. 写 `sources/writes/<item-id>.json`，这是这次写入的账本。
6. best-effort 写 `index.db`。失败不撤销 Markdown，只追加 `.index-dirty`。

所以一次长期记忆写入后，通常会看到：

```text
items/mem-*.md
sources/writes/<item-id>.json
resources/res-*.json
extractions/ext-*.json
index.db
```

### 3. 会话结束或一个 turn 停止

`Stop` hook 触发。

如果 hook payload 里有 `transcript_path`，AMH 会把完整 transcript 导入：

```text
sources/conversations/<conv>/messages.jsonl
```

这才是**权威原始对话**。如果 transcript 里包含和 `live-prompt` 内容相同的用户消息，AMH 会删除那条重复的 `live-prompt`，只保留 transcript 版本。

`Stop` 还会用 `.session-flags/<session-id>` 去重，每个 session 只写一次 `session-active` signal，避免每个 turn 都污染 `items/`。

### 4. 第二天换成 Codex 继续

用户对 Codex 说：

```text
这个项目 CSV 编码之前怎么定的？
```

`UserPromptSubmit` 再次触发。

AMH 先把这句作为 `live-prompt` 防丢写入 `sources/conversations/`，然后执行召回：

1. `query_signal` 判断这不是噪声查询。
2. `Retriever` 从 `index.db` 做 BM25 + vector 检索。
3. RRF 融合全文和向量排名。
4. 叠加 rerank、遗忘衰减、反馈价值、运行证据、stale/supersession filter。
5. `context_firewall` 过滤不适合注入的候选。
6. 输出 context pack，包含 locator/overview/detail hint。
7. `runtime/injection-cohorts.jsonl` 记录这次真正注入了哪个 item。

Codex 看到的不是完整 transcript，而是类似：

```text
[decision] CSV 导出统一使用 utf-8-sig
summary: macOS Excel 兼容场景下，项目 CSV 导出统一使用 utf-8-sig。
retrieve="memory read mem-... --view detail --head 2000"
```

如果 Codex 需要证据正文，它再按 hint 读取：

```bash
memory read mem-20260622-... --view detail --head 2000
```

这一步读的是 `items/mem-*.md`，不是 raw transcript。

### 5. 用户想看原始对话

如果用户不放心，想看这条记忆到底来自哪次对话：

```bash
memory conversation list --agent claude-code
memory conversation read <conversation-id> --head 20
```

这读的是：

```text
sources/conversations/<conv>/messages.jsonl
```

如果想看某条长期记忆的写入账本：

```bash
cat ~/.agent-memory-hub/sources/writes/<item-id>.json
```

如果想看它背后的证据 sidecar：

```bash
rg "<item-id>" ~/.agent-memory-hub/resources ~/.agent-memory-hub/extractions
```

### 6. 索引坏了也不怕

如果 `index.db` 损坏、丢失或向量模型不可用：

- `items/mem-*.md` 仍然是事实源。
- 写入不会因为索引失败而丢失。
- `.index-dirty` 会记录需要修复的 item。

修复方式：

```bash
memory reindex
memory sync-pending
memory doctor --offline
```

### 7. 记忆变老、冲突或质量低

后续治理不会直接删除事实源。

常见动作是：

- 低边界候选：加 `needs-review`，进入 review queue。
- 被证明有用：提高 feedback/value。
- 被证明误导：记录 outcome feedback，降低召回权重。
- 过期或低价值：提出 archive proposal，或移到 `items/archived/`。
- L2/L3 层级摘要：写 `derived/hierarchical-memory.json`，不改原 item。

## 用户最常用的检查命令

```bash
# 1. 看长期记忆有没有写进去
ls ~/.agent-memory-hub/items | tail

# 2. 搜长期记忆，并看召回解释
memory search "CSV 编码 Excel" --explain --format text

# 3. 读某条长期记忆正文
memory read <item-id> --view detail --head 2000

# 4. 看原始对话证据
memory conversation list
memory conversation read <conversation-id> --head 20

# 5. 看某条 item 的写入账本
cat ~/.agent-memory-hub/sources/writes/<item-id>.json

# 6. 看资源和抽取证据
find ~/.agent-memory-hub/resources ~/.agent-memory-hub/extractions -type f | tail

# 7. 修复 pending / index
memory sync-pending
memory reindex
memory doctor --offline
```

## 最重要的边界

- **原始对话**在 `sources/conversations/`，不是长期记忆。
- **长期记忆**在 `items/`，由 `WriteService` 写入和治理。
- **证据旁路**在 `sources/writes/`、`resources/`、`extractions/`。
- **检索加速**在 `index.db`，它可重建，不是事实源。
- **运行诊断**在 `runtime/`，它记录系统行为，不记录完整正文。
- **失败兜底**在 `pending/` 和 `.index-dirty`，用于恢复，不是最终状态。

因此用户可以放心：AMH 不是把所有聊天粗暴塞进 prompt，而是把原始证据、可复用结论、索引投影和运行诊断分层管理。
