"""MCP tools for raw conversation evidence."""
from __future__ import annotations

from typing import Any

from agent_brain.interfaces.mcp.tools._shared import _brain_dir


def list_conversations(
    source_agent: str | None = None,
    project: str | None = None,
    tenant_id: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """List captured raw conversation evidence streams.

    This reads the evidence/source layer, not MemoryItem knowledge. Use it when
    a user explicitly asks to inspect raw session evidence or validate whether a
    transcript has been captured.
    """
    from agent_brain.memory.evidence.conversation_store import ConversationStore

    conversations = list(ConversationStore(_brain_dir()).iter_conversations(
        source_agent=source_agent,
        project=project,
        tenant_id=tenant_id,
    ))
    shown = conversations[:max(0, limit)]
    return {
        "count": len(shown),
        "total": len(conversations),
        "conversations": [
            {
                "conversation_id": conversation.conversation_id,
                "source_agent": conversation.source_agent,
                "session_id": conversation.session_id,
                "project": conversation.project,
                "message_count": conversation.message_count,
                "tier": str(conversation.tier),
                "first_observed_at": conversation.first_observed_at.isoformat(),
                "last_observed_at": conversation.last_observed_at.isoformat(),
            }
            for conversation in shown
        ],
    }


def read_conversation(
    conversation_id: str,
    head: int = 20,
) -> dict[str, Any]:
    """Read a bounded slice of raw conversation evidence by conversation id."""
    from agent_brain.memory.evidence.conversation_store import ConversationStore

    store = ConversationStore(_brain_dir())
    all_messages = list(store.iter_messages(conversation_id))
    shown = all_messages[:max(0, head)]
    if not all_messages:
        raise ValueError(f"conversation not found or empty: {conversation_id}")
    payload = {
        "conversation_id": conversation_id,
        "count": len(shown),
        "total": len(all_messages),
        "truncated": len(shown) < len(all_messages),
        "messages": [
            {
                "id": message.id,
                "role": message.role,
                "content_text": message.content_text,
                "content_sha256": message.content_sha256,
                "source_agent": message.source_agent,
                "session_id": message.session_id,
                "project": message.project,
                "sensitivity": str(message.sensitivity),
                "tier": str(message.tier),
                "source_uri": message.source_uri,
                "source_offset_start": message.source_offset_start,
                "source_offset_end": message.source_offset_end,
                "observed_at": message.observed_at.isoformat(),
            }
            for message in shown
        ],
    }
    store.touch_conversation(conversation_id, message_ids=[message.id for message in shown])
    return payload


def register(mcp) -> None:
    """Register raw conversation evidence tools."""
    mcp.tool()(list_conversations)
    mcp.tool()(read_conversation)


__all__ = ["list_conversations", "read_conversation", "register"]
