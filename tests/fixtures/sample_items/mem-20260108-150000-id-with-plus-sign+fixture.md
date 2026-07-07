---
id: mem-20260108-150000-id-with-plus-sign+fixture
schema_version: "0.2"
type: fact
created_at: "2026-01-08T15:00:00+08:00"
agent: claude-code
session: null
project: agent-memory-hub
tenant_id: null
auth_context: null
tags:
  - test
  - plus-sign-id
  - schema-edge-case
sensitivity: internal
title: "ID with + fixture — validates B1 widening accepts plus sign"
summary: "v0.2 \\w regex 拒绝 +; v1.1 B1 [^\\s/\\\\]+ 应接受"
refs:
  files: []
  urls: []
  mems: []
  commits: []
---

**事实**: + 字符在 id 后缀里应被 schema 接受 (e.g. 'real-cua+driver' compound topic)
**来源**: 2026-05-26 dogfooding mem-20260521-165500 case
