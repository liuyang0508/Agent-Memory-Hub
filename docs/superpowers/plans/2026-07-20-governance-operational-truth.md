# 治理运行真相与可审计批次实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除 pending readiness 的重复 items 扫描假失败，并补齐低敏摘要、两阶段批次回执与安全 record-lock 回收。

**Architecture:** 生命周期扫描生成一份显式可信的 item catalog snapshot，并传给 pending classifier；CLI 和 readiness 统一复用 `PendingPreview.to_summary_dict()`。apply 在既有 queue→catalog 锁序内写 prepared/completed 回执，并在 queue lock 内有界回收可证明 orphan 的 record lock。

**Tech Stack:** Python 3.11+、dataclasses、Typer、descriptor-relative secure I/O、pytest、ruff、mypy baseline。

---

## 文件结构

- Modify: `agent_brain/memory/store/pending.py` — snapshot 合同、summary 聚合、apply 回执接线。
- Create: `agent_brain/memory/governance/pending_receipts.py` — 固定 schema 的低敏 receipt ledger。
- Create: `agent_brain/memory/governance/pending_lock_gc.py` — queue-lock 内有界 lock 观测与回收。
- Modify: `agent_brain/product/governance_readiness.py` — 单次 items 扫描、pending/receipt/lock metrics。
- Modify: `agent_brain/interfaces/cli/commands/maintenance.py` — `--summary-only` 输出。
- Modify: `tests/unit/test_pending_queue.py` — snapshot、summary、receipt、apply 与 lock GC 单元测试。
- Create: `tests/unit/test_pending_receipts.py` — ledger schema、耐久性与失败语义。
- Create: `tests/unit/test_pending_lock_gc.py` — lock 并发、inode 与边界测试。
- Modify: `tests/unit/test_governance_readiness.py` — 单次扫描、规模 fixture、readiness metrics。
- Modify: `tests/unit/test_cli_smoke.py` — preview/apply summary-only 兼容性。
- Modify: `CHANGELOG.md` 和治理文档 — 新增公开操作合同和回滚边界。

### Task 1: 共享可信 item catalog snapshot

**Files:**
- Modify: `agent_brain/memory/store/pending.py`
- Modify: `agent_brain/product/governance_readiness.py`
- Test: `tests/unit/test_pending_queue.py`
- Test: `tests/unit/test_governance_readiness.py`

- [ ] **Step 1: 写 shared snapshot 的失败测试**

```python
def test_readiness_passes_existing_item_catalog_without_rescanning(monkeypatch, tmp_path):
    calls = 0
    def forbidden_scan(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("items rescanned")
    monkeypatch.setattr(pending_module, "_scan_existing_item_metadata", forbidden_scan)
    snapshot = PendingItemCatalogSnapshot(items={}, trusted=True, entry_count=0, metadata_bytes=0)
    preview = PendingQueue(brain=tmp_path).preview_for_readiness(item_catalog=snapshot)
    assert preview.scan_unavailable is False
    assert calls == 0
```

- [ ] **Step 2: 验证测试按预期失败**

Run: `python -m pytest tests/unit/test_pending_queue.py -k 'existing_item_catalog or untrusted_item_catalog' -q`

Expected: FAIL，原因是 `PendingItemCatalogSnapshot` 或 `item_catalog` 参数尚不存在。

- [ ] **Step 3: 实现显式 snapshot 合同和 fail-closed 路径**

```python
@dataclass(frozen=True)
class PendingItemCatalogSnapshot:
    items: Mapping[str, MemoryItem]
    trusted: bool
    reason: str | None = None
    entry_count: int = 0
    metadata_bytes: int = 0
```

`_preview_from_snapshot(..., item_catalog=None)` 仅在参数为 `None` 时扫描 items；显式 untrusted snapshot 返回 `PENDING_ITEM_SNAPSHOT_UNTRUSTED`，不得回退重扫。

- [ ] **Step 4: 让 readiness 的一次扫描同时构造 active metrics 与完整 catalog**

```python
@dataclass(frozen=True)
class _ReadinessItemsSnapshot:
    active_items: tuple[MemoryItem, ...]
    catalog: PendingItemCatalogSnapshot
    archived_count: int
    malformed_count: int
    scan_unavailable: bool
```

扫描需把 archived item 放进 `catalog.items`，但只把 active item 放进 lifecycle 统计。

- [ ] **Step 5: 运行定向测试并提交**

Run: `python -m pytest tests/unit/test_pending_queue.py tests/unit/test_governance_readiness.py -k 'catalog or pending or lifecycle' -q`

Expected: PASS。

Commit: `fix: share trusted catalog with pending readiness`

### Task 2: 统一低敏 pending summary

**Files:**
- Modify: `agent_brain/memory/store/pending.py`
- Modify: `agent_brain/interfaces/cli/commands/maintenance.py`
- Modify: `agent_brain/product/governance_readiness.py`
- Test: `tests/unit/test_pending_queue.py`
- Test: `tests/unit/test_cli_smoke.py`

- [ ] **Step 1: 写隐私 canary 与封闭 reason 测试**

```python
def test_pending_summary_contains_counts_but_no_record_content(tmp_brain):
    preview = PendingQueue(brain=tmp_brain).preview(limit=100)
    encoded = json.dumps(preview.to_summary_dict())
    assert "PRIVATE_TITLE_CANARY" not in encoded
    assert "record_id" not in encoded
    assert preview.to_summary_dict()["schema_version"] == 1
```

- [ ] **Step 2: 验证测试失败**

Run: `python -m pytest tests/unit/test_pending_queue.py -k summary -q`

Expected: FAIL，`to_summary_dict` 尚不存在。

- [ ] **Step 3: 实现封闭聚合器**

```python
def to_summary_dict(self) -> dict[str, object]:
    classifications = Counter(record.classification for record in self.records)
    reasons = Counter(_public_pending_reason(record.reason) for record in self.records)
    return {
        "schema_version": 1,
        "total": self.total,
        "returned": self.returned,
        "truncated": self.truncated,
        "scan_unavailable": self.scan_unavailable,
        "reason": _public_pending_reason(self.reason),
        "classification_counts": dict(sorted(classifications.items())),
        "reason_counts": dict(sorted(reasons.items())),
        "groups": _pending_groups(classifications),
        "oldest_age_seconds": max((r.age_seconds or 0 for r in self.records), default=0),
    }
```

未知 reason 固定归一化为 `UNKNOWN_PENDING_REASON`，不可回传异常文本。

- [ ] **Step 4: 增加 CLI `--summary-only` 并复用同一聚合器**

Preview JSON 只输出 summary；apply JSON 输出 aggregate + receipt，不输出 `results`。详细模式保持原字段和退出码。

- [ ] **Step 5: 运行测试并提交**

Run: `python -m pytest tests/unit/test_pending_queue.py tests/unit/test_cli_smoke.py tests/unit/test_governance_readiness.py -k 'summary or pending' -q`

Expected: PASS。

Commit: `feat: add privacy safe pending summaries`

### Task 3: 两阶段 batch receipt ledger

**Files:**
- Create: `agent_brain/memory/governance/pending_receipts.py`
- Create: `tests/unit/test_pending_receipts.py`

- [ ] **Step 1: 写固定 schema、digest 与隐私失败测试**

```python
def test_receipt_json_is_fixed_schema_and_low_sensitivity():
    prepared = prepare_pending_receipt(selection_mode="explicit", selected=[])
    payload = prepared.to_dict()
    assert set(payload) == PENDING_RECEIPT_FIELDS
    assert payload["state"] == "prepared"
    assert not ({"record_id", "item_id", "title", "summary", "path"} & set(payload))
```

- [ ] **Step 2: 验证测试失败**

Run: `python -m pytest tests/unit/test_pending_receipts.py -q`

Expected: FAIL，模块尚不存在。

- [ ] **Step 3: 实现 receipt value object 与 canonical digest**

```python
@dataclass(frozen=True)
class PendingBatchReceipt:
    schema_version: int
    batch_id: str
    batch_digest: str
    selection_mode: Literal["explicit", "safe_only"]
    requested_count: int
    selected_count: int
    depth_before: int
    depth_after: int | None
    status_counts: Mapping[str, int]
    classification_counts: Mapping[str, int]
    reason_counts: Mapping[str, int]
    index_repair_required_count: int
    warning_counts: Mapping[str, int]
    prepared_at: str
    completed_at: str | None
    state: Literal["prepared", "completed", "incomplete"]
    result_digest: str | None
```

digest 使用 `amh.pending.batch.v1\0` / `amh.pending.result.v1\0` domain separator、排序后的低敏 tuple 和 canonical JSON。

- [ ] **Step 4: 实现 append + fsync + byte rollback 与 bounded reader**

文件固定为 `runtime/pending-apply-receipts.jsonl`、0600；行上限 64 KiB、文件上限 16 MiB、记录上限 100000。append 失败先 truncate 到原长度并 fsync；rollback 失败抛专用错误。

- [ ] **Step 5: 覆盖 prepare/completed、malformed、oversize、symlink 与 partial write**

Run: `python -m pytest tests/unit/test_pending_receipts.py -q`

Expected: PASS。

Commit: `feat: add pending apply receipt ledger`

### Task 4: 将 receipt 接入 apply 事务

**Files:**
- Modify: `agent_brain/memory/store/pending.py`
- Modify: `agent_brain/interfaces/cli/commands/maintenance.py`
- Test: `tests/unit/test_pending_queue.py`
- Test: `tests/unit/test_cli_smoke.py`

- [ ] **Step 1: 写 prepared-before-write 和 completion-failure 测试**

```python
def test_apply_aborts_before_mutation_when_prepared_receipt_fails(monkeypatch, tmp_brain):
    monkeypatch.setattr(pending_module, "append_pending_receipt", Mock(side_effect=OSError()))
    stats = PendingQueue(brain=tmp_brain).apply(record_ids=["approved"])
    assert stats.written == 0
    assert stats.governance_reason == "PENDING_RECEIPT_PREPARE_FAILED"
```

- [ ] **Step 2: 验证测试失败**

Run: `python -m pytest tests/unit/test_pending_queue.py -k receipt -q`

Expected: FAIL，apply stats 尚无 receipt 字段。

- [ ] **Step 3: additive 扩展 `PendingApplyStats`**

增加 `receipt: PendingBatchReceipt | None`、`governance_reason: str | None`、`warning_counts`；默认详细 `to_dict()` 保留 `results`，`to_summary_dict()` 删除逐记录结果。

- [ ] **Step 4: 在 queue→catalog 锁内接入两阶段 append**

选择和 fresh preview 完成后先 append prepared，再执行 `_apply_record`，最后 append completed。prepare 失败必须零写；completed 失败保留逐记录事实，返回 `PENDING_RECEIPT_COMPLETION_FAILED` 与 incomplete receipt。

- [ ] **Step 5: 运行测试并提交**

Run: `python -m pytest tests/unit/test_pending_queue.py tests/unit/test_cli_smoke.py -k 'apply or receipt or sync_pending' -q`

Expected: PASS。

Commit: `feat: audit pending apply batches`

### Task 5: queue-lock 内安全 record-lock GC

**Files:**
- Create: `agent_brain/memory/governance/pending_lock_gc.py`
- Create: `tests/unit/test_pending_lock_gc.py`
- Modify: `agent_brain/memory/store/pending.py`

- [ ] **Step 1: 写 orphan/live/held/inode-swap/fallback 测试**

```python
def test_gc_preserves_held_or_live_record_locks(tmp_brain):
    report = collect_orphan_record_locks(tmp_brain, live_record_names={"live.jsonl"}, apply=True)
    assert report.deleted == 0
    assert report.preserved >= 2
```

- [ ] **Step 2: 验证测试失败**

Run: `python -m pytest tests/unit/test_pending_lock_gc.py -q`

Expected: FAIL，模块尚不存在。

- [ ] **Step 3: 实现有界 report/GC**

`PendingLockGcReport` 只包含 `total`、`orphan`、`deleted`、`preserved`、`unsafe`、`truncated`、`reason`。删除前按 basename hash 对照 live pending、no-follow open、identity 复核、`flock(LOCK_EX|LOCK_NB)`、unlink 前二次 identity、目录 fsync。

- [ ] **Step 4: 只从持有 queue lock 的 apply 路径调用 mutation GC**

普通 readiness 调用 `apply=False` 仅观测；无可靠 dirfd/flock 时返回 `PENDING_LOCK_GC_UNAVAILABLE`，不得 unlink。

- [ ] **Step 5: 运行测试并提交**

Run: `python -m pytest tests/unit/test_pending_lock_gc.py tests/unit/test_pending_queue.py -k 'lock or apply' -q`

Expected: PASS。

Commit: `feat: safely collect orphan pending record locks`

### Task 6: readiness 运行真相与规模回归

**Files:**
- Modify: `agent_brain/product/governance_readiness.py`
- Modify: `agent_brain/interfaces/cli/doctor_offline.py`
- Modify: `tests/unit/test_governance_readiness.py`
- Modify: `tests/unit/test_doctor_offline.py`

- [ ] **Step 1: 写 2500 items / 100 pending 与 receipt/lock metrics 测试**

断言 lifecycle lane 含 `pending_reason_counts`、`pending_lock_files`、`pending_orphan_lock_files`、`pending_receipt_ledger_status`，且 monkeypatch 的 item scanner 只被调用一次。

- [ ] **Step 2: 验证测试失败**

Run: `python -m pytest tests/unit/test_governance_readiness.py -k 'large or receipt or lock or single_scan' -q`

Expected: FAIL，新 metrics 尚不存在。

- [ ] **Step 3: 接入 summary、receipt ledger health 与 lock report**

readiness 全程只读；ledger 缺失为 `not_present`，malformed/oversize/symlink 为 fail；lock unsafe/truncated 进入稳定 reason 和检查状态。

- [ ] **Step 4: 运行定向性能与只读指纹测试**

Run: `python -m pytest tests/unit/test_governance_readiness.py tests/unit/test_doctor_offline.py -q`

Expected: PASS，且测试前后 tree snapshot 相同。

Commit: `feat: report pending operational truth`

### Task 7: 文档、兼容与本地全量门禁

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `docs/storage-lifecycle.zh.md`
- Test: `tests/conformance/test_public_surface_lock.py`
- Test: `tests/unit/test_docs_truth_contract.py`

- [ ] **Step 1: 更新操作合同**

记录 `--summary-only`、receipt states、失败 reason、GC 安全边界、read-only 验收命令，并明确详细 JSON 仍向后兼容。

- [ ] **Step 2: 运行定向合同测试**

Run: `python -m pytest tests/unit/test_docs_truth_contract.py tests/conformance/test_public_surface_lock.py -q`

Expected: PASS。

- [ ] **Step 3: 运行全部本地门禁**

```bash
python -m pytest tests/unit -q
python -m pytest tests/system -q
python -m pytest tests/conformance -q
./agent_runtime_kit/hooks/test-hook.sh
python scripts/check-recall-quality.py
python scripts/generate-adapter-governance.py --check
python scripts/generate-lifecycle-governance-report.py --check
ruff check .
python scripts/check_mypy_baseline.py
git diff --check
```

Expected: 全部 exit 0。

- [ ] **Step 4: 提交发布文档**

Commit: `docs: document pending governance receipts`

### Task 8: 直推 main、GitHub checks 与真实只读验收

**Files:**
- No source changes unless verification exposes a defect.

- [ ] **Step 1: fast-forward 集成到 main 并直接 push**

核对主 worktree 无冲突，`git merge --ff-only feat/governance-operational-truth`，然后 `git push origin main`；不创建 PR。

- [ ] **Step 2: 等待全部 required checks success**

使用 GitHub checks/branch protection 查询确认 9 个 required contexts 与其他 workflow 全绿。

- [ ] **Step 3: 真实 brain 只读验收**

```bash
memory govern readiness --format json
memory sync-pending --summary-only --limit 100 --format json
memory verify
```

验收前后保存 items、pending、receipt ledger 与 index 的 metadata/hash 指纹；不得执行 apply/archive/reindex repair。

- [ ] **Step 4: 形成最终证据**

报告 main/origin SHA、GitHub checks、readiness 关键 metrics、真实指纹不变，以及 28 pending / 318 stale 未被本轮清理的边界。
