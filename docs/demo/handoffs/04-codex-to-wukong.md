---
id: handoff-20260514-170000-export-csv-pm-sync
mode: task-handoff
created_at: "2026-05-14T17:00:00+08:00"
source_agent: codex
target_agent: wukong
sensitivity: internal
task: "weather-cli/export-csv-pm-sync"
parent_handoff: "handoff-20260514-154200-export-csv-tests"
---

# Handoff: 在团队群同步 CSV 导出功能上线 + 收集 PM 字段需求反馈

## 1. Objective

weather-cli 新增的 CSV 导出功能开发完成。需要在团队群同步进度，
让 PM 确认当前字段是否够用、是否要扩展。

## 2. Context

上周 PM 提出希望把每日天气数据导出成 CSV 给 BI 团队做趋势分析。
开发已完成基础导出能力，目前 CSV 包含 4 列：日期、城市、温度、湿度。
PM 还没看到具体字段，需要由你确认字段范围是否够用，是否需要补充
风速、气压等其他指标。

字段集是开发期间跟 PM 当面对齐过的最小集合，但可能 PM 当时没
仔细想，需要看到样例后才能给出准确反馈。

## 3. Ask

1. 在「天气产品」钉钉群发功能上线通知，附 1 份 CSV 样例（用脱敏数据）
2. @PM 询问当前 4 列是否够用；如果不够，请列出还需要哪些字段
3. 把 PM 反馈整理后回写到 `docs/demo/snapshots/05-wukong-final.md`

## 4. Constraints

- 截止：2026-05-16（周五）下午 5 点前拿到 PM 反馈
- 对外口径：只说"已支持 CSV 导出"，不要提 commit hash / 文件名 / 命令行参数
- CSV 样例**禁止**使用真实生产数据，必须用脱敏数据（推荐用北京/上海几个公开城市）
- 如果 PM 提出新字段需求，**不要**当场承诺工期，直接转回开发态

## 5. Expected Outcome

- PM 在群里给出明确反馈（"够了" 或 "还要加 X / Y / Z 列"）
- 反馈内容回写到 `docs/demo/snapshots/05-wukong-final.md`
- 如果有新增字段需求，**创建一个 follow-up handoff**（mode: code-resume）
  指向开发态 Agent，让它接着改代码

## 6. Evidence Links

- 上游 handoff（开发态完成的细节，参考用，不强制读）: `docs/demo/handoffs/02-cc-to-codex.md`
- 原始需求讨论：钉钉「天气产品」群 5/10 14:30
- 字段对齐讨论：钉钉「天气产品」群 5/12 16:00

---

> ⚠️ **Demo 注释（不属于真实 handoff）**：
> 注意这份 handoff **完全没有** `repo`/`branch`/`files_touched`，
> 这是跨域转交的关键差异——Wukong 不需要这些，给了反而引导它跑偏。
> Context 用的也是业务语言，没有暴露任何代码细节。
