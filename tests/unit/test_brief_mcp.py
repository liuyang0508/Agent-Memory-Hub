from datetime import datetime, timezone

import pytest

from agent_brain.memory.store.items_store import ItemsStore, make_item_id
from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Sensitivity


def test_brief_memory_returns_structured_tiers(tmp_brain):
    store = ItemsStore(items_dir=tmp_brain / "items")
    now = datetime.now(timezone.utc).astimezone()
    store.write(MemoryItem(id=make_item_id("blk", when=now), type=MemoryType("signal"),
                           created_at=now, title="open blocker", summary="waiting"), "b")
    import agent_brain.interfaces.mcp.server as m
    out = m.brief_memory(budget_tokens=1500)
    assert out["total_shown"] >= 1
    assert "footer" in out
    names = [t["name"] for t in out["tiers"]]
    assert "open_signals" in names


def test_brief_memory_filters_noninjectable_items_before_tiering(tmp_brain):
    store = ItemsStore(tmp_brain / "items")
    now = datetime.now(timezone.utc).astimezone()
    safe = MemoryItem(
        id=make_item_id("safe-signal", when=now),
        type=MemoryType.signal,
        created_at=now,
        title="safe gateway signal",
        summary="safe summary",
        confidence=0.9,
    )
    forbidden = [
        safe.model_copy(update={
            "id": make_item_id("private-signal", when=now),
            "title": "private gateway signal",
            "summary": "private gateway summary",
            "sensitivity": Sensitivity.private,
        }),
        safe.model_copy(update={
            "id": make_item_id("secret-signal", when=now),
            "title": "secret gateway signal",
            "summary": "secret gateway summary",
            "sensitivity": Sensitivity.secret,
        }),
        safe.model_copy(update={
            "id": make_item_id("review-signal", when=now),
            "title": "review gateway signal",
            "summary": "review gateway summary",
            "tags": ["needs-review"],
        }),
        safe.model_copy(update={
            "id": make_item_id("unverified-signal", when=now),
            "title": "unverified gateway signal",
            "summary": "unverified gateway summary",
            "tags": ["unverified-boundary"],
        }),
        safe.model_copy(update={
            "id": make_item_id("superseded-signal", when=now),
            "title": "superseded gateway signal",
            "summary": "superseded gateway summary",
            "superseded_by": safe.id,
        }),
    ]
    for value in (safe, *forbidden):
        store.write(value, f"body:{value.title}")

    import agent_brain.interfaces.mcp.server as m

    out = m.brief_memory(budget_tokens=1500)

    serialized = repr(out)
    assert "safe gateway signal" in serialized
    for value in forbidden:
        assert value.id not in serialized
        assert value.title not in serialized
        assert value.summary not in serialized
        assert f"body:{value.title}" not in serialized
    assert out["total_shown"] == 1
    assert out["total_withheld"] == len(forbidden)


@pytest.mark.parametrize("query", ["memory", ""])
def test_brief_memory_noninjectable_query_returns_no_items(tmp_brain, query):
    store = ItemsStore(tmp_brain / "items")
    now = datetime.now(timezone.utc).astimezone()
    value = MemoryItem(
        id=make_item_id("weak-brief", when=now),
        type=MemoryType.episode,
        created_at=now,
        title="memory",
        summary="memory",
        confidence=0.9,
    )
    store.write(value, "memory")

    import agent_brain.interfaces.mcp.server as m

    out = m.brief_memory(budget_tokens=1500, query=query)

    assert out["total_shown"] == 0
    assert out["total_withheld"] == 1


def test_read_memory_head_bounds_body(tmp_brain):
    store = ItemsStore(items_dir=tmp_brain / "items")
    now = datetime.now(timezone.utc).astimezone()
    iid = make_item_id("big", when=now)
    store.write(MemoryItem(id=iid, type=MemoryType("episode"), created_at=now,
                           title="big", summary="s"), "B" * 9000)
    import agent_brain.interfaces.mcp.server as m
    full = m.read_memory(iid)
    full_len = len(full["body"])
    assert full_len >= 9000 and not full.get("body_truncated")   # default unchanged
    bounded = m.read_memory(iid, head=100)
    assert len(bounded["body"]) == 100
    assert bounded["body_truncated"] is True and bounded["full_chars"] == full_len
