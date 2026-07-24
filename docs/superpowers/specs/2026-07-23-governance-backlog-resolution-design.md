# 治理积压闭环与归档版设计

日期：2026-07-23（Asia/Shanghai）

状态：方案 A 已确认，规格待复核

## 1. 背景与现场证据

索引运行真相治理已发布，真实 brain 当前索引为 `2072/2072 clean`，missing、orphan、
dirty marker 与 supersession graph drift 均为 0。剩余 readiness 失败集中在两类真实治理
积压，而不是索引损坏：

1. lifecycle review 共 318 条：
   - 238 条是 `session-active + auto-captured` 的机械会话信号；
   - 80 条是有业务语义的 signal/handoff；
   - 全部为 `ephemeral`、`raw`、`L0`，当前仍占用主动召回和 review queue。
2. pending 共 28 条：
   - 21 条 `audit_blocked`；
   - 4 条 `duplicate_candidate`；
   - 3 条 legacy `feedback`，因类型不受支持而标记 `unsupported_type`。
3. pending record lock 共 17 个，全部为已证明 orphan 的安全零字节锁，unsafe 为 0。
4. lifecycle review queue 的 supersession candidates 全为空。根因不是没有候选，而是
   `build_lifecycle_review_plan()` 调用 `build_maintenance_plan()` 时没有传入当前
   `items_by_id`。把同一批 items 交给既有 ranker 后，80 条业务记录中有 62 条可找到至少
   一个候选；多数最高分只有 `0.25`，只能作为人工阅读线索，不能据此自动 supersede。

238 条机械信号的证据覆盖不完整：

- 151 条来自旧 hook 格式，没有可追溯 transcript pointer；
- 63 条指向的 transcript 已不存在；
- 24 条 transcript 仍存在。

因此本轮采用用户确认的方案 A：**归档，不删除**。归档文件保留原始字节和恢复可能性，但不再
进入主动召回与生命周期 review。

## 2. 目标

本轮完成后：

1. lifecycle review queue 能显示既有 `SupersessionCandidateRanker` 计算出的真实候选；
2. 238 条机械 session signal 全部显式归档，不物理删除；
3. 80 条业务 signal/handoff 逐条形成显式动作清单，只允许
   `archive`、`supersede`、`keep-active` 三种结论；
4. 弱候选分数不能触发自动 supersede；未解除的高风险 signal 必须保持 active；
5. 21 条非 secret 的 audit blocker 能经逐记录、可审计的人工批准写入；
6. 4 条已确认的同 scope 元数据重复记录能显式确认并从 pending queue 移除；
7. 3 条 legacy `feedback` 能显式转换为 `decision`，通过统一 `WriteService` 写入；
8. orphan pending locks 能独立 preview，并在持有全局 queue lock 时安全回收；
9. 所有变更默认 preview，只有 `--apply` 才能修改状态；
10. 每个 pending 治理批次都有 prepared/completed 两阶段低敏 receipt，且 apply 时重新验证
    record identity、payload hash、分类理由和目标事实；
11. 代码先在临时 brain 完成故障与并发验证，发布到 GitHub 并通过 required checks 后，才允许
    操作真实 brain；
12. 真实运行完成后 readiness 中本轮目标项归零或只剩显式 `keep-active` 记录。

## 3. 非目标

- 不删除任何 lifecycle item；
- 不用 `memory gc` 的 tag-OR 规则处理本批次；
- 不按候选分数批量自动 supersede；
- 不放宽审计器的全局规则；
- 不允许绕过 secret、API key、password、token 或 private-key 发现；
- 不直接编辑 pending JSON 或 items Markdown；
- 不以 SQLite index 作为 pending 决策的权威事实源；
- 不新增 Web、MCP、Gateway 或云端治理入口；
- 不把 title、summary、正文、绝对路径或原始 ID 写入公开 batch receipt；
- 不顺带处理 low-confidence、npm packaging 等未纳入本轮的 readiness 提示。

## 4. 方案比较与决策

### 方案 A：保留型归档 + 显式 pending resolution（采用）

复用现有 lifecycle archive、`PendingQueue`、`WriteService`、record locks、
orphan-lock collector 和两阶段 receipt；只新增缺失的显式 resolution 动作与候选 wiring。

优点：

- 不丢失旧会话证据；
- 不削弱审计与并发边界；
- 能把当前积压闭环成可重复的产品能力；
- 改动集中在既有 CLI 和领域服务。

代价：

- archived 目录仍占磁盘；
- 80 条业务记录需要一次人工复核；
- pending apply 需要为三种 resolution 建立闭合集合和冲突校验。

### 方案 B：物理删除机械信号

队列会快速变小，但 214 条没有现存 transcript 的记录一旦删除无法恢复，不采用。

### 方案 C：自动 supersede / 自动猜类型

多数候选只有弱 topic overlap，legacy feedback 的正确类型也不是纯结构判断。自动化会把不确定性
伪装成确定事实，不采用。

## 5. 总体架构

```text
Lifecycle
  ItemsStore trusted scan
    -> AutoGovernanceCycle
    -> build_maintenance_plan(items_by_id, supersedes_edges)
    -> explicit review manifest
    -> govern apply-lifecycle (preview)
    -> govern apply-lifecycle --apply
    -> archived bytes / supersession ledger / keep-active ledger

Pending
  PendingQueue trusted preview
    -> explicit resolution manifest
       - approve-audit
       - accept-duplicate
       - convert-type
       - gc-orphan-locks
    -> queue lock -> catalog lock
    -> prepared receipt
    -> per-record identity/hash/fresh-classification validation
    -> WriteService or exact queue removal
    -> orphan-lock collector
    -> completed receipt
```

CLI 只负责解析、互斥校验和序列化。分类、锁、record revalidation、receipt 与写入必须留在
`PendingQueue` / governance 层，不能在 CLI 中复制文件操作。

## 6. Lifecycle 治理设计

### 6.1 修复候选 wiring

`build_lifecycle_review_plan()` 在同一次可信遍历中物化：

- `items_by_id: Mapping[str, MemoryItem]`；
- frontmatter 中已有的 supersession edges。

随后把二者传给 `build_maintenance_plan()`。继续复用现有
`SupersessionCandidateRanker`，不引入新的打分算法，不读取 SQLite index。

要求：

- 每个 review row 的 `candidates` 与直接调用现有 ranker 的结果一致；
- candidate 只用于阅读辅助；
- 既有无候选场景继续返回空数组；
- bounded output 和排序合同不变。

### 6.2 明确动作清单

真实 brain 治理时生成一份 operator-reviewed、带版本与输入 digest 的本地 manifest，
包含 item ID 与唯一动作。该文件保存到 `~/.agent-memory-hub/runtime/governance-manifests/`，
权限必须为 mode `0600`，不得写入仓库、公开文档、公开 receipt 或日志：

- `archive`：信息已过时、机械、或已被当前项目事实替代，但仍应保存原文；
- `supersede:<replacement_id>`：只有存在明确、人工核验的替代 item 时使用；
- `keep-active`：仍未解除、仍应召回的风险或待办。

同一 item 出现多个不同动作时，整批 fail closed。manifest 不包含正文；代码仓库只保存
synthetic fixture 与合同测试，执行前后仅保存低敏计数和 digest。发布前的能力分支不代表
Task 7 真实 brain 闭环已经完成。

### 6.3 归档规则

238 条机械 signal 全部归档：

- 使用既有 `archive_reviewed_item()`；
- secure copy 原始字节到 `items/archived/` 后再 unlink active source；
- 索引删除失败时标记 repair required，不伪装成功；
- 不使用 `gc`，不删除 archived 文件。

80 条业务 signal/handoff 逐条复核：

- 候选最高分低或仅命中 `TOPIC_OVERLAP` / `NEWER_ITEM` 时，不得自动 supersede；
- 明确 unresolved 的安全、凭据、生产风险 signal 必须 `keep-active`；
- 只有 replacement 的事实范围覆盖 source 且状态更新可验证时才 supersede；
- 其余已过时的过程态记录归档。

### 6.4 生命周期结果

preview 必须输出：

- requested/action counts；
- conflict/invalid/not-in-review-queue counts；
- archive/supersede/keep-active counts；
- generation token 或等价输入 digest；
- 不执行 mutation 的明确标记。

apply 前重新生成 review queue。manifest item 若已变化、不再处于 queue、replacement 消失或输入
digest 改变，则对应记录失败；批次不能把旧审批套到新事实上。

## 7. Pending resolution 合同

### 7.1 闭合动作模型

新增内部 `PendingResolutionAction`，动作集合固定为：

```text
approve_audit(record_id)
accept_duplicate(record_id, existing_item_id)
convert_type(record_id, target_type)
```

CLI 复用 `memory sync-pending`，新增 repeatable 参数：

```bash
memory sync-pending --approve-audit <record-id>
memory sync-pending --accept-duplicate <record-id>:<existing-item-id>
memory sync-pending --convert-type <record-id>:decision
memory sync-pending --gc-orphan-locks
```

规则：

- 默认只 preview；
- mutation 必须加 `--apply`；
- resolution 参数与 `--record`、`--safe-only` 互斥；
- 同一 record 的相同动作幂等去重，不同动作整批冲突；
- 空 selection 不创建 receipt；
- parser 不接受缺字段、额外分隔、未知 action 或未知 target type。

不新增独立顶级 CLI，以免形成第二套 pending 治理入口。

### 7.2 `approve_audit`

这是逐记录例外审批，不是全局 `allow_unsafe` 开关。

preview 条件：

- 当前 classification 必须为 `audit_blocked`；
- reason 必须为 `AUDIT_BLOCKED`；
- sensitivity 只能是 `internal` 或 `public`；
- 重跑审计后不得出现 secrets 类发现；
- 输出审计 rule/category/severity 的封闭计数，不输出正文。

apply 时在 queue/catalog/record lock 内再次读取并重跑审计。仅当 record identity、payload hash、
分类和审计发现集与审批 snapshot 一致时，才通过 `WriteService.write(..., allow_unsafe=True)` 写入。
任何 secret finding、scope 变化或新 finding 都返回稳定失败 reason，记录继续留在 pending。

本轮 21 条记录必须逐个显式列入 manifest；不能用“全部 audit blocker”通配。

### 7.3 `accept_duplicate`

该动作表示“现有 item 已承载相同知识，不再重复写入”。

preview 与 apply 都必须证明：

- pending 仍为 `duplicate_candidate`；
- reason 仍为 `SAME_SCOPE_METADATA_DUPLICATE`；
- 用户提供的 existing item ID 仍存在；
- type、title、summary 与 scope 仍满足当前 exact duplicate 规则；
- record identity 与 payload hash 未变化。

满足后，在 per-record lock 内删除**精确的 pending 文件**并 fsync pending 目录；不改现有 item，
不新增 item。任何证明失败均保留 pending。

真实 pending 与 existing item 映射仅保存在 mode-0600 的本地 manifest，
不得写入仓库、公开 receipt、日志或文档。

### 7.4 `convert_type`

仅处理 schema 已合法、但 type 不在支持集合的 legacy record：

- source type 必须仍为 `unsupported_type`；
- target type 必须来自现有 `MemoryItem` 支持集合；
- 本轮 manifest 只允许 `feedback -> decision`；
- 转换后的正文必须通过 `decision` 的三段硬约束；
- 原 pending 文件不原地改写；
- 在内存中构造新 item，经完整 schema/audit 后交给 `WriteService`；
- 写入成功并通过 source-ledger 边界后，才删除原 pending；
- 写入失败、index degraded 或 unlink 失败沿用现有可恢复语义。

三条 legacy feedback 的 durable intent 为：

1. 通用代码质量标准；
2. UI 改生产前必须先给预览；
3. UI 主题迁移必须完整并完成视觉验收。

转换正文使用固定、人工复核的 decision 模板，不允许 LLM 在 apply 时即时改写。

## 8. Orphan lock 独立回收

`--gc-orphan-locks` 复用 `collect_pending_record_locks()`，不复制 unlink 逻辑。

调用顺序：

```text
global pending queue lock
  -> bounded pending filename scan
  -> derive live lock names
  -> collect_pending_record_locks(apply=false|true)
```

preview 只报告 `total/orphan/preserved/unsafe/truncated/reason`。apply 仍需：

- secure no-follow directory/file open；
- 文件名、类型、owner/mode、size 校验；
- non-blocking exclusive flock；
- unlink 前 identity 二次校验；
- directory fsync。

任一 unsafe/truncated/platform unsupported 条件都 fail closed。GC 可和 resolution 同批执行，但其
失败只形成 batch warning，不回滚已成功的 item 写入。

## 9. Receipt、隐私与崩溃语义

扩展现有 `PendingBatchReceipt`，使 resolution 批次能表达：

- `selection_mode=resolution`；
- action counts；
- 规范化 selection digest；
- outcome status/classification/reason counts；
- orphan-lock GC summary；
- prepared/completed/incomplete 状态。

公开 receipt 继续禁止：

- record ID / item ID；
- title / summary / body；
- path；
- 原始审计文本。

规范化 digest 输入包含 action、record identity hash、payload hash，以及 duplicate target 或
conversion target 的摘要。ledger 仍为 append-only、0600、append + fsync。

执行顺序：

```text
queue lock
  -> catalog lock
  -> fresh trusted preview
  -> validate full action set
  -> append prepared receipt
  -> per-record mutation
  -> optional orphan-lock GC
  -> append completed receipt
```

- prepared append 失败：零 mutation；
- 中途崩溃：保留 prepared，逐记录事实由 item/source ledger 和 pending 是否存在共同证明；
- completed append 失败：返回 incomplete 和治理失败 reason，不谎报完整成功；
- 某条记录失败不让其他已验证记录绕过自己的检查；
- CLI exit code 非零表示至少一条未按 manifest 完成或 receipt 不完整。

## 10. 稳定失败原因

新增或复用以下封闭 reason：

- `CONFLICTING_PENDING_RESOLUTIONS`
- `PENDING_RESOLUTION_NOT_APPLICABLE`
- `PENDING_RESOLUTION_CHANGED`
- `PENDING_AUDIT_APPROVAL_REQUIRED`
- `PENDING_AUDIT_SECRET_BLOCKED`
- `PENDING_AUDIT_FINDINGS_CHANGED`
- `PENDING_DUPLICATE_TARGET_MISMATCH`
- `PENDING_CONVERSION_UNSUPPORTED`
- `PENDING_CONVERSION_INVALID`
- `PENDING_LOCK_GC_TRUNCATED`
- `PENDING_LOCK_GC_UNSAFE_ENTRY`
- `PENDING_RECEIPT_PREPARE_FAILED`
- `PENDING_RECEIPT_COMPLETION_FAILED`

未知异常不能原样进入 summary/receipt；内部日志可保留调试上下文，但不得包含 memory body。

## 11. 测试策略

实现遵循 TDD，至少覆盖：

### Lifecycle

- 候选 wiring 回归：当前 item map 传入后 candidates 非空且与 ranker 一致；
- 无候选、supersedes edge、bounded ordering；
- 机械 signal manifest 全量归档 preview/apply；
- archived 原始字节保持一致，active/index 均不再包含该 item；
- weak candidate 不自动 supersede；
- unresolved high-risk signal 保持 active；
- manifest 冲突、queue drift、replacement drift fail closed。

### Pending resolution

- 三类动作的 parser、互斥、幂等和批次冲突；
- audit approval 只允许显式 record；
- secrets finding 永不允许 bypass；
- audit finding 或 payload 并发变化被拒绝；
- exact duplicate target 成功移除 pending，错误 target 保留；
- feedback 转 decision 经过完整 schema/body/audit；
- WriteService、source ledger、index degraded 与 unlink 失败恢复语义；
- `safe_only` 不再让 unsupported 记录从结果中静默消失；
- receipt prepared/completed/incomplete、隐私字段与 digest 稳定性。

### Lock GC

- standalone preview 零 mutation；
- queue lock 内删除已证明 orphan；
- live、locked、unsafe、symlink、identity replacement 均保留；
- bounded/truncated/platform fallback fail closed。

### 端到端

- 临时 brain 复刻 `21 + 4 + 3` 队列并完整 preview/apply；
- 临时 brain 复刻 `238 + 80` lifecycle manifest；
- apply 后重新启动进程，readiness 与 receipt ledger 一致；
- CLI text/json/summary-only 合同；
- 既有 pending apply、lifecycle apply、doctor、public surface tests 全部通过。

## 12. 发布与真实 brain 操作顺序

严格按以下顺序：

1. 在隔离 worktree 完成 TDD；
2. 跑 targeted tests、完整 unit、conformance 与 docs truth；
3. 在临时 brain 做 dry-run、apply、崩溃/并发故障演练；
4. self-review diff，确认没有 secrets、真实 brain 路径或正文进入 fixture/receipt；
5. 直接提交并 push GitHub `main`；
6. 等全部 required checks 绿色；
7. 对真实 brain 重新执行只读 `readiness`、lifecycle plan、pending preview；
8. 保存输入 digest 和三份显式 manifest；
9. 先 lifecycle/pending/lock-GC dry-run，核对计数；
10. 执行真实 apply；
11. 重跑 `memory index verify --format json` 与 `memory govern readiness --format json`；
12. 用新进程再次检查，避免只看到进程内缓存；
13. 记录低敏治理回执和仍保留的 `keep-active` 列表。

真实 apply 前若数量、record hash、候选目标或 audit finding 与本规格证据不一致，立即停在 preview，
重新评审，不沿用旧批准。

## 13. 完成标准

代码能力完成：

- lifecycle candidates 不再因 wiring 缺失而恒为空；
- pending 三类显式 resolution 与 standalone lock GC 可 preview/apply；
- 所有 mutation 有 identity/hash/fresh-classification 校验；
- secret finding 无法被 audit approval 绕过；
- receipt 保持低敏、两阶段、崩溃可识别；
- 全量测试和 GitHub required checks 绿色。

真实治理完成：

- 238 条机械信号全部进入 archive，零物理删除；
- 80 条业务记录全部有显式动作，未解除高风险项保持 active；
- 21 条 approved non-secret audit blocker 被写入；
- 4 条 exact duplicate pending 被确认移除；
- 3 条 feedback 被转换为 decision 并写入；
- 17 个当前 orphan locks 被安全回收，unsafe 仍为 0；
- pending depth 为 0；
- index 保持 clean；
- readiness 不再因本轮 lifecycle/pending/lock 项失败；显式 keep-active 项以可解释状态保留。

## 14. Ponytail 约束

本轮只增加真实积压所需的最小能力：

- 一个候选参数 wiring 修复；
- 一个闭合的 pending resolution action；
- `sync-pending` 上四组增量参数；
- 一个 queue-lock 包裹的 standalone GC 入口；
- 对现有 receipt 的最小兼容扩展。

不新增数据库、后台服务、远程治理协议、第二套 archive、第二套审计器或第二套 pending queue。
