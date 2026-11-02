"""Explicit, rebuildable native asset vectors; never invokes the legacy brain.

Model loading is local-only. Build computes vectors outside SQLite transactions;
queries only use vectors whose source revision AND content hash are still current.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Protocol

from .storage import database

INDEX_VERSION = 2
WINDOW = 700
STRIDE = 550


class SemanticBackend(Protocol):
    model_id: str
    model_version: str
    dim: int
    semantic: bool

    def embed(self, text: str) -> list[float]: ...


class LocalSentenceTransformer:
    """Reuse the legacy real encoder, with NO hashing or online fallback."""

    semantic = True

    def __init__(self, model_name: str):
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

    def embed(self, text: str) -> list[float]:
        return self.encoder.embed(text)

    def split_text(self, text: str) -> list[str]:
        # Character windows exceed this multilingual model's token limit for
        # CJK. Slice actual tokens so late facts cannot silently truncate away.
        model = self.encoder._model
        tokenizer = model.tokenizer
        size = model.max_seq_length - tokenizer.num_special_tokens_to_add(False)
        ids = tokenizer.encode(text, add_special_tokens=False, truncation=False, verbose=False)
        stride = max(1, size * 3 // 4)
        return [
            tokenizer.decode(ids[i : i + size], skip_special_tokens=True)
            for i in range(0, max(1, len(ids)), stride)
        ]


def descriptor(backend: SemanticBackend) -> dict:
    if not getattr(backend, "semantic", False):
        raise ValueError("a real semantic backend is required; hashing is not semantic")
    return dict(
        model=backend.model_id,
        model_version=backend.model_version,
        dimension=backend.dim,
        index_version=INDEX_VERSION,
        window=WINDOW,
        stride=STRIDE,
    )


def vector(backend: SemanticBackend, text: str) -> list[float]:
    result = [float(v) for v in backend.embed(text)]
    if len(result) != backend.dim or not all(math.isfinite(v) for v in result):
        raise ValueError("invalid semantic vector dimension or values")
    if not any(result):
        raise ValueError("empty semantic vector")
    return result


def text_windows(item: dict, backend: SemanticBackend) -> list[str]:
    body = item.get("index_text") if item["kind"] == "transcript" else None
    body = item["body"] if body is None else body
    title = item.get("title", "")
    if hasattr(backend, "split_text"):
        return backend.split_text(title + "\n" + body)
    return [title + "\n" + body[i : i + WINDOW] for i in range(0, max(1, len(body)), STRIDE)]


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


def exists(db) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_semantic_meta'"
        ).fetchone()
        is not None
    )


def rebuild(hub, token: str, backend: SemanticBackend) -> dict:
    """Build a tenant snapshot, then atomically replace its derived generation.

    Concurrent source changes are skipped at commit and cannot resurrect a stale
    vector. Source deletes do not need a model or vector write on the put path.
    """
    config = descriptor(backend)
    with database(hub.path) as db:
        who = hub._identity(db, token)
        db.execute("BEGIN")
        rows = db.execute(
            "SELECT payload FROM assets WHERE tenant=? AND json_extract(payload,'$.deleted')=0 AND json_extract(payload,'$.encoding')='utf8'",
            (who["tenant"],),
        ).fetchall()
        items = [json.loads(row["payload"]) for row in rows]
        items = [item for item in items if hub._retrieval_valid(db, item)]
    encoded = [
        (item, json.dumps([vector(backend, text) for text in text_windows(item, backend)]))
        for item in items
    ]
    with database(hub.path) as db:
        db.executescript(SCHEMA)
        db.execute("BEGIN IMMEDIATE")
        current_who = hub._identity(db, token)
        if current_who["tenant"] != who["tenant"]:
            raise PermissionError("organization changed during semantic build")
        db.execute("DELETE FROM native_semantic_vectors WHERE tenant=?", (who["tenant"],))
        count = 0
        for item, vectors in encoded:
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
    return {**config, "indexed": count, "skipped_changed": len(items) - count}
