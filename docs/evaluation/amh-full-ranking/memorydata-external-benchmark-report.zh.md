# MemoryData 外部横评与 AMH 本地指标

**生成时间**：2026-07-02T18:00:31.692869+00:00
**总状态**：`PASS_WITH_MEMORYDATA_FULL_PARTIAL`

这份报告把 AMH 本地 system benchmark 的核心指标和 MemoryData 外部横评 loop 放在同一个门禁视图里。AMH 本地指标可以直接复核；MemoryData / AgentMemory-Bench 外部结果只有在源码、依赖、数据集和模型 endpoint 都满足并完成运行后才写入，不填充无法复核的外部榜单数字。

## AMH 核心指标

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
| indexed items | 1314 |
| 运行耗时 | 73.762s |

## 外部 Source Lock

| 来源 | 状态 | URL / 路径 | commit / 说明 |
| --- | --- | --- | --- |
| MemoryData | ready | https://github.com/OpenDataBox/MemoryData | e7ecdbe368426bb3b24bbb6126a57ea90eba1dfb |
| MEMTRON/AgentMemory-Bench | blocked | https://github.com/MEMTRON/AgentMemory-Bench | Current public source lock uses OpenDataBox/MemoryData; MEMTRON/AgentMemory-Bench is not treated as an anonymously readable canonical repo. |
| OpenViking | ready | https://openviking.ai/ | Design/reference source, not an AMH result source. |
| arXiv 2606.24775 | ready | https://arxiv.org/abs/2606.24775 | Paper reference for agent-native memory evaluation taxonomy. |

## 记忆评估 Loop（四源融合）

这条 loop 把四类外部口径合并成 AMH 的评估合同：先锁来源，再准备数据和 adapter，再跑 smoke / full matrix，最后只发布可复核结果。

| 来源 | 作用 | 对齐信号 |
| --- | --- | --- |
| agentmemory COMPARISON | 横向对照口径：LongMemEval、质量、规模、成本，不把第三方表格数字冒充 AMH 结果。 | LongMemEval / quality benchmark / scale benchmark / cost |
| State-Bench | 有状态任务闭环口径：任务完成率、可靠性、效率、用户体验。 | task completion / pass^5 / efficiency / user experience |
| MemoryAgentBench | 能力分型口径：准确召回、测试时学习、长程理解、冲突解决。 | 准确召回 / 测试时学习 / 长程理解 / 冲突解决 |
| OpenViking | 公开评测体系参考：LoCoMo、tau2-bench、HotpotQA / KB QA、延迟和 token 成本。 | LoCoMo / tau2-bench / HotpotQA / KB QA / latency / token cost |

| 阶段 | 状态 | 门禁 |
| --- | --- | --- |
| source lock | done | 四份外部资料有固定 URL；MemoryData 本地 repo 有 commit SHA。 |
| dataset materialize | done | MemoryAgentBench / LoCoMo / LongBench / MemBench 数据集本地可读。 |
| adapter mapping | planned | AMH write / retrieve / update / context pack 映射到外部 runner。 |
| smoke run | done | 最小样本在依赖、数据集和 OpenAI-compatible endpoint 全部 ready 后执行。 |
| full matrix | partial | smoke pass 后才跑 AR / TTL / LRU / CR、LoCoMo、State-Bench 类任务。 |
| result normalize | done | 统一 Recall@K、MRR、accuracy、pass^5、latency、token、storage 和失败类型。 |
| report publish | done | 本地指标已发布；外部指标必须区分 source-lock / smoke / full matrix。 |

流程：`source lock -> dataset materialize -> adapter mapping -> smoke run -> full matrix -> result normalize -> report publish`

## 能力与指标矩阵

| 维度 | 外部指标 | AMH 本地指标 | 门禁 |
| --- | --- | --- | --- |
| 准确召回 | MemoryAgentBench AR、LoCoMo QA、LongMemEval-S、Recall@K / MRR | Recall@10、MRR、词频/BM25、向量召回、RRF 融合 | 候选必须可追溯到 MemoryItem 和 source evidence |
| 测试时学习 | MemoryAgentBench TTL、State-Bench state update tasks | WriteService、MemoryItem 写入审计、runtime ledger、feedback ledger | 新事实必须落到本地事实层，不能只停在 prompt |
| 长程理解 | MemoryAgentBench LRU、LoCoMo long conversation、多跳/时序问题 | locator / overview / detail 分层注入、ContextPack 可逆、token budget | 长上下文只允许分层装载，detail 需要按需取证 |
| 冲突解决 | MemoryAgentBench CR、知识更新、过期/冲突状态处理 | supersession、stale filter、用户/Agent 反馈、成熟度和废止过滤 | 旧事实不得覆盖新证据；冲突必须保留来源边界 |
| 有状态任务闭环 | State-Bench task completion、pass^5、reliability、user experience | 弱意图阻断、可注入识别、防火墙 include/exclude、ContextPack 可逆 | 能完成任务，也要能拒绝不该注入的上下文 |
| 成本与规模 | token / latency / storage / indexed items / scale benchmark | indexed items、运行耗时、top_k、pack reversible、报告生成耗时 | 报告必须同时给准确率和成本边界 |

## 发布规则

- AMH 本地指标可以直接发布，但必须带用例数、indexed items、top_k 和运行耗时。
- 外部 source-lock 只证明来源和入口可复核，不等于跑完外部榜单。
- smoke 只证明 adapter / dataset / endpoint 最小链路可跑，不能外推到 full matrix。
- full matrix 必须按来源维度写清 benchmark family、样本范围、指标和失败类型。
- OpenViking、agentmemory COMPARISON、State-Bench、MemoryAgentBench 都是评估口径来源；没有真实运行就不写外部成绩。

## Dataset Provenance Audit

**生成时间**：2026-07-02T18:00:32.160322+00:00

这份审计只回答一个问题：当前评测结果依赖的数据集是否与论文 / 官方 benchmark / 常见记忆工具评测口径同源，以及哪些结果还不能当作 full benchmark 成绩。

## 分档定义

| 档位 | 含义 |
| --- | --- |
| A | A：论文/官方同源 full 可比 |
| B | B：官方同源但有派生/子集边界 |
| C | C：smoke / adapter 验证，不可当 benchmark 成绩 |

## 数据集与结果分档

| ID | Benchmark | 档位 | 就绪 | 范围 | 边界 |
| --- | --- | --- | --- | --- | --- |
| longmemeval_s_cleaned | LongMemEval | C：smoke / adapter 验证，不可当 benchmark 成绩 | ready | Retrieval smoke / AMH ranking smoke only in the current report. | 当前只证明 retrieval loop；未跑 full answer generation 和 judge，不可写成 LongMemEval full 成绩。 |
| longmemeval_oracle | LongMemEval | C：smoke / adapter 验证，不可当 benchmark 成绩 | missing | Not materialized in the current local run. | 本地未就绪；不能参与下一阶段 full 结论。 |
| memoryagentbench_hf | MemoryAgentBench | A：论文/官方同源 full 可比 | ready | Four core capability tracks: AR / TTL / LRU / CR. | Core four-dimensional representative configs are complete locally; InfBench summarization LLM-as-judge remains a separate track. |
| locomo_raw | LoCoMo | B：官方同源但有派生/子集边界 | ready | Official raw source for downstream LoCoMo 4-category artifact. | Raw source only; benchmark scoring uses the derived MemoryData-compatible 4-category file. |
| locomo_4cat_dist | LoCoMo | B：官方同源但有派生/子集边界 | ready | Question-answering categories 1-4 from official LoCoMo raw data. | 官方原始数据派生：保留 category 1-4，排除 adversarial/category 5；不是重新下载的第三方私有副本。 |
| longbench_rep150_proportional | LongBench | B：官方同源但有派生/子集边界 | ready | Deterministic MemoryData-compatible 150-row proportional subset. | 150-row proportional subset；不是 LongBench-v2 503-question full set。 |
| longbench_v2_503_full | LongBench | A：论文/官方同源 full 可比 | ready | Official 503-question LongBench-v2 full set through a MemoryData-compatible save_to_disk path. | Full dataset source is official; score comparability still depends on the same model and judging/evaluation harness. |
| membench_firstagent | MemBench | B：官方同源但有派生/子集边界 | ready | FirstAgent simple/noisy/knowledge_update/highlevel/RecMultiSession slices are present locally. | FirstAgent 五个 public slice 是 MemoryData-compatible full-family 口径；仍需和其他私有/扩展 MemBench 口径区分。 |

## 已发布结果分档

| 结果 | Benchmark | 档位 | 样本范围 | 边界 |
| --- | --- | --- | --- | --- |
| memoryagentbench-ar-eventqa | MemoryAgentBench | C：smoke / adapter 验证，不可当 benchmark 成绩 | 0 / 500 | 未达到预期行数，不能作为 full 成绩。 |
| memoryagentbench-ttl-icl-banking77 | MemoryAgentBench | C：smoke / adapter 验证，不可当 benchmark 成绩 | 0 / 100 | 未达到预期行数，不能作为 full 成绩。 |
| memoryagentbench-lru-detectiveqa | MemoryAgentBench | C：smoke / adapter 验证，不可当 benchmark 成绩 | 0 / 71 | 未达到预期行数，不能作为 full 成绩。 |
| memoryagentbench-cr-fact-mh-6k | MemoryAgentBench | C：smoke / adapter 验证，不可当 benchmark 成绩 | 0 / 100 | 未达到预期行数，不能作为 full 成绩。 |
| memorydata-membench-simple-full | MemBench | B：官方同源但有派生/子集边界 | 100 / 100 rows | MemBench public FirstAgent simple slice full. |

## 来源与行业使用证据

### LongMemEval / longmemeval_s_cleaned

- 官方来源：LongMemEval official benchmark
- URL：https://github.com/xiaowu0162/longmemeval; https://arxiv.org/abs/2410.10813
- 常见使用口径：Used by public memory systems for LongMemEval-S retrieval and QA comparisons.
- 本地路径：`.cache/external/LongMemEval/data/longmemeval_s_cleaned.json`
- 本地结果：No completed benchmark result in current report.

### LongMemEval / longmemeval_oracle

- 官方来源：LongMemEval official benchmark
- URL：https://github.com/xiaowu0162/longmemeval; https://arxiv.org/abs/2410.10813
- 常见使用口径：Oracle-style LongMemEval splits are often used for retrieval sanity checks.
- 本地路径：`.cache/external/LongMemEval/data/longmemeval_oracle.json`
- 本地结果：No completed benchmark result in current report.

### MemoryAgentBench / memoryagentbench_hf

- 官方来源：MemoryAgentBench official dataset and configs
- URL：https://github.com/HUST-AI-HYZ/MemoryAgentBench; https://huggingface.co/datasets/ai-hyz/MemoryAgentBench
- 常见使用口径：MemoryData unifies MemoryAgentBench as one of its four benchmark families.
- 本地路径：`.cache/external/MemoryData/datasets/MemoryAgentBench/eval_dataset_collection`
- 本地结果：No MemoryAgentBench full result in current report.

### LoCoMo / locomo_raw

- 官方来源：LoCoMo official locomo10 release
- URL：https://github.com/snap-research/locomo; https://snap-research.github.io/locomo/
- 常见使用口径：LoCoMo is widely used for long-term conversational memory QA evaluation.
- 本地路径：`.cache/external/MemoryData/datasets/LoCoMo/locomo10.json`
- 本地结果：No completed benchmark result in current report.

### LoCoMo / locomo_4cat_dist

- 官方来源：LoCoMo official locomo10 release
- URL：https://github.com/snap-research/locomo; https://snap-research.github.io/locomo/
- 常见使用口径：Common memory-tool reports use LoCoMo QA with 10 conversations and 1540 category 1-4 questions.
- 本地路径：`.cache/external/MemoryData/datasets/LoCoMo/rq1_4cat_600_dist/locomo_4cat_600_dist.json`
- 本地结果：Dataset ready; only MemoryData smoke is completed in current report.

### LongBench / longbench_rep150_proportional

- 官方来源：LongBench-v2 official Hugging Face dataset
- URL：https://huggingface.co/datasets/THUDM/LongBench-v2; https://arxiv.org/abs/2412.15204
- 常见使用口径：MemoryData includes LongBench as a benchmark family for long-context reasoning.
- 本地路径：`.cache/external/MemoryData/datasets/longBench_rep150_proportional/datasets`
- 本地结果：Dataset ready as 150-row subset; only MemoryData smoke is completed in current report.

### LongBench / longbench_v2_503_full

- 官方来源：LongBench-v2 official Hugging Face dataset
- URL：https://huggingface.co/datasets/THUDM/LongBench-v2; https://arxiv.org/abs/2412.15204
- 常见使用口径：MemoryData includes LongBench as a benchmark family for long-context reasoning.
- 本地路径：`.cache/external/MemoryData/datasets/longBench_v2_503_full/datasets`
- 本地结果：LongBench-v2 503 full dataset may be ready, but no completed 503 full result is in the current report.

### MemBench / membench_firstagent

- 官方来源：MemBench public FirstAgent JSON slices
- URL：https://github.com/import-myself/Membench; https://github.com/OpenDataBox/MemoryData
- 常见使用口径：MemoryData includes MemBench as a benchmark family; Letta issue trackers also reference MemBench as a standardized memory benchmark candidate.
- 本地路径：`.cache/external/MemoryData/datasets/MemBench/MemData/FirstAgent`
- 本地结果：MemBench FirstAgent five-slice full completed.

## 下一阶段门禁

**是否允许直接进入 full-family 跑分**：否

必须先完成：

- LoCoMo：确认 MemoryData full preset 是否接受 4-category derived file；full 结果必须标注为 category 1-4 QA，不含 adversarial/category 5。
- LongBench：补跑 THUDM/LongBench-v2 503-question full；rep150 只能作为 B 档子集结果。
- MemBench：从 simple smoke 扩展到 FirstAgent 五个 slice full，并逐 slice 发布样本数和指标。
- LongMemEval：retrieval smoke 不能冒充 full QA；若要横向对比工具榜单，需要补 answer generation / judge 或明确只比较 R@K。

## LongMemEval-S Retrieval Loop

这条子 loop 先对齐公开工具常用的 LongMemEval-S R@K 口径：先下载 cleaned 数据，再做 retrieval-only smoke，最后再接 AMH ranking run。它不依赖 full generation / judge endpoint。

| 阶段 | 状态 | 门禁 |
| --- | --- | --- |
| source lock | done | LongMemEval cleaned 数据源锁定到 Hugging Face xiaowu0162/longmemeval-cleaned。 |
| dataset materialize | done | 本地存在 longmemeval_s_cleaned.json，且文件非空。 |
| retrieval-only smoke | planned | 先跑小样本 R@5/R@10，不依赖外部 LLM judge。 |
| AMH ranking run | waiting-smoke | 把 session evidence 写成 MemoryItem，再用 AMH retriever 计算 R@K。 |
| report publish | planned | 只有跑出本地可复现指标后，才能写 LongMemEval-S R@K 数字。 |

| 数据 | 状态 | 来源 | 本地路径 |
| --- | --- | --- | --- |
| LongMemEval-S cleaned | ready | https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_s_cleaned.json | `.cache/external/LongMemEval/data/longmemeval_s_cleaned.json` |

一键 materialize：

```bash
python benchmarks/materialize_memory_eval_datasets.py --dataset longmemeval-s --cache-root .cache/external --memorydata-repo .cache/external/MemoryData
```

## LongMemEval-S QA / Judge Loop

这条子 loop 单独追踪 answer generation 与 LLM-as-judge。它不能和 R@K-only retrieval 分数混写；只有 generation result 和 judge sidecar 都覆盖 full case 时，才可称为 LongMemEval QA/Judge full。

| 阶段 | 状态 | 门禁 |
| --- | --- | --- |
| source lock | done | LongMemEval-S cleaned 数据源锁定；QA/Judge 不复用 MemoryAgentBench 300-QA 派生口径冒充 500 full。 |
| dataset materialize | done | 本地存在 LongMemEval-S cleaned 数据。 |
| answer generation | planned | 必须保存逐题 output 的 generation *_results.json。 |
| LLM-as-judge | blocked | 必须保存 sidecar judge JSON，且 judged_rows 覆盖 supported_rows。 |
| report publish | planned | generation 与 judge 分数单独发布，不替代 retrieval R@K。 |

| 项 | 状态 | 样本 | 指标 | 产物 |
| --- | --- | --- | --- | --- |
| Generation | missing | - | - | - |
| Judge | missing | 0 / 0 | - | - |

## MemoryData 外部横评

**执行模式**：`full`

| 前置项 | 状态 | 说明 |
| --- | --- | --- |
| 源码 | ready | .cache/external/MemoryData |
| Python 依赖 | ready | required modules importable |
| 数据集 | ready | all family datasets present |
| 模型 endpoint | ready | TCP reachable: 127.0.0.1:11434 |

| Benchmark family | 配置 | 数据集状态 |
| --- | --- | --- |
| MemoryAgentBench | `benchmark/memoryagentbench/Accurate_Retrieval/config/EventQA/Eventqa_full.yaml` | ready |
| LoCoMo | `benchmark/locomo/config/Locomo_qa_4cat_600_dist.yaml` | ready |
| LoCoMoCategory5 | `benchmark/locomo/config/Locomo_qa_category5_adversarial.yaml` | ready |
| LongBench | `benchmark/longbench/config/LongBench_rep150_proportional.yaml` | ready |
| LongBenchV2Full | `benchmark/longbench/config/LongBench_v2_503_full.yaml` | ready |
| MemBench | `benchmark/membench/config/MemBench_simple.yaml` | ready |

## MemoryAgentBench 四维 Full 结果

这部分只统计 MemoryAgentBench 核心四维：AR / TTL / LRU / CR。LRU 使用 Detective_QA exact_match 路径；InfBench summarization 需要按 HELMET 口径做 LLM-as-judge，未混入这里的四维 full 结果。

| 维度 | 状态 | 样本行数 | 关键指标 | 产物 |
| --- | --- | --- | --- | --- |
| 准确召回 AR | missing | 0 / 500 | - | - |
| 测试时学习 TTL | missing | 0 / 100 | - | - |
| 长程理解 LRU | missing | 0 / 71 | - | - |
| 冲突解决 CR | missing | 0 / 100 | - | - |

## MemoryData Full-family 结果

这部分统计 MemoryData 其他 family 的 full-family 本地 artifact：LoCoMo 4-category QA、LongBench 150-row proportional subset、MemBench FirstAgent 五个 slice。它们与论文 full / 第三方榜单的边界由 Dataset Provenance Audit 单独标注。

| 结果 | Benchmark | 状态 | 样本范围 | 关键指标 | 边界 | 产物 |
| --- | --- | --- | --- | --- | --- | --- |
| LoCoMo 4cat QA full | LoCoMo | missing | 0 / 1540 QA | - | LoCoMo official locomo10 derived category 1-4 QA; excludes adversarial/category 5. | - |
| LoCoMo category5 adversarial full | LoCoMoCategory5 | missing | 0 / 446 QA | - | LoCoMo official locomo10 category 5 adversarial questions, scored separately against adversarial_answer; not mixed into the category 1-4 QA benchmark. | - |
| LongBench rep150 proportional full | LongBench | missing | 0 / 150 rows | - | MemoryData deterministic 150-row proportional subset; not THUDM LongBench-v2 503-question full. | - |
| LongBench-v2 503-question full | LongBenchV2Full | missing | 0 / 503 rows | - | Official THUDM LongBench-v2 503-question full set through MemoryData-compatible loader. | - |
| MemBench simple full | MemBench | passed | 100 / 100 rows | EM 94.00%; F1 67.00%; Recall@10 0.00% | MemBench public FirstAgent simple slice full. | docs/evaluation/amh-full-ranking/memorydata-artifacts/full-family/membench-simple/outputs/gui-owl-amh/MemBench/membench_simple_light_in200000_size8_shots0_max_samples100_variantbm25-rrf_results.json |
| MemBench noisy full | MemBench | missing | 0 / 100 rows | - | MemBench public FirstAgent noisy slice full. | - |
| MemBench knowledge_update full | MemBench | missing | 0 / 100 rows | - | MemBench public FirstAgent knowledge_update slice full. | - |
| MemBench highlevel full | MemBench | missing | 0 / 150 rows | - | MemBench public FirstAgent highlevel slice full. | - |
| MemBench RecMultiSession full | MemBench | missing | 0 / 50 rows | - | MemBench public FirstAgent RecMultiSession slice full. | - |

## 运行记录

| 名称 | 状态 | 命令 / 原因 | 产物 |
| --- | --- | --- | --- |
| memorydata-membench-full | passed | `python main.py --agent_config config/hybrid_amh.yaml --dataset_config benchmark/membench/config/MemBench_simple.yaml --max_test_queries_ablation 100 --artifact_root docs/evaluation/amh-full-ranking/memorydata-artifacts/full-family/membench-simple` | docs/evaluation/amh-full-ranking/memorydata-artifacts/full-family/membench-simple |

## 一键命令

```bash
python benchmarks/run_memory_benchmarks.py --run-longmemeval-smoke --memorydata-agent-config config/hybrid_amh.yaml --output-dir docs/evaluation
```

如果外部数据集和 endpoint 已准备好，可以同时打开 MemoryData 外部 smoke：

```bash
python benchmarks/run_memory_benchmarks.py --run-longmemeval-smoke --run-memorydata-smoke --memorydata-agent-config config/hybrid_amh.yaml --output-dir docs/evaluation
```
