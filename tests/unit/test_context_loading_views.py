from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from typer.testing import CliRunner

from agent_brain.contracts.memory_item import MemoryItem, MemoryType
from agent_brain.interfaces.cli import app
from agent_brain.memory.context.context_firewall_types import ContextCandidate, FirewallDecision
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.platform.embedding import HashingEmbedder
from agent_brain.platform.indexing.index import HubIndex

runner = CliRunner()


def test_index_stores_context_views_and_does_not_fts_index_detail(tmp_brain_dir: Path):
    idx = HubIndex(db_path=tmp_brain_dir / "index.db", embedding_dim=8)
    emb = HashingEmbedder(dim=8)
    item = MemoryItem(
        id="mem-20260615-020000-context-index",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Context loading index",
        summary="locator alpha",
        abstraction="L1",
        context_views={
            "locator": "locator alpha",
            "overview": "overview beta",
            "detail_uri": "memory://items/mem-20260615-020000-context-index/body",
        },
    )

    idx.upsert(item, "detail-only gamma", embedding=emb.embed(item.context_views.locator))

    columns = {row[1] for row in idx.connection.execute("PRAGMA table_info(items_meta)").fetchall()}
    assert "maturity" in columns
    assert "context_views_json" in columns

    row = idx.connection.execute(
        "SELECT maturity, context_views_json FROM items_meta WHERE id = ?",
        (item.id,),
    ).fetchone()
    assert row[0] == "consolidated"
    assert json.loads(row[1]) == item.context_views.model_dump(mode="json")

    assert [hit.id for hit in idx.bm25_search("overview", top_k=5)] == [item.id]
    assert idx.bm25_search("gamma", top_k=5) == []


def test_cli_write_accepts_overview_context_view(tmp_brain: Path):
    result = runner.invoke(app, [
        "write",
        "--type", "fact",
        "--title", "Context write",
        "--summary", "locator write alpha",
        "--overview", "overview write beta",
        "--body", "detail write gamma",
    ])

    assert result.exit_code == 0, result.output
    item, body = next(ItemsStore(tmp_brain / "items").iter_all())
    assert item.context_views.locator == "locator write alpha"
    assert item.context_views.overview == "overview write beta"
    assert item.context_views.detail_uri == f"memory://items/{item.id}/body"
    assert body.rstrip() == "detail write gamma"


def test_cli_search_verbosity_controls_loaded_context_view(tmp_brain: Path):
    item = MemoryItem(
        id="mem-20260615-020001-context-search",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Context search",
        summary="locator search alpha",
        context_views={
            "locator": "locator search alpha",
            "overview": "overview search beta",
            "detail_uri": "memory://items/mem-20260615-020001-context-search/body",
        },
    )
    body = "detail search gamma"
    ItemsStore(tmp_brain / "items").write(item, body)
    idx = HubIndex(db_path=tmp_brain / "index.db", embedding_dim=384)
    emb = HashingEmbedder()
    idx.upsert(item, body, embedding=emb.embed(item.context_views.locator))

    locator = runner.invoke(app, ["search", "search", "--format", "text", "--verbosity", "locator"])
    assert locator.exit_code == 0, locator.output
    assert "locator search alpha" in locator.output
    assert "overview search beta" not in locator.output
    assert "detail search gamma" not in locator.output

    overview = runner.invoke(app, ["search", "search", "--format", "text", "--verbosity", "overview"])
    assert overview.exit_code == 0, overview.output
    assert "overview search beta" in overview.output
    assert "detail search gamma" not in overview.output

    detail = runner.invoke(app, ["search", "search", "--format", "text", "--verbosity", "detail"])
    assert detail.exit_code == 0, detail.output
    assert "detail search gamma" in detail.output


def test_cli_context_firewall_defaults_to_auto_overview_for_sourced_fact(tmp_brain: Path):
    item = MemoryItem(
        id="mem-20260615-020003-context-auto-cli",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Context loading policy",
        summary="context loading locator alpha",
        abstraction="L1",
        refs={"commits": ["abc1234"]},
        context_views={
            "locator": "context loading locator alpha",
            "overview": "context loading overview beta",
            "detail_uri": "memory://items/mem-20260615-020003-context-auto-cli/body",
        },
    )
    body = "context loading detail gamma"
    ItemsStore(tmp_brain / "items").write(item, body)
    idx = HubIndex(db_path=tmp_brain / "index.db", embedding_dim=384)
    emb = HashingEmbedder()
    idx.upsert(item, body, embedding=emb.embed(item.context_views.locator))

    result = runner.invoke(app, [
        "search",
        "context loading",
        "--context-firewall",
        "--format", "text",
        "--top-k", "1",
    ])

    assert result.exit_code == 0, result.output
    assert "context loading overview beta" in result.output
    assert "context loading detail gamma" not in result.output
    assert "view=overview" in result.output
    assert "packed=" in result.output
    assert (
        'retrieve="memory read mem-20260615-020003-context-auto-cli --head 2000 --view detail"'
        in result.output
    )
    assert "created_at=" not in result.output
    assert "meta:" not in result.output


def test_auto_context_loading_uses_overview_for_raw_item_with_direct_evidence() -> None:
    from agent_brain.memory.context.context_loading import select_context_view

    item = MemoryItem(
        id="mem-20260615-020004-context-auto-policy",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Raw evidence policy",
        summary="raw evidence locator",
        abstraction="L0",
        refs={"files": ["/tmp/evidence.log"]},
        context_views={
            "locator": "raw evidence locator",
            "overview": "raw evidence overview",
            "detail_uri": "memory://items/mem-20260615-020004-context-auto-policy/body",
        },
    )

    selection = select_context_view(item, "raw evidence detail")

    assert selection.view == "overview"
    assert "raw_direct_evidence" in selection.reasons


def test_auto_context_loading_falls_back_to_locator_when_raw_overview_is_empty() -> None:
    from agent_brain.memory.context.context_loading import select_context_view

    item = MemoryItem(
        id="mem-20260615-020004-context-auto-locator",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="Raw evidence locator fallback",
        summary="raw locator only",
        abstraction="L0",
        refs={"resources": ["res-direct-evidence"]},
        context_views={
            "locator": "raw locator only",
            "overview": "",
            "detail_uri": "memory://items/mem-20260615-020004-context-auto-locator/body",
        },
    )

    selection = select_context_view(item, "raw body marker")

    assert selection.view == "locator"
    assert "raw_direct_evidence" in selection.reasons


def test_explicit_detail_still_selects_detail_for_raw_direct_evidence() -> None:
    from agent_brain.memory.context.context_loading import select_context_view

    item = MemoryItem(
        id="mem-20260615-020004-context-explicit-detail",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Explicit detail",
        summary="detail locator",
        abstraction="L0",
        refs={"files": ["/tmp/evidence.log"]},
    )

    selection = select_context_view(item, "detail body", requested="detail")

    assert selection.view == "detail"
    assert selection.reasons == ("explicit_detail",)


def test_broad_explicit_detail_search_is_warned_but_not_blocked() -> None:
    from agent_brain.memory.context.recall_policy import search_governance_warnings

    assert search_governance_warnings(verbosity="detail", top_k=4)
    assert search_governance_warnings(verbosity="detail", top_k=3) == ()
    assert search_governance_warnings(verbosity="auto", top_k=10) == ()


def test_context_pack_is_reversible_and_keeps_detail_out_of_prompt() -> None:
    from agent_brain.memory.context.context_packing import build_context_pack

    item = MemoryItem(
        id="mem-20260615-020006-context-pack",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Reversible context pack",
        summary="pack locator alpha",
        abstraction="L1",
        refs={"urls": ["https://example.test/context-pack"]},
        context_views={
            "locator": "pack locator alpha",
            "overview": "pack overview beta",
            "detail_uri": "memory://items/mem-20260615-020006-context-pack/body",
        },
    )
    body = "pack detail gamma " * 120

    pack = build_context_pack(item, body, requested="auto")

    assert pack.selected_view == "overview"
    assert pack.text == "pack overview beta"
    assert "pack detail gamma" not in pack.text
    assert pack.detail_uri == "memory://items/mem-20260615-020006-context-pack/body"
    assert pack.retrieve_hint == (
        "read_memory(id='mem-20260615-020006-context-pack', head=2000, view='detail')"
    )
    assert pack.compressed is True
    assert pack.reversible is True
    assert pack.to_dict()["text"] == "pack overview beta"


def test_pack_decisions_uses_packed_tokens_for_budget() -> None:
    from agent_brain.memory.context.context_packing import pack_decisions

    item = MemoryItem(
        id="mem-20260620-010000-pack-budget",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Pack budget",
        summary="pack budget locator",
        abstraction="L1",
        refs={"urls": ["https://example.test/pack-budget"]},
        context_views={
            "locator": "pack budget locator",
            "overview": "short overview",
            "detail_uri": "memory://items/mem-20260620-010000-pack-budget/body",
        },
    )
    body = "long body token " * 200
    decision = FirewallDecision(
        candidate=ContextCandidate(item=item, body=body, score=0.9),
        action="include",
        reasons=("eligible",),
        score=0.9,
        effective_score=0.9,
    )

    result = pack_decisions([decision], requested="auto", budget_tokens=10)

    assert [entry.decision.candidate.item.id for entry in result.included] == [item.id]
    assert result.included[0].pack.selected_view == "overview"
    assert result.included[0].pack.text == "short overview"
    assert "long body token" not in result.included[0].pack.text
    assert result.used_tokens == result.included[0].pack.packed_tokens
    assert result.full_tokens == result.included[0].pack.full_tokens


def test_pack_decisions_downgrades_to_locator_before_excluding() -> None:
    from agent_brain.memory.context.context_packing import pack_decisions

    item = MemoryItem(
        id="mem-20260620-010001-pack-downgrade",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Pack downgrade",
        summary="tiny locator",
        abstraction="L0",
        refs={"files": ["/tmp/evidence.log"]},
        context_views={
            "locator": "tiny locator",
            "overview": "overview token " * 40,
            "detail_uri": "memory://items/mem-20260620-010001-pack-downgrade/body",
        },
    )
    decision = FirewallDecision(
        candidate=ContextCandidate(item=item, body="detail token " * 80, score=0.8),
        action="include",
        reasons=("raw_direct_evidence",),
        score=0.8,
        effective_score=0.8,
    )

    result = pack_decisions([decision], requested="auto", budget_tokens=5)

    assert len(result.included) == 1
    assert result.included[0].pack.selected_view == "locator"
    assert "budget_downgraded_to_locator" in result.included[0].decision.reasons
    assert result.excluded == []

    too_small = pack_decisions([decision], requested="auto", budget_tokens=0)

    assert too_small.included == []
    assert too_small.excluded[0].action == "exclude"
    assert "pack_budget_exceeded" in too_small.excluded[0].reasons


def test_mcp_tools_accept_overview_and_context_view_controls(tmp_brain: Path):
    from agent_brain.interfaces.mcp.tools.mutation_tools import write_memory
    from agent_brain.interfaces.mcp.tools.read_tools import read_memory
    from agent_brain.interfaces.mcp.tools.search_tools import search_memory

    written = write_memory(
        type="fact",
        title="MCP context loading",
        summary="mcp locator alpha",
        overview="mcp overview beta",
        body="mcp detail gamma",
    )
    item_id = written["id"]

    locator_hits = search_memory("mcp", top_k=1, verbosity="locator")
    assert locator_hits[0]["locator"] == "mcp locator alpha"
    assert "overview" not in locator_hits[0]
    assert "body" not in locator_hits[0]

    overview_hits = search_memory("mcp", top_k=1, verbosity="overview")
    assert overview_hits[0]["overview"] == "mcp overview beta"
    assert "body" not in overview_hits[0]

    detail_hits = search_memory("mcp", top_k=1, verbosity="detail")
    assert detail_hits[0]["body"].rstrip() == "mcp detail gamma"
    assert detail_hits[0]["context_pack"]["selected_view"] == "detail"

    locator_read = read_memory(item_id, view="locator")
    assert locator_read["locator"] == "mcp locator alpha"
    assert "body" not in locator_read

    detail_read = read_memory(item_id, view="detail")
    assert detail_read["body"].rstrip() == "mcp detail gamma"


def test_mcp_search_auto_returns_selected_view_and_load_reason(tmp_brain: Path):
    from agent_brain.interfaces.mcp.tools._shared import _components_cache
    from agent_brain.interfaces.mcp.tools.search_tools import search_memory

    _components_cache.clear()
    item = MemoryItem(
        id="mem-20260615-020005-context-auto-mcp",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="MCP auto context",
        summary="mcp auto locator alpha",
        abstraction="L1",
        refs={"urls": ["https://example.test/mcp-auto"]},
        context_views={
            "locator": "mcp auto locator alpha",
            "overview": "mcp auto overview beta",
            "detail_uri": "memory://items/mem-20260615-020005-context-auto-mcp/body",
        },
    )
    body = "mcp auto detail gamma"
    ItemsStore(tmp_brain / "items").write(item, body)
    idx = HubIndex(db_path=tmp_brain / "index.db", embedding_dim=384)
    emb = HashingEmbedder()
    idx.upsert(item, body, embedding=emb.embed(item.context_views.locator))

    hits = search_memory("mcp auto", top_k=1, verbosity="auto")

    assert hits[0]["selected_view"] == "overview"
    assert "fact_or_decision_boundary" in hits[0]["load_reason"]
    assert hits[0]["overview"] == "mcp auto overview beta"
    assert hits[0]["context_pack"]["text"] == "mcp auto overview beta"
    assert hits[0]["context_pack"]["detail_uri"] == (
        "memory://items/mem-20260615-020005-context-auto-mcp/body"
    )
    assert hits[0]["context_pack"]["retrieve_hint"] == (
        "read_memory(id='mem-20260615-020005-context-auto-mcp', head=2000, view='detail')"
    )
    assert hits[0]["context_pack"]["compressed"] is True
    assert hits[0]["context_pack"]["reversible"] is True
    assert "body" not in hits[0]


def test_mcp_staged_search_preserves_explicit_detail_and_warns_when_broad(
    tmp_brain: Path,
):
    from agent_brain.interfaces.mcp.tools._shared import _components_cache
    from agent_brain.interfaces.mcp.tools.search_tools import search_memory

    _components_cache.clear()
    item = MemoryItem(
        id="mem-20260715-020005-staged-mcp",
        type=MemoryType.artifact,
        created_at=datetime.now(timezone.utc),
        title="Staged MCP recall",
        summary="staged mcp locator",
        abstraction="L0",
        refs={"files": ["/tmp/staged-mcp.log"]},
        context_views={
            "locator": "staged mcp locator",
            "overview": "",
            "detail_uri": "memory://items/mem-20260715-020005-staged-mcp/body",
        },
    )
    body = "detail-only marker"
    ItemsStore(tmp_brain / "items").write(item, body)
    idx = HubIndex(db_path=tmp_brain / "index.db", embedding_dim=384)
    emb = HashingEmbedder()
    idx.upsert(item, body, embedding=emb.embed(item.context_views.locator))

    auto_hit = search_memory("staged mcp", top_k=5, verbosity="auto")[0]
    detail_hit = search_memory("staged mcp", top_k=5, verbosity="detail")[0]
    bounded_detail_hit = search_memory("staged mcp", top_k=3, verbosity="detail")[0]

    assert auto_hit["selected_view"] == "locator"
    assert "body" not in auto_hit
    assert "detail-only marker" not in auto_hit["context_pack"]["text"]
    assert detail_hit["body"].rstrip() == "detail-only marker"
    assert detail_hit["context_pack"]["selected_view"] == "detail"
    assert detail_hit["governance_warnings"]
    assert bounded_detail_hit["body"].rstrip() == "detail-only marker"
    assert "governance_warnings" not in bounded_detail_hit


def test_mcp_search_can_return_retrieval_trace(tmp_brain: Path):
    from agent_brain.interfaces.mcp.tools._shared import _components_cache
    from agent_brain.interfaces.mcp.tools.search_tools import search_memory

    _components_cache.clear()
    item = MemoryItem(
        id="mem-20260620-020000-mcp-trace",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="MCP trace",
        summary="mcp trace locator",
        abstraction="L1",
        refs={"urls": ["https://example.test/mcp-trace"]},
        context_views={
            "locator": "mcp trace locator",
            "overview": "mcp trace overview",
            "detail_uri": "memory://items/mem-20260620-020000-mcp-trace/body",
        },
    )
    body = "mcp trace body"
    ItemsStore(tmp_brain / "items").write(item, body)
    idx = HubIndex(db_path=tmp_brain / "index.db", embedding_dim=384)
    emb = HashingEmbedder()
    idx.upsert(item, body, embedding=emb.embed(item.context_views.locator))

    hit = search_memory("mcp trace", top_k=1, verbosity="auto", include_trace=True)[0]
    plain = search_memory("mcp trace", top_k=1, verbosity="auto")[0]

    assert hit["context_pack"]["text"] == "mcp trace overview"
    assert hit["retrieval_trace"]["final_rank"] == 1
    assert "bm25" in hit["retrieval_trace"]["signals"]
    assert "retrieval_trace" not in plain


def test_cli_context_firewall_text_prints_reversible_retrieve_hint(tmp_brain: Path):
    item = MemoryItem(
        id="mem-20260615-020007-context-pack-cli",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="CLI reversible pack",
        summary="cli pack locator alpha",
        abstraction="L1",
        refs={"urls": ["https://example.test/cli-context-pack"]},
        context_views={
            "locator": "cli pack locator alpha",
            "overview": "cli pack overview beta",
            "detail_uri": "memory://items/mem-20260615-020007-context-pack-cli/body",
        },
    )
    body = "cli pack detail gamma " * 80
    ItemsStore(tmp_brain / "items").write(item, body)
    idx = HubIndex(db_path=tmp_brain / "index.db", embedding_dim=384)
    emb = HashingEmbedder()
    idx.upsert(item, body, embedding=emb.embed(item.context_views.locator))

    result = runner.invoke(app, [
        "search",
        "cli pack",
        "--context-firewall",
        "--format", "text",
        "--top-k", "1",
    ])

    assert result.exit_code == 0, result.output
    assert "cli pack overview beta" in result.output
    assert "cli pack detail gamma" not in result.output
    assert (
        'retrieve="memory read mem-20260615-020007-context-pack-cli --head 2000 --view detail"'
        in result.output
    )


def test_update_summary_refreshes_default_locator(tmp_brain: Path):
    store = ItemsStore(tmp_brain / "items")
    item = MemoryItem(
        id="mem-20260615-020002-context-update",
        type=MemoryType.fact,
        created_at=datetime.now(timezone.utc),
        title="Context update",
        summary="old locator",
    )
    store.write(item, "detail")

    updated = store.update_frontmatter(item.id, summary="new locator")

    assert updated.summary == "new locator"
    assert updated.context_views.locator == "new locator"
