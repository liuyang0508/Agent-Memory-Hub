---
id: mem-20260110-170000-v05-keycolon-bracket-quirk
schema_version: "0.2"
type: fact
created_at: "2026-01-10T17:00:00+08:00"
agent: claude-code
session: null
project: agent-memory-hub
tenant_id: null
auth_context: null
tags:
  - test
  - yaml-quirk
  - v05-compat
sensitivity: internal
title: "v0.5 YAML quirk fixture — key:[] no-space-after-colon"
summary: "ItemsStore._YAML_COLON_BRACKET_FIX should normalize key:[] to key: [] before parse"
refs:
  files:[]
  urls:[]
  mems:[]
  commits:[]
---

**事实**: v0.5 hub 偶尔写出 `key:[]` (无空格), 不合法 YAML
**来源**: items_store.py 的 _YAML_COLON_BRACKET_FIX 正则就是处理这个
