from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from typer.testing import CliRunner

from agent_brain.contracts.conversation import ConversationMessageRecord, make_message_id
from agent_brain.contracts.resource import sha256_text
from agent_brain.interfaces.cli import app
from agent_brain.memory.evidence.conversation_store import ConversationStore


runner = CliRunner()


def test_cli_conversation_ingest_list_and_read(tmp_brain: Path) -> None:
    transcript = Path("tests/fixtures/sample_transcript.jsonl")

    ingest = runner.invoke(app, [
        "conversation",
        "ingest",
        str(transcript),
        "--agent",
        "codex",
        "--session",
        "sess-cli",
        "--project",
        "agent-memory-hub",
    ])
    assert ingest.exit_code == 0, ingest.output
    assert "written=3" in ingest.output
    assert "skipped=0" in ingest.output
    assert "conversation_id=" in ingest.output

    again = runner.invoke(app, [
        "conversation",
        "ingest",
        str(transcript),
        "--agent",
        "codex",
        "--session",
        "sess-cli",
        "--project",
        "agent-memory-hub",
    ])
    assert again.exit_code == 0, again.output
    assert "written=0" in again.output
    assert "skipped=3" in again.output

    listing = runner.invoke(app, [
        "conversation",
        "list",
        "--agent",
        "codex",
        "--project",
        "agent-memory-hub",
    ])
    assert listing.exit_code == 0, listing.output
    assert "sess-cli" in listing.output
    assert "3" in listing.output

    conversation_id = ingest.output.split("conversation_id=", 1)[1].split()[0]
    read = runner.invoke(app, ["conversation", "read", conversation_id, "--head", "1"])
    assert read.exit_code == 0, read.output
    assert "fix the failing test_cli_version" in read.output
    assert "mechanical-first" not in read.output

    messages = list(ConversationStore(tmp_brain).iter_messages(conversation_id))
    assert messages[0].retention.access_count == 1
    assert all(message.retention.access_count == 0 for message in messages[1:])


def test_cli_conversation_rebalance_reports_distribution(tmp_brain: Path) -> None:
    now = datetime.now(timezone.utc)
    store = ConversationStore(tmp_brain)
    conversation_id = "conv-4444444444444444-cli-tier"
    for text, age_days in (("fresh", 1), ("frozen", 420)):
        store.write_message(ConversationMessageRecord(
            id=make_message_id(conversation_id=conversation_id, role="user", content_text=text),
            conversation_id=conversation_id,
            source_agent="codex",
            session_id="sess-cli-tier",
            role="user",
            content_text=text,
            content_sha256=sha256_text(text),
            observed_at=now - timedelta(days=age_days),
            retention={"importance": 0.0, "half_life_days": 30},
        ))

    result = runner.invoke(app, ["conversation", "rebalance"])

    assert result.exit_code == 0, result.output
    assert "hot" in result.output
    assert "frozen" in result.output
    assert "2 message(s)" in result.output
