# 治理积压闭环与归档版实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐 lifecycle 候选、pending 显式 resolution 与 orphan-lock 独立回收能力，并在代码发布通过后安全闭环真实 brain 的 `318 + 28 + 17` 条治理积压。

**Architecture:** `AutoGovernanceCycle` 把已扫描 item snapshot 随 report 传给现有 `build_maintenance_plan()`，一次修复所有候选调用方。pending 在现有 `PendingQueue` 内增加闭合 resolution action，继续复用 queue/catalog/record locks、`WriteService`、安全 unlink、两阶段 receipt 与既有 lock collector；CLI 只做参数解析和输出。

**Tech Stack:** Python 3.11+、dataclasses、Pydantic、Typer、descriptor-relative no-follow I/O、flock、append-only JSONL receipt、pytest、ruff、mypy。

---

## 文件结构

- Modify: `agent_brain/memory/governance/auto_governance.py` — 在只读 report 中携带本轮已扫描 item snapshot。
- Modify: `agent_brain/memory/governance/maintenance_plan.py` — 默认复用 report snapshot 构建 supersession candidates。
- Modify: `agent_brain/memory/governance/pending_receipts.py` — 兼容 resolution selection、action counts 和新封闭 reason。
- Modify: `agent_brain/memory/store/pending.py` — resolution action、preview/apply、fresh audit/duplicate/conversion 校验、standalone lock GC。
- Modify: `agent_brain/interfaces/cli/commands/maintenance.py` — `sync-pending` 的四组增量参数及互斥/退出语义。
- Modify: `tests/unit/test_lifecycle_review_actions.py` — shared lifecycle plan 候选 wiring 回归。
- Modify: `tests/unit/test_cli_auto_governance.py` — general governance plan 候选回归。
- Modify: `tests/unit/test_pending_receipts.py` — resolution receipt schema、兼容性与隐私。
- Modify: `tests/unit/test_pending_queue.py` — 三类 resolution 的安全、并发、崩溃与恢复语义。
- Modify: `tests/unit/test_pending_lock_gc.py` — standalone queue-lock GC 回归。
- Modify: `tests/unit/test_cli_smoke.py` — CLI parser、preview-first、JSON/text 与退出码。
- Modify: `tests/unit/test_docs_truth_contract.py` — 文档命令合同。
- Modify: `README.md`, `README.zh.md`, `CHANGELOG.md`, `docs/storage-lifecycle.zh.md` — 运维入口、审批边界和恢复语义。

不创建新的生产模块；上述能力已有明确归属，拆新服务只会复制锁与写入边界。

## Task 1：从治理 report 的同一 item snapshot 填充 lifecycle candidates

**Files:**
- Modify: `agent_brain/memory/governance/auto_governance.py:55-93,120-142`
- Modify: `agent_brain/memory/governance/maintenance_plan.py:103-170`
- Modify: `tests/unit/test_lifecycle_review_actions.py`
- Modify: `tests/unit/test_cli_auto_governance.py`

- [ ] **Step 1：写 shared lifecycle plan 的候选失败测试**

```python
def test_lifecycle_review_plan_uses_cycle_item_snapshot_for_candidates(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    old = MemoryItem(
        id="mem-20260101-180030-hook-recall-old",
        type=MemoryType.signal,
        created_at=now - timedelta(days=60),
        project="amh",
        title="Hook recall failure",
        summary="Hook recall failed before the adapter repair",
        tags=["hooks", "recall"],
    )
    replacement = MemoryItem(
        id="mem-20260722-180031-hook-recall-new",
        type=MemoryType.signal,
        created_at=now - timedelta(days=1),
        project="amh",
        title="Hook recall repaired",
        summary="Hook recall issue resolved after adapter repair",
        tags=["hooks", "recall"],
    )
    store.write(old, "old")
    store.write(replacement, "new")

    plan = build_lifecycle_review_plan(
        brain_dir=brain,
        items_store=store,
        now=now,
    )

    row = next(row for row in plan.review_queue if row.item_id == old.id)
    assert row.candidates[0]["replacement_id"] == replacement.id
    assert row.candidates[0]["can_auto_apply"] is False
```

- [ ] **Step 2：运行测试并确认 RED**

Run:

```bash
python -m pytest -q tests/unit/test_lifecycle_review_actions.py::test_lifecycle_review_plan_uses_cycle_item_snapshot_for_candidates
```

Expected: FAIL，`row.candidates == []`。

- [ ] **Step 3：让 report 携带首次扫描的只读 snapshot**

在 `AutoGovernanceReport` 增加不进入公开 `to_dict()` 的字段，并由 `run()` 已有的 `items`
列表填充：

```python
from collections.abc import Mapping


@dataclass(frozen=True)
class AutoGovernanceReport:
    scanned_items: int
    actions: list[AutoGovernanceAction]
    applied_count: int = 0
    apply: bool = False
    items_by_id: Mapping[str, MemoryItem] = field(
        default_factory=dict,
        repr=False,
        compare=False,
    )


def run(self, *, apply: bool = False) -> AutoGovernanceReport:
    items = list(self.items_store.iter_all())
    actions: list[AutoGovernanceAction] = []
    actions.extend(self._maturity_actions(apply=apply))
    actions.extend(self._lifecycle_actions(items))
    actions.extend(self._governance_actions())
    actions.extend(self._drift_actions())
    if self.include_evolve:
        actions.extend(self._evolve_actions())
    if self.include_conversations:
        actions.extend(self._conversation_actions(apply=apply))
    if self.include_index:
        actions.extend(self._index_actions(apply=apply))
    return AutoGovernanceReport(
        scanned_items=len(items),
        actions=actions,
        applied_count=sum(1 for action in actions if action.applied),
        apply=apply,
        items_by_id={item.id: item for item, _body in items},
    )
```

`to_dict()` 保持现有字段，不序列化 `MemoryItem` 或正文。

- [ ] **Step 4：让 maintenance plan 默认使用 report snapshot**

```python
normalized_items = dict(
    report.items_by_id if items_by_id is None else items_by_id
)
normalized_edges = set(supersedes_edges or ())
normalized_edges.update(
    (item.superseded_by, item.id)
    for item in normalized_items.values()
    if item.superseded_by
)
```

显式 `items_by_id={}` 仍表示调用方主动关闭候选；省略参数才使用 report snapshot。

- [ ] **Step 5：补 general `govern plan` 调用方回归并跑测试**

在 `tests/unit/test_cli_auto_governance.py` 创建同 scope 的旧/新 signal，执行：

```python
result = runner.invoke(
    app,
    [
        "govern",
        "plan",
        "--format",
        "json",
        "--category",
        "lifecycle",
        "--limit",
        "100",
        "--no-index-repair",
        "--no-evolve",
        "--no-conversations",
    ],
)
payload = json.loads(result.output)
row = next(row for row in payload["review_queue"] if row["item_id"] == old.id)
assert row["candidates"][0]["replacement_id"] == replacement.id
assert row["candidates"][0]["can_auto_apply"] is False
```

Run:

```bash
python -m pytest -q tests/unit/test_lifecycle_review_actions.py tests/unit/test_cli_auto_governance.py tests/unit/test_lifecycle_candidates.py
```

Expected: PASS。

- [ ] **Step 6：提交候选根因修复**

```bash
git add agent_brain/memory/governance/auto_governance.py \
  agent_brain/memory/governance/maintenance_plan.py \
  tests/unit/test_lifecycle_review_actions.py \
  tests/unit/test_cli_auto_governance.py
git commit -m "fix: populate lifecycle review candidates"
```

## Task 2：扩展现有低敏 receipt 以承载 resolution

**Files:**
- Modify: `agent_brain/memory/governance/pending_receipts.py:20-170,200-310,530-560`
- Modify: `tests/unit/test_pending_receipts.py`

- [ ] **Step 1：写 resolution receipt 与旧 ledger 兼容失败测试**

```python
def test_resolution_receipt_binds_actions_without_public_identifiers() -> None:
    receipt = prepare_pending_receipt(
        selection_mode="resolution",
        requested_count=2,
        selected=[
            PendingReceiptSelection(
                record_id="pending-audit-one",
                payload_sha256="a" * 64,
                action="approve_audit",
            ),
            PendingReceiptSelection(
                record_id="pending-duplicate-two",
                payload_sha256="b" * 64,
                action="accept_duplicate",
                target_digest="c" * 64,
            ),
        ],
        depth_before=2,
    )

    payload = receipt.to_dict()
    rendered = json.dumps(payload, sort_keys=True)
    assert payload["selection_mode"] == "resolution"
    assert payload["action_counts"] == {
        "accept_duplicate": 1,
        "approve_audit": 1,
    }
    assert "pending-audit-one" not in rendered
    assert "pending-duplicate-two" not in rendered
```

再构造一行不含 `action_counts` 的既有 schema-v1 receipt，确认
`read_pending_receipt_ledger_health()` 仍返回 `healthy`。

- [ ] **Step 2：运行测试并确认 RED**

Run:

```bash
python -m pytest -q tests/unit/test_pending_receipts.py -k resolution
```

Expected: FAIL，selection mode、selection action 和 action counts 尚不支持。

- [ ] **Step 3：最小扩展 selection 与 receipt**

```python
PendingSelectionMode = Literal["explicit", "safe_only", "resolution"]
PendingReceiptAction = Literal[
    "apply",
    "approve_audit",
    "accept_duplicate",
    "convert_type",
]
_ALLOWED_ACTIONS = frozenset(
    {"apply", "approve_audit", "accept_duplicate", "convert_type"}
)
_ALLOWED_STATUSES = frozenset(
    {
        "written",
        "already_written",
        "review_required",
        "skipped",
        "failed",
        "applied",
        "blocked",
    }
)


@dataclass(frozen=True)
class PendingReceiptSelection:
    record_id: str
    payload_sha256: str
    action: PendingReceiptAction = "apply"
    target_digest: str | None = None


@dataclass(frozen=True)
class PendingBatchReceipt:
    schema_version: int
    batch_id: str
    batch_digest: str
    selection_mode: PendingSelectionMode
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
    action_counts: Mapping[str, int] = field(default_factory=dict)
```

`PENDING_RECEIPT_FIELDS` 加入 `action_counts`；同时保留
`PENDING_RECEIPT_V1_FIELDS = PENDING_RECEIPT_FIELDS - {"action_counts"}`。`_parse_receipt()`
只接受这两个精确字段集合，旧集合先执行 `data["action_counts"] = {}` 再构造 dataclass；其他缺失
或额外字段继续判 corrupt，不改旧 schema version。

- [ ] **Step 4：把 action/target 绑定进 selection digest**

`prepare_pending_receipt()` 校验：

```python
canonical_selection: list[tuple[str, str, str, str | None]] = []
actions: Counter[str] = Counter()
for selection in selections:
    if (
        type(selection) is not PendingReceiptSelection
        or not selection.record_id
        or _HEX_64.fullmatch(selection.payload_sha256) is None
        or selection.action not in _ALLOWED_ACTIONS
        or (
            selection.target_digest is not None
            and _HEX_64.fullmatch(selection.target_digest) is None
        )
    ):
        raise TypeError("INVALID_PENDING_RECEIPT_SELECTION")
    canonical_selection.append(
        (
            selection.record_id,
            selection.payload_sha256,
            selection.action,
            selection.target_digest,
        )
    )
    actions[selection.action] += 1
```

resolution 使用 domain `b"amh.pending.resolution.batch.v1"`；普通 apply 继续使用原 domain 和原
二元 canonical tuple，保证既有 digest 合同不漂移。`_valid_receipt()` 只允许封闭 action counts；
`selection_mode == "resolution"` 时 counts 总和必须等于 `selected_count`，旧
`explicit/safe_only` receipt 允许空 counts。`_receipt_sequence_health()` 额外要求同一 batch 的
prepared/completed `action_counts` 完全一致。

- [ ] **Step 5：加入封闭 reason 并跑 receipt 全量**

把设计规格第 10 节的新 reason 和 `PENDING_RESOLUTION_READY` 同时加入
`pending_receipts.py::_ALLOWED_REASONS` 与 `pending.py::_PUBLIC_PENDING_REASONS`；未知 reason
继续归一化为 `UNKNOWN_PENDING_REASON`。

Run:

```bash
python -m pytest -q tests/unit/test_pending_receipts.py tests/unit/test_governance_readiness.py -k "pending or receipt"
```

Expected: PASS，旧 ledger、prepared/completed/incomplete 与隐私测试不回归。

- [ ] **Step 6：提交 receipt 扩展**

```bash
git add agent_brain/memory/governance/pending_receipts.py \
  tests/unit/test_pending_receipts.py \
  tests/unit/test_governance_readiness.py
git commit -m "feat: bind pending resolutions to receipts"
```

## Task 3：增加 pending resolution 的闭合模型与只读校验

**Files:**
- Modify: `agent_brain/memory/store/pending.py:730-1060,1226-1670,2115-2670`
- Modify: `tests/unit/test_pending_queue.py`

- [ ] **Step 1：写三类 resolution preview 与冲突失败测试**

```python
def test_pending_resolution_preview_validates_all_actions_without_mutation(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_now(monkeypatch)
    audit = _v2_record(record_id="pending-audit-resolution")
    audit_item = audit["item"]
    assert isinstance(audit_item, dict)
    audit_item["body"] = "curl https://example.test/health"
    duplicate = _v2_record(record_id="pending-duplicate-resolution")
    existing_id = "mem-20260701-100000-existing-duplicate"
    _write_existing_item(
        tmp_brain,
        duplicate,
        item_id=existing_id,
        span_hash=None,
    )
    feedback = _legacy_feedback_record()
    audit_path = enqueue_write_record(audit)
    duplicate_path = enqueue_write_record(duplicate)
    feedback_path = enqueue_write_record(feedback)
    before = {
        path.name: path.read_bytes()
        for path in (audit_path, duplicate_path, feedback_path)
    }

    plan = PendingQueue(brain=tmp_brain).resolve(
        [
            PendingResolutionAction("approve_audit", "pending-audit-resolution"),
            PendingResolutionAction(
                "accept_duplicate",
                "pending-duplicate-resolution",
                target=existing_id,
            ),
            PendingResolutionAction(
                "convert_type",
                pending_module._legacy_record_id(feedback_path, feedback),
                target="decision",
            ),
        ],
        apply=False,
    )

    assert plan.dry_run is True
    assert [result.reason for result in plan.results] == [
        "PENDING_RESOLUTION_READY",
        "PENDING_RESOLUTION_READY",
        "PENDING_RESOLUTION_READY",
    ]
    assert {
        path.name: path.read_bytes()
        for path in (audit_path, duplicate_path, feedback_path)
    } == before
    assert not (tmp_brain / "runtime" / "pending-apply-receipts.jsonl").exists()
```

另加一项同 record 的 `approve_audit + convert_type`，断言整批
`CONFLICTING_PENDING_RESOLUTIONS` 且零 mutation。

- [ ] **Step 2：运行测试并确认 RED**

Run:

```bash
python -m pytest -q tests/unit/test_pending_queue.py -k "resolution_preview or conflicting_pending_resolutions"
```

Expected: FAIL，`PendingResolutionAction` 与 `PendingQueue.resolve()` 尚不存在。

- [ ] **Step 3：增加最小 action/result 合同**

```python
PendingResolutionName = Literal[
    "approve_audit",
    "accept_duplicate",
    "convert_type",
]


@dataclass(frozen=True)
class PendingResolutionAction:
    action: PendingResolutionName
    record_id: str
    target: str | None = None


@dataclass(frozen=True)
class PendingResolutionResult:
    action: PendingResolutionName
    record_id: str
    status: Literal["ready", "applied", "blocked", "failed"]
    reason: str
    classification: PendingClassification | None
    target: str | None = None
    item_id: str | None = None
    index_repair_required: bool = False
    warnings: tuple[str, ...] = ()


@dataclass
class PendingResolutionStats:
    dry_run: bool
    results: list[PendingResolutionResult] = dataclass_field(default_factory=list)
    receipt: PendingBatchReceipt | None = None
    governance_reason: str | None = None
    lock_gc_report: PendingLockGcReport | None = None


@dataclass(frozen=True)
class _PendingResolutionSelection:
    action: PendingResolutionAction
    preview: PendingRecordPreview
    audit_digest: str | None = None
    target_digest: str | None = None


def _blocked_resolution(
    selection: _PendingResolutionSelection,
    reason: str,
) -> PendingResolutionResult:
    return PendingResolutionResult(
        action=selection.action.action,
        record_id=selection.action.record_id,
        status="blocked",
        reason=reason,
        classification=selection.preview.classification,
        target=selection.action.target,
    )
```

`PendingResolutionStats.to_dict()` 可输出显式明细；`to_summary_dict()` 只输出 action/status/reason
counts、receipt 和 lock-GC 聚合，不输出 record/item/target。私有 selection 只在本次调用内保存
preview 的 record identity/hash、audit finding digest 和 target digest，不落盘。

- [ ] **Step 4：实现全批只读校验**

`PendingQueue.resolve(actions, apply=False, gc_orphan_locks=False)`：

1. action 精确去重；
2. 同 record 不同 action/target 返回批次冲突；
3. 完整 trusted preview；
4. record 必须唯一；
5. 按动作校验当前 classification/reason/target；
6. `apply=False` 返回 `ready`，不创建 runtime、receipt 或 lock。

核心校验使用现有数据：

```python
def _resolution_reason(
    action: PendingResolutionAction,
    preview: PendingRecordPreview,
) -> str | None:
    if action.action == "approve_audit":
        if (
            preview.classification != "audit_blocked"
            or preview.reason != "AUDIT_BLOCKED"
            or preview.sensitivity not in {"public", "internal"}
            or action.target is not None
        ):
            return "PENDING_RESOLUTION_NOT_APPLICABLE"
        return None
    if action.action == "accept_duplicate":
        if (
            preview.classification != "duplicate_candidate"
            or preview.reason != "SAME_SCOPE_METADATA_DUPLICATE"
            or action.target is None
        ):
            return "PENDING_DUPLICATE_TARGET_MISMATCH"
        return None
    if action.action == "convert_type":
        if (
            preview.classification != "unsupported_type"
            or preview.reason != "UNSUPPORTED_MEMORY_TYPE"
            or preview.type != "feedback"
            or action.target != "decision"
        ):
            return "PENDING_CONVERSION_UNSUPPORTED"
        return None
    return "PENDING_RESOLUTION_NOT_APPLICABLE"
```

duplicate 的 exact metadata/scope 证明与 conversion 的 schema/body 证明放在 record snapshot
校验中完成，preview 不能只信展示字段。audit digest 由排序后的
`(rule_id, severity, category)` 计算；apply 时必须相等。

```python
def _audit_finding_digest(report: AuditReport) -> str:
    rows = sorted(
        (finding.rule_id, finding.severity, finding.category)
        for finding in report.findings
    )
    payload = json.dumps(
        rows,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(
        b"amh.pending.audit-findings.v1\0" + payload
    ).hexdigest()
```

- [ ] **Step 5：覆盖 private/secret、错误 target、重复 action 和零副作用**

加入参数化测试，断言：

```python
assert result.status == "blocked"
assert pending_path.exists()
assert list((tmp_brain / "items").glob("*.md")) == expected_existing
assert not (tmp_brain / "runtime" / "pending-apply-receipts.jsonl").exists()
```

Run:

```bash
python -m pytest -q tests/unit/test_pending_queue.py -k "resolution"
```

Expected: PASS。

- [ ] **Step 6：提交 resolution preview**

```bash
git add agent_brain/memory/store/pending.py tests/unit/test_pending_queue.py
git commit -m "feat: preview explicit pending resolutions"
```

## Task 4：在既有锁与 WriteService 内执行三类 resolution

**Files:**
- Modify: `agent_brain/memory/store/pending.py:1500-1815,2012-2225,2500-2670`
- Modify: `tests/unit/test_pending_queue.py`
- Modify: `tests/unit/test_pending_receipts.py`

- [ ] **Step 1：写 audit approval 的 RED 测试**

```python
def test_approved_non_secret_audit_record_writes_through_write_service(
    tmp_brain: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_now(monkeypatch)
    record = _v2_record(record_id="pending-approved-curl")
    item = record["item"]
    assert isinstance(item, dict)
    item["body"] = "curl https://example.test/health"
    path = enqueue_write_record(record)

    stats = PendingQueue(brain=tmp_brain).resolve(
        [PendingResolutionAction("approve_audit", "pending-approved-curl")],
        apply=True,
    )

    assert stats.results[0].status == "applied"
    assert not path.exists()
    assert len(list(ItemsStore(tmp_brain / "items").iter_all())) == 1
    assert stats.receipt is not None
    assert stats.receipt.state == "completed"
    assert stats.receipt.action_counts == {"approve_audit": 1}
```

再用含 secrets 类 finding 的 body，断言 `PENDING_AUDIT_SECRET_BLOCKED`、pending 保留、item 未写；
测试名固定为 `test_approved_audit_never_bypasses_secret_findings`。

- [ ] **Step 2：写 duplicate acceptance 与 feedback conversion 的 RED 测试**

```python
def test_accept_duplicate_removes_only_exact_pending_record(tmp_brain: Path) -> None:
    record = _v2_record(record_id="pending-accept-exact-duplicate")
    existing_id = "mem-20260701-100000-duplicate-target"
    _write_existing_item(
        tmp_brain,
        record,
        item_id=existing_id,
        span_hash=None,
    )
    path = enqueue_write_record(record)

    stats = PendingQueue(brain=tmp_brain).resolve(
        [
            PendingResolutionAction(
                "accept_duplicate",
                "pending-accept-exact-duplicate",
                target=existing_id,
            )
        ],
        apply=True,
    )

    assert stats.results[0].status == "applied"
    assert not path.exists()
    assert len(list(ItemsStore(tmp_brain / "items").iter_all())) == 1


def test_convert_feedback_to_decision_preserves_intent_and_required_sections(
    tmp_brain: Path,
) -> None:
    record = _legacy_feedback_record()
    path = enqueue_write_record(record)
    record_id = pending_module._legacy_record_id(path, record)

    stats = PendingQueue(brain=tmp_brain).resolve(
        [PendingResolutionAction("convert_type", record_id, target="decision")],
        apply=True,
    )

    item, body = next(ItemsStore(tmp_brain / "items").iter_all())
    assert item.type == "decision"
    assert "legacy feedback body" in body
    assert all(
        section in body
        for section in ("**决策**", "**理由**", "**改回去的代价**")
    )
    assert not path.exists()
```

- [ ] **Step 3：写真实数量分布的临时 brain smoke**

在同一个 `tmp_brain` 中 enqueue 真实数量分布：

```python
actions: list[PendingResolutionAction] = []
for index in range(21):
    record_id = f"pending-audit-{index:03d}"
    record = _v2_record(record_id=record_id)
    item = record["item"]
    assert isinstance(item, dict)
    item["title"] = f"approved curl operation {index}"
    item["summary"] = f"approved curl operation summary {index}"
    item["body"] = "curl https://example.test/health"
    enqueue_write_record(record)
    actions.append(PendingResolutionAction("approve_audit", record_id))

duplicate_target = "mem-20260701-100000-real-distribution-target"
for index in range(4):
    record_id = f"pending-duplicate-{index:03d}"
    record = _v2_record(record_id=record_id)
    if index == 0:
        _write_existing_item(
            tmp_brain,
            record,
            item_id=duplicate_target,
            span_hash=None,
        )
    enqueue_write_record(record)
    actions.append(
        PendingResolutionAction(
            "accept_duplicate",
            record_id,
            target=duplicate_target,
        )
    )

for _index in range(3):
    record = _legacy_feedback_record()
    path = enqueue_write_record(record)
    actions.append(
        PendingResolutionAction(
            "convert_type",
            pending_module._legacy_record_id(path, record),
            target="decision",
        )
    )

lock_dir = tmp_brain / "pending" / ".amh-record-locks"
lock_dir.mkdir(parents=True, exist_ok=True)
for index in range(17):
    name = hashlib.sha256(f"orphan-{index}".encode()).hexdigest()[:32]
    lock = lock_dir / f"{name}.lock"
    lock.write_bytes(b"")
    lock.chmod(0o600)

preview = PendingQueue(brain=tmp_brain).resolve(actions, apply=False)
assert len(preview.results) == 28
assert {result.status for result in preview.results} == {"ready"}
assert PendingQueue(brain=tmp_brain).depth() == 28

applied = PendingQueue(brain=tmp_brain).resolve(
    actions,
    apply=True,
    gc_orphan_locks=True,
)
assert len(applied.results) == 28
assert {result.status for result in applied.results} == {"applied"}
assert PendingQueue(brain=tmp_brain).depth() == 0
assert applied.lock_gc_report is not None
# 17 个既有 orphan + 本批 28 个 record lock。
assert applied.lock_gc_report.deleted == 45
assert applied.receipt is not None
assert applied.receipt.state == "completed"
assert len(list(ItemsStore(tmp_brain / "items").iter_all())) == 25
```

测试名固定为
`test_pending_resolution_real_distribution_smoke`，供发布前单独重跑。

- [ ] **Step 4：实现 queue → catalog → prepared receipt → record lock 顺序**

`resolve(..., apply=True)` 必须沿用：

```python
with _locked_pending_queue(self._brain_dir()):
    store = ItemsStore(self._brain_dir() / "items")
    with store.locked_catalog():
        return self._resolve_catalog_locked(
            actions=normalized_actions,
            gc_orphan_locks=gc_orphan_locks,
        )
```

`_resolve_catalog_locked()` 在任何 mutation 前完成全部结构校验并 append prepared receipt；
prepared append 失败为所有 action 返回 `PENDING_RECEIPT_PREPARE_FAILED`。

receipt selection 的 `target_digest` 使用：

```python
def _resolution_target_digest(target: str | None) -> str | None:
    if target is None:
        return None
    return hashlib.sha256(
        b"amh.pending.resolution.target.v1\0" + target.encode("utf-8")
    ).hexdigest()
```

receipt 只存 digest，不存 duplicate item ID 或 conversion target。

- [ ] **Step 5：实现 fresh record validation 和三条 mutation path**

每条 action 在 `_locked_pending_record(path)` 内：

1. `_read_pending_record_snapshot()`；
2. identity 与 preview `_record_identity` 相同；
3. SHA-256 与 preview `_record_sha256` 相同；
4. 重新构造 item/body；
5. 重新检查当前动作事实；
6. 写入或 exact unlink。

audit approval：

```python
report = audit_memory_text(f"{item.title}\n{item.summary}\n{body}")
if any(finding.category == "secrets" for finding in report.findings):
    return _blocked_resolution(selection, "PENDING_AUDIT_SECRET_BLOCKED")
if report.passed:
    return _blocked_resolution(selection, "PENDING_RESOLUTION_CHANGED")
if _audit_finding_digest(report) != selection.audit_digest:
    return _blocked_resolution(selection, "PENDING_AUDIT_FINDINGS_CHANGED")
write_result = service.write(item=item, body=body, allow_unsafe=True)
```

duplicate acceptance：从 `ItemsStore.get(action.target)` 读取目标，复用 `_same_scope()`，并要求
type、规范化 title、规范化 summary 全相等；通过后只调用现有
`_unlink_pending_record(path, expected_hash, expected_identity)`。

conversion：复制 raw item dict，将 `type` 改为 `decision`，正文固定转换为：

```python
converted_body = (
    f"**决策**\n\n{body.strip()}\n\n"
    "**理由**\n\n"
    "该内容来自旧版 feedback 中已确认的长期约束，迁移为 decision 后才能进入统一记忆模型。\n\n"
    "**改回去的代价**\n\n"
    "恢复为不受支持的 feedback 会让该约束再次滞留在 pending，无法被正常维护和召回。"
)
```

对替换后的 dict 调用现有 `_validate_pending_item()`；`WriteService.write()` 成功且 source ledger
未 degraded 后才 exact unlink。index degraded 记录 `index_repair_required=True`，不撤销 Markdown
事实源。

- [ ] **Step 6：完成 receipt 与部分失败语义**

finally 阶段复用 `complete_pending_receipt()`：

- 每个 action 必须产生一个 outcome，不能静默跳过 unsupported；
- completed append 失败返回 incomplete receipt 和
  `PENDING_RECEIPT_COMPLETION_FAILED`；
- record 消失、hash 变化、audit finding 变化、目标变化都保留 pending；
- `safe_only` 既有路径补一条结果，使 unsupported/review records 不再从 stats 静默消失。

- [ ] **Step 7：跑并发、崩溃、隐私和既有 apply 回归**

Run:

```bash
python -m pytest -q tests/unit/test_pending_queue.py tests/unit/test_pending_receipts.py
```

Expected: PASS，既有 explicit/safe-only apply、prepared/completed receipt、record identity replacement、
directory fsync 与 source-ledger tests 均不回归。

- [ ] **Step 8：提交 resolution apply**

```bash
git add agent_brain/memory/store/pending.py \
  tests/unit/test_pending_queue.py \
  tests/unit/test_pending_receipts.py
git commit -m "feat: apply governed pending resolutions"
```

## Task 5：复用 lock collector 暴露 standalone GC 和 CLI

**Files:**
- Modify: `agent_brain/memory/store/pending.py`
- Modify: `agent_brain/interfaces/cli/commands/maintenance.py:204-315`
- Modify: `tests/unit/test_pending_lock_gc.py`
- Modify: `tests/unit/test_cli_smoke.py`

- [ ] **Step 1：写 standalone GC preview/apply 的 RED 测试**

```python
def test_pending_queue_collect_orphan_locks_is_preview_first(
    tmp_brain: Path,
) -> None:
    lock_dir = tmp_brain / "pending" / ".amh-record-locks"
    lock_dir.mkdir(parents=True)
    orphan = lock_dir / f"{'0' * 32}.lock"
    orphan.write_bytes(b"")
    orphan.chmod(0o600)
    queue = PendingQueue(brain=tmp_brain)

    preview = queue.collect_orphan_locks(apply=False)
    assert preview.orphan == 1
    assert preview.deleted == 0
    assert orphan.exists()
    assert not (tmp_brain / "runtime").exists()

    applied = queue.collect_orphan_locks(apply=True)
    assert applied.deleted == 1
    assert not orphan.exists()
```

- [ ] **Step 2：实现只包一层 queue lock 的方法**

```python
def collect_orphan_locks(self, *, apply: bool = False) -> PendingLockGcReport:
    if not apply:
        return self._collect_orphan_locks_unlocked(apply=False)
    with _locked_pending_queue(self._brain_dir()):
        return self._collect_orphan_locks_unlocked(apply=True)


def _collect_orphan_locks_unlocked(self, *, apply: bool) -> PendingLockGcReport:
    snapshot = _pending_record_paths(
        self._brain_dir() / "pending",
        entry_cap=MAX_PENDING_QUEUE_ENTRIES,
    )
    if snapshot.scan_unavailable or snapshot.total > len(snapshot.paths):
        return PendingLockGcReport(
            truncated=True,
            reason="PENDING_LOCK_GC_TRUNCATED",
        )
    return collect_pending_record_locks(
        self._brain_dir() / "pending",
        live_record_names={path.name for path in snapshot.paths},
        apply=apply,
        limit=MAX_PENDING_QUEUE_ENTRIES,
    )
```

preview 不获取会创建 runtime queue-lock 的 mutation lock，保证目录树零变化；apply 才持有全局
queue lock。两条路径都不修改 `pending_lock_gc.py` 的安全删除算法。

- [ ] **Step 3：写 CLI preview、互斥和 apply RED 测试**

```python
preview = runner.invoke(
    app,
    ["sync-pending", "--gc-orphan-locks", "--format", "json"],
)
assert preview.exit_code == 0
assert json.loads(preview.output)["dry_run"] is True

conflict = runner.invoke(
    app,
    [
        "sync-pending",
        "--record",
        "pending-one",
        "--approve-audit",
        "pending-two",
        "--apply",
    ],
)
assert conflict.exit_code == 2
```

分别覆盖 repeatable `--approve-audit`、`--accept-duplicate ID:ITEM`、
`--convert-type ID:decision`，以及无 `--apply` 时零 mutation。

- [ ] **Step 4：扩展 `sync-pending` 薄 CLI**

新增 Typer options：

```python
approve_audit: list[str] = typer.Option([], "--approve-audit")
accept_duplicate: list[str] = typer.Option([], "--accept-duplicate")
convert_type: list[str] = typer.Option([], "--convert-type")
gc_orphan_locks: bool = typer.Option(False, "--gc-orphan-locks")
```

解析后构造 `PendingResolutionAction`。以下 selection group 两两互斥：

1. `--record`
2. `--safe-only`
3. 任一 resolution option

`--gc-orphan-locks` 可单独使用，也可附加到 resolution；默认 preview。详细 JSON 调用
`PendingResolutionStats.to_dict()`，`--summary-only` 调用低敏聚合。任一 action 未 applied、receipt
incomplete 或 GC unsafe/truncated 时 exit 1；参数错误 exit 2。

- [ ] **Step 5：跑 CLI 与 lock-GC 回归**

Run:

```bash
python -m pytest -q \
  tests/unit/test_pending_lock_gc.py \
  tests/unit/test_cli_smoke.py \
  tests/unit/test_pending_queue.py
```

Expected: PASS。

- [ ] **Step 6：提交 CLI 与 standalone GC**

```bash
git add agent_brain/memory/store/pending.py \
  agent_brain/interfaces/cli/commands/maintenance.py \
  tests/unit/test_pending_lock_gc.py \
  tests/unit/test_cli_smoke.py
git commit -m "feat: expose pending resolution governance"
```

## Task 6：同步文档合同并完成临时 brain 验证

**Files:**
- Modify: `README.md`
- Modify: `README.zh.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/storage-lifecycle.zh.md`
- Modify: `tests/unit/test_docs_truth_contract.py`

- [ ] **Step 1：写文档 truth 的 RED 测试**

```python
def test_pending_resolution_docs_are_preview_first_and_secret_safe() -> None:
    readme = Path("README.zh.md").read_text(encoding="utf-8")
    lifecycle = Path("docs/storage-lifecycle.zh.md").read_text(encoding="utf-8")
    combined = f"{readme}\n{lifecycle}"
    assert "sync-pending --approve-audit" in combined
    assert "sync-pending --accept-duplicate" in combined
    assert "sync-pending --convert-type" in combined
    assert "sync-pending --gc-orphan-locks" in combined
    assert "默认预览" in combined
    assert "secrets" in combined
    assert "--apply" in combined
```

- [ ] **Step 2：更新中英文运维说明**

文档明确：

- 三类 resolution 都要求显式 record；
- 不加 `--apply` 只预览；
- audit approval 不能绕过 secrets；
- duplicate acceptance 不写新 item；
- conversion 当前只支持 `feedback -> decision`；
- standalone GC 只删除可证明、未持锁的 orphan；
- receipt 不公开原始 ID 与正文；
- 失败后如何重跑 preview、index verify 和 readiness。

- [ ] **Step 3：运行静态与完整单测**

Run:

```bash
python -m ruff check agent_brain tests
python scripts/check_mypy_baseline.py
python -m pytest -q tests/unit
python -m pytest -q tests/conformance
python -m pytest -q tests/unit/test_docs_truth_contract.py
```

Expected:

- ruff PASS；
- mypy 不新增相对 `.github/mypy-baseline.txt` 的错误；
- unit、conformance、docs truth 全部 PASS。

- [ ] **Step 4：重跑真实数量分布与 secret 负例**

Run:

```bash
python -m pytest -q \
  tests/unit/test_pending_queue.py::test_pending_resolution_real_distribution_smoke \
  tests/unit/test_pending_queue.py::test_approved_audit_never_bypasses_secret_findings
```

Expected: PASS；第一项在 pytest 临时 brain 中完成 `21 + 4 + 3 + 17` 的 preview/apply，第二项证明
secret finding 始终保留在 pending。

- [ ] **Step 5：提交文档与验证证据**

```bash
git add README.md README.zh.md CHANGELOG.md \
  docs/storage-lifecycle.zh.md tests/unit/test_docs_truth_contract.py
git commit -m "docs: document pending resolution governance"
```

## Task 7：发布代码后闭环真实 brain

**Files:**
- No repository code changes.
- Create locally with mode `0600`: `~/.agent-memory-hub/runtime/governance-manifests/2026-07-23-lifecycle.json`
- Create locally with mode `0600`: `~/.agent-memory-hub/runtime/governance-manifests/2026-07-23-pending.json`

- [ ] **Step 1：最终 diff 与测试复核**

Run:

```bash
git status --short
git diff origin/main...HEAD --check
git log --oneline origin/main..HEAD
python -m pytest -q \
  tests/unit/test_lifecycle_review_actions.py \
  tests/unit/test_cli_auto_governance.py \
  tests/unit/test_pending_receipts.py \
  tests/unit/test_pending_queue.py \
  tests/unit/test_pending_lock_gc.py \
  tests/unit/test_cli_smoke.py \
  tests/unit/test_governance_readiness.py \
  tests/unit/test_docs_truth_contract.py
```

Expected: 仅计划内文件变化，targeted suite PASS。

- [ ] **Step 2：直接推送 GitHub main**

Run:

```bash
git fetch origin main
git rebase origin/main
git push origin HEAD:main
```

Expected: fast-forward push；不创建 PR。若 remote 已前进且 rebase 冲突，停止并保留冲突现场，不
强推。

- [ ] **Step 3：等待 required checks**

Run:

```bash
gh run list --branch main --commit "$(git rev-parse HEAD)" --limit 20
```

Expected: 该 commit 的全部 required workflows 为 `completed/success`。在此之前不操作真实
brain。

- [ ] **Step 4：重新采集真实只读快照**

Run:

```bash
memory index verify --format json
memory govern readiness --format json
memory govern plan \
  --category lifecycle --limit 1000 --format json \
  --no-index-repair --no-evolve --no-conversations
memory sync-pending --limit 100 --format json
memory sync-pending --gc-orphan-locks --format json
```

Expected baseline：

- index clean；
- lifecycle review 仍为 318，或对 drift 明确停下重审；
- pending 为 `21 audit_blocked + 4 duplicate_candidate + 3 unsupported_type`，或对 drift 停下；
- orphan locks 为 17、unsafe 0，或对 drift 停下。

- [ ] **Step 5：生成并复核 mode-0600 manifests**

生命周期 manifest 必须恰好覆盖当前 review queue：

- 238 条同时带 `session-active` 与 `auto-captured` 的 signal：`archive`；
- 80 条业务 signal/handoff：逐条 `archive`、`supersede:<id>` 或 `keep-active`；
- 未解除的凭据、安全、生产风险：`keep-active`；
- 只有明确 replacement 才 `supersede`；
- 不允许空动作或一个 item 多动作。

pending manifest：

- 21 条 `approve_audit`；
- 4 条 `accept_duplicate`，目标使用规格中的 exact mapping；
- 3 条 `convert_type:decision`。

Run:

```bash
install -d -m 700 ~/.agent-memory-hub/runtime/governance-manifests
chmod 600 ~/.agent-memory-hub/runtime/governance-manifests/2026-07-23-*.json
shasum -a 256 ~/.agent-memory-hub/runtime/governance-manifests/2026-07-23-*.json
```

Expected: 两个稳定 digest；manifest 不含正文、summary、路径或 secret。

- [ ] **Step 6：执行真实 dry-run**

先定义只读参数生成函数；它们只把 manifest 的封闭 action 转成 NUL 分隔 argv：

```bash
LIFECYCLE_MANIFEST="$HOME/.agent-memory-hub/runtime/governance-manifests/2026-07-23-lifecycle.json"
PENDING_MANIFEST="$HOME/.agent-memory-hub/runtime/governance-manifests/2026-07-23-pending.json"

lifecycle_args() {
  python - "$LIFECYCLE_MANIFEST" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
for row in manifest["actions"]:
    action = row["action"]
    if action == "archive":
        values = ("--archive", row["item_id"])
    elif action == "keep-active":
        values = ("--keep-active", row["item_id"])
    elif action == "supersede":
        values = ("--supersede", f'{row["item_id"]}:{row["replacement_id"]}')
    else:
        raise SystemExit("INVALID_LIFECYCLE_MANIFEST_ACTION")
    for value in values:
        sys.stdout.buffer.write(value.encode() + b"\0")
PY
}

pending_args() {
  python - "$PENDING_MANIFEST" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
for row in manifest["actions"]:
    action = row["action"]
    if action == "approve_audit":
        values = ("--approve-audit", row["record_id"])
    elif action == "accept_duplicate":
        values = (
            "--accept-duplicate",
            f'{row["record_id"]}:{row["existing_item_id"]}',
        )
    elif action == "convert_type":
        values = ("--convert-type", f'{row["record_id"]}:decision')
    else:
        raise SystemExit("INVALID_PENDING_MANIFEST_ACTION")
    for value in values:
        sys.stdout.buffer.write(value.encode() + b"\0")
PY
}

lifecycle_args | xargs -0 memory govern apply-lifecycle \
  --dry-run --format json
pending_args | xargs -0 memory sync-pending \
  --gc-orphan-locks --format json
```

Expected：

- lifecycle requested count 等于 manifest 行数；
- resolution 全部 `ready`；
- GC orphan=17、deleted=0；
- 无 conflict、secret finding、missing target、changed hash；
- filesystem tree 与 index verify 均不变。

- [ ] **Step 7：执行真实 apply 并外部复核**

Run:

```bash
lifecycle_args | xargs -0 memory govern apply-lifecycle \
  --apply --format json
pending_args | xargs -0 memory sync-pending \
  --gc-orphan-locks --apply --format json
memory index verify --format json
memory govern readiness --format json
env -i HOME="$HOME" PATH="$PATH" memory index verify --format json
env -i HOME="$HOME" PATH="$PATH" memory govern readiness --format json
```

Expected：

- 238 条机械信号归档，零删除；
- 80 条业务记录全部命中 manifest；
- pending depth=0；
- orphan locks=0、unsafe=0；
- receipt completed；
- index clean；
- 新进程复核与当前进程一致；
- readiness 只保留显式 `keep-active` 或本轮非目标 warning。

- [ ] **Step 8：记录治理 artifact**

通过 `write-memory.sh --type artifact` 只记录：

- GitHub commit；
- 两个 manifest digest；
- archive/supersede/keep-active/action counts；
- pending receipt batch digest；
- 最终 index/readiness 状态。

不把 manifests、record IDs、item IDs、正文或 audit line content 写进 memory artifact。
