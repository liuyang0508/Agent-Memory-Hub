from __future__ import annotations


def test_build_enrichment_updates_filters_invalid_fields_and_clamps_confidence() -> None:
    from agent_brain.memory.evidence.harvest.enrichment_updates import build_enrichment_updates

    updates = build_enrichment_updates(
        {
            "title": "Better title",
            "summary": "Better summary",
            "tags": ["a", 1, "b", "c", "d", "e", "f", "g", "h", "i"],
            "confidence": 1.7,
        }
    )

    assert updates == {
        "abstraction": "L2",
        "source.extractor": "llm",
        "title": "Better title",
        "summary": "Better summary",
        "tags": ["a", "b", "c", "d", "e", "f", "g", "h"],
        "confidence": 1.0,
    }


def test_build_enrichment_updates_keeps_base_fields_for_empty_result() -> None:
    from agent_brain.memory.evidence.harvest.enrichment_updates import build_enrichment_updates

    assert build_enrichment_updates({}) == {
        "abstraction": "L2",
        "source.extractor": "llm",
    }
