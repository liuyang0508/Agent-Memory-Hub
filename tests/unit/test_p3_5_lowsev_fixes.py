"""P3-5: eight low-severity correctness fixes (audit §3).

#33 gc --dry-run summary always reported 0 instead of the candidate count.
#34 `read` returned silently (exit 0, no output) when the resolved item failed to parse.
#35 `doctor` crashed on a malformed ~/.claude/settings.json.
#36 _resolve_id globbed only the top level while iter_all walks recursively, so
    archived items were unaddressable by read/delete.
#37 update_memory changing `type` left retention.decay_class stale.
#38 confirm/batch_confirm: the md schema rejected out-of-range confidence while
    HubIndex.update_confidence clamped — inconsistent. Now both clamp.
#47 HealthScore.grade ignored drift once issue_rate dropped below the B tier.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from agent_brain.interfaces.cli import app
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.observability import HealthScore
from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.agent_integrations import claude_code, codex

runner = CliRunner()


def _combined(result) -> str:
    out = result.output or ""
    try:
        out += result.stderr
    except (ValueError, Exception):
        pass
    return out


# ----- #47 HealthScore.grade -----


def test_grade_demoted_by_high_drift_with_low_issue_rate():
    # issue_rate == 0 but 50 drift findings. Before the fix the B/C/D tiers
    # ignored drift entirely, so this returned "B"; now drift demotes it.
    score = HealthScore(total_items=100, items_with_issues=0, drift_findings=50)
    assert score.issue_rate == 0.0
    assert score.grade == "D"
    assert not score.healthy


def test_grade_existing_tiers_preserved():
    assert HealthScore(total_items=100).grade == "A"
    assert HealthScore(total_items=100, items_with_issues=20).grade == "C"
    # drift within the tier ceiling stays in tier
    assert HealthScore(total_items=100, drift_findings=10).grade == "B"


# ----- #33 gc --dry-run counter -----


def _write_item(store, suffix, tags, days_old):
    item = MemoryItem(
        id=f"mem-20260101-000000-{suffix}-aaaa",
        type=MemoryType.signal,
        created_at=datetime.now(timezone.utc) - timedelta(days=days_old),
        title=f"S {suffix}",
        summary="s",
        tags=tags,
    )
    store.write(item, "body")


def test_gc_dry_run_reports_candidate_count(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path))
    store = ItemsStore(tmp_path / "items")
    _write_item(store, "old1", ["session-end"], days_old=30)
    _write_item(store, "old2", ["auto-captured"], days_old=30)
    result = runner.invoke(app, ["gc", "--dry-run"])
    assert result.exit_code == 0
    # Before the fix this said "would delete 0 items".
    assert "would delete 2 items" in result.output
    # Nothing actually removed.
    assert len(list((tmp_path / "items").glob("*.md"))) == 2


# ----- #34 read silent on parse failure -----


def test_read_errors_on_unparseable_item(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path))
    items = tmp_path / "items"
    items.mkdir(parents=True)
    bad = items / "mem-20990101-000000-broken-zzzz.md"
    bad.write_text("this file has no frontmatter and cannot parse\n", encoding="utf-8")
    result = runner.invoke(app, ["read", "mem-20990101-000000-broken-zzzz"])
    # Before the fix: exit_code 0 with empty output (silent).
    assert result.exit_code == 1
    combined = _combined(result)
    assert "could not be parsed" in combined or "not found" in combined


# ----- #35 doctor adapter footprint aggregation -----


def _isolate_doctor_adapter_paths(home, monkeypatch):
    monkeypatch.setattr(codex, "AGENTS_MD", home / ".codex" / "AGENTS.md")
    monkeypatch.setattr(codex, "CODEX_HOOKS_JSON", home / ".codex" / "hooks.json")
    monkeypatch.setattr(codex, "CODEX_CONFIG_TOML", home / ".codex" / "config.toml")
    monkeypatch.setattr(claude_code, "SETTINGS_PATH", home / ".claude" / "settings.json")
    monkeypatch.setattr(claude_code, "AWARENESS_PATH", home / ".claude" / "CLAUDE.md")
    monkeypatch.setenv("AGENT_MEMORY_HUB_BIN", str(home / ".local" / "bin"))


def test_doctor_skips_non_amh_malformed_settings(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text("{ this is not valid json ", encoding="utf-8")
    brain = tmp_path / "brain"
    (brain / "items").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    _isolate_doctor_adapter_paths(home, monkeypatch)
    result = runner.invoke(app, ["doctor"])
    assert result.exception is None, result.exception
    assert result.exit_code == 0
    assert "claude_code" not in result.output
    assert "Claude Code settings" not in result.output


def test_doctor_counts_only_amh_claude_hooks(tmp_path, monkeypatch):
    home = tmp_path / "home"
    brain = tmp_path / "brain"
    (brain / "items").mkdir(parents=True)
    (home / ".claude").mkdir(parents=True)
    hook_events = [
        "SessionStart",
        "UserPromptSubmit",
        "Stop",
        "PreCompact",
        "PostCompact",
        "SubagentStart",
        "SubagentStop",
    ]
    settings = {
        "hooks": {
            event: [
                {"hooks": [{"type": "command", "command": "/usr/local/bin/foreign-audit"}]},
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": (
                                "PATH=/usr/bin:/bin AGENT_MEMORY_HUB_ADAPTER=claude_code "
                                f"/repo/agent_runtime_kit/hooks/{event}.sh"
                            ),
                        }
                    ]
                },
            ]
            for event in hook_events
        }
    }
    (home / ".claude" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    _isolate_doctor_adapter_paths(home, monkeypatch)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "claude_code" in result.output
    assert "ERROR" in result.output
    assert "7 AMH registered" not in result.output


def test_doctor_reports_invalid_empty_search_index(tmp_path, monkeypatch):
    brain = tmp_path / "brain"
    (brain / "items").mkdir(parents=True)
    (brain / "index.db").touch()
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    _isolate_doctor_adapter_paths(home, monkeypatch)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "Search index" in result.output
    assert "INVALID" in result.output
    assert "reindex" in result.output


def test_doctor_reports_broken_memory_cli_shim_row(tmp_path, monkeypatch):
    home = tmp_path / "home"
    brain = tmp_path / "brain"
    shim = home / ".local" / "bin" / "memory"
    target = tmp_path / "deleted" / ".venv" / "bin" / "memory"
    (brain / "items").mkdir(parents=True)
    shim.parent.mkdir(parents=True)
    shim.write_text(f'#!/bin/sh\nexec "{target}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    _isolate_doctor_adapter_paths(home, monkeypatch)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "memory CLI shim" in result.output
    assert "re-run install.sh" in result.output
    assert "->" in result.output


# ----- #36 archived items addressable -----


def _archive(tmp_path, item, body):
    store = ItemsStore(tmp_path / "items")
    store.write(item, body)
    archived = tmp_path / "items" / "archived"
    archived.mkdir(parents=True, exist_ok=True)
    (tmp_path / "items" / f"{item.id}.md").rename(archived / f"{item.id}.md")
    return archived


def test_read_resolves_archived_item(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path))
    item = MemoryItem(
        id="mem-20260101-000000-arch-bbbb",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Archived one",
        summary="s",
    )
    _archive(tmp_path, item, "archived body")
    result = runner.invoke(app, ["read", "mem-20260101-000000-arch"])
    # Before the fix: "item not found" (top-level glob missed archived/).
    assert result.exit_code == 0, _combined(result)
    assert item.id in result.output
    assert "archived body" in result.output


def test_delete_resolves_archived_item(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path))
    item = MemoryItem(
        id="mem-20260101-000000-del-cccc",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Archived del",
        summary="s",
    )
    archived = _archive(tmp_path, item, "x")
    result = runner.invoke(app, ["delete", "mem-20260101-000000-del"])
    # Must not raise FileNotFoundError now that _resolve_id is recursive.
    assert result.exit_code == 0, _combined(result)
    assert not (archived / f"{item.id}.md").exists()


# ----- MCP-based fixtures (#37, #38) -----


@pytest.fixture
def mcp_env(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path))
    monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")
    (tmp_path / "items").mkdir(parents=True, exist_ok=True)
    from agent_brain.interfaces.mcp import server as mcp_server

    mcp_server._components_cache.clear()
    yield mcp_server
    mcp_server._components_cache.clear()


# ----- #37 update_memory type -> decay_class -----


def test_update_memory_type_change_updates_decay_class(mcp_env):
    m = mcp_env
    item_id = m.write_memory(type="fact", title="Decay item", summary="s", body="b")["id"]
    assert m.read_memory(item_id)["frontmatter"]["retention"]["decay_class"] == "fact"

    m.update_memory(item_id=item_id, type="signal")
    fm = m.read_memory(item_id)["frontmatter"]
    # Before the fix decay_class stayed "fact"; signal maps to "ephemeral".
    assert fm["type"] == "signal"
    assert fm["retention"]["decay_class"] == "ephemeral"


# ----- #38 confirm / batch_confirm clamp -----


def test_confirm_memory_clamps_out_of_range(mcp_env):
    m = mcp_env
    item_id = m.write_memory(type="fact", title="Conf item", summary="s")["id"]
    # Before the fix: confidence=1.5 raised a pydantic ValidationError (md schema
    # le=1.0) even though the index clamps to 1.0.
    res = m.confirm_memory(item_id=item_id, confidence=1.5)
    assert res["confidence"] == 1.0
    assert m.read_memory(item_id)["frontmatter"]["confidence"] == 1.0


def test_batch_confirm_clamps_out_of_range(mcp_env):
    m = mcp_env
    a = m.write_memory(type="fact", title="A item", summary="s")["id"]
    b = m.write_memory(type="fact", title="B item", summary="s")["id"]
    res = m.batch_confirm(item_ids=[a, b], confidence=-0.5)
    assert res["confirmed"] == 2
    assert all(m.read_memory(i)["frontmatter"]["confidence"] == 0.0 for i in (a, b))
