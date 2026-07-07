# Dataset Provenance Audit

**生成时间**：2026-07-01T20:06:23.243661+00:00

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
| membench_firstagent | MemBench | B：官方同源但有派生/子集边界 | ready | FirstAgent simple/noisy/knowledge_update/highlevel/RecMultiSession slices are present locally. | FirstAgent 五个 public slice 是 MemoryData-compatible full-family 口径；仍需和其他私有/扩展 MemBench 口径区分。 |

## 已发布结果分档

| 结果 | Benchmark | 档位 | 样本范围 | 边界 |
| --- | --- | --- | --- | --- |
| memoryagentbench-ar-eventqa | MemoryAgentBench | A：论文/官方同源 full 可比 | 500 / 500 | MemoryAgentBench four-dimensional full artifact. |
| memoryagentbench-ttl-icl-banking77 | MemoryAgentBench | A：论文/官方同源 full 可比 | 100 / 100 | MemoryAgentBench four-dimensional full artifact. |
| memoryagentbench-lru-detectiveqa | MemoryAgentBench | A：论文/官方同源 full 可比 | 71 / 71 | MemoryAgentBench four-dimensional full artifact. |
| memoryagentbench-cr-fact-mh-6k | MemoryAgentBench | A：论文/官方同源 full 可比 | 100 / 100 | MemoryAgentBench four-dimensional full artifact. |
| memorydata-locomo-4cat-full | LoCoMo | B：官方同源但有派生/子集边界 | 1540 / 1540 QA | LoCoMo official locomo10 derived category 1-4 QA; excludes adversarial/category 5. |
| memorydata-longbench-rep150-full | LongBench | B：官方同源但有派生/子集边界 | 150 / 150 rows | MemoryData deterministic 150-row proportional subset; not THUDM LongBench-v2 503-question full. |
| memorydata-membench-simple-full | MemBench | B：官方同源但有派生/子集边界 | 100 / 100 rows | MemBench public FirstAgent simple slice full. |
| memorydata-membench-noisy-full | MemBench | B：官方同源但有派生/子集边界 | 100 / 100 rows | MemBench public FirstAgent noisy slice full. |
| memorydata-membench-knowledge-update-full | MemBench | B：官方同源但有派生/子集边界 | 100 / 100 rows | MemBench public FirstAgent knowledge_update slice full. |
| memorydata-membench-highlevel-full | MemBench | B：官方同源但有派生/子集边界 | 150 / 150 rows | MemBench public FirstAgent highlevel slice full. |
| memorydata-membench-recmultisession-full | MemBench | B：官方同源但有派生/子集边界 | 50 / 50 rows | MemBench public FirstAgent RecMultiSession slice full. |
| longmemeval-lexical-rk-full | LongMemEval | B：官方同源但有派生/子集边界 | 500 / 500 cases | LongMemEval lexical R@K full；只比较 retrieval R@K/MRR，不包含 answer generation / judge。 |
| longmemeval-amh-ranking-rk-full | LongMemEval | B：官方同源但有派生/子集边界 | 500 / 500 cases | LongMemEval AMH ranking R@K full；只比较 retrieval R@K/MRR，不包含 answer generation / judge。 |
| longmemeval-lexical-retrieval-smoke | LongMemEval | C：smoke / adapter 验证，不可当 benchmark 成绩 | 5 cases | LongMemEval lexical retrieval smoke；不包含 answer generation / judge。 |
| longmemeval-amh-ranking-smoke | LongMemEval | C：smoke / adapter 验证，不可当 benchmark 成绩 | 5 cases | LongMemEval AMH ranking smoke；不包含 answer generation / judge。 |

## 来源与行业使用证据

### LongMemEval / longmemeval_s_cleaned

- 官方来源：LongMemEval official benchmark
- URL：https://github.com/xiaowu0162/longmemeval; https://arxiv.org/abs/2410.10813
- 常见使用口径：Used by public memory systems for LongMemEval-S retrieval and QA comparisons.
- 本地路径：`.cache/external/LongMemEval/data/longmemeval_s_cleaned.json`
- 本地结果：LongMemEval-S 500-case R@K-only full completed; generation/judge not run.

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
- 本地结果：MemoryAgentBench AR / TTL / LRU / CR four-dimensional full completed.

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
- 本地结果：LoCoMo category 1-4 QA full completed: 1540 / 1540 QA.

### LongBench / longbench_rep150_proportional

- 官方来源：LongBench-v2 official Hugging Face dataset
- URL：https://huggingface.co/datasets/THUDM/LongBench-v2; https://arxiv.org/abs/2412.15204
- 常见使用口径：MemoryData includes LongBench as a benchmark family for long-context reasoning.
- 本地路径：`.cache/external/MemoryData/datasets/longBench_rep150_proportional/datasets`
- 本地结果：LongBench rep150 proportional full completed: 150 / 150 rows.

### MemBench / membench_firstagent

- 官方来源：MemBench public FirstAgent JSON slices
- URL：https://github.com/import-myself/Membench; https://github.com/OpenDataBox/MemoryData
- 常见使用口径：MemoryData includes MemBench as a benchmark family; Letta issue trackers also reference MemBench as a standardized memory benchmark candidate.
- 本地路径：`.cache/external/MemoryData/datasets/MemBench/MemData/FirstAgent`
- 本地结果：MemBench FirstAgent five-slice full completed.

## 下一阶段门禁

**是否允许直接进入 full-family 跑分**：是

必须先完成：
