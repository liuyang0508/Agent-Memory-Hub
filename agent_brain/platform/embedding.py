from __future__ import annotations

import hashlib
import logging
import os
from typing import Protocol

logger = logging.getLogger(__name__)

# Multilingual by default so CJK (and other non-English) text shares a
# semantic space with English. paraphrase-multilingual-MiniLM-L12-v2 keeps the
# same 384-dim output as the old all-MiniLM-L6-v2, so index dimensions are
# unchanged. Override with MEMORY_HUB_EMBEDDING_MODEL (e.g. "BAAI/bge-m3").
DEFAULT_PROD_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
FALLBACK_PROD_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class Embedder(Protocol):
    dim: int

    def embed(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Deterministic test-only embedder. NOT for production retrieval."""

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim
        # Set True only when this instance is a *production* fallback (no real
        # semantic model could load) — lets retrieval/doctor see degraded mode.
        self.degraded = False

    def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [digest[i % len(digest)] / 255.0 for i in range(self.dim)]
        norm = sum(x * x for x in raw) ** 0.5 or 1.0
        return [x / norm for x in raw]


class SentenceTransformerEmbedder:
    """Production embedder. Downloads the model on first use.

    Defaults to a multilingual model so CJK (and other non-English) text embeds
    into a shared semantic space; the English-only all-MiniLM-L6-v2 used
    previously gave poor CJK vector recall.
    """

    def __init__(self, model_name: str = DEFAULT_PROD_MODEL) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def embed(self, text: str) -> list[float]:
        vector = self._model.encode(text, normalize_embeddings=True)
        return vector.tolist()


_embedder_cache: dict[str, Embedder] = {}


def _resolve_model_name() -> str:
    """Production model name, overridable via MEMORY_HUB_EMBEDDING_MODEL.

    Defaults to a multilingual model (good CJK recall). Set the env var to
    e.g. ``BAAI/bge-m3`` to swap models without code changes.
    """
    name = os.environ.get("MEMORY_HUB_EMBEDDING_MODEL", "").strip()
    return name or DEFAULT_PROD_MODEL


def _build_prod_embedder() -> Embedder:
    """Load the production embedder with a safe fallback chain.

    Tries the configured/multilingual model first, then the English
    all-MiniLM model, and finally the deterministic HashingEmbedder so
    retrieval degrades gracefully instead of hard-crashing when no model can
    be loaded (e.g. offline first run, sentence-transformers not installed).
    """
    if os.environ.get("MEMORY_HUB_EMBEDDING_OFFLINE") == "1":
        fallback = HashingEmbedder()
        fallback.degraded = True
        logger.info(
            "MEMORY_HUB_EMBEDDING_OFFLINE=1; using HashingEmbedder without "
            "probing sentence-transformer models"
        )
        return fallback

    candidates = [_resolve_model_name()]
    if FALLBACK_PROD_MODEL not in candidates:
        candidates.append(FALLBACK_PROD_MODEL)
    for name in candidates:
        try:
            return SentenceTransformerEmbedder(name)
        except Exception as exc:
            logger.warning("embedding model %r failed to load: %s", name, exc)
    logger.error(
        "no sentence-transformer model could be loaded; falling back to "
        "HashingEmbedder (degraded semantic recall)"
    )
    fallback = HashingEmbedder()
    fallback.degraded = True
    return fallback


def get_default_embedder() -> Embedder:
    """Return Embedder per env config. Test mode uses Hashing for speed.

    Cached per mode so the production model loads only once.
    """
    mode = "test" if os.environ.get("MEMORY_HUB_TEST_EMBEDDING") == "1" else "prod"
    if mode not in _embedder_cache:
        _embedder_cache[mode] = HashingEmbedder() if mode == "test" else _build_prod_embedder()
    return _embedder_cache[mode]


def reset_embedder_cache() -> None:
    """Clear cached embedders (used by tests)."""
    _embedder_cache.clear()


def is_prod_embedder_degraded() -> bool:
    """True if the cached prod embedder fell back to HashingEmbedder.

    Means no real semantic model could load (offline first run, model not
    cached, sentence-transformers missing) — vector recall is degraded. False
    when no prod embedder has been built yet or a real model loaded.
    """
    emb = _embedder_cache.get("prod")
    return bool(getattr(emb, "degraded", False))


def probe_semantic_available() -> bool:
    """Side-effect-free check of whether a real semantic model is usable now.

    Does NOT hit the network: only checks that sentence-transformers imports.
    Used by ``memory doctor --offline`` to report vector-search availability
    without triggering a model download.
    """
    import importlib.util
    return importlib.util.find_spec("sentence_transformers") is not None
