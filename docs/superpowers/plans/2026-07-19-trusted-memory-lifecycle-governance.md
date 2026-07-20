# 可信记忆生命周期治理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> 执行状态（2026-07-20）：本地实现与 synthetic fixture 验证完成；整体 release、branch
> protection required context 和真实 brain dry-run 均保持 `PENDING`。Task 10 Step 4–8 未执行；
> 未 merge、未 push、未读取或修改真实 brain。历史 TDD 步骤没有逐项重放证据，因此保持未勾选。

**Goal:** 修复 supersession 图与 Markdown 的事实分裂，把 Pending Queue 升级为保留原时间、稳定 ID、默认预览且可审计回放的 v2，并让 CLI、MCP、Web、doctor、readiness 共享同一生命周期合同。

**Architecture:** 保持 `items/*.md` 为长期知识权威，不新增 MemoryItem 状态字段。新增独立的 supersession transaction、candidate generator 和低敏 ledger；pending 使用向后兼容的 v2 envelope 与稳定 replay identity。SQLite、图谱、readiness 和 Web 都是对 Markdown、pending files 与 ledger 的派生视图，失败时必须显式降级或阻断。

**Tech Stack:** Python 3.11/3.12、Pydantic v2、Typer、FastAPI、SQLite/FTS、pytest、GitHub Actions、Markdown frontmatter。

---

## 实施边界与任务顺序

本规格包含两个紧密关联、但可独立验收的工作包：

1. Task 1–5：统一 supersession 与 lifecycle review；
2. Task 6–8：Pending Queue v2 与运行健康；
3. Task 9–10：发布证据、真实 dry-run 迁移和直推 `main`。

不得跳过 Task 1–3 直接批量 supersede，也不得跳过 Task 6–7 直接运行当前会修改数据的
`memory sync-pending`。真实 brain mutation 只能发生在 Task 10 的书面 dry-run 结果经用户确认之后。

## 文件地图

### 新增文件

- `agent_brain/memory/governance/supersession.py`：supersession/revert 的验证、预览、应用与 reason code。
- `agent_brain/memory/governance/lifecycle_ledger.py`：不含正文的 append-only lifecycle action ledger。
- `agent_brain/memory/governance/lifecycle_candidates.py`：确定性 replacement 候选评分。
- `tests/unit/test_supersession.py`：事务、回滚、幂等和边界测试。
- `tests/unit/test_lifecycle_candidates.py`：候选排序和隐私测试。
- `tests/fixtures/lifecycle_governance_evidence_v1.json`：公开、安全、确定性的治理证据夹具。
- `scripts/generate-lifecycle-governance-report.py`：生成或校验 committed 报告。
- `docs/evaluation/lifecycle-governance-readiness.json`：机器可读发布证据。
- `docs/evaluation/lifecycle-governance-readiness.zh.md`：人类可读边界说明。

### 修改文件

- `agent_brain/memory/store/items_store.py`：frontmatter 原子更新。
- `agent_brain/platform/indexing/index_writer.py`：从 `superseded_by` 重建 `supersedes` 图边。
- `agent_brain/platform/indexing/graph_index.py`、`index.py`：按 relation 精确移除图边。
- `agent_brain/interfaces/mcp/tools/graph.py`：`relation=supersedes` 路由到共享事务。
- `agent_brain/memory/governance/lifecycle_review.py`：action-object preview/apply 和兼容 wrapper。
- `agent_brain/memory/governance/maintenance_plan.py`：候选、review evidence 与 reason 输出。
- `agent_brain/interfaces/cli/commands/subapps.py`：lifecycle action CLI。
- `web/api/routes/governance.py`：统一 lifecycle request schema。
- `agent_brain/memory/store/pending.py`：v2 envelope、分类、稳定 identity、apply。
- `agent_brain/interfaces/cli/commands/maintenance.py`：`sync-pending` 默认 preview。
- `agent_brain/interfaces/cli/doctor_offline.py`：安全 next action 与分类健康度。
- `agent_brain/product/governance_readiness.py`：生命周期、pending、graph drift 指标。
- `tests/unit/test_pending_queue.py`、`test_cli_auto_governance.py`、`test_web_api.py`、
  `test_governance_readiness.py`、`test_link_unlink.py`、`test_search_filter.py`：回归合同。
- `.github/workflows/governance-gates.yml`、`tests/unit/test_ci_governance_contract.py`：required gate。
- `CHANGELOG.md`、Stage 1 plan 顶部状态说明和 trusted lifecycle 设计状态。

### 明确不修改

- MemoryItem schema 与 `MemoryType` 枚举；legacy pending `feedback` 走 `unsupported_type`，不扩充长期知识类型。
- 召回打分、semantic provider、Qoder 客户端验证和 npm packaging。

---

### Task 1: 冻结 supersession 类型、reason code 与只读验证

**Files:**
- Create: `agent_brain/memory/governance/supersession.py`
- Test: `tests/unit/test_supersession.py`

- [ ] **Step 1: 写失败测试，冻结方向和拒绝边界**

```python
from datetime import datetime, timezone

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.memory.governance.supersession import SupersessionService
from agent_brain.memory.store.items_store import ItemsStore


def _item(item_id: str, *, project: str = "agent-memory-hub", tenant: str | None = None):
    return MemoryItem(
        id=item_id,
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc),
        title=item_id,
        summary=f"summary {item_id}",
        project=project,
        tenant_id=tenant,
        tags=["lifecycle"],
    )


def test_preview_accepts_replacement_supersedes_obsolete(tmp_brain_dir):
    store = ItemsStore(tmp_brain_dir / "items")
    old = _item("mem-20260719-100000-old-signal")
    new = _item("mem-20260719-110000-new-signal")
    store.write(old, "old")
    store.write(new, "new")

    result = SupersessionService(tmp_brain_dir, store).preview(
        replacement_id=new.id,
        obsolete_id=old.id,
    )

    assert result.status == "ready"
    assert result.reason == "OK"
    assert result.replacement_id == new.id
    assert result.obsolete_id == old.id


def test_preview_rejects_self_cycle_cross_tenant_and_cross_project(tmp_brain_dir):
    store = ItemsStore(tmp_brain_dir / "items")
    left = _item("mem-20260719-100000-left", tenant="tenant-a")
    right = _item("mem-20260719-110000-right", tenant="tenant-b")
    other = _item("mem-20260719-120000-other", project="other", tenant="tenant-a")
    for item in (left, right, other):
        store.write(item, item.title)

    service = SupersessionService(tmp_brain_dir, store)
    assert service.preview(left.id, left.id).reason == "SELF_SUPERSESSION"
    assert service.preview(right.id, left.id).reason == "TENANT_MISMATCH"
    assert service.preview(other.id, left.id).reason == "PROJECT_MISMATCH"
```

- [ ] **Step 2: 运行测试并确认红灯**

Run:

```bash
python -m pytest tests/unit/test_supersession.py -q
```

Expected: collection fails with `ModuleNotFoundError: agent_brain.memory.governance.supersession`.

- [ ] **Step 3: 实现最小只读类型和验证器**

在 `supersession.py` 定义以下公开合同：

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from agent_brain.contracts.memory_item import MemoryItem, Sensitivity
from agent_brain.memory.store.items_store import ItemsStore

SupersessionStatus = Literal["ready", "blocked", "already_applied"]

SENSITIVITY_RANK = {
    Sensitivity.public.value: 0,
    Sensitivity.internal.value: 1,
    Sensitivity.private.value: 2,
    Sensitivity.secret.value: 3,
}


@dataclass(frozen=True)
class SupersessionResult:
    status: SupersessionStatus
    reason: str
    replacement_id: str
    obsolete_id: str
    dry_run: bool = True
    snapshot: str | None = None
    index_repair_required: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class SupersessionService:
    def __init__(self, brain_dir: Path, store: ItemsStore, index=None) -> None:
        self.brain_dir = Path(brain_dir)
        self.store = store
        self.index = index

    def preview(self, replacement_id: str, obsolete_id: str) -> SupersessionResult:
        if replacement_id == obsolete_id:
            return self._blocked(replacement_id, obsolete_id, "SELF_SUPERSESSION")
        try:
            replacement, _ = self.store.get(replacement_id)
            obsolete, _ = self.store.get(obsolete_id)
        except FileNotFoundError:
            return self._blocked(replacement_id, obsolete_id, "ITEM_MISSING")
        reason = self._validate_pair(replacement, obsolete)
        if reason != "OK":
            return self._blocked(replacement_id, obsolete_id, reason)
        if obsolete.superseded_by == replacement.id:
            return SupersessionResult(
                "already_applied", "ALREADY_APPLIED", replacement.id, obsolete.id
            )
        if obsolete.superseded_by:
            return self._blocked(replacement_id, obsolete_id, "OBSOLETE_ALREADY_SUPERSEDED")
        return SupersessionResult("ready", "OK", replacement.id, obsolete.id)

    def _validate_pair(self, replacement: MemoryItem, obsolete: MemoryItem) -> str:
        if replacement.tenant_id != obsolete.tenant_id:
            return "TENANT_MISMATCH"
        if replacement.project != obsolete.project:
            return "PROJECT_MISMATCH"
        if "needs-review" in replacement.tags:
            return "REPLACEMENT_REQUIRES_REVIEW"
        if SENSITIVITY_RANK[str(replacement.sensitivity)] > SENSITIVITY_RANK[str(obsolete.sensitivity)]:
            return "VISIBILITY_REDUCTION"
        cursor = replacement
        seen = {obsolete.id}
        while cursor.superseded_by:
            if cursor.superseded_by in seen:
                return "SUPERSESSION_CYCLE"
            seen.add(cursor.superseded_by)
            try:
                cursor, _ = self.store.get(cursor.superseded_by)
            except FileNotFoundError:
                return "BROKEN_REPLACEMENT_CHAIN"
        return "OK"

    @staticmethod
    def _blocked(replacement_id: str, obsolete_id: str, reason: str) -> SupersessionResult:
        return SupersessionResult("blocked", reason, replacement_id, obsolete_id)
```

- [ ] **Step 4: 扩充 cycle、missing、needs-review、visibility 测试并跑绿**

Run:

```bash
python -m pytest tests/unit/test_supersession.py -q
```

Expected: all tests PASS，且没有真实 brain 写入。

- [ ] **Step 5: 提交只读合同**

```bash
git add agent_brain/memory/governance/supersession.py tests/unit/test_supersession.py
git commit -m "feat: define governed supersession contract"
```

---

### Task 2: 实现原子 supersession、revert 与低敏 ledger

**Files:**
- Create: `agent_brain/memory/governance/lifecycle_ledger.py`
- Modify: `agent_brain/memory/governance/supersession.py`
- Modify: `agent_brain/memory/store/items_store.py:95-145`
- Test: `tests/unit/test_supersession.py`

- [ ] **Step 1: 写失败测试，冻结 apply、幂等、回滚和 ledger**

新增测试，断言：

```python
def _seed_pair(brain_dir):
    store = ItemsStore(brain_dir / "items")
    old = _item("mem-20260719-100000-transaction-old")
    new = _item("mem-20260719-110000-transaction-new")
    store.write(old, "old body")
    store.write(new, "new body")
    return store, old, new


def test_apply_updates_both_markdown_items_and_writes_private_safe_ledger(tmp_brain_dir):
    store, old, new = _seed_pair(tmp_brain_dir)
    result = SupersessionService(tmp_brain_dir, store).apply(new.id, old.id, apply=True)
    old_after, _ = store.get(old.id)
    new_after, _ = store.get(new.id)
    assert result.status == "applied"
    assert old_after.superseded_by == new.id
    assert old.id in new_after.refs.mems
    ledger = (tmp_brain_dir / "runtime" / "lifecycle-actions.jsonl").read_text()
    assert old.id in ledger and new.id in ledger
    assert "old body" not in ledger and "new body" not in ledger


def test_apply_is_idempotent_and_revert_restores_only_transaction_added_ref(tmp_brain_dir):
    store, old, new = _seed_pair(tmp_brain_dir)
    service = SupersessionService(tmp_brain_dir, store)
    first = service.apply(new.id, old.id, apply=True)
    second = service.apply(new.id, old.id, apply=True)
    reverted = service.revert(new.id, old.id, apply=True)
    assert first.status == "applied"
    assert second.status == "already_applied"
    assert reverted.status == "reverted"
    assert store.get(old.id)[0].superseded_by is None
    assert old.id not in store.get(new.id)[0].refs.mems
```

再用 monkeypatch 让第二次 Markdown 写失败，断言第一个文件恢复原字节、ledger 记录
`MARKDOWN_UPDATE_FAILED`、没有半条 supersession。

- [ ] **Step 2: 运行失败测试**

```bash
python -m pytest tests/unit/test_supersession.py -q
```

Expected: FAIL，因为 `apply`、`revert` 和 ledger 尚不存在。

- [ ] **Step 3: 把 ItemsStore frontmatter 更新改为原子替换**

在 `items_store.py` 增加：

```python
import os
import tempfile


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))
```

让 `ItemsStore.update_frontmatter()` 最后一行调用：

```python
_atomic_write_text(md_path, render_item_markdown(updated_item, body))
```

并增加 `restore_raw(item_id, data)`，校验 active item 路径后调用 `_atomic_write_bytes()`，只供
事务异常回滚使用。

- [ ] **Step 4: 实现低敏 ledger**

`lifecycle_ledger.py` 使用 append + fsync，固定字段为：

```python
@dataclass(frozen=True)
class LifecycleLedgerRecord:
    action: str
    timestamp: str
    status: str
    reason: str
    obsolete_id: str
    replacement_id: str | None
    snapshot: str | None
    replacement_ref_preexisted: bool
```

`append_lifecycle_record(brain_dir, record)` 只序列化上述字段，文件权限设为 `0600`。

- [ ] **Step 5: 实现 apply/revert 的预览与事务顺序**

在 `SupersessionService` 增加 `apply()` 与 `revert()`：

```python
def apply(self, replacement_id: str, obsolete_id: str, *, apply: bool = False):
    preview = self.preview(replacement_id, obsolete_id)
    if not apply or preview.status != "ready":
        return preview
    replacement, replacement_body = self.store.get(replacement_id)
    obsolete, obsolete_body = self.store.get(obsolete_id)
    replacement_ref_preexisted = obsolete.id in replacement.refs.mems
    snapshot = BrainHistory(self.brain_dir).snapshot(
        f"pre-supersession {replacement.id} -> {obsolete.id}"
    )
    old_bytes = (self.store.items_dir / f"{obsolete.id}.md").read_bytes()
    new_bytes = (self.store.items_dir / f"{replacement.id}.md").read_bytes()
    try:
        self.store.update_frontmatter(obsolete.id, superseded_by=replacement.id)
        if not replacement_ref_preexisted:
            self.store.link_mem(replacement.id, obsolete.id)
    except Exception:
        self.store.restore_raw(obsolete.id, old_bytes)
        self.store.restore_raw(replacement.id, new_bytes)
        self._record("supersede", "blocked", "MARKDOWN_UPDATE_FAILED", obsolete.id,
                     replacement.id, snapshot, replacement_ref_preexisted)
        return SupersessionResult("blocked", "MARKDOWN_UPDATE_FAILED",
                                  replacement.id, obsolete.id, dry_run=False, snapshot=snapshot)
    index_repair_required = not self._sync_index(replacement.id, obsolete.id)
    self._record("supersede", "applied", "OK", obsolete.id, replacement.id,
                 snapshot, replacement_ref_preexisted)
    return SupersessionResult("applied", "OK", replacement.id, obsolete.id,
                              dry_run=False, snapshot=snapshot,
                              index_repair_required=index_repair_required)
```

将 `SupersessionStatus` 扩展为 `applied`、`reverted`；`revert()` 先验证 old 当前精确指向 new，
再清空 `superseded_by`。只有 ledger 最近匹配事务记录表明 `replacement_ref_preexisted=false`
时才调用 `unlink_mem()`。

- [ ] **Step 6: 跑事务与 store 回归**

```bash
python -m pytest tests/unit/test_supersession.py tests/unit/test_write_service.py tests/unit/test_link_unlink.py -q
```

Expected: PASS；异常注入测试证明没有半写 Markdown。

- [ ] **Step 7: 提交事务实现**

```bash
git add agent_brain/memory/governance/supersession.py \
  agent_brain/memory/governance/lifecycle_ledger.py \
  agent_brain/memory/store/items_store.py tests/unit/test_supersession.py
git commit -m "feat: apply supersession as an auditable transaction"
```

---

### Task 3: 收敛 MCP graph、reindex 与召回事实

**Files:**
- Modify: `agent_brain/interfaces/mcp/tools/graph.py:40-110`
- Modify: `agent_brain/platform/indexing/index_writer.py:88-105`
- Modify: `agent_brain/platform/indexing/graph_index.py:37-55`
- Modify: `agent_brain/platform/indexing/index.py:333-341`
- Test: `tests/unit/test_link_unlink.py`
- Test: `tests/unit/test_search_filter.py`

- [ ] **Step 1: 写失败测试，复现当前 graph/frontmatter split-brain**

```python
from agent_brain.contracts.memory_item import Refs
from agent_brain.interfaces.mcp.tools._shared import _components_cache
from agent_brain.interfaces.mcp.tools.graph import link_memories
from agent_brain.interfaces.cli.commands.index_maintenance import reindex_store
from agent_brain.platform.embedding import HashingEmbedder


def seed_graph_pair(brain_dir):
    old = _item("mcp-old")
    new = _item("mcp-new")
    idx = _seed(brain_dir, [(old, "old"), (new, "new")])
    idx.close()
    return old, new


def seed_superseded_pair(brain_dir):
    old = _item("reindex-old")
    new = _item("reindex-new")
    old = old.model_copy(update={"superseded_by": new.id})
    new = new.model_copy(update={"refs": Refs(mems=[old.id])})
    idx = _seed(brain_dir, [(old, "old"), (new, "new")])
    return ItemsStore(brain_dir / "items"), idx, old, new


def test_mcp_supersedes_updates_obsolete_frontmatter(tmp_brain_dir, monkeypatch):
    old, new = seed_graph_pair(tmp_brain_dir)
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    _components_cache.clear()
    result = link_memories(new.id, old.id, relation="supersedes")
    store = ItemsStore(tmp_brain_dir / "items")
    assert result["linked"] is True
    assert store.get(old.id)[0].superseded_by == new.id


def test_reindex_rebuilds_supersedes_relation_from_frontmatter(tmp_brain_dir):
    store, idx, old, new = seed_superseded_pair(tmp_brain_dir)
    reindex_store(store, idx, HashingEmbedder(dim=8), prune=True)
    assert (new.id, old.id, "supersedes") in idx.get_refs(new.id)
```

- [ ] **Step 2: 运行红灯**

```bash
python -m pytest tests/unit/test_link_unlink.py tests/unit/test_search_filter.py -q
```

Expected: MCP 测试看到 `old.superseded_by is None`，reindex 只生成普通 `refs`。

- [ ] **Step 3: 把 MCP supersedes 路由到 SupersessionService**

在 `link_memories()` 中加入精确分支：

```python
if relation == "supersedes":
    store, idx, _ = _components()
    result = SupersessionService(_brain_dir(), store, idx).apply(
        replacement_id=source_id,
        obsolete_id=target_id,
        apply=True,
    )
    return {
        "source": source_id,
        "target": target_id,
        "relation": relation,
        "linked": result.status in {"applied", "already_applied"},
        "status": result.status,
        "reason": result.reason,
    }
```

普通关系继续使用当前 `idx.add_ref()` + `store.link_mem()`。

- [ ] **Step 4: 让 IndexWriter 双向推导 supersedes relation**

替换 refs 写入循环：

```python
for target_id in item.refs.mems:
    row = conn.execute(
        "SELECT superseded_by FROM items_meta WHERE id = ?",
        (target_id,),
    ).fetchone()
    relation = "supersedes" if row and row[0] == item.id else "refs"
    conn.execute(
        "INSERT OR IGNORE INTO refs_graph (source_id, target_id, relation) VALUES (?, ?, ?)",
        (item.id, target_id, relation),
    )
if item.superseded_by:
    conn.execute(
        "INSERT OR IGNORE INTO refs_graph (source_id, target_id, relation) VALUES (?, ?, 'supersedes')",
        (item.superseded_by, item.id),
    )
```

- [ ] **Step 5: 增加 relation-aware remove 并锁定 revert 语义**

将 `GraphIndex.remove_ref()`、`HubIndex.remove_ref()` 增加可选 `relation`。提供 relation 时 SQL
必须包含 `AND relation = ?`；未提供时保持现有兼容行为。generic `unlink_memories()` 检测到
supersedes edge 时返回 `SUPERSESSION_REVERT_REQUIRED`，不得直接删除。

- [ ] **Step 6: 跑图谱、reindex、Gateway 回归**

```bash
python -m pytest tests/unit/test_link_unlink.py tests/unit/test_knowledge_graph.py \
  tests/unit/test_search_filter.py tests/unit/test_injection_gateway.py \
  tests/unit/test_mcp_injection_gateway.py -q
```

Expected: PASS；superseded item 默认不可注入，audit 查询仍可显式读取。

- [ ] **Step 7: 提交图谱收敛**

```bash
git add agent_brain/interfaces/mcp/tools/graph.py \
  agent_brain/platform/indexing/index_writer.py \
  agent_brain/platform/indexing/graph_index.py agent_brain/platform/indexing/index.py \
  tests/unit/test_link_unlink.py tests/unit/test_search_filter.py
git commit -m "fix: converge supersession graph and markdown truth"
```

---

### Task 4: 生成可解释 replacement 候选

**Files:**
- Create: `agent_brain/memory/governance/lifecycle_candidates.py`
- Modify: `agent_brain/memory/governance/maintenance_plan.py:59-300`
- Test: `tests/unit/test_lifecycle_candidates.py`
- Test: `tests/unit/test_maintenance_plan.py`

- [ ] **Step 1: 写失败测试，冻结候选证据与 top-3 边界**

```python
from datetime import datetime, timedelta, timezone

from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Refs
from agent_brain.memory.governance.lifecycle_candidates import rank_supersession_candidates


def _candidate(item_id, *, project="agent-memory-hub", tenant=None, days=0,
               tags=None, refs=None, summary="lifecycle update"):
    return MemoryItem(
        id=item_id,
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc) + timedelta(days=days),
        title=item_id,
        summary=summary,
        project=project,
        tenant_id=tenant,
        tags=tags or ["lifecycle"],
        refs=refs or Refs(),
    )


def test_existing_supersedes_edge_is_highest_confidence_candidate():
    old = _candidate("mem-20260719-100000-candidate-old")
    newer = _candidate("mem-20260720-100000-candidate-new", days=1)
    unrelated = _candidate("mem-20260720-110000-candidate-unrelated", days=1,
                           tags=["other"], summary="unrelated")
    candidates = rank_supersession_candidates(
        obsolete=old,
        items=[newer, unrelated],
        supersedes_edges={(newer.id, old.id)},
    )
    assert candidates[0].replacement_id == newer.id
    assert candidates[0].evidence_codes[0] == "EXPLICIT_SUPERSEDES_EDGE"
    assert candidates[0].score == 1.0


def test_candidates_never_cross_project_or_tenant_and_return_at_most_three():
    old = _candidate("mem-20260719-100000-boundary-old", tenant="tenant-a")
    same_project_a = _candidate("mem-20260720-100000-boundary-a", tenant="tenant-a", days=1)
    same_project_b = _candidate("mem-20260720-100001-boundary-b", tenant="tenant-a", days=1)
    same_project_c = _candidate("mem-20260720-100002-boundary-c", tenant="tenant-a", days=1)
    same_project_d = _candidate("mem-20260720-100003-boundary-d", tenant="tenant-a", days=1)
    cross_project = _candidate("mem-20260720-100004-boundary-project", project="other",
                               tenant="tenant-a", days=1)
    cross_tenant = _candidate("mem-20260720-100005-boundary-tenant", tenant="tenant-b", days=1)
    candidates = rank_supersession_candidates(
        obsolete=old,
        items=[same_project_a, same_project_b, same_project_c, same_project_d,
               cross_project, cross_tenant],
        supersedes_edges=set(),
    )
    assert len(candidates) == 3
    assert cross_project.id not in {row.replacement_id for row in candidates}
    assert cross_tenant.id not in {row.replacement_id for row in candidates}
```

- [ ] **Step 2: 运行红灯**

```bash
python -m pytest tests/unit/test_lifecycle_candidates.py tests/unit/test_maintenance_plan.py -q
```

Expected: FAIL with missing `lifecycle_candidates` module and missing candidate fields.

- [ ] **Step 3: 实现纯函数候选评分**

定义：

```python
@dataclass(frozen=True)
class SupersessionCandidate:
    replacement_id: str
    score: float
    evidence_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "replacement_id": self.replacement_id,
            "score": self.score,
            "evidence_codes": list(self.evidence_codes),
        }
```

评分固定为：explicit edge `1.0`、explicit `refs.mems` `+0.45`、refs commit/file/resource
交集 `+0.25`、tag/title token overlap `+0.20`、summary/locator 关闭词 `+0.10`、newer `+0.05`。
候选必须更晚、同 tenant/project、type 兼容、未 superseded、非 needs-review。最终分数 cap 1.0，
按 `(-score, -created_at, id)` 排序并返回前三条。

- [ ] **Step 4: 把候选加入 lifecycle review_queue**

给 `MaintenanceReviewQueueItem` 增加：

```python
candidates: list[dict[str, object]] = field(default_factory=list)
reviewed_at: str | None = None
```

`_build_review_queue()` 通过传入的 item map 和 graph edges 调用纯函数；没有候选时仍推荐
`archive_after_review`，有候选时推荐 `select_supersession_or_keep_active`。

- [ ] **Step 5: 跑候选、计划和隐私测试**

```bash
python -m pytest tests/unit/test_lifecycle_candidates.py tests/unit/test_maintenance_plan.py \
  tests/conformance/test_public_hygiene.py -q
```

Expected: PASS；候选 JSON 只含 id、score、evidence codes，不含 body/summary。

- [ ] **Step 6: 提交候选生成器**

```bash
git add agent_brain/memory/governance/lifecycle_candidates.py \
  agent_brain/memory/governance/maintenance_plan.py \
  tests/unit/test_lifecycle_candidates.py tests/unit/test_maintenance_plan.py
git commit -m "feat: rank deterministic supersession candidates"
```

---

### Task 5: 统一 lifecycle action 的 CLI 与 Web 合同

**Files:**
- Modify: `agent_brain/memory/governance/lifecycle_review.py`
- Modify: `agent_brain/interfaces/cli/commands/subapps.py:368-445`
- Modify: `web/api/routes/governance.py:90-155`
- Test: `tests/unit/test_cli_auto_governance.py`
- Test: `tests/unit/test_web_api.py`

- [ ] **Step 1: 写失败测试，冻结 action-object 和默认 preview**

CLI 测试使用：

```bash
memory govern apply-lifecycle --supersede OLD:NEW --format json
memory govern apply-lifecycle --archive OLD --format json
memory govern apply-lifecycle --keep-active OLD --format json
```

断言没有 `--apply` 时三个命令都不改文件。Web 测试 POST：

```json
{
  "actions": [
    {"action": "supersede", "item_id": "OLD", "replacement_id": "NEW"},
    {"action": "archive", "item_id": "OTHER"}
  ],
  "apply": false,
  "index_repair": true
}
```

断言返回逐项 `status/reason/dry_run`，旧 `{"item_ids": [...]}` 仍按 archive preview 兼容。

- [ ] **Step 2: 运行红灯**

```bash
python -m pytest tests/unit/test_cli_auto_governance.py \
  tests/unit/test_web_api.py::TestLifecycleGovernanceAPI -q
```

Expected: 新参数和 `actions` schema 不存在。

- [ ] **Step 3: 定义 action 类型和共享执行函数**

在 `lifecycle_review.py` 定义：

```python
LifecycleActionName = Literal[
    "supersede", "archive", "keep-active", "defer", "revert-supersession"
]


@dataclass(frozen=True)
class LifecycleReviewAction:
    action: LifecycleActionName
    item_id: str
    replacement_id: str | None = None
    defer_days: int | None = None
```

新增 `apply_lifecycle_review_actions()`；archive 复用当前 queue membership 检查，supersede/revert
复用 `SupersessionService`，keep-active 原子更新 `validity.observed_at`，defer 只写 ledger 的
`deferred_until`。保留 `apply_lifecycle_review_items()` 作为 archive-only wrapper。

- [ ] **Step 4: 扩展 CLI 参数并保持旧位置参数兼容**

增加 repeatable `--archive`、`--supersede OLD:NEW`、`--keep-active`、`--defer ID:DAYS`、
`--revert-supersession OLD:NEW`。旧位置参数继续转成 archive action；同一 item 出现冲突 action
时返回 exit 2 和 `CONFLICTING_ACTIONS`。

- [ ] **Step 5: 扩展 Pydantic request schema**

```python
class LifecycleActionRequest(BaseModel):
    action: Literal["supersede", "archive", "keep-active", "defer", "revert-supersession"]
    item_id: str
    replacement_id: str | None = None
    defer_days: int | None = None


class LifecycleApplyRequest(BaseModel):
    actions: list[LifecycleActionRequest] = Field(default_factory=list)
    item_ids: list[str] = Field(default_factory=list)
    apply: bool = False
    index_repair: bool = True
```

Pydantic validator 要求 supersede/revert 必有 replacement，defer_days 在 1–365，actions 与 legacy
item_ids 至少一项非空。

- [ ] **Step 6: 跑 CLI/Web/MCP 相关回归**

```bash
python -m pytest tests/unit/test_cli_auto_governance.py \
  tests/unit/test_web_api.py::TestLifecycleGovernanceAPI \
  tests/unit/test_governance_mcp.py tests/conformance/test_web_surface_lock.py -q
```

Expected: PASS，现有 endpoint 不变，默认仍为 dry-run。

- [ ] **Step 7: 提交统一 action 合同**

```bash
git add agent_brain/memory/governance/lifecycle_review.py \
  agent_brain/interfaces/cli/commands/subapps.py web/api/routes/governance.py \
  tests/unit/test_cli_auto_governance.py tests/unit/test_web_api.py
git commit -m "feat: unify lifecycle review actions across surfaces"
```

---

### Task 6: 实现 Pending Queue v2 envelope 与只读分类

**Files:**
- Modify: `agent_brain/memory/store/pending.py`
- Test: `tests/unit/test_pending_queue.py`

- [ ] **Step 1: 写失败测试，覆盖 v1/v2、时间、hash 和 legacy feedback**

```python
def v2_fact_record():
    return {
        "v": 2,
        "op": "write",
        "origin": "hook",
        "record_id": "pending-test-fact-0001",
        "enqueued_at": "2026-07-01T10:00:00+00:00",
        "original_created_at": "2026-07-01T10:00:00+00:00",
        "item": {
            "type": "fact",
            "title": "queued fact",
            "summary": "queued fact summary",
            "body": "queued fact body",
            "tags": ["pending"],
            "sensitivity": "internal",
            "confidence": 0.7,
        },
    }


def legacy_feedback_record():
    return {
        "v": 1,
        "op": "write",
        "origin": "hook",
        "ts": "2026-07-01T11:00:00+00:00",
        "item": {
            "type": "feedback",
            "title": "legacy feedback",
            "summary": "legacy feedback summary",
            "body": "legacy feedback body",
            "tags": ["pending"],
            "sensitivity": "internal",
        },
    }


def test_v2_preview_preserves_original_time_and_stable_identity(tmp_brain, monkeypatch):
    path = enqueue_write_record(v2_fact_record())
    first = PendingQueue().preview(limit=10).records[0]
    second = PendingQueue().preview(limit=10).records[0]
    assert path.exists()
    assert first.record_id == second.record_id
    assert first.payload_sha256 == second.payload_sha256
    assert first.original_created_at == "2026-07-01T10:00:00+00:00"
    assert first.classification == "ready"


def test_legacy_feedback_is_unsupported_not_malformed(tmp_brain):
    enqueue_write_record(legacy_feedback_record())
    record = PendingQueue().preview(limit=10).records[0]
    assert record.malformed is False
    assert record.classification == "unsupported_type"
    assert record.reason == "UNSUPPORTED_MEMORY_TYPE"
```

再覆盖 30 天以上 signal/handoff 为 `stale_requires_review`，损坏 JSON 为 `malformed`。

- [ ] **Step 2: 运行红灯**

```bash
python -m pytest tests/unit/test_pending_queue.py -q
```

Expected: preview dataclass 缺少 v2 字段和 classification。

- [ ] **Step 3: 增加规范化 payload hash 与 legacy identity**

在 `pending.py` 增加：

```python
def _canonical_payload_sha256(item: dict[str, object]) -> str:
    payload = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _legacy_record_id(path: Path, record: dict[str, object]) -> str:
    seed = f"{path.name}\n{json.dumps(record, ensure_ascii=False, sort_keys=True)}"
    return "pending-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
```

`enqueue_write_record()` 默认写入 `v=2`、UUID record_id、enqueued_at、original_created_at、
payload_sha256；调用者显式传入 v1 时保持原字节合同用于测试。

- [ ] **Step 4: 实现封闭分类枚举和 preview 字段**

```python
PendingClassification = Literal[
    "ready", "already_written", "stale_requires_review", "duplicate_candidate",
    "conflict", "unsupported_type", "malformed", "audit_blocked"
]
```

`PendingRecordPreview` 增加 record_id、enqueued_at、original_created_at、age_seconds、
payload_sha256、classification、reason。type 不在 `MemoryType` 时返回 unsupported；signal/handoff
使用 original time 与 30 天窗口分类。preview 不修改 legacy 文件。

- [ ] **Step 5: 跑 pending 只读测试**

```bash
python -m pytest tests/unit/test_pending_queue.py -q
```

Expected: PASS；重复 preview 字节级不改变 pending 目录。

- [ ] **Step 6: 提交 v2 envelope**

```bash
git add agent_brain/memory/store/pending.py tests/unit/test_pending_queue.py
git commit -m "feat: classify pending writes with stable v2 envelopes"
```

---

### Task 7: 实现 exactly-once pending apply 与默认预览 CLI

**Files:**
- Modify: `agent_brain/memory/store/pending.py`
- Modify: `agent_brain/interfaces/cli/commands/maintenance.py:70-120`
- Modify: `agent_brain/interfaces/cli/commands/review.py:15-40`
- Modify: `agent_brain/interfaces/cli/doctor_offline.py:55-75`
- Test: `tests/unit/test_pending_queue.py`
- Test: `tests/unit/test_cli_smoke.py`

- [ ] **Step 1: 写失败测试，冻结稳定 item id 与 crash 恢复**

```python
def test_apply_preserves_original_created_at(tmp_brain):
    enqueue_write_record(v2_fact_record())
    result = PendingQueue().apply(safe_only=True)
    item, _ = next(ItemsStore(tmp_brain / "items").iter_all())
    assert result.written == 1
    assert item.created_at.isoformat() == "2026-07-01T10:00:00+00:00"


def test_crash_after_write_before_unlink_becomes_already_written(tmp_brain, monkeypatch):
    path = enqueue_write_record(v2_fact_record())
    record_id = PendingQueue().preview(limit=1).records[0].record_id
    original_unlink = Path.unlink
    failed = False

    def fail_once(self, *args, **kwargs):
        nonlocal failed
        if self == path and not failed:
            failed = True
            raise OSError("simulated unlink failure")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_once)
    first = PendingQueue().apply(record_ids=[record_id])
    second = PendingQueue().apply(record_ids=[record_id])
    assert first.failed == 1
    assert second.already_written == 1
    assert len(list(ItemsStore(tmp_brain / "items").iter_all())) == 1
```

CLI 测试断言 bare `memory sync-pending --format json` 只返回 preview，只有 `--apply` 才写。

- [ ] **Step 2: 运行红灯**

```bash
python -m pytest tests/unit/test_pending_queue.py tests/unit/test_cli_smoke.py -q
```

Expected: `PendingQueue.apply` 和 CLI `--apply` 不存在，bare command 会执行 replay。

- [ ] **Step 3: 实现稳定 pending item id**

```python
def _pending_item_id(title: str, original_created_at: datetime, record_id: str) -> str:
    slug = re.sub(r"[/\\]+", "-", "-".join(title.lower().split()))[:30].strip("-")
    stable = hashlib.sha256(record_id.encode("utf-8")).hexdigest()[:8]
    return f"mem-{original_created_at:%Y%m%d-%H%M%S}-{slug or 'pending'}-{stable}"
```

创建 MemoryItem 时使用 `Source(kind="pending-replay", span_hash=payload_sha256)`。若稳定 id 已存在，
相同 span hash 为 `already_written`；不同 hash 为 `conflict`。

- [ ] **Step 4: 实现选择性 apply**

新增 `PendingApplyStats`：written、already_written、review_required、skipped、failed、dead 和
逐条 results。`apply(record_ids=None, safe_only=False)` 只处理显式 record_ids；safe_only 只处理
classification=ready。stale、duplicate、unsupported、conflict、malformed、audit-blocked 都不写。

- [ ] **Step 5: 把 CLI 改为 preview-first**

`sync_pending()` 参数改为：

```python
apply: bool = typer.Option(False, "--apply", help="Apply explicitly selected safe records")
record_ids: list[str] = typer.Option([], "--record", help="Pending record id to apply")
safe_only: bool = typer.Option(False, "--safe-only", help="Apply all records classified ready")
dry_run: bool = typer.Option(False, "--dry-run", help="Compatibility alias for preview")
```

没有 `--apply` 时始终 preview；`--apply` 但既无 record 也无 safe-only 时 exit 2。doctor 和
review status 的 next action 改为 `memory sync-pending --format json`，不再给出会修改数据的命令。

- [ ] **Step 6: 跑 pending、CLI、doctor 回归**

```bash
python -m pytest tests/unit/test_pending_queue.py tests/unit/test_cli_smoke.py \
  tests/unit/test_doctor_offline.py tests/unit/test_cli_crud.py -q
```

Expected: PASS；所有测试使用隔离 `BRAIN_DIR`，不触碰真实 pending。

- [ ] **Step 7: 提交安全回放**

```bash
git add agent_brain/memory/store/pending.py \
  agent_brain/interfaces/cli/commands/maintenance.py \
  agent_brain/interfaces/cli/commands/review.py \
  agent_brain/interfaces/cli/doctor_offline.py \
  tests/unit/test_pending_queue.py tests/unit/test_cli_smoke.py
git commit -m "fix: make pending replay explicit and idempotent"
```

---

### Task 8: 把生命周期与 pending truth 纳入 readiness

**Files:**
- Modify: `agent_brain/product/governance_readiness.py`
- Modify: `agent_brain/interfaces/cli/doctor_offline.py`
- Modify: `tests/unit/test_governance_readiness.py`
- Test: `tests/unit/test_governance_readiness.py`

- [ ] **Step 1: 写失败测试，冻结 metrics 和严重度**

```python
from agent_brain.memory.store.pending import enqueue_write_record


def test_lifecycle_readiness_reports_pending_and_broken_supersession(tmp_path, monkeypatch):
    brain = tmp_path / "brain"
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    store = ItemsStore(brain / "items")
    broken = MemoryItem(
        id="mem-20260719-100000-broken-supersession",
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc) - timedelta(days=40),
        title="broken supersession",
        summary="broken supersession summary",
        tags=["lifecycle"],
        superseded_by="mem-20260719-110000-missing-target",
    )
    store.write(broken, "broken body")
    old_time = datetime.now(timezone.utc) - timedelta(days=8)
    enqueue_write_record({
        "v": 2,
        "op": "write",
        "origin": "hook",
        "record_id": "pending-readiness-0001",
        "enqueued_at": old_time.isoformat(),
        "original_created_at": old_time.isoformat(),
        "item": {
            "type": "fact",
            "title": "pending readiness fact",
            "summary": "pending readiness summary",
            "body": "pending readiness body",
            "tags": ["pending"],
            "sensitivity": "internal",
        },
    })
    result = runner.invoke(app, ["govern", "readiness", "--format", "json"])
    payload = json.loads(result.output)
    lane = next(row for row in payload["lanes"] if row["id"] == "memory_lifecycle")
    assert lane["status"] == "fail"
    assert lane["metrics"]["broken_superseded_count"] == 1
    assert lane["metrics"]["pending_total"] == 1
    assert lane["metrics"]["pending_oldest_age_seconds"] >= 7 * 86400
    assert lane["metrics"]["pending_classifications"]["ready"] == 1
```

再覆盖 graph edge 存在但 old frontmatter 为空时 `supersession_drift_count=1` 和 status fail。

- [ ] **Step 2: 运行红灯**

```bash
python -m pytest tests/unit/test_governance_readiness.py -q
```

Expected: metrics 缺失。

- [ ] **Step 3: 实现 lifecycle health 汇总**

`_memory_lifecycle_lane()` 使用 `validity.observed_at or created_at` 计算 stale；扫描 target ids 得到
broken chain；调用 `PendingQueue.preview(limit=queue.depth())` 聚合分类和 oldest age；只读打开
index.db 比较 supersedes graph 与 frontmatter。规则：broken/conflict/malformed/dead/drift 为 fail，
pending oldest >7 天为 fail，>24 小时为 warn，stale backlog 为 warn。

- [ ] **Step 4: 修正 doctor 展示**

offline doctor 的 pending 行显示 `ready/review/blocker` 分类计数和 oldest age；semantic
`not_fast_ready` 保持独立 degraded，不让 lifecycle PASS 冒充整体 semantic ready。

- [ ] **Step 5: 跑 readiness、doctor 和公开卫生**

```bash
python -m pytest tests/unit/test_governance_readiness.py tests/unit/test_doctor.py \
  tests/conformance/test_public_hygiene.py -q
```

Expected: PASS，JSON 中不包含 pending title/body/path，只含聚合和 reason codes。

- [ ] **Step 6: 提交 readiness truth**

```bash
git add agent_brain/product/governance_readiness.py \
  agent_brain/interfaces/cli/doctor_offline.py tests/unit/test_governance_readiness.py
git commit -m "feat: expose lifecycle truth in governance readiness"
```

---

### Task 9: 建立 committed lifecycle governance 发布证据

**Files:**
- Create: `tests/fixtures/lifecycle_governance_evidence_v1.json`
- Create: `scripts/generate-lifecycle-governance-report.py`
- Create: `docs/evaluation/lifecycle-governance-readiness.json`
- Create: `docs/evaluation/lifecycle-governance-readiness.zh.md`
- Modify: `.github/workflows/governance-gates.yml`
- Modify: `tests/unit/test_ci_governance_contract.py`
- Modify: `tests/unit/test_docs_truth_contract.py`
- Modify: `CHANGELOG.md`
- Modify: `docs/superpowers/plans/2026-07-19-stage1-reliability-security-release.md`

- [ ] **Step 1: 写失败的报告和 CI 合同测试**

要求 committed report 包含：schema、implementation hash、fixture hash、supersession contract、
pending contract、surface parity、privacy、failed_gates。CI 合同断言 required governance job 显式运行：

```bash
python -m pytest tests/unit/test_supersession.py tests/unit/test_lifecycle_candidates.py \
  tests/unit/test_pending_queue.py tests/unit/test_governance_readiness.py -q
python scripts/generate-lifecycle-governance-report.py --check
```

并禁止 `continue-on-error`。

- [ ] **Step 2: 运行红灯**

```bash
python -m pytest tests/unit/test_ci_governance_contract.py \
  tests/unit/test_docs_truth_contract.py -q
```

Expected: generator/report/workflow contract 缺失。

- [ ] **Step 3: 创建公开安全 fixture**

fixture 只使用 synthetic ids、项目名和封闭 reason，覆盖：valid supersession、cycle、cross tenant、
stale pending、already-written、unsupported feedback、malformed、graph drift。不得包含真实 HOME、
真实 pending title、prompt、transcript 或 secret。

- [ ] **Step 4: 实现生成器和 committed 报告**

生成器加载 fixture，通过公开纯函数生成 report；`--check` 比较规范化 JSON 与 committed file。
Markdown 报告明确区分“代码/fixture PASS”和“真实 brain 尚待 dry-run”。状态只有 failed_gates 为空
且所有合同为 pass 时才是 pass。

- [ ] **Step 5: 把报告检查加入 required governance job**

在 `adapter-governance` 相邻位置新增 `lifecycle-governance` step，不能设为 advisory。更新合同测试
要求四个定向模块和 generator 命令存在。

- [ ] **Step 6: 更新事实源文档**

CHANGELOG 记录 CLI 默认行为变化。Stage 1 plan 顶部增加：

```markdown
> 历史执行计划；当前完成状态以 `docs/evaluation/stage1-reliability-security-release-readiness.zh.md` 为准。
```

不要勾选无法从当前 commit 重放证明的历史步骤。

- [ ] **Step 7: 生成并验证报告**

```bash
python scripts/generate-lifecycle-governance-report.py
python scripts/generate-lifecycle-governance-report.py --check
python -m pytest tests/unit/test_ci_governance_contract.py \
  tests/unit/test_docs_truth_contract.py tests/conformance/test_public_hygiene.py -q
```

Expected: generator 输出 `lifecycle-governance: PASS`，pytest PASS。

- [ ] **Step 8: 提交发布证据**

```bash
git add tests/fixtures/lifecycle_governance_evidence_v1.json \
  scripts/generate-lifecycle-governance-report.py \
  docs/evaluation/lifecycle-governance-readiness.json \
  docs/evaluation/lifecycle-governance-readiness.zh.md \
  .github/workflows/governance-gates.yml tests/unit/test_ci_governance_contract.py \
  tests/unit/test_docs_truth_contract.py CHANGELOG.md \
  docs/superpowers/plans/2026-07-19-stage1-reliability-security-release.md
git commit -m "ci: require trusted lifecycle governance evidence"
```

---

### Task 10: 完整验证、直推发布与真实 dry-run 迁移

**Files:**
- Modify: `docs/superpowers/specs/2026-07-19-trusted-memory-lifecycle-governance-design.md`
- Modify: `docs/superpowers/plans/2026-07-19-trusted-memory-lifecycle-governance.md`
- Runtime artifact only: `/tmp/amh-lifecycle-readiness.json`
- Runtime artifact only: `/tmp/amh-pending-preview.json`

- [x] **Step 1: 跑聚焦测试**

```bash
python -m pytest tests/unit/test_supersession.py tests/unit/test_lifecycle_candidates.py \
  tests/unit/test_pending_queue.py tests/unit/test_maintenance_plan.py \
  tests/unit/test_cli_auto_governance.py tests/unit/test_governance_readiness.py \
  tests/unit/test_link_unlink.py tests/unit/test_search_filter.py -q
```

Expected: PASS。

2026-07-20 新鲜证据：退出码 0，`368 passed in 196.45s`。

- [x] **Step 2: 跑完整本地门禁**

```bash
python -m pytest tests/unit -q
python -m pytest tests/system -q
python -m pytest tests/conformance -q
bash agent_runtime_kit/hooks/test-hook.sh
python scripts/check-recall-quality.py
python scripts/generate-adapter-governance.py --check
python scripts/generate-lifecycle-governance-report.py --check
ruff check .
python scripts/check_mypy_baseline.py
git diff --check
```

Expected: 全部 PASS。使用项目支持的 Python 3.12 运行类型门禁，不得跳过或新增 baseline
fingerprint。最初计划中的 `agent_runtime_kit/tests/test_hooks.sh` 从未存在，实际执行入口以
`.github/workflows/hook-tests.yml` 为事实源，即 `agent_runtime_kit/hooks/test-hook.sh`；旧路径的
新鲜执行结果为退出码 127，没有创建兼容空壳掩盖该问题。

2026-07-20 新鲜证据（Python 3.12.13）：

- unit：退出码 0，`3432 passed, 2 skipped in 693.29s`；两项 skip 均要求显式读取真实
  `~/.agent-memory-hub`，本轮按真实 brain 禁读边界未启用；
- system：退出码 0，`33 passed in 30.76s`；
- conformance：退出码 0，`56 passed, 2 skipped in 15.31s`；两项 skip 同为真实 brain opt-in；
- Hook unit tests 权威脚本：退出码 0，`6 passed / 0 failed`；
- recall quality：退出码 0，`cases=37`；adapter governance：退出码 0，
  `PASS manifests=16 batches=2 privacy=PASS`；
- lifecycle governance：退出码 0，`synthetic=PASS release=PENDING real-brain-dry-run=PENDING`；
- Ruff：退出码 0；mypy baseline：退出码 0，`current=691 baseline=701 resolved=10`；
- `git diff --check`：退出码 0。

- [x] **Step 3: 标记规格与计划完成并提交**

规格状态改为“实现完成，待真实数据分批治理”；计划中仅勾选已经有命令证据的步骤。提交：

```bash
git add docs/superpowers/specs/2026-07-19-trusted-memory-lifecycle-governance-design.md \
  docs/superpowers/plans/2026-07-19-trusted-memory-lifecycle-governance.md
git commit -m "docs: record trusted lifecycle governance verification"
```

本步骤只确认规格状态、上述本地证据和提交本身；不把 Step 4–8、外部 required context 或真实
brain 治理写成已完成。

- [ ] **Step 4: 快进本地 main 并直推 GitHub**

在主 worktree 执行：

```bash
git fetch origin main
git merge --ff-only feat/trusted-memory-lifecycle-governance
git push origin main
```

Expected: `main` 与 `origin/main` 指向相同 SHA；不创建 PR。

- [ ] **Step 5: 等待 required GitHub workflows 全绿**

```bash
gh run list --branch main --commit "$(git rev-parse HEAD)" \
  --json workflowName,status,conclusion,url
```

Expected: python-tests、Hook unit tests、governance-gates 全部 completed/success；外部分发任务按其
独立边界展示，不冒充核心代码结果。

- [ ] **Step 6: 从稳定 main 生成真实 brain 的只读报告**

```bash
memory govern readiness --format json > /tmp/amh-lifecycle-readiness.json
memory sync-pending --format json > /tmp/amh-pending-preview.json
memory govern plan --category lifecycle --format json > /tmp/amh-lifecycle-plan.json
```

Expected: 命令不修改 `items/`、`pending/` 或 `index.db`；报告列出 45 条 legacy pending 的新分类、
现有 supersedes drift 和 stale review queue。

- [ ] **Step 7: 证明 dry-run 无副作用**

运行前后分别记录：

```bash
find "$HOME/.agent-memory-hub/items" "$HOME/.agent-memory-hub/pending" -type f \
  -exec stat -f '%N %m %z' {} + | shasum -a 256
sqlite3 "$HOME/.agent-memory-hub/index.db" 'PRAGMA data_version; SELECT COUNT(*) FROM refs_graph;'
```

Expected: 文件清单 hash、pending depth 和 graph count 不变。

- [ ] **Step 8: 停在真实 mutation 审批门禁**

向用户汇报以下聚合，不粘贴 private body/title：ready、already-written、stale-review、duplicate、
unsupported、conflict、malformed、audit-blocked 数量；supersession 候选批次；建议首批 action。
只有用户明确批准具体批次后，才运行 `--apply`。未获批准时本轮代码发布仍可完成，但真实 backlog
保持显式 blocker，不得写成已清零。

---

## 计划自审清单

- 规格 §5 生命周期状态：Task 4、5、8 覆盖。
- 规格 §6 supersession 事务：Task 1–3 覆盖。
- 规格 §7 确定性候选：Task 4 覆盖。
- 规格 §8 Pending Queue v2：Task 6–7 覆盖。
- 规格 §9 review action：Task 5 覆盖。
- 规格 §10 readiness：Task 8–9 覆盖。
- 规格 §11 安全隐私：Task 1、2、4、5、7、9 的失败和公开卫生测试覆盖。
- 规格 §14 发布迁移回滚：Task 2、5、10 覆盖。
- 真实 mutation 明确停在 Task 10 Step 8，符合 preview-first 与用户审批边界。
