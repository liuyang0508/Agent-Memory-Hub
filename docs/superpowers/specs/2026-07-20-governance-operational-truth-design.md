# 治理运行真相与可审计批次设计

日期：2026-07-20（Asia/Shanghai）

状态：已批准，待实现

## 1. 背景与现场证据

可信生命周期治理已经发布，`lifecycle-governance` 也已加入 GitHub `main` 的 required
checks。真实 pending 第一批治理验证了 preview-first、显式 record selection、queue/catalog
双锁和逐记录 hash 校验能够工作：批准的 17 条 `ready` 全部写入，失败与降级均为 0。

这次真实运行同时暴露出四个运行真相缺口：

1. `memory govern readiness` 先扫描一次 items，随后 pending readiness 为判断
   `already_written` / duplicate 又扫描同一批 items。约 2000 条 item 时，pending 的独立
   1 秒预算会耗尽并返回 `pending_integrity=fail`，而紧接着运行完整
   `memory sync-pending --limit 100 --format json` 又能成功。这是重复扫描造成的假失败，不是
   queue 损坏。
2. 批次审批需要操作者手工从 preview 提取 `{record_id,payload_sha256}`、计算批次 hash、保存
   前后快照并聚合 apply 结果。底层已有逐记录校验，但缺少原生的低敏批次回执。
3. pending JSON 默认包含 title、summary、path、record id 等明细。治理者只想看 blocker
   reason 数量时，没有正式的 summary-only 输出，只能额外用 `jq` 做脱敏聚合。
4. 每个 record apply 会创建 `.amh-record-locks/<digest>.lock`。锁文件为零字节、0600，当前
   永久保留。直接 unlink 会引入 inode replacement / split-lock 风险，但无限保留也会累计
   inode；系统缺少明确、可验证的安全回收协议与指标。

当前真实队列在第一批完成后剩余 28 条：21 条 `audit_blocked`、4 条
`duplicate_candidate`、3 条 `unsupported_type`，已无 `ready`。本轮只建设治理能力，不处理
这些记录，也不处理 318 条 stale lifecycle review。

## 2. 目标

本轮完成后：

1. lifecycle readiness 与 pending classification 共享同一份可信 item metadata snapshot，
   不再为同一报告重复扫描全部 items；
2. 共享 snapshot 不可信、超预算或发生并发变化时继续 fail closed，不用增大 timeout 掩盖问题；
3. 约 2500 条 item、100 条 pending 的受控 fixture 在默认预算内完成，且 pending 分类完整；
4. preview 和 apply 都能输出 `summary-only` 低敏视图，只包含封闭的 counts/reason codes；
5. apply 返回版本化 batch receipt，包含审批批次摘要、前后 depth、结果计数和完整性 digest，
   不包含 title、summary、path、record id 或 item id；
6. batch receipt 采用 prepared/completed 两阶段的 append-only runtime ledger：先持久化准备记录，
   再执行逐记录事务，最后持久化完成记录；崩溃或最终 append 失败会留下可识别的 incomplete
   receipt，不伪装成完整成功；
7. record lock 仅在持有全局 pending queue lock 时回收；只有对应 pending record 已不存在、
   lock 文件安全且可获得非阻塞独占锁时才 unlink；
8. readiness 暴露 `pending_reason_counts`、`pending_lock_files`、
   `pending_orphan_lock_files` 与 receipt ledger 健康状态；
9. CLI 以及任何既有 Web/MCP pending 入口共享 `PendingQueue` 的同一套
   summary/receipt 语义，不复制分类逻辑；本轮不为不存在的 surface 新建远程 apply 接口；
10. 所有新公开输出有稳定 schema、大小边界、隐私测试和 fail-closed reason code。

## 3. 非目标

- 不自动应用剩余 pending；
- 不批量 archive stale signal/handoff；
- 不调整 recall 排序、Gateway 或 ContextFirewall；
- 不通过简单把 1 秒或 2 秒预算扩大数倍来“修复” readiness；
- 不把正文、title、summary、绝对路径或原始 ID 写入 batch receipt；
- 不尝试从不可验证的 legacy payload 猜测正确类型；
- 不删除仍可能被其他进程持有或等待的 lock inode；
- 不新增云端控制面、远程数据库或 LLM 自动治理。
- 不为当前不存在的 Web/MCP pending apply surface 新增远程变更入口。

## 4. 方案比较与决策

### 方案 A：共享可信快照 + 两阶段低敏回执 + queue-lock 内安全 GC（采用）

readiness 首次 items 扫描形成只读 metadata snapshot，并把它显式传给 pending classifier；apply
在现有 queue/catalog 锁内创建 prepared receipt、执行逐记录 apply、清理可证明 orphan 的 lock，
最后写 completed receipt。

优点：解决根因；保留 source-of-truth 与 fail-closed 边界；真实批次可审计；CLI/Web/MCP 可复用。
缺点：需要给 pending 模块新增内部 snapshot/receipt 合同，并严格验证 ledger 与 GC 的部分失败。

### 方案 B：只放宽 readiness deadline

实现简单，但 item 数继续增长后问题会复现；同时保留重复 I/O，不能解决回执、隐私聚合和 lock
累积，因此不采用。

### 方案 C：用 SQLite index 代替 Markdown metadata 扫描

速度快，但 index 是派生事实，当前真实环境还出现过 `.index-dirty` 与 supersession drift。用它
决定 pending duplicate/already-written 会把派生状态提升为权威源，违反现有架构，因此不采用。

## 5. 共享可信 item snapshot

### 5.1 合同

在 pending 模块定义只读输入合同 `PendingItemCatalogSnapshot`：

- `items: Mapping[str, MemoryItem]`；
- `trusted: bool`；
- `reason: str | None`；
- `entry_count` 与 `metadata_bytes`；
- 不包含 Markdown body。

`PendingQueue.preview()` 保持现有行为：调用方未提供 snapshot 时自行进行有界扫描。
`PendingQueue.preview_for_readiness()` 新增显式 snapshot 参数；readiness 必须传入刚刚完成的 items
扫描结果，不允许默默回退到第二次全量扫描。

### 5.2 信任边界

- item scan 完整且未超过 entry/byte/depth 边界时，snapshot 为 trusted；
- scan 中出现 malformed、unsafe inode、重复 ID、预算耗尽或并发变化时，snapshot 为 untrusted；
- untrusted snapshot 下，pending 返回 `scan_unavailable=true` 和稳定 reason，不输出看似完整的
  分类计数；
- pending path 与 record 内容仍按现有 no-follow、identity、mtime、size、sha256 规则独立校验；
- lifecycle generation token 的前后比较继续覆盖 items、pending、ledger 与 index components，
  共享 snapshot 不替代一致性检查。

### 5.3 性能边界

默认门禁 fixture：2500 个合法 item、100 个 pending、每条 frontmatter 小于 4 KiB。要求：

- `_read_items_readonly()` 只调用一次；
- pending classification 不调用 `_scan_existing_item_metadata()`；
- memory lifecycle lane 在 2 秒内部预算内完成；
- 报告 counts 与独立 `PendingQueue.preview()` 相同；
- 测试使用相对性能/调用次数约束，不以开发机绝对毫秒作为唯一正确性证据。

## 6. 隐私安全的 pending summary

### 6.1 统一结构

`PendingPreview` 增加纯聚合方法 `to_summary_dict()`：

```json
{
  "schema_version": 1,
  "total": 28,
  "returned": 28,
  "truncated": false,
  "scan_unavailable": false,
  "reason": null,
  "classification_counts": {"audit_blocked": 21},
  "reason_counts": {"AUDIT_...": 21},
  "groups": {"ready": 0, "review": 7, "blocker": 21},
  "oldest_age_seconds": 0
}
```

所有 classification/reason 必须来自封闭集合；未知 reason 归一化为
`UNKNOWN_PENDING_REASON`，不能把异常文本原样放进 summary。

### 6.2 CLI

`memory sync-pending --summary-only --format json`：

- preview 时只输出 summary；
- apply 时输出 aggregate stats + batch receipt，不输出 per-record results；
- `--summary-only` 与 text/json 都可用；JSON 是稳定机器合同；
- 不改变默认详细输出，避免破坏已有调用方。

readiness 直接复用同一个 summary builder，不能自己维护另一套 reason grouping。

## 7. 两阶段 batch receipt

### 7.1 低敏 schema

新增 `PendingBatchReceipt`，固定字段：

- `schema_version`；
- `batch_id`：随机不可预测 ID；
- `batch_digest`：按排序后的 record identity/hash 规范化计算；
- `selection_mode`：`explicit` / `safe_only`；
- `requested_count`、`selected_count`；
- `depth_before`、`depth_after`；
- `status_counts`、`classification_counts`、`reason_counts`；
- `index_repair_required_count`、`warning_counts`；
- `prepared_at`、`completed_at`；
- `state`：`prepared` / `completed` / `incomplete`；
- `result_digest`：排序后的低敏 per-record outcome 的整体摘要。

receipt 不包含原始 record id/item id。digest 输入使用 domain separator 与 canonical JSON；公开
receipt 只暴露最终 digest，不能据此还原正文。

### 7.2 ledger

文件：`runtime/pending-apply-receipts.jsonl`，0600，append + fsync，固定 schema 和大小边界。

执行顺序：

```text
queue lock
  -> catalog lock
  -> trusted preview / explicit selection
  -> append prepared receipt
  -> per-record apply（现有 identity/hash 再校验）
  -> bounded orphan lock GC
  -> append completed receipt
  -> return stats + receipt
```

- prepared append 失败：零 mutation，返回 `PENDING_RECEIPT_PREPARE_FAILED`；
- 中途崩溃：ledger 留下 prepared，无 completed；逐记录事实仍由 WriteService/source ledger 证明；
- completed append 失败：不谎报完整成功，返回结果并标记
  `PENDING_RECEIPT_COMPLETION_FAILED`，prepared 可供后续 reconcile；
- receipt ledger malformed/超预算：readiness fail closed，但不能阻断普通 pending enqueue；
- preview 不创建 receipt、runtime 目录或 lock 文件。

## 8. record lock 安全回收

### 8.1 为什么不能直接 unlink

进程 A/B 若已打开同一 lock inode，进程 C 在 A 释放后删除路径并创建新 inode，B 仍等待旧 inode，
C 却能锁住新 inode，形成 split lock。因此“context manager 退出就 unlink”禁止实现。

### 8.2 可回收条件

仅在持有全局 pending queue lock 时执行 bounded GC。对每个 `.lock`：

1. 名称、类型、owner/mode、size 满足固定合同；
2. 当前 pending 根目录中不存在 hash 对应的 record filename；
3. 以 no-follow 打开后 identity 与目录项一致；
4. `flock(LOCK_EX | LOCK_NB)` 成功；
5. unlink 前再次核对 identity；
6. unlink 后 fsync lock directory。

任一条件失败都保留文件并计入 `preserved` / `unsafe`，不能为了清数字降低锁安全。

### 8.3 边界

- 每次最多扫描/删除固定数量，超限返回 `PENDING_LOCK_GC_TRUNCATED`；
- fallback 平台没有可靠 dirfd/flock 时只报告，不删除；
- GC 失败不回滚已经成功的 MemoryItem 写入，但 receipt 必须记录 warning count；
- readiness 只报告 lock 总数、可证明 orphan 数、unsafe/truncated 状态，不输出 lock filename。

## 9. 数据流与接口一致性

```text
Items read-only scan
  -> PendingItemCatalogSnapshot
  -> lifecycle metrics
  -> PendingQueue.preview_for_readiness(snapshot)
  -> PendingSummary
  -> readiness / CLI summary / Web summary

Pending apply
  -> queue + catalog lock
  -> fresh trusted preview
  -> prepared receipt
  -> WriteService per-record transaction
  -> safe orphan-lock GC
  -> completed receipt
  -> detailed result or summary-only result
```

Web/MCP 不直接写 receipt 或做 reason grouping；它们只序列化共享对象。任何入口新增 apply 参数时，
仍必须保留显式 selection、auth/admin 和 preview-first 边界。

## 10. 失败语义

新增稳定 reason codes：

- `PENDING_ITEM_SNAPSHOT_UNTRUSTED`
- `PENDING_RECEIPT_PREPARE_FAILED`
- `PENDING_RECEIPT_COMPLETION_FAILED`
- `PENDING_RECEIPT_LEDGER_UNAVAILABLE`
- `PENDING_RECEIPT_LEDGER_CORRUPT`
- `PENDING_LOCK_GC_UNAVAILABLE`
- `PENDING_LOCK_GC_TRUNCATED`
- `PENDING_LOCK_GC_UNSAFE_ENTRY`

失败必须落在三类：

- pre-mutation blocker：零写入；
- per-record outcome：沿用现有 written/already-written/review-required/skipped/failed；
- post-mutation governance warning：事实已写入，但 receipt/GC 降级必须显式返回，不能吞掉。

## 11. 安全与隐私

- summary/receipt 禁止 title、summary、body、path、record id、item id、session、project；
- reason code 使用 allowlist；异常字符串不进入公开 JSON；
- ledger 使用 `SecureDirectory`、no-follow、0600、bounded line/file size、append rollback；
- batch digest 不作为授权凭证，只作为一致性证据；
- receipt 的随机 batch id 不复用 record id；
- 所有 read-only 命令必须通过“目录和文件指纹不变”测试；
- unsafe symlink/FIFO/device、identity swap、partial write、non-finite JSON 全部 fail closed；
- lock GC 必须覆盖同 inode 校验与非阻塞 flock，禁止基于文件年龄直接删除。

## 12. 测试与验收

### 12.1 定向测试

- shared snapshot：完整、不可信、重复 ID、malformed、并发变化、预算边界；
- readiness：2500/100 fixture、只扫描 items 一次、分类与独立 preview 一致；
- summary：分类/reason/group counts、未知 reason 归一化、零隐私字段；
- receipt：canonical digest、prepared-before-write、completed-after-write、两种 append failure、崩溃
  recovery、大小/行数边界；
- lock GC：orphan 删除、live record 保留、held lock 保留、inode swap 阻断、unsafe entry 阻断、
  cap/truncation、fallback report-only；
- CLI/Web：详细输出向后兼容，summary-only 稳定，apply selection 与退出码不漂移。

### 12.2 全量门禁

- `python -m pytest tests/unit -q`
- `python -m pytest tests/system -q`
- `python -m pytest tests/conformance -q`
- `./agent_runtime_kit/hooks/test-hook.sh`
- `python scripts/check-recall-quality.py`
- `python scripts/generate-adapter-governance.py --check`
- `python scripts/generate-lifecycle-governance-report.py --check`
- `ruff check .`
- `python scripts/check_mypy_baseline.py`
- `git diff --check`

### 12.3 真实只读验收

发布并等待 GitHub required checks 全绿后，在真实 brain 仅执行：

- `memory govern readiness --format json`
- `memory sync-pending --summary-only --limit 100 --format json`
- receipt ledger health/read-only summary
- `memory verify`

前后比较 items、pending、runtime receipt ledger 与 index 指纹。真实验收不执行 pending apply、
lifecycle archive、supersession 或 index repair。

## 13. 发布、迁移与回滚

- 新字段均为 additive；旧 CLI 详细 JSON 保持原有字段；
- receipt ledger 缺失视为“尚无 receipt”，不自动创建；
- 第一次新版本 apply 才创建 receipt ledger 与必要 lock 目录；
- readiness 新增 metrics/checks，不删除现有字段；
- feature rollback 后，新 ledger 被旧版本忽略，不影响 items/pending 权威事实；
- 若 shared snapshot 导致意外 unavailable，可通过单一 feature flag 暂时回退旧的独立扫描，
  但 CI 必须持续证明新路径，不把 fallback 当默认；
- 发布仍走直接 fast-forward `main`，不创建 PR；推送后等待 GitHub required checks。

## 14. 完成标准

只有以下证据全部成立才算完成：

1. 根因测试证明 readiness 不再重复扫描 items；
2. 大规模 fixture 在预算内返回完整 pending 分类；
3. summary/receipt 隐私合同通过公开卫生测试；
4. receipt prepared/completed 与部分失败测试通过；
5. lock GC 并发与 inode 安全测试通过；
6. 全量本地门禁通过；
7. `main` 与 `origin/main` 指向同一提交，全部 GitHub checks success；
8. 真实只读 readiness 不再把完整 pending 错报为 scan unavailable；
9. 真实 brain 前后指纹不变；
10. 剩余 pending/stale 数量如实保留，不写成已清理。
