# 跨行业记忆可信度与冲突治理实施计划

> 规格：`docs/superpowers/specs/2026-07-28-domain-agnostic-memory-trust-governance-design.md`

**目标：** 阻止未经来源核验的长期结论进入正常召回，显式展示跨行业长期知识冲突，并统一结构化失效入口。

**约束：** 复用 `WriteService`、`ContextFirewall`、`SupersessionService` 和现有 refs/validity；不新增行业插件、LLM 裁决或 schema。

## Task 1：写入隔离与通用同主题候选

**文件：**

- 修改：`tests/unit/test_write_service.py`
- 修改：`tests/unit/test_context_firewall.py`
- 修改：`agent_brain/memory/context/context_firewall_rules.py`
- 修改：`agent_brain/memory/store/write_service.py`

**步骤：**

1. 增加失败测试：manual 来源的无 ref `fact` / `decision` 被保存，但带
   `needs-review`、`unverified-boundary` 和 `confidence <= 0.35`。
2. 增加表驱动测试：运营、产品、法务、金融文本走同一规则；有 file、URL、resource、
   extraction 或 mem ref 时不因缺来源隔离。
3. 增加失败测试：同 project/tenant 的已核验同主题条目存在时，新条目若未在 `refs.mems`
   确认关联则进入 review；已确认关联不误隔离。
4. 给既有 `topic_recency_terms()` 补确定性的 CJK n-gram，解决中文连续文本被当成单个词的问题；
   不引入分词依赖。
5. 在 `WriteService` 的 sidecar 生成前检查显式 ref，并复用 topic helper 对 source-of-truth
   items 做同范围候选预检。
6. 运行：
   `python -m pytest -q tests/unit/test_write_service.py tests/unit/test_context_firewall.py`
7. 提交：`fix: quarantine unverified durable memories`

## Task 2：召回冲突不再静默 newer-wins

**文件：**

- 修改：`tests/unit/test_context_firewall.py`
- 修改：`tests/unit/test_routed_answerability.py`
- 修改：`tests/system/test_dual_route_recall_matrix.py`
- 修改：`agent_brain/memory/context/context_firewall.py`
- 修改：`agent_brain/interfaces/cli/routed_query.py`

**步骤：**

1. 把旧测试改成新合同：同范围、同主题的 `fact` / `decision`（允许跨类型）都保留，
   `cohort_reasons` 包含 `topic_conflict_requires_verification`。
2. 保留并验证 `signal` / `handoff` 的时效淘汰行为。
3. 增加失败测试：routed recall 的 conflict reason 能穿过 gateway，并在 Hook context 顶部渲染
   固定“核验权威来源”警告。
4. 修改 `_apply_topic_recency_gate()`：长期知识只标冲突，过程状态沿用原淘汰逻辑。
5. 把 topic gate reason 纳入 `FirewallResult.cohort_reasons`；渲染层只读取该稳定 reason。
6. 运行：
   `python -m pytest -q tests/unit/test_context_firewall.py tests/unit/test_routed_answerability.py tests/system/test_dual_route_recall_matrix.py`
7. 提交：`fix: surface durable memory conflicts`

## Task 3：关闭普通 link 的 supersedes 绕行

**文件：**

- 修改：`tests/unit/test_cli_crud.py`
- 修改：`agent_brain/interfaces/cli/commands/links.py`

**步骤：**

1. 增加失败测试：`memory link ... --label supersedes` 返回非零，且 Markdown 与索引均不变。
2. CLI 对该 label fail closed，并提示
   `memory govern apply-lifecycle --supersede OLD:NEW`（待失效条目:替代条目）。
3. 保留其他 label 与 unlink 行为。
4. 运行：`python -m pytest -q tests/unit/test_cli_crud.py tests/unit/test_link_unlink.py`
5. 提交：`fix: reserve supersedes for lifecycle governance`

## Task 4：真实错误条目结构化失效

**步骤：**

1. 对正确条目替代错误条目执行 `memory govern apply-lifecycle --supersede ...` preview。
2. preview 为 `ready` 后使用 `--apply`。
3. 执行 `memory verify`。
4. 重放原始窄查询，确认错误条目因 `superseded` 退出 Hook 注入。
5. 不手工修改 item Markdown、索引或 ledger。

## Task 5：整体验证与直接发布

**步骤：**

1. 运行上述聚焦测试。
2. 运行仓库既有 Python 全量测试和 lint/type checks。
3. 检查 `git diff --check`、工作区只包含本轮文件。
4. 将实现分支 fast-forward 合入本地 `main`。
5. 直接推送 `origin/main`，不创建 PR。
6. 推送后核对远端 HEAD 和 GitHub required checks。
