"""Explicit, rebuildable native asset vectors; never invokes the legacy brain.

Model loading is local-only. Build computes vectors outside SQLite transactions;
queries only use vectors whose source revision AND content hash are still current.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from pathlib import Path
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal, NotRequired, Protocol, TypedDict, cast

from .storage import database
from .asset_roles import PRIMARY_MEMORY_SQL, asset_role
from .common import KINDS

if TYPE_CHECKING:
    from .hub import Hub


class TextWindow(TypedDict):
    text: str
    start: int
    end: int
    source_field: NotRequired[Literal["body", "index_text"]]


class StoredWindow(TypedDict):
    start: int
    end: int
    source_field: Literal["body", "index_text"]
    vector: list[float]


class SemanticDescriptor(TypedDict):
    model: str
    model_version: str
    dimension: int
    index_version: int
    window: int
    stride: int
    token_window: int | None
    token_stride: int | None
    aggregation: Literal["max_window_cosine"]


class IndexRefreshResult(SemanticDescriptor):
    indexed: int
    skipped_changed: int
    reused: int
    encoded: int
    kinds: list[str]
    include_evidence: bool


INDEX_VERSION = 3
WINDOW = 700
STRIDE = 550


class SemanticBackend(Protocol):
    model_id: str
    model_version: str
    dim: int
    semantic: bool

    def embed(self, text: str) -> list[float]: ...


class WindowedSemanticBackend(SemanticBackend, Protocol):
    window_tokens: int
    stride_tokens: int

    def split_windows(self, text: str) -> list[TextWindow]: ...


class LocalSentenceTransformer:
    """Reuse the legacy real encoder, with NO hashing or online fallback."""

    semantic = True

    def __init__(self, model_name: str) -> None:
        from huggingface_hub import snapshot_download
        from agent_brain.platform.embedding import SentenceTransformerEmbedder

        path = Path(model_name).expanduser()
        if not path.is_dir():
            path = Path(snapshot_download(model_name, local_files_only=True))
        # Fingerprint actual artifacts, including custom local models. The commit
        # path alone would not detect an operator replacing files in that path.
        fingerprint = hashlib.sha256()
        for source in sorted(p for p in path.rglob("*") if p.is_file()):
            fingerprint.update(str(source.relative_to(path)).encode())
            with source.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    fingerprint.update(block)
        self.model_id = model_name
        self.model_version = fingerprint.hexdigest()
        self.encoder = SentenceTransformerEmbedder(str(path), local_only=True)
        self.dim = self.encoder.dim
        model = self.encoder._model
        self.window_tokens: int = model.max_seq_length - model.tokenizer.num_special_tokens_to_add(
            False
        )
        self.stride_tokens = max(1, self.window_tokens * 3 // 4)

    def embed(self, text: str) -> list[float]:
        return self.encoder.embed(text)

    def split_windows(self, text: str) -> list[TextWindow]:
        # Keep tokenizer offsets into source text; decoded model input may have
        # normalized spacing, so it must not itself be presented as a quotation.
        tokenizer = self.encoder._model.tokenizer
        encoded = tokenizer(
            text,
            add_special_tokens=False,
            truncation=False,
            return_offsets_mapping=True,
            verbose=False,
        )
        ids, offsets = encoded["input_ids"], encoded["offset_mapping"]
        windows: list[TextWindow] = []
        for i in range(0, max(1, len(ids)), self.stride_tokens):
            end = min(i + self.window_tokens, len(ids))
            windows.append(
                dict(
                    text=tokenizer.decode(ids[i:end], skip_special_tokens=True),
                    start=offsets[i][0] if offsets else 0,
                    end=offsets[end - 1][1] if offsets else 0,
                )
            )
        return windows


MANIFEST = "amh-semantic.json"
POOLING = ("mean", "cls")


def _fingerprint(paths: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode())
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
    return digest.hexdigest()


def _unit(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not 0 <= number <= 1:
        raise ValueError("gate thresholds must be between 0 and 1")
    return number


class LocalOnnxEncoder:
    """Quantized ONNX encoder for small CPU-only Hubs: no torch, never downloads.

    A model directory holds the ONNX graph, its tokenizer.json and an
    amh-semantic.json manifest naming pooling and query/passage prefixes. The
    manifest is part of the fingerprint, so changing a prefix forces a rebuild.
    """

    semantic = True

    def __init__(self, directory: Path) -> None:
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        manifest_path = directory / MANIFEST
        manifest = json.loads(manifest_path.read_text())
        if not isinstance(manifest, dict):
            raise ValueError("invalid semantic model manifest")
        onnx_path = directory / str(manifest["onnx"])
        tokenizer_path = directory / str(manifest.get("tokenizer", "tokenizer.json"))
        for path in (onnx_path, tokenizer_path):
            if not path.resolve().is_relative_to(directory.resolve()) or not path.is_file():
                raise ValueError("semantic model files must live inside the model directory")
        self.pooling = str(manifest.get("pooling", "mean"))
        if self.pooling not in POOLING:
            raise ValueError("unsupported pooling")
        self.query_prefix = str(manifest.get("query_prefix", ""))
        self.passage_prefix = str(manifest.get("passage_prefix", ""))
        self.max_tokens = int(manifest.get("max_tokens", 512))
        if not 4 <= self.max_tokens <= 8192:
            raise ValueError("max_tokens out of range")
        self._np = np
        options = ort.SessionOptions()
        threads = int(os.environ.get("AMH_NATIVE_SEMANTIC_THREADS") or manifest.get("threads") or 0)
        if threads > 0:
            options.intra_op_num_threads = threads
            options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(
            str(onnx_path), options, providers=["CPUExecutionProvider"]
        )
        self.inputs = {item.name for item in self.session.get_inputs()}
        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        pad_ids = [self.tokenizer.token_to_id(token) for token in ("<pad>", "[PAD]")]
        self.pad_id = next((int(value) for value in pad_ids if value is not None), 0)
        self.model_id = str(manifest.get("model_id") or directory.name)
        # Gate thresholds tune ranking only; they must not force a re-index.
        gate_keys = {"hook_min_score", "hook_margin", "semantic_min_score"}
        encoding = {k: v for k, v in manifest.items() if k not in gate_keys}
        self.model_version = hashlib.sha256(
            json.dumps(encoding, sort_keys=True).encode()
            + _fingerprint([onnx_path, tokenizer_path]).encode()
        ).hexdigest()
        self.hook_min_score = _unit(manifest.get("hook_min_score"))
        self.hook_margin = _unit(manifest.get("hook_margin"))
        self.semantic_min_score = _unit(manifest.get("semantic_min_score"))
        specials = len(self.tokenizer.encode("", add_special_tokens=True).ids)
        prefix = len(self.tokenizer.encode(self.passage_prefix, add_special_tokens=False).ids)
        self.window_tokens: int = max(1, self.max_tokens - specials - prefix)
        self.stride_tokens = max(1, self.window_tokens * 3 // 4)
        self.dim = len(self._encode([self.passage_prefix + "dimension probe"])[0])

    def _encode(self, texts: Sequence[str]) -> list[list[float]]:
        np = self._np
        rows = []
        for text in texts:
            ids = self.tokenizer.encode(text, add_special_tokens=True).ids
            if len(ids) > self.max_tokens:
                ids = ids[: self.max_tokens - 1] + ids[-1:]  # Keep the closing special token.
            rows.append(ids)
        width = max(len(ids) for ids in rows)
        input_ids = np.full((len(rows), width), self.pad_id, dtype=np.int64)
        mask = np.zeros((len(rows), width), dtype=np.int64)
        for n, ids in enumerate(rows):
            input_ids[n, : len(ids)] = ids
            mask[n, : len(ids)] = 1
        feed = {"input_ids": input_ids, "attention_mask": mask}
        if "token_type_ids" in self.inputs:
            feed["token_type_ids"] = np.zeros_like(input_ids)
        hidden = self.session.run(None, {k: v for k, v in feed.items() if k in self.inputs})[0]
        if self.pooling == "cls":
            pooled = hidden[:, 0, :]
        else:
            weights = mask[..., None].astype(hidden.dtype)
            pooled = (hidden * weights).sum(axis=1) / np.maximum(weights.sum(axis=1), 1e-9)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        pooled = pooled / np.maximum(norms, 1e-12)
        return [[float(v) for v in row] for row in pooled]

    def embed(self, text: str) -> list[float]:
        return self._encode([self.passage_prefix + text])[0]

    def embed_query(self, text: str) -> list[float]:
        return self._encode([self.query_prefix + text])[0]

    def split_windows(self, text: str) -> list[TextWindow]:
        # Windows are slices of the source text by token offsets, so a match
        # can be quoted from the stored body without decoded-token drift.
        encoded = self.tokenizer.encode(text, add_special_tokens=False)
        offsets = encoded.offsets
        windows: list[TextWindow] = []
        for i in range(0, max(1, len(offsets)), self.stride_tokens):
            end = min(i + self.window_tokens, len(offsets))
            start_char = offsets[i][0] if offsets else 0
            end_char = offsets[end - 1][1] if offsets else len(text)
            windows.append(dict(text=text[start_char:end_char], start=start_char, end=end_char))
            if end >= len(offsets):
                break
        return windows


def load_backend(spec: str) -> SemanticBackend:
    """A directory with an amh-semantic.json manifest uses ONNX; anything else sentence-transformers."""
    path = Path(spec).expanduser()
    if path.is_dir() and (path / MANIFEST).is_file():
        return LocalOnnxEncoder(path)
    return LocalSentenceTransformer(spec)


def query_vector(backend: SemanticBackend, text: str) -> list[float]:
    """Encode a search query; asymmetric models add their own query prefix."""
    encode = getattr(backend, "embed_query", None)
    raw = encode(text) if callable(encode) else backend.embed(text)
    return _checked(backend, raw)


def descriptor(backend: SemanticBackend) -> SemanticDescriptor:
    if not getattr(backend, "semantic", False):
        raise ValueError("a real semantic backend is required; hashing is not semantic")
    return dict(
        model=backend.model_id,
        model_version=backend.model_version,
        dimension=backend.dim,
        index_version=INDEX_VERSION,
        window=WINDOW,
        stride=STRIDE,
        token_window=getattr(backend, "window_tokens", None),
        token_stride=getattr(backend, "stride_tokens", None),
        aggregation="max_window_cosine",
    )


def vector(backend: SemanticBackend, text: str) -> list[float]:
    return _checked(backend, backend.embed(text))


def _checked(backend: SemanticBackend, raw: Sequence[float]) -> list[float]:
    result = [float(v) for v in raw]
    if len(result) != backend.dim or not all(math.isfinite(v) for v in result):
        raise ValueError("invalid semantic vector dimension or values")
    if not any(result):
        raise ValueError("empty semantic vector")
    return result


def text_windows(item: dict[str, Any], backend: SemanticBackend) -> list[TextWindow]:
    field: Literal["body", "index_text"] = (
        "index_text"
        if item["kind"] == "transcript" and item.get("index_text") is not None
        else "body"
    )
    body, title = item[field], item.get("title", "")
    if hasattr(backend, "split_windows"):
        windows = cast(WindowedSemanticBackend, backend).split_windows(title + "\n" + body)
        for window in windows:
            window["start"] = max(0, window["start"] - len(title) - 1)
            window["end"] = max(0, window["end"] - len(title) - 1)
    else:
        windows = [
            dict(text=title + "\n" + body[i : i + WINDOW], start=i, end=min(len(body), i + WINDOW))
            for i in range(0, max(1, len(body)), STRIDE)
        ]
    for window in windows:
        window["source_field"] = field
    return windows


SCHEMA = """
CREATE TABLE IF NOT EXISTS native_semantic_meta(
 tenant TEXT PRIMARY KEY, descriptor TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS native_semantic_vectors(
 tenant TEXT NOT NULL, asset_id TEXT NOT NULL, revision INTEGER NOT NULL,
 content_hash TEXT NOT NULL, vectors TEXT NOT NULL,
 PRIMARY KEY(tenant,asset_id)
);
"""


def exists(db: sqlite3.Connection) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_semantic_meta'"
        ).fetchone()
        is not None
    )


def rebuild(
    hub: Hub,
    token: str,
    backend: SemanticBackend,
    *,
    kinds: Sequence[str] | None = None,
    include_evidence: bool = False,
    tenant: str | None = None,
) -> IndexRefreshResult:
    """Build a tenant snapshot, then atomically replace its derived generation.

    ``tenant`` is the server's own maintenance path (no device credential):
    whoever can run it already holds the database file. Without changes since
    the last build nothing is written, so a periodic refresh costs one read.

    Reuse only the same model/index descriptor and exact source revision/hash.
    Concurrent source changes are skipped at commit and cannot resurrect a stale
    vector. Source deletes do not need a model or vector write on the put path.
    encoded/reused count prepared assets (before skipped_changed), not windows.
    """
    selected_kinds = ["memory"] if kinds is None else list(dict.fromkeys(kinds))
    if not selected_kinds or not set(selected_kinds) <= KINDS.keys():
        raise ValueError("invalid semantic index kinds")
    if type(include_evidence) is not bool:
        raise ValueError("include_evidence must be boolean")
    filters = [
        "a.tenant=?",
        "json_extract(a.payload,'$.deleted')=0",
        "json_extract(a.payload,'$.encoding')='utf8'",
        "json_extract(a.payload,'$.kind') IN (" + ",".join("?" for _ in selected_kinds) + ")",
    ]
    if not include_evidence:
        filters.append(PRIMARY_MEMORY_SQL)
    predicate = " AND ".join(filters)
    config = descriptor(backend)
    with database(hub.path) as db:
        db.create_function("native_asset_role", 3, asset_role, deterministic=True)
        who = {"tenant": tenant} if tenant is not None else hub._identity(db, token)
        db.execute("BEGIN")
        scope = [who["tenant"], *selected_kinds]
        rows = db.execute(
            "SELECT a.payload FROM assets a WHERE " + predicate,
            scope,
        ).fetchall()
        items: list[dict[str, Any]] = [json.loads(row["payload"]) for row in rows]
        items = [item for item in items if hub._retrieval_valid(db, item)]
        cached: dict[str, str] = {}
        stored: set[tuple[str, int, str]] = set()
        if exists(db):
            previous = db.execute(
                "SELECT descriptor FROM native_semantic_meta WHERE tenant=?", (who["tenant"],)
            ).fetchone()
            if previous and json.loads(previous["descriptor"]) == config:
                stored = {
                    (row["asset_id"], row["revision"], row["content_hash"])
                    for row in db.execute(
                        "SELECT asset_id,revision,content_hash FROM native_semantic_vectors "
                        "WHERE tenant=?",
                        (who["tenant"],),
                    )
                }
                cached = {
                    row["asset_id"]: row["vectors"]
                    for row in db.execute(
                        "SELECT v.asset_id,v.vectors FROM native_semantic_vectors v "
                        "JOIN assets a ON a.tenant=v.tenant AND a.id=v.asset_id "
                        "AND a.revision=v.revision "
                        "AND json_extract(a.payload,'$.content_hash')=v.content_hash "
                        "WHERE " + predicate,
                        scope,
                    )
                }
    current_keys = {(i["asset_id"], i["revision"], i["content_hash"]) for i in items}
    if stored and current_keys == stored:
        # Same model, same eligible revisions: the stored generation is current.
        return {
            **config,
            "indexed": len(stored),
            "skipped_changed": 0,
            "reused": len(stored),
            "encoded": 0,
            "kinds": selected_kinds,
            "include_evidence": include_evidence,
        }
    prepared: list[tuple[dict[str, Any], str]] = []
    reused = 0
    encoded = 0
    for item in items:
        previous_vectors = cached.get(item["asset_id"])
        if previous_vectors is not None:
            prepared.append((item, previous_vectors))
            reused += 1
        else:
            prepared.append(
                (
                    item,
                    json.dumps(
                        [
                            {
                                **{k: v for k, v in window.items() if k != "text"},
                                "vector": vector(backend, window["text"]),
                            }
                            for window in text_windows(item, backend)
                        ]
                    ),
                )
            )
            encoded += 1
    with database(hub.path) as db:
        db.executescript(SCHEMA)
        db.execute("BEGIN IMMEDIATE")
        if tenant is None:
            current_who = hub._identity(db, token)
            if current_who["tenant"] != who["tenant"]:
                raise PermissionError("organization changed during semantic build")
        db.execute("DELETE FROM native_semantic_vectors WHERE tenant=?", (who["tenant"],))
        count = 0
        for item, vectors in prepared:
            row = db.execute(
                "SELECT revision,payload FROM assets WHERE tenant=? AND id=?",
                (who["tenant"], item["asset_id"]),
            ).fetchone()
            if row is None or row["revision"] != item["revision"]:
                continue
            current = json.loads(row["payload"])
            if current["content_hash"] != item["content_hash"] or not hub._retrieval_valid(
                db, current
            ):
                continue
            db.execute(
                "INSERT INTO native_semantic_vectors VALUES(?,?,?,?,?)",
                (who["tenant"], item["asset_id"], item["revision"], item["content_hash"], vectors),
            )
            count += 1
        db.execute(
            "INSERT OR REPLACE INTO native_semantic_meta VALUES(?,?)",
            (who["tenant"], json.dumps(config, sort_keys=True)),
        )
    return {
        **config,
        "indexed": count,
        "skipped_changed": len(items) - count,
        "reused": reused,
        "encoded": encoded,
        "kinds": selected_kinds,
        "include_evidence": include_evidence,
    }
