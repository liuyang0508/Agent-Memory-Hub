# MemoryAgentBench 四维 full 覆盖说明

**更新时间**：2026-07-01

这份说明回答一个问题：MemoryAgentBench 论文里的四个核心能力维度，AMH 当前是否已经跑到 full。

## 结论

四个核心维度已经用 MemoryData runner、本地 `gui-owl-1.5` Ollama OpenAI-compatible endpoint 跑完 full 配置，并产出 raw JSON artifact。这里的 full 是指不再使用 `--max_test_queries_ablation 1`；每个维度按当前配置覆盖完整 query 行数。

| 能力维度 | MemoryData / MemoryAgentBench 配置 | full 行数 | 关键指标 | 产物 |
| --- | --- | ---: | --- | --- |
| 准确召回 AR | `benchmark/memoryagentbench/Accurate_Retrieval/config/EventQA/Eventqa_full.yaml` | 500 / 500 | EM 37.00%，F1 59.17%，EventQA Recall 37.00% | `docs/evaluation/memorydata-artifacts/full/memoryagentbench-ar-eventqa/` |
| 测试时学习 TTL | `benchmark/memoryagentbench/Test_Time_Learning/config/ICL/ICL_banking77.yaml` | 100 / 100 | EM 80.00%，Label Accuracy 80.00%，Label Format 100.00% | `docs/evaluation/memorydata-artifacts/full/memoryagentbench-ttl-icl-banking77/` |
| 长程理解 LRU | `benchmark/memoryagentbench/Long_Range_Understanding/config/Detective_QA.yaml` | 71 / 71 | EM 0.00%，F1 11.18%，ROUGE-L Recall 43.97% | `docs/evaluation/memorydata-artifacts/full/memoryagentbench-lru-detectiveqa/` |
| 冲突解决 CR | `benchmark/memoryagentbench/Conflict_Resolution/config/Factconsolidation_mh_6k.yaml` | 100 / 100 | EM 4.00%，Answer Hit 4.00%，Concise Response 100.00% | `docs/evaluation/memorydata-artifacts/full/memoryagentbench-cr-fact-mh-6k/` |

## LRU 补齐说明

MemoryData 原 loader 没开放 `Long_Range_Understanding` split。本轮新增 `agent_brain/evaluation/memoryagentbench_matrix.py`，用于准备四维配置并 patch MemoryData loader，使其支持 `Long_Range_Understanding / detective_qa`。

LRU 跑的是官方 MemoryAgentBench 核心长程理解配置 `Detective_QA`，指标口径是 `exact_match`。InfBench summarization 是额外的 summarization / LLM-as-judge 轨道，需要按 HELMET 口径另跑 judge；它没有混入上面的四维 full 结果。

## 当前边界

- MemoryAgentBench 四维核心 full：已完成。
- MemoryData family smoke：MemoryAgentBench / LoCoMo / LongBench / MemBench 已完成。
- LoCoMo、LongBench、MemBench 的 full-family 运行：未在这份四维表里声明完成，需要作为下一阶段单独跑。
- OpenViking、MEMTRON/AgentMemory-Bench、第三方榜单数字：仍只作为参考口径，不写成 AMH 本机成绩。
