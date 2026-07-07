# AMH 记忆评估 Loop

> 状态：截至 2026-07-02，AMH 已完成本地 `memory benchmark system`、LongMemEval-S 500-case R@K full、MemoryData source-lock、一键评测入口、MemoryData full-family reference baseline，以及 AMH MemoryData method preset materialization。当前公开报告状态为 `PASS_WITH_MEMORYDATA_FULL`；LongMemEval-S 仍只发布 retrieval-only R@K / MRR，不冒充 QA / Judge full；MemoryData 的 LoCoMo / MemoryAgentBench / LongBench / MemBench AMH adapter 分数尚未发布。

## 为什么要重做评估 Loop

单看 Recall@K 不够。AMH 不是一个纯向量库，也不是只给 prompt 塞历史聊天；它要证明五件事：

1. 该记的事实能被维护成长期 MemoryItem。
2. 该召回的事实能在当前任务里靠前出现。
3. 过期、冲突、弱意图和不该注入的上下文能被挡住。
4. 长程任务里能用分层上下文完成闭环，而不是靠无限长 prompt。
5. 成本、延迟、token、存储和失败类型能被一键复核。

所以评估 loop 不能只接一个榜单。它需要融合四份外部资料的不同口径，再落到 AMH 自己可运行的一键门禁里。

## 四源融合

| 来源 | AMH 采用的评估口径 | 当前落地状态 |
|---|---|---|
| [agentmemory COMPARISON](https://github.com/rohitg00/agentmemory/blob/main/benchmark/COMPARISON.md) | 横向对照 LongMemEval、质量、规模、成本；只采用维度，不冒充第三方表格结果。 | source reference |
| [State-Bench](https://opensource.microsoft.com/blog/2026/05/19/introducing-state-bench-a-benchmark-for-ai-agent-memory/) | 有状态任务闭环：task completion、pass^5、reliability、efficiency、user experience。 | source reference |
| [MemoryAgentBench](https://github.com/HUST-AI-HYZ/MemoryAgentBench) | 能力分型：准确召回 / 测试时学习 / 长程理解 / 冲突解决。 | source reference；MemoryData 里保留可执行 family |
| [OpenViking](https://openviking.ai/) | 公开评测体系参考：LoCoMo、tau2-bench、HotpotQA / KB QA、latency、token cost。 | design reference |

MemoryData / AgentMemory-Bench 外部横评仍采用可复核的源码入口：[OpenDataBox/MemoryData](https://github.com/OpenDataBox/MemoryData)。用户给出的 `https://github.com/MEMTRON/AgentMemory-Bench` 在当前本机匿名 source-lock 中没有拿到可复核 HEAD，因此报告只把它作为 blocked source 记录，不写外部结果。

## LongMemEval Retrieval 表格（加入 AMH）

下面是截图里 `Retrieval Accuracy (LongMemEval)` 的可比核心表格，加上 AMH 本地可复现结果。这里只保留 LongMemEval / LongMemEval-S 相关行；LoCoMo 公开数字放到下一节，避免把不同 benchmark 的百分比排在同一个 R@5 排名里。

| System | Benchmark | R@5 | Notes |
|---|---|---|---|
| **Agent Memory Hub (AMH, BM25/RRF)** | LongMemEval-S | **97.4%** | Local reproducible 500-case R@K-only run; R@10 98.4%, MRR 91.3%. Retrieval-only, no answer generation / judge. Backend: AMH HubIndex + Retriever BM25/RRF (`vector_weight=0.0`). |
| **agentmemory** (BM25 + Vector) | LongMemEval-S | **95.2%** | `all-MiniLM-L6-v2` embeddings, no API key |
| agentmemory (BM25-only) | LongMemEval-S | 86.2% | Fallback when no embedding provider available |
| MemPalace | LongMemEval-S | ~96.6% (self-reported) | Vendor-published number we have not independently reproduced. Vector-only with a larger embedding model and no agent-integration surface (no hooks, no MCP, no multi-agent). |
| oracleagentmemory | LongMemEval | 94.4% (self-reported) | Vendor-published, scored with GPT-5.5 at "xhigh reasoning" and requires an Oracle AI Database. We have not reproduced it. agentmemory's 95.2% uses free local embeddings and no API key. |

表格边界：

- 上游原表格里只有 `agentmemory` 的 95.2% 是该项目自行声明的可复现运行结果；MemPalace、oracleagentmemory 是各自 vendor / self-reported 的 LongMemEval 类数字。
- AMH 的 97.4% 是本机 `LongMemEval-S` 500-case R@K-only full 产物，当前不是上游 `agentmemory` 维护者独立复现的数字。
- AMH 行可以用于给 `rohitg00/agentmemory` 的 `benchmark/COMPARISON.md` 提 PR，但 PR 需要补最终 commit / version。

### 97.4% 的计算口径

AMH 的 `97.4%` 来自 `docs/evaluation/longmemeval-amh-ranking-rk-full.json`：

```text
R@5 = 命中 top-5 的 case 数 / 总 case 数
    = sum(case.recall_at_5) / 500
    = 0.974
```

逐题规则是：每个 LongMemEval-S 问题都有一个或多个 `answer_session_ids`；AMH Retriever 返回 `ranked_session_ids`；如果任一 `answer_session_id` 出现在前 5 个 `ranked_session_ids` 中，这题 `recall_at_5 = 1`，否则为 `0`。最终对 500 题取平均。对应代码在 `agent_brain/evaluation/longmemeval_retrieval.py` 的 `_run_amh_case` 和 `_aggregate_metrics`。

是否和别人同标准：

- 同：都是 LongMemEval-S retrieval R@5，都是判断正确 session 是否进入 top-5，不涉及 answer generation / judge。
- 不完全同：AMH 这条是我们本机 `AMH HubIndex + Retriever BM25/RRF` 的 R@K-only harness；`agentmemory` 的 95.2% 是其项目维护者发布的 BM25 + Vector 结果；MemPalace / oracleagentmemory 是 vendor/self-reported。除非把所有系统放进同一个 runner 复跑，否则只能说“同类 R@5 指标”，不能说“完全同一实验实现”。

## LoCoMo 指标（论文 runner / 本地 MemoryData reference baseline）

上游截图里 Letta / MemGPT 和 Mem0 也有百分比，是因为原表把它们的 LoCoMo published score 放进了同一张横评表，并在 notes 里标成 “Different benchmark”。这类百分比可以作为“公开声称数字”记录，但不应该和 LongMemEval R@5 排序。

LoCoMo 是 QA benchmark，本地 MemoryData 输出的是 EM / F1 / ROUGE-L / Recall@K，而不是 LongMemEval 的 R@5。注意：下面 `gui-owl-bm25` 是本仓库本地跑通的 MemoryData `reference_simple_rag_bm25_ollama` baseline，不是 AMH adapter 接入 MemoryData 后的结果。

| System | Benchmark | Metric / score | Scope | Notes |
|---|---|---|---|---|
| Letta / MemGPT | LoCoMo | 83.2% published score | vendor-published | Different benchmark from LongMemEval; not reproduced in the AMH local runner. |
| Mem0 | LoCoMo | 68.5% published score | vendor-published | Different benchmark from LongMemEval; not reproduced in the AMH local runner. |
| Local MemoryData reference run (`gui-owl-bm25`) | LoCoMo category 1-4 QA | EM 3.90%; F1 13.08%; ROUGE-L Recall 18.36%; Recall@10 7.55% | 1540 / 1540 QA | `reference_simple_rag_bm25_ollama` baseline, not AMH adapter. Official LoCoMo `locomo10` derived category 1-4 QA; excludes adversarial/category 5. Artifact: `docs/evaluation/memorydata-artifacts/full-family/locomo-4cat/`. |
| Local MemoryData reference run (`gui-owl-bm25`) | LoCoMo category 5 adversarial | EM 3.59%; F1 13.18%; ROUGE-L Recall 19.02%; Recall@10 10.09% | 446 / 446 QA | `reference_simple_rag_bm25_ollama` baseline, not AMH adapter. Category 5 adversarial questions, scored separately against `adversarial_answer`; not mixed into the category 1-4 table. Artifact: `docs/evaluation/memorydata-artifacts/full-family/locomo-category5-adversarial/`. |

对外说法建议：

- 可以说：AMH repo 已有 LoCoMo-compatible local full-family artifacts 和一键 runner。
- 不能说：AMH 工具已经在 LoCoMo 上得到上述 EM/F1；当前 LoCoMo 本地 full-family 产物是 MemoryData reference baseline。
- 可以说：Letta / MemGPT 的 `83.2%` 和 Mem0 的 `68.5%` 是上游表引用的 LoCoMo published score。
- 不能说：AMH 的 LoCoMo EM / F1 / ROUGE / Recall@K 和 Letta / MemGPT 的 `83.2%` 或 Mem0 的 `68.5%` 是同一指标；没有同一个 runner 统一复跑前，不能做严格百分比排名。

## 是否能按论文方式做统一排名

可以，但要分清三个层级：

| 层级 | 能不能做 | 含义 | 当前 AMH 状态 |
|---|---|---|---|
| 论文复刻排名 | 可以 | 直接采用论文 / MemoryData 图表里的 12 个系统 + 2 个 reference baseline 的公开结果，按论文每个 workload / metric 排名。 | 可作为背景表，但 AMH 不在论文原始排名里。 |
| AMH 可比排名 | 可以，adapter 已接入，LoCoMo smoke 已通过，full 分数待跑 | 把 AMH 接成 MemoryData 的一个 method preset，使用论文同一 dataset、dataset config、模型 endpoint、judge 和 metric 运行，再把 AMH 插入同一 workload 的排名。 | LongMemEval-S R@K 已有 AMH harness；MemoryData 已新增 `config/hybrid_amh.yaml` materializer，并用同一 `gui-owl-1.5:latest` OpenAI-compatible endpoint 跑通 LoCoMo 1-query smoke；LoCoMo / MemoryAgentBench / LongBench / MemBench 的 AMH adapter full 分数还没有真实跑完。 |
| 单一总榜 | 不建议默认做 | 把 LongMemEval、LoCoMo、MemoryAgentBench、LongBench、MemBench 等不同任务聚成一个分数，需要人为权重或归一化。 | 可以做“加权综合分”作为产品视角，但必须公开权重，不能说这是论文原生结论。 |

最稳的发布方式：

1. 每个 benchmark / metric 单独排名，例如 `LoCoMo F1`、`LoCoMo ROUGE-L Recall`、`LongMemEval R@5`、`MemoryAgentBench AR F1`。
2. 每张表只放同一 runner、同一数据集、同一模型 / judge 下的结果。
3. 如果要总分，用 normalized rank 或 z-score 做二级汇总，并把权重写死在报告里。
4. 在 AMH adapter 跑完同一 MemoryData workload 之前，不把本地 `gui-owl-bm25` baseline 当作 AMH 工具分数。

### AMH MemoryData method preset 当前状态

已完成：

- 本仓库新增 `agent_brain/evaluation/memorydata_amh.py`，负责把 AMH method preset materialize 到外部 MemoryData checkout。
- 实际 cache 已生成 `.cache/external/MemoryData/config/hybrid_amh.yaml` 和 `.cache/external/MemoryData/methods/amh/amh_adapter.py`。
- `.cache/external/MemoryData/utils/agent.py` 已追加带 marker 的 AMH `AgentWrapper` patch：`BEGIN AMH MemoryData adapter patch`。
- `agent_brain/evaluation/memorydata_runner.py` 已支持当 `--memorydata-agent-config config/hybrid_amh.yaml` 时自动 materialize AMH preset。
- `config/hybrid_amh.yaml` 当前使用 `gui-owl-1.5:latest` 和 `http://127.0.0.1:11434/v1`，与本地 reference baseline 使用同一 OpenAI-compatible endpoint。
- materializer 已支持刷新既有 AMH marker block，避免外部 MemoryData cache 保留旧 patch；`AgentWrapper` 初始化会同步创建 `self.client` 供答案生成调用。

可执行 smoke 命令：

```bash
python benchmarks/run_memory_benchmarks.py \
  --run-memorydata-smoke \
  --memorydata-family LoCoMo \
  --memorydata-agent-config config/hybrid_amh.yaml \
  --max-test-queries-ablation 1 \
  --memorydata-timeout-s 600 \
  --output-dir docs/evaluation \
  --format json
```

当前会话前置探测和 smoke 结果：

- endpoint ready：`http://127.0.0.1:11434/v1` 正在监听，模型列表包含 `gui-owl-1.5:latest`。
- MemoryData LoCoMo 数据和 Python 依赖 ready。
- LoCoMo AMH 1-query smoke 已通过：`memorydata_failed_query_count=0`，run record 在 `docs/evaluation/memorydata-artifacts/smoke/locomo/run-record.json`。
- 这仍是 smoke，只证明 dataset / runner / endpoint / AMH adapter / artifact 链路可跑；不能发布 LoCoMo AMH full EM / F1 / ROUGE-L / Recall@K。

### AMH MemoryData full ranking 当前进度

这里的 “full AMH ranking” 指：把 AMH 作为 MemoryData 的 `hybrid_amh` method preset，使用同一 dataset config、同一 `gui-owl-1.5:latest` OpenAI-compatible endpoint、同一 MemoryData metric 逐条跑完完整 workload，然后把 AMH 的分数插入同一 workload 的可比排名。它不是 LongMemEval R@K-only ranking，也不是旧的 `gui-owl-bm25` reference baseline。

独立输出目录：`docs/evaluation/amh-full-ranking/`。这样不会覆盖主报告中已经存在的 `gui-owl-bm25` reference baseline artifact。

| Workload | 状态 | 样本 | AMH 指标 | Raw artifact | 发布边界 |
|---|---|---:|---|---|---|
| MemBench simple | passed | 100 / 100 | EM 94.00%; F1 67.00%; substring EM 95.00%; Recall@10 0.00% | `docs/evaluation/amh-full-ranking/memorydata-artifacts/full-family/membench-simple/outputs/gui-owl-amh/MemBench/membench_simple_light_in200000_size8_shots0_max_samples100_variantbm25-rrf_results.json` | 可作为 MemBench simple full AMH 分数；不能代表 MemBench 全五个 slice。 |
| MemoryAgentBench AR / EventQA | partial only | 2 / 500 | 当前 partial 平均 EM/F1/EventQA Recall 100%，但样本太少 | `docs/evaluation/amh-full-ranking/memorydata-artifacts/full/memoryagentbench-ar-eventqa/outputs/gui-owl-amh/Accurate_Retrieval/eventqa_full_unknown_in800000_size40_shots0_max_samples5_variantbm25-rrf_results.json` | 不可发布 benchmark 分数；前 2 条平均 query time 约 72.84s，500 条预计小时级。 |

对外 PR 还需要带上：

- methodology：`benchmarks/run_longmemeval_retrieval_smoke.py --mode amh-ranking --max-cases 500`
- result artifact：`docs/evaluation/longmemeval-amh-ranking-rk-full.json`
- source dataset：`LongMemEval-S cleaned`，本地路径 `.cache/external/LongMemEval/data/longmemeval_s_cleaned.json`
- version：AMH benchmark 变更提交后的 commit / tag；当前工作区仍是未提交评测状态，不能用 `main@e4828af` 代表最终结果

## Loop 总览

```text
source lock -> dataset materialize -> adapter mapping -> smoke run -> full matrix -> result normalize -> report publish
```

这条 loop 的核心规则是：AMH 本地指标可以直接发布，外部指标必须等真实运行完成后才发布；source-lock、smoke、full matrix 三种状态必须分开写。

| 阶段 | 输入 | 动作 | 输出 | 当前状态 |
|---|---|---|---|---|
| source lock | 四份外部资料、MemoryData repo | 固定 URL、repo、commit、入口命令和引用边界。 | `memorydata-external-benchmark-report.json` | done |
| dataset materialize | MemoryAgentBench / LoCoMo / LongBench / MemBench 数据 | 下载、挂载或声明缺失；缺数据时明确 blocked。 | `.cache/external/MemoryData/datasets/` | done |
| adapter mapping | AMH CLI / SDK / brain dir | 映射 write、retrieve、context evidence、retrieval debug；通过 MemoryData `AgentWrapper` patch 接入。 | `config/hybrid_amh.yaml` + `methods/amh/amh_adapter.py` | materialized |
| smoke run | 最小 EventQA / MemBench one-query config | 在依赖、数据集、endpoint ready 后跑最小链路。 | `docs/evaluation/memorydata-artifacts/smoke/` | done |
| full matrix | AR / TTL / LRU / CR、LoCoMo、LongBench、MemBench 类任务 | 跑完整切片并保存 raw results。 | result JSON / logs / traces | done |
| result normalize | 外部 raw results + AMH 本地指标 | 统一 accuracy、Recall@K、MRR、pass^5、latency、token、storage、失败类型。 | normalized report JSON | done |
| report publish | normalized report + docs contract | 写最新报告、README、预览页、文档契约测试。 | `latest-memory-benchmark-report.zh.md` | full published |

## LongMemEval-S Retrieval Loop

这次评测先从 LongMemEval-S retrieval-only 开始，因为它最接近公开工具常用的 R@5 / R@10 横评口径，也不要求先接完整 generation / judge endpoint。

```text
source lock -> dataset materialize -> retrieval-only smoke -> AMH ranking run -> report publish
```

| 阶段 | 输入 | 动作 | 输出 | 当前状态 |
|---|---|---|---|---|
| source lock | LongMemEval cleaned HF 数据源 | 锁定 `xiaowu0162/longmemeval-cleaned` 和 `longmemeval_s_cleaned.json`。 | source URL | done |
| dataset materialize | HF JSON 文件 | 下载到 `.cache/external/LongMemEval/data/longmemeval_s_cleaned.json`。 | 本地数据文件 | done |
| retrieval-only smoke | 小样本 QA + session evidence | 验证 parser、session ranking 和 R@K 计算链路。 | `docs/evaluation/longmemeval-retrieval-smoke.json` | done |
| AMH ranking run | LongMemEval-S 500-case 样本 | 将 session evidence 写成 MemoryItem，用 AMH Retriever 计算 R@5 / R@10 / MRR。 | `docs/evaluation/longmemeval-amh-ranking-rk-full.json` | done |
| report publish | ranking report + docs contract | 写入最新报告和 README 评估区，只发布可复现数字。 | AMH ranking report published；AMH ranking R@K full published | rk-full-published |

一键 materialize：

```bash
python benchmarks/materialize_memory_eval_datasets.py --dataset longmemeval-s
```

Dry-run 检查：

```bash
python benchmarks/materialize_memory_eval_datasets.py --dataset longmemeval-s --dry-run --format json
```

## 能力与指标矩阵

| 评估维度 | 外部指标 | AMH 本地指标 | 门禁 |
|---|---|---|---|
| 准确召回 | MemoryAgentBench AR、LoCoMo QA、LongMemEval-S、Recall@K / MRR | Recall@10、MRR、词频/BM25、向量召回、RRF 融合 | 候选必须可追溯到 MemoryItem 和 source evidence |
| 测试时学习 | MemoryAgentBench TTL、State-Bench state update tasks | WriteService、MemoryItem 写入审计、runtime ledger、feedback ledger | 新事实必须落到本地事实层，不能只停在 prompt |
| 长程理解 | MemoryAgentBench LRU、LoCoMo long conversation、多跳/时序问题 | locator / overview / detail 分层注入、ContextPack 可逆、token budget | 长上下文只允许分层装载，detail 需要按需取证 |
| 冲突解决 | MemoryAgentBench CR、知识更新、过期/冲突状态处理 | supersession、stale filter、用户/Agent 反馈、成熟度和废止过滤 | 旧事实不得覆盖新证据；冲突必须保留来源边界 |
| 有状态任务闭环 | State-Bench task completion、pass^5、reliability、user experience | 弱意图阻断、可注入识别、防火墙 include/exclude、ContextPack 可逆 | 能完成任务，也要能拒绝不该注入的上下文 |
| 成本与规模 | token / latency / storage / indexed items / scale benchmark | indexed items、运行耗时、top_k、pack reversible、报告生成耗时 | 报告必须同时给准确率和成本边界 |

## 已落地的一键入口

```bash
python benchmarks/run_memory_benchmarks.py --output-dir docs/evaluation
python benchmarks/materialize_memory_eval_datasets.py --dataset all --dry-run --format json
python benchmarks/run_memory_benchmarks.py --run-memorydata-smoke --output-dir docs/evaluation
python benchmarks/run_memory_benchmarks.py --run-memorydata-smoke --memorydata-family LoCoMo --memorydata-agent-config config/hybrid_amh.yaml --max-test-queries-ablation 1 --output-dir docs/evaluation --format json
python benchmarks/run_memory_benchmarks.py --run-memorydata-full --memorydata-family MemoryAgentBench --output-dir docs/evaluation
python benchmarks/run_longmemeval_retrieval_smoke.py --mode lexical-smoke --max-cases 5 --output docs/evaluation/longmemeval-retrieval-smoke.json
python benchmarks/run_longmemeval_retrieval_smoke.py --mode amh-ranking --max-cases 5 --workspace-dir .cache/external/LongMemEval/amh-ranking-workspace-smoke --output docs/evaluation/longmemeval-amh-ranking-smoke.json
python benchmarks/run_longmemeval_retrieval_smoke.py --mode amh-ranking --max-cases 500 --workspace-dir .cache/external/LongMemEval/amh-ranking-workspace-full --output docs/evaluation/longmemeval-amh-ranking-rk-full.json
```

固定产物：

```text
docs/evaluation/latest-memory-benchmark-report.zh.md
docs/evaluation/memorydata-external-benchmark-report.zh.md
docs/evaluation/memorydata-external-benchmark-report.json
docs/evaluation/amh-evaluation-report.zh.md
docs/evaluation/amh-evaluation-report.html
docs/evaluation/longmemeval-retrieval-smoke.json
docs/evaluation/longmemeval-amh-ranking-smoke.json
docs/evaluation/longmemeval-retrieval-rk-full.json
docs/evaluation/longmemeval-amh-ranking-rk-full.json
docs/evaluation/memorydata-artifacts/smoke/memoryagentbench/
docs/evaluation/memorydata-artifacts/smoke/membench/
docs/evaluation/memorydata-artifacts/full/
docs/evaluation/memorydata-artifacts/full-family/
```

当前本地核心指标已经进入最新报告：

| 指标 | 结果 |
|---|---|
| 总用例 | 240 |
| 失败数 | 0 |
| 弱意图阻断 | 100% |
| 可注入问题识别 | 100% |
| Recall@10 | 100% |
| MRR | 99.78% |
| Firewall include / exclude | 100% / 100% |
| ContextPack 可逆 | 100% |
| indexed items | 1287 |

当前 LongMemEval-S lexical R@K full 结果：

| 指标 | 结果 |
|---|---|
| cases | 500 |
| R@5 | 89.0% |
| R@10 | 93.6% |
| MRR | 78.7% |

当前 LongMemEval-S AMH ranking R@K full 结果：

| 指标 | 结果 |
|---|---|
| cases | 500 |
| R@5 | 97.4% |
| R@10 | 98.4% |
| MRR | 91.3% |
| backend | AMH HubIndex + Retriever BM25/RRF pipeline |
| 边界 | retrieval-only，不包含 answer generation / judge |

当前 MemoryData full-family 结果：

| Family | 状态 | 样本 | 关键结果 | Raw artifact |
|---|---|---|---|---|
| MemoryAgentBench / Accurate Retrieval / EventQA | passed | 500 / 500 | `failed_query_count=0`，full artifact published | `docs/evaluation/memorydata-artifacts/full/memoryagentbench-ar-eventqa/` |
| LoCoMo category 1-4 QA | passed | 1540 / 1540 QA | EM 3.90%，F1 13.08%，ROUGE-L Recall 18.36%，Recall@10 7.55% | `docs/evaluation/memorydata-artifacts/full-family/locomo-4cat/` |
| LoCoMo category 5 adversarial | passed | 446 / 446 QA | EM 3.59%，F1 13.18%，ROUGE-L Recall 19.02% | `docs/evaluation/memorydata-artifacts/full-family/locomo-category5-adversarial/` |
| LongBench rep150 | passed | 150 / 150 rows | EM 23.33%，F1 15.33% | `docs/evaluation/memorydata-artifacts/full-family/longbench-rep150/` |
| LongBench-v2 503 full | passed | 503 / 503 rows | EM 27.24%，F1 17.89% | `docs/evaluation/memorydata-artifacts/full-family/longbench-v2-503/` |
| MemBench public slices | passed | 500 / 500 rows | simple / noisy / knowledge_update / highlevel / RecMultiSession slices complete | `docs/evaluation/memorydata-artifacts/full-family/membench-*` |

这些是 full-family 本地产物；对外引用时仍要保留每个 family 的数据集边界和模型 endpoint，不把不同 benchmark 的 EM / F1 写进 LongMemEval R@5 对照列。

## 外部运行边界

MemoryData full matrix 已补齐当前公开 family；对外发布时的主要边界不再是缺数据，而是 benchmark 口径必须分开：

| 前置项 | 状态 | 说明 |
|---|---|---|
| 源码 | ready | OpenDataBox/MemoryData 已 source-lock 到 `e7ecdbe368426bb3b24bbb6126a57ea90eba1dfb` |
| Python 依赖 | ready | `datasets`、`rank_bm25` 和 MemoryData smoke 必需模块可导入 |
| 数据集 | ready | MemoryAgentBench、LoCoMo 4cat、LoCoMo category5、LongBench rep150、LongBench-v2 503、MemBench public slices 已落地 |
| 模型 endpoint | ready for reference baseline and AMH smoke | `http://127.0.0.1:11434/v1` 正在监听，模型为 `gui-owl-1.5:latest`；同一 OpenAI-compatible endpoint 已完成 reference smoke/full，并跑通 AMH MemoryData LoCoMo 1-query smoke |
| LongMemEval QA / Judge | planned | 当前只完成 R@K-only full；若要和 QA/Judge 榜单比较，需要另跑 answer generation 和 judge |

## 发布规则

- 没有跑完外部 smoke：README 只能写“source-lock 和一键入口已完成，smoke/full 被前置条件阻断”。
- 只跑完 smoke：README 可以写“external smoke pass”，不能写 benchmark 结论。
- 跑完一个 family：README 只能写该 family 的样本和指标，不能外推到完整矩阵。
- 跑完 full matrix：才能把外部横评结果放进评估层和公开报告。
- OpenViking、agentmemory COMPARISON、State-Bench、MemoryAgentBench 都是评估口径来源；没有真实运行就不写外部成绩。
