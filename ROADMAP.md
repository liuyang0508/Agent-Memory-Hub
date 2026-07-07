# Agent Memory Hub — Roadmap

> **Product source of truth**: [`STRATEGY.md`](./STRATEGY.md) (16-competitor matrix + differentiation thesis).
> **Architecture source of truth**: [`docs/architecture.md`](./docs/architecture.md).
> Public-facing summary below — implementation milestones (M1–M7) and public-launch milestones (PL1–PL6) are tracked separately.

## Where we are: v1.1 polish (2026-05-26) — plus v1.2/v1.3 retrieval & governance landed (2026-05-29)

> **M1–M4 SHIPPED** ✅ (code in `main`) — **v1.1 polish COMPLETE** ✅ (6/6 BLOCKER + 6/6 MAJOR resolved). Ready for merge to main.
>
> **⚠️ 2026-05-29 audit (P2-13)**: several capabilities first scheduled for v1.2/v1.3 in the capability specs have since landed in the code line — query expansion, opt-in cross-encoder rerank, confidence + retention decay (schema v0.3) and a lightweight knowledge graph. They are listed under "Live capabilities" below; the M5-M7 / PL milestones remain future.

Live capabilities (after v1.1 polish merges):

- ✅ 6 memory types · Pydantic v2 + typer CLI + FastMCP server
- ✅ SQLite FTS5 + sqlite-vec hybrid retrieval (BM25 + vector RRF)
- ✅ Schema accepts CJK / `+` / forward-compatible refs (v0.2 → v1 migration-free)
- ✅ MCP server exposes **28 registered tools**: 27 operation tools + 1 onboarding guide tool. Operation tools remain tiered as stable 10-tool core (write/search/read/update/confirm/stats/list_recent/tag_suggest/delete/brief) + governance 6 / io 5 / graph 3 / evolve 1 / conversation 2 (grew past the v1 spec §6.2.2 core-7 as governance/evolve/graph/io/conversation landed)
- ✅ Skill Audit — 30+ builtin rules + scanner + 30+ malicious sample tests + outbound event log
- ✅ Anti-drift + Governance — sha256 fingerprint dedup, per-project jaccard fuzzy dedup, tz-aware staleness, opt-in HTTP HEAD citation probe, drift-cluster detection
- ✅ Self-evolve engine + fail-closed audit gate (critical/high blocks proposal)
- ✅ 12 agent adapter **plugin architecture** (real-hook installation coming in v1.1 M1 — see below)
- ✅ L3 RBAC interfaces reserved (`tenant_id` + `auth_context`)
- ✅ Retrieval v2 — query expansion (`expand_query`) + opt-in cross-encoder rerank (`RERANK_ENABLED`) + MMR diversity (landed 2026-05-29, first specced for v1.2)
- ✅ Confidence + retention decay — schema v0.3 (`confidence` + `Retention.decay_class` half-life), effective-score ranking, `memory decay-status` / `memory confirm` (landed 2026-05-29, first specced for v1.3)
- ✅ Knowledge graph (lightweight) — entities/relations tables, 1-hop `graph_neighbors`, `memory link` / `unlink` / `memory graph`, MCP `graph_expand` (landed 2026-05-29, first specced for v1.3)
- ✅ Raw conversation evidence layer — `sources/conversations/*/messages.jsonl`, CLI `memory conversation ingest/list/read`, MCP `list_conversations` / `read_conversation`, and `memory harvest` raw-message snapshot (landed 2026-06-18)

## Implementation milestones (tech spec M1–M7)

| Milestone | Scope | Status |
|---|---|---|
| **M1** Hub core | Python rewrite — Pydantic schema, SQLite FTS5+vec, typer CLI, FastMCP, conformance | ✅ shipped (main) |
| **M2** Skill Audit Engine | 30+ rules, scanner, audit CLI, 30+ malicious sample tests, outbound log | ✅ shipped (main) |
| **M3** Anti-drift + Governance | dedup pipeline, drift detector (contradiction / staleness / citation-rot / drift-cluster), 10K/100K bench | ✅ shipped (main) |
| **M4** Self-evolve + Adapters | evolve engine, audit gate, 12 agent adapter plugin architecture | ✅ shipped (main) |
| **v1.1** polish | 6 BLOCKER + 6 MAJOR from 2026-05-26 dogfooding audit | ✅ complete (6/6 + 6/6) |
| **M5** Platform v0.1 | Next.js + multi-tenant + sync server (L2 → L3 bridge) | ⏳ blocked on v1.1 merge |
| **M6** Commercialization | Open Core split, paid tier surface, billing | ⏳ |
| **M7** Marketplace | Cross-agent skill / playbook exchange | ⏳ |

### v1.1 polish detail

Driven by the 2026-05-26 brutal-honest audit (A: static review of M1–M4 vs spec, B: dogfooding `govern`/`anti-drift`/`evolve`/`audit` on 187 real items, C: 10-competitor research). See [`STRATEGY.md`](./STRATEGY.md) for competitive context.

| ID | Fix | Status |
|---|---|---|
| B1+B2 | Schema accepts CJK / `+` ids and historical `refs.tags` | ✅ |
| B3 | MCP server +3 tools (delete / audit_skill / audit_outbound) | ✅ |
| B4 | EvolveEngine.audit_gate API mismatch (was 100% reject) | ✅ |
| B5 | DriftDetector tz-aware datetime (was TypeError on real data) | ✅ |
| B6 | Commit conformance fixtures + CI fail-on-skip | ✅ |
| M1 | Adapter install paths with truth-contract matrix (6 install-ready, 6 wip; priority claude_code + codex first) | ✅ |
| M2 | Split `_open_components` — govern/audit run offline (no HuggingFace) | ✅ |
| M3 | `iter_all` yield-and-skip on per-item parse errors | ✅ |
| M4 | Two-pass dedup (sha256 exact + jaccard per-project) | ✅ |
| M5 | Drift contradiction filter tightened + citation_rot HTTP HEAD opt-in | ✅ |
| M6 | This ROADMAP rewrite (kill M2-Playbook fiction + collapse 2 numbering systems) | ✅ (this commit) |

## Public-launch milestones (PL1–PL6)

Separate track from tech milestones to stop the long-standing collision between "M3 = Anti-drift" (tech) and "M3 = Launch artifacts" (GTM).

| | Milestone | Verification |
|---|---|---|
| **PL1** | v1.0.1 polished release with full audit + governance pipeline live | All v1.1 BLOCKER closed, 107+ unit tests green |
| **PL2** | Launch artifacts — long-form post + benchmark data + landing page + demo video | HN-ready checklist 100% complete |
| **PL3** | 🚀 HN launch + Twitter / Reddit / 中文社区 联动 | 1000+ stars in launch day |
| **PL4** | Issue triage + community PR onboarding + bugfix release | 90% of issues acknowledged within 7 days |
| **PL5** | 5k stars retrospective + L3 commercial decision point | 5k+ stars / 5+ agents integrated / first commercial intent |
| **PL6** | Listed as a Hermes (Nous Research) memory provider | Distribution leverage validated |

## Long-term layered model (Open Core)

| Layer | When | Status |
|---|---|---|
| **L1** Personal developer brain pool | now → forever, MIT, free | ✅ shipping |
| **L2** Team shared brain (git repo sync) | after L1 has 1000+ users | 🔜 |
| **L3** Enterprise Agent Memory infra (multi-tenant SaaS / private cloud / RBAC / role dashboards for OPC / OPT / managers / sales / engineers) | after 6-month metrics validate | 🔜 (interfaces reserved at v0.3.5) |

## What we are NOT building (YAGNI)

- L2 team git sync (waiting for L1 user pull)
- L3 multi-tenant SaaS implementation (interfaces only)
- LLM-assisted semantic contradiction detection (v1.2 question — cost / latency / proxy)
- Commercial SaaS dashboard / admin UI
- Playbook namespace as a separate type — folded into `decision` + `artifact` after audit found no v0.5-alpha implementation behind the prior ROADMAP claim

## How to follow / contribute

- Watch this repo for monthly milestone announcements
- Issues with `help-wanted` label welcome external contributions
- See [CONTRIBUTING.md](./CONTRIBUTING.md) for getting started
- v1.1 polish work is on `worktree-v1.1-polish` — PRs welcome against that branch until merge

## Open questions ("new continents")

8 explicit unsolved areas guide future exploration: cross-agent skill protocol normalization, L1→L2 migration path, L3 commercial form, semantic memory enhancement, MCP protocol evolution coupling, privacy/compliance enforcement, memory aging policy, multilingual brain.
