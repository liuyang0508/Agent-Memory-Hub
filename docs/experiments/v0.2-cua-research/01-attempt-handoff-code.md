# 实验 1：硬套 v0.1 handoff-code 模板

> 目标：把上面的元数据强行填进 agent_runtime_kit/templates/handoff-code.md，
> 看哪些字段坍塌、哪些字段缺失。**不修复**，只标记。

---

```yaml
---
id: handoff-20260514-220000-cua-best-practices
mode: code-resume                          # 🔴 坍塌：这不是 code 任务
schema_version: 0.1
created_at: "2026-05-14T22:00:00+08:00"
source_agent: claude-code-48acbd08
target_agent: claude-code-new-session
sensitivity: internal
task: "computer-use-best-practices"
repo: "<workspace>"             # 🔴 坍塌：Desktop 不是 repo
branch: "N/A"                              # 🔴 字段空
head_commit: "N/A"                         # 🔴 字段空
scope: "<full repo>"                       # 🔴 字段不适用
parent_handoff: null
child_handoffs: []
---
```

## 1. Objective ✅
继续 Computer Use 最佳实践调研。

## 2. Current State 🔴 半坍塌

**代码层**（必填段强制要求）：
- branch: N/A 🔴
- HEAD: N/A 🔴
- 变更数量：0 🔴 但实际工作 1h17min，"产出"是知识不是代码
- working tree 状态：N/A 🔴
- 是否跑过测试: N/A 🔴

**任务层**：
- 已完成：⚠️ 1h 调研，具体不知（隐私边界）
- 未完成：⚠️ "其中某一步" 暗示有多步候选，但 step 列表丢失

🔴 **schema 漏洞**：研究任务的"产出"无法用 git diff 表达。
没有字段记录"已查的资源 / 已读的文档 / 已 grep 的关键词"。

## 3. Decisions（必填⭐）⚠️
仅能从 metadata 推断 1 条：
- "下一步建议验证 idealab proxy" | 理由不明 🔴 | 改回去的代价不明 🔴

🔴 真实研究类决策应该是"我用 X 角度切了，没用 Y/Z 角度，因为..."——这种**研究路径决策**当前 schema 完全不表达。

## 4. Next Actions（必填）⚠️
must_complete:
1. 验证 idealab proxy 是否透传 beta header

🔴 **schema 漏洞**：研究类 next actions 经常是**分叉的**（OR 关系），
而不是顺序执行。当前 schema 强制 1/2/3 编号，无法表达"做 A 或者做 B"。

## 5. Verification Expectations（必填⭐）🔴 完全坍塌
- 命令：⚠️ 研究任务无可跑命令
- 期望：⚠️
- 产出物校验：⚠️

🔴 这一段在研究任务里要么留空（违反必填）要么硬编个"看起来对就行"——schema 把 Agent 推向虚假合规。

## 6. Files Touched 🔴
- 不知道。研究任务的产出可能根本没文件。

## 7. Blockers ✅
- "要不要我帮你做其中某一步？"——上游在等用户分叉决策。

## 8. Evidence Links 🔴
- 全部不知道（隐私边界）。即使能读 transcript，研究任务的 evidence 是 web search 结果、读过的 PDF 段、跑过的 grep——没有标准 git 引用。

---

## 坍塌统计

| 段 | 状态 |
|---|---|
| frontmatter `mode` | 🔴 类型错误 |
| frontmatter `repo`/`branch`/`head_commit`/`scope` | 🔴 全部 N/A |
| §2 代码层 | 🔴 全部 N/A |
| §2 任务层 | ⚠️ 部分 |
| §3 Decisions | ⚠️ 表达不到位（研究路径决策没字段） |
| §4 Next Actions | ⚠️ 不支持分叉 |
| §5 Verification | 🔴 完全不适用 |
| §6 Files Touched | 🔴 完全不适用 |
| §8 Evidence | 🔴 引用形式不匹配 |

**结论**：8 段中 5 段坍塌或不适用。schema 必须改。
