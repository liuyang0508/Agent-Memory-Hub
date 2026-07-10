from datetime import datetime, timezone

from typer.testing import CliRunner

from agent_brain.interfaces.cli import app
from agent_brain.memory.store.items_store import ItemsStore, make_item_id
from agent_brain.contracts.memory_item import MemoryItem, MemoryType


def _seed(brain, type_, title, summary):
    store = ItemsStore(items_dir=brain / "items")
    now = datetime.now(timezone.utc).astimezone()
    store.write(MemoryItem(
        id=make_item_id(title, when=now),
        type=MemoryType(type_),
        created_at=now,
        title=title,
        summary=summary,
        refs={"urls": [f"https://example.test/{title}"]} if type_ == "decision" else {},
    ), "body")


def test_brief_cli_renders_grouped_and_footer(tmp_brain):
    _seed(tmp_brain, "signal", "open blocker", "waiting on api")
    _seed(tmp_brain, "decision", "use sse", "simpler than ws")
    r = CliRunner().invoke(app, ["brief"])
    assert r.exit_code == 0
    assert "open blocker" in r.output
    assert "use sse" in r.output
    assert "memory read --full" in r.output      # footer guidance present


def test_brief_cli_empty_pool(tmp_brain):
    r = CliRunner().invoke(app, ["brief"])
    assert r.exit_code == 0
    assert "no active context to resume" in r.output
