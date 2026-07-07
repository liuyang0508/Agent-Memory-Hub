---
id: mem-20260109-160000-quoted-title-fixture
schema_version: "0.2"
type: fact
created_at: "2026-01-09T16:00:00+08:00"
agent: claude-code
session: null
project: agent-memory-hub
tenant_id: null
auth_context: null
tags:
  - test
  - yaml-edge-case
sensitivity: internal
title: 'YAML single-quoted title with "embedded double quotes" — must parse'
summary: "Single-quoted YAML scalar avoids the broken nested-double-quote case seen in real data"
refs:
  files: []
  urls: []
  mems: []
  commits: []
---

**事实**: YAML 单引号 wrap 时, 内嵌双引号无需 escape
**来源**: 2026-05-26 dogfooding 一条 wukong fact item 用 double-quoted title + 内嵌 double quote 触发 YAML parser 崩
