from agent_brain.memory.store.pending import PendingQueue, enqueue_write_record


def test_enqueue_then_replay_writes_item(tmp_brain):
    rec = {"v": 1, "op": "write", "origin": "test",
           "item": {"type": "fact", "title": "queued fact", "summary": "s",
                    "body": "b", "tags": [], "sensitivity": "internal",
                    "confidence": 0.7, "allow_unsafe": True}}
    path = enqueue_write_record(rec)
    assert path.exists()
    q = PendingQueue()
    stats = q.replay()
    assert stats.written == 1
    assert not path.exists()             # drained on success
    assert q.depth() == 0


def test_replay_is_idempotent_on_empty(tmp_brain):
    assert PendingQueue().replay().written == 0


def test_pending_preview_summarizes_records_without_replay(tmp_brain):
    rec = {
        "v": 1,
        "op": "write",
        "origin": "hook",
        "attempt": 2,
        "item": {
            "type": "decision",
            "title": "queued decision",
            "summary": "queued summary",
            "body": "body",
            "tags": ["ops"],
            "sensitivity": "internal",
            "confidence": 0.7,
        },
    }
    path = enqueue_write_record(rec)

    preview = PendingQueue().preview(limit=10)

    assert path.exists()
    assert preview.total == 1
    assert preview.records[0].path == str(path)
    assert preview.records[0].title == "queued decision"
    assert preview.records[0].type == "decision"
    assert preview.records[0].attempt == 2
    assert PendingQueue().depth() == 1
