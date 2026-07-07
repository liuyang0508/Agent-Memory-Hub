from agent_brain.memory.evidence.harvest.enricher import enrich_pool


def test_enrich_pool_is_safe_noop_when_model_unavailable(tmp_brain, monkeypatch):
    monkeypatch.setenv("MEMORY_HUB_NO_MODEL", "1")   # force "model unavailable"
    assert enrich_pool() == 0                         # no crash, no writes
