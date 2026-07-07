---
id: handoff-YYYYMMDD-HHMMSS-slug
mode: code-resume
schema_version: 0.1
created_at: "YYYY-MM-DDTHH:MM:SS+08:00"
source_agent: claude-code
target_agent: codex
sensitivity: internal
task: "weather-cli/export-csv"
repo: "/path/to/repo"
branch: "feature/export-csv"
head_commit: "a1b2c3d"
parent_handoff: null
child_handoffs: []
---

# 同域续做 Handoff（Claude Code ↔ Codex）

> 此模板用于：同一份代码、同一个 repo、同一个 task 在两个开发态 Agent
> 之间接力。下游 Agent 必须能直接 `cd` 到 repo 接着改代码。
>
> **v0.1 变更**（基于 v0 demo 反推）：
> - Decisions 段升为**必填**，强制列非显然决策
> - 加 `child_handoffs` 反向链字段
> - Next Actions 区分 must_complete / nice_to_have
> - 新增 Verification Expectations 段（独立成段）

## 1. Objective（一句话）

要做什么。**不写**为什么、不写历史背景。

> 例：给 weather-cli 加 `--export-csv FILE` flag，把查询结果写成 CSV。

## 2. Current State（当前到哪了）

**代码层**：
- 哪些文件改过了 / 改了哪一段
- branch / HEAD commit / 是否有未提交改动
- 是否跑过测试 / 测试结果

**任务层**：
- 已完成的子目标
- 未完成的子目标

> 例：
> - `src/cli.py` 加 `--export-csv` argparse 选项（行 42-58）
> - `src/exporters/csv.py` 新文件，实现 `write_weather_csv()`，未处理空数据
> - branch `feature/export-csv`，HEAD `a1b2c3d`，本地有未提交改动
> - 未跑测试

## 3. Decisions（必填⭐）

> **v0.1 关键变更**：从可选段升为**必填段**。
>
> 强制要求列出**非显然决策**——任何换个人会本能改回去的选择必须列出，
> 否则下游 Agent 在"做清理 / 简化"时会无意识地推翻你的设计。
>
> 每条用格式：`决策 | 理由 | 改回去的代价`

> 例：
> 1. `CSV 列固定为 date,city,temp,humidity` | 跟 PM 5/12 对齐过最小集合，BI 当前只用这 4 列 | 改成动态列会让下游 ETL 复杂
> 2. `文件编码用 utf-8-sig（带 BOM）` | macOS Excel 双击打开中文不乱码 | 改回普通 utf-8 会让 PM 那侧产出无法直接看
> 3. `空数据返回 False 不写文件` | 避免下游误以为"写了空 CSV = 成功" | 改回写空文件会污染 BI 数据源

如果**真的没有**非显然决策，写"无非显然决策"。但先反问自己：刚才那 2 小时
我做的所有选择里，有没有哪个换个人会本能改回去的？

## 4. Next Actions（必填）

3-5 条祈使句，每条都是**可验证完成**的动作。

**must_complete**（本次续做必须做完，否则 eval 失败）：
1. ...
2. ...

**nice_to_have**（可挪到下个 session）：
1. ...

> 例：
> must_complete:
> 1. 在 `tests/test_csv_export.py` 写 pytest 用例覆盖正常导出 / 空数据 / 文件已存在
> 2. 在 `write_weather_csv()` 加空数据处理（返回 False）
> 3. 跑 `pytest tests/test_csv_export.py -v` 确认全绿
>
> nice_to_have:
> 1. 更新 `README.md` 的 Usage 段加 `--export-csv` 例子

## 5. Verification Expectations（必填⭐）

> **v0.1 新增段**：独立于 Next Actions，描述**判定本次续做成功的客观命令**。
> 这是机器/Agent 能自动跑的检验，不能写"看起来对就行"。

- 命令：`pytest tests/test_csv_export.py -v`
- 期望：5 passed
- 产出物校验：导出的 CSV 在 macOS Excel 双击能正确显示中文（**针对 Decision #2**）

## 6. Files Touched

每个文件一行，说明动了什么。**不贴 diff**。

> 例：
> - `src/cli.py` —— 加 `--export-csv` argparse 选项
> - `src/exporters/csv.py` —— 新文件，写入逻辑

## 7. Blockers（卡住的点 / 风险）

无的话写"无"。

## 8. Evidence Links

最多 5 条。

> 例：
> - 上一个 commit: `git show a1b2c3d`
> - 需求文档: `docs/specs/csv-export.md`
> - 决策来源: 钉钉「天气产品」群 5/12 16:00

---

## Schema 规则（v0.1）

- **frontmatter 必填**：`id` / `mode` / `schema_version` / `created_at` / `source_agent` / `target_agent` / `sensitivity`
- **正文必填**：1 / 2 / 3 / 4 / 5 段。缺一不可。
- **正文可选**：6 / 7 / 8 段。无内容时段落保留、内容写"无"。
- **不允许**：贴完整对话 transcript、贴大段 diff、贴 secrets。
- **child_handoffs**：闭环时回填，由下游 Agent 完成 task 后追加自己的 handoff_id。
