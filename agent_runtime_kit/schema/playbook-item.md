# Playbook Item Schema (v0.5)

> Playbook items 是数字员工的"工作手册"——跟 memory items（"工作日志/档案"）配对。
> Memory items 答 "what happened"；Playbook items 答 "how to do it"。

## 物理位置

- 默认：`~/.agent-memory-hub/playbook/`（用户级，跨项目复用）
- 可被 `BRAIN_DIR` env 覆盖到自定义位置（同 memory items）
- v0.6+ 可能加 project-level overlay (`<project>/playbook/`)

## 目录组织（按 kind 物理分组）

```
~/.agent-memory-hub/playbook/
├── README.md                    namespace 说明
├── disciplines/                 元规则（5 触发器、写入纪律）
├── skills/                      可触发的能力（"X 时怎么做"）
├── hooks/                       事件驱动的注入逻辑（normalized 跨 Agent 形式）
├── rules/                       约束规则（"绝不..."）
└── sops/                        多步流程（"按以下顺序做"）
```

## 5 个 kind 的语义

| kind | 表达 | 何时写 | 何时检索 |
|---|---|---|---|
| `discipline` | 元规则 / 集体行为约定 | 跨 Agent 共享的 "what is the right way to work here" | 新 Agent onboarding 时全局注入 |
| `skill` | 自治能力包（含触发条件 + 步骤）| Agent 学到 "怎么做 X" 时 | LLM 判断 trigger 命中时 |
| `hook` | 事件驱动注入逻辑（normalized 跨 Agent）| 系统级行为，比如 SessionStart 时干啥 | runtime 由各 Agent 自身机制触发 |
| `rule` | 约束规则 | 关键 "禁不准" / "必须" | LLM 自检 / 决策前 |
| `sop` | 多步流程 / 标准操作 | 重复性流程（部署、release）| 流程开始时 |

## Frontmatter 模板

```yaml
---
id: play-YYYYMMDD-HHMMSS-slug                   # play- 前缀（区别于 memory item 的 mem-）
schema_version: 0.5
kind: discipline | skill | hook | rule | sop
created_at: "YYYY-MM-DDTHH:MM:SS+08:00"
updated_at: "YYYY-MM-DDTHH:MM:SS+08:00"          # 跟 created_at 不同则有过修订
agent: claude-code | codex | qoder | wukong | human
target_agents: [claude-code, codex, cursor]      # ["any"] = 通用
title: "一句话标题"
summary: "1-2 句总结，让 LLM 决策时秒判断"
tags: [tag1, tag2]
sensitivity: public | internal | private | secret
trigger: "什么情况下应用此 playbook (自然语言 + 关键词)"  # skill / hook 必填，其他可选
related_mems: []                                 # 关联的 memory item id（双向链）
related_plays: []                                # 关联的其他 playbook id
license: MIT                                     # 默认随项目 LICENSE
---
```

## 5 个 kind 的正文必含

### `discipline`

- **元规则**：核心约定（多条）
- **触发**（如适用）：什么场景应用
- **例外**：什么时候不适用

### `skill`

- **触发条件**：自然语言 + 关键词 list
- **步骤**：编号步骤（1, 2, 3...）
- **检查清单**：完成验证 (- [ ] ...)
- **例子**：1-2 个 input → output 示例

### `hook`

- **事件**：触发的事件名（SessionStart / UserPromptSubmit / Stop / 自定义）
- **输入**：事件上下文（JSON 字段）
- **行为**：normalized 描述（"在 system context 注入 X"）
- **每个 Agent 的实现 hint**（本 schema 不强制 enforce，仅提示）：
  - Claude Code: shell hook 命令
  - Codex: ?
  - Cursor: ?

### `rule`

- **规则陈述**："绝不 X" / "必须 X"
- **理由**：为什么
- **适用范围**：什么场景
- **例外**：什么场景不适用

### `sop`

- **目标**：完成什么
- **前置条件**：开始前需要的东西
- **步骤**：编号步骤（每步可有子步骤 / 验证）
- **回滚步骤**：如果中间失败怎么办

## ID 命名规则

- 前缀 `play-` + 时间戳 + slug（topic-centered，不写 source/target 方向，跟 memory item slug 规则一致）
- slug 限定字符：`a-z 0-9 -`（保留中文 CJK）

## 跟 memory item 的关系

| 维度 | memory item | playbook item |
|---|---|---|
| 前缀 | `mem-` | `play-` |
| 性质 | "what happened" | "how to do" |
| schema_version | 0.2 | 0.5+ |
| 6 type / 5 kind | type=fact/episode/decision/artifact/signal/handoff | kind=discipline/skill/hook/rule/sop |
| 物理位置 | `items/` | `playbook/<kind>/` |
| 检索 | search-memory.sh | list-playbook.sh / search-memory.sh（统一时） |
| MCP tool | search_memory / write_memory / list_recent / read_memory / stats / delete_memory | list_playbook (v0.5)、未来更多 |

## 跨 Agent 兼容（D3=A: Pull model）

每个 LLM Agent 应在启动时（或 prompt 触发时）：

1. 拉 `target_agents` 含自身（或 `any`）的 playbook items
2. 按 `kind` 分组：discipline 总是注入；skill 按 trigger 评估；hook/rule/sop 按场景调用
3. 不强制每个 Agent 完全实现 5 kind —— 不支持的 kind 安静 skip

具体实现 adapter（Codex / Cursor / Cline 等）由各 Agent 自己写（v0.6+ 我们提供 reference adapter）。

## v0.5 不做（YAGNI）

- ❌ Auto-translate skill 到各 Agent 原生格式（保留各 Agent 自己的格式）
- ❌ Playbook items 的 versioning / dependency graph
- ❌ 跨 Agent runtime enforce（M2 仅文档约定）
- ❌ project-level overlay（v0.6+）

## 现存 5 个示例（M2 PoC 落地）

| 路径 | kind | 来源 |
|---|---|---|
| `disciplines/agent-memory.md` | discipline | 复刻 agent_runtime_kit/AGENT_MEMORY_DISCIPLINE.md |
| `skills/write-good-decision.md` | skill | meta-skill: 怎么写好 decision item |
| `hooks/session-start-discipline-injector.md` | hook | normalized 版的 inject-discipline.sh |

后续 PR 欢迎扩充。
