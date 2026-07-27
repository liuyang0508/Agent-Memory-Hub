# 跨行业记忆可信度与冲突治理设计

日期：2026-07-28（Asia/Shanghai）

状态：方案 2 已确认并复核

## 1. 问题

一次未经核验的临时判断可以被写成长期 `fact` / `decision`，随后在相似查询中反复召回。
这不是 Coding 特有问题：运营结论、产品判断、合同解释、监管口径、财务数据和医疗信息都可能
因来源不足、适用范围变化或互相冲突而失真。

系统不能把关键词命中、模型判断、创建时间或较高置信度当成真伪裁决。它们只能发现候选；
可信度必须来自可追溯证据，无法裁决的冲突必须显式暴露。

## 2. 目标

1. 所有行业共用同一套可信度与生命周期规则，不按职业硬编码。
2. 无可靠来源的长期结论可以保存，但不能静默进入正常 Hook 注入。
3. 同范围内的疑似冲突不能再由“更新者胜出”静默裁决。
4. 结构化失效统一走既有 `SupersessionService`，不能退化成普通图标签。
5. 修复当前已知错误条目，并用原始窄查询验证它退出正常召回。

## 3. 通用可信度模型

本轮复用现有字段，不新增行业插件或职业枚举：

| 通用概念 | 现有承载 |
|---|---|
| 结论 | `type`、`title`、`summary`、正文 |
| 来源 | `source`、`refs.files/urls/resources/extractions/mems/commits` |
| 适用范围 | `tenant_id`、`project`、`tags`、`validity` |
| 时间边界 | `created_at`、`validity.observed_at/ttl_hours` |
| 可信状态 | `confidence`、`needs-review`、`unverified-boundary` |
| 生命周期 | `superseded_by`、lifecycle ledger |

`refs.commits` 只是 Coding 证据的一种。运营后台导出、PRD、会议审批、法条、合同、财报、
监管文件、实验记录、日志、网页和原始会话证据都通过已有 file、URL、resource、extraction
或 memory reference 表达。

行业规则只决定“核验时应看什么来源”，不改变核心写入、隔离、冲突和失效流程。

## 4. 写入治理

统一在 `WriteService.write()` 生效，覆盖 CLI、MCP、Hook、SDK、pending replay 和 harvester。

### 4.1 来源隔离

新写入的 `fact` / `decision` 若调用时没有显式 source ref：

- 仍写入 Markdown 权威存储，避免丢失；
- 自动添加 `needs-review`、`unverified-boundary`；
- `confidence` 上限设为 `0.35`；
- 返回稳定 warning；
- 由现有 `ContextFirewall` 阻止其进入普通注入。

写入后自动生成的“本次写入正文”sidecar 只能证明写入发生过，不能反向证明正文结论正确，
因此不能作为本规则的显式来源。

### 4.2 疑似冲突

写入前用现有项目/租户范围和召回词项查找既有 `fact` / `decision` 候选。候选发现只负责提高
召回率，不负责判断哪条为真：

- 新条目已通过 `refs.mems` 明确引用相关条目时，保留该关系供后续治理；
- 未引用疑似同主题候选时，沿用相同 review 隔离；
- 不自动创建 `superseded_by`，不自动删除旧条目。

固定阈值留在既有 topic helper 中；中文使用有界、非重叠的连续共享锚点，避免通用状态短语
因滑动窗口重叠被重复计数。不引入 LLM 裁决服务或行业专用分词器。

## 5. 召回冲突治理

### 5.1 长期知识

同一查询召回同范围的多个未失效 `fact` / `decision`，且既有 topic gate 判定为同主题时：

- 不再按创建时间排除旧条目；
- 保留预算内候选；
- cohort 增加稳定 reason：`topic_conflict_requires_verification`；
- Hook context 显示固定警告：
  `检测到可能冲突的长期记忆；不得按新旧或置信度选边，请核验当前领域的权威来源。`

这里表达的是“需要核验”，不是系统已经证明两条逻辑矛盾。

### 5.2 过程状态

`signal` / `handoff` 表达运行状态，继续使用现有时效规则。此次不改变它们的 newer-wins
行为，避免已解除的阻塞重新覆盖当前状态。

### 5.3 预算边界

只渲染固定警告和已有候选摘要，不注入额外全文。关键判断仍按 `detail_uri` 有界读取。

## 6. 结构化失效边界

`SupersessionService` 仍是唯一失效写路径，负责：

- `obsolete.superseded_by = replacement.id`；
- replacement memory ref；
- snapshot、ledger、索引同步与失败恢复。

通用 CLI `memory link --label supersedes` 不得直接写普通边。最小安全行为是 fail closed，并提示
使用 `memory govern apply-lifecycle --supersede OLD:NEW`，其中 `OLD` 是待失效条目，
`NEW` 是替代条目。其他 link label 不变。

## 7. 当前错误数据修复

设计和代码验证通过后，先 preview、再 apply：

```text
replacement:
mem-20260727-233933-评分配置旧鉴权修复不等于本次权限口径整改完成-2749c966

obsolete:
mem-20260728-002715-go评分配置鉴权无需重复开发-a5b4a21c
```

apply 后执行 `memory verify`，并重放原始窄查询。验收要求：

- obsolete 条目存在结构化 `superseded_by`；
- Hook 注入不再包含错误条目；
- lifecycle ledger 与索引无 drift。

## 8. 验收

最小自动化检查覆盖：

1. 无 source ref 的 `fact` / `decision` 被保存但进入 review 隔离；
2. 有 file、URL、resource、extraction 或 memory ref 的结论不被误隔离；
3. 运营、产品、法务、金融文本使用同一规则，无职业分支；
4. 同范围长期候选不再静默 newer-wins，并渲染核验警告；
5. `signal` / `handoff` 的现有时效行为不变；
6. `memory link --label supersedes` 无法绕过治理服务；
7. 真实错误条目完成 preview、apply、verify 和窄查询回归。

## 9. 非目标

- 不判断哪个行业结论为真；
- 不引入 LLM contradiction judge；
- 不为职业创建插件、枚举或独立数据库；
- 不自动 supersede、删除或改写冲突条目；
- 不在本轮扩展法域、币种、医疗人群等强类型 schema；真实需求出现后再在通用 scope 上演进。
