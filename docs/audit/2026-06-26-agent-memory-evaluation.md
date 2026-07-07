# Agent-Memory-Hub 记忆系统指标评估

日期：2026-06-26

范围：基于当前 `codex/loop-contract-v1` worktree、真实 `~/.agent-memory-hub` 本地池、现有 benchmark/test，并结合近期 Agent Memory 论文与最佳实践。

状态：评估报告；未改运行时代码。

## 参考基准

这次评估主要采用四类外部依据：

1. **Are We Ready For An Agent-Native Memory System?**（arXiv:2606.24775，2026-06-23）：把 agent memory 拆成四个数据管理模块：representation/storage、extraction、retrieval/routing、maintenance；并用五个 RQ 评估 task effectiveness、retrieval fidelity、dynamic update robustness、long-horizon stability、operational cost。
2. **Memory in the Age of AI Agents**（arXiv:2512.13564）：强调 forms / functions / dynamics，区分 factual、experiential、working memory，以及 memory formation / evolution / retrieval。
3. **Memory for Autonomous LLM Agents**（arXiv:2603.07670）：把 memory 形式化为 write-manage-read loop，提出 utility、efficiency、adaptivity、faithfulness、governance 五个设计目标。
4. LoCoMo、LongMemEval、MemoryAgentBench、Memora：分别强调长对话、多会话推理、增量多轮交互、动态更新与遗忘感知评估。

Loop Engineering 文章提供产品定位背景：循环需要 automation、worktree、skills、plugins/connectors、sub-agents，以及外部状态记忆。AMH 的差异化位置不是再做一个 runner，而是做多智能体循环的事实层、验证层和治理层。

## 当前实测

### 环境与健康

```text
python -m agent_brain.interfaces.cli doctor
10/10 checks passed
Memory items: 1142
Brain size: 19.4 MB
```

### 内部发布门禁

```text
MEMORY_HUB_TEST_EMBEDDING=1 python benchmarks/release_gate.py --synthetic 80 --format json
```

结果：

| 指标 | 当前值 | 阈值 | 结论 |
|---|---:|---:|---|
| mean_recall_at_10 | 0.408 | 0.750 | FAIL |
| mean_mrr | 0.364 | 0.350 | PASS |
| compression_pass_rate | 1.000 | 1.000 | PASS |
| compression_mean_ratio | 0.317 | <= 0.800 | PASS |
| compression_mean_tokens_saved | 187.6 | >= 1.0 | PASS |
| ml_advisory_pass_rate | 1.000 | 1.000 | PASS |
| ml_advisory_unsafe_promotions | 0 | <= 0 | PASS |

解释：治理、压缩、ML advisory gate 已有较强工程边界；retrieval fidelity 是当前最明显短板。

### 合成检索消融

```text
MEMORY_HUB_TEST_EMBEDDING=1 python benchmarks/benchmark_relevance.py --synthetic 80 --queries 24 --ablation --format json
```

| variant | MRR | P@5 | R@10 | NDCG@10 | query_s | token_cost |
|---|---:|---:|---:|---:|---:|---:|
| bm25_only | 0.462 | 0.242 | 0.458 | 0.433 | 0.015 | 122.2 |
| vector_only | 0.216 | 0.075 | 0.136 | 0.098 | 0.041 | 208.6 |
| rrf | 0.376 | 0.200 | 0.413 | 0.311 | 0.045 | 211.0 |
| rrf_decay | 0.376 | 0.200 | 0.417 | 0.314 | 0.053 | 211.0 |
| rrf_graph | 0.376 | 0.200 | 0.413 | 0.311 | 0.044 | 211.0 |
| rrf_mmr | 0.380 | 0.217 | 0.405 | 0.302 | 4.286 | 210.6 |
| rrf_hopfield | 0.382 | 0.208 | 0.412 | 0.312 | 0.116 | 210.5 |
| rrf_context_firewall | 0.000 | 0.000 | 0.000 | 0.000 | 0.045 | 0.0 |

解释：

- 当前合成集上 BM25-only 反而最好，说明混合融合没有稳定增益。
- vector-only 明显偏弱，说明测试 embedder / embedding text / 语义查询覆盖需要重新校准。
- MMR 延迟异常高，不能直接默认开启。
- context firewall benchmark 变体把所有候选挡掉，应拆成“安全过滤评估”和“检索质量评估”，避免把安全门当成检索失败或成功。

### 真实本地池手工标注 fixture

```text
MEMORY_HUB_TEST_EMBEDDING=1 python benchmarks/benchmark_relevance.py \
  --queries-file tests/fixtures/relevance/hand_labeled_queries.json \
  --queries 6 --format text
```

真实池中 6 条 expected ids 都存在，但结果很弱：

| 指标 | 当前值 |
|---|---:|
| Items indexed | 1140 |
| Queries | 6 |
| MRR | 0.042 |
| Precision@5 | 0.033 |
| Recall@10 | 0.167 |
| NDCG@10 | 0.072 |

解释：AMH 真实长期池里的老记忆、风险类记忆、runtime evidence、false friend、linked association 定位能力不足。这比合成集更接近生产风险。

### 性能

```text
python benchmarks/benchmark_retrieval.py --count 1000
```

结果：

| 指标 | 当前值 |
|---|---:|
| 生成 1000 items | 0.02s |
| 写入 | 0.74s |
| 建索引 | 0.34s |
| 查询 p50 | 0.3ms |
| 查询 p95 | 0.7ms |
| 查询 p99 | 1.0ms |
| drift findings | 143 |

注意：这是 synthetic + local test embedder，不代表真实 embedding / large pool / remote reranker 延迟。

### 测试面

```text
python -m pytest tests/unit/test_relevance_benchmark.py ... tests/unit/test_loop_cli.py -q
74 passed

python -m pytest tests/unit/test_compression_gate.py ... tests/unit/test_write_funnel_contract.py -q
87 passed
```

说明：当前代码对 loop contract、retrieval benchmark、governance、compression、ML advisory 的单元测试覆盖较好，但外部 benchmark 适配和真实池 gold-case 召回仍不足。

### 评估过程中发现的问题

`memory benchmark retrieval --cases tests/fixtures/relevance/hand_labeled_queries.json` 报错：

```text
AttributeError: 'list' object has no attribute 'get'
```

原因是 CLI retrieval gate 的 loader 试图先调用 `payload.get(...)`，再判断 `payload` 是否 list。这个不是核心记忆算法问题，但会影响评估工具可用性，应作为 P0 小修。

## 指标评分

评分标准：0 不存在，1 原型，2 有基础但不稳定，3 可用但存在明显短板，4 强工程化，5 接近论文/生产标杆并有外部 benchmark 证明。

| 维度 | 分数 | 证据 | 主要缺口 |
|---|---:|---|---|
| Representation / Storage | 4.2 | Markdown truth、SQLite FTS/vector/graph 派生索引、refs graph、resources/extractions、sources/writes。 | 外部 benchmark 数据模型导入不足；还没有 content-addressed portable memory 协议。 |
| Extraction / Write Path | 4.0 | WriteService、schema、audit gate、quality warnings、pending fallback、evidence sidecars。 | LLM extraction / consolidation 仍偏保守；没有用 LoCoMo/LongMemEval 的 evidence spans 做写入质量评估。 |
| Retrieval / Routing Fidelity | 2.1 | 有 BM25/vector/RRF/decay/graph/MMR/Hopfield/trace，但当前 release gate R@10 失败，真实 gold fixture R@10=0.167。 | 查询规划、结构过滤、时间/类型/项目 scope routing、hybrid fusion 校准不足。 |
| Maintenance / Lifecycle | 3.8 | maturity、drift、duplicates、tiering、review queue、auto-governance、sync-pending、reindex。 | 缺少论文式 dynamic update robustness 和 localized maintenance cost 曲线。 |
| Dynamic Update Robustness | 3.2 | stale/supersession/conflict/feedback/temporal gate 已有实现。 | 缺 FAMA/invalid-memory penalty；缺多轮 update/delete stress benchmark。 |
| Long-Horizon Stability | 2.7 | 1000 synthetic 性能良好，真实池 1140 items 可索引。 | 老 gold item 召回弱；缺 temporal distance bins、evidence-distance decay 曲线。 |
| Operational Cost / Latency | 4.1 | synthetic p95 0.7ms；压缩平均 ratio 0.317；token cost 可统计。 | MMR 4.286s 不可默认；真实 embedding/reranker/large pool 成本未建持续门禁。 |
| Governance / Privacy / Safety | 4.4 | audit gate、sensitivity、review-required、redacted runtime ledger、human gate、ML default promotion block。 | 缺加密/删除全链路审计、备份/向量索引删除证明、可携带 memory 权限模型。 |
| Observability / Debuggability | 3.8 | doctor、explain trace、data-flow、memory-lineage、benchmarks、public surface locks。 | retrieval gate CLI schema bug；还缺可重放的 failed retrieval case bundle。 |
| Loop Contract / Multi-Agent Governance | 4.3 | goal/state/action/feedback/verifier/budget/stop condition/human gate 已形成产品对象；AMH 不默认当 runner。 | 需要更多真实 loop run 数据、cockpit 趋势、外部 agent 对接样例。 |
| Interop / Portability | 4.2 | CLI、MCP、SDK、hooks、多 adapter、recent resolver bug 已修。 | 还不是标准 portable agent memory protocol；跨工具迁移缺签名/provenance graph。 |
| External Benchmark Readiness | 2.0 | 有内部 benchmark 和手工 fixture。 | 未接入 LoCoMo、LongMemEval、MemoryAgentBench、Memora、DB-Bench。 |

综合判断：**AMH 当前不是“检索最强”的 memory system，而是“治理和循环事实层最完整”的 memory infrastructure。** 如果按论文 2606.24775 的五个 RQ 排序，当前强项是 maintenance、operational governance、loop verification；短板是 retrieval fidelity、dynamic update robustness 的标准化评估、external benchmark comparability。

## 与论文指标的映射

| 2606.24775 维度 | AMH 当前状态 | 评估结论 |
|---|---|---|
| RQ1 Task effectiveness | 还没有 LoCoMo/LongMemEval/DB-Bench 级端到端任务指标；内部 release gate 用 retrieval/compression/advisory 代理。 | 不能声称外部 SOTA；只能声称内部任务门禁存在。 |
| RQ2 Retrieval fidelity | 内部合成 R@10=0.408；真实 hand-labeled R@10=0.167。 | 当前最高优先级短板。 |
| RQ3 Dynamic update robustness | 有 stale/supersession/conflict/feedback，但缺多更新/删除基准。 | 中等；需引入 FAMA/invalid-memory penalty。 |
| RQ4 Long-horizon stability | 真实池规模过千可跑，但老记忆召回弱。 | 中低；需按 temporal distance bins 做曲线。 |
| RQ5 Operational cost | 本地 synthetic 延迟很好，token cost 可测，压缩 gate 强。 | 强，但需要真实 embedding/reranker 成本面板。 |

## 优先整改

### P0：先让评估系统可信

1. 修复 `memory benchmark retrieval --cases` 对 bare JSON list 的兼容 bug。
2. 把 hand-labeled fixture 升级成正式 gate，至少覆盖：
   - stale memory risk；
   - false friend；
   - runtime evidence；
   - linked association；
   - semantic paraphrase；
   - regression memory。
3. 把 release gate 从单一 mean R@10 扩成 per-category threshold，避免 title/project 类掩盖 type/tag/linked 类失败。
4. benchmark 中把 context firewall 拆成两个指标：
   - `retrieval_quality_before_firewall`
   - `safe_injection_after_firewall`

### P1：修检索，不急着堆新 memory 类型

1. 增加 explicit query planning：
   - query 中出现 `fact/episode/handoff/artifact/decision` 时走 type filter；
   - query 命中项目名/adapter 名时走 metadata filter；
   - query 是风险类/状态类时优先 status/runtime evidence boost。
2. 调整 hybrid fusion：
   - 当前合成集 BM25-only 好于 RRF，说明默认 fusion 权重不稳；
   - 应按 category 学习或至少配置 balanced / sparse-leaning / dense-leaning 三套 profile。
3. 对 linked association 使用明确的 `refs.mems` graph traversal，而不是依赖语义相似。
4. 对老记忆增加 temporal distance benchmark，不要只看均值。

### P2：接外部 benchmark

1. LoCoMo import：先做 retrieval-only gold evidence 召回，不急着做最终回答生成。
2. LongMemEval adapter：覆盖 information extraction、multi-session reasoning、temporal reasoning、knowledge updates、abstention。
3. MemoryAgentBench adapter：覆盖 accurate retrieval、test-time learning、long-range understanding、selective forgetting。
4. Memora/FAMA adapter：把 invalidated memory 作为负样本，评估是否误用旧记忆。
5. DB-Bench / Loop task：验证 AMH 对 procedural execution / loop contract 的价值，而不是只做聊天 QA。

## 产品定位建议

AMH 的最大价值不应该宣传成“比 Mem0/Zep/Letta 更会搜”。当前证据不支持。

更准确的定位是：

> AMH 是面向多智能体循环的本地优先记忆治理层：把原始证据、长期知识、检索门禁、反馈账本、human gate 和 loop contract 放在同一个可审计事实层里。

这和 Loop Engineering 的要求一致：循环不只需要能想、能写，还需要能记、能验、能停、能被审计。AMH 当前在“能记、能验、能治理”上已经有强基础；下一阶段必须补齐“能精准召回”和“能在动态更新中不误用旧记忆”。
