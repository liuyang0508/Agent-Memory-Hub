"""P1-6: generated ids must not collide on same-second same-title writes.

ids were ``mem-{YYYYMMDD-HHMMSS}-{slug}`` — 1s resolution. Two agents (or one
batch) writing the same title in the same second produced an identical id, and
ItemsStore.write raised an uncaught FileExistsError. A short random suffix makes
ids collision-proof while staying within the schema id pattern.
"""
from datetime import datetime, timezone


def test_same_second_same_title_ids_are_unique():
    from agent_brain.memory.store.items_store import make_item_id

    when = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)
    ids = {make_item_id("same title", when=when) for _ in range(100)}
    assert len(ids) == 100
    assert all(i.startswith("mem-20260529-120000-") for i in ids)


def test_generated_id_matches_schema_pattern():
    from agent_brain.memory.store.items_store import make_item_id
    from agent_brain.contracts.memory_item import _ID_PATTERN

    assert _ID_PATTERN.match(make_item_id("hello world"))
    assert _ID_PATTERN.match(make_item_id("", when=datetime(2026, 5, 29, 1, 2, 3, tzinfo=timezone.utc)))
    assert _ID_PATTERN.match(make_item_id("title", label="merged"))


def test_label_is_embedded():
    from agent_brain.memory.store.items_store import make_item_id

    mid = make_item_id("two notes", label="merged", when=datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc))
    assert "-merged-" in mid
