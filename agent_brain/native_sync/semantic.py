"""Explicit, rebuildable native asset vectors; never invokes the legacy brain.

Model loading is local-only. Build computes vectors outside SQLite transactions;
queries only use vectors whose source revision AND content hash are still current.
"""

from __future__ import annotations

import hashlib
import json
import math
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
    result = [float(v) for v in backend.embed(text)]
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
) -> IndexRefreshResult:
    """Build a tenant snapshot, then atomically replace its derived generation.

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
        who = hub._identity(db, token)
        db.execute("BEGIN")
        scope = [who["tenant"], *selected_kinds]
        rows = db.execute(
            "SELECT a.payload FROM assets a WHERE " + predicate,
            scope,
        ).fetchall()
        items: list[dict[str, Any]] = [json.loads(row["payload"]) for row in rows]
        items = [item for item in items if hub._retrieval_valid(db, item)]
        cached: dict[str, str] = {}
        if exists(db):
            previous = db.execute(
                "SELECT descriptor FROM native_semantic_meta WHERE tenant=?", (who["tenant"],)
            ).fetchone()
            if previous and json.loads(previous["descriptor"]) == config:
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
