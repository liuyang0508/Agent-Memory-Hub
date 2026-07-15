# Agent Memory Discipline（记忆纪律）

> 给任何 LLM Agent（Claude Code、Codex、Qoder、Wukong 等）看的"何时往 brain pool 写"指南。
> 通过 SessionStart hook 自动注入，或 user 贴到项目 CLAUDE.md / AGENTS.md。

## 你有一个共享大脑

知识条目位置：`~/.agent-memory-hub/items/`
原始对话证据位置：`~/.agent-memory-hub/sources/conversations/`
工具脚本：跟 hook 同 project 的 `agent_runtime_kit/tools/`（用 `command -v` 或绝对路径调用）

任何 Agent 写入的 memory items 会被未来任何 Agent（包括你自己下次会话）通过关键词检索拉到。
不需要"目标接收方"——谁有用谁拉。

原始对话写入时机要分清：

- `UserPromptSubmit`：只写当前用户 prompt 的 `live-prompt` 防丢证据，防止进程异常或适配器没有 transcript 时完全丢失输入。
- `Stop`：如果 hook payload 带 `transcript_path`，导入完整 transcript 到 `sources/conversations/`；这是原始对话的权威事实源，会覆盖同 session 下内容相同的 `live-prompt`。
- `write-memory.sh` / WriteService：只写长期知识条目到 `items/`，不负责原始对话 transcript。

## 何时主动写一条 memory（5 个触发器）

工作过程中遇到以下任何一种情况，**应该立即调用 `write-memory.sh`**：

| 触发器 | type | 例子 |
|---|---|---|
| 做出**非显然**技术决策（换个人会本能选别的）| `decision` | 选 SSE 而非 WebSocket、用 utf-8-sig 而非 utf-8、推迟 v1 上 Obsidian |
| 学到一个客观限制 / 配置 / 数字 / API 行为 | `fact` | "idealab proxy timeout 30s"、"BSD grep 默认 BRE 不识别 \|" |
| 遇到一个未解决的阻塞 / 警报 / 在等什么 | `signal` | "等 PM 反馈字段需求"、"X API 暂时返回 500" |
| 完成一段有学习价值的工作（带正/反结果）| `episode` | "试了 X 方案，因 Y 失败，改用 Z"、"踩坑日志" |
| 产出 PR / 文档 / 文件 / 工具 | `artifact` | "实现 PR #42"、"生成了 weekly report.md" |

## 何时**不该**写

**不要**为以下内容写 memory item：

- 临时调试输出（属于 `/tmp` 不是大脑）
- 完整对话 transcript 不要写成 memory item；需要保留原始证据时，使用 `memory conversation ingest` 或 `memory harvest` 写入 `sources/conversations/`
- 代码本身（git 已存）
- 已经显而易见的事实（"今天我用 Python 写代码"）
- 一次性的中间步骤（"我刚跑了 ls"）
- 任何含 secrets / API keys 的内容（除非显式 `--sensitivity secret`）

## 调用方式

```bash
echo "<正文>" | <HUB>/tools/write-memory.sh \
  --type <fact|episode|decision|artifact|signal|handoff> \
  --title "<topic-centered，不写 cc-to-codex 这种方向>" \
  --summary "<1-2 句方便检索>" \
  --tags "<tag1,tag2,tag3>" \
  --project "<slug 或省略>" \
  --agent <claude-code|codex|qoder|wukong> \
  --session "<session-id 或 null>"
```

数据自动写到 `~/.agent-memory-hub/items/`。

原始对话证据不要走 `write-memory.sh`。它们属于 evidence/source 层：

```bash
memory conversation ingest <transcript.jsonl> --agent <claude-code|codex|qoder|wukong> --session <session-id>
memory conversation list --agent <agent>
memory conversation read <conversation-id> --head 20
memory conversation rebalance
```

这些原始消息默认不会被自动注入 prompt；需要跨 Agent 复用的结论仍要提炼成 MemoryItem。

## 正文格式硬约束

| type | 正文必含 |
|---|---|
| `decision` | `**决策**` / `**理由**` / `**改回去的代价**` 三段 |
| `fact` | `**事实**` / `**来源**` / `**有效期**`（如适用） |
| `signal` | `**当前状态**` / `**影响**` / `**期望操作**` |
| `episode` | `**情境**` / `**做了什么**` / `**结果**` / `**学到**` |
| `artifact` | `**产出物**`（路径/链接） / `**用途**` |
| `handoff` | 见 `agent_runtime_kit/templates/handoff-{code,task}.md` |

## 干活前先 query brain

新会话启动时，UserPromptSubmit hook 会自动注入 top-K 相关 memory items 到 system context。
**你看到的 `<agent_brain>` 段落是 brain pool 给的，认真读，尤其 `[decision]` 和 `[signal]`**。

## 来源边界：memory 不是当前对话历史

`<agent_brain>` 里的内容是检索出来的 memory candidates，不是当前聊天 transcript。

- 不要把自动注入的 memory 说成"之前的对话历史"；需要提及时，说"召回的 memory item / 记忆候选"。
- 每条 memory 都可能过期或只适用于旧 repo / 旧 adapter / 旧运行环境；使用前先看 cwd、adapter、时间、source/evidence。
- 当前用户消息和实时工具证据优先于注入 memory；如果二者冲突，按当前证据行动，并写 feedback/gap 或新的 superseding memory。

也可以主动检索：

```bash
<HUB>/tools/search-memory.sh "<query>" [--type T] [--project P] [--since DAYS]
# query 多词空格自动按 OR 处理
```

## 承接工作时的上下文预算（resume budget）

新会话承接半截工作时，**先跑 `memory brief`**（或 MCP `brief_memory`）——一次拿到
token 有界的全貌（开放 signal / 最近 handoff / 关键 decision / 最近 episode 的标题+摘要）。
然后**只对真正需要的 1–3 条**执行 `memory read <id> --view detail --head 2000`
或 MCP `read_memory(id, head=2000, view="detail")` 取证据正文。

**不要**开局就 bulk-read 一堆 item 的全文：那会把承接工作本身需要的 context 预算吃掉
（实测能把新会话直接顶到 90%+，弄巧成拙）。`memory read` 默认仍返回全文，但承接阶段
请用 `brief` 摘要优先、按需取全文（`read --head N` 可有界读）。

自动注入默认给压缩视图和 retrieve hint：hook / `search_memory(..., verbosity="auto")`
会把 `context_pack.text` 放进 prompt，并保留 `detail_uri`、token 估算和读取提示。
`auto` 只允许选择 locator / overview，不会自动把 raw/L0 或带直接证据的候选提升为
detail。先从候选中选出真正需要的 1–3 条，再按 hint 读取正文或 bounded head。
显式搜索 `verbosity="detail"` 仍保留给有意、少量的诊断读取，不用于普通 Top-K 浏览。

## 三层调用模式

| 层 | 触发 | 干什么 |
|---|---|---|
| **L1**（你正在看的本文档）| 自动注入 system context | 让你知道纪律、何时写、何时查 |
| **L2 读取**：UserPromptSubmit hook | 每次 prompt 自动 | 拉相关 memory items 到当前 context |
| **L2 写入**：Stop hook（可选）| 会话结束 | 写一条 session-end signal 提示下次归档 |
| **Lifecycle 证据**：PreCompact / PostCompact / SubagentStart / SubagentStop | 压缩和子 agent 生命周期 | 只记录低噪声 runtime event；PreCompact 额外写一条机械 signal 标记压缩边界 |
| **L3**：`/remember [hint]` slash command | user 显式触发 | LLM 智能扫描会话 + 列计划 + 用户确认 + 写入 |

**优先级**：你工作中遇到 5 个触发器之一就**主动写**（这是纪律）。
忘记了也没关系，user 敲 `/remember` 会让你回顾。

## 元规则

- **质量 > 数量**：写 1 条精准的 decision 比 10 条噪声 episode 有价值
- **别为写而写**：没有触发器就**不要**写
- **写的时候带未来 query 视角**：如果一周后任何 Agent 搜什么关键词应该能拉到这条？这些关键词写进 tags
- **诚实标 sensitivity**：默认 internal；含密钥/私聊用 private 或 secret
