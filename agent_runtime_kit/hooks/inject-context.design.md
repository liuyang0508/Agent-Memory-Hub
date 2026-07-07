# Hook 设计：UserPromptSubmit 自动注入相关 memory

> 这是设计文档，不是实现。v0.2.1 实现。
> 当前用户用 b 模式：每次 prompt 前 hook 自动从 brain pool 拉 top-K 相关 items 注入 system prompt。

## 目标体验

用户在 Claude Code / Codex / Qoder 里输入 prompt，Agent 在干活前**已经知道**：
- 这个 project 之前做过什么（episode）
- 当前有什么阻塞 / 警报（signal）
- 关键决策是什么（decision）
- 是否有定向交接给我的 handoff

不需要用户提醒，不需要 Agent 显式 query。

## 触发点

### Claude Code

CC 支持的 hook 类型（按文档）：
- `UserPromptSubmit`：用户提交 prompt 后、Agent 推理前 ← **目标 hook**
- `PreToolUse` / `PostToolUse`：工具调用前后
- `SessionStart` / `Stop`：会话级
- `Notification`：通知

`UserPromptSubmit` hook 可以在 prompt 上加 system context，正合需要。

### Codex

Codex 通过 MCP server 暴露 `search_memory` 工具——Codex 自己决定是否调用，不能像 CC 那样强制注入。但可以在 system prompt 里明确指示"工作前先调 search_memory"，让它形成习惯。

### Qoder

需要看 Qoder 的 hook 能力是否支持 prompt 注入，先按 MCP 工具方式做。

## 设计草案

### 配置文件 `~/.claude/hooks.json` 加一项

```json
{
  "UserPromptSubmit": [
    {
      "name": "agent-memory-hub-auto-inject",
      "command": "/path/to/agent-memory-hub/agent_runtime_kit/hooks/inject-context.sh",
      "timeout": 3000
    }
  ]
}
```

### `inject-context.sh` 行为

```bash
# 输入：stdin = JSON {"prompt": "...", "session_id": "...", "cwd": "..."}
# 输出：stdout = JSON {"systemPrompt": "<additional context>"} 或空

1. 读 prompt
2. 抽关键词（前 3 个名词 + 已知 project tags）
3. 对每个关键词调 search-memory.sh --top-k 2
4. 按 type 优先级（signal > handoff > decision > fact > episode > artifact）排序
5. 取 top-3，组装成 system prompt 段：
   <relevant_memory>
   ## Recent decisions / blockers / handoffs
   - [decision] BOM 编码：见 mem-...
   - [signal] CUA beta header 未确认：见 mem-...
   ...
   </relevant_memory>
6. 输出 JSON 给 CC 注入
```

## 关键设计权衡

### 1. 关键词抽取：本地 vs LLM

- **本地**（v0.2.1 起步）：split + stopword + named entity 简单规则
  - 优点：快、零成本、无外部依赖
  - 缺点：抽不准复杂语义
- **LLM**（v0.3+）：调一个 small model 做意图抽取
  - 优点：准
  - 缺点：每个 prompt 多 200ms / API 成本

先做本地版本，发现召回不行再加 LLM 层。

### 2. 注入数量：top-3 vs top-N

太多 → 污染上下文，干扰主任务
太少 → 漏掉关键 item

推荐 **top-3**，并按 type 优先级强制保留至少 1 个 signal（如果有阻塞）。

### 3. 全局 vs 项目级 brain pool

当前设计：单一 brain（`~/.agent-memory-hub/`）

未来可能：
- 多个 brain（个人 / 团队 / 每个 repo 一个）
- 项目级 brain 通过 `cwd` 自动定位
- 全局 brain 跨项目（用户级 fact，比如"我的偏好"）

v0.2 不做分层，单一 brain 即可。

### 4. 隐私边界

`sensitivity: secret` 的 item **不应该被自动注入**——hook 默认过滤掉。
`sensitivity: private` 注入时降级（只给 title + summary，不给 body）。

### 5. 减噪策略

避免每次 prompt 都注入相同的高频 item：
- 加 `last_injected_at` 字段（v0.2.2）
- 同一 session 内同一 item 不重复注入
- 全局降权 24 小时内已注入过 N 次的 item

## v0.2.1 最小实现切片

```
agent_runtime_kit/hooks/
├── inject-context.design.md   ← 本文档
├── inject-context.sh          ← v0.2.1 写
└── extract-keywords.sh        ← v0.2.1 写（简单本地版）
```

测试方法：
1. 装 hook
2. 在 CC 里输入 "继续 weather-cli 的 csv 工作"
3. CC 内部应该看到注入的 BOM decision + handoff item
4. CC 不需要被提醒就该尊重 BOM 编码

## 跟 MCP 的关系

Hook 是 CC 端的客户端机制，MCP 是跨 Agent 的服务端机制。

两者并存：
- CC 用 hook（最自然），底层调 MCP server 的 search_memory
- Codex 用 MCP（不支持 hook 的 Agent 唯一选择）
- Qoder/Wukong 看具体能力

MCP server 是 v0.3 做的事——先把 brain pool 的工具跑通，再包一层 MCP。
