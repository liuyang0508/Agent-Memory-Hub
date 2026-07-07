from datetime import datetime, timezone

from agent_brain.memory.store.items_store import ItemsStore, make_item_id
from agent_brain.contracts.memory_item import MemoryItem, MemoryType


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
