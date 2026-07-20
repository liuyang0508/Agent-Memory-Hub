# 索引运行真相与分治修复设计

日期：2026-07-20（Asia/Shanghai）

状态：待用户复核

## 1. 背景与现场证据

上一轮 pending operational truth 已让 readiness 能在同一份可信 item snapshot 上报告 pending、
receipt 与 record-lock 状态。真实脑库复验仍存在一组彼此矛盾的索引结论：

- `memory verify` 输出 `md items=2057`、`index items=2057`、missing=0、orphan=0，随后宣称
  `index in sync`；
- `memory govern readiness` 同时报告 `.index-dirty=repair_required`、
  `supersession_drift_count=1`；
- `.index-dirty` 实际有 7988 行、42 个唯一 item id，这 42 个 id 全部已不在当前 Markdown
  source tree；
- Markdown frontmatter 当前没有 supersession edge，`refs_graph` 却保留 1 条
  `relation=supersedes`；该 source 的 Markdown 仍普通引用 target，而 target 的
  `superseded_by` 已为空。

这不是 `readiness` 假失败，而是 `memory verify` 的合同过窄：它只比较 Markdown id 集合与
`items_meta.id` 集合，不读取 dirty marker，也不比较 Markdown-derived supersession truth 与
`refs_graph`。因此“ID 集合相等”被错误表述成“整个索引同步”。

## 2. 目标

本轮建立一个可复用、低敏、可审计的索引运行真相合同：

1. 一次完整 Markdown 扫描同时生成 item id 集合与期望 supersession edge；
2. verify 同时检查 id projection、dirty marker、supersession projection；
3. `index in sync` 只在三个维度全部 clean 且扫描可信时出现；
4. repair 按漂移类型分治，不默认对全部 item 重新向量化；
5. repair 结束后必须重新采集完整健康报告，不能只凭“执行过写入”返回成功；
6. 默认 JSON 只输出数量、状态和稳定 reason，不泄露 item id、title、summary、path；
7. readiness 与 CLI 复用同一套比较语义，避免再次形成两个口径。

## 3. 非目标

- 不在本轮自动修改 318 条 stale、28 条 pending 或 17 个 orphan record lock；
- 不把 `.index-dirty` 变成主事实源；Markdown 始终是 source of truth；
- 不自动在 hook、readiness、doctor 中执行 repair；
- 不更换 embedding 模型，也不解决 Hugging Face TLS/离线缓存策略；该问题属于下一独立
  P2 子项目；
- 不重建所有 graph relation。只治理完全由 frontmatter `superseded_by` 派生的
  `relation=supersedes`，保留 `refs`、`refines`、`contradicts` 等其他关系。

## 4. 方案比较

### 4.1 方案 A：沿用全量 reindex

对任何 marker 或 graph drift 都重新 upsert 全部 Markdown，并重建向量。

优点是复用现有 `repair_index_drift()`；缺点是 2057 条真实 item 会触发不必要的 embedding，
受模型缓存和网络影响，并可能用降级 embedder 重写已有向量。它能清现场，但没有按风险分层。

### 4.2 方案 B：统一健康报告 + 分治 repair（采用）

先形成三维健康报告，再按证据选择最小修复：

- id missing / active dirty：只 re-upsert 确有需要的 active item；
- id orphan：只 prune 已证明没有 Markdown 的 index row；
- supersession drift：事务性替换 `refs_graph` 中的 `supersedes` projection；
- retired dirty marker：当 id 同时不在 Markdown 和 index 中时，只移除对应 marker entry；
- repair 后重新采集三维报告，只有 clean 才返回成功。

优点是离线友好、写入面小、结论可解释；代价是需要明确新的 health/repair 数据结构和并发合同。

### 4.3 方案 C：readiness 自动修复

readiness 发现漂移后直接修 index。

它能减少人工步骤，但破坏 read-only、preview-first 和 authority boundary，因此拒绝。

## 5. 权威边界

| 维度 | 权威事实 | 派生事实 | clean 条件 |
|---|---|---|---|
| item identity | `items/**/*.md` | `items_meta.id` | 两侧集合完全相等 |
| supersession | target Markdown 的 `superseded_by` | `refs_graph(relation=supersedes)` | 两侧 edge 集合完全相等 |
| dirty repair debt | `.index-dirty` 的有界 canonical entries | 无 | marker 缺失或为空 |

`.index-dirty` 只证明某次 index 更新没有完成；它不能证明当前仍有哪种具体漂移。健康报告必须把
marker entry 分为：

- `active_dirty`：id 仍在 Markdown；可能需要 re-upsert；
- `orphan_dirty`：id 不在 Markdown、但仍在 index；由 orphan prune 处理；
- `retired_dirty`：id 同时不在 Markdown 和 index；只需安全清理 marker debt；
- `duplicate_entries`：同一 id 的重复行数；用于说明 marker 膨胀，不改变 repair 授权。

## 6. 组件与接口

### 6.1 `index_health.py`

新增 `agent_brain/memory/governance/index_health.py`，承载纯比较语义和低敏输出：

```python
@dataclass(frozen=True)
class IndexHealthReport:
    status: Literal["clean", "repair_required", "corrupt", "unavailable"]
    source_scan_trusted: bool
    md_count: int
    index_count: int
    missing_ids: frozenset[str]
    orphan_ids: frozenset[str]
    dirty_status: str
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
```

该类型暴露 `to_summary_dict() -> dict[str, object]`。内部报告保留 id/edge 供显式 repair 使用；
summary 方法只允许 schema version、状态、
各类 count、closed-set reason 和 `repair_required`，禁止输出 id、edge、path、memory content。

### 6.2 采集器

CLI 采集器在 `ItemsStore.iter_all()` 完整扫描后获得：

- `md_ids`；
- `expected_supersedes={(replacement_id, obsolete_id)}`；
- `source_scan_trusted`，任何 skipped/error/truncated 都 fail closed。

index 侧在既有 managed `HubIndex` 连接中有界读取 `items_meta.id` 与
`refs_graph(relation=supersedes)`。dirty marker 继续复用已有 no-follow、size/entry cap 和
canonical id parser。

readiness 继续使用外部 SQLite snapshot，避免 live connection 带来的 schema/WAL 写入；它把安全
采集到的 index ids/edges 交给同一个纯比较函数。共享的是判定语义，不强行共享不同信任边界的 I/O。

### 6.3 CLI 合同

`memory verify` 保持现有 text 默认与 missing/orphan 明细兼容，并新增：

- dirty marker status、entries、unique、active/orphan/retired/duplicate counts；
- graph status、expected/indexed/frontmatter-only/graph-only counts；
- 只有完整 health clean 才输出 `index in sync` 并 exit 0；
- 任一可修漂移 exit 1；corrupt/unavailable/source scan 不可信同样非零且 reason 稳定。

新增 `--format json`。JSON 永远使用 summary contract，不输出 item id 或 edge。`--repair` 与
`--format json` 可组合，输出 before、repair result、after 三段低敏摘要。

## 7. 分治 repair

repair 必须显式调用 `memory verify --repair`，并遵循以下顺序：

1. 在 repair 前生成完整 `before` report；
2. 若 source scan、dirty marker 或 graph 采集不可信，零写入退出；
3. 对 `missing_ids ∪ active_dirty_ids` re-upsert source item；只有该集合非空时才初始化 embedder；
4. prune `orphan_ids`；
5. 在单个 SQLite transaction 中把全部 `relation=supersedes` 替换成
   `expected_supersedes`，不触碰其他 relation；
6. 清理 captured marker 中已证明处理完成的 entries：active 已 upsert、orphan 已 prune、retired
   已确认两侧都不存在；并发追加由现有 entry-count/marker-lock 语义保留；
7. 重新完整采集 `after` report；只有 `after.status=clean` 才 exit 0。

若 upsert、prune、graph reconciliation 或 marker clear 任一步失败：

- 不宣称 clean；
- 返回 closed-set failure reason；
- 已完成的 SQLite transaction 保持原子性；
- marker 只移除已证明 repaired 的 captured entries；
- 最终 `after` report 如实暴露剩余 repair debt。

## 8. 并发与一致性

- repair 沿用 catalog/index 的既有锁序，不引入反向锁；
- source scan 完成度必须检查 `ItemsStore.last_scan`；
- graph replacement 在一个 SQLite transaction 内完成；
- marker 清理由既有 marker file lock、identity check 和 expected entry counts 防止丢失并发 append；
- repair 后的第二次 health scan 是成功判定的唯一依据；
- readiness 与纯 verify 保持严格只读，目录、文件 metadata/content 指纹都不得变化。

## 9. 隐私与安全

- JSON summary 不含 id/edge/path/title/summary/body/project/session；
- text 默认只保留历史 missing/orphan id 明细，新增 graph/marker 默认只报数量，不打印 edge 或
  dirty id；
- dirty marker、items tree、index component 的 symlink/FIFO/device/oversize/identity swap 全部
  fail closed；
- corrupt/unavailable 不可被 `--repair` 当作空集合处理；
- repair 不下载模型，除非确有 `missing_ids` 或 `active_dirty_ids` 需要重新生成 embedding；
- 真实脑库验收前后保存 items、index、dirty marker 的文件和目录 metadata/hash 指纹。

## 10. 测试策略

### 10.1 健康报告

- id clean 但 dirty marker 非空时 verify exit 1；
- id clean 但 graph-only/frontmatter-only edge 存在时 exit 1；
- marker active/orphan/retired/duplicate 分类准确；
- source scan incomplete、marker corrupt、graph unavailable 均 fail closed；
- summary JSON 不含任何 id、edge 或内容字段。

### 10.2 repair

- 只有 retired marker 时不创建 embedder、不重写 item/index；
- 只有 graph drift 时只替换 supersedes relation，保留其他 relation；
- active dirty/missing 才调用 embedder 并 re-upsert；
- orphan 只 prune 对应 index row；
- 并发 marker append 不丢失；
- repair 中途失败不返回 clean；
- after report 非 clean 时 CLI 非零退出；
- 同一 repair 重跑幂等。

### 10.3 回归门禁

- `pytest tests/unit/test_reindex_prune.py tests/unit/test_graph_prune.py`
- 新增 `tests/unit/test_index_health.py`
- `pytest tests/unit/test_governance_readiness.py tests/unit/test_cli_smoke.py`
- 全量 unit/system/conformance、hook、recall-quality、adapter/lifecycle governance、ruff、mypy
  baseline 与 docs/public-surface gates。

## 11. 发布与真实验收

1. 设计、计划、实现都在独立 worktree 以 TDD 推进；
2. fast-forward 到 `main` 后直接 push，不创建 PR；
3. 等待 GitHub 9 个 required contexts 全绿；
4. 复制真实 brain 到隔离临时目录，在测试 embedder/offline 条件下执行 repair 演练；
5. 真实 brain 先运行 JSON verify preview，保存 before fingerprint；
6. 仅在 preview 与临时副本结果符合本设计时执行一次显式 `memory verify --repair`；
7. repair 后再次 verify/readiness，并比较 after fingerprint；items Markdown 必须不变，只允许
   index/dirty marker 出现预期派生变化；
8. 28 pending、318 stale 与 17 orphan record lock 不属于本修复，不得顺带清理。

## 12. 完成标准

以下证据必须同时成立：

1. 失败测试证明旧 verify 会在 marker/graph drift 下假绿；
2. 新 verify 的三维 summary 与 readiness 数量一致；
3. 分治 repair 的四类路径、失败与并发语义均有 red-green 证据；
4. JSON 隐私合同与默认 text 向后兼容通过；
5. 全量本地门禁和 GitHub required checks 通过；
6. 临时真实副本 repair 后 health clean；
7. 真实脑库 repair 前后 Markdown 指纹一致；
8. 真实 `.index-dirty` clean、supersession drift=0、ID drift=0；
9. remaining pending/stale/record-lock 存量如实保留。
