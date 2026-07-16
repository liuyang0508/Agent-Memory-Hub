# Dual-route recall 发布准备证据（2026-07-17）

## 结论

当前状态是 **BLOCKED**，不得标记为 release-ready，也不得据此推送或发布。
阻塞项是 held-out 用例 `multi-hi-08`：Hindi 问题要召回 cache TTL 条目，但当前冻结的
multilingual MiniLM 证据把无关条目排在目标之前。机器可读事实源是
`dual-route-calibration-report.json`，门禁命令是：

```bash
PYTHONPATH=. python scripts/check-dual-route-calibration.py
```

当前命令返回退出码 1，并报告 `release_gate=blocked`、`unresolved_gap_count=1`。
测试通过只证明实现没有已知代码回归，不会把校准门禁自动改成通过。

## 已完成的上线边界

- Gateway 是逻辑安全边界，不是常驻 semantic service。候选必须先通过
  InjectionGateway 与 ContextFirewall，才可进入 ContextPack。
- hook 只使用已经 ready 的 semantic provider；不会在冷路径加载或下载模型。
  semantic 不 ready 时保留 term BM25，并用完整问题走 Unicode-aware raw BM25 fallback。
- `AGENT_MEMORY_HUB_ROUTED_RECALL=0` 只回滚候选生成，不能关闭 Gateway，也不能把
  raw hit 直接注入 Prompt。
- `memory brief` 用于项目恢复摘要；`memory search` 用于具体任务相关性召回，输入应是
  完整任务描述。两者不是 `brief || search` 关系。
- “继续 / 确认 / 是 / 1”这类无主题锚点的 Session continuation 不在本阶段范围内。

## 旧用户升级与 adapter 刷新

旧 hook 不会自行变成 routed hook。用户需要先升级包，再执行 adapter refresh/repair：

```bash
memory self-update --dry-run
memory self-update --repair-hooks
memory doctor --fix
memory adapter install <adapter> --format json
memory adapter install-verify <adapter> --format json
```

`self-update --repair-hooks` 适合发布包升级后的全局 hook 修复；`memory doctor --fix`
用于 doctor 发现的 core adapter/path 漂移；单个 adapter 可重新运行幂等的 install，随后
用 install-verify 和真实 runtime evidence 验收。升级包但不 refresh/repair 已安装 hook，
不能据此声称已获得新召回链路。

## 冻结校准证据

| split | expected items | TP | FP | FN | precision | recall |
|---|---:|---:|---:|---:|---:|---:|
| calibration | 15 | 15 | 0 | 0 | 1.0000 | 1.0000 |
| heldout | 11 | 10 | 0 | 1 | 1.0000 | 0.9091 |

冻结 embedder 为
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，revision
`e8f8c211226b894fcb81acc59f3b34ba3efd5f42`，snapshot digest
`sha256:d9ffa8b29d3b9b379a3168bce486cab75ac50f5f8f7f9ba6c17cf6e07a600792`。
`multi-hi-08` 的目标 cosine 为 0.326786（rank 2），竞争项为 0.343218（rank 1）；
在不放宽安全阈值的前提下，当前证据不足以放行目标。

## 探索性模型 bakeoff（不得默认启用）

下表是同一 41-case / 33-item 公共安全夹具上的聚合探索结果。它用于解释为什么没有把
临时模型试验直接并入产品；不是 committed release gate，也不替代冻结校准报告。

| 方案 | 结果摘要 | 性能摘要 | 默认启用判定 |
|---|---|---|---|
| E5：`intfloat/multilingual-e5-small@614241f...` | 修复 `multi-hi-08`，但 non-gap positives 出现 1 FN、3 injection FP；全 positives injection 为 TP 33 / FP 3 / FN 1 | 进程内加载约 0.414s；warm query encode p95 约 13.76ms；warm routed search p95 约 16.27ms | **不得默认启用**：存在既有用例回归，替换 embedder 还要求明确的模型分发、冷启动和重建索引策略 |
| `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1@1427fd...` 安全策略（logit ≥ 0、margin ≥ 1） | 41 cases 中只触发 2 cases；安全策略未修复 `multi-hi-08`，保持 TP 33 / FP 0 / FN 1 | 进程内加载约 0.436s；warm bounded batch p95 约 23.36ms | **不得默认启用**：没有修复门禁，且 hook 冷路径不能加载第二模型 |
| 同一 reranker 的宽松探索策略（logit ≥ -2.5、margin ≥ 0.5） | 在当前夹具上修复 gap 且负例 0 injection FP | warm bounded batch p95 约 22.23ms | **不得默认启用**：阈值由单一 gap 驱动，缺少独立 held-out 校准、持久化可复现 runner 与跨平台冷启动证据 |

这里没有把探索脚本或临时模型缓存当作长期证据。若后续继续评估，必须把 runner、模型
revision/digest、夹具提取规则、阈值选择和 held-out 结果一并提交，并重新通过隐私、性能与
安全门禁。

## 静态旁路审计

旧字符串扫描命中已按执行边界分类：

- routed `inject-context.sh` 不含关键词为空提前退出、`no matches` 文本解析或
  `AGENT_MEMORY_HUB_RAW_QUERY` 旁路；
- `AGENT_MEMORY_HUB_RAW_QUERY` 仍只存在于明确的 legacy/raw CLI 兼容路径及其迁移测试；
- `no matches` 的其他命中属于人类 CLI 空结果、wiki 输出，或 SessionStart discipline 的
  recent-signal 检查，不是 UserPromptSubmit routed recall 协议；
- docs truth tests 中出现的旧短语是负向断言，用来防止 Agent-facing 指导回归。

因此本轮不删除合法 legacy 合同，也没有把静态字符串命中误判成可执行旁路。

## 30-run hook 性能证据

机器可读事实源是 `dual-route-hook-benchmark-report.json`。最终独立运行直接调用真实 base
worktree hook 与 candidate hook；benchmark runner 自身严格校验 adapter
`hookSpecificOutput` envelope，不再依赖仓库外 normalizer。校验项包括：唯一外层键、唯一
内层键、`UserPromptSubmit` event、非空 `additionalContext`、完整且唯一的
`<agent_brain>` 边界，以及边界内公开 sentinel。

base 固定为 `bb9128a668fea98bf9063bfbedc85cc75dc8936c`，candidate 固定为
`5ba8ab19b4fa4cf0616a69e5a33146cd048640ad`。两边使用同一个全新公开合成 brain、同一个
committed payload、同一个 Python、同一个离线 HashingEmbedder 和同一组 adapter 环境变量；
只有 worktree 的 `PYTHONPATH` 与真实 hook 路径不同。runner 参数固定为 30 measured
samples、3 warmups、交错 old/new、期望 `injected`。5 秒只是每次子进程的观测窗口；性能
放行仍使用 candidate 单次 2000ms 和 p95 增量 150ms 两项硬门槛。报告只保存聚合统计和
哈希，不包含 hook context、原始 hook stdout 或 payload 中的 prompt。

| command | samples | p50 | p95 | max | errors | timeouts |
|---|---:|---:|---:|---:|---:|---:|
| base hook | 30 | 2972.512ms | 3159.247ms | 3255.564ms | 0 | 0 |
| candidate hook | 30 | 1700.536ms | 1801.364ms | 1862.811ms | 0 | 0 |

candidate p95 相对 base 为 -1357.883ms，benchmark exit code 为 0，性能子门禁 PASS。
这不改变整体 **BLOCKED**：`multi-hi-08` 校准门禁仍未通过。

### 可复现命令

先把 `BASE` 和 `CAND` 分别指向上述精确 commit 的独立 worktree；`BRAIN` 必须是尚不存在
的路径，materializer 遇到已存在路径会 fail closed。下面的 `OLD` 与 `NEW` 除 worktree
路径外使用完全相同的公开 fixture、payload、Python 和环境变量：

```bash
BASE=/path/to/base-worktree
CAND=/path/to/candidate-worktree
PY="$BASE/.venv/bin/python"
RUN_ROOT="$(mktemp -d)"
BRAIN="$RUN_ROOT/brain"
PAYLOAD="$CAND/tests/fixtures/dual_route_hook_benchmark_payload.json"
CONTEXT_SENTINEL='PUBLIC DUAL ROUTE BENCHMARK SENTINEL'

test "$(git -C "$BASE" rev-parse HEAD)" = bb9128a668fea98bf9063bfbedc85cc75dc8936c
test "$(git -C "$CAND" rev-parse HEAD)" = 5ba8ab19b4fa4cf0616a69e5a33146cd048640ad

PYTHONPATH="$CAND" "$PY" \
  "$CAND/scripts/materialize-dual-route-hook-benchmark.py" \
  --brain-dir "$BRAIN"

OLD="/usr/bin/env BRAIN_DIR=$BRAIN MEMORY_HUB_TEST_EMBEDDING=1 MEMORY_HUB_EMBEDDING_OFFLINE=1 AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT=json AGENT_MEMORY_HUB_ADAPTER=codex AGENT_MEMORY_HUB_PYTHON=$PY PYTHONPATH=$BASE $BASE/agent_runtime_kit/hooks/inject-context.sh"
NEW="/usr/bin/env BRAIN_DIR=$BRAIN MEMORY_HUB_TEST_EMBEDDING=1 MEMORY_HUB_EMBEDDING_OFFLINE=1 AGENT_MEMORY_HUB_HOOK_OUTPUT_FORMAT=json AGENT_MEMORY_HUB_ADAPTER=codex AGENT_MEMORY_HUB_PYTHON=$PY PYTHONPATH=$CAND $CAND/agent_runtime_kit/hooks/inject-context.sh"

PYTHONPATH="$CAND" "$PY" "$CAND/scripts/benchmark-dual-route-hook.py" \
  --old-command "$OLD" \
  --new-command "$NEW" \
  --payload "$PAYLOAD" \
  --protocol adapter-envelope \
  --context-sentinel "$CONTEXT_SENTINEL" \
  --repeats 30 \
  --warmup 3 \
  --min-samples 30 \
  --timeout-seconds 5 > "$RUN_ROOT/aggregate.json"
```

本次 provenance 哈希全部记录在机器报告中，包括 old/candidate hook、runner、materializer、
payload 与确定性 fixture item。索引数据库不是哈希事实源；它由 materializer 使用上述
committed item 和 HashingEmbedder 重新生成。

优化前的第一轮独立 30-run 为 candidate p95 2727.697ms、max 2823.634ms，真实失败。
分段 profile 显示 routed CLI p50 约 431ms，而 `execute_routed_query`、HubIndex open、
`search_routed` 和 Gateway 在合成单条 brain 上分别约 9ms、3ms、3ms、4ms；主要成本是
hook、runtime-event shim 与 search shim 重复探测解释器时完整导入 CLI（单次 p50 约
459ms）。修复只复用父 hook 已验证并导出的 `MEMORY_PYTHON` verdict；独立调用仍执行
完整探测。修复后 5 次 warm candidate 均为 1.67–1.69s，随后才执行上面的最终 30-run。

## 发布解除条件

只有同时满足以下条件，才能把 BLOCKED 改成 PASS：

1. `multi-hi-08` 在独立 held-out 证据中命中目标，且禁止注入与 near-topic hard negatives
   仍保持 100% 拦截；
2. 更新 committed calibration report 后，`check-dual-route-calibration.py` 返回 0；
3. 30-run base/candidate hook 基准满足 candidate 单次小于 2s，p95 增量不超过 150ms，
   且两边使用同一隔离 brain、同一 payload 和可比协议；
4. targeted、全仓测试、Ruff、静态旁路扫描和 `git diff --check` 全部通过。
