# 阶段二召回质量就绪报告

> 状态：**PASS**；评测时间冻结为 `2026-07-19T02:00:00+00:00`。

## 六层总览

- cases：37
- retrieval：R@10 100.00%，MRR 98.39%
- admission：FP 0，FN 0
- answerability mismatch：0
- temporal mismatch：0
- abstention：precision 100.00%，recall 100.00%
- injection：FP 0，FN 0，prohibited 0
- packed token cost：336
- 41-case safety fixture：FP 0，FN 0，prohibited 0

## Split 结果

| split | cases | R@10 | MRR | injection FP | injection FN | answerability mismatch | temporal mismatch |
|---|---:|---:|---:|---:|---:|---:|---:|
| calibration | 15 | 100.00% | 96.67% | 0 | 0 | 0 | 0 |
| heldout | 10 | 100.00% | 100.00% | 0 | 0 | 0 | 0 |
| production_replay | 12 | 100.00% | 100.00% | 0 | 0 | 0 | 0 |

## 事实边界

- retrieval 命中不替代 Gateway 注入结论；六层分别计数。
- production replay 为去敏运行时回放，公开报告不包含原始 prompt、session 或路径。
- project shadow 只计诊断数量，不进入 hits、evidence、Gateway 或 access count。
- committed JSON 必须与 corpus hash 和 implementation hash 一致，否则门禁失败。
