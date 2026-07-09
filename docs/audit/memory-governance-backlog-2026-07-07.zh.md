# 记忆链路待治理表

更新时间：2026-07-09

这份表用于跟踪 Agent Memory Hub 在记忆维护、记忆召回、记忆治理、记忆注入四条链路上的真实缺口和治理状态。口径来自本机只读审计、单元测试、runtime sidecar 和已有 benchmark artifact；不把未复现指标写成已完成能力，也不把个人 brain 数据清理冒充产品代码能力。

## 证据快照

| 证据面 | 结果 |
|---|---:|
| 定向维护/召回/治理/注入测试 | 279 passed |
| 适配 / Hook / MCP / 注入测试 | 374 passed |
| 本机 brain item | 1545 条 |
| `memory doctor` | 10/11 passed；新增 `memory CLI shim` WARN，定位到漂移 shim |
| `memory verify` | index 与 md 同步，0 drift |
| `memory health` | D，issue rate 20.7% |
| `memory review status --format json` | review_total 153，pending_depth 43，pending_dead 0 |
| `memory recall-drift replay-cohort --root-cause query_gate_underqualified --limit 5 --format json` | matched_gap_count 585，deduped_query_count 501 |
| LongMemEval-S 召回 | R@5 97.4%，R@10 98.4%，MRR 91.3% |
| 系统 benchmark | 240/240 通过；firewall include/exclude 100%；pack reversible 100% |

## 待治理表

| 优先级 | 链路 | 待治理项 | 数据证据 | 影响 | 治理状态 | 下一步 |
|---|---|---|---|---|---|---|
| P0 | 记忆注入 | 长中文/中英混合任务会退化成单个泛词关键词 | 复现样本中原输出为 `agent`、`true` 或 `agent\|amh\|hopfield`；修复后输出 `多agent协作\|长期记忆\|上下文工程...`、`新增接口\|复用接口...` | 召回偏题，容易注入微信公众号配图、封面图、JSON 布尔值或命令泛词等无关记忆 | 已代码闭环：补 `query_signal` 单测、真实 hook 端到端回归和 `real_prompt_cases.json` 对抗样本 | 继续把真实长 prompt / 日志 / 代码片段样本沉淀到 query signal corpus |
| P0 | 记忆维护 | 过期 item 没有进入稳定清理节奏 | `expired=229`，health `D` | 旧阻塞、旧状态继续参与召回，污染上下文 | 已有 `memory govern plan` 和 health 指标；本轮不自动改个人 brain 数据 | 用 `memory govern plan` 产出的 review 队列分批 archive / rewrite |
| P0 | 记忆召回 | 召回后“能不能答对”弱于 R@K | LongMemEval R@5 97.4%，但 judge acc 41.6%；multi-session 6.6%，temporal 12.6% | 找得到材料，但多会话/时序问题容易答偏 | 已有 answerability、temporal stale、supersession 过滤；本轮不继续放宽策略 | 单独建立 answer-generation / temporal resolver 评测，不和 R@K 混成一个分数 |
| P0 | 记忆注入 | 实际 runtime 有大量 context rejected gap | recall drift 601 gaps；本机 `replay-cohort` 当前匹配 585 个 `query_gate_underqualified` gap、去重 501 个 query | 用户感知是“没想起来”或“召回被吞” | 已代码闭环：新增 `memory recall-drift replay-cohort`，可导出可回放 prompt cohort | 将 cohort 接入 query-signal / firewall 回归数据集 |
| P0 | 记忆治理 | review 队列积压 | `memory review status`: review_total 153，pending_depth 43，pending_dead 0 | 好记忆无法进入正常召回，或 pending 长期不落库 | 已代码闭环：新增 `memory review status --format json`，把 review 与 pending 积压放到同一入口 | 后续做批量 approve/reject UI 和 pending TTL 告警 |
| P1 | 记忆维护 | 自动治理比例太低 | `govern plan`: 400 actions，只有 3 个 safe auto-apply，397 个需人工 review | 治理成本高，规模增长后不可持续 | 已定位为策略边界问题；本轮不扩大自动写权限 | 先扩大只读 plan 分类，再逐类允许 safe auto-apply |
| P1 | 记忆治理 | 低质量 summary 偏多 | `low_quality=92`，主要是 summary 超长 | 召回片段太长，压缩视图噪声大 | 已有 summary rewrite 管线和治理命令 | 用 review 队列逐批执行 summary rewrite，不自动改写未审计条目 |
| P1 | 记忆治理 | 漂移聚类需要产品化入口 | `drift_clusters=28`，contradictions `7` | 同主题多条 artifact/episode 堆叠，后续 agent 难判断最新版 | 已有 drift/contradiction 检测；产品入口仍待做 | Web Admin 增加“按项目聚类合并/设 supersedes”界面 |
| P1 | 记忆召回 | multimodal 提取缺口仍显著 | recall drift: `multimodal_extraction_missing=80` | 图片/音频/PDF 类上下文不能稳定进入记忆链路 | 已有多模态 hook 和 ASR 回归；缺独立 benchmark | 给 multimodal extraction 增加独立 benchmark 和失败样本落盘 |
| P1 | 记忆注入 | 多 Agent runtime verified 未满格 | adapter matrix: total 16，ready 15，verified 13，runtime_observed 10 | “已接入”和“真实运行注入过”还不是一回事 | README 中已拆清“接入面”和 verified 状态；安装输出已改为“可选未配置” | Adapter 矩阵继续拆 installed / configured / runtime observed / injected |
| P1 | 记忆注入 | 纯命令式 prompt 误触发泛词召回 | `Run /review on my current changes` 原会提取 `run\|review\|current\|changes` | 这类 prompt 本意是执行当前命令，不需要历史记忆；误召回会污染 review 上下文 | 已代码闭环：新增 `generic_command_without_topic` gate | 后续扩展到 `/test`、`/fix`、`/commit` 等命令型样本 |
| P1 | 记忆注入 | Hook 注入质量缺少关键词账本 | 之前 `memory hook recent` 能看 injected/gap/outcome，但注入成功路径看不到本次 hook 的安全关键词 | 用户只能从 prompt 输出肉眼判断关键词，难以批量定位“关键词对了但命中不对”还是“关键词本身错了” | 已代码闭环：`injection-cohorts.jsonl` 增加 `query_terms`，`memory hook recent --format json` 输出 `keywords` | 下一步把关键词质量、命中 item、feedback outcome 聚合成日报/周报 |
| P2 | 记忆维护 | 本机 `memory` shim 可漂移 | `memory doctor` 新增 `memory CLI shim` 行；本机当前 WARN，指向 `/var/folders/...` 临时路径 | 用户安装后可能遇到 CLI 不可用，但项目 venv 下 doctor 仍正常 | 已代码闭环：installer 每次重写 shim；doctor 增加 shim target 检查 | 用户重跑 install 后消除本机 WARN |
| P2 | 记忆维护 | storage tier 没有 cold 层 | hot 1066，warm 479，cold 0 | 长期 items 都还在热/温层，成本和噪声会上升 | 已有 tier show / tiering 机制；cold 策略仍需产品规则确认 | 明确 cold tier 规则：过期 session、低置信、旧项目归 cold |
| P2 | 记忆召回 | 空召回仍存在小簇 | recall drift: `empty_recall=25`，gap cluster 20 medium | 少数项目/关键词没有被索引或 query expansion 覆盖 | 已有 gap-clusters；本轮新增 replay-cohort 可复用同一机制 | 对 `empty_recall` 建立项目级词表和 query expansion 回归样本 |

## 当前已处理

- 长中文/中英混合任务的关键词退化问题已补测试并修复。
- JSON / JS 对象配置片段已纳入 `tests/fixtures/query_intent/real_prompt_cases.json`：`true`、`const`、数字 exit code 不再作为可见关键词；未加引号的 JS/TS 字段可作为结构化锚点。
- 纯命令式 prompt 已新增 fail-close：`Run /review on my current changes` 不再触发 `run|review|current|changes` 泛词召回。
- Hook 注入成功路径已记录安全关键词：`InjectionCohort.query_terms` 不存 prompt 原文，`memory hook recent --format json` 可直接查看 `keywords`、`item_ids`、后续 `outcome.usage`。
- Query Signal 定向回归、关键 hook 回归和全量 pytest 已通过。
- `memory recall-drift replay-cohort` 已新增，能按 root cause 导出可回放 gap 样本。
- `memory review status` 已新增，能同时报告 review queue 和 pending queue 积压。
- `memory doctor` 已新增 `memory CLI shim` 检查，能发现 shim 指向已删除临时目录。
