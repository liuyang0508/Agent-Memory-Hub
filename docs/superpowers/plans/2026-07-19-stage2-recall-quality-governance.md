# 阶段二：召回质量增强实施计划

> 状态：阶段一已通过；本计划从 fresh replay 红灯开始，不继承旧 PASS 结论。

**目标：** 建立可复现、可分层、隐私安全的召回质量事实源，关闭当前 4 个 fresh
漏召回，并把 production replay、时序、拒答、项目边界和端到端注入质量变成 required
gate。

**原则：** 保留当前“完整问题语义路由 + QuerySignal 词项路由 + 原始 BM25 兜底”。
不再新增一套主观分词器；先修证据、时序误判和治理边界。所有候选仍必须经过 Recall
Admission、Injection Gateway 与 ContextFirewall，shadow 候选不得进入注入集合。

## 已冻结红灯

冻结提交 `adcb39c60887ad3317f2726558608ebc49346aa2` 上运行：

```bash
MEMORY_HUB_TEST_EMBEDDING=1 .venv/bin/python -m pytest \
  tests/system/test_dual_route_recall_matrix.py -q
```

结果：`2 failed, 21 passed`。旧 committed calibration 报告不可复现：

- `semantic-zh-02`、`semantic-zh-04`：候选已找到，但稳定 decision 被误判为
  `stale_positive_state`；
- `multi-zh-01`、`multi-ru-02`：正确向量结果本应排名第一，但在 retrieval 的
  `temporal_state_filter` 被当作过期浏览器状态剔除；
- 根因是固定日期夹具随墙上时间漂移，加上 TemporalStateGate 把“当前任务”“恢复”或
  “browser”这类宽泛词组合误当成短期运行状态。

旧报告保留为历史 artifact，但不得继续标作当前代码 PASS。

---

## Task 1：冻结时间稳定的评测合同

**Files**

- Modify: `agent_brain/memory/governance/temporal_state.py`
- Modify: `agent_brain/memory/recall/retrieval_temporal.py`
- Modify: `agent_brain/memory/recall/retrieval.py`
- Modify: `agent_brain/memory/context/context_firewall.py`
- Modify: `agent_brain/memory/context/injection_gateway.py`
- Modify: `tests/system/test_dual_route_recall_matrix.py`
- Modify: `tests/unit/test_temporal_state.py`
- Modify: `tests/unit/test_routed_retrieval.py`

### Step 1：先写失败测试

覆盖：

1. 普通 decision 中“恢复历史决策”“提供给当前任务”不属于短期状态；
2. 客观配置 fact 中“browser proxy timeout 30s”不因年龄超过 2 天被剔除；
3. 带 `state/current-state/runtime/status` 强标签的成功、失败或可用性观察仍按 TTL
   过期；
4. Retriever 与 Gateway 接受可选 `temporal_now`，默认仍使用真实当前时间；评测显式
   传入冻结时间；
5. 同一 fixture 在任意墙上日期运行得到相同结果。

### Step 2：收窄 TemporalStateGate

- 强状态 tag 或 `signal` type 可单独建立状态语义；
- 宽泛正文词不能单独把 fact/decision 变成 current state；
- positive/negative 词只有与明确运行状态 anchor 共现时才形成短期状态；
- `current task`、`restore decision`、`browser timeout configuration` 建立稳定事实回归；
- 不改现有显式 validity/scope mismatch 合同。

### Step 3：注入确定性时钟

Retriever temporal stage、ContextFirewall 和 Gateway 增加只用于依赖注入的可选 `now`；
生产默认值不变。system fixture 固定 `evaluation_now`，报告记录该值。

### Step 4：验证红灯转绿

```bash
.venv/bin/python -m pytest tests/unit/test_temporal_state.py \
  tests/unit/test_routed_retrieval.py -q
MEMORY_HUB_TEST_EMBEDDING=1 .venv/bin/python -m pytest \
  tests/system/test_dual_route_recall_matrix.py -q
```

期望：四个漏召回关闭，旧 calibration/heldout fresh 结果重新可复现。

---

## Task 2：建立三分、追加式 production replay corpus

**Files**

- Create: `tests/fixtures/recall_quality_production_replay_v1.json`
- Create: `agent_brain/evaluation/recall_quality_corpus.py`
- Create: `tests/unit/test_recall_quality_corpus.py`
- Create: `tests/system/test_recall_quality_replay.py`

### Step 1：冻结 schema

每个 case 必须包含：

- `id`、`split`、`category`、`language`、`query`；
- `expected_item_ids`、`prohibited_item_ids`；
- `expected_admission`、`expected_answerability`、`expected_temporal`、
  `expected_abstention`、`expected_injection`；
- `project_scope` 与 `scope_source`；
- 去敏来源类别和不可逆 `source_digest`，不含 session id、原始路径或私有正文。

允许 split 仅为 `calibration`、`heldout`、`production_replay`。fixture 只能追加；修改或
删除已有 case 必须提升 schema/corpus version 并生成差异报告。

### Step 2：加入去敏真实 replay

至少覆盖：长中文、关键词提取质疑、hooks 召回确认、中英混合、日志/命令、弱跟进、
项目错配、过期状态、冲突事实、无充分证据拒答、多模态占位。production replay 使用
人工去敏且可公开的 query，不直接导出 runtime 原始 prompt。

### Step 3：验证 split 隔离

- case id 不重复；
- production replay 的 query/source digest 不与 calibration/heldout 重合；
- 同一个目标 item 可跨 split，但 query 和来源证据不得复用；
- 每个关键 category 至少有 heldout 或 production replay；
- corpus hash、case count 和 category count 写入报告。

---

## Task 3：让 runtime gap/replay 默认不保存原始 prompt

**Files**

- Modify: `agent_brain/memory/governance/recall_events.py`
- Modify: `agent_brain/memory/governance/recall_gap_clustering.py`
- Modify: `agent_brain/platform/telemetry_safety.py`
- Modify: `agent_brain/interfaces/cli/commands/query.py`
- Modify: `tests/unit/test_recall_events.py`
- Modify: `tests/unit/test_data_flow_ledger.py`

### Step 1：先写隐私失败测试

用 email、JWT 形状、绝对路径、私有 item 片段和唯一 prompt sentinel 调用
`record_gap`，断言 JSONL、CLI 输出、cluster 和 replay export 均不含原文。

### Step 2：版本化 GapRecord

新写入记录保存：`query_digest`、`query_shape`、语言/类别、封闭 reason、去敏 evidence、
adapter、project/scope hash；不保存 `query` 和 `normalized_query` 原文。reader 继续兼容
旧记录，但输出默认 redacted。

### Step 3：安全 replay export

默认 export 只输出 digest 和派生特征。只有显式 `--include-redacted-query` 且通过
public hygiene 后才输出人工去敏文本；任何公开/committed corpus 都必须走人工审核。

---

## Task 4：增加不扩权的跨项目 shadow 诊断

**Files**

- Modify: `agent_brain/memory/recall/routed_types.py`
- Modify: `agent_brain/memory/recall/retrieval.py`
- Modify: `agent_brain/interfaces/cli/routed_query.py`
- Modify: `agent_brain/memory/governance/recall_events.py`
- Modify: `tests/unit/test_routed_retrieval.py`
- Modify: `tests/unit/test_routed_cli.py`

### Step 1：冻结安全合同

- 显式 `--project` 永远是 hard filter；
- shadow 只返回候选 id hash、候选 project、route/reason/score bucket；
- shadow hit 不进入 `RoutedSearchResult.hits`、Gateway、ContextPack、access count；
- private/secret 或跨 tenant item 不得成为 shadow telemetry；
- 默认关闭，用 feature flag/显式诊断开启。

### Step 2：实现 `ProjectShadowTrace`

仅在 hard project scope 且主路径空/低充分性时运行小 top-K 全局诊断。输出稳定 reason
`possible_project_mismatch`，不自动改变 scope，不自动重试注入。

### Step 3：端到端安全回归

构造 project A query 实际匹配 project B 的 case，证明主注入为空、shadow 记录 project
B 的低敏诊断、Gateway 从未看到 B 的正文。

---

## Task 5：生成六层机器可读质量报告

**Files**

- Create: `agent_brain/evaluation/recall_quality.py`
- Create: `scripts/check-recall-quality.py`
- Create: `tests/unit/test_recall_quality_report.py`
- Create: `docs/evaluation/stage2-recall-quality-report.json`
- Create: `docs/evaluation/stage2-recall-quality-readiness.zh.md`

报告必须分别输出：

1. retrieval：R@K、MRR、FP/FN；
2. admission：reason 分布、误拒/误放；
3. answerability：supported/partial/insufficient；
4. temporal：current/stale/superseded/conflict；
5. abstention：precision/recall；
6. injection：Gateway include/exclude、project mismatch、token cost。

每层按 split、adapter、project scope、language、category 分桶。禁止用 retrieval PASS
替代 injection PASS。

### 冻结阈值

- calibration、heldout、production replay：prohibited injection = 0；
- 41-case 安全夹具：0 FP / 0 FN；
- 当前已有 positive case 不允许未解释回归；
- temporal、multi-session、knowledge-update、abstention 子集全部达到 fixture 的显式预期；
- 缺 case、缺 split、malformed、旧 artifact、代码 SHA 不匹配全部 fail closed。

---

## Task 6：把 recall quality 变成 required gate

**Files**

- Modify: `.github/workflows/governance-gates.yml`
- Modify: `tests/unit/test_ci_governance_contract.py`
- Modify: `CHANGELOG.md`

新增稳定 job id：`recall-quality`。运行 corpus/schema、fresh system replay、41-case 安全
夹具和 committed report 一致性检查。先在 PR 成功一次，再把 `recall-quality` 加入
`main` branch protection；不得用 `continue-on-error`。

---

## Task 7：两轮 fresh brain 性能与阶段二退出

运行两轮独立 30-run hook gate，记录：p50、p95、max、error、timeout、fallback、协议
污染和 fresh brain provenance。任何一轮超预算都不取平均掩盖。

最终命令：

```bash
.venv/bin/python -m ruff check .
.venv/bin/python scripts/check_mypy_baseline.py
MEMORY_HUB_TEST_EMBEDDING=1 .venv/bin/python -m pytest tests/unit -q
MEMORY_HUB_TEST_EMBEDDING=1 .venv/bin/python -m pytest tests/system -q
MEMORY_HUB_TEST_EMBEDDING=1 .venv/bin/python -m pytest tests/conformance -q
./agent_runtime_kit/hooks/test-hook.sh
./scripts/check-recall-quality.py
```

阶段二仅在以下全部满足时退出：三分数据和六层指标报告可复现；fresh replay 无未解释
回归；temporal/multi-session/abstention 达标；安全夹具全绿；两轮 fresh brain 性能达标；
shadow 不扩权；`recall-quality` required check 与 GitHub protection readback 生效。
