# v0.2 真实任务压测：48acbd08 "Analyze Computer Use best practices"

## 任务来源

用户的另一个 Claude Code 后台会话，已工作 1h17min 后 blocked，
等用户决策"要不要我帮你做其中某一步"。

## 为什么选它做 v0.2 测试

不是虚构。这是一个真实的、当前正在卡住的 handoff 时刻——
比任何虚构 demo 都更能暴露真实摩擦。

## 已知客观信息（仅来自 ~/.claude/jobs/48acbd08/state.json，非 transcript）

| 字段 | 值 |
|---|---|
| session_id | <session-id> |
| name | Analyze Computer Use best practices |
| cwd | <workspace> |
| createdAt | 2026-05-14T12:32:00Z |
| updatedAt | 2026-05-14T13:49:05Z |
| state | blocked |
| needs | 要不要我帮你做其中某一步？ |
| suggestedReply | 验证 idealab proxy 是否透传 beta header |
| intent | 用户从一张图（Image #4）提的"没能找到"问题 |
| respawnFlags | --permission-mode acceptEdits |

## 隐私边界（这次不读）

- 1.6MB 完整对话 jsonl —— 不读
- timeline.jsonl 14KB —— 等用户授权再读
- Image #4 原图 —— 不可读

## 这次实验只用 state.json 元数据

刻意限制信息源到"任何另一个 Agent 都能拿到的最少元数据"。
如果 schema 在这个最少信息下都站不住，说明 schema 必须改。
