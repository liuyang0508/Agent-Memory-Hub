from agent_brain.platform.embedding import HashingEmbedder, get_default_embedder


def test_hashing_embedder_dim_consistent():
    """A deterministic embedder for tests. Real default is MiniLM."""
    emb = HashingEmbedder(dim=8)
    vec1 = emb.embed("hello world")
    vec2 = emb.embed("hello world")
    assert len(vec1) == 8
    assert vec1 == vec2


def test_hashing_embedder_different_text_different_vec():
    emb = HashingEmbedder(dim=8)
    assert emb.embed("a") != emb.embed("b")


def test_get_default_embedder_returns_callable(monkeypatch):
    monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")
    emb = get_default_embedder()
    vec = emb.embed("test")
    assert isinstance(vec, list)
    assert all(isinstance(x, float) for x in vec)
    assert len(vec) >= 8
