# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned

- **M5 Platform v0.1** — Next.js admin UI + multi-tenant sync server (L2→L3 bridge)
- **Adapter promotions** — move at least 2 of the 10 WIP adapters out of `WIPAdapter` based on user demand (priority candidates: cursor, continue_dev)
- **LLM-assisted semantic contradiction** — opt-in v1.2 feature behind `--llm-provider` flag

## [1.1.0] — 2026-05-31

Capability-restoration + deep-audit release. Driven by a brutal-honest capability
audit on 2026-05-29 (`docs(audit): deep capability audit report`) which found a
large body of valuable work stranded on divergent worktree branches plus a set of
P0–P3 correctness/security issues in the merged line. This release lands 33 commits:
the full P0–P3 audit remediation (33/35 items, 2 deferred), restoration of the
d3 knowledge-graph web UI and its `[[wiki-link]]` edge derivation, graceful
degradation / offline self-check, and integration of the
abstraction/consolidation/tiering/entities + Obsidian-wiki feature set
(schema 0.3 → 0.4). Capability ledger restored to 21/21; full suite green.

### Added

- **Schema v0.4 — abstraction axis.** `MemoryItem.schema_version` 0.3 → 0.4,
  adding the abstraction / consolidation / tiering / entities fields and Obsidian
  `[[wiki-link]]` interop. Backward-compatible: existing v0.x items load unchanged.
- **d3 force-directed knowledge graph** restored in the web admin (had regressed
  off the main line), now deriving edges from both `refs.mems` and
  `[[wiki-link]]` body tokens (≈133 edges on the live pool: 76 refs + 57 wiki).
- **Graceful degradation + `memory doctor --offline`** (task #15) — core read
  paths and a self-check command operate with no network and no embedder.
- **Token-budget tiered read** (P1-8) and **git brain snapshots** (P1-9).
- **Multilingual embedder** (P3-1), **`reindex --prune/--verify`** (P3-2), and a
  **`migrate`** path (P3-4).
- **`GET /api/auth/needs-init`** first-use detection + test (web onboarding).
- **`?` keyboard-shortcuts cheatsheet**, health score-ring gauge, and
  type-colored activity heatmap restored in the dashboard.
- **Smoke / conformance tests** for previously-untested CLI commands (QC test-gap
  closure), plus a regression guard for `dashboard.html` inline-script integrity.

### Changed

- **`mutate_item` primitive** (P1-10) — scoped, index-aware item mutation.
- **CJK BM25 tokenization** (P1-1) and **components singleton + WAL** (P1-7).
- **rerank score normalization** switched to sigmoid (P1-5); test expectations
  updated accordingly.
- **`make_item_id` suffix widened 4 → 8 hex** to remove flaky id collisions under
  burst writes.
- **Adapter CLI surface** (P2-10) and **adapter robustness** (P3-6) hardened.
- Web admin login restored to the polished purple memory-graph design.

### Fixed

- **P0/P1 safe batch** — timezone validator, tenant guard, index-aware delete,
  archived-item skip, path-traversal guard, async webhooks, rerank sigmoid.
- **P0-3** — write path no longer routed through the audit gate.
- **BOM/CRLF tolerance** (P2-2) and **JSONL import resilience** (P2-4).
- **citation-rot scheme-less URL** handling (P2-9).
- **durable unlink** (P2-5) and **Obsidian sync fixes** (P2-8).
- **web robustness** (P3-8) plus assorted P2-3 / P2-7 / P2-11 / P3-5 fixes.
- Un-escaped template literals in `dashboard.html` login / snapshot JS.

### Security

- **SSE / WebSocket tenant scoping** (P2-12) — event streams are now filtered by
  tenant, closing a cross-tenant leak on the live channels.
- **Export injection hardening** (P2-6).
- **XSS escape in the user list** + `me`-highlight + copy-API-key restored without
  reintroducing the injection vector.
- **Confidence feedback** (P3-3) wired into the evolve / governance path.

### Documentation

- Deep capability audit report + capability ledger (21/21 restored), P2/P3
  apply-plan (conflict-free 21-item batch order), and a doc-currency pass (P2-13).

### Migration notes

- **No data migration required.** Schema 0.4 only widens the model; items written
  by 1.0.x continue to load. Run `memory reindex --verify` after upgrade to confirm
  the index matches the on-disk markdown.

## [1.0.1] — 2026-05-26

v1.1 polish release. Driven by a brutal-honest audit on 2026-05-26 that ran three
parallel tracks: (A) static review of M1–M4 source vs the 1011-line v1 tech spec,
(B) dogfooding the governance pipeline on 187 real brain-pool items, (C) 10-vendor
competitive research including Hermes' provider table, Tencent's
TencentDB-Agent-Memory, and rohitg00/agentmemory (17.7k★). Six BLOCKER fixes plus
six MAJOR fixes plus documentation rewrites — see commit trail on
`worktree-v1.1-polish` branch for the full history.

### Added

- **MCP server tools 4 → 7** — `delete_memory`, `audit_skill`, `audit_outbound`
  added per v1 spec §6.2.2. The earlier 4-tool surface silently violated the spec.
  ([B3])
- **`HubIndex.delete()`** — three-table cleanup (`items_meta` + `items_fts` +
  `items_vec`) so the new MCP `delete_memory` tool can fully evict an item.
- **`memory anti-drift --check-urls`** flag — opt-in real HTTP HEAD probing of
  each URL in old items. Default off keeps the command offline and
  proxy-friendly; opt-in upgrades confidence from 0.4 (age heuristic) to 0.95
  (verified 4xx/5xx/network-error). ([M5])
- **`ItemsStore.last_scan`** — `ScanStats` recording every item skipped during
  `iter_all`, so governance reports can surface parse failures instead of
  silently dropping items. ([M3])
- **`WIPAdapter`** base class — the 10 not-yet-implemented adapters now raise
  `NotImplementedError` from `install()`/`inject_context()` with a clear message
  pointing at the reference implementations, replacing the previous silent
  no-op stubs that returned hardcoded strings. ([M1])
- **Real install for `ClaudeCodeAdapter`** — atomically writes
  `~/.claude/settings.json` hooks (`SessionStart` / `UserPromptSubmit` / `Stop`)
  via tmp-then-rename. Idempotent via hooks-dir prefix detection; uninstall
  removes only hub-owned entries. ([M1])
- **Real install for `CodexAdapter`** — sentinel-bracketed
  (`<!-- BEGIN agent-memory-hub -->` / `<!-- END agent-memory-hub -->`) discipline
  block written to `~/.codex/AGENTS.md`. Idempotent in-place updates; uninstall
  preserves any user-authored content above/below the block. ([M1])
- **Two-pass dedup** in `governance/pipeline.py`: SHA-256 fingerprint
  (O(N), `severity=error`, suggests `refs.mems` supersession) followed by
  project-partitioned jaccard 0.8 (O(M² within project)). ([M4])
- **Conformance fixture set** — 10 items in `tests/fixtures/sample_items/`
  covering all 6 memory types + CJK in id + `+` in id + refs.tags
  forward-compat + numeric session id + tz-aware datetime + v0.5 `key:[]`
  YAML quirk. ([B6])
- **Conformance suite skip policy** — `tests/conformance/conftest.py` rejects
  any `skipif` whose reason does not declare an explicit env opt-in pattern.
  Silent skips become hard failures so CI cannot quietly pass without
  conformance coverage. ([B6])
- **`LOCAL_BRAIN_CONFORMANCE=1`** opt-in test sweep against the developer's
  own `~/.agent-memory-hub/items/`. Replaces the earlier always-skipped
  test that produced false CI green. ([B6])

### Changed

- **`MemoryItem.id` regex widened** from `^mem-\d{8}-\d{6}-[\w\-]+$` to
  `^mem-\d{8}-\d{6}-[^\s/\\]{1,200}$`. Accepts CJK characters, `+`, and other
  non-whitespace non-path-separator identifiers. Forbids path separators to
  keep ids usable as filenames on all OSes. ([B1])
- **`Refs.extra`** changed from `forbid` to `ignore` — historical v0.2 items
  that wrote `refs.tags=[...]` no longer fail validation. Unknown fields
  silently drop on read; the canonical `tags` is still the item top-level
  field. ([B2])
- **`ItemsStore.iter_all`** no longer aborts the entire iteration when a
  single item fails to parse. Errors are recorded in `last_scan.skipped`
  and iteration continues, so governance / drift / evolve survive one bad
  file. ([M3])
- **`EvolveEngine._audit_gate`** rewritten — was calling
  `scanner.scan_file().passed` but `scan_file` returns `list[Finding]`
  (no `.passed` attribute). The previous broad `except` silently rejected
  every proposal. Now fail-closed only on critical/high severity findings;
  unexpected errors propagate instead of being swallowed. ([B4])
- **`DriftDetector` datetime arithmetic** unified to tz-aware. All four
  `datetime.now()` calls now use `datetime.now(timezone.utc)`, matching
  `item.created_at`. No more `TypeError: can't compare offset-naive and
  offset-aware datetimes` on real data. ([B5])
- **`DriftDetector._extract_tool_names`** rewritten with three positive
  patterns (CamelCase ≥4, versioned/namespaced identifiers, short ALL-CAPS
  acronyms) plus a curated stop-word list, replacing "any capitalized word
  minus 4 exclusions". Contradiction confidence dropped from 0.7 to 0.5 and
  labeled "heuristic — not semantic" in code comments. ([M5])
- **`_open_components` split** in `cli.py` — `_store_only()` opens just the
  markdown store (no embedder, no sqlite, offline-safe) and is used by
  `read` / `list-recent` / `govern run` / `anti-drift` / `inspect` / `evolve`.
  `_open_components()` retains the full stack for `write` / `search` /
  `reindex` which genuinely need vector search. Eliminates HuggingFace
  network access during governance operations. ([M2])
- **`GovernancePipeline.run`** now correctly unpacks `(MemoryItem, body)`
  tuples from `iter_all()`. Was treating tuples as items, crashing with
  `AttributeError: 'tuple' object has no attribute 'id'` on the first
  duplicate check.
- **`ROADMAP.md`** rewritten — collapsed two competing milestone numbering
  systems into separate M1–M7 implementation track and PL1–PL6 public-launch
  track. Removed the M2 "Playbook namespace alpha done" fiction (no playbook
  module existed in the codebase). Source of truth pointer corrected to the
  2026-05-19 1011-line tech spec.
- **`STRATEGY.md`** rewritten with honest 16-vendor matrix:
  - **rohitg00/agentmemory** correctly labeled as red-zone threat
    (17.7k★, 53 MCP tools, full audit + drift + evolve trio) instead of
    one-line dismissal.
  - **Tencent/TencentDB-Agent-Memory** correctly described as closest
    design twin (4.1k★ MIT, local-first, SQLite+sqlite-vec+Markdown,
    4-layer pyramid) instead of "top-down enterprise SaaS".
  - **OpenViking** "deep dive pending" replaced with actual research.
  - Differentiation thesis narrowed from "5 features no one else has" to
    "4-feature combination no one else hits" — md-as-source-of-truth ×
    防进-gate × MCP-neutral × md-interop adapters.
  - **Honesty log** section added documenting the falsified claim
    ("none of 14 do all three") so future readers see the correction trail.
- **`adapter` test suite** rewritten in `tests/unit/test_adapters.py` —
  hardcoded "every adapter returns a non-empty string" assertions removed
  (they were the exact tests that hid the stub behavior). New tests:
  registry/config validity across all 12; explicit
  `pytest.raises(NotImplementedError)` for each of the 10 WIP adapters;
  real install integration tests for claude_code + codex against
  monkeypatched HOME.

### Fixed

- **187 real brain-pool items now parse end-to-end** under the v1 schema.
  The pre-v1.1 schema rejected items with CJK ids, items with `+` in id,
  items with `refs.tags=[...]`, and items whose YAML titles had unescaped
  embedded quotes. Combined effect made `govern` / `anti-drift` / `evolve`
  100% non-functional on any pool with historical data. Now: full pipeline
  completes (660 fuzzy duplicates + 12 low-quality items detected, 8 evolve
  proposals all `audit-approved`).
- **`EvolveEngine` audit gate** was rejecting every proposal due to the
  API mismatch above. Now `audit-blocked=0` on the real brain pool —
  no more silent 100% blockage hidden by exception suppression. ([B4])
- **CI "167/167 v0.5 conformance" claim** was unverifiable — the test
  skipped when the local brain dir was missing, which is always the case
  on CI. Replaced with a fixture-based test that runs unconditionally,
  plus an opt-in real-brain sweep gated on `LOCAL_BRAIN_CONFORMANCE=1`.
  ([B6])

### Removed

- Silent no-op `install()` stubs in 10 of the 12 adapter files (replaced
  with explicit `NotImplementedError` via `WIPAdapter`).
- Pre-existing claim that the M2 Playbook namespace was alpha-shipped
  (no implementation existed). Now folded into `decision` + `artifact`
  types per the YAGNI list.

### Security

- **Skill audit pre-gate** integrated into `EvolveEngine._audit_gate`
  with fail-closed semantics: any proposal whose output_preview triggers
  a critical or high severity finding is blocked before write. Lower
  severities (medium / low) are advisory and allowed. ([B4])

### Performance

- **`_check_duplicates`** time complexity dropped from O(N²) global to
  O(N) sha256 pass + O(sum M_k²) within-project jaccard. On the live
  187-item pool this cut the full govern run from 7s to under 2s. ([M4])

### Documentation

- New `CHANGELOG.md` (this file).
- Rewrites of `ROADMAP.md` and `STRATEGY.md` covered above.
- Hook script paths in adapter install messages now show the absolute
  install location so users can audit before running.

### Migration notes

- **No data migration required.** v1.1 widens the schema, never narrows it.
  Items written by v1.0.x continue to load unchanged.
- **MCP clients**: the 3 new tools (`delete_memory`, `audit_skill`,
  `audit_outbound`) are additive. Existing tool calls keep working.
- **`memory anti-drift`** default behavior unchanged; pass `--check-urls`
  to opt into HTTP HEAD probing of URLs (slow, requires network).
- **Adapter callers**: code that called `XxxAdapter().install()` on a WIP
  adapter and ignored the return value will now raise `NotImplementedError`.
  This is intentional — see Removed section.

[B1]: https://github.com/agent-memory-hub/agent-memory-hub/issues/B1
[B2]: https://github.com/agent-memory-hub/agent-memory-hub/issues/B2
[B3]: https://github.com/agent-memory-hub/agent-memory-hub/issues/B3
[B4]: https://github.com/agent-memory-hub/agent-memory-hub/issues/B4
[B5]: https://github.com/agent-memory-hub/agent-memory-hub/issues/B5
[B6]: https://github.com/agent-memory-hub/agent-memory-hub/issues/B6
[M1]: https://github.com/agent-memory-hub/agent-memory-hub/issues/M1
[M2]: https://github.com/agent-memory-hub/agent-memory-hub/issues/M2
[M3]: https://github.com/agent-memory-hub/agent-memory-hub/issues/M3
[M4]: https://github.com/agent-memory-hub/agent-memory-hub/issues/M4
[M5]: https://github.com/agent-memory-hub/agent-memory-hub/issues/M5

## [1.0.0] — 2026-05-19

Initial v1 release. M1–M4 implementation milestones merged to `main`.

### Added

- **M1** — Python core: Pydantic v2 schema, typer CLI, FastMCP server,
  SQLite FTS5 + sqlite-vec hybrid retrieval (BM25 + vector RRF).
- **M2** — Skill Audit Engine: 30+ builtin rules, scanner, audit CLI,
  30+ malicious sample tests, outbound event log.
- **M3** — Anti-drift + Governance: dedup pipeline (jaccard), drift detector
  (contradiction / staleness / citation-rot / drift-cluster), 10K/100K bench.
- **M4** — Self-evolve engine + audit gate + 12 agent adapter
  **plugin architecture** (real-hook installation deferred to v1.1).
- L3 RBAC interfaces reserved (`tenant_id` + `auth_context` fields).
- STRATEGY.md with 14-competitor matrix (later expanded to 16 in v1.0.1).

[Unreleased]: https://github.com/agent-memory-hub/agent-memory-hub/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/agent-memory-hub/agent-memory-hub/compare/v1.0.1...v1.1.0
[1.0.1]: https://github.com/agent-memory-hub/agent-memory-hub/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/agent-memory-hub/agent-memory-hub/releases/tag/v1.0.0
