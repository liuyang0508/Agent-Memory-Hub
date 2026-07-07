# Agent Memory Hub Current-State Gap Matrix

Date: 2026-06-09
Owner: Codex
Status: historical evidence baseline, not a completion claim

Update note 2026-06-18: MCP operation surface is now 27 tools after adding the
conversation evidence tier. Use `docs/architecture.md` and
`agent_brain/memory/evidence/` for current raw conversation evidence architecture.

## Scope

This audit records the current repository state for the next evolution pass. It
does not claim the whole Agent Memory Hub vision is complete. It exists to keep
future work anchored to code, tests, and current documentation instead of old
conversation memory or stale launch drafts.

## Evidence Commands

Run from `<repo>`:

- `git status --short --branch`
  - Result: `## main`
- `.venv/bin/python -m agent_brain.cli --help`
  - Result: CLI imports and lists the current command surface.
- `python -m agent_brain.cli adapter list --format json`
  - Result: 16 adapter records; 15 `install-ready`, 1 `wip`; capability JSON includes runtime observation and verified-gate fields.
- `find agent_brain -type f -name '*.py' -print | xargs wc -l | sort -nr | head -30`
  - Result: core package total is 19,263 Python lines; largest files listed below.
- `python -m pytest tests/unit/test_adapters.py tests/unit/test_cli_adapter.py tests/unit/test_adapter_capabilities.py tests/unit/test_web_api.py::TestAdapterCapabilitiesAPI::test_adapter_capabilities_uses_truth_contract_records tests/unit/test_docs_truth_contract.py tests/conformance/test_web_surface_lock.py -q`
  - Result: `126 passed`.

## Adapter Truth Matrix

Current source of truth: `memory adapter list --format json`.
The JSON surface now includes `evidence_paths`, `evidence_level`,
`runtime_observed`, `runtime_event_count`, `last_runtime_event`, `verified`,
`verification_status`, and `verification_blockers`; an adapter must not be
promoted based on prose, config presence, or runtime events alone.

| Adapter | Level | Modes | Evidence | Current limitation |
|---|---|---|---|---|
| `aider` | `install-ready` | file | `tests/unit/test_adapters.py`, `tests/unit/test_cli_adapter.py`, `agent_brain/agent_integrations/aider.py`, `agent_brain/agent_integrations/aider_diagnostics.py` | Config doctor implemented; local Aider config missing; runtime not verified |
| `claude_code` | `install-ready` | command, hook, mcp | `tests/unit/test_adapters.py`, `tests/unit/test_cli_adapter.py`, `agent_brain/agent_integrations/claude_code.py`, `agent_brain/agent_integrations/claude_code_diagnostics.py` | Real Claude Code config doctor passed; runtime hook event not recorded |
| `cline` | `install-ready` | mcp | `tests/unit/test_adapters.py`, `tests/unit/test_cli_adapter.py`, `agent_brain/agent_integrations/cline.py`, `agent_brain/agent_integrations/mcp_config_diagnostics.py` | Config doctor implemented; local Cline MCP config missing; runtime not verified |
| `codex` | `install-ready` | file, hook, mcp | `tests/unit/test_adapters.py`, `tests/unit/test_cli_adapter.py`, `agent_brain/agent_integrations/codex.py`, `agent_brain/agent_integrations/codex_hooks.py`, `agent_brain/agent_integrations/codex_diagnostics.py` | Real Codex config doctor passed; hook runtime event not yet observed |
| `continue_dev` | `install-ready` | mcp | `tests/unit/test_adapters.py`, `tests/unit/test_cli_adapter.py`, `agent_brain/agent_integrations/continue_dev.py` | Official Continue global config.yaml MCP path implemented; real runtime not verified |
| `cursor` | `install-ready` | mcp, hook | `tests/unit/test_adapters.py`, `tests/unit/test_cli_adapter.py`, `agent_brain/agent_integrations/cursor.py`, `agent_brain/agent_integrations/mcp_config_diagnostics.py` | Config doctor implemented; local Cursor MCP config malformed; runtime not verified |
| `github_copilot` | `install-ready` | file | `tests/unit/test_adapters.py`, `tests/unit/test_cli_adapter.py`, `agent_brain/agent_integrations/github_copilot.py` | Repository-level copilot-instructions.md installer implemented; real Copilot runtime not verified |
| `hermes_agent` | `install-ready` | mcp | `tests/unit/test_adapters.py`, `agent_brain/agent_integrations/hermes/provider.py`, `https://hermes-agent.nousresearch.com/docs/user-guide/features/tool-calling-mcp` | Hermes MCP server config installed; Hermes provider listing/upstream runtime not verified |
| `openclaw` | `install-ready` | command, mcp | `tests/unit/test_adapters.py`, `https://docs.openclaw.ai/cli/mcp` | Official OpenClaw MCP registry CLI path implemented; real runtime not verified |
| `openhuman` | `install-ready` | file | `tests/unit/test_adapters.py`, `https://github.com/tinyhumansai/openhuman` | Agentmemory backend config bridge implemented; real OpenHuman runtime not verified |
| `opensquilla` | `install-ready` | file, mcp | `tests/unit/test_adapters.py`, `https://github.com/opensquilla/opensquilla` | OpenSquilla config.toml MCP entry implemented; real runtime not verified |
| `qoder` | `install-ready` | file, hook | `tests/unit/test_adapters.py`, `tests/unit/test_cli_adapter.py`, `agent_brain/agent_integrations/qoder.py`, `agent_brain/agent_integrations/qoder_diagnostics.py` | Official Qoder hooks and awareness path implemented; AMH context effectiveness not verified |
| `qoder_work` | `verified` | file, hook | `tests/unit/test_adapters.py`, `tests/unit/test_cli_adapter.py`, `https://docs.qoder.com/extensions/hooks`, real QoderWork CLI smoke + AMH injection cohort `<injection-cohort-id>` | QoderWork hooks and awareness/main path implemented; model observed `<agent_brain>` in the same session that AMH recorded an injection cohort |
| `wukong` | `install-ready` | file, hook, mcp | `tests/unit/test_adapters.py`, `tests/unit/test_adapter_robustness_p36.py`, `tests/unit/test_cli_adapter.py`, `agent_brain/agent_integrations/wukong.py`, `<rewinddesktop-repo>/tauri-app/src-tauri/src/mcp/config.rs` | RewindDesktop MCP config path and Wukong context writer implemented; runtime not verified |
| `aone_copilot` | `install-ready` | file | `tests/unit/test_adapters.py`, `/Applications/IntelliJ IDEA Ultimate.app` | IntelliJ IDEA plugin awareness sidecar implemented; plugin runtime/tool bridge not verified |
| `mulerun` | `wip` | file | none | Install path not implemented |

Implication: public docs may say Qoder has an install-ready hooks path, but must
not say Qoder is verified or that Qoder MCP auto-config is implemented. Qoder
Work is now verified through real-client context-effectiveness evidence; Aone Copilot
still needs real-client runtime evidence.
MuleRun remains planned until install, uninstall, malformed-config, and idempotence tests exist.

## Public Surfaces

| Surface | Current state | Lock / evidence |
|---|---|---|
| CLI | Typer app with CRUD, lifecycle, insight, IO, adapter, audit, govern, tier, entity commands | `.venv/bin/python -m agent_brain.cli --help` |
| MCP | Historical baseline: 25 operation tools at the time of this audit. Current count is locked in `tests/conformance/test_public_surface_lock.py`. | `tests/conformance/test_public_surface_lock.py` |
| Markdown store | `ItemsStore` is source of truth; malformed items are skipped and recorded in `last_scan` | `tests/unit/test_items_store.py` |
| Shell shims | `agent_runtime_kit/tools/write-memory.sh` buffers pending writes; `search-memory.sh` defaults embedding offline | `tests/unit/test_write_shim_fallback.py` |
| Web admin | FastAPI route split exists; `web/api/routes/items.py` is now a thin mount-only router; adapter capability API exposes the truth-contract records | route tests exist; visual browser smoke not yet recorded |
| Adapter matrix | Capability records exist and CLI JSON exposes `support_level`, `integration_modes`, `limitations`, runtime observation fields, and verified-gate blockers | `tests/unit/test_adapter_capabilities.py`, `tests/unit/test_cli_adapter.py`, `tests/unit/test_adapter_runtime_events.py` |

## Architecture And Directory Findings

### Current ownership map

| Path | Responsibility |
|---|---|
| `agent_runtime_kit/` | Agent-facing bootstrap layer: hooks, MCP launcher, shell shims, discipline docs |
| `agent_brain/core/` | Markdown store, index, retrieval, write funnel, pending queue, doctor |
| `agent_brain/cli/` | Typer command surface split by command family |
| `agent_brain/mcp/` | FastMCP server and tiered MCP tools |
| `agent_brain/adapters/` | Agent integration registry, install paths, diagnostics |
| `agent_brain/governance/` | drift, dedup, tiering, conflict and consolidation logic |
| `agent_brain/evolve/` | self-evolve proposals, dreaming, crystallization |
| `web/` | web admin app, API routes, templates, static assets |
| `agent_brain/harvest/` | offline-first transcript harvesting and optional enrichment |
| `agent_brain/hermes/` | Hermes provider integration surface |
| `agent_brain/integrations/` | external integrations such as Obsidian |

### Large-file risks

| File | Lines | Risk |
|---|---:|---|
| `web/api/routes/items.py` | 27 | Thin mount-only router for item route modules |
| `web/api/routes/item_crud.py` | 166 | Focused owner for item list/detail/delete/patch/create/clone HTTP route orchestration |
| `web/api/routes/item_listing.py` | 72 | Focused owner for `/api/items` visible-item filtering, sorting, pagination, and row serialization |
| `web/api/routes/item_payloads.py` | 101 | Focused owner for item CRUD request models, pinned summary payloads, patch update extraction, create records, and clone records |
| `web/api/routes/item_batch.py` | 158 | Focused owner for batch delete/confirm/tag and merge item endpoints |
| `web/api/routes/item_history.py` | 38 | Focused owner for item snapshot history endpoints |
| `web/api/routes/item_maintenance.py` | 134 | Focused owner for decay status, Obsidian export/import, and reindex endpoints |
| `web/api/routes/item_exports.py` | 138 | Focused owner for JSON, CSV, and Markdown ZIP export endpoints |
| `web/api/routes/item_imports.py` | 90 | Focused owner for JSON import endpoint and import strategies |
| `web/api/routes/item_search.py` | 139 | Focused owner for semantic search, related-item search, and full-text search endpoints |
| `web/api/routes/item_metadata.py` | 94 | Focused owner for project/tag listing and tag mutation endpoints |
| `web/api/routes/item_mutations.py` | 131 | Focused owner for touch, body update, batch update, and pin/unpin endpoints |
| `web/api/routes/health.py` | 127 | Focused owner for Web health/version/routes endpoints and health-detail route orchestration |
| `web/health_payloads.py` | 80 | Focused owner for pure Web health-detail governance/drift payload assembly and grade calculation |
| `web/api/routes/graph.py` | 117 | Focused owner for graph/link HTTP routes and request/response boundary orchestration |
| `web/graph_payload.py` | 95 | Focused owner for visible graph nodes, explicit/frontmatter/wiki-link edge assembly, dedupe, and debug payloads |
| `web/_base.py` | 162 | Web shared infrastructure facade for component/state caches, webhook triggering, audit/snapshot helpers, mutation primitive, and compatibility exports |
| `web/live_events.py` | 87 | Focused owner for tenant-scoped SSE/WebSocket subscriber state, visibility filtering, fanout, and dead socket reaping |
| `web/visibility.py` | 38 | Focused owner for tenant visibility checks, path-safe item ids, and visible item loading |
| `agent_brain/hermes/provider.py` | 404 | Thin compatibility layer for Hermes public tool wrappers, runtime component delegation, and server entrypoint |
| `agent_brain/hermes/provider_registry.py` | 53 | Focused owner for canonical Hermes tool names and tool tuple assembly |
| `agent_brain/hermes/provider_server.py` | 24 | Focused owner for Hermes FastMCP tool registration and standalone provider server startup |
| `agent_brain/hermes/components.py` | 22 | Focused owner for Hermes provider runtime ItemsStore, HubIndex, embedder, and Retriever construction |
| `agent_brain/hermes/core_tools.py` | 157 | Focused owner for search orchestration, profile/context base payloads, conclude handoff helper, and compatibility re-exports |
| `agent_brain/hermes/remember.py` | 82 | Focused owner for Hermes remember audit gate, MemoryItem construction, write/index upsert, and related-memory suggestions |
| `agent_brain/hermes/search.py` | 29 | Focused owner for Hermes `hub_search` hit-to-response formatting and snippet shaping |
| `agent_brain/hermes/profile.py` | 41 | Focused owner for optional Hermes profile preference enrichment and warning-level failure reporting |
| `agent_brain/hermes/related.py` | 42 | Focused owner for post-write related memory suggestions and warning-level failure reporting |
| `agent_brain/hermes/context.py` | 43 | Focused owner for optional Hermes context active-recall payloads and warning-level failure reporting |
| `agent_brain/hermes/governance_tools.py` | 169 | Focused owner for drift detection, evolve proposals, governance summaries, health stats, and tag suggestion Hermes tool implementations |
| `agent_brain/hermes/import_export_tools.py` | 136 | Focused owner for import, Obsidian export/import, and garbage-collection Hermes tool implementations |
| `agent_brain/hermes/item_tools.py` | 180 | Focused owner for graph/link/read/delete/list/update/batch confirm Hermes tool implementations |
| `agent_brain/integrations/obsidian.py` | 165 | Focused owner for Obsidian vault sync orchestration, file-level export/write decisions, overwrite handling, and import indexing |
| `agent_brain/integrations/obsidian_export.py` | 61 | Focused owner for pure Obsidian export frontmatter and markdown rendering helpers |
| `agent_brain/integrations/obsidian_import.py` | 90 | Focused owner for pure Obsidian markdown import parsing, trailer stripping, frontmatter whitelist restoration, and MemoryItem reconstruction |
| `agent_brain/governance/drift.py` | 148 | Focused owner for drift detection orchestration, citation-rot assembly, helper delegation, and optional confidence feedback |
| `agent_brain/governance/drift_contradictions.py` | 105 | Focused owner for heuristic and semantic decision-contradiction finding within project groups |
| `agent_brain/governance/drift_clusters.py` | 35 | Focused owner for project-level drift cluster finding assembly and consolidation-review evidence |
| `agent_brain/governance/drift_types.py` | 42 | Shared drift detection enums, findings, and report value objects |
| `agent_brain/governance/drift_patterns.py` | 61 | Focused owner for decision-pattern extraction, tool-name heuristics, and contradiction pattern comparison |
| `agent_brain/governance/drift_staleness.py` | 32 | Focused owner for timezone-aware staleness finding detection with injectable current time |
| `agent_brain/governance/drift_citations.py` | 40 | Focused owner for citation URL extraction and HTTP rot probing helpers |
| `agent_brain/governance/consolidation.py` | 111 | Focused owner for L0 fact consolidation group detection, store-level dry-run/apply orchestration, and compatibility re-exports |
| `agent_brain/governance/consolidation_builder.py` | 107 | Focused owner for building L1 consolidated MemoryItem ids, metadata, body templates, provenance, and sensitivity/confidence aggregation |
| `agent_brain/governance/consolidation_types.py` | 38 | Shared consolidation value objects for source groups, reports, item-body pairs, and summarizer callables |
| `agent_brain/evolve/preference.py` | 171 | Focused owner for statistical preference inference over tags, item types, co-occurrence, projects, and decision patterns |
| `agent_brain/evolve/preference_types.py` | 32 | Shared inferred-preference value objects for signals and profiles |
| `agent_brain/evolve/preference_format.py` | 30 | Focused owner for rendering inferred preference profiles into agent-context text |
| `agent_brain/cli/commands/lifecycle.py` | 181 | Focused owner for decay status and anti-drift lifecycle diagnostics |
| `agent_brain/cli/commands/evolution.py` | 167 | Focused owner for `consolidate`, `evolve`, and `dream` CLI evolution commands |
| `agent_brain/cli/commands/crud.py` | 162 | Focused owner for write, update, delete, and confirm CLI item mutation commands |
| `agent_brain/cli/commands/links.py` | 33 | Focused owner for link and unlink CLI knowledge-graph mutation commands |
| `agent_brain/cli/crud_updates.py` | 25 | Focused owner for pure CLI update command field construction and add-tags merging |
| `agent_brain/cli/commands/query.py` | 146 | Focused owner for read, search, list-recent, and tag-suggest CLI query commands |
| `agent_brain/cli/commands/batch.py` | 74 | Focused owner for `batch-confirm` and `batch-archive` item mutation commands |
| `agent_brain/cli/commands/doctor.py` | 126 | Focused owner for `memory doctor` command options, validation, and online/offline presenter dispatch |
| `agent_brain/cli/doctor_offline.py` | 139 | Focused owner for `memory doctor --offline` capability reporting and malformed-item repair/restore presentation |
| `agent_brain/cli/_shared.py` | 112 | Shared CLI imports, module-level state, component openers, id resolution, enum parsing, and compatibility exports |
| `agent_brain/cli/commands/maintenance.py` | 154 | Focused owner for reindex, verify, sync-pending, harvest, and migrate storage maintenance command presentation |
| `agent_brain/cli/commands/gc.py` | 52 | Focused owner for top-level `gc` stale auto-captured item and session-flag cleanup command |
| `agent_brain/cli/commands/index_maintenance.py` | 69 | Focused owner for reindex, index drift inspection, and repair helper logic used by maintenance commands |
| `agent_brain/harvest/enricher.py` | 162 | Focused owner for optional LLM enrichment availability, prompting, pool candidate selection, and best-effort item updates |
| `agent_brain/harvest/enrichment_updates.py` | 16 | Focused owner for pure LLM enrichment result sanitization into frontmatter updates |
| `agent_brain/cli/commands/subapps.py` | 119 | Focused owner for governance and entity subcommand groups |
| `agent_brain/cli/commands/audit.py` | 90 | Focused owner for `memory audit skill` and `memory audit outbound` commands |
| `agent_brain/cli/commands/tier.py` | 64 | Focused owner for `memory tier show` and `memory tier rebalance` commands |
| `agent_brain/cli/commands/adapters.py` | 157 | Focused owner for adapter list/install/uninstall/doctor commands |
| `agent_brain/cli/commands/status.py` | 161 | Focused owner for top-level `stats` and `health` CLI orchestration, Rich table rendering, and command exit behavior |
| `agent_brain/cli/status_payloads.py` | 45 | Focused owner for pure JSON payload assembly for status/statistics CLI output |
| `agent_brain/cli/commands/insight.py` | 139 | Focused owner for top-level version, inspect, serve, and resume brief commands |
| `agent_brain/cli/commands/graph.py` | 81 | Focused owner for top-level knowledge graph inspection command output in table or JSON format |
| `agent_brain/cli/commands/io.py` | 162 | Focused owner for export, import, and Obsidian import/export CLI commands |
| `agent_brain/cli/commands/api_docs.py` | 86 | Focused owner for top-level `api-docs` endpoint table command and endpoint catalog |
| `agent_brain/mcp/tools/core.py` | 41 | Stable core MCP tier registration/re-export module for canonical tool ordering and compatibility exports |
| `agent_brain/mcp/tools/search_tools.py` | 76 | Focused owner for `search_memory` and `tag_suggest` MCP query implementations |
| `agent_brain/mcp/tools/mutation_tools.py` | 171 | Focused owner for `write_memory`, `delete_memory`, `update_memory`, and `confirm_memory` MCP state-changing orchestration |
| `agent_brain/mcp/tools/mutation_enrichment.py` | 56 | Focused owner for best-effort post-write related-memory and tag-suggestion enrichment |
| `agent_brain/mcp/tools/mutation_updates.py` | 38 | Focused owner for pure MCP `update_memory` field construction, type validation, and type-to-decay-class synchronization |
| `agent_brain/mcp/tools/read_tools.py` | 66 | Focused owner for `read_memory`, `list_recent`, and `brief_memory` MCP read-path implementations |
| `agent_brain/mcp/tools/status.py` | 65 | Focused owner for `brain_stats` MCP observability and health-score implementation |
| `agent_brain/mcp/tools/governance.py` | 147 | Focused owner for MCP governance-tier audit, drift, govern wrappers, registration, and compatibility re-exports |
| `agent_brain/mcp/tools/governance_batch.py` | 78 | Focused owner for MCP batch confirm/archive mutation helpers, confidence clamping, archive moves, index deletion, and item-id safety delegation |
| `agent_brain/mcp/tools/io.py` | 162 | Focused owner for MCP import/export/Obsidian/gc tool orchestration and FastMCP registration |
| `agent_brain/mcp/tools/io_export.py` | 32 | Focused owner for pure MCP export filtering and JSON/JSONL payload assembly |
| `web/api/routes/governance.py` | 135 | Web governance router for stats, gc, evolve, and mounting audit/webhook/activity subroutes |
| `web/api/routes/governance_activity.py` | 67 | Focused owner for Web activity timeline and recent item aggregation route |
| `web/api/routes/governance_audit.py` | 85 | Focused owner for Web audit log, audit scan, and outbound audit routes |
| `web/api/routes/governance_webhooks.py` | 45 | Focused owner for Web webhook list/add/remove routes |
| `web/state_store.py` | 157 | SQLite-backed web runtime state store facade for audit log, item snapshots, and manual link/webhook helper delegation |
| `web/state_links.py` | 89 | Focused owner for SQLite-backed manual item-link existence, create, list, and delete persistence helpers |
| `web/state_webhooks.py` | 40 | Focused owner for SQLite-backed webhook list/add/remove persistence helpers |
| `web/state_storage.py` | 54 | Focused owner for web-state SQLite connection setup, WAL/busy-timeout pragmas, and schema creation |
| `web/auth.py` | 160 | Web auth facade for password hashing, user creation/authentication, JWT encode/decode, API-key lookup, and FastAPI current-user dependency |
| `web/auth_storage.py` | 56 | Focused owner for brain-dir-scoped web secret persistence and atomic users.yaml load/save helpers |
| `agent_brain/core/items_store.py` | 174 | Markdown item filesystem store for item id generation, recursive scans, append-only writes, frontmatter updates, durable unlink, and malformed-item skip accounting |
| `agent_brain/core/item_markdown.py` | 34 | Pure MemoryItem Markdown/frontmatter parse and render helpers, including BOM/line-ending normalization and historical YAML quirk compatibility |
| `agent_brain/core/write_service.py` | 141 | Write funnel orchestration for audit gating, markdown source-of-truth writes, best-effort indexing, and dirty-index degradation |
| `agent_brain/core/write_types.py` | 19 | Shared write result value object for write funnel callers |
| `agent_brain/spec/memory_item.py` | 148 | MemoryItem schema owner for pydantic models, schema compatibility coercion, id validation, and retention auto-fill |
| `agent_brain/spec/memory_enums.py` | 67 | Focused owner for memory type, abstraction, decay, sensitivity enums, and decay-class mappings |
| `agent_brain/core/index.py` | 163 | HubIndex facade for connection lifecycle, FTS search/text fetch, and graph/metadata/vector/writer/schema delegates; connection allows web cache cross-thread reuse |
| `agent_brain/core/index_schema.py` | 96 | Focused owner for SQLite schema creation, index migrations, and CJK FTS segmentation |
| `agent_brain/core/index_writer.py` | 87 | Focused owner for item upsert/delete writes across items_meta, items_fts, items_vec, and refs_graph |
| `agent_brain/core/vector_index.py` | 64 | Focused owner for items_vec embedding upsert/delete/search/fetch operations |
| `agent_brain/core/index_types.py` | 13 | Shared index value objects such as `Hit` |
| `agent_brain/core/metadata_index.py` | 124 | Focused owner for items_meta confidence, access, tier, filter, and search-metadata operations |
| `agent_brain/core/graph_index.py` | 65 | Focused owner for refs_graph edge mutation, lookup, and neighbor traversal |
| `agent_brain/core/retrieval.py` | 198 | Retrieval orchestration, query construction/filtering, graph/MMR/rerank/decay/access/status/tag/runtime helper delegation |
| `agent_brain/core/retrieval_graph.py` | 39 | Focused owner for graph-neighbor expansion of retrieval candidates |
| `agent_brain/core/retrieval_budget.py` | 38 | Focused owner for token estimation and tiered read-context packing within a token budget |
| `agent_brain/core/retrieval_status.py` | 118 | Focused owner for stale-status risk query detection and current handoff/status supplement/boost strategy |
| `agent_brain/core/retrieval_tags.py` | 33 | Focused owner for similar-item tag suggestion based on vector neighbors and indexed tag metadata |
| `agent_brain/core/retrieval_runtime.py` | 83 | Focused owner for adapter-specific runtime-evidence query detection and boost strategy |
| `agent_brain/core/retrieval_access.py` | 43 | Focused owner for retrieval access recording, optional confidence reinforcement, and warning-level failure reporting |
| `agent_brain/core/retrieval_fusion.py` | 47 | Focused owner for reciprocal-rank fusion of BM25 and vector hit lists |
| `agent_brain/core/query_expansion.py` | 37 | Focused compatibility owner for FTS query construction and legacy query helper re-exports |
| `agent_brain/core/query_synonyms.py` | 111 | Focused owner for query synonym lookup, CJK word extraction, and synonym expansion |
| `agent_brain/core/query_tokens.py` | 26 | Focused owner for mixed CJK/ASCII FTS-compatible tokenization |
| `agent_brain/core/retrieval_rerank.py` | 83 | Focused owner for cross-encoder model loading, enablement, sigmoid normalization, and candidate reranking |
| `agent_brain/core/retrieval_decay.py` | 62 | Focused owner for retention-factor and confidence × retention decay scoring |
| `agent_brain/core/retrieval_mmr.py` | 67 | Focused owner for Maximal Marginal Relevance diversity reranking |
| `agent_brain/core/retrieval_types.py` | 14 | Shared retrieval result data types |
| `agent_brain/audit/scanner.py` | 160 | Focused owner for file/directory/in-memory audit scanning and builtin write-path audit assembly |
| `agent_brain/audit/report.py` | 93 | Shared audit finding/report value objects plus dict and Markdown rendering |
| `agent_brain/governance/compressor.py` | 181 | Semantic compression coordinator for LLM/mechanical compression with candidate/type/writeback delegation |
| `agent_brain/governance/compressor_candidates.py` | 60 | Focused owner for compression candidate discovery by project/tag eligibility |
| `agent_brain/governance/compressor_types.py` | 40 | Shared compression candidate/report value objects |
| `agent_brain/governance/compressor_writeback.py` | 60 | Focused owner for compressed L2 item construction and observable source supersession updates |
| `agent_brain/governance/conflict_resolver.py` | 118 | Conflict auto-resolution coordinator for drift contradiction iteration, strategy selection, action dispatch, report assembly, and resolution logging |
| `agent_brain/governance/conflict_actions.py` | 113 | Focused owner for KEEP_NEWER, KEEP_HIGHER_CONFIDENCE, MARK_CONTESTED, and MERGE_RESOLUTION write actions |
| `agent_brain/governance/conflict_types.py` | 46 | Shared conflict resolution strategy enum, resolution record, and report value objects |
| `agent_brain/governance/conflict_strategy.py` | 32 | Focused owner for conflict resolution strategy selection from confidence and item attributes |
| `agent_brain/governance/tiering.py` | 142 | Focused owner for hot/warm/cold tier value objects, age/confidence classification, item tiering, and rebalance orchestration |
| `agent_brain/governance/tier_scan.py` | 32 | Focused owner for recursive markdown tier scanning and tier distribution counting |
| `agent_brain/governance/pipeline.py` | 143 | Governance pipeline orchestration for duplicate/noise/TTL/quality checks and report issue counting |
| `agent_brain/governance/duplicates.py` | 88 | Focused owner for exact fingerprint and project-partitioned Jaccard duplicate detection |
| `agent_brain/governance/pipeline_types.py` | 36 | Shared governance issue and report value objects re-exported by the pipeline module |
| `agent_brain/evolve/engine.py` | 140 | Focused owner for evolve pipeline orchestration, audit gating, report assembly, and executor delegation |
| `agent_brain/evolve/analyzers.py` | 153 | Focused owner for proposal analyzer orchestration plus consolidation, crystallization, skill-synthesis, and delegated promotion/skill/archive analysis |
| `agent_brain/evolve/dreaming.py` | 194 | Focused owner for dreaming cycle orchestration, daemon lifecycle, phase sequencing, and reporting |
| `agent_brain/evolve/dream_phases.py` | 21 | Focused owner for reusable dreaming phase error isolation and labeled report error collection |
| `agent_brain/evolve/dream_skill_synthesis.py` | 59 | Focused owner for mature-policy skill synthesis grouping, existing-skill lookup, and per-group error collection |
| `agent_brain/evolve/archive_analysis.py` | 88 | Focused owner for archive proposal analysis, expired-signal detection, and decay-aware archive candidates with injectable current time |
| `agent_brain/evolve/promotion_analysis.py` | 44 | Focused owner for episode-to-decision/fact promotion proposal detection and preview wiring |
| `agent_brain/evolve/skill_generation_analysis.py` | 49 | Focused owner for repeated episode/decision pattern grouping and skill-generation proposal construction |
| `agent_brain/evolve/proposal_previews.py` | 73 | Focused owner for human-readable evolve proposal preview builders |
| `agent_brain/evolve/executors.py` | 156 | Focused owner for applying approved evolve proposals by action via an executor registry |
| `agent_brain/reasoning/causal_chain.py` | 197 | Focused owner for cross-session causal trace orchestration with explicit, implicit, related-decision, scoring, and type helper delegation |
| `agent_brain/reasoning/causal_inference.py` | 85 | Focused owner for implicit temporal cause/effect candidate discovery and threshold filtering |
| `agent_brain/reasoning/causal_explicit.py` | 63 | Focused owner for explicit causal candidate extraction from graph refs, item refs, and evolved-from metadata |
| `agent_brain/reasoning/causal_related.py` | 45 | Focused owner for related decision discovery, decision filtering, scoring, sorting, and result limiting |
| `agent_brain/reasoning/causal_types.py` | 62 | Shared causal reasoning constants and value objects for links, traces, and candidates |
| `agent_brain/reasoning/causal_scoring.py` | 76 | Focused owner for temporal/project/tag/type/semantic causal likelihood scoring |
| `agent_brain/client/sdk.py` | 194 | MemoryClient facade for write/search/read/feedback/confirm/stats/brief operations with direct ClientComponents delegation and no redundant private getter layer |
| `agent_brain/client/components.py` | 66 | Focused owner for SDK lazy local component cache: ItemsStore, HubIndex, embedder, Retriever, and ConfidenceFeedback |
| `agent_brain/client/write_index.py` | 34 | Focused owner for best-effort indexing of SDK-written items and warning-level failure reporting |
| `agent_brain/client/query.py` | 126 | Focused owner for SDK search result payloads, search/read/list-recent conversions, and brief payload assembly |
| `agent_brain/client/stats.py` | 50 | Focused owner for SDK stats payload assembly from observability, governance, and drift reports |
| `agent_brain/adapters/claude_code.py` | 182 | Claude Code adapter install/uninstall/inject orchestration and doctor report assembly |
| `agent_brain/adapters/claude_code_diagnostics.py` | 92 | Focused owner for Claude Code settings hook and hook script diagnostic checks |
| `agent_brain/adapters/hook_config.py` | 111 | Shared JSON config read/write, hook ownership predicates, hook command update, and shell-token parsing for hook-based adapters |
| `agent_brain/adapters/codex.py` | 194 | Adapter install/uninstall orchestration for Codex; AGENTS block, hooks, sentinel/MCP editing, and diagnostics live in focused modules |
| `agent_brain/adapters/codex_agents.py` | 46 | Focused owner for Codex AGENTS.md discipline block rendering, install, and uninstall |
| `agent_brain/adapters/codex_hooks.py` | 82 | Focused owner for Codex hooks.json install, command repair, idempotency, and uninstall |
| `agent_brain/adapters/codex_config.py` | 144 | Focused owner for Codex sentinel block editing, JSON config IO, and MCP TOML section editing with compatibility re-exports |
| `agent_brain/adapters/codex_diagnostics.py` | 122 | Focused owner for Codex doctor report assembly, AGENTS block check, MCP server check, launcher check, and runtime-evidence delegation |
| `agent_brain/adapters/codex_hook_diagnostics.py` | 86 | Focused owner for Codex hooks.json and hook script diagnostic checks |
| `agent_brain/adapters/codex_hook_commands.py` | 22 | Compatibility export surface for Codex-style hook command helpers now owned by `hook_config.py` |
| `agent_brain/adapters/diagnostics.py` | 76 | Shared adapter diagnostic records, status aggregation, runtime evidence checks, and compatibility re-exports |
| `agent_brain/adapters/mcp_config_diagnostics.py` | 151 | Focused owner for JSON/YAML MCP config parsing, mcpServers lookup, and adapter doctor server diagnostics |
| `agent_brain/adapters/mcp_diagnostics.py` | 48 | Focused owner for shared MCP server field validation used by JSON and YAML adapter doctors |
| `agent_brain/adapters/qoder.py` | 170 | Focused owner for Qoder `settings.json` hook install/uninstall, context injection text, install instructions, and diagnostic orchestration |
| `agent_brain/adapters/qoder_diagnostics.py` | 94 | Focused owner for Qoder settings-hook and hook-script diagnostic checks |
| `agent_brain/adapters/github_copilot.py` | 171 | Focused owner for GitHub Copilot `.github/copilot-instructions.md` static instructions install/uninstall/doctor |
| `agent_brain/adapters/continue_dev.py` | 151 | Focused owner for Continue `~/.continue/config.yaml` MCP install/uninstall/doctor using documented `mcpServers` list entries |
| `agent_brain/adapters/aider.py` | 127 | Focused owner for Aider read-file directive install/uninstall, digest generation, context text, and adapter registration |
| `agent_brain/adapters/aider_config.py` | 31 | Focused owner for Aider YAML config read/write helpers with malformed YAML protection and atomic replace |
| `agent_brain/adapters/aider_diagnostics.py` | 66 | Focused owner for Aider read directive and brain digest diagnostic checks |
| `agent_brain/adapters/runtime_events.py` | 149 | Focused owner for adapter hook runtime event JSONL recording and capability summaries |

Next refactor should start with a surface-locked slice, not a broad directory
move. The first `web/api/routes/items.py` slices now own export behavior in
`item_exports.py`, import behavior in `item_imports.py`, and search behavior in
`item_search.py`, metadata/tag behavior in `item_metadata.py`, and tested item
mutation behavior in `item_mutations.py`; tested CRUD/batch behavior now lives
in `item_crud.py`; item snapshot history now lives in `item_history.py`; decay,
Obsidian, and reindex behavior now lives in `item_maintenance.py`. The next web
architecture candidate should move beyond `items.py` to another large route or
provider module. Hermes provider now has four focused implementation modules:
`core_tools.py` for core Hermes memory-provider tools, `item_tools.py` for
item utilities and mutation, `import_export_tools.py` for IO/gc, and
`governance_tools.py` for drift/evolve/governance/stats/tag suggestion.

## Competitive And Research Gaps

Current `STRATEGY.md` already records the honest competitor read: rohitg00 is the
red-zone threat; Tencent is the closest design twin; OpenViking tiered loading
is worth borrowing; HRR/Holographic is interesting but likely not the first
pragmatic axis.

Open gaps that need evidence before implementation:

- Hopfield / HRR / associative memory ideas need the existing concrete retrieval
  eval before touching production ranking. Fast synthetic runs now balance
  title, tag, project, type, multi-keyword, semantic-paraphrase, and
  linked-association categories. A small hand-labeled dogfooding fixture now
  covers stale-memory risk, false-friend adapter claims, runtime evidence
  ambiguity, semantic paraphrase, linked association, and regression memory, but
  paper-inspired features still need before/after deltas.
- OpenViking-style tiered loading maps naturally to hot/warm/cold and resume
  briefing, but needs a bounded design around token budgets and recency/quality.
- rohitg00-style lifecycle memory should be studied as a lifecycle model, not as
  a reason to copy their large flat tool surface.

## Memory Risk Findings

| Risk | Current mitigation | Remaining gap |
|---|---|---|
| Offline embedding stalls writes/search | `write-memory.sh` and `search-memory.sh` default to `MEMORY_HUB_EMBEDDING_OFFLINE=1` | Full semantic index rebuild remains an explicit online/local-model task |
| Malformed historical items pollute search output | `ItemsStore.iter_all` records skipped files in `last_scan`; `memory doctor --offline` reports skipped count; `memory doctor --offline --verbose` shows bounded file-level details; normal search no longer emits warning noise by default; `memory doctor --offline --repair-malformed` previews quarantine and `--apply` moves skipped files to `items/archived/malformed/` with reason files; `memory doctor --offline --restore-malformed <file>` validates one manually repaired archived file before restoring it to active items | Manual content editing remains human-owned; restore validates and moves repaired files but does not synthesize missing memory content |
| Stale memory/docs create false confidence | truth-contract tests now guard STRATEGY, ROADMAP, MCP examples, stale blog draft | Archive/blog/docs still need broader publication-readiness review |
| Retrieval can surface stale or similar-but-wrong memories | `benchmark_relevance.py --queries-file tests/fixtures/relevance/hand_labeled_queries.json` records hand-labeled dogfooding queries; status-risk queries now supplement and boost `signal` items tagged with both `handoff` and `status`; latest candidate moved `memory_risk` from top-10 miss to rank 1; `benchmark_relevance.py --ablation` now compares `bm25_only`, `vector_only`, `rrf`, `rrf_decay`, `rrf_graph`, `rrf_mmr`, and `rrf_context_firewall`, with token cost and stale hit rate | Fixture is still intentionally small and should grow with real failures; future ranking/query changes still need before/after deltas and category-level regression notes |
| Retrieval/maturity terminology drifts from implementation | `agent_brain/memory/governance/maturity_scoring.py` and `agent_brain/memory/recall/` record the live formulas, vector feature text (`locator + overview`), and retrieval stages; `memory govern maturity` gives dry-run/apply recommendations | We still need real-pool dogfooding snapshots over time to tune weights and thresholds |
| WIP adapters advertised as real | README/STRATEGY/MCP examples/dashboard copy now use `install-ready` / `docs-only` / `wip`; adapter JSON/table/web API expose evidence summaries; dashboard agent marquee renders from `/api/adapters/capabilities` with fallback; install-ready adapters now have read-only config doctors | Real runtime verification remains needed before any adapter can move to `verified` |

## Recommended Next Work

1. Repair or install local configs for `cursor`, `cline`, `aider`, and
   `wukong`, then capture real runtime evidence before promoting any adapter to
   `verified`. `claude_code` and `codex` config doctors pass, but runtime hook
   events are still not recorded.
2. Expand the hand-labeled relevance fixture and run it before implementing
   Hopfield/HRR retrieval changes. The first stale-memory `memory_risk` miss is
   now fixed by a narrow status-handoff supplement/boost, but future ranking
   changes must still report before/after category deltas instead of relying on
   intuition.
3. If quarantined malformed files need to return to the active tree, manually
   repair the archived markdown first, then use `memory doctor --offline
   --restore-malformed <file>` to validate and preview the restore, adding
   `--apply` only after the plan is correct.
4. Continue splitting large modules only where tests already lock the public
   behavior; avoid architecture churn without a failing maintainability signal.
