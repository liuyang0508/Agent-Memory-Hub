"""P2-3: sanitize slugs and validate enums in write paths.

- Titles containing ``/`` or ``\\`` used to flow straight into make_item_id's
  slug. The id schema pattern (_ID_PATTERN) forbids ``/`` and ``\\``, so the
  generated id was rejected by MemoryItem with a raw ValueError — and even if it
  had slipped through, the separator would scatter the md file into accidental
  subdirectories of items_dir. Fixed inside make_item_id so all call sites
  (cli, mcp, web x3, hermes) benefit.
- Invalid --type / --sensitivity raised raw tracebacks instead of a clean,
  user-facing error at the cli / mcp boundary.
"""
from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner


def _combined(result) -> str:
    out = result.output
    try:
        out += result.stderr
    except (ValueError, Exception):
        pass
    return out


@pytest.mark.parametrize("title", ["fix a/b bug", r"win\path\x", "a/b/c", "///", r"\\"])
def test_make_item_id_strips_path_separators(title):
    from agent_brain.memory.store.items_store import make_item_id
    from agent_brain.contracts.memory_item import MemoryItem, MemoryType, _ID_PATTERN

    when = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)
    iid = make_item_id(title, when=when)

    assert "/" not in iid and "\\" not in iid, iid
    assert _ID_PATTERN.match(iid), iid
    # Before the fix this raised: "id must match ... got 'mem-...-a/b...'".
    item = MemoryItem(id=iid, type=MemoryType.fact, created_at=when, title=title, summary="s")
    assert item.id == iid


def test_cli_write_rejects_bad_type(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path))
    from agent_brain.interfaces.cli import app

    result = CliRunner().invoke(
        app, ["write", "--type", "bogus", "--title", "t", "--summary", "s"]
    )
    assert result.exit_code != 0
    assert "Traceback" not in _combined(result)
    assert "invalid --type" in _combined(result)
    # Early exit means nothing was written.
    assert not list(tmp_path.glob("items/*.md"))


def test_cli_write_rejects_bad_sensitivity(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path))
    from agent_brain.interfaces.cli import app

    result = CliRunner().invoke(
        app,
        ["write", "--type", "fact", "--title", "t", "--summary", "s",
         "--sensitivity", "ultra-secret"],
    )
    assert result.exit_code != 0
    assert "Traceback" not in _combined(result)
    assert "invalid --sensitivity" in _combined(result)


def test_mcp_write_memory_rejects_bad_type(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path))
    (tmp_path / "items").mkdir()
    from agent_brain.interfaces.mcp import server as mcp_server

    result = mcp_server.write_memory(type="bogus", title="t", summary="s")
    assert result.get("status") == "error"
    assert "invalid type" in result.get("reason", "")
    assert not list((tmp_path / "items").glob("*.md"))


def test_mcp_write_memory_rejects_bad_sensitivity(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path))
    (tmp_path / "items").mkdir()
    from agent_brain.interfaces.mcp import server as mcp_server

    result = mcp_server.write_memory(
        type="fact", title="t", summary="s", sensitivity="ultra-secret"
    )
    assert result.get("status") == "error"
    assert "invalid sensitivity" in result.get("reason", "")
    assert not list((tmp_path / "items").glob("*.md"))
