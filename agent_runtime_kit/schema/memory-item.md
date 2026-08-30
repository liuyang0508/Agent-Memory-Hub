# Memory Item Schema (v0.2)

Brain pool 里每条记录的统一格式。任何 Agent 写入和读取都遵守这个 schema。

## 核心理念

- **没有"目标接收方"**：写的时候不知道谁会用
- **type-driven 检索**：不同 type 的 item 有不同的查询模式
- **双向 refs**：item 之间可以互相引用，形成知识图
- **人类可读**：纯 Markdown + frontmatter，Obsidian 直接打开

## Frontmatter（统一）

```yaml
---
id: mem-YYYYMMDD-HHMMSS-slug
schema_version: 0.2
type: fact | episode | decision | artifact | signal | handoff
created_at: "YYYY-MM-DDTHH:MM:SS+08:00"
agent: claude-code | codex | qoder | wukong | human
session: <session-id-or-null>
project: <project-slug-or-null>
tags: [tag1, tag2, tag3]
sensitivity: public | internal | private | secret
tenant_id: <null-default-or-tenant-slug>  # L3 multi-tenant interface reservation (v0.3.5+)
auth_context: <null-default-or-rbac-context>     # L3 RBAC interface reservation (v0.3.5+)
title: "一句话标题，搜索结果会先看到这个"
summary: "1-2 句总结，让查询时快速判断相关性"
refs:
  files: []      # 本地文件路径
  urls: []       # web 链接
  mems: []       # 其他 memory item id（双向链）
  commits: []    # git commit hash
---
```

## 6 种 type 的语义

| type | 表达 | 何时写 | 何时检索 |
|---|---|---|---|
| `fact` | 客观事实陈述 | 学到一个事实/限制时 | 干活前查"项目状态" |
| `episode` | 做过的事 + 结果 | 完成一段工作时 | "曾经处理过这种情况吗" |
| `decision` | 关键决策 + 理由 | 做出非显然选择时 | "为什么当时选了 X" |
| `artifact` | 产出物的引用 | 生成 PR/文档/CSV 时 | "找下这个 task 的产出物" |
| `signal` | 当前活跃信号 | 阻塞/警报/变更通知 | "有什么阻塞我吗" |
| `handoff` | 任务交接包（v0.1 的） | 显式交给某个 Agent 续做 | 下游接手时 |

### 何时不该用 MemoryItem

- **正在编辑的代码**：用 git，不要写进 brain
- **完整 transcript**：不要写成 `mem-*.md`；原始对话证据用 `memory conversation ingest` 或 `memory harvest` 写入 `sources/conversations/`
- **secrets**：永远不写

`items/` 是长期知识结论层，`sources/conversations/` 是原始对话证据层。后者可以被抽取器引用或回放成候选，但默认不自动进入 prompt 注入。

## 正文结构

正文按 type 不同有不同推荐结构（不强制）：

### fact 推荐结构
```markdown
# {title}

**事实**：...
**来源**：...
**有效期**：（如有）
```

### episode 推荐结构
```markdown
# {title}

**情境**：...
**做了什么**：...
**结果**：...
**学到**：...
```

### decision 推荐结构
```markdown
# {title}

**决策**：...
**理由**：...
**备选方案**（被否决的）：...
**改回去的代价**：...
```

### artifact 推荐结构
```markdown
# {title}

**产出物**：链接 / 路径
**生成方式**：...
**用途**：...
```

### signal 推荐结构
```markdown
# {title}

**信号类型**：阻塞 / 警报 / 变更
**当前状态**：...
**影响**：...
**期望操作**：...
```

### handoff 推荐结构

复用 v0.1 的 agent_runtime_kit/templates/handoff-code.md 或 handoff-task.md 段落结构，
作为 type=handoff 的 memory item 的正文（无须改字段）。

## ID 命名规则

- `mem-YYYYMMDD-HHMMSS-slug`
- 时间精度到秒，slug 用 kebab-case 描述主题
- 例：`mem-20260514-153000-cua-beta-header`
- 工具自动生成，人不要手填

## 关键搜索维度

任何搜索/查询应该至少支持：
- 全文搜索（title + summary + body）
- type 过滤
- project 过滤
- tags 过滤
- 时间窗口（"最近 7 天"）
- agent 过滤（"CC 写过的"）
- sensitivity 过滤

## 与 v0/v0.1 的关系

- v0/v0.1 的 `agent_runtime_kit/templates/handoff-code.md` 和 `handoff-task.md` 仍然有效
- 它们现在是 **type=handoff 的 memory item 的正文模板**
- v0.1 的 `agent_runtime_kit/tools/gen-handoff.sh` 仍然有效，但应改造成调用 `write-memory.sh --type handoff`（v0.2.1 做）
