"""Exercise saved tags, warnings and scoped suggestions through the MCP write."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.interfaces.mcp.tools import mutation_tools
from agent_brain.memory.store.items_store import ItemsStore
from agent_brain.memory.store.write_service import WriteService
from agent_brain.platform.indexing.index import HubIndex


def test_write_keeps_caller_tags_and_filters_enrichment(tmp_brain):
    store = ItemsStore(tmp_brain / "items")
    index = HubIndex(tmp_brain / "index.db", embedding_dim=2)
    embedder = SimpleNamespace(embed=lambda text: [1.0, 0.0])
    for suffix, project, tenant, tags in (
        ("scope", "amh", "team", ["OpenAI", "evals"]),
        ("project", "other", "team", ["OpenAI", "Python"]),
        ("tenant", "amh", "other", ["OpenAI", "Anthropic"]),
    ):
        item = MemoryItem(
            id=f"mem-20261017-000000-{suffix}", type="fact",
            created_at=datetime.now(timezone.utc), title=suffix, summary=suffix,
            project=project, tenant_id=tenant, tags=tags,
        )
        store.write(item, "body")
        index.upsert(item, "body", embedding=[1.0, 0.0])
    service = WriteService(store, lambda: index, lambda: embedder, brain_dir=tmp_brain)
    body = (
        "**事实**\nOpenAI evals Python Anthropic example.\n"
        "**来源**\nCurrent test fixture.\n**有效期**\n2026-10-17 test snapshot."
    )
    try:
        with (
            patch.object(WriteService, "for_brain", return_value=service),
            patch.object(mutation_tools, "_components", return_value=(store, index, None)),
            patch.object(mutation_tools, "get_default_embedder", return_value=embedder),
        ):
            result = mutation_tools.write_memory(
                type="fact", title="Official evaluation examples", summary="OpenAI evals",
                body=body, tags=["OpenAI"], project="amh", tenant_id="team", ref_files=[__file__],
            )
        saved, saved_body = store.get(result["id"])
        assert saved.tags == ["OpenAI"]
        assert saved_body.strip() == body
        assert result["suggested_tags"] == ["evals"]
        assert not any("missing required sections" in warning for warning in result.get("warnings", []))
        assert all(entry["id"].endswith("scope") for entry in result.get("related", []))
    finally:
        index.close()
