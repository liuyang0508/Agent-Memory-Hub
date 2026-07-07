from __future__ import annotations

from pathlib import Path


def test_mcp_lists_and_reads_conversation_evidence(tmp_brain: Path) -> None:
    from agent_brain.memory.evidence.conversation_store import ConversationStore
    from agent_brain.interfaces.mcp.tools.conversation import (
        list_conversations,
        read_conversation,
    )

    store = ConversationStore(tmp_brain)
    result = store.ingest_transcript(
        Path("tests/fixtures/sample_transcript.jsonl"),
        source_agent="codex",
        session_id="sess-mcp",
        project="agent-memory-hub",
    )

    listed = list_conversations(source_agent="codex", project="agent-memory-hub")
    assert listed["count"] == 1
    assert listed["conversations"][0]["conversation_id"] == result.conversation_id
    assert listed["conversations"][0]["message_count"] == 3

    read = read_conversation(result.conversation_id, head=2)
    assert read["conversation_id"] == result.conversation_id
    assert read["count"] == 2
    assert read["truncated"] is True
    assert read["messages"][0]["role"] == "user"
    assert "test_cli_version" in read["messages"][0]["content_text"]

    messages = list(store.iter_messages(result.conversation_id))
    assert [message.retention.access_count for message in messages] == [1, 1, 0]
