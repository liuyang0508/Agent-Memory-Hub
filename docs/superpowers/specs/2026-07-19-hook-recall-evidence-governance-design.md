# Hook 召回事实链与持续门禁设计

日期：2026-07-19
状态：已批准
范围：`UserPromptSubmit` Hook 的真实召回稳定性、证据一致性与发布门禁

## 1. 背景与实时证据

当前 `main@b8849f8` 已具备双路召回、Injection Gateway、三分 recall corpus、六层质量报告和
`recall-quality` required job。实时核验结果为：

- `tests/system/test_dual_route_recall_matrix.py`：23 passed；
- `scripts/check-recall-quality.py`：37 cases PASS；
- 将 production replay 语料真正送入 `agent_runtime_kit/hooks/inject-context.sh` 后，11 个
  Hook 可表达 case 符合预期；`prod-project-mismatch` 出现跨项目注入。

最后一项不是简单的召回算法回归，而是评测 surface 错配：现有 system replay 直接构造
`ProjectScope(source="explicit", hard_filter=True)`，真实 `UserPromptSubmit` payload 只有
`prompt/session_id/cwd/event`，并没有显式 project 字段。因此内部函数测试可以 PASS，
真实 Hook 却不具备同一输入合同。

下一阶段治理不再增加新的召回算法，而是建立唯一事实链，保证：真实 Hook 行为、运行证据、
质量报告和 CI 门禁对同一输入合同给出同一结论。

## 2. 目标与非目标

### 2.1 目标

1. 使用真实 `inject-context.sh` 进程回放去敏 production cases，而不是只调用内部 Python
   函数。
2. 每轮回放生成唯一、完整、低敏、可验证的 Run Manifest。
3. 将 corpus、实现、Hook、配置、Git commit 和逐 case 结果绑定到同一证据。
4. 缺失、重复、旧产物、协议污染、超时、异常、预期不一致或 surface 冒充均 fail closed。
5. 保留 retrieval、admission、answerability、temporal、abstention、injection 六层报告，
   但禁止用内部层 PASS 替代真实 Hook PASS。
6. 在 GitHub `recall-quality` required job 中 fresh 生成并校验真实 Hook 证据。

### 2.2 非目标

- 不用 cwd 猜测结果建立新的硬 project filter；该行为可能造成合法跨项目召回假阴性。
- 不升级 embedding 或 reranker 模型。
- 不把私有原始 prompt、session、cwd、memory body 或 token 写入报告。
- 不建设新的 Web 可观测平台；本阶段只产出机器可读 artifact 和简明 readiness 摘要。
- 不承诺 Hook 无法表达的 CLI/MCP `--project` 合同已经过 Hook 验证。

## 3. 事实源与不变量

### 3.1 四类事实源

1. **Corpus**：定义输入、surface applicability 和预期。
2. **真实进程结果**：`inject-context.sh` 的退出码、严格 JSON envelope 和最终注入 item IDs。
3. **低敏 runtime 记录**：同一 session 的 injection cohort、gap 和 hook latency/outcome。
4. **Run Manifest**：绑定本轮输入、实现和结果；Markdown/JSON 报告只能从它派生。

### 3.2 核心不变量

- 报告不是事实源，不能手工把状态改成 PASS。
- 一个 case 必须明确属于哪个 surface；未声明不得进入该 surface 的分母。
- `not_applicable` 必须带稳定 reason，不能记为 pass、fail 或 skipped-success。
- Hook 正例必须同时满足：协议合法、进程成功、预期 IDs 全部进入最终 context、禁止 IDs
  为零、对应 injection cohort 与 stdout 一致。
- Hook 负例必须同时满足：协议合法空输出、无 injection cohort、存在允许的 fail-closed
  gap/reason，且无禁止内容泄露。
- 每次 required CI 都 fresh 运行，不接受 committed 历史 manifest 代替。

## 4. Corpus surface 合同

production replay corpus 升级 schema，为每个 case 增加 `hook_expectation`：

```json
{
  "applicable": true,
  "cwd": "/sanitized/agent-memory-hub",
  "expected_status": "injected",
  "expected_item_ids": ["mem-..."],
  "prohibited_item_ids": []
}
```

不适用于 Hook 的 case 使用：

```json
{
  "applicable": false,
  "reason": "explicit_project_scope_unavailable"
}
```

约束如下：

- `applicable=true` 时必须提供去敏 cwd、期望状态和 ID 集；
- `applicable=false` 时只能提供受控 reason，不能携带伪造的 Hook 结果；
- query/source digest 仍保持只追加；修改历史 case 必须提升 corpus version 并输出 diff；
- 至少覆盖：长中文、Hook 确认、中英混合、日志/命令、弱跟进、时序、拒答、多模态；
- 显式 project hard-filter case 继续由 routed CLI/MCP system test 验证，但不计入 Hook 分母。

## 5. Run Manifest 合同

新增 `HookRecallRunManifest`，包含：

- `schema_version`、随机 `run_id`、`started_at`、`completed_at`；
- `status`：仅允许 `pass/fail/blocked`；
- `git_commit`、`dirty`、`hook_sha256`、`implementation_sha256`；
- `corpus_sha256`、`corpus_version`、`config_sha256`；
- adapter、Python/platform 标识和 timeout；
- planned/applicable/not-applicable/executed case counts；
- 每个 case 的期望/实际状态、预期/实际 ID、protocol validity、exit code、duration、
  cohort/gap 一致性和标准 reason；
- 汇总 gate failures。

Manifest 不保存 raw query、完整 stdout/stderr、memory body、真实 session、真实 cwd 或密钥。
`query_digest`、fixture item IDs 和去敏 cwd 可以保留。

Manifest 必须在所有 case 结束后原子写入。runner 异常时也要写 `blocked/fail` 的终态证据；
没有终态 manifest 的 CI 直接失败。

## 6. 真实 Hook 回放流程

每轮执行：

1. 校验 corpus schema、append-only 和 surface 合同。
2. 在隔离临时 brain 中写入公开去敏 MemoryItem，并用生产一致的 hashing embedder 建索引。
3. 为每个 Hook-applicable case 生成唯一低敏 session ID 和 payload。
4. 以独立子进程运行真实 `inject-context.sh`，设置受控 `BRAIN_DIR`、adapter、timeout 和
   test embedding 环境。
5. 严格解析 adapter envelope；从最终 context 提取 item IDs。
6. 按 session 读取 injection cohort 或 gap，验证 stdout、运行记录与预期一致。
7. 聚合 Run Manifest，执行 G0-G3 门禁并原子写入 artifact。
8. 独立 verifier 重新读取 manifest；runner 自己打印 PASS 不构成证据。

## 7. 四层门禁

### G0：证据完整性

- commit、Hook、实现、配置、corpus 哈希合法且匹配；
- case ID 唯一，planned/applicable/executed 数量闭合；
- 每个不适用 case 有允许的 reason；
- manifest 有终态、无缺行、无重复结果。

### G1：安全与协议

- 退出码异常、JSON/envelope 污染、timeout、stderr 敏感信息均失败；
- prohibited injection 必须为零；
- stdout IDs 与 injection cohort IDs 必须一致；
- 失败只能返回合法空协议，不得泄露候选正文。

### G2：召回语义

- 正例预期 IDs 必须全部进入最终 Hook context；
- 负例不得生成上下文；
- 真实 Hook 结果单独计数，不能由 routed-core 结果代替；
- 新 runtime gap 在人工去敏后追加到 production replay，不能覆盖旧失败样本。

### G3：运行稳定性

- fresh run 记录每 case duration、timeout 和 error；
- correctness gate 每次 CI 必跑一次全 corpus；
- 发布前另跑两轮 30-run 性能 gate，任一轮超预算即失败，不取平均掩盖尖峰；
- p50/p95/max/token cost 等软指标只做趋势，不替代 G0-G2。

## 8. CI 与 artifact

`governance-gates.yml` 的 `recall-quality` job 顺序调整为：

1. corpus/unit/system 六层验证；
2. fresh 真实 Hook runner 生成 `.artifacts/hook-recall-evidence.json`；
3. 独立 manifest verifier；
4. committed 六层报告一致性检查；
5. `if: always()` 上传低敏 manifest，失败时仍可诊断。

job 不使用 `continue-on-error`。artifact 上传失败不能把质量失败改绿；manifest 不完整时 verifier
和 job 都必须失败。

## 9. 错误处理与隐私

- 子进程超时后终止整个进程组并记录 `hook_timeout`。
- JSON malformed、envelope 字段缺失、未知 reason、重复 cohort、cohort/gap 同时出现均失败。
- stderr 只检查是否为空和是否命中敏感形状，不原样写入 manifest。
- 临时 brain 由 runner 独占，执行结束清理；不得读取或修改用户真实 brain。
- repo dirty 时本地可生成 `blocked` 证据，但 required CI 必须是 clean commit。
- 公开 fixture 只允许 public/internal 去敏内容，经过 public-hygiene 检查。

## 10. 测试策略

### 单元测试

- corpus schema v2、surface applicable/not-applicable 合同；
- manifest 缺 case、重复 case、旧哈希、未知状态、空结果、错误计数均 fail closed；
- stdout/cohort 不一致、正例漏 ID、负例有 context、prohibited ID、timeout 和 malformed JSON；
- manifest 序列化不包含 raw prompt/session/cwd/body/token。

### 系统测试

- 使用真实 Hook 跑完整 production replay；
- 证明当前内部函数 PASS、真实 Hook 不可表达的 explicit-project case 被准确标记
  `not_applicable`，不再冒充 PASS；
- 注入一个故意破坏的 Hook wrapper，证明 verifier 拒绝空/旧/部分 manifest；
- 运行现有 41-case safety、23-case matrix 和 Hook shell tests，防止兼容回归。

### CI 合同测试

- `recall-quality` job 必须包含真实 Hook runner、独立 verifier 和 artifact upload；
- 禁止 `continue-on-error`、禁止 CI 使用 `--write` 更新基线；
- required job 名保持 `recall-quality`，避免分支保护漂移。

## 11. 发布与迁移

1. 先迁移 corpus schema 和测试，不改变运行时召回算法。
2. 加入 runner/manifest/verifier，在本地复现全绿。
3. 将真实 Hook gate 加入现有 required job，不新增分支保护名称。
4. 更新 readiness 报告，明确 routed-core 与 real-hook 两个分母。
5. 连续两次 fresh main CI 全绿后，将旧的“仅内部函数 replay”证据降级为历史证据。

回滚时可以移除 CI 新步骤，但不得删除 production replay case 或把失败 manifest 改成 PASS。

## 12. 完成定义

以下全部满足才算本治理完成：

1. production corpus 明确声明每个 case 的 Hook surface applicability；
2. 真实 `inject-context.sh` 全部 applicable cases 符合预期；
3. 不适用 case 有稳定 reason，未计入 Hook PASS 分母；
4. fresh Run Manifest 绑定当前 commit/Hook/实现/corpus/config；
5. 独立 verifier 对空、旧、部分、重复、协议污染和不一致证据 fail closed；
6. `recall-quality` required job 执行并上传真实 Hook manifest；
7. 定向、system、hook、conformance 和全量门禁通过；
8. readiness 文档只引用当前证据，不再把 routed-core PASS 写成 real-hook PASS。
