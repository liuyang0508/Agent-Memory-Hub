from __future__ import annotations


def test_headroom_adapter_falls_back_to_reversible_pack_when_external_missing(monkeypatch) -> None:
    from agent_brain.platform.headroom_integration import compress_with_headroom

    monkeypatch.setenv("MEMORY_HUB_HEADROOM_EXTERNAL", "0")
    result = compress_with_headroom(
        "\n".join(f"line {i}" for i in range(40)),
        budget_chars=80,
        detail_uri="memory://items/mem-example/body",
    )

    assert result.provider == "amh-local"
    assert result.strategy == "important_lines"
    assert result.content_type == "plain_text"
    assert result.reversible is True
    assert result.detail_uri == "memory://items/mem-example/body"
    assert len(result.text) <= 80
    assert "memory://items/mem-example/body" in result.text
    assert "memory://items/mem-example/body" in result.retrieve_hint
