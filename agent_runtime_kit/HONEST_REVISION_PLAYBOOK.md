# Honest Revision Playbook（诚实修订手册）

> 给任何 LLM Agent（Claude Code、Codex、Qoder、Wukong 等）看的"何时主动 falsify 自己旧论断"指南。
> 跟 `AGENT_MEMORY_DISCIPLINE.md` 配对：纪律说"何时写"，本手册说"何时改 / 何时主动推翻"。

## 为什么需要这条

项目早期的 STRATEGY.md / ROADMAP.md / brain item 里写过的论断，会被后续的 dogfooding、竞品 research、static audit 推翻。
默认 LLM 反射是**守住旧论断**（sycophancy + commitment escalation）。这条 reflex 会让 brain pool 慢慢长成"宣传材料"而非"现实地图"。

STRATEGY.md §9 已经做过一次（"what collapsed in this revision"段），那是 user 触发的。本手册把它升级成 **agent 自触发的 procedure**，
不依赖 user 每次提醒。

---

## 何时触发诚实修订（5 个触发器）

工作过程中遇到以下任一情况，**应该立即走本手册的 procedure**：

| 触发器 | 信号源 | 例子 |
|---|---|---|
| **竞品 deep research** 推翻独占性论断 | 10+ 家 vendor 真实仓库 / docs 比对 | "none of N does X" 被某家做了 |
| **Dogfooding audit** 暴露 BLOCKER | 用项目自己的工具跑项目自己的数据 | 自家 schema 拒收自家历史 item |
| **Static audit** 发现 spec ↔ code drift | 静态代码审 vs 已 ship 声明 | "M2 Playbook 已落地" 但 grep 不到实现 |
| **新 commit / handoff 的描述与实际 diff 严重不符** | `git diff --stat` vs commit message | 自称 "1050 lines preservation"，实际 -15536 |
| **新数据让旧 brain item 变得 misleading** | search-memory 拉到旧条目，与现状冲突 | 旧 handoff 说 "未 commit"，但已 commit；旧 signal 说 "等审批"，但已批 |

不在表格内的不必触发。**不要为了刷"修订次数"而修订**——错误的修订比不修订更损害 thesis 完整性。

---

## Procedure（5 步硬约束）

### Step 1 — Evidence-grade match

新证据的等级必须**不低于**旧论断当时的证据等级。

| 旧论断证据等级 | 推翻所需新证据等级 |
|---|---|
| **L1 直觉/手感** | L1 反例足够 |
| **L2 一次 ad-hoc 比对** | L2 系统比对（多源） |
| **L3 多源 deep research + 写入 STRATEGY** | L3 多源 deep research + 至少 2 独立信号源 |
| **L4 在 spec / 论文层做的论证** | L4 同等级反证（不能仅凭一次 dogfooding）|

**反模式**: 用一条 issue / 一次失败 / 一次用户抱怨去推翻 spec 层论断。
单一信号 ≠ 推翻证据；它**触发调查**，调查产出的多源证据才是推翻证据。

### Step 2 — 拉出旧论断的所有承载体

- 在 brain pool 中 `search-memory` 拉相关 decision / fact / handoff
- 在 repo 中 grep README / STRATEGY / ROADMAP / spec 文档
- 列一个 "受影响载体清单"（不修订就会撒谎的文档列表）

### Step 3 — 写新 brain item（append-only）

**不删旧 item**。brain pool 是 append-only：
- 删除会丢失"曾经判断错过"的记录，让未来 agent 无法学习
- 删除让 cross-reference 链断裂
- 删除是 destructive action

新 item 类型选 `decision`（这是个 governance 决定），正文按 decision 模板：
- `**决策**`：把旧论断 X 修订为 Y
- `**理由**`：列新证据 + 证据等级 + 信号源数
- `**改回去的代价**`：不修订的代价 vs 修订的代价
- 用 `[[mem-id]]` wiki-link 反向链回旧 item，让 search 时新旧同时出现

### Step 4 — 修订承载体文档

对每个受影响文档，**追加 honesty-log 段落**（不要悄悄改原文）：
- README / STRATEGY / ROADMAP 顶部加 "Honesty note (YYYY-MM-DD revision)" 段
- 旧论断段落保留，新论断段落跟在后面，**标注 "supersedes previous claim X"**
- 历史可追溯，比"无声修订"更可信

参考 STRATEGY.md §9 的格式（这是项目内的 reference impl）。

### Step 5 — 写一条 governance episode 复盘

把这次修订过程本身写一条 episode brain item：
- `**情境**`：什么触发了修订
- `**做了什么**`：走完哪几步 procedure
- `**结果**`：受影响的载体清单 + 新 brain item id
- `**学到**`：这次修订暴露了哪个流程漏洞，是不是要更新本 playbook

---

## 自适用（dogfooding）

本 playbook 必须能修订自己。
如果未来某次诚实修订暴露了本 playbook 的某条规则不合理，按 Step 1-5 修订本文件，**不删旧规则**——在文件底部加 `## Revision Log` 段落记录修订。

---

## 反模式（要主动识别并拒绝）

| 反模式 | 为什么是反模式 | 该怎么做 |
|---|---|---|
| **删除旧 item 而非 append** | 丢失"判断错过"的元信号 | append 新 item，wiki-link 互链 |
| **改原 STRATEGY 段落不留痕** | 让历史不可追溯，等于撒谎重写 | 追加 honesty-log 段，supersedes 标注 |
| **用 L1 证据推翻 L3 论断** | 证据等级不匹配，朝令夕改 | Step 1 evidence-grade gate |
| **修订后不写复盘 episode** | 流程漏洞不会被下次修订发现 | Step 5 必走 |
| **修订时把"自己当时为什么错"写成"前任的错"** | 推卸责任，未来 agent 学不到 reflex | 用 "we / 项目 / 本 brain pool 之前论断"，不区分谁写的 |
| **用 LLM judge 当 evidence-grade 评分员** | 被审者同类来审，递归不可信 | evidence-grade 必须基于客观计数（信号源数、文档数、commit 数） |
| **修订 spec 不通知 user** | spec 层修订是战略级动作，user 必须知道 | 修订完用一句话告知 user：哪条 supersede 了哪条 |

---

## 调用 cue（agent 内部如何 self-prompt）

工作中遇到任意以下"内心声音"，立即问"是否需要触发诚实修订？"：

- "诶，这条 brain 跟现状好像不一样了"
- "竞品 X 好像在做我们 STRATEGY 说没人做的事"
- "这个 commit message 跟实际 diff 对不上"
- "audit 报告说我们某 spec 没实现，但 ROADMAP 写已 shipped"
- "用户的某句话推翻了我们某个长期论断"

不在以上清单里的内心声音不必触发；触发后按 Step 1-5 走完。

---

## Open Questions（本 playbook 自己的"新大陆"）

1. 修订频率上限？过度修订 = 朝令夕改，但本手册没设上限。等真实数据再回来填。
2. 多 agent 同时修订同一论断的 race condition 怎么处理？（短期：append-only 容忍；长期：需要 lease 机制）
3. 是否需要"修订投票阈值"——比如 2 个独立 agent 都触发同一修订才算数？

---

## Revision Log

- 2026-06-01 v0.1 初稿（claude-code）— 应对 mem-20260601-014747 handoff 误导事件，把 STRATEGY.md §9 honesty-log 模式抽成可复用 procedure
