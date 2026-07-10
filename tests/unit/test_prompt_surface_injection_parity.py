from datetime import datetime, timezone
import re

import pytest
from typer.testing import CliRunner

from agent_brain.contracts.memory_item import MemoryItem, MemoryType, Sensitivity
from agent_brain.interfaces.cli import app
from agent_brain.interfaces.sdk import MemoryClient
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex


runner = CliRunner()


@pytest.fixture(autouse=True)
def close_mcp_components(monkeypatch):
    import agent_brain.interfaces.cli as cli_module
    from agent_brain.interfaces.mcp.tools import _shared as mcp_shared, search_tools
    from agent_brain.interfaces.mcp.tools._shared import _components_cache
    from agent_brain.platform.embedding import reset_embedder_cache

    monkeypatch.setenv("MEMORY_HUB_TEST_EMBEDDING", "1")
    reset_embedder_cache()
    embedder = HashingEmbedder(dim=64)
    monkeypatch.setattr(cli_module, "get_default_embedder", lambda: embedder)
    monkeypatch.setattr(mcp_shared, "get_default_embedder", lambda: embedder)
    monkeypatch.setattr(search_tools, "get_default_embedder", lambda: embedder)
    for _store, index, _retriever in _components_cache.values():
        index.close()
    _components_cache.clear()
    yield
    for _store, index, _retriever in _components_cache.values():
        index.close()
    _components_cache.clear()
    reset_embedder_cache()


def memory(
    suffix,
    *,
    sensitivity=Sensitivity.internal,
    tags=None,
    superseded_by=None,
):
    return MemoryItem(
        id=f"mem-20260711-030000-surface-parity-{suffix}",
        type=MemoryType.episode,
        created_at=datetime(2026, 7, 11, 3, 0, tzinfo=timezone.utc),
        title=f"Surface parity gateway boundary {suffix}",
        summary=f"Surface parity gateway boundary {suffix}",
        sensitivity=sensitivity,
        tags=tags or [],
        superseded_by=superseded_by,
        confidence=0.9,
    )


def seed(brain, items):
    store = ItemsStore(brain / "items")
    embedder = HashingEmbedder(dim=64)
    index = HubIndex(brain / "index.db", embedding_dim=embedder.dim)
    for item in items:
        body = f"Surface parity gateway boundary body {item.title}"
        store.write(item, body)
        index.upsert(item, body, embedding=embedder.embed(body))
    index.close()


def fixtures():
    safe = memory("safe")
    return safe, [
        safe,
        memory("private", sensitivity=Sensitivity.private),
        memory("secret", sensitivity=Sensitivity.secret),
        memory("review", tags=["needs-review"]),
        memory("superseded", superseded_by=safe.id),
    ]


def test_mcp_sdk_cli_search_return_same_eligible_ids(tmp_path, monkeypatch):
    safe, items = fixtures()
    mcp_brain = tmp_path / "mcp-search"
    sdk_brain = tmp_path / "sdk-search"
    cli_brain = tmp_path / "cli-search"
    for brain in (mcp_brain, sdk_brain, cli_brain):
        seed(brain, items)
    query = "surface parity gateway boundary"

    import agent_brain.interfaces.mcp.server as mcp

    monkeypatch.setenv("BRAIN_DIR", str(mcp_brain))
    mcp_ids = {row["id"] for row in mcp.search_memory(query, top_k=10)}
    client = MemoryClient(brain_dir=sdk_brain)
    try:
        sdk_ids = {row.id for row in client.search(query, top_k=10)}
    finally:
        client._components.get_index().close()
    monkeypatch.setenv("BRAIN_DIR", str(cli_brain))
    cli = runner.invoke(app, [
        "search", query, "--top-k", "10", "--format", "text", "--context-firewall",
    ])
    assert cli.exit_code == 0, cli.output
    cli_ids = set(re.findall(r"\(id:(mem-[^\s)]+)", cli.output))

    assert mcp_ids == sdk_ids == cli_ids == {safe.id}
    for forbidden in items[1:]:
        assert forbidden.id not in cli.output
        assert forbidden.title not in cli.output
        assert forbidden.summary not in cli.output


def test_mcp_sdk_cli_brief_share_eligible_items(tmp_path, monkeypatch):
    safe, items = fixtures()
    mcp_brain = tmp_path / "mcp-brief"
    sdk_brain = tmp_path / "sdk-brief"
    cli_brain = tmp_path / "cli-brief"
    for brain in (mcp_brain, sdk_brain, cli_brain):
        seed(brain, items)

    import agent_brain.interfaces.mcp.server as mcp

    monkeypatch.setenv("BRAIN_DIR", str(mcp_brain))
    mcp_payload = mcp.brief_memory(budget_tokens=1500)
    mcp_titles = {
        row["title"]
        for tier in mcp_payload["tiers"]
        for row in tier["items"]
    }
    client = MemoryClient(brain_dir=sdk_brain)
    try:
        sdk_payload = client.brief(budget_tokens=1500)
    finally:
        client._components.get_index().close()
    sdk_titles = {
        row["title"]
        for tier in sdk_payload["tiers"]
        for row in tier["items"]
    }
    monkeypatch.setenv("BRAIN_DIR", str(cli_brain))
    cli = runner.invoke(app, ["brief", "--budget-tokens", "1500"])
    assert cli.exit_code == 0, cli.output
    cli_titles = set(re.findall(r"\*\*(.+?)\*\*", cli.output))

    assert mcp_titles == sdk_titles == cli_titles == {safe.title}
    for forbidden in items[1:]:
        assert forbidden.title not in cli.output
        assert forbidden.summary not in cli.output


def test_mcp_sdk_cli_refill_past_three_x_top_k_with_same_safe_id(
    tmp_path,
    monkeypatch,
):
    query = "deep refill parity boundary"
    safe = memory("deep-refill-safe").model_copy(update={
        "title": f"{query} safe lower relevance extra unrelated tokens",
        "summary": "safe lower relevance context",
    })
    forbidden = [
        memory("deep-refill-private", sensitivity=Sensitivity.private),
        memory("deep-refill-secret", sensitivity=Sensitivity.secret),
        memory("deep-refill-review", tags=["needs-review"]),
        memory("deep-refill-unverified", tags=["unverified-boundary"]),
    ]
    forbidden = [
        item.model_copy(update={"title": query, "summary": f"{query} {query}"})
        for item in forbidden
    ]
    items = [*forbidden, safe]
    mcp_brain = tmp_path / "mcp-refill"
    sdk_brain = tmp_path / "sdk-refill"
    cli_brain = tmp_path / "cli-refill"
    for brain in (mcp_brain, sdk_brain, cli_brain):
        seed(brain, items)

    import agent_brain.interfaces.mcp.server as mcp

    monkeypatch.setenv("BRAIN_DIR", str(mcp_brain))
    mcp_ids = {row["id"] for row in mcp.search_memory(query, top_k=1)}
    client = MemoryClient(brain_dir=sdk_brain)
    try:
        sdk_ids = {row.id for row in client.search(query, top_k=1)}
    finally:
        client._components.get_index().close()
    monkeypatch.setenv("BRAIN_DIR", str(cli_brain))
    cli = runner.invoke(app, [
        "search", query, "--top-k", "1", "--format", "text", "--context-firewall",
    ])
    assert cli.exit_code == 0, cli.output
    cli_ids = set(re.findall(r"\(id:(mem-[^\s)]+)", cli.output))

    assert mcp_ids == sdk_ids == cli_ids == {safe.id}


def test_cli_text_and_table_formats_emit_the_same_gateway_id(tmp_path, monkeypatch):
    item = MemoryItem(
        id="mem-20260711-030000-cli-parity",
        type=MemoryType.episode,
        created_at=datetime(2026, 7, 11, 3, 0, tzinfo=timezone.utc),
        title="CLI format parity boundary",
        summary="CLI format parity boundary",
        confidence=0.9,
    )
    text_brain = tmp_path / "cli-format-text"
    table_brain = tmp_path / "cli-format-table"
    seed(text_brain, [item])
    seed(table_brain, [item])

    monkeypatch.setenv("BRAIN_DIR", str(text_brain))
    text_result = runner.invoke(app, [
        "search", item.title, "--top-k", "1", "--format", "text", "--context-firewall",
    ])
    monkeypatch.setenv("BRAIN_DIR", str(table_brain))
    table_result = runner.invoke(app, [
        "search", item.title, "--top-k", "1", "--format", "table", "--context-firewall",
    ])

    assert text_result.exit_code == 0, text_result.output
    assert table_result.exit_code == 0, table_result.output
    assert item.id in text_result.output
    assert item.id in table_result.output
