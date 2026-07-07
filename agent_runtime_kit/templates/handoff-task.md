---
id: handoff-YYYYMMDD-HHMMSS-slug
mode: task-handoff
schema_version: 0.1
created_at: "YYYY-MM-DDTHH:MM:SS+08:00"
source_agent: claude-code
target_agent: wukong
sensitivity: internal
task: "weather-cli/export-csv-pm-sync"
parent_handoff: "handoff-20260514-150000-export-csv"
child_handoffs: []
---

# 跨域转交 Handoff（开发态 Agent → Wukong）

> 此模板用于：开发态 Agent 把工作交给协同态 Agent。下游不改代码，
> 它干的是通知、同步、收集反馈、跑工作流。所以它**不需要**代码细节，
> 它需要**业务意图 + 输出契约**。

## 1. Objective（业务意图）

用业务语言写。**不写**文件名、commit hash、技术栈。

> 例：weather-cli 新增 CSV 导出功能已上线，需要在团队群同步给 PM 并收集字段需求反馈。

## 2. Context（背景，1 段）

下游不知道这事的来龙去脉，给它一个最小可理解的背景。

> 例：上周 PM 提出希望把每日天气数据导出成 CSV 给 BI 分析用。开发态 Agent
> 已实现 `--export-csv` flag，目前 CSV 列固定为 date/city/temp/humidity 四列。
> PM 还没看到具体字段，需要确认是否需要加 wind / pressure 等。

## 3. Ask（要对方做什么，业务语言）

3 条以内。每条说**做什么** + **目标**。

> 例：
> 1. 在「天气产品」钉钉群发功能上线通知，附 CSV 样例
> 2. @PM 询问字段是否够用，是否要加 wind / pressure / 自定义列
> 3. 把 PM 反馈整理成需求点回写到本任务

## 4. Constraints（约束 / 口径）

下游不能违反的硬规则。截止时间、合规、对外口径。

> 例：
> - 截止：本周五前拿到 PM 反馈
> - 口径：对外只说"已支持 CSV 导出"，不暴露内部命令行参数
> - 合规：CSV 样例不能包含真实用户数据

## 5. Expected Outcome（什么算完成）

下游做完什么事这次转交才算闭环。一定要可验证。

> 例：
> - PM 在群里给出明确反馈（"够了" / 或者 "再加 X 列"）
> - 反馈内容回写到 `docs/demo/feedback.md`
> - 如果有新增需求，创建一个 follow-up handoff（mode: code-resume）回开发态

## 6. Evidence Links（参考资料，不强制读）

> 例：
> - 上游 handoff: `docs/demo/03-handoff-codex-to-wukong.md`
> - 功能 PR: `https://github.com/example/weather-cli/pull/42`
> - 原始需求: 钉钉 5/10 PM 提的需求

---

## Schema 规则（v0.1）

- **frontmatter 必填**：`id` / `mode` / `schema_version` / `created_at` / `source_agent` / `target_agent` / `sensitivity`
- **正文必填**：1, 2, 3, 5 段。Context 和 Expected Outcome 是这类 handoff 的命脉。
- **正文可选**：4, 6 段。
- **不允许**：贴代码、贴 stack trace、把开发态细节灌过来。如果下游问"具体是哪个文件"，
  说明你给的 Context 不够抽象。
- **关键差异**（vs handoff-code）：这个模板**不应该**有 `repo`/`branch`/`files_touched` 字段。
  如果你发现你想加这些，说明这个任务其实是同域续做，应该用 `handoff-code.md`。
