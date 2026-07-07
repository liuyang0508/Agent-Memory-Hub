# Demo 场景：weather-cli 加 CSV 导出

## 背景设定（虚构）

`weather-cli` 是一个团队内部用的 Python 命令行天气查询工具。
当前已有：

```bash
weather-cli show --city Beijing
# → Beijing: 23°C, 60% humidity, sunny
```

PM 5/10 在钉钉群提出新需求：希望能把多日多城市的天气数据导出成 CSV，
给 BI 团队做趋势分析。

## 三段式工作流

```
┌──────────────────────────────────────────────────────────┐
│  阶段 1（CC）   实现 --export-csv 主逻辑（停在测试前）    │
│      │                                                    │
│      ▼                                                    │
│  handoff-1（同域续做，handoff-code）                     │
│      │                                                    │
│      ▼                                                    │
│  阶段 2（Codex）  写测试 + 处理 edge case                │
│      │                                                    │
│      ▼                                                    │
│  handoff-2（跨域转交，handoff-task）                     │
│      │                                                    │
│      ▼                                                    │
│  阶段 3（Wukong）  通知 PM、收集字段需求反馈              │
└──────────────────────────────────────────────────────────┘
```

## Demo 故意埋的"雷"

为了演示 v0 反推 schema 的过程，**第一段 handoff 故意漏掉一个非显然的关键决策**：
CC 在 CSV 写入时用了 `utf-8-sig`（带 BOM）以便 Excel 直接打开。
handoff 里只写了"列固定为 4 列"，没写编码决策。

**预期结果**：Codex 写测试时按普通 utf-8 处理，导致测试通过但实际产出
让 Excel 显示乱码。eval 时抓出来，反推出 schema 应该新增
"non-obvious decisions" 字段（或者强化 Decisions 段的强制性）。

## 不演的事

- 不写真实的 weather-cli 代码（不是验证目标）
- 不真的调用任何 Agent（演的是 handoff schema 是否撑得住）
- snapshots 只是"工作快照"的纯文本表达，模拟 Agent session 状态
