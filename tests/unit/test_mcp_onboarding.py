from __future__ import annotations

from agent_brain.interfaces.mcp.onboarding import get_usage_guide


def test_usage_guide_teaches_context_pack_deep_read_chain() -> None:
    guide = get_usage_guide()
    text = "\n".join(str(value) for value in guide.values())

    assert 'search_memory(query="...", top_k=5, verbosity="auto")' in text
    assert "context_pack" in text
    assert "locator/overview only" in text
    assert "1-3" in text
    assert 'read_memory(id, head=2000, view="detail")' in text
    assert "only when needed" in text
    assert "Reserve explicit search `verbosity=\"detail\"`" in text
