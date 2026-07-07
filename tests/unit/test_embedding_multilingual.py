"""P3-1: default embedder must be multilingual, env-configurable, with a safe fallback."""

from agent_brain.platform import embedding
from agent_brain.platform.embedding import (
    DEFAULT_PROD_MODEL,
    _build_prod_embedder,
    _resolve_model_name,
    reset_embedder_cache,
)


def test_default_prod_model_is_multilingual():
    # Regression: the default must not be the English-only all-MiniLM model.
    assert "multilingual" in DEFAULT_PROD_MODEL.lower()
    assert DEFAULT_PROD_MODEL != "sentence-transformers/all-MiniLM-L6-v2"


def test_resolve_model_name_default(monkeypatch):
    monkeypatch.delenv("MEMORY_HUB_EMBEDDING_MODEL", raising=False)
    assert _resolve_model_name() == DEFAULT_PROD_MODEL


def test_resolve_model_name_env_override(monkeypatch):
    monkeypatch.setenv("MEMORY_HUB_EMBEDDING_MODEL", "BAAI/bge-m3")
    assert _resolve_model_name() == "BAAI/bge-m3"


def test_build_prod_embedder_tries_configured_model_first(monkeypatch):
    monkeypatch.setenv("MEMORY_HUB_EMBEDDING_MODEL", "some/multilingual-model")
    seen = []

    class _FakeEmbedder:
        dim = 384

        def __init__(self, model_name):
            seen.append(model_name)

        def embed(self, text):
            return [0.0] * self.dim

    monkeypatch.setattr(embedding, "SentenceTransformerEmbedder", _FakeEmbedder)
    emb = _build_prod_embedder()
    assert seen == ["some/multilingual-model"]
    assert isinstance(emb, _FakeEmbedder)


def test_build_prod_embedder_falls_back_when_model_load_fails(monkeypatch):
    reset_embedder_cache()

    def _boom(model_name):
        raise RuntimeError(f"cannot load {model_name}")

    # Every sentence-transformer model fails to load -> must not crash.
    monkeypatch.setattr(embedding, "SentenceTransformerEmbedder", _boom)
    emb = _build_prod_embedder()
    vec = emb.embed("混合 text 中文")  # mixed CJK + ASCII
    assert isinstance(vec, list)
    assert vec and all(isinstance(x, float) for x in vec)
    reset_embedder_cache()


def test_build_prod_embedder_offline_mode_skips_sentence_transformer(monkeypatch):
    reset_embedder_cache()
    monkeypatch.setenv("MEMORY_HUB_EMBEDDING_OFFLINE", "1")
    seen = []

    def _should_not_load(model_name):
        seen.append(model_name)
        raise AssertionError(f"offline mode tried to load {model_name}")

    monkeypatch.setattr(embedding, "SentenceTransformerEmbedder", _should_not_load)
    emb = _build_prod_embedder()

    assert getattr(emb, "degraded", False) is True
    assert seen == []
    assert len(emb.embed("offline write")) == emb.dim
    reset_embedder_cache()
