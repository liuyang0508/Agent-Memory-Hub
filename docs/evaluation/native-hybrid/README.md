# Native organization hybrid retrieval candidate

This is local implementation and isolated evaluation evidence, not production acceptance.
No production state, installed client configuration, or shared Python environment was changed.

## Server integration

`Hub.retrieve(token, query, limit=8, kinds=None, include_evidence=False,
project_filter=None)` returns `items` and `retrieval`. Identity comes only from the
validated device credential. Default kinds are memory, excluding Codex rollout
summaries/raw extraction. Explicit kinds can request transcripts/rules/etc.;
`include_evidence=True` adds the supporting memory roles. Roles describe use,
never truth. `verified=True` means source bytes/version completeness, not fact checking.
Existing `Hub.search` behavior and response contract remain compatible.

To enable a real cached server-side model, instantiate:

```python
hub = Hub(db_path,
    semantic_model='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
    mmr_lambda=0.85)  # optional; default is disabled
hub.rebuild_semantic(device_token)  # explicit maintenance, outside hooks
result = hub.retrieve(device_token, 'How long does a refund take?')
```

Alternatively set **only the Hub server** environment `AMH_NATIVE_SEMANTIC_MODEL`
to the cached model name/local model directory. The server needs the existing
`embeddings` optional dependency; clients/Cowork do not import it. Model files
must already be cached: this implementation always loads local-only and never
falls back to HashingEmbedder. It does not download dependencies or weights.
An operator CLI can call `rebuild_semantic` using an authorized organization
credential; there is intentionally no unauthenticated build endpoint.

Without a built index, retrieve returns lexical results and
`semantic_index_not_built`, without initializing a model. Other explicit reasons
cover absent/unavailable models, model fingerprint mismatch, stale/partial
indexes, query encoder failure, and optional reranker failure. `mode=hybrid`
means semantic search actually ran, even if no semantic candidate cleared the
floor. `answer_status=candidates_only` is not a claim that an answer was found.

Optional reranker is a server-injected callable `(query, authorized_items) ->
list[float]`, one score per item. Its errors degrade to the fused ranking. This
extension point was protocol-tested; no real reranker model was evaluated.

## Data and isolation

Shared RRF, MMR, hit and trace dataclasses live under `agent_brain.retrieval`.
Legacy imports re-export the same objects; no legacy ItemsStore, brain pool,
feedback write, or network-enabled default embedder enters native retrieval.

Derived tables `native_semantic_meta` and `native_semantic_vectors` are created
only by explicit build. Metadata stores model id, SHA256 of actual model files,
dimension and indexing version. Real model text uses tokenizer-sized overlapping
windows (the cached multilingual model accepts 128 tokens); semantic asset score
is the maximum window cosine. Exact asset-id and lexical matches remain available.

All source selection and model candidates are scoped by credential-derived tenant,
explicit project/kinds/roles before ranking. Current source revision/content hash
must match the index. Completeness checks reject mixed/incomplete multipart data.
The response rechecks identity and source versions after inference. Concurrent
updates are omitted, never returned as old content. Build snapshots authorized
source data, releases its read transaction before embedding, then swaps a tenant's
derived vectors atomically and skips source rows changed during computation.
Deletes immediately make old vectors ineligible; the next rebuild physically
removes obsolete derived rows. No model work runs inside the source write path.

Identical bodies are deduplicated in output, with `duplicate_sources` retaining
other asset/version/source references. This does not change source records.

## Reproduction and limits

Used interpreter: `/Users/liuyang/amh-native-review-fixes/.venv/bin/python`.
It already contained sentence-transformers, torch, and the local Hugging Face
cache; no installation was needed. Cached model snapshot:
`e8f8c211226b894fcb81acc59f3b34ba3efd5f42`, 384 dimensions.

```sh
PYTHONPATH=. HF_HUB_OFFLINE=1 TOKENIZERS_PARALLELISM=false \
  /Users/liuyang/amh-native-review-fixes/.venv/bin/python \
  benchmarks/native_hybrid_smoke.py --output /tmp/native-hybrid.json
```

`development-smoke.json` uses REAL cached embeddings with a synthetic temporary
corpus. Answerable cases achieved Hit@3 6/6 (Top1 5/6); the two unanswerable/isolation
queries returned empty. Cross-language retrieval had zero lexical hits and one
correct semantic hit. Failure-summary interference and duplicates are included;
an updated source demonstrates stale-vector exclusion and explicit partial-index
reporting. The long-tail case is only rank 3 and relied on lexical matches: this
is an outstanding ranking-quality limitation, not a passed semantic-tail claim.

The separate unit tests deliberately inject a synthetic backend for authorization,
version races, lock behavior, fallback, compatibility, duplicate attribution and
model mismatch contracts. They are not embedding quality evidence.

This is a development smoke set, not sealed evaluation or production acceptance.
The 0.45 semantic cosine floor is a configurable eligibility threshold, not
calibrated answer confidence. Lexical overlap can still return irrelevant
candidates; consumers must inspect sources and abstain when unsupported.
Queries scan current authorized vectors in SQLite/Python rather than an ANN
index. Large-organization latency/memory use, production corpus quality, real
reranker performance and incremental background index refresh remain unverified.
Source changes fall back to lexical until explicit rebuild; hooks never trigger
builds. No old index backup is needed for rollback: remove model configuration
and retain source assets/FTS; derived tables may be rebuilt in a later candidate.
