---
id: mem-20260106-130000-sample-handoff
schema_version: "0.2"
type: handoff
created_at: "2026-01-06T13:00:00+08:00"
agent: claude-code
session: null
project: agent-memory-hub
tenant_id: null
auth_context: null
tags:
  - test
  - handoff
sensitivity: internal
title: "Handoff fixture — covers refs.tags forward-compat (v0.2 wrote this field)"
summary: "Validates that historical refs.tags field is silently ignored by extra=ignore"
refs:
  files: []
  urls: []
  mems:
    - mem-20260103-100000-sample-decision
  commits: []
  tags:
    - v0.2.0-historical
---

**暗号识别**: "接续 sample handoff fixture"
**项目目标**: 验证 Refs(extra=ignore) 接受历史 tags 字段
