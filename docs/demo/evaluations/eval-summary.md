# Demo 评估总结

对照 `docs/demo/00-scenario.md` 跑两次交接，列出**真实命中的成功 / 失败信号**，
反推 v0.2 应该改的 schema 点。

## 评估 1：CC → Codex（同域续做）

### ✅ 成功信号

- [x] Codex 没问"请告诉我更多上下文"，直接 `cd` 到 repo 开干
- [x] 第一个动作就是 `git status` + 看 `src/exporters/csv.py`，符合 handoff 引导
- [x] 5 个 Next Actions 完成 4 个，剩 1 个（README）放进了下个 commit
- [x] frontmatter 必填字段齐全（id / mode / created_at / source/target_agent / sensitivity）
- [x] 没有把任何 transcript / secrets 漏给 Codex

### ❌ 失败信号

- [x] **Codex 推翻了上游的关键决策（utf-8-sig → utf-8）**，根因：handoff 的
  Decisions 段只列了"列范围"，**没列编码决策**。Codex 不知道 BOM 是为了
  Excel 兼容，按"清洁 CSV"改了。结果：测试绿但产出在 Excel 显示乱码。
- [x] **README 那条 Next Action 没在本次 session 闭环**，Codex 自己挪到了
  下次 commit，handoff 没说"必须本次完成"。schema 没有"软/硬必做"区分。

### 失败的根因

| 失败 | 根因 | 类别 |
|---|---|---|
| BOM 决策丢失 | handoff 的 Decisions 段对"非显然决策"没强制力 | schema |
| README 漏做 | Next Actions 没区分必做/可挪 | schema |

---

## 评估 2：Codex → Wukong（跨域转交）

### ✅ 成功信号

- [x] Wukong 用业务语言跟 PM 沟通，**没暴露**任何代码细节
- [x] 遵守了 Constraints 4 条全部：截止时间、对外口径、脱敏数据、不当场承诺工期
- [x] PM 反馈回写到 `docs/demo/snapshots/05-wukong-final.md`，闭环
- [x] 触发了正确的 mode 切换：从 task-handoff → 生成 code-resume follow-up

### ❌ 失败信号

- [x] **`parent_handoff` 是单向引用**，没有反向"triggers_followup" 链。
  如果要追踪"这个跨域转交派生了几个 follow-up"，必须扫全部 handoff
  反向找，没有索引层的话效率差。
- [x] Wukong 主动生成 follow-up 这个动作**没在 schema 里有合法位置**。
  当前 handoff schema 只描述"我从哪儿来、要做什么"，没描述"我做完会
  生成什么"。

### 失败的根因

| 失败 | 根因 | 类别 |
|---|---|---|
| 反向链缺失 | frontmatter 只有 `parent_handoff`，没有 `child_handoffs` | schema |
| follow-up 没合法位置 | 没有"completion artifacts"章节 | schema |

---

## v0.2 Schema 改进建议（按优先级）

### P0（必须改，否则 v1 会受伤）

1. **handoff-code 模板的 Decisions 段升级**
   - 从可选段升为**必填段**
   - 强制要求列出"non-obvious decisions"——任何换个人会本能改回去的选择
   - 每条决策必须带 `decision + reason + cost-of-reverting`
   - 例：`utf-8-sig | Excel 双击中文不乱码 | 改回普通 utf-8 会让 PM 那侧产出无法直接看`

2. **frontmatter 加 `child_handoffs` / `completion_artifacts` 双向链字段**
   - 闭环时上游回填 child；下游生成 follow-up 时反向填 parent
   - 这是 v1 索引层能做查询的前提

### P1（v0.2 改）

3. **Next Actions 区分硬/软**
   - `must_complete: [1, 2, 3]` 列硬指标
   - `nice_to_have: [4, 5]` 列可下次做
   - eval 时只对 must_complete 判失败

4. **handoff-code 加 "Verification Expectations" 段（升回 P0）**
   - 当前模板的 Next Actions 第 4 条隐含了"测试要绿"，但没法机器校验
   - 应该独立成段，明确写"判定本次续做成功的客观命令"
   - 例：`pytest tests/test_csv_export.py -v` 必须 5 passed
   - 加上"产出物必须能在 Excel 双击打开看不乱码"——这就是 BOM 决策的反向校验

### P2（v0.3 之后）

5. **handoff-task 加 "out-of-scope" 段**
   - 显式列出"协同态 Agent 不应该做的事"
   - 当前 demo 中 Wukong 没承诺工期是靠 Constraints 第 4 条托底，但
   Constraints 表达的是约束，out-of-scope 表达的是边界，含义不同

6. **sensitivity 等级细化**
   - 当前是 4 级 `public/internal/private/secret`
   - 跨域转交场景下 Wukong 给"群里 N 人可见"的内容，跟"PM 私聊"差别大
   - 建议加 `audience` 字段，跟 sensitivity 解耦

---

## 这次 demo 验证了什么

| v0 假设 | 验证结果 |
|---|---|
| 一个 Agent 不读 transcript 能续做 | ✅ 两次都做到了 |
| 两种 handoff 应该用不同 schema | ✅ 字段差异化是必要的，不能合并 |
| 极简 schema（6 必填字段）够用 | ⚠️ 大部分够，但漏了 BOM 这种非显然决策 |
| 不依赖 Obsidian 也能跑 | ✅ 全程纯 Markdown |

## 这次 demo 没验证什么

- 真实的 Agent 调用（演的是 schema 张力测试，不是实际运行 CC/Codex/Wukong）
- 长链路（>3 段）handoff 的衰减情况
- 并发改动（两个 Agent 同时改一个 task）
- secrets / 私密数据的实际泄露风险

这些等 v0.1（gen-handoff 脚本）和 v0.2（schema 修订）后再压。

---

## 下一步建议

1. 按 P0 改 schema：升级 Decisions 段 + 加双向链字段
2. v0.1 实现 `agent_runtime_kit/tools/gen-handoff.sh`：从 Claude Code 当前 git/files/todo
   状态自动出 handoff 草稿（让人手填 Decisions / Next Actions 等思考层段）
3. 再用一个**真实任务**（不是虚构）跑一次，看脚本+schema 在真实场景的摩擦
