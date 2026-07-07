from datetime import datetime, timezone

from typer.testing import CliRunner

from agent_brain.interfaces.cli import app
from agent_brain.memory.store.items_store import ItemsStore, make_item_id
from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def _seed(brain, body):
    store = ItemsStore(items_dir=brain / "items")
    now = datetime.now(timezone.utc).astimezone()
    item = MemoryItem(id=make_item_id("big", when=now), type=MemoryType("episode"),
                      created_at=now, title="big item", summary="s")
    store.write(item, body)
    return item.id


def test_read_default_returns_full_body(tmp_brain):
    body = "B" * 9000
    iid = _seed(tmp_brain, body)
    r = CliRunner().invoke(app,["read", iid])
    assert r.exit_code == 0
    assert ("B" * 9000) in r.stdout          # full body, byte-identical contract


def test_read_head_bounds_body(tmp_brain):
    iid = _seed(tmp_brain, "B" * 9000)
    r = CliRunner().invoke(app,["read", iid, "--head", "100"])
    assert r.exit_code == 0
    assert r.stdout.count("B") <= 200        # bounded
    assert "more" in r.stdout.lower()        # truncation marker shown


def test_read_view_locator_omits_body(tmp_brain):
    iid = _seed(tmp_brain, "detail body should stay hidden")
    r = CliRunner().invoke(app, ["read", iid, "--view", "locator"])
    assert r.exit_code == 0
    assert "big item" in r.stdout
    assert "s" in r.stdout
    assert "detail body should stay hidden" not in r.stdout


def test_read_view_detail_returns_body(tmp_brain):
    iid = _seed(tmp_brain, "detail body should be visible")
    r = CliRunner().invoke(app, ["read", iid, "--view", "detail"])
    assert r.exit_code == 0
    assert "detail body should be visible" in r.stdout
