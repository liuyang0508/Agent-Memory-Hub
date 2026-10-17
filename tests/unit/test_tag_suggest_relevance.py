"""Tag popularity must not introduce a different task's vocabulary."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.recall.retrieval_tags import suggest_tags
from agent_brain.platform.indexing.index import HubIndex


@pytest.fixture
def tag_index(tmp_brain_dir):
    index = HubIndex(tmp_brain_dir / "index.db", embedding_dim=2)
    yield index
    index.close()


def _add(index, suffix, tags, vector=(1.0, 0.0), **fields):
    item = MemoryItem(
        id=f"mem-20261017-000000-{suffix}", type="fact",
        created_at=datetime.now(timezone.utc), title=suffix, summary=suffix,
        tags=tags, **fields,
    )
    index.upsert(item, suffix, embedding=list(vector))
    return item.id


EMBEDDER = SimpleNamespace(embed=lambda text: [1.0, 0.0])


def test_frequent_unrelated_tags_are_not_suggested(tag_index):
    for i in range(4):
        _add(tag_index, f"unrelated-{i}", ["视频推荐", "旅行摄影", "第二阶段"])
    _add(tag_index, "official-evals", ["OpenAI", "evals", "视频推荐"])

    suggestions = suggest_tags(tag_index, EMBEDDER, "Anthropic 与 OpenAI 的 evals 官方资料")

    assert suggestions == [("OpenAI", 1), ("evals", 1)]


def test_scope_is_applied_before_nearest_neighbor_limit(tag_index):
    for i in range(12):
        _add(tag_index, f"other-{i}", ["Python"], project="other", tenant_id="other")
    _add(tag_index, "scoped", ["Python"], vector=(0.8, 0.6), project="amh", tenant_id="team")
    _add(tag_index, "old", ["Python"], project="amh", tenant_id="team", superseded_by="new")

    assert suggest_tags(
        tag_index, EMBEDDER, "Python", top_k_neighbors=1, project="amh", tenant_id="team",
    ) == [("Python", 1)]


def test_low_similarity_never_becomes_relevant_through_frequency(tag_index):
    _add(tag_index, "far", ["Python"], vector=(0.0, 1.0))
    assert suggest_tags(tag_index, EMBEDDER, "Python") == []


def test_exclusions_happen_before_tag_limit_and_self_does_not_vote(tag_index):
    own_id = _add(tag_index, "new", ["invented"])
    _add(tag_index, "neighbor", ["Python", "Python", "evals"])
    assert suggest_tags(
        tag_index, EMBEDDER, "Python evals invented", max_tags=1,
        exclude_item_ids={own_id}, exclude_tags=["python"],
    ) == [("evals", 1)]


def test_tag_matching_handles_separators_but_not_embedded_english_words(tag_index):
    _add(tag_index, "syntax", ["case-mining", "api", "旅行摄影"])
    assert suggest_tags(tag_index, EMBEDDER, "CASE mining rapid tests") == [("case-mining", 1)]


def test_no_content_or_zero_limits_produce_no_suggestions(tag_index):
    _add(tag_index, "known", ["Python"])
    assert suggest_tags(tag_index, EMBEDDER, "  ") == []
    assert suggest_tags(tag_index, EMBEDDER, "Python", max_tags=0) == []
    assert suggest_tags(tag_index, EMBEDDER, "Python", top_k_neighbors=0) == []
