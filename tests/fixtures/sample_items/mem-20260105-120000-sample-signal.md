---
id: mem-20260105-120000-sample-signal
schema_version: "0.2"
type: signal
created_at: "2026-01-05T12:00:00+08:00"
agent: claude-code
session: 1735776000
project: agent-memory-hub
tenant_id: null
auth_context: null
tags:
  - test
  - signal
sensitivity: internal
title: "Signal fixture — numeric session id (coerced from YAML int)"
summary: "Validates that YAML-int session ids get coerced to string"
refs:
  files: []
  urls: []
  mems: []
  commits: []
---

**当前状态**: 测试 signal type + 数字 session id 兼容
**影响**: 历史 v0.5 数据曾把 epoch session id 写成 YAML int
**期望操作**: schema 应自动 coerce 成 str
