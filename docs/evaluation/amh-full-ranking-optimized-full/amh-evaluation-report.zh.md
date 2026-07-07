# 评测结果

**报告名称**：Agent Memory Hub Evaluation Report
**生成时间**：2026-07-04T05:08:23.572253+00:00
**数据来源**：`memory benchmark system`
**结论**：PASS，cases=240，items=1361，top_k=10。

这份报告使用 AMH 自己的系统级 benchmark 数据生成，不是 OpenViking LOCOMO 横评，也不填充无法复核的第三方数字。它回答的是：当前这套 AMH 链路在本地样本上，能不能正确阻断弱意图、召回目标记忆、通过防火墙，并把上下文可逆地装进 ContextPack。

## 核心指标快照

| 指标 | 结果 |
| --- | --- |
| 总用例 | 240 |
| 失败数 | 0 |
| 弱意图阻断 | 100.00% |
| 可注入问题识别 | 100.00% |
| Recall@10 | 100.00% |
| MRR | 99.78% |
| Firewall include | 100.00% |
| Firewall exclude | 100.00% |
| ContextPack 可逆 | 100.00% |
| top_k | 10 |
| indexed items | 1361 |
| 运行耗时 | 82.982s |

## 完成率指标对比

| 指标 | 完成率 | 样本/口径 | 来源 |
| --- | --- | --- | --- |
| 弱意图阻断 | 100.00% | 12 weak prompts | `metrics.query_gate.block_accuracy` |
| 可注入问题识别 | 100.00% | 228 injectable prompts | `metrics.query_gate.inject_accuracy` |
| Recall@10 | 100.00% | 228 retrieval cases | `metrics.retrieval.recall_at_k` |
| MRR | 99.78% | mean reciprocal rank | `metrics.retrieval.mrr` |
| Firewall include | 100.00% | 4 expected include | `metrics.context.firewall_include_rate` |
| ContextPack 可逆 | 100.00% | 4 packed cases | `metrics.context.pack_reversible_rate` |

## Token 消耗对比

| 指标 | Token | 说明 |
| --- | --- | --- |
| 全文详情预算 | 129771 | 如果把命中 item 的详情全部放入上下文，benchmark 记录的 token 预算。 |
| ContextPack 注入 | 29275 | 经过 locator / overview / detail_uri 分层装载后的实际注入预算。 |
| 节省 Token | 100496 | full_tokens - packed_tokens，负数按 0 处理。 |

ContextPack 节省率：**77.44%**；压缩比：**0.226**。

## 治理与防火墙

| 指标 | 值 | 说明 |
| --- | --- | --- |
| 弱意图阻断样本 | 12 | 继续、好的、确认等不应自动注入的 prompt。 |
| Firewall 覆盖样本 | 228 | 进入检索后接受防火墙决策的样本。 |
| 应注入样本 | 4 | 目标 item 应该进入 ContextPack 的样本。 |
| 应排除样本 | 62 | 低置信、缺来源、过期或敏感 item 应被挡下的样本。 |
| 排除正确率 | 100.0 | 应排除样本中，防火墙确实挡下目标 item 的比例。 |

## 评测口径分层

报告 PASS 不等于 release gate PASS。公开报告只展示当前本地 system benchmark 的可复核结果；发布前仍要单独跑 release gate 和开放式相关性基准。

| 层 | 作用 | 状态来源 |
| --- | --- | --- |
| public_report | 开源展示当前本地可复核结果 | `system_benchmark.passed` |
| system_benchmark | 验证 query gate、retrieval、firewall、context pack 的端到端链路 | `memory benchmark system` |
| release_gate | 发布前质量门禁，必须单独运行 benchmarks/release_gate.py | `benchmarks/release_gate.py` |
| relevance_benchmark | 开放式相关性基准，衡量非精确标题/locator 查询表现 | `benchmarks/benchmark_relevance.py` |
| memorydata_external_loop | 外部横评 source-lock 与可执行入口；MemoryData smoke/full 需依赖、数据集和 endpoint 就绪 | `docs/evaluation/latest-memory-benchmark-report.zh.md` |

## 外部横评状态

AMH 已完成本地 system benchmark 和 MemoryData source-lock；MemoryData smoke/full 需要 `datasets`、`rank_bm25`、四类数据集和 OpenAI-compatible endpoint 就绪后才能写入外部横评结果。OpenViking 仍是设计参考，不作为 AMH 指标来源。

| 参考 | 用途 | 当前状态 |
| --- | --- | --- |
| OpenViking | 参考 context database、文件系统范式、L0/L1/L2 tiered context loading 和 retrieval trajectory 叙事。 | 设计参考，不作为 AMH 评测结果来源。 |
| arXiv 2606.24775 | 参考 agent-native memory evaluation 的系统拆分：表示/存储、抽取、检索/路由、维护。 | 论文口径已引用，不替代 AMH 实测。 |
| OpenDataBox/MemoryData | 统一 MemoryAgentBench、LoCoMo、LongBench、MemBench 的外部 benchmark harness。 | source-lock 已完成；最新状态见 `docs/evaluation/latest-memory-benchmark-report.zh.md`。 |

## 多 Agent 适配矩阵

当前矩阵读取的是 adapter capability 记录：total=16，ready=15，verified=13，runtime_observed=10。

| Agent | 状态 | 证据等级 | 运行观测 | 接入模式 | 阻塞项 |
| --- | --- | --- | --- | --- | --- |
| aider | ready | verified | no | file | - |
| aone_copilot | ready | verified | no | file | - |
| claude_code | ready | verified | yes | command, hook, mcp | - |
| cline | ready | verified | yes | mcp | - |
| codex | ready | verified | yes | file, hook, mcp | - |
| continue_dev | ready | verified | yes | mcp | - |
| cursor | ready | verified | yes | mcp, hook | - |
| github_copilot | ready | verified | no | file | - |
| hermes_agent | ready | verified | yes | mcp | - |
| mulerun | wip | wip | no | file | install path not implemented |
| openclaw | ready | install-ready | no | command, mcp | evidence level is install-ready, not verified; runtime event not observed |
| openhuman | ready | verified | no | file | - |
| opensquilla | ready | verified | yes | file, mcp | - |
| qoder | ready | install-ready | yes | file, hook, mcp | evidence level is install-ready, not verified |
| qoder_work | ready | verified | yes | file, hook, mcp | - |
| wukong | ready | verified | yes | file, hook, mcp | - |

## 数据口径

- Query Gate：只判断该不该进入搜索/注入，不代表最终一定注入。
- Retrieval：使用当前 AMH system benchmark 的 BM25/vector/RRF/MMR/graph 配置和 deterministic hashing embedding。
- Context Firewall：统计应注入样本的 include rate、应排除样本的 exclude rate，以及 ContextPack 可逆性。
- Token：来自 benchmark case 里的 `full_tokens` 和 `packed_tokens`，是 AMH 本地打包预算口径，不是模型供应商计费账单。
- Adapter：来自 `agent_brain.agent_integrations.capabilities`，表示安装/doctor/runtime evidence 状态，不等价于公开任务完成率。
