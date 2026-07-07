from __future__ import annotations

from agent_brain.memory.recall.query_expansion import _tokenize_mixed
from agent_brain.memory.recall.query_synonyms import _expand_with_synonyms, _extract_words


def test_query_synonym_helpers_are_split_and_reexported():
    import agent_brain.memory.recall.query_expansion as query_expansion

    assert query_expansion._expand_with_synonyms is _expand_with_synonyms
    assert query_expansion._extract_words is _extract_words


def test_cjk_synonym_expansion_uses_raw_query_words():
    tokens = _tokenize_mixed("数据库性能")
    expanded = _expand_with_synonyms(tokens, raw_query="数据库性能")
    joined = " ".join(expanded).lower()

    assert "db" in joined or "database" in joined
    assert "perf" in joined or "performance" in joined


def test_extract_words_keeps_cjk_ngrams_for_synonym_lookup():
    words = _extract_words("消息队列")

    assert "消息队列" in words
    assert "消息" in words
    assert "队列" in words
