# 索引运行真相与分治修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `memory verify` 以 Markdown、dirty marker 和 supersession graph 三维事实判定索引健康，并通过显式、分治、修复后二次验证的流程消除真实索引债务。

**Architecture:** 新增纯 `IndexHealthReport` 聚合层；CLI preview 与 readiness 复用 descriptor-relative items 扫描和外部 SQLite snapshot，严格只读。显式 repair 才打开 managed write connection，按 missing/active-dirty、orphan、supersession、retired marker 分治处理，关闭写连接后再次只读采集，只有最终 clean 才成功。

**Tech Stack:** Python 3.11+、dataclasses、Typer、SQLite/WAL external snapshot、descriptor-relative no-follow I/O、pytest、ruff、mypy baseline。

---

## 文件结构

- Create: `agent_brain/memory/governance/index_health.py` — 纯健康模型、三维比较、低敏 summary。
- Modify: `agent_brain/product/governance_readiness.py` — 外部 snapshot 同时读取 index ids 与 supersession edges，向 CLI 暴露只读 collector，并复用统一报告。
- Modify: `agent_brain/interfaces/cli/commands/index_maintenance.py` — 分治 repair orchestration、lazy embedder、修复结果。
- Modify: `agent_brain/interfaces/cli/commands/maintenance.py` — verify text/JSON 合同、退出码、before/repair/after 输出。
- Modify: `agent_brain/platform/indexing/graph_index.py` — 事务性替换 derived supersedes projection。
- Modify: `agent_brain/platform/indexing/index.py` — 暴露 supersession reconciliation facade。
- Create: `tests/unit/test_index_health.py` — 纯模型、分类、状态与隐私合同。
- Modify: `tests/unit/test_governance_readiness.py` — 只读 collector、单 snapshot、兼容 metrics。
- Modify: `tests/unit/test_reindex_prune.py` — CLI 假绿、JSON、repair 后置条件。
- Modify: `tests/unit/test_graph_prune.py` — graph transaction、marker 并发和分治 repair。
- Modify: `tests/unit/test_docs_truth_contract.py` — 文档与命令合同。
- Modify: `README.md`, `CHANGELOG.md`, `docs/storage-lifecycle.zh.md` — 运维入口与边界。

## Task 1：纯索引健康模型与低敏摘要

**Files:**
- Create: `agent_brain/memory/governance/index_health.py`
- Create: `tests/unit/test_index_health.py`

- [ ] **Step 1：写 marker 分类与三维漂移的失败测试**

```python
from agent_brain.memory.governance.index_health import build_index_health
from agent_brain.memory.store.pending import DirtyIndexMarker


def test_index_health_classifies_marker_and_graph_drift() -> None:
    report = build_index_health(
        md_ids={"mem-20260720-140000-active", "mem-20260720-140000-target"},
        index_ids={"mem-20260720-140000-active", "mem-20260720-140000-orphan"},
        expected_supersedes={
            ("mem-20260720-140000-active", "mem-20260720-140000-target")
        },
        indexed_supersedes={
            ("mem-20260720-140000-orphan", "mem-20260720-140000-active")
        },
        source_scan_trusted=True,
        graph_status="available",
        dirty_marker=DirtyIndexMarker(
            "repair_required",
            frozenset(
                {
                    "mem-20260720-140000-active",
                    "mem-20260720-140000-orphan",
                    "mem-20260720-140000-retired",
                }
            ),
            (
                "mem-20260720-140000-active",
                "mem-20260720-140000-active",
                "mem-20260720-140000-orphan",
                "mem-20260720-140000-retired",
            ),
        ),
    )

    assert report.status == "repair_required"
    assert report.missing_ids == frozenset({"mem-20260720-140000-target"})
    assert report.orphan_ids == frozenset({"mem-20260720-140000-orphan"})
    assert report.active_dirty_ids == frozenset({"mem-20260720-140000-active"})
    assert report.orphan_dirty_ids == frozenset({"mem-20260720-140000-orphan"})
    assert report.retired_dirty_ids == frozenset({"mem-20260720-140000-retired"})
    assert report.duplicate_dirty_entries == 1
    assert len(report.frontmatter_only_edges) == 1
    assert len(report.graph_only_edges) == 1
```

- [ ] **Step 2：运行测试并确认 RED**

Run: `pytest -q tests/unit/test_index_health.py::test_index_health_classifies_marker_and_graph_drift`

Expected: FAIL，`agent_brain.memory.governance.index_health` 尚不存在。

- [ ] **Step 3：实现最小健康模型和状态优先级**

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agent_brain.memory.store.pending import DirtyIndexMarker

IndexHealthStatus = Literal["clean", "repair_required", "corrupt", "unavailable"]


@dataclass(frozen=True)
class IndexHealthReport:
    status: IndexHealthStatus
    reason: str | None
    source_scan_trusted: bool
    md_count: int
    index_count: int
    missing_ids: frozenset[str]
    orphan_ids: frozenset[str]
    dirty_status: str
    dirty_entries: tuple[str, ...]
    dirty_entry_count: int
    dirty_unique_count: int
    active_dirty_ids: frozenset[str]
    orphan_dirty_ids: frozenset[str]
    retired_dirty_ids: frozenset[str]
    duplicate_dirty_entries: int
    graph_status: str
    expected_supersedes: frozenset[tuple[str, str]]
    indexed_supersedes: frozenset[tuple[str, str]]
    frontmatter_only_edges: frozenset[tuple[str, str]]
    graph_only_edges: frozenset[tuple[str, str]]

    @property
    def repair_required(self) -> bool:
        return self.status == "repair_required"

    def to_summary_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "status": self.status,
            "reason": self.reason,
            "repair_required": self.repair_required,
            "source_scan_trusted": self.source_scan_trusted,
            "items": {
                "md": self.md_count,
                "index": self.index_count,
                "missing": len(self.missing_ids),
                "orphan": len(self.orphan_ids),
            },
            "dirty_marker": {
                "status": self.dirty_status,
                "entries": self.dirty_entry_count,
                "unique": self.dirty_unique_count,
                "active": len(self.active_dirty_ids),
                "orphan": len(self.orphan_dirty_ids),
                "retired": len(self.retired_dirty_ids),
                "duplicates": self.duplicate_dirty_entries,
            },
            "supersession": {
                "status": self.graph_status,
                "expected": len(self.expected_supersedes),
                "indexed": len(self.indexed_supersedes),
                "frontmatter_only": len(self.frontmatter_only_edges),
                "graph_only": len(self.graph_only_edges),
            },
        }
```

`build_index_health()` 按 `source_scan_trusted=False/graph unavailable/dirty unavailable -> unavailable`、
`dirty corrupt -> corrupt`、任一集合漂移或 marker repair debt -> `repair_required`、否则 `clean`
的顺序构造报告。`reason` 只能是 `SOURCE_SCAN_UNTRUSTED`、`DIRTY_MARKER_CORRUPT`、
`DIRTY_MARKER_UNAVAILABLE`、`INDEX_PROJECTION_UNAVAILABLE`、`INDEX_PROJECTION_NOT_AVAILABLE`、
`INDEX_REPAIR_REQUIRED` 或 `None`。

- [ ] **Step 4：补 summary 隐私失败测试并实现 allowlist 输出**

```python
def test_index_health_summary_is_low_sensitivity() -> None:
    report = build_index_health(
        md_ids={"mem-20260720-140001-secret-source"},
        index_ids=set(),
        expected_supersedes=set(),
        indexed_supersedes=set(),
        source_scan_trusted=True,
        graph_status="available",
        dirty_marker=DirtyIndexMarker("clean"),
    )

    rendered = json.dumps(report.to_summary_dict(), sort_keys=True)
    assert "mem-20260720-140001-secret-source" not in rendered
    assert set(report.to_summary_dict()) == {
        "schema_version",
        "status",
        "reason",
        "repair_required",
        "source_scan_trusted",
        "items",
        "dirty_marker",
        "supersession",
    }
```

Run: `pytest -q tests/unit/test_index_health.py`

Expected: PASS。

- [ ] **Step 5：提交纯模型**

```bash
git add agent_brain/memory/governance/index_health.py tests/unit/test_index_health.py
git commit -m "feat: model index operational truth"
```

## Task 2：严格只读的 items + SQLite snapshot collector

**Files:**
- Modify: `agent_brain/product/governance_readiness.py`
- Modify: `tests/unit/test_governance_readiness.py`

- [ ] **Step 1：写一次 snapshot 同时返回 ids/edges 的失败测试**

```python
def test_readonly_index_projection_returns_ids_and_supersedes(tmp_path) -> None:
    brain = tmp_path / "brain"
    store = ItemsStore(brain / "items")
    new = MemoryItem(
        id="mem-20260720-140002-snapshot-new",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="snapshot new",
        summary="snapshot new summary",
    )
    old = MemoryItem(
        id="mem-20260720-140002-snapshot-old",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="snapshot old",
        summary="snapshot old summary",
        superseded_by=new.id,
    )
    store.write(new, "new")
    store.write(old, "old")
    index = HubIndex(brain / "index.db")
    index.upsert(new, "new", embedding=None)
    index.upsert(old, "old", embedding=None)
    index.close()

    truth = readiness_module._read_index_projection_readonly(brain / "index.db")

    assert truth.status == "available"
    assert truth.item_ids == frozenset({old.id, new.id})
    assert truth.supersedes == frozenset({(new.id, old.id)})
```

- [ ] **Step 2：运行并确认 RED**

Run: `pytest -q tests/unit/test_governance_readiness.py::test_readonly_index_projection_returns_ids_and_supersedes`

Expected: FAIL，`_read_index_projection_readonly` 尚不存在。

- [ ] **Step 3：扩展外部 SQLite snapshot 查询**

将现有 `_IndexGraphTruth` 替换为：

```python
@dataclass(frozen=True)
class _IndexProjectionTruth:
    status: Literal["available", "not_available", "unavailable"]
    item_ids: frozenset[str]
    supersedes: frozenset[tuple[str, str]]
```

在同一个 copied snapshot / query-only connection 中先验证 `items_meta` 与 `refs_graph` schema，
执行有界 `COUNT/SUM/MAX/typeof` aggregate 后分批读取：

```sql
SELECT id FROM items_meta LIMIT ?
SELECT source_id, target_id, relation
FROM refs_graph
WHERE relation = 'supersedes'
LIMIT ?
```

沿用 `_MAX_READINESS_ITEM_ENTRIES`、`_MAX_SUPERSEDES_ROWS`、总字节和单 id 大小限制；重复、非 text、
超限或 component identity 变化返回 `unavailable`，不返回部分集合。

- [ ] **Step 4：写 CLI collector 不构造 HubIndex 的失败测试**

```python
def test_collect_index_health_readonly_does_not_open_hub_index(
    tmp_brain_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ItemsStore(tmp_brain_dir / "items")
    item = _item("readonly-health")
    store.write(item, item.summary)
    index = HubIndex(tmp_brain_dir / "index.db")
    index.upsert(item, item.summary, embedding=None)
    index.close()

    monkeypatch.setattr(
        "agent_brain.platform.indexing.index.HubIndex.__init__",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("write open")),
    )

    report = collect_index_health_readonly(tmp_brain_dir)
    assert report.status == "clean"
```

实现 `collect_index_health_readonly(brain: Path) -> IndexHealthReport`：复用
`_read_items_readonly()`、`read_dirty_index_marker()` 与新的 index projection truth，然后调用
`build_index_health()`。Markdown 的期望 edge 为 `(item.superseded_by, item.id)`，仅当两端都在可信
active catalog 中才纳入比较。

- [ ] **Step 5：运行 collector 与 readiness 定向测试**

Run: `pytest -q tests/unit/test_governance_readiness.py`

Expected: PASS，现有 snapshot/security/预算测试继续通过。

- [ ] **Step 6：提交只读 collector**

```bash
git add agent_brain/product/governance_readiness.py tests/unit/test_governance_readiness.py
git commit -m "feat: collect readonly index health"
```

## Task 3：verify 三维 preview、JSON 与退出语义

**Files:**
- Modify: `agent_brain/interfaces/cli/commands/maintenance.py`
- Modify: `tests/unit/test_reindex_prune.py`

- [ ] **Step 1：写旧 verify 假绿的失败测试**

```python
def test_verify_fails_when_ids_match_but_dirty_marker_remains(
    tmp_brain_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    item, body = _make_item(LIVE_ID, "dirty live")
    store = ItemsStore(tmp_brain_dir / "items")
    store.write(item, body)
    index = HubIndex(tmp_brain_dir / "index.db")
    index.upsert(item, body, embedding=None)
    index.close()
    dirty_index_path(tmp_brain_dir).write_text(f"{item.id}\n", encoding="utf-8")

    result = runner.invoke(app, ["verify"])

    assert result.exit_code == 1
    assert "index in sync" not in result.output
    assert "dirty marker: repair_required" in result.output
```

同时加入 graph-only drift：

```python
def test_verify_fails_for_graph_only_supersession_without_printing_edge_ids(
    tmp_brain_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    store = ItemsStore(tmp_brain_dir / "items")
    source, source_body = _make_item(
        "mem-20260720-140003-graph-source", "graph source"
    )
    target, target_body = _make_item(
        "mem-20260720-140003-graph-target", "graph target"
    )
    store.write(source, source_body)
    store.write(target, target_body)
    index = HubIndex(tmp_brain_dir / "index.db")
    index.upsert(source, source_body, embedding=None)
    index.upsert(target, target_body, embedding=None)
    index.add_ref(source.id, target.id, "supersedes")
    index.close()

    result = runner.invoke(app, ["verify"])

    assert result.exit_code == 1
    assert "graph-only: 1" in result.output
    assert source.id not in result.output
    assert target.id not in result.output
```

- [ ] **Step 2：运行并确认 RED**

Run: `pytest -q tests/unit/test_reindex_prune.py -k 'dirty_marker_remains or graph_only'`

Expected: 两个测试 FAIL；旧 verify 仍输出 `index in sync`。

- [ ] **Step 3：实现 text 与 JSON formatter**

给 CLI 增加：

```python
format: str = typer.Option(
    "text",
    "--format",
    help="Output format: text or json.",
)
```

无 `--repair` 时直接调用 `collect_index_health_readonly(_brain_dir())`，text 保留原有四行 id
统计和 missing/orphan 明细，再追加 marker/graph 聚合。JSON 输出：

```python
typer.echo(json.dumps(report.to_summary_dict(), ensure_ascii=False, indent=2))
```

`format` 非 `text|json` exit 2。`report.status != "clean"` exit 1；只有 clean 输出
`index in sync`。

- [ ] **Step 4：验证 JSON 不打开 managed components 且不泄露 ID**

```python
def test_verify_json_is_readonly_and_low_sensitivity(tmp_brain_dir, monkeypatch) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    monkeypatch.setattr(
        maintenance._cli,
        "_managed_components",
        lambda: (_ for _ in ()).throw(AssertionError("managed write open")),
    )

    result = runner.invoke(app, ["verify", "--format", "json"])
    payload = json.loads(result.output)
    assert result.exit_code in {0, 1}
    assert set(payload) == {
        "schema_version",
        "status",
        "reason",
        "repair_required",
        "source_scan_trusted",
        "items",
        "dirty_marker",
        "supersession",
    }
    assert "mem-" not in result.output
```

Run: `pytest -q tests/unit/test_reindex_prune.py`

Expected: PASS。

- [ ] **Step 5：提交 preview 合同**

```bash
git add agent_brain/interfaces/cli/commands/maintenance.py tests/unit/test_reindex_prune.py
git commit -m "feat: verify complete index health"
```

## Task 4：事务性 supersession projection reconciliation

**Files:**
- Modify: `agent_brain/platform/indexing/graph_index.py`
- Modify: `agent_brain/platform/indexing/index.py`
- Modify: `tests/unit/test_graph_prune.py`

- [ ] **Step 1：写保留非 supersedes relation 的失败测试**

```python
def test_replace_supersedes_is_transactional_and_preserves_other_relations(
    tmp_brain_dir: Path,
) -> None:
    index = HubIndex(tmp_brain_dir / "index.db", embedding_dim=_DIM)
    source = _item("replace-source")
    target = _item("replace-target")
    stale = _item("replace-stale")
    for item in (source, target, stale):
        index.upsert(item, item.summary, embedding=None)
    index.add_ref(source.id, target.id, "refines")
    index.add_ref(stale.id, source.id, "supersedes")

    result = index.reconcile_supersedes({(source.id, target.id)})

    assert result.deleted == 1
    assert result.inserted == 1
    rows = index.connection.execute(
        "SELECT source_id, target_id, relation FROM refs_graph ORDER BY relation"
    ).fetchall()
    assert rows == [
        (source.id, target.id, "refines"),
        (source.id, target.id, "supersedes"),
    ]
```

- [ ] **Step 2：运行并确认 RED**

Run: `pytest -q tests/unit/test_graph_prune.py::test_replace_supersedes_is_transactional_and_preserves_other_relations`

Expected: FAIL，`HubIndex.reconcile_supersedes` 尚不存在。

- [ ] **Step 3：实现 projection replacement**

在 `graph_index.py` 新增：

```python
@dataclass(frozen=True)
class GraphReconcileResult:
    deleted: int
    inserted: int


def replace_supersedes(
    self,
    edges: Collection[tuple[str, str]],
) -> GraphReconcileResult:
    normalized = sorted(set(edges))
    with self.connection:
        deleted = self.connection.execute(
            "DELETE FROM refs_graph WHERE relation = 'supersedes'"
        ).rowcount
        self.connection.executemany(
            "INSERT INTO refs_graph (source_id, target_id, relation) "
            "VALUES (?, ?, 'supersedes')",
            normalized,
        )
    return GraphReconcileResult(deleted=max(0, deleted), inserted=len(normalized))
```

`HubIndex.reconcile_supersedes()` 只转发到 `self.graph.replace_supersedes()`。

- [ ] **Step 4：写注入失败回滚测试**

```python
def test_replace_supersedes_rolls_back_delete_when_insert_fails(tmp_brain_dir: Path) -> None:
    index = HubIndex(tmp_brain_dir / "index.db", embedding_dim=_DIM)
    source = _item("rollback-source")
    old_target = _item("rollback-old-target")
    rejected_target = _item("rollback-rejected-target")
    for item in (source, old_target, rejected_target):
        index.upsert(item, item.summary, embedding=None)
    index.add_ref(source.id, old_target.id, "supersedes")
    index.connection.execute(
        "CREATE TRIGGER reject_supersedes BEFORE INSERT ON refs_graph "
        "WHEN NEW.relation = 'supersedes' AND NEW.target_id = '"
        + rejected_target.id
        + "' BEGIN SELECT RAISE(ABORT, 'injected'); END"
    )

    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        index.reconcile_supersedes({(source.id, rejected_target.id)})

    assert index.get_refs(source.id) == [
        (source.id, old_target.id, "supersedes")
    ]
```

该断言证明 delete+insert 位于同一 transaction。

Run: `pytest -q tests/unit/test_graph_prune.py -k 'replace_supersedes'`

Expected: PASS。

- [ ] **Step 5：提交 graph primitive**

```bash
git add agent_brain/platform/indexing/graph_index.py agent_brain/platform/indexing/index.py tests/unit/test_graph_prune.py
git commit -m "feat: reconcile supersession projection"
```

## Task 5：分治 repair engine 与 lazy embedder

**Files:**
- Modify: `agent_brain/interfaces/cli/commands/index_maintenance.py`
- Modify: `tests/unit/test_graph_prune.py`

- [ ] **Step 1：写 retired-marker-only 不触发 embedder 的失败测试**

```python
def test_repair_retired_marker_without_embedder_or_index_rewrite(
    tmp_brain_dir: Path,
) -> None:
    store = ItemsStore(tmp_brain_dir / "items")
    index = HubIndex(tmp_brain_dir / "index.db", embedding_dim=_DIM)
    retired = "mem-20260720-140010-retired"
    dirty_index_path(tmp_brain_dir).write_text(
        f"{retired}\n{retired}\n",
        encoding="utf-8",
    )
    before = collect_index_health_readonly(tmp_brain_dir)
    called = False

    def forbidden_embedder():
        nonlocal called
        called = True
        raise AssertionError("embedder must stay lazy")

    result = repair_index_health(
        store,
        index,
        before,
        embedder_factory=forbidden_embedder,
    )

    assert called is False
    assert result.upserted == 0
    assert result.pruned == 0
    assert result.marker_entries_cleared == 2
```

- [ ] **Step 2：运行并确认 RED**

Run: `pytest -q tests/unit/test_graph_prune.py::test_repair_retired_marker_without_embedder_or_index_rewrite`

Expected: FAIL，`repair_index_health` 尚不存在。

- [ ] **Step 3：实现 repair result 与可信 preflight**

```python
@dataclass(frozen=True)
class IndexRepairResult:
    upserted: int
    pruned: int
    supersedes_deleted: int
    supersedes_inserted: int
    marker_entries_cleared: int


def repair_index_health(
    store: Any,
    idx: Any,
    before: IndexHealthReport,
    *,
    embedder_factory: Callable[[], Any],
) -> IndexRepairResult:
    if before.status in {"corrupt", "unavailable"} or not before.source_scan_trusted:
        raise OSError("INDEX_HEALTH_PREFLIGHT_UNTRUSTED")
```

完整扫描 `store.iter_all()` 建立 `source_by_id`，再次确认 scan complete。对
`sorted(before.missing_ids | before.active_dirty_ids)` 才调用一次 `embedder_factory()`，使用
`embedding_text_for_item(item)` 生成 embedding 并 upsert；对 orphan id 调用 `idx.delete()`；调用
`idx.reconcile_supersedes(before.expected_supersedes)`；最后以
`clear_dirty_index_marker(repaired_ids=set(before.dirty_entries), expected_entries=before.dirty_entries)`
清 captured entries。任一步异常都不吞掉。

- [ ] **Step 4：覆盖 active dirty、missing、orphan、graph 四类分治**

加入一个覆盖 active dirty、missing、orphan 的真实 `HubIndex` 测试。用 counting embedder 和
`monkeypatch` 包装 `index.upsert`，然后断言：

```python
assert embedder.calls == 1
assert set(embedder.inputs) == {
    embedding_text_for_item(active_dirty_item),
    embedding_text_for_item(missing_item),
}
assert orphan_id not in index.all_ids()
assert index.get_refs(replacement.id) == [
    (replacement.id, obsolete.id, "supersedes")
]
```

测试 setup 必须把 `active_dirty_item` 与 `unrelated_item` 同时写入 Markdown 和 index，只把
`missing_item` 写入 Markdown，把 `orphan_item` 只写入 index，并把 active id 写入 marker。断言
`upsert_calls == {active_dirty_item.id, missing_item.id}`，无关 active item 未传给 `idx.upsert`，
其他 graph relation 保留。

- [ ] **Step 5：覆盖 marker 并发 append 与 repair 失败**

沿用 `test_clear_dirty_marker_preserves_append_between_read_and_lock` 的 event barrier，在 repair
captured marker 后并发追加新 id；断言新行保留。分别注入 upsert、delete、graph reconcile、marker
clear 失败，断言异常传播且调用方不能得到成功 result。

Run: `pytest -q tests/unit/test_graph_prune.py tests/unit/test_index_health.py`

Expected: PASS。

- [ ] **Step 6：提交 repair engine**

```bash
git add agent_brain/interfaces/cli/commands/index_maintenance.py tests/unit/test_graph_prune.py tests/unit/test_index_health.py
git commit -m "feat: repair index drift by category"
```

## Task 6：CLI before/repair/after 与修复后置条件

**Files:**
- Modify: `agent_brain/interfaces/cli/commands/maintenance.py`
- Modify: `tests/unit/test_reindex_prune.py`

- [ ] **Step 1：写 after 非 clean 时 repair 必须失败的测试**

```python
def test_verify_repair_exits_nonzero_when_after_report_is_not_clean(
    tmp_brain_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_brain_dir))
    before = build_index_health(
        md_ids={"mem-20260720-140020-after-missing"},
        index_ids=set(),
        expected_supersedes=set(),
        indexed_supersedes=set(),
        source_scan_trusted=True,
        graph_status="available",
        dirty_marker=DirtyIndexMarker("clean"),
    )
    after = dataclasses.replace(before, status="repair_required")
    reports = iter((before, after))
    monkeypatch.setattr(
        maintenance,
        "collect_index_health_readonly",
        lambda _brain: next(reports),
    )
    monkeypatch.setattr(
        maintenance,
        "repair_index_health",
        lambda *_args, **_kwargs: IndexRepairResult(
            upserted=1,
            pruned=0,
            supersedes_deleted=0,
            supersedes_inserted=0,
            marker_entries_cleared=0,
        ),
    )

    result = runner.invoke(app, ["verify", "--repair", "--format", "json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["before"]["status"] == "repair_required"
    assert payload["after"]["status"] == "repair_required"
```

- [ ] **Step 2：运行并确认 RED**

Run: `pytest -q tests/unit/test_reindex_prune.py::test_verify_repair_exits_nonzero_when_after_report_is_not_clean`

Expected: FAIL，CLI 仍沿用旧的全量 repair 路径。

- [ ] **Step 3：实现显式 write scope 与 post verification**

CLI 流程固定为：

```python
before = collect_index_health_readonly(_brain_dir())
if not repair:
    _emit_verify(before, format=format)
    if before.status != "clean":
        raise typer.Exit(1)
    return
if before.status in {"unavailable", "corrupt"}:
    _emit_verify(before, format=format)
    raise typer.Exit(1)
with _cli._managed_components() as (store, idx, _retriever):
    result = repair_index_health(
        store,
        idx,
        before,
        embedder_factory=_cli.get_default_embedder,
    )
after = collect_index_health_readonly(_brain_dir())
_emit_repair(before, result, after, format=format)
if after.status != "clean":
    raise typer.Exit(1)
```

JSON payload 顶层固定 `schema_version/before/repair/after`；repair 仅包含五个 count。text 保留
`repaired N items, pruned N orphans` 兼容句，并追加 graph/marker/after 状态。

- [ ] **Step 4：覆盖 clean no-op 与幂等**

clean + `--repair` 不打开 managed components，输出 `index in sync`；同一 drift 连续两次 repair，
第一次 after clean，第二次 no-op 且仍 clean。

Run: `pytest -q tests/unit/test_reindex_prune.py`

Expected: PASS。

- [ ] **Step 5：提交 CLI repair 合同**

```bash
git add agent_brain/interfaces/cli/commands/maintenance.py tests/unit/test_reindex_prune.py
git commit -m "feat: verify repair postconditions"
```

## Task 7：readiness 复用统一报告且保持兼容

**Files:**
- Modify: `agent_brain/product/governance_readiness.py`
- Modify: `tests/unit/test_governance_readiness.py`

- [ ] **Step 1：写 readiness/verify 数量一致的失败测试**

```python
def test_readiness_index_metrics_match_shared_health_report(tmp_brain_dir: Path) -> None:
    report = collect_index_health_readonly(tmp_brain_dir)
    lane = build_memory_lifecycle_readiness(tmp_brain_dir)

    assert lane.metrics["index_health_status"] == report.status
    assert lane.metrics["index_missing_count"] == len(report.missing_ids)
    assert lane.metrics["index_orphan_count"] == len(report.orphan_ids)
    assert lane.metrics["index_dirty_entries"] == report.dirty_entry_count
    assert lane.metrics["index_dirty_unique"] == report.dirty_unique_count
    assert lane.metrics["index_dirty_retired"] == len(report.retired_dirty_ids)
    assert lane.metrics["supersession_drift_count"] == (
        len(report.frontmatter_only_edges) + len(report.graph_only_edges)
    )
```

- [ ] **Step 2：运行并确认 RED**

Run: `pytest -q tests/unit/test_governance_readiness.py::test_readiness_index_metrics_match_shared_health_report`

Expected: FAIL，新 metrics 尚不存在。

- [ ] **Step 3：接入报告并保留旧字段**

`_memory_lifecycle_lane_once()` 使用同一 item snapshot 与同一次 index projection 构造
`IndexHealthReport`，新增上述 metrics；保留 `supersession_graph_status`、
`supersession_drift_count`、`index_dirty_status`、`index_repair_required` 及既有 check id，值全部从
报告派生。不得在 lane 内再次扫描 items 或 index。

- [ ] **Step 4：覆盖只读指纹与性能预算**

在现有 2500 items / 100 pending fixture 中增加计数器：

```python
item_scans = 0
index_scans = 0
real_items = readiness_module._read_items_readonly
real_index = readiness_module._read_index_projection_readonly

def counted_items(path):
    nonlocal item_scans
    item_scans += 1
    return real_items(path)

def counted_index(path):
    nonlocal index_scans
    index_scans += 1
    return real_index(path)

monkeypatch.setattr(readiness_module, "_read_items_readonly", counted_items)
monkeypatch.setattr(readiness_module, "_read_index_projection_readonly", counted_index)
lane = build_memory_lifecycle_readiness(brain)
assert lane.metrics["pending_total"] == 100
assert item_scans == 1
assert index_scans == 1
```

另在带 marker 与 graph drift 的 fixture 上复用 `_full_tree_snapshot()`，比较
items/pending/index/marker 文件和目录 metadata/hash 前后不变。

Run: `pytest -q tests/unit/test_governance_readiness.py`

Expected: PASS。

- [ ] **Step 5：提交 readiness 统一口径**

```bash
git add agent_brain/product/governance_readiness.py tests/unit/test_governance_readiness.py
git commit -m "feat: align readiness with index health"
```

## Task 8：文档、公开合同与本地全量门禁

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/storage-lifecycle.zh.md`
- Modify: `tests/unit/test_docs_truth_contract.py`
- Test: `tests/conformance/test_public_surface_lock.py`

- [ ] **Step 1：写文档合同失败测试**

```python
def test_docs_describe_three_dimensional_verify_and_explicit_repair() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    lifecycle = Path("docs/storage-lifecycle.zh.md").read_text(encoding="utf-8")
    for text in (readme, lifecycle):
        assert "memory verify --format json" in text
        assert "items_meta" in text
        assert ".index-dirty" in text
        assert "refs_graph" in text
        assert "memory verify --repair" in text
    assert "不会自动修复" in lifecycle
```

- [ ] **Step 2：运行并确认 RED**

Run: `pytest -q tests/unit/test_docs_truth_contract.py -k three_dimensional_verify`

Expected: FAIL，当前文档未描述新合同。

- [ ] **Step 3：更新运维合同**

README 加入以下命令块：

```bash
memory verify --format json
memory verify --repair --format json
```

storage lifecycle 加入：“`items/**/*.md` 是 identity/supersession 权威事实；`items_meta`、
`refs_graph` 是派生 projection，`.index-dirty` 是待核销修复债。verify/readiness/hook 不会自动
修复；只有显式 `memory verify --repair` 会在可信 preflight 后按类别修复，并以 after report
作为成功依据。”CHANGELOG 记录 additive JSON、默认 text 兼容与退出码从 ID-only 收紧为三维 clean。

- [ ] **Step 4：运行文档与定向回归**

```bash
pytest -q \
  tests/unit/test_index_health.py \
  tests/unit/test_reindex_prune.py \
  tests/unit/test_graph_prune.py \
  tests/unit/test_governance_readiness.py \
  tests/unit/test_docs_truth_contract.py \
  tests/conformance/test_public_surface_lock.py
ruff check agent_brain tests/unit/test_index_health.py tests/unit/test_reindex_prune.py tests/unit/test_graph_prune.py tests/unit/test_governance_readiness.py
python scripts/check_mypy_baseline.py
git diff --check
```

Expected: 全部 exit 0。

- [ ] **Step 5：提交文档**

```bash
git add README.md CHANGELOG.md docs/storage-lifecycle.zh.md tests/unit/test_docs_truth_contract.py
git commit -m "docs: document complete index verification"
```

- [ ] **Step 6：运行全部本地门禁**

```bash
pytest -q tests/unit
pytest -q tests/system
pytest -q tests/conformance
./agent_runtime_kit/hooks/test-hook.sh
python scripts/check-recall-quality.py
python scripts/generate-adapter-governance.py --check
python scripts/generate-lifecycle-governance-report.py --check
ruff check .
python scripts/check_mypy_baseline.py
git diff --check
```

Expected: 全部 exit 0；不以定向测试替代全量结论。

## Task 9：临时真实副本、直推 main 与真实 repair 验收

**Files:**
- No source changes unless verification exposes a defect.

- [ ] **Step 1：复制真实 authority scope 到隔离临时目录**

复制 `items/`、`index.db*`、`.index-dirty` 和必要 runtime marker；不复制 secrets。记录真实 source
fingerprint，并在副本设置 `BRAIN_DIR` 与 `MEMORY_HUB_TEST_EMBEDDING=1`。

- [ ] **Step 2：副本 preview 与 repair 演练**

```bash
memory verify --format json
memory verify --repair --format json
memory verify --format json
memory govern readiness --format json
```

Expected: before 精确报告 retired dirty=42、dirty entries=7988、graph-only=1、ID drift=0；repair
不初始化 embedder、不改 items；after `status=clean`、dirty marker clean、graph drift=0。

- [ ] **Step 3：fast-forward 集成并直接 push**

确认 feature worktree clean，主 worktree 仅有用户原来的 `findings.md/progress.md/task_plan.md`，执行：

```bash
git merge --ff-only feat/index-operational-truth
git push origin main
```

不创建 PR。

- [ ] **Step 4：等待 GitHub 9 个 required contexts**

核对 `unit (3.11)`、`unit (3.12)`、`hook-tests`、`security`、`benchmark-integrity`、
`docker-smoke`、`recall-quality`、`adapter-governance`、`lifecycle-governance` 全部 success。

- [ ] **Step 5：真实 brain before fingerprint 与 preview**

对 items 全树、index components、`.index-dirty`、pending 和 receipt scope 记录文件与目录
mode/size/mtime/ctime/inode/content hash；执行 `memory verify --format json`，确认与副本 before 数量
一致且 preview 指纹不变。

- [ ] **Step 6：执行一次显式真实 repair**

Run: `memory verify --repair --format json`

Expected: before repair_required；repair 仅 graph/marker counts 非零，upserted=0、pruned=0；after
clean。若实际计划出现 active dirty/missing/orphan 或 embedder 初始化，立即停止，不扩大 mutation。

- [ ] **Step 7：真实 after 验收**

```bash
memory verify --format json
memory govern readiness --format json
memory sync-pending --summary-only --limit 100 --format json
```

Expected: items Markdown fingerprint 与 before 完全一致；index/marker 仅有预期派生变化；
`.index-dirty=clean`、supersession drift=0、ID drift=0、snapshot stable；28 pending、318 stale、17
orphan record lock 未被顺带修改。

- [ ] **Step 8：清理本次 worktree 与已合并分支**

```bash
git worktree remove "$WORKTREE"
git branch -d feat/index-operational-truth
```

仅清理本次 worktree，不处理其他历史 worktree。
