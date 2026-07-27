from __future__ import annotations

from typer.testing import CliRunner

from agent_brain.interfaces.cli import app
from agent_brain.memory.store.items_store import ItemsStore


runner = CliRunner()


def test_handoff_writes_complete_checkpoint_and_resume_reads_it(tmp_brain, tmp_path):
    result = runner.invoke(
        app,
        [
            "handoff",
            "--objective",
            "Finish shared-agent recall gate",
            "--done",
            "Added the deterministic fixture",
            "--pending",
            "Wire fixture into release gate",
            "--decision",
            "Use the existing gateway | preserves policy parity | avoids split behavior",
            "--next",
            "Add the fixture to governance readiness",
            "--verify",
            "pytest tests/unit/test_handoff_cli.py -q",
            "--project",
            "agent-memory-hub",
            "--repo",
            str(tmp_path),
            "--agent",
            "codex",
            "--target-agent",
            "claude_code",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "handoff_written=mem-" in result.output
    item, body = next(ItemsStore(tmp_brain / "items").iter_all())
    assert str(item.type) == "handoff"
    assert item.project == "agent-memory-hub"
    assert item.validity.ttl_hours == 720
    assert item.validity.adapter == "codex"
    assert "## 3. Decisions" in body
    assert "Wire fixture into release gate" in body

    resumed = runner.invoke(app, ["resume", "--project", "agent-memory-hub", "--fail-empty"])

    assert resumed.exit_code == 0, resumed.output
    assert f"resuming={item.id}" in resumed.output
    assert "Finish shared-agent recall gate" in resumed.output
    assert "pytest tests/unit/test_handoff_cli.py -q" in resumed.output


def test_handoff_rejects_incomplete_checkpoint(tmp_brain):
    result = runner.invoke(app, ["handoff", "--objective", "Too little context"])

    assert result.exit_code == 2
    assert "--done or --pending" in result.output
    assert "--next" in result.output
    assert "--verify" in result.output
    assert list((tmp_brain / "items").glob("*.md")) == []


def test_resume_empty_can_fail_for_adapter_fallback(tmp_brain):
    result = runner.invoke(app, ["resume", "--fail-empty"])

    assert result.exit_code == 3
    assert "no resumable handoff" in result.output
