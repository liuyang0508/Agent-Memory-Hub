from agent_brain.memory.evidence.harvest.dedup import span_hash, is_duplicate_span


def test_span_hash_is_stable_and_normalized():
    assert span_hash("  Hello   World \n") == span_hash("hello world")


def test_is_duplicate_span_detects_seen(tmp_brain):
    seen = {"sha256:" + span_hash("abc")}
    assert is_duplicate_span("abc", seen) is True
    assert is_duplicate_span("xyz", seen) is False
