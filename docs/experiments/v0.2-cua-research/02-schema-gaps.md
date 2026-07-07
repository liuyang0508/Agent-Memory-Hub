# v0.1 → v0.2 Schema Gap 分析

## 核心发现

v0.1 模板用 mode 区分了"代码续做" vs "业务转交"两种 handoff。
**这个分类不完整**——现实中至少还有第三种：**研究/分析续做**。

且这第三种在用户日常工作里出现频率不低：让 CC/Codex 调研一个技术、
做选型、读一份代码出报告、分析最佳实践——这些都不是改代码任务。

## 三种 handoff 性质对照

| 维度 | code-resume | task-handoff | research-resume（缺失）|
|---|---|---|---|
| 同/跨域 | 同（开发态） | 跨（开发→协同） | 同（开发态）但不是改代码 |
| 主要产出 | 代码 / commit | 通知 / 反馈 | 知识 / 结论 / 推荐 |
| 是否 git repo | 必须是 | 不要求 | 通常不是 |
| Verification 形态 | 跑命令（pytest） | PM 反馈接收 | 产出报告 / 论证链 |
| Next Actions 形状 | 顺序祈使句 | 业务动作清单 | 多分叉路径（OR） |
| 已完成的表达 | git diff | 已发消息 | 已读资料 / 已查关键词 |

## 必须新加的字段

### frontmatter

```yaml
mode: research-resume                      # 第三种 mode
working_dir: /path/to/wherever              # 替代 repo（可以不是 git）
data_sources: [URL, file, doc]             # 替代 repo+branch+head
investigation_window:                       # 时间窗
  started_at: ...
  duration: 1h17min
```

### 新模板 handoff-research.md 必填段

1. **Objective**（同）
2. **Investigation Frame**（用什么角度切——架构 / 性能 / 成本 / 安全 / API 等）
3. **Already Investigated**（已查的资源 + 关键发现）
   - 每条带：源 / 查询时间 / 信息丰度（高/中/低）
4. **Tentative Findings**（带置信度，每条说"凭什么这么相信"）
5. **Open Questions**（必答 vs 可选两组）
6. **Next Actions**（**支持分叉**：用 OR 区分候选路径）
7. **Reasoning Chain for Next Action**（为什么是这些 next actions）

### Verification Expectations 段需要 mode-aware

不同 mode 下这一段的形态不同：
- `code-resume`：跑命令 + 期望输出
- `task-handoff`：PM 反馈接收 + 关键事件
- `research-resume`：**产出物形式**（一份报告？一个决策？）+ **论证链质量标准**（必须有 N 个独立来源 / 必须 cover Y 个 angle）

## 还需要修的旧模板字段

### handoff-code.md 的 §2 Current State 中的"代码层"要打散

不要把"branch / HEAD / git status / 测试结果"绑死成"代码层"四件套。
应该改成 `evidence_layer`，按 mode 实例化：

- code-resume → git evidence
- task-handoff → message evidence
- research-resume → research evidence (URLs / docs / search queries)

## 推荐 v0.2 改动清单（按代价排序）

| 优先级 | 改动 | 改动代价 | 价值 |
|---|---|---|---|
| P0 | 新增 agent_runtime_kit/templates/handoff-research.md | 1 个新文件 | 解锁研究类任务 |
| P0 | frontmatter `mode` 加 research-resume | 单字段 | 类型识别 |
| P0 | gen-handoff.sh 加 --mode 参数 | 改 ~30 行 bash | 自动选模板 |
| P1 | frontmatter `repo`/`branch` 改 conditional | doc 变更 | 不再硬塞 N/A |
| P1 | Next Actions 支持分叉表达 | 模板小改 | 研究类必需 |
| P2 | gen-handoff.sh 加 research mode 自动抽取 | 中等 bash | 自动从 web history / open files 抽 |
| P2 | Verification Expectations mode-aware | 三模板对齐 | 干净 |

## 这次实验的元价值

### v0/v0.1 的设计**不是错的**——是**不完整的**

先把最高频场景（代码续做）做窄做深，再扩第三种 mode，
比一开始铺三种好——三个模板平行做必然有一个糙。

但这次真实任务暴露了"研究类"的高频性，触发 v0.2 加 mode 的时机到了。

### 这次实验的成本

- 没读那个 session 的对话内容（隐私）
- 仅用 state.json 元数据（约 1KB）
- 推出 7 个具体 schema 改动建议
- 元数据成本：极低；信息密度：极高

这印证了 handoff schema 设计的一个原则：
**好的 schema 应该让"上下游能用最少元数据沟通"——如果只有 metadata
都能反推出有用结论，schema 就抓到了关键信号。**
