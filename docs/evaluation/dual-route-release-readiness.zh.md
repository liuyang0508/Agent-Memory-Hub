# Dual-route recall 发布准备证据（2026-07-18）

## 结论

当前冻结代码候选 `98eef3fb45abb2d5a9d198529445103ceb9d43be` 的校准门禁与
hook 性能门禁均为 **PASS**，整体 release gate 为 **PASS**。机器事实源分别是：

- `dual-route-calibration-report.json`：calibration 15/15、heldout 11/11，
  41-case 公共安全夹具保持 0 FP / 0 FN，未解决 gap 为 0；
- `dual-route-hook-benchmark-report.json`：同一冻结候选完成连续两轮独立正式
  30-run，均无错误、无超时、每个 candidate 样本小于 2 秒，且 p95 增量不超过
  150ms。

校准门禁可复核为：

```bash
PYTHONPATH=. python scripts/check-dual-route-calibration.py
```

## Consolidated preflight 的能力与边界

本轮性能修复只合并 hook 的进程，不删除证据，也不放宽授权：

- payload parser 用一个 system Python 进程解析输入，并通过固定 NUL 协议交给 shell；
- `_resolve-python.sh` 仍负责 canonical path、symlink、import、identity 与 PID 验证；
- verified preflight 用一个已验证的 AMH Python 进程依次写 runtime event、保存 live prompt、
  归一化问题、提取 multimodal recall 文本并产生 multimodal gap JSON；单项证据写入失败
  仍 fail-open；
- 整个 preflight 进程失败或协议不合法时，才进入原有 multi-process legacy fallback；
  fallback 继续保存 runtime event、live prompt 和 multimodal 证据；
- InjectionGateway、ContextFirewall、2 秒搜索预算、stdout cap、descendant cleanup、
  adapter envelope 与 feature-off 边界均未改变；
- `AGENT_MEMORY_HUB_ROUTED_RECALL=0` 只回滚候选生成，不能关闭 Gateway，也不能让
  未授权命中进入 ContextPack。

因此，性能改善来自减少重复解释器启动与重复 import，不来自删减 evidence、降低阈值或
绕过安全链路。

## 旧用户升级与 adapter 刷新

旧 hook 不会自行获得 consolidated preflight。旧用户必须先升级包，再完成 adapter
refresh/repair。已有安装可按真实 CLI 合同执行：

```bash
memory self-update --repair-hooks
memory doctor --fix
```

也可以对单个 adapter 运行幂等安装并验收：

```bash
memory adapter install <adapter> --format json
memory adapter install-verify <adapter> --format json
```

`memory self-update --repair-hooks` 用于升级包后的全局 hook 修复，`memory doctor --fix`
用于修复 doctor 识别出的 core adapter/path 漂移；单 adapter 重装后必须通过
install-verify 和真实 runtime evidence。仅升级包、未 refresh/repair 已安装 hook，不能
声称已经获得新链路。

## 冻结校准证据

| split | expected items | TP | FP | FN | precision | recall |
|---|---:|---:|---:|---:|---:|---:|
| calibration | 15 | 15 | 0 | 0 | 1.0000 | 1.0000 |
| heldout | 11 | 11 | 0 | 0 | 1.0000 | 1.0000 |

冻结 embedder 仍为
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，revision
`e8f8c211226b894fcb81acc59f3b34ba3efd5f42`，snapshot digest
`sha256:d9ffa8b29d3b9b379a3168bce486cab75ac50f5f8f7f9ba6c17cf6e07a600792`。
修复采用共享 lexical technical-anchor alias，不替换模型、不降低 Gateway 阈值，也不要求
已有 memory item 重建索引。机器报告的 `unresolved_gap_count` 为 0，门禁命令返回 0。

## 探索性模型 bakeoff（不得默认启用）

历史 E5 与 reranker 试验只用于排除高风险替代路线，并未进入默认产品路径：

- E5 虽能覆盖目标语言现象，但曾在现有公共夹具上引入 injection FP 与既有正例 FN；
- reranker 的保守阈值没有解决问题，宽松阈值则缺少独立 held-out 校准与跨平台冷启动证据；
- hook 冷路径不得默认启用第二模型，也不得下载模型。

当前 PASS 由 committed technical-anchor 规则、冻结 calibration report 和正式 hook
基准共同支持，不借用这些探索结果。

## 连续两轮正式 hook 性能证据

base 固定为 `bb9128a668fea98bf9063bfbedc85cc75dc8936c`，candidate 固定为
`98eef3fb45abb2d5a9d198529445103ceb9d43be`。两边使用同一个全新公开合成 brain、
同一个 committed payload、同一个 Python、同一个离线 HashingEmbedder 和同一组 adapter
环境变量；只有 worktree 的 `PYTHONPATH` 与真实 hook 路径不同。每轮固定 30 个 measured
samples、3 个 warmups、交错 old/new，runner 校验完整 adapter envelope 与公开 sentinel。

| round | command | samples | p50 | p95 | max | errors | timeouts |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 | base hook | 30 | 2843.794ms | 2982.526ms | 3410.441ms | 0 | 0 |
| 1 | candidate hook | 30 | 1281.076ms | 1346.079ms | 1367.384ms | 0 | 0 |
| 2 | base hook | 30 | 2861.569ms | 2941.064ms | 2971.851ms | 0 | 0 |
| 2 | candidate hook | 30 | 1275.982ms | 1357.832ms | 1461.996ms | 0 | 0 |

第一轮 p95 delta 为 -1636.447ms，第二轮为 -1583.232ms。两轮 `passed=true`、
`publishable=true`；没有用第三轮覆盖失败结果。

机器报告的 `run_history` 同时保留优化前的两个真实尾延迟失败：一个 candidate
max 2400.187ms、p95 2144.243ms，另一个 max 2081.018ms、p95 1994.710ms；两次均为
0 error / 0 timeout。这些历史证据解释了为什么引入 consolidated preflight，并不代表
冻结候选的当前状态。

报告只保存聚合统计、固定 commit 与可重建哈希，不保存请求正文、注入结果正文或子进程
原始输出。`result` 保持既有消费者合同，原样使用第二轮聚合；两轮完整聚合保存在
`run_history`。

## 可复现方法

将 `BASE` 与 `CAND` 分别指向上述精确 commit 的独立 worktree，并为每轮选择一个尚不
存在的 `BRAIN` 路径。materializer 遇到已有路径会 fail closed。随后按机器报告中的
`commands` 模板 materialize fixture 并运行 benchmark；不得改变 payload、Python、环境变量、
warmup、样本数、协议或门槛。

机器报告记录并由 contract test 重算 old/candidate hook、runner、materializer、payload 与
确定性 fixture item 的 SHA-256。索引数据库不是哈希事实源；它由 materializer 使用
committed item 和 HashingEmbedder 重新生成。

## 发布验收条件

本次 PASS 建立在以下条件同时成立：

1. 校准报告为 calibration 15/15、heldout 11/11、0 FP / 0 FN，且门禁退出码为 0；
2. 固定代码候选的连续两轮正式 30-run 均满足单次小于 2 秒、p95 增量不超过 150ms、
   0 error、0 timeout；
3. targeted、全仓测试、Ruff、shell 语法、隐私合同与 `git diff --check` 均通过；
4. 发布物中保留升级包后的 refresh/repair 指引，不能把代码仓 PASS 误解为旧安装已自动刷新。
