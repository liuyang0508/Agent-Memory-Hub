"""P2-2: BOM / CRLF tolerance for md item reads and adapter JSON config reads.

Before the fix:
- ItemsStore._read_one read utf-8 and probed `text.startswith("---\n")`, so a
  BOM-prefixed or CRLF md file raised ValueError("missing frontmatter").
- The adapter _read_json/_read_settings helpers read utf-8 and json.loads
  rejects a leading BOM, which was re-wrapped as RuntimeError("refuse to
  overwrite malformed ...").
"""
import json
from pathlib import Path

import pytest

from agent_brain.agent_integrations.claude_code import _read_settings
from agent_brain.agent_integrations.cline import _read_json as cline_read_json
from agent_brain.agent_integrations.cursor import _read_json as cursor_read_json
from agent_brain.memory.store.items_store import ItemsStore

UTF8_BOM = b"\xef\xbb\xbf"


def _md_bytes(eol: str, bom: bool) -> bytes:
    lines = [
        "---",
        "id: mem-20260101-120000-bom-crlf",
        "type: fact",
        "created_at: 2026-01-01T12:00:00+00:00",
        "title: bom crlf item",
        "summary: tolerate bom and crlf",
        "---",
        "",
        "body text here",
        "",
    ]
    raw = eol.join(lines).encode("utf-8")
    return (UTF8_BOM + raw) if bom else raw


@pytest.mark.parametrize(
    "eol,bom",
    [("\r\n", True), ("\r\n", False), ("\n", True), ("\n", False)],
)
def test_items_store_reads_bom_and_crlf(tmp_path: Path, eol: str, bom: bool) -> None:
    items = tmp_path / "items"
    items.mkdir()
    item_id = "mem-20260101-120000-bom-crlf"
    (items / f"{item_id}.md").write_bytes(_md_bytes(eol, bom))

    store = ItemsStore(items_dir=items)
    item, body = store.get(item_id)

    assert item.id == item_id
    assert item.type == "fact"
    assert "body text here" in body
    # iter_all must also pick it up without recording a skip.
    loaded = list(store.iter_all())
    assert [i.id for i, _ in loaded] == [item_id]
    assert store.last_scan.skipped == []


def test_claude_code_settings_tolerates_bom(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    p.write_bytes(UTF8_BOM + json.dumps({"hooks": {"Stop": []}}).encode("utf-8"))
    assert _read_settings(p) == {"hooks": {"Stop": []}}


def test_cursor_json_tolerates_bom(tmp_path: Path) -> None:
    p = tmp_path / "mcp.json"
    p.write_bytes(UTF8_BOM + json.dumps({"mcpServers": {}}).encode("utf-8"))
    assert cursor_read_json(p) == {"mcpServers": {}}


def test_cline_json_tolerates_bom(tmp_path: Path) -> None:
    p = tmp_path / "mcp_servers.json"
    p.write_bytes(UTF8_BOM + json.dumps({"mcpServers": {}}).encode("utf-8"))
    assert cline_read_json(p) == {"mcpServers": {}}
