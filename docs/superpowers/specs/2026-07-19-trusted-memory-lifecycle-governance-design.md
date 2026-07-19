# 可信记忆生命周期治理设计

日期：2026-07-19（Asia/Shanghai）

状态：待用户复核书面规格

## 1. 背景与实时证据

Hook、Gateway、召回质量门禁和 Qoder/QoderWork 配置收敛已经完成，但真实 brain 的
生命周期状态仍不健康。2026-07-19 的只读核验得到：

- 共有 1975 条 MemoryItem，其中 495 条为 signal；
- 318 条 signal/handoff 已达到 stale review 条件；
- 全库 `superseded_count=0`，但 SQLite `refs_graph` 已存在 1 条 `supersedes` 边；
- `memory govern plan` 产生 516 条 review-required action；
- pending queue 有 45 条 Hook 写入，0 malformed、0 dead，时间跨度为
  2026-05-31 至 2026-07-10；
- 45 条 pending 中包含 20 artifact、9 episode、5 fact、3 decision、3 signal、
  2 handoff 和 3 feedback；
- `memory doctor --offline` 因 pending backlog 显示 `DEGRADED`；
- Stage 1 readiness 已为 PASS，但对应实施计划仍保留 55 个未勾选步骤。

源码核验确认两个直接缺陷：

1. `link_memories(new, old, relation="supersedes")` 只写 SQLite 图边和
   `new.refs.mems`，没有把 `old.superseded_by` 写回 Markdown；默认召回过滤和
   ContextFirewall 因此看不到这次 supersession。
2. `PendingQueue.replay()` 使用回放时刻创建 MemoryItem，并重新生成随机 ID；旧 signal
   会被改写成“今天创建”，进程在 Markdown 写成功、queue unlink 前崩溃时还可能重复写。

现有 lifecycle review 只能 archive，不能完成 supersede；doctor 给出的
`memory sync-pending` 建议默认会直接执行回放，不符合治理操作 preview-first 的安全边界。

## 2. 目标

本轮完成后：

1. `supersedes` 在 Markdown、图边、索引和召回过滤中表达同一事实；
2. supersede、archive、restore 都通过共享事务服务，不由 CLI、MCP、Web 各写一套；
3. stale 只是 review 信号，不自动等价于 archive 或 superseded；
4. supersession 候选由可解释、确定性的证据生成，只建议、不自动应用；
5. pending replay 保留原始时间，并具备崩溃后幂等性；
6. 过期 signal/handoff 不会被无提示回放成新记忆；
7. `memory sync-pending` 默认只预览，任何写入都要求显式 `--apply`；
8. governance readiness 同时显示 stale、superseded、broken chain、pending age 和
   pending classification；
9. 历史计划、当前发布报告和本机运行健康的权威边界明确，不再互相冒充；
10. 真实 45 条 pending 与 318 条 stale backlog 在代码发布后进入有证据、可回滚的
    分批治理，而不是一次性批量删除或回放。

## 3. 非目标

- 本轮不解决 semantic provider 的 fast-ready；
- 不完成 Qoder/QoderWork 客户端消费证明；
- 不扩充 Stage 2 召回语料，也不改召回排序模型；
- 不引入 LLM 自动判断“哪条记忆是真相”；
- 不新增远端控制面或云端数据库；
- 不改变 Markdown 作为长期知识权威事实源的架构；
- 不自动 archive 318 条 stale item；
- 不在没有用户选择的情况下回放 45 条真实 pending write；
- 不用 GitHub Issue 替代本地治理事实源。

## 4. 方案比较与决策

### 方案 A：复用现有字段，增加统一生命周期事务（采用）

继续使用 `MemoryItem.superseded_by`、`refs.mems`、`items/archived/` 和
`pending/*.jsonl`。新增共享 lifecycle transaction，把候选、预览、应用、回滚和派生索引
同步起来。

优点：兼容现有 schema 和召回过滤，变更范围可审计，能直接修复当前 split-brain。
缺点：需要迁移现有图边和 pending v1，并严格处理部分成功。

### 方案 B：给 MemoryItem 新增 active/resolved/archived 状态字段

优点：状态看起来直观。缺点：与 `superseded_by`、归档目录和 validity state 形成第四套
状态源，需要全库迁移，也容易出现字段与物理位置冲突，因此不采用。

### 方案 C：使用 LLM 自动清理 stale memory

优点：短期可快速压低 backlog。缺点：不可重复、难以解释、可能跨项目误删或把旧证据
误判为无效，不符合本地优先和可审计原则，因此不采用。

## 5. 生命周期事实模型

不新增 MemoryItem 状态字段。生命周期状态由现有事实确定性派生：

| 状态 | 权威事实 | 默认召回行为 |
|---|---|---|
| active | 文件位于 `items/` 且 `superseded_by` 为空 | 可继续进入后续治理与 Gateway |
| stale-review | active 且 `validity.observed_at`（缺失时用 `created_at`）超过对应 type 的 review 窗口 | 默认降低运行状态可信度；是否注入仍服从现有 temporal/Gateway 合同 |
| superseded | `superseded_by=<existing item id>` | 默认禁止注入；审计可显式读取 |
| broken-superseded | `superseded_by` 指向不存在或已归档目标 | fail closed，进入 blocker，不静默降级为 active |
| archived | 文件位于 `items/archived/` | 默认不扫描、不索引、不召回 |
| contested | 现有 contested/contradiction 证据 | 不自动选赢家，继续进入人工复核 |

`stale` 是“需要复核”的观察，不是删除结论。没有 replacement 的旧 signal 可以在人工确认后
archive；存在 replacement 时优先 supersede，保留可追溯链。

## 6. 统一 supersession 事务

### 6.1 输入与方向

统一使用：

```text
replacement_id supersedes obsolete_id
```

事务同时保证：

- `obsolete.superseded_by = replacement.id`；
- `replacement.refs.mems` 包含 obsolete id；
- `refs_graph` 存在 `replacement -> obsolete / supersedes`；
- reindex 从 obsolete 的 `superseded_by` 反向重建 supersedes 图边，不能把它降成普通 refs；
- 索引中的 obsolete 状态被更新或写入 dirty-index repair 标记；
- governance ledger 记录 action、时间、操作者、两个 item id、结果和失败 reason；
- ledger 不复制正文、summary、prompt 或秘密。

### 6.2 前置校验

应用前必须验证：

1. 两个 item 都存在于 active items 目录；
2. 不能自己 supersede 自己；
3. 不形成 supersession cycle；
4. obsolete 尚未指向另一个 replacement；重复同一事务视为幂等成功；
5. tenant 必须相同；
6. project 相同，或二者都为 global；跨项目关系默认阻断；
7. replacement 不能因为更严格的 sensitivity 让原本可用的知识静默消失；
8. replacement 不带 `needs-review`，且没有 malformed/broken validity；
9. archive 目标、缺失目标和 pending-only 目标不能作为 replacement。

前置校验失败只返回封闭 reason code，不修改任何文件。

### 6.3 写入顺序与部分失败

Markdown 是权威源，写入采用同目录临时文件、fsync、atomic replace：

```text
validate
  -> snapshot/rollback marker
  -> atomic update obsolete.superseded_by
  -> atomic update replacement.refs.mems
  -> best-effort index upsert + refs_graph
  -> append low-sensitive lifecycle ledger
```

Markdown 更新失败时事务失败并回滚；索引失败不撤销已经成功的 Markdown 事实，而是写入
`.index-dirty` 并返回 `passed_with_index_repair_required`。默认召回已有 Markdown
candidate guard，索引短暂漂移也不能把 obsolete item 注入。

事务 ledger 额外记录 `replacement_ref_preexisted`。显式 revert 只有在该 refs link 确由本次
事务新增时才移除它；原本就存在的 provenance link 必须保留，避免 relationless `refs.mems`
在回滚时丢失合法引用。

### 6.4 MCP/CLI/Web 一致性

新增共享 `SupersessionService`，所有入口复用：

- MCP `link_memories(..., relation="supersedes")` 路由到该服务；
- 普通 `refs/refines/contradicts/derives` 仍走图链接；
- lifecycle CLI 支持显式 obsolete/replacement 预览与应用；
- Web Admin 使用 action object，不再把所有 item id 都解释成 archive；
- generic unlink 不得静默撤销 supersession，撤销必须走显式、可审计的
  `revert-supersession` 事务。

## 7. 确定性 supersession 候选

候选生成只读取 metadata 和有界的 locator，不依赖 LLM。至少满足相同 tenant/project 和
兼容 type，再按以下证据评分：

- 新 item 的 `refs.mems` 已显式引用旧 item；
- 已存在 `supersedes` 图边；
- title/keyphrase/tag 高重合；
- replacement 创建时间更新；
- commit/file/resource evidence 有交集；
- signal 的 summary/locator 出现明确的“已关闭/已修复/取代”结构段；
- 旧 item 已 stale，新 item 当前有效。

输出 `candidate_id`、分数、命中的 evidence code 和拒绝原因。候选最多给出 3 条，正文不进入
治理报表。候选无论多高分都不能自动应用；只有显式选择 replacement 后才能执行事务。

## 8. Pending Queue v2

### 8.1 记录合同

新 pending record 增加向后兼容字段：

- `record_id`：入队时生成，稳定不变；
- `enqueued_at`：真实入队时间；
- `original_created_at`：原事件时间，缺失时回退 `enqueued_at`；
- `payload_sha256`：规范化 item payload 指纹；
- `attempt`、`last_error_code`、`last_attempt_at`；
- `origin` 与现有 item payload。

legacy v1 在预览时确定性派生 record id、时间和 hash，不立即改写原文件。

### 8.2 稳定 item id 与 exactly-once 边界

pending replay 根据 `original_created_at + title + record_id` 生成稳定 MemoryItem id：

- 首次写成功后删除 queue record；
- 若进程在 Markdown 写成功后、unlink 前崩溃，下一次看到相同 id 和相同 payload hash，
  返回 `already_written` 并安全删除 queue record；
- 相同 id 但 payload 不同则 fail closed，进入 conflict review；
- 不再用回放时刻和随机后缀制造重复 item。

### 8.3 时间与分类

回放保留 `original_created_at`，不把历史 signal 刷新为当前状态。每条 record 在预览时进入
一个封闭分类：

| 分类 | 含义 | 默认动作 |
|---|---|---|
| ready | 新鲜、合法、无重复 | 可被 `--safe-only --apply` 选择 |
| already_written | 相同稳定 id/hash 已存在 | 可清理 queue record |
| stale_requires_review | 过期 signal/handoff 或有效期已过 | 禁止 safe replay |
| duplicate_candidate | 与现有 item 高概率重复 | 人工选择 skip/link/replay |
| conflict | 稳定 id 相同但 payload 不同 | blocker |
| unsupported_type | legacy 类型不在当前 MemoryType 中，例如现有 pending `feedback` | 人工映射或 quarantine，不自动猜测 |
| malformed | JSON/schema 无法解析 | 进入 dead/quarantine 预览 |
| audit_blocked | WriteService 安全审计不通过 | blocker |

### 8.4 CLI 安全合同

`memory sync-pending` 改为默认 preview：

```bash
memory sync-pending --format json
memory sync-pending --record <record-id> --apply
memory sync-pending --safe-only --apply
```

保留 `--dry-run` 作为兼容别名，但不再需要它才能避免写入。没有 `--apply` 时任何命令都不
修改 queue、items 或 index。doctor 的 next action 也改成明确的 preview 命令。

## 9. Lifecycle Review 与真实 backlog

review queue 的 action 从“只有 archive”扩展为：

- `supersede`：必须携带 replacement id；
- `archive`：无 replacement 且用户确认已无长期价值；
- `keep-active`：确认仍有效并刷新 `validity.observed_at` 和 review evidence，而不是改写 created_at；
- `revert-supersession`：只用于错误关系，必须显式选择；
- `defer`：保留并给出下次 review 时间。

批量请求使用 action objects，不能把同一批 item 隐式解释成同一高风险动作。archive、
supersede、pending replay 都默认 preview，应用前创建 brain snapshot 或等价 rollback marker。

真实数据迁移分三步：

1. 只读生成 45 条 pending classification 和 318 条 lifecycle candidate 报告；
2. 先处理确定性 `already_written`、broken edge 和显式 supersedes graph edge；
3. 再按项目和类型分批请求用户确认，不一次性自动清空 backlog。

## 10. Readiness 与事实源边界

`memory govern readiness` 增加：

- active/stale/superseded/archived/broken-superseded 数量；
- pending total、oldest age、分类、dead/malformed 数量；
- supersession graph/frontmatter drift；
- review queue 数量与最老年龄；
- safe/review/blocker 三类 next action。

建议门槛：

- broken chain、pending conflict、malformed/dead：error；
- pending oldest 超过 24 小时：warn，超过 7 天：error；
- stale signal 比例和 review backlog：warn，不自动阻断 core read；
- `supersedes` 图边与 Markdown 不一致：error；
- 当前运行健康和 repo release readiness 分开展示。

历史 implementation plan 是执行说明，不再作为当前完成状态；plan 顶部必须链接权威
readiness report。committed readiness/report 由生成器和 CI 校验，本机 brain health 由
runtime readiness 生成，二者都不能伪装成另一方。

## 11. 错误处理、安全与隐私

- 高风险 mutation 只接受当前 review queue 中的 item 或显式 supersession pair；
- tenant 不一致立即拒绝；project 不一致默认拒绝；
- preview/report 不输出 body、raw prompt、transcript、token、secret 或第三方命令；
- private/secret item 只输出 id、分类和封闭 reason，标题按现有隐私策略处理；
- snapshot 失败则不执行 apply；
- 一条 action 失败不掩盖其他结果，但批次输出逐项状态，不给整体假 PASS；
- queue record 只有在 Markdown 已可验证存在后才允许删除；
- rollback 不删除 replacement item，只恢复关系或归档位置；
- 所有失败使用封闭 reason code，日志不包含正文。

## 12. 文件与组件边界

预计新增：

- `agent_brain/memory/governance/supersession.py`：统一事务、校验、回滚；
- `agent_brain/memory/governance/lifecycle_candidates.py`：确定性候选和 reason codes；
- `agent_brain/memory/governance/lifecycle_ledger.py`：低敏 action ledger；
- 独立 unit/system fixture 覆盖 graph/frontmatter、pending crash 和真实 backlog 形态。

预计修改：

- `lifecycle_review.py`、`maintenance_plan.py`：action object 与候选；
- `pending.py`、maintenance CLI：v2、稳定 ID、默认 preview；
- MCP graph tools 与 reindex：`supersedes` 路由到统一事务，并从 frontmatter 重建关系；
- Web governance routes：统一 preview/apply schema；
- governance readiness、offline doctor：生命周期与 pending truth；
- retrieval supersession guard：broken target fail closed 与 trace；
- docs truth contract、CHANGELOG 和迁移文档。

不把候选、事务、CLI/Web formatting 塞进同一文件；每个模块只负责一种状态转换。

## 13. 测试设计

### 13.1 Supersession

1. MCP `supersedes` 同时更新 old frontmatter、new refs 和 graph；
2. 重复同一事务幂等；
3. self、cycle、missing target、archived target、cross-tenant、cross-project 被拒绝；
4. 部分 Markdown 写失败完整回滚；
5. index 写失败留下 dirty marker，召回仍过滤 obsolete item；
6. broken `superseded_by` 不恢复为 active；
7. generic refs 不误写 superseded_by；
8. revert 只撤销精确匹配关系；
9. ledger 无正文和秘密；
10. Web、CLI、MCP 返回相同 reason code。

### 13.2 Pending

1. v1/v2 record 都能只读分类；
2. 默认 CLI 不写入；
3. 原始 created_at 被保留；
4. crash-after-write-before-unlink 不重复创建 item；
5. stale signal/handoff 不进入 safe replay；
6. already-written 相同 hash 可安全清 queue；
7. 同 id 不同 hash fail closed；
8. unsupported-type/malformed/audit-blocked/dead 分层准确；
9. 多 record 中一条失败不阻塞其他已选择 action；
10. dry-run 与 apply 的分类/hash 一致。

### 13.3 Governance 与召回

1. readiness 报告所有生命周期和 pending 指标；
2. graph/frontmatter drift 触发 required failure；
3. superseded 与 broken-superseded 都不能进入 Gateway；
4. audit 查询可以显式读取 superseded；
5. 真实风格 fixture 覆盖 45 pending 的 type/project 分布；
6. committed report 与当前代码 hash 不一致时 CI 失败；
7. full unit、system、conformance、hook、recall-quality、adapter-governance、ruff、mypy
   保持通过。

## 14. 发布、迁移与回滚

1. 先提交代码和 fixture，不修改真实 brain；
2. 完整门禁通过后直推 `main`；
3. 从稳定 main 生成真实 lifecycle/pending dry-run 报告；
4. 用户审核后按项目和 action 分批 apply；
5. 每批复核 Markdown、index、graph、Gateway 和 readiness；
6. 只有真实 pending 已 written/already-written/deferred/quarantined 分类闭合后，才能把
   pending lane 标成 pass；
7. 只有明确 supersede/archive/keep-active 后，才能降低 stale backlog，不允许改阈值假绿。

回滚使用 apply 前 snapshot 和 lifecycle ledger：supersession 恢复 old frontmatter、new refs 和
graph；archive 恢复文件并 reindex；pending 写入不删除已经形成的有效 MemoryItem，只恢复
未完成 queue record 或将重复标记为 already-written。

## 15. 完成定义

代码与发布层：

- supersedes 不再出现 graph/frontmatter split-brain；
- pending replay 保留原始时间、稳定 ID、崩溃后幂等；
- 所有高风险操作默认 preview、显式 apply、可回滚；
- CLI/MCP/Web/doctor/readiness 消费同一事务和 reason codes；
- required CI 与完整本地门禁通过，GitHub `main` 绿。

真实运行层：

- 45 条 pending 每条都有明确分类，不存在无提示批量回放；
- 现有 1 条 supersedes 图边完成显式核对或修复；
- 318 条 stale review backlog 按项目进入可执行队列，并至少完成首个高置信批次；
- broken chain、pending conflict、malformed/dead 均为 0，或以显式 blocker 记录；
- readiness 不再把历史计划、发布 PASS 和当前机器健康混成一个结论。

本轮完成不意味着全部 318 条都自动归档，而是让每次保留、取代和归档都可解释、可验证、
可回滚，并阻止旧事实在没有治理证据的情况下继续冒充当前真相。
