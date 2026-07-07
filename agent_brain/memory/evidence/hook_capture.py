"""Fail-open hook capture for raw conversation evidence."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_brain.contracts.conversation import (
    ConversationMessageRecord,
    make_conversation_id,
    make_message_id,
)
from agent_brain.contracts.resource import sha256_text
from agent_brain.memory.evidence.conversation_store import ConversationIngestResult, ConversationStore
from agent_brain.memory.evidence.multimodal_capture import capture_multimodal_prompt_resources


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _brain_dir() -> Path:
    return Path(os.environ.get("BRAIN_DIR", "~/.agent-memory-hub")).expanduser()


def _adapter(payload: dict[str, Any]) -> str:
    return (
        _text(os.environ.get("AGENT_MEMORY_HUB_ADAPTER"))
        or _text(payload.get("adapter"))
        or _text(payload.get("source_agent"))
        or "unknown"
    )


def _project(payload: dict[str, Any]) -> str | None:
    return _text(os.environ.get("AGENT_MEMORY_HUB_PROJECT")) or _text(payload.get("project"))


def capture_prompt_payload(payload: dict[str, Any], *, root_dir: Path | None = None) -> bool:
    """Snapshot a UserPromptSubmit prompt into ``sources/conversations``.

    This is a live loss-prevention capture, not the authoritative full
    transcript. The Stop hook's transcript ingest supersedes matching live
    prompts when a transcript becomes available.
    """
    prompt = _text(payload.get("prompt"))
    if prompt is None:
        return False

    source_agent = _adapter(payload)
    session_id = _text(payload.get("session_id"))
    hook_event = _text(payload.get("hook_event_name")) or "UserPromptSubmit"
    cwd = _text(payload.get("cwd"))
    conversation_id = make_conversation_id(source_agent, session_id)
    source_uri = f"hook://{source_agent}/{hook_event}/{session_id or 'unknown-session'}"
    message = ConversationMessageRecord(
        id=make_message_id(
            conversation_id=conversation_id,
            role="user",
            content_text=prompt,
            source_uri=source_uri,
        ),
        conversation_id=conversation_id,
        source_agent=source_agent,
        session_id=session_id,
        role="user",
        content_text=prompt,
        content_sha256=sha256_text(prompt),
        observed_at=datetime.now(timezone.utc),
        source_uri=source_uri,
        project=_project(payload),
        cwd=cwd,
        tags=["hook", "live-prompt"],
        metadata={"hook_event_name": hook_event, "capture_kind": "live_prompt"},
    )
    brain_dir = root_dir or _brain_dir()
    written = ConversationStore(brain_dir).write_message(message)
    try:
        capture_multimodal_prompt_resources(payload, root_dir=brain_dir)
    except Exception:
        pass
    return written


def ingest_transcript_payload(
    payload: dict[str, Any],
    *,
    root_dir: Path | None = None,
) -> ConversationIngestResult | None:
    """Snapshot a hook transcript path into ``sources/conversations`` if present."""
    transcript_path = _text(payload.get("transcript_path"))
    if transcript_path is None:
        return None
    transcript = Path(transcript_path).expanduser()
    if not transcript.exists() or not transcript.is_file():
        return None
    store = ConversationStore(root_dir or _brain_dir())
    result = store.ingest_transcript(
        transcript,
        source_agent=_adapter(payload),
        session_id=_text(payload.get("session_id")),
        project=_project(payload),
        cwd=_text(payload.get("cwd")),
        tags=["hook", "transcript"],
    )
    _drop_superseded_live_prompts(store, result.conversation_id)
    return result


def _drop_superseded_live_prompts(store: ConversationStore, conversation_id: str) -> int:
    messages = list(store.iter_messages(conversation_id))
    transcript_keys = {
        (message.role, message.content_sha256)
        for message in messages
        if "transcript" in message.tags
    }
    if not transcript_keys:
        return 0
    superseded_ids = [
        message.id
        for message in messages
        if message.metadata.get("capture_kind") == "live_prompt"
        and (message.role, message.content_sha256) in transcript_keys
    ]
    return store.remove_messages(conversation_id, superseded_ids)


def _load_payload() -> dict[str, Any]:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prompt", "transcript"))
    args = parser.parse_args(argv)
    payload = _load_payload()
    if args.mode == "prompt":
        capture_prompt_payload(payload)
    else:
        ingest_transcript_payload(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "capture_prompt_payload",
    "ingest_transcript_payload",
    "main",
]
