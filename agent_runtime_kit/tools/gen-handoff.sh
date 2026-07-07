#!/usr/bin/env bash
#
# gen-handoff.sh — 从当前 git repo 状态生成 handoff-code.md 草稿
#
# 用法：
#   cd /path/to/your/repo
#   /path/to/gen-handoff.sh [--scope <subdir>] <target_agent> <task_slug>
#   /path/to/gen-handoff.sh --scope src/exporters codex weather-csv > handoff.md
#
# 设计原则：
#   - 只填客观字段（git/files/branch/commit）
#   - 思考层字段留 ⚠️ 待人填
#   - 大 repo 用 --scope 限定子目录
#   - 变更 > 50 项自动截断 + 警告
#

set -euo pipefail

SCOPE=""
if [ "${1:-}" = "--scope" ]; then
  SCOPE="$2"
  shift 2
fi

TARGET_AGENT="${1:-codex}"
TASK_SLUG="${2:-untitled-task}"

if ! git rev-parse --git-dir > /dev/null 2>&1; then
  echo "Error: 当前目录不是 git repo。先 cd 到目标 repo 再跑。" >&2
  exit 1
fi

NOW=$(date +"%Y-%m-%dT%H:%M:%S%z")
NOW_COMPACT=$(date +"%Y%m%d-%H%M%S")
HANDOFF_ID="handoff-${NOW_COMPACT}-${TASK_SLUG}"
REPO_PATH=$(git rev-parse --show-toplevel)
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "DETACHED")
HEAD_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "NO_COMMITS")

if [ "$HEAD_HASH" = "NO_COMMITS" ]; then
  HEAD_MSG="(repo has no commits yet)"
  RECENT_COMMITS="(no commits)"
else
  HEAD_MSG=$(git log -1 --pretty=%s)
  RECENT_COMMITS=$(git log --oneline -5)
fi

if [ -n "$SCOPE" ]; then
  RAW_STATUS=$(git status --porcelain -- "$SCOPE" || true)
  SCOPE_NOTE="（仅显示 \`${SCOPE}\` 范围）"
else
  RAW_STATUS=$(git status --porcelain || true)
  SCOPE_NOTE=""
fi

CHANGE_COUNT=$(printf "%s" "$RAW_STATUS" | grep -c . || true)
TRUNCATED_NOTE=""
CAP=50

if [ "$CHANGE_COUNT" -gt "$CAP" ]; then
  TRUNCATED_NOTE="

⚠️ **超过 ${CAP} 项变更（共 ${CHANGE_COUNT} 项），仅显示前 ${CAP} 项**。
看起来不像聚焦 task。建议传 \`--scope <subdir>\` 限定子目录。"
  STATUS_DISPLAY=$(printf "%s\n" "$RAW_STATUS" | head -n "$CAP")
else
  STATUS_DISPLAY="$RAW_STATUS"
fi

[ -z "$STATUS_DISPLAY" ] && STATUS_DISPLAY="(working tree clean)"

cat <<EOF
---
id: ${HANDOFF_ID}
mode: code-resume
schema_version: 0.1
created_at: "${NOW}"
source_agent: claude-code
target_agent: ${TARGET_AGENT}
sensitivity: internal
task: "${TASK_SLUG}"
repo: "${REPO_PATH}"
branch: "${BRANCH}"
head_commit: "${HEAD_HASH}"
scope: "${SCOPE:-<full repo>}"
parent_handoff: null
child_handoffs: []
---

# Handoff: ⚠️ 待人填一句话目标

## 1. Objective

⚠️ **待人填**。一句话讲做什么，不写为什么。

## 2. Current State

**代码层**（已自动抽取）${SCOPE_NOTE}：
- branch: \`${BRANCH}\`
- HEAD: \`${HEAD_HASH}\` ("${HEAD_MSG}")
- 变更数量：${CHANGE_COUNT} 项${TRUNCATED_NOTE}
- working tree 状态（\`??\`=untracked, \`M\`=modified, \`A\`=added, \`D\`=deleted）：

\`\`\`
${STATUS_DISPLAY}
\`\`\`

- 最近 5 commits：

\`\`\`
${RECENT_COMMITS}
\`\`\`

- 是否跑过测试: ⚠️ **待人填**

**任务层**（待人填）：
- 已完成的子目标: ⚠️
- 未完成的子目标: ⚠️

## 3. Decisions（必填⭐）

⚠️ **必填**。列出**非显然决策**——任何换个人会本能改回去的选择。

格式：\`决策 | 理由 | 改回去的代价\`

> 反问：刚才我做的所有选择里，有没有哪个换个人会本能改回去的？

1. ⚠️
2. ⚠️
3. ⚠️

## 4. Next Actions（必填）

**must_complete**（本次续做必须做完）：
1. ⚠️
2. ⚠️

**nice_to_have**（可挪到下个 session）：
1. ⚠️

## 5. Verification Expectations（必填⭐）

- 命令：⚠️ 例如 \`pytest tests/ -v\`
- 期望：⚠️ 例如 "全绿"
- 产出物校验：⚠️

## 6. Files Touched

见 Section 2 working tree 状态。下游运行 \`git diff\` 看具体改动。

## 7. Blockers

⚠️ 无的话写"无"。

## 8. Evidence Links

⚠️ 最多 5 条。

---

> 此 handoff 由 \`gen-handoff.sh\` 自动生成于 ${NOW}。
> 标 ⚠️ 的字段必须人工/Agent 补全后才能交给下游。
EOF
