---
id: mem-20260102-090000-sample-episode
schema_version: "0.2"
type: episode
created_at: "2026-01-02T09:00:00+08:00"
agent: claude-code
session: null
project: agent-memory-hub
tenant_id: null
auth_context: null
tags:
  - test
  - episode
sensitivity: internal
title: "Episode fixture — covers episode body shape"
summary: "Episode 类 fixture, 验证 frontmatter parses 且 type=episode 合法"
refs:
  files: []
  urls: []
  mems: []
  commits: []
---

**情境**: 测试 episode 类 schema 加载
**做了什么**: 写一条标准 episode item
**结果**: 应该 parse 成功
**学到**: episode type 是 v1 schema 6 种之一
