from datetime import datetime, timedelta, timezone

import pytest

from agent_brain.memory.store.items_store import ItemsStore, make_item_id
from agent_brain.memory.recall.brief import build_brief, Brief
from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Sensitivity


def _seed(store: ItemsStore, type_: str, title: str, summary: str, *,
          days_ago: int = 0, project: str | None = None, tags=None, body: str = "x"):
    now = datetime.now(timezone.utc).astimezone() - timedelta(days=days_ago)
    item = MemoryItem(
        id=make_item_id(title, when=now), type=MemoryType(type_), created_at=now,
        title=title, summary=summary, project=project, tags=tags or [],
        refs={"urls": [f"https://example.test/{title}"]} if type_ == "decision" else {},
    )
    store.write(item, body)
    return item


def test_brief_orders_signal_handoff_decision_episode(tmp_path):
    store = ItemsStore(items_dir=tmp_path / "items")
    _seed(store, "episode", "ep one", "did a thing")
    _seed(store, "signal", "blocker", "waiting on X")
    _seed(store, "decision", "chose Y", "because Z")
    _seed(store, "handoff", "handoff A", "pick up here")
    brief = build_brief(store, budget_tokens=1500)
    order = [t.name for t in brief.tiers if t.shown]
    assert order == ["open_signals", "recent_handoffs", "key_decisions", "recent_episodes"]


def test_brief_excludes_session_noise(tmp_path):
    store = ItemsStore(items_dir=tmp_path / "items")
    _seed(store, "signal", "Session abc active", "noise", tags=["session-active", "auto-captured"])
    _seed(store, "signal", "real blocker", "waiting on Y")
    brief = build_brief(store, budget_tokens=1500)
    titles = [i.title for t in brief.tiers for i in t.shown]
    assert "real blocker" in titles
    assert "Session abc active" not in titles
    assert brief.total_withheld == 0


def test_brief_filters_gateway_forbidden_items_and_counts_withheld(tmp_path):
    store = ItemsStore(items_dir=tmp_path / "items")
    safe = _seed(store, "signal", "safe gateway signal", "safe summary")
    now = safe.created_at
    forbidden = [
        safe.model_copy(update={
            "id": make_item_id("private gateway signal", when=now),
            "title": "private gateway signal",
            "sensitivity": Sensitivity.private,
        }),
        safe.model_copy(update={
            "id": make_item_id("secret gateway signal", when=now),
            "title": "secret gateway signal",
            "sensitivity": Sensitivity.secret,
        }),
        safe.model_copy(update={
            "id": make_item_id("review gateway signal", when=now),
            "title": "review gateway signal",
            "tags": ["needs-review"],
        }),
        safe.model_copy(update={
            "id": make_item_id("unverified gateway signal", when=now),
            "title": "unverified gateway signal",
            "tags": ["unverified-boundary"],
        }),
        safe.model_copy(update={
            "id": make_item_id("superseded gateway signal", when=now),
            "title": "superseded gateway signal",
            "superseded_by": safe.id,
        }),
    ]
    for item in forbidden:
        store.write(item, f"body:{item.title}")

    brief = build_brief(store, budget_tokens=1500)

    assert [item.title for tier in brief.tiers for item in tier.shown] == [safe.title]
    assert brief.total_shown == 1
    assert brief.total_withheld == len(forbidden)


@pytest.mark.parametrize("query", ["memory", ""])
def test_brief_explicit_noninjectable_query_withholds_all_items(tmp_path, query):
    store = ItemsStore(items_dir=tmp_path / "items")
    _seed(store, "episode", "memory", "memory")

    brief = build_brief(store, budget_tokens=1500, query=query)

    assert brief.total_shown == 0
    assert brief.total_withheld == 1


def test_brief_respects_budget_and_announces_withheld(tmp_path):
    store = ItemsStore(items_dir=tmp_path / "items")
    for n in range(40):
        _seed(store, "episode", f"episode number {n}", "a reasonably long summary " * 4, days_ago=n)
    brief = build_brief(store, budget_tokens=200)   # tiny budget
    shown = sum(len(t.shown) for t in brief.tiers)
    withheld = sum(t.withheld for t in brief.tiers)
    assert shown >= 1
    assert withheld >= 1                       # some announced, not silently dropped
    assert shown + withheld == 40


def test_brief_filters_by_project(tmp_path):
    store = ItemsStore(items_dir=tmp_path / "items")
    _seed(store, "decision", "proj-a decision", "s", project="a")
    _seed(store, "decision", "proj-b decision", "s", project="b")
    brief = build_brief(store, budget_tokens=1500, project="a")
    titles = [i.title for t in brief.tiers for i in t.shown]
    assert titles == ["proj-a decision"]


def test_brief_empty_pool(tmp_path):
    store = ItemsStore(items_dir=tmp_path / "items")
    brief = build_brief(store, budget_tokens=1500)
    assert all(not t.shown for t in brief.tiers)
    assert brief.total_shown == 0
