from __future__ import annotations

from pathlib import Path


def test_draft_store_create_list_edit_skip(tmp_path: Path) -> None:
    from agent_brain.contracts.memory_item import MemoryType
    from agent_brain.product.memory_drafts import DraftStore, MemoryDraftInput

    store = DraftStore(tmp_path)
    draft = store.create(MemoryDraftInput(
        title="Use pytest for AMH",
        summary="AMH test runs use python -m pytest.",
        body="Use python -m pytest tests/unit/test_web_api.py -q.",
        type=MemoryType.fact.value,
        tags=["amh", "tests"],
        source_agent="codex",
        source_refs={"conversation_id": "conv-codex-1", "message_ids": ["m1"]},
        generation_mode="mechanical",
    ))

    assert draft.status == "pending"
    assert store.list()[0].draft_id == draft.draft_id

    edited = store.update(draft.draft_id, title="Use python -m pytest for AMH")
    assert edited.title == "Use python -m pytest for AMH"
    assert edited.status == "edited"

    skipped = store.skip(draft.draft_id)
    assert skipped.status == "skipped"


def test_draft_apply_writes_memory_item(tmp_path: Path, monkeypatch) -> None:
    from agent_brain.contracts.memory_item import MemoryType
    from agent_brain.memory.store.items_store import ItemsStore
    from agent_brain.product.memory_drafts import DraftStore, MemoryDraftInput

    monkeypatch.setenv("BRAIN_DIR", str(tmp_path))
    store = DraftStore(tmp_path)
    draft = store.create(MemoryDraftInput(
        title="Prefer local history MVP",
        summary="Local history sync should not call cloud APIs.",
        body="Only scan local agent histories for the first release.",
        type=MemoryType.decision.value,
        tags=["amh", "history-sync"],
        source_agent="codex",
        source_refs={"conversation_id": "conv-codex-2", "message_ids": ["m2"]},
        generation_mode="mechanical",
    ))

    applied = store.apply(draft.draft_id)

    assert applied.status == "applied"
    assert applied.item_id is not None
    items = list(ItemsStore(tmp_path / "items").iter_all())
    assert len(items) == 1
    item, body = items[0]
    assert item.title == "Prefer local history MVP"
    assert item.source.kind == "history_sync"
    assert item.source.transcript_id == "conv-codex-2"
    assert body.strip() == "Only scan local agent histories for the first release."
