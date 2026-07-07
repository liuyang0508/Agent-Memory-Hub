from agent_brain.contracts.memory_item import MemoryItem, Source, MemoryType
from datetime import datetime, timezone


def _mk(**kw):
    base = dict(
        id="mem-20260531-000000-test1234",
        type=MemoryType.fact,
        created_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
        title="t", summary="s",
    )
    base.update(kw)
    return MemoryItem(**base)


def test_source_defaults_to_manual_and_schema_is_1():
    item = _mk()
    assert item.schema_version == "1"
    assert item.source.kind == "manual"
    assert item.source.span_hash is None


def test_source_roundtrips_harvested_provenance():
    item = _mk(source=Source(kind="harvested", transcript_id="abc",
                             span_hash="sha256:deadbeef", extractor="mechanical"))
    dumped = item.model_dump()
    again = MemoryItem(**dumped)
    assert again.source.kind == "harvested"
    assert again.source.span_hash == "sha256:deadbeef"


def test_legacy_0_4_item_without_source_still_loads():
    item = _mk(schema_version="0.4")  # old items had no `source`
    assert item.source.kind == "manual"   # filled by default, no error
