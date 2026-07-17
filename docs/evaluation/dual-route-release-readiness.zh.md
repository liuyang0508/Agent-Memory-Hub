# Dual-route recall 发布准备证据（2026-07-18）

## 结论

当前冻结代码候选 `8d3929d1589be304703a26ec4955f896c308c2ca` 的校准门禁与
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

本轮性能与输入完整性修复只调整 hook 的进程和字节传递，不删除证据，也不放宽授权：

- 原始 stdin 先流入 mode-0600 私有文件，不进入 shell 变量；payload parser、verified
  preflight 与 legacy fallback 都从该文件重放相同字节，HUP/INT/TERM/EXIT 均清理文件；
- payload parser 用一个 system Python 进程解析输入，并递归拒绝任意层级 JSON key/value
  中解码后的 NUL，再通过固定 NUL 协议交给 shell；
- `_resolve-python.sh` 仍负责 canonical path、symlink、import、identity 与 PID 验证；
- verified preflight 用一个已验证的 AMH Python 进程依次写 runtime event、保存 live prompt、
  归一化问题、提取 multimodal recall 文本并产生 multimodal gap JSON；单项证据写入失败
  仍 fail-open；
- parser 与 preflight 均为父 hook 管理的直接 child；即使信号只发给父 PID，父进程也会
  kill、reap child 并清理私有文件；
- preflight 以 0 退出但协议污染时只执行 derivation-only fallback，避免 runtime/live
  prompt/multimodal 证据双写；preflight 非 0 退出才执行 full legacy fallback；
- 空 prompt 只要带 attachment，仍进入 verified preflight；无 verified Python 或进程失败时，
  legacy multimodal 路径继续提取 attachment 并参与召回；
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
`8d3929d1589be304703a26ec4955f896c308c2ca`。两边使用同一个全新公开合成 brain、
同一个 committed payload、同一个 Python、同一个离线 HashingEmbedder 和同一组 adapter
环境变量；只有 worktree 的 `PYTHONPATH` 与真实 hook 路径不同。每轮固定 30 个 measured
samples、3 个 warmups、交错 old/new，runner 校验完整 adapter envelope 与公开 sentinel。

| round | command | samples | p50 | p95 | max | errors | timeouts |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 | base hook | 30 | 2879.942ms | 3046.240ms | 3063.865ms | 0 | 0 |
| 1 | candidate hook | 30 | 1264.821ms | 1320.596ms | 1334.546ms | 0 | 0 |
| 2 | base hook | 30 | 2903.986ms | 3055.255ms | 3173.345ms | 0 | 0 |
| 2 | candidate hook | 30 | 1289.906ms | 1317.649ms | 1327.535ms | 0 | 0 |

第一轮 p95 delta 为 -1725.644ms，第二轮为 -1737.606ms。两轮 `passed=true`、
`publishable=true`；没有用第三轮覆盖失败结果。

机器报告的 `run_history` 同时保留优化前的两个真实尾延迟失败：一个 candidate
max 2400.187ms、p95 2144.243ms，另一个 max 2081.018ms、p95 1994.710ms；两次均为
0 error / 0 timeout。这些历史证据解释了为什么引入 consolidated preflight，并不代表
冻结候选的当前状态。

较早的优化候选 `98eef3fb45abb2d5a9d198529445103ceb9d43be` 也曾连续两轮通过
性能门禁；它没有失败，也没有从历史中删除。最终规格审查发现 shell 变量无法完整承载
raw-NUL 输入，因此该候选被输入完整性修复取代。机器报告将它的两轮结果标为
`superseded_candidate_confirmation`。随后候选
`17696138262b8c807852be5baf3c9cb9eccf7c49` 同样连续两轮通过，但最终 edge-case
审查又补齐了 nested decoded NUL、fallback 去重、managed child 信号清理和空 prompt
attachment 边界；其成功结果也作为 superseded evidence 保留。最终候选完成上表两轮正式
门禁，前两代成功候选均未被误记为失败或计入最终 confirmation。

报告只保存聚合统计、固定 commit 与可重建哈希，不保存请求正文、注入结果正文或子进程
原始输出。`result` 保持既有消费者合同，原样使用第二轮聚合；两轮完整聚合保存在
`run_history`。

## 可复现方法

将 `BASE` 与 `CAND` 分别指向上述精确 commit 的独立 worktree，并为每轮选择一个尚不
存在的 `BRAIN` 路径。materializer 遇到已有路径会 fail closed。随后按机器报告中的
`commands` 模板 materialize fixture 并运行 benchmark；不得改变 payload、Python、环境变量、
warmup、样本数、协议或门槛。

机器报告记录并由 contract test 重算 old/candidate hook、payload parser、preflight module、
runner、materializer、payload 与确定性 fixture item 的 SHA-256。索引数据库不是哈希事实源；
它由 materializer 使用 committed item 和 HashingEmbedder 重新生成。

## 发布验收条件

本次 PASS 建立在以下条件同时成立：

1. 校准报告为 calibration 15/15、heldout 11/11、0 FP / 0 FN，且门禁退出码为 0；
2. 固定代码候选的连续两轮正式 30-run 均满足单次小于 2 秒、p95 增量不超过 150ms、
   0 error、0 timeout；
3. targeted、全仓测试、Ruff、shell 语法、隐私合同与 `git diff --check` 均通过；
4. 发布物中保留升级包后的 refresh/repair 指引，不能把代码仓 PASS 误解为旧安装已自动刷新。
