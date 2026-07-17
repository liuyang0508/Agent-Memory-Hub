# Hook Preflight 合并设计

日期：2026-07-18  
状态：待用户书面规格复核  
适用分支：`codex/dual-route-recall`

## 1. 背景与目标

双路召回的 `multi-hi-08` 校准缺口已在 `895e472` 通过冻结技术别名关闭，41-case
评测达到 0 FP / 0 FN，校准门禁返回 0。但真实 hook 30-run 仍出现过 2400.187ms 和
2081.018ms 的单次耗时，违反“candidate 任一次小于 2 秒”的发布门槛。隔离对照表明
技术别名扫描仅约 1.52µs，不是数百毫秒回归来源；主要可控成本是 hook 在搜索前重复
启动多个短命 Python 进程。

本设计的目标是减少正常 UserPromptSubmit 路径的 Python 进程数并获得稳定性能余量，
同时完整保留：

- adapter runtime event；
- `live-prompt` 原始对话防丢证据；
- prompt attachment / multimodal resource 与 extraction evidence；
- prompt normalization 与 multimodal recall enrichment；
- 现有 2 秒生产搜索预算、结构化 hook-json 协议、Gateway 与 ContextFirewall；
- `AGENT_MEMORY_HUB_ROUTED_RECALL=0` 的候选生成 rollback 边界。

## 2. 不采用的方案

### 2.1 只合并初始 JSON 字段解析

把 prompt、session、cwd、event 的四次 `python3` 解析合为一次，改动最小，但后续仍有
runtime event、hook capture、normalization、multimodal recall-text、gap-json 等多个
解释器启动。预计不能为 2 秒门槛提供足够稳定余量，因此只作为本设计的一部分。

### 2.2 常驻 daemon 或 sidecar

常驻进程可以最大幅度降低冷启动，但会引入生命周期、升级、端口/IPC、崩溃恢复与多用户
隔离问题，并违反本轮明确的“无 daemon”发布边界。本阶段不采用。

### 2.3 删除证据写入或放宽性能门禁

runtime event、live prompt 和 multimodal evidence 是真实 hook 可维护性与可追溯性的组成
部分，不能为性能直接删除；也不能把 2 秒单次门槛改成只看平均值或通过重跑选择绿色结果。

## 3. 推荐架构

### 3.1 阶段 A：单次轻量 payload 解析

`inject-context.sh` 读取 stdin 后，只启动一次系统 `python3` 解析 JSON，并通过 NUL 分帧
输出 prompt、session_id、cwd、hook_event_name 四个字段。替代当前四次独立解析。

约束：

- payload 必须是 JSON object；非字符串字段归一为空字符串或默认 event；
- 任一字段含 NUL、字段数不等于 4、解析异常时直接输出 `{}` 并安全退出；
- 不使用 `eval`，不把用户文本拼成 shell 代码；
- 原始 prompt 不写入 debug stdout/stderr。

### 3.2 阶段 B：一次 Python authority 验证

沿用 `_resolve-python.sh` 的 symlink/canonical path/import/identity/PID 凭据校验。父 hook
仍是短生命周期凭据的唯一创建者，子 search shim 只能复用父进程导出的有效凭据。

本任务不降低 required imports，不信任外层环境伪造的 resolved marker，也不改变
`AGENT_MEMORY_HUB_PYTHON` 的候选顺序。

### 3.3 阶段 C：单进程 `hook_preflight`

新增 `agent_brain.memory.evidence.hook_preflight`，在一个已验证的 AMH Python 进程中按顺序：

1. 解析原始 payload；
2. 写 adapter runtime event；
3. 调用 `capture_prompt_payload` 写 live prompt，并复用其 multimodal resource capture；
4. 调用 `normalize_hook_prompt_for_recall`；
5. 调用 `recall_text_for_payload`；
6. 调用 `multimodal_gap_payload_for_payload`；
7. 通过 NUL 分帧输出固定协议：
   `amh-hook-preflight-v1`、normalized prompt、multimodal recall text、gap JSON。

模块只编排现有领域函数，不复制 ConversationStore、ResourceStore 或 normalization 规则。
输出协议有固定字段数和版本标记；recall text 继续受现有 4000 字符上限约束，gap JSON 只含
既有低敏 evidence，不包含原始 prompt。

### 3.4 阶段 D：搜索与注入协议保持不变

shell 将 normalized prompt 与 multimodal recall text 组成 `RECALL_PROMPT`，随后继续调用
现有 `search-memory.sh --routed-recall --context-firewall --format hook-json`。以下边界不改：

- 生产搜索预算默认 2 秒；
- stdout 上限 1 MiB；
- timeout 时杀死整个子进程组；
- 搜索非零、超时、污染 stdout、schema 不匹配均空注入；
- 最终 adapter envelope 仍只由现有严格解析器生成；
- raw candidates 不能绕过 Gateway 进入 prompt。

## 4. 错误处理与兼容路径

preflight 内的 runtime event 与 evidence capture 是 best-effort：单项写入异常不会阻止后续
normalization 和 recall。若 preflight 进程整体失败、协议版本错误、字段数不符或输出含污染，
hook 才执行现有分进程预处理作为兼容回退；回退路径仍受现有错误吞吐和搜索 fail-closed
规则约束。

兼容回退只服务异常，不参与正常性能测量。它保证升级时即使新模块缺失或某个平台出现
导入差异，也不会静默删除 runtime event、live prompt 或 multimodal evidence。

空 prompt、malformed JSON、NUL 字段继续安全返回 `{}`。多模态 gap 的 query hash 仍只在
确有 gap 时计算，不把 raw query 写入 telemetry。

## 5. 测试设计

### 5.1 `hook_preflight` 单元测试

- 正常文本 payload：固定版本、4 个字段、normalized prompt 正确；
- runtime event 与 live prompt 均落盘；
- attachment payload：resource/extraction 落盘、recall text 与 gap 互斥；
- 各写入函数分别抛异常：进程仍输出合法协议；
- malformed JSON、非 object、NUL、超长 recall text、非字符串字段；
- 输出不包含未授权的 raw prompt telemetry。

### 5.2 shell 合同测试

- 初始 payload 只解析一次且不用 `eval`；
- 正常路径不再调用 `record-runtime-event.sh`、独立 hook capture、独立 normalization、两次
  multimodal CLI；
- preflight 缺失/失败时回退旧路径，三类证据仍可验证；
- feature-off、timeout、stdout cap、descendant cleanup、malformed hook-json、adapter envelope
  与 Gateway fail-closed 测试继续通过；
- Hindi technical alias 的 degraded hook 正例继续通过。

### 5.3 回归与性能门禁

先运行 targeted suite、Ruff、Bash syntax、静态旁路扫描和 `git diff --check`，再运行全仓
pytest。性能发布证据必须满足：

- base 固定 `bb9128a668fea98bf9063bfbedc85cc75dc8936c`；
- candidate 固定实现提交，不使用 dirty worktree；
- 每轮都使用全新公开 fixture brain、同一 committed payload、同一 Python；
- 连续两轮正式 30-run 均为 30 samples、0 error、0 timeout；
- 两轮 candidate 的每一个样本都小于 2000ms；
- 两轮 candidate p95 相对各自 base 的增量都不超过 150ms；
- 任一轮失败则整体 release gate 保持 BLOCKED，不以第三次选择性重跑覆盖失败。

机器报告必须保留 run history，至少记录 `895e472` 优化前出现过的失败聚合结果以及优化后
两轮连续确认结果；不保存 prompt、context 或原始 hook stdout。

## 6. 发布与回滚

本优化不改变用户配置和数据 schema，不需要 reindex。旧用户仍需升级 package 并执行
adapter refresh/repair 才能获得新 hook。`AGENT_MEMORY_HUB_ROUTED_RECALL=0` 只回滚候选生成，
不会回滚 preflight 证据采集，也不会关闭 Gateway。

如果新 preflight 在特定平台失败，兼容路径自动恢复现有多进程行为；这可能重新触发性能
告警，但不会放宽注入授权。只有校准门禁、连续两轮性能门禁、全仓测试和独立审查全部通过，
发布文档才能从 BLOCKED 改为 PASS 并推送 GitHub。

## 7. 非目标

- 不引入 daemon、socket、后台 worker 或常驻模型；
- 不改变召回算法、technical alias、阈值或 candidate 排序；
- 不删除 transcript/live-prompt/resource/runtime event 证据；
- 不改变 hook-json、adapter envelope 或 telemetry schema；
- 不处理“继续/确认/是/1”的 session continuation。
