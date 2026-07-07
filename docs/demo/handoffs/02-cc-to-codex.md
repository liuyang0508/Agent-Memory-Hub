---
id: handoff-20260514-154200-export-csv-tests
mode: code-resume
created_at: "2026-05-14T15:42:00+08:00"
source_agent: claude-code
target_agent: codex
sensitivity: internal
task: "weather-cli/export-csv"
repo: "~/repos/weather-cli"
branch: "feature/export-csv"
---

# Handoff: 续做 weather-cli CSV 导出的测试和 edge case

## 1. Objective

给 weather-cli 的 `--export-csv` 功能补 pytest 测试 + 处理 edge case。

## 2. Current State

**代码层**：
- `src/cli.py` 已加 `--export-csv FILE` argparse 选项（行 42-58）
- `src/exporters/csv.py` 新文件，实现 `write_weather_csv(rows, path)` 主逻辑
- `src/exporters/__init__.py` 已 export `write_weather_csv`
- 当前 branch `feature/export-csv`，HEAD 是 `a1b2c3d`，**尚未提交本地修改**
- 未跑测试，`tests/test_csv_export.py` 不存在

**任务层**：
- 已完成：argparse 改造、主写入逻辑、CSV 列范围决策
- 未完成：测试、edge case 处理、README 更新

**思考层**：
- **决策**：CSV 列固定为 `date,city,temp,humidity`，**不做**动态列
  - 理由：BI 当前只用这 4 列，动态列会让下游 ETL 复杂
  - 来源：5/12 跟 PM 同步过

## 3. Next Actions

1. 创建 `tests/test_csv_export.py`，覆盖：正常导出 / 空数据 / 文件已存在 / 路径不存在 4 个 case
2. 在 `src/exporters/csv.py:write_weather_csv()` 加空数据处理：返回 `False`，**不写文件**
3. 在 `write_weather_csv()` 加 `os.makedirs(parent, exist_ok=True)` 处理路径不存在
4. 跑 `pytest tests/test_csv_export.py -v` 确认全绿
5. 更新 `README.md` 的 Usage 段，加 `--export-csv` 例子

## 4. Files Touched

- `src/cli.py` —— 加 `--export-csv` argparse 选项
- `src/exporters/csv.py` —— 新文件，写入主逻辑
- `src/exporters/__init__.py` —— 导出 `write_weather_csv`

## 5. Blockers

无。

## 6. Evidence Links

- 上一个 commit: `git show a1b2c3d`
- 原始需求讨论：钉钉「天气产品」群 5/10 14:30 PM 发言
- 列范围决策记录：钉钉「天气产品」群 5/12 16:00 跟 PM 同步

---

> ⚠️ **Demo 注释（不属于真实 handoff）**：
> 这份 handoff 故意漏掉了 CSV 编码决策（`utf-8-sig` with BOM）。
> Decisions 段只写了"列固定 4 列"，没写编码。这是 CC 上游的真实
> 失误模式——非显然的决策容易被遗忘。下一步看 Codex 续做时会不会踩雷。
