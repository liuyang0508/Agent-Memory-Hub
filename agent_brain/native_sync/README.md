# Native organization memory — code map

This package has one responsibility: move product-owned assets between enrolled
devices and an organization Hub, preserving source identity. It must not invoke
the legacy AMH extraction, MemoryItem, governance or recall pipelines.

Start with [architecture](../../docs/native-memory/architecture.zh.md) for the
data flow, invariants, extension recipe and availability limits, or
[operations](../../docs/native-memory/operations.zh.md) for failure handling.

| Responsibility | Modules | Boundary |
|---|---|---|
| Entry/composition | `cli`, `__main__`, `install` | Configuration and explicit activation; no new business rules |
| Product observation | `discovery`, `source`, `lineage` | Stable source bytes, real host evidence, original producer |
| Device synchronization | `client`, `worker` | Durable outbox/replica; scheduling owns retries |
| Consumption | `hooks`, `query_worker`, `recall`, `projection` | Bounded Hook path and native local path |
| Explicit behavior assets | `assets`, `materialize` | Complete snapshots; preserve edits; never auto-execute |
| Hub authority | `api`, `hub`, `models` | Validate identity and atomically commit revision/index/change |
| Shared foundations | `contracts`, `storage`, `transport`, `common` | Typed transport, independent databases, HTTPS, pure helpers |

Small responsibility modules remain in one package: no one-file subpackages,
forwarding wrappers, duplicate registries or generic `manager`/`utils` layer.
`storage.py` owns both **separate** SQLite schemas and connection lifecycle;
`common.py` contains no database/service orchestration. Keep dependencies explicit.

The Cowork export contains an explicit, stdlib-only runtime subset. Adding a
runtime dependency requires updating the export list in `install.py` and passing
`tests/native_sync/test_architecture.py` plus portable runtime execution tests.
Do not include credentials, desktop configuration or server dependencies in it.
