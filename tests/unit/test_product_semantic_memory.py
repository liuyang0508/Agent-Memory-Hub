from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.memory.store.items_store import ItemsStore


def test_semantic_candidate_generation_extracts_pending_review_only(tmp_path: Path) -> None:
    from agent_brain.product.proactive_memory import (
        generate_semantic_candidates,
        list_candidates,
    )

    store = ItemsStore(tmp_path / "items")
    item = MemoryItem(
        id="mem-20260621-020000-transcript-learning",
        type=MemoryType.episode,
        created_at=datetime(2026, 6, 21, tzinfo=timezone.utc),
        title="Transcript learning",
        summary="Session contains a reusable architecture decision.",
        tags=["conversation"],
        confidence=0.8,
    )
    store.write(
        item,
        "\n".join(
            [
                "User: we decided to keep Markdown as source of truth.",
                "Assistant: reason is portability and auditability.",
                "Later noise that should not matter.",
            ]
        ),
    )

    result = generate_semantic_candidates(tmp_path, limit=20)
    queue = list_candidates(tmp_path)

    assert result["created"] == 1
    assert queue["pending"] == 1
    candidate = queue["items"][0]
    assert candidate["status"] == "pending"
    assert candidate["source_item_ids"] == [item.id]
    assert "semantic" in candidate["risk_flags"]
    assert "Markdown as source of truth" in candidate["body"]
    assert len(list((tmp_path / "items").glob("*.md"))) == 1


def test_semantic_candidate_generation_is_idempotent(tmp_path: Path) -> None:
    from agent_brain.product.proactive_memory import generate_semantic_candidates, list_candidates

    store = ItemsStore(tmp_path / "items")
    store.write(
        MemoryItem(
            id="mem-20260621-020100-policy-learning",
            type=MemoryType.fact,
            created_at=datetime(2026, 6, 21, tzinfo=timezone.utc),
            title="Policy learning",
            summary="Need a rule candidate.",
            confidence=0.8,
        ),
        "Important rule: do not bypass WriteService in SDK or Web paths.",
    )

    assert generate_semantic_candidates(tmp_path)["created"] == 1
    assert generate_semantic_candidates(tmp_path)["created"] == 0
    assert list_candidates(tmp_path)["pending"] == 1
