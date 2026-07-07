---
id: mem-20260103-100000-sample-decision
schema_version: "0.2"
type: decision
created_at: "2026-01-03T10:00:00+08:00"
agent: claude-code
session: null
project: agent-memory-hub
tenant_id: null
auth_context: null
tags:
  - test
  - decision
sensitivity: internal
title: "Decision fixture — sha256 dedup baseline"
summary: "Decision 类 fixture, 用于 governance dedup baseline"
refs:
  files:
    - /tmp/example.md
  urls:
    - https://example.com/docs
  mems: []
  commits: []
---

**决策**: 用 sha256 + jaccard 二级 dedup
**理由**: O(N) 快速 exact + O(M^2 within project) 模糊
**改回去的代价**: 重写 governance/pipeline.py 1 函数
