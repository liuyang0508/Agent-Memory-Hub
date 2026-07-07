"""P3-6: adapter robustness.

Covers:
  - registry auto-discovery (bare package import registers every adapter)
  - atomic wukong context-file writes (no leftover .tmp)
  - missing-END sentinel hardening for wukong install/uninstall
  - missing-END sentinel hardening for codex _upsert_block / _remove_block

Each test below fails against the pre-P3-6 code (ImportError for
discover_adapters; ValueError from str.index(END) on truncated blocks) and
passes after the fix.
"""

import subprocess
import sys

import pytest


def _patch_wukong_paths(wk, tmp_path, monkeypatch):
    ctx = tmp_path / ".wukong" / "brain_context.md"
    mcp = tmp_path / ".real" / ".mcp" / "mcpServerConfig.json"
    monkeypatch.setattr(wk, "CONTEXT_FILE", ctx)
    monkeypatch.setattr(wk, "MCP_CONFIG_PATH", mcp)
    return ctx, mcp


def test_auto_discovery_on_bare_package_import():
    """A clean interpreter that only imports the package (no per-adapter
    imports) must still see every adapter registered."""
    code = (
        "import agent_brain.agent_integrations;"
        "from agent_brain.agent_integrations.registry import ADAPTER_REGISTRY;"
        "names = sorted(ADAPTER_REGISTRY);"
        "assert 'wukong' in names and 'codex' in names, names;"
        "assert len(names) >= 12, names;"
        "print('OK', len(names))"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout


def test_discover_adapters_callable_and_idempotent():
    from agent_brain.agent_integrations.registry import discover_adapters

    first = discover_adapters()
    second = discover_adapters()
    assert first == second
    assert "wukong" in first and "codex" in first


def test_wukong_install_survives_missing_end(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import wukong as wk

    ctx, mcp = _patch_wukong_paths(wk, tmp_path, monkeypatch)
    ctx.parent.mkdir(parents=True)
    # Corrupted: BEGIN present, END truncated away, plus user text before.
    ctx.write_text("# user notes\n\n" + wk.BEGIN + "\nhalf a block, no end marker\n")

    msg = wk.WukongAdapter(brain_dir=tmp_path / ".brain").install()  # must not raise
    assert "wukong adapter" in msg
    content = ctx.read_text()
    assert wk.BEGIN in content and wk.END in content
    assert "# user notes" in content
    assert content.count(wk.BEGIN) == 1 and content.count(wk.END) == 1
    assert "half a block" not in content
    assert mcp.exists()


def test_wukong_uninstall_survives_missing_end(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import wukong as wk

    ctx, mcp = _patch_wukong_paths(wk, tmp_path, monkeypatch)
    ctx.parent.mkdir(parents=True)
    ctx.write_text("# keep me\n\n" + wk.BEGIN + "\ntruncated block no end\n")
    mcp.parent.mkdir(parents=True)
    mcp.write_text('{"mcpServers": {"agent-memory-hub": {"command": "old", "args": []}}}')

    msg = wk.WukongAdapter(brain_dir=tmp_path / ".brain").uninstall()  # must not raise
    assert "wukong adapter" in msg
    content = ctx.read_text()
    assert "# keep me" in content
    assert wk.BEGIN not in content
    assert "agent-memory-hub" not in mcp.read_text()


def test_wukong_install_is_atomic_no_tmp_left(tmp_path, monkeypatch):
    from agent_brain.agent_integrations import wukong as wk

    ctx, mcp = _patch_wukong_paths(wk, tmp_path, monkeypatch)
    wk.WukongAdapter(brain_dir=tmp_path / ".brain").install()
    # tmp+replace must not leave a sibling .tmp file behind
    assert list(ctx.parent.glob("*.tmp")) == []
    assert list(mcp.parent.glob("*.tmp")) == []
    # and the adapter must actually expose an atomic-write path
    assert hasattr(wk.WukongAdapter, "_atomic_write")


def test_codex_upsert_block_survives_missing_end():
    from agent_brain.agent_integrations import codex as cx

    corrupted = "intro\n" + cx.BEGIN + "\ntruncated, no end\n"
    new_content, action = cx._upsert_block(
        corrupted, cx.BEGIN + "\nfresh\n" + cx.END + "\n"
    )
    assert cx.END in new_content
    assert new_content.count(cx.BEGIN) == 1
    assert "truncated, no end" not in new_content
    assert action == "updated"


def test_codex_remove_block_survives_missing_end():
    from agent_brain.agent_integrations import codex as cx

    corrupted = "keep\n\n" + cx.BEGIN + "\ntruncated, no end\n"
    cleaned = cx._remove_block(corrupted)
    assert cx.BEGIN not in cleaned
    assert "keep" in cleaned
