"""Unit tests for synonym-based Query Expansion in retrieval.py."""
from __future__ import annotations

from agent_brain.memory.recall.retrieval import (
    _expand_with_synonyms,
    _extract_words,
    _tokenize_mixed,
    expand_query,
)


class TestTokenizeMixed:
    def test_ascii_words(self):
        assert _tokenize_mixed("hello world") == ["hello", "world"]

    def test_cjk_single_chars(self):
        tokens = _tokenize_mixed("数据库")
        assert tokens == ["数", "据", "库"]

    def test_mixed(self):
        tokens = _tokenize_mixed("SSE实时推送")
        assert "SSE" in tokens
        assert "实" in tokens
        assert "时" in tokens


class TestExtractWords:
    def test_english(self):
        words = _extract_words("SSE push")
        assert "sse" in words
        assert "push" in words

    def test_cjk_ngrams(self):
        words = _extract_words("数据库性能")
        assert "数据库" in words
        assert "性能" in words
        assert "数据" in words

    def test_mixed_language(self):
        words = _extract_words("docker 容器编排")
        assert "docker" in words
        assert "容器" in words


class TestExpandWithSynonyms:
    def test_abbreviation_expansion(self):
        tokens = _tokenize_mixed("SSE")
        expanded = _expand_with_synonyms(tokens, raw_query="SSE")
        joined = " ".join(expanded).lower()
        assert "server" in joined
        assert "eventsource" in joined

    def test_cjk_synonym(self):
        tokens = _tokenize_mixed("消息队列")
        expanded = _expand_with_synonyms(tokens, raw_query="消息队列")
        joined = " ".join(expanded).lower()
        assert "mq" in joined or "queue" in joined or "kafka" in joined

    def test_no_expansion_for_unknown(self):
        tokens = _tokenize_mixed("foobar")
        expanded = _expand_with_synonyms(tokens, raw_query="foobar")
        assert expanded == tokens

    def test_max_expansions_respected(self):
        tokens = _tokenize_mixed("db")
        expanded_full = _expand_with_synonyms(tokens, raw_query="db", max_expansions=10)
        expanded_limited = _expand_with_synonyms(tokens, raw_query="db", max_expansions=1)
        assert len(expanded_limited) <= len(expanded_full)

    def test_bidirectional_lookup(self):
        tokens = _tokenize_mixed("database")
        expanded = _expand_with_synonyms(tokens, raw_query="database")
        joined = " ".join(expanded).lower()
        assert "db" in joined


class TestExpandQuery:
    def test_default_or_join(self):
        result = expand_query("hello world", synonyms=False)
        assert "OR" in result
        assert '"hello"' in result
        assert '"world"' in result

    def test_and_join(self):
        result = expand_query("hello world", use_or=False, synonyms=False)
        assert "OR" not in result
        assert '"hello"' in result

    def test_synonyms_enabled(self):
        result = expand_query("SSE", synonyms=True)
        assert "server" in result.lower()

    def test_synonyms_disabled(self):
        result = expand_query("SSE", synonyms=False)
        assert "server" not in result.lower()

    def test_empty_query(self):
        assert expand_query("") == '""'

    def test_deduplication(self):
        result = expand_query("db database", synonyms=True)
        tokens_lower = [t.strip('"').lower() for t in result.replace(" OR ", "|").split("|")]
        assert len(tokens_lower) == len(set(tokens_lower))

    def test_cjk_expansion(self):
        result = expand_query("数据库性能", synonyms=True)
        lower = result.lower()
        assert "perf" in lower or "performance" in lower
        assert "db" in lower or "database" in lower

    def test_cjk_phrase_keeps_adjacent_chars_together(self):
        result = expand_query("浏览器", synonyms=False)

        assert result == '"浏" "览" "器"'
        assert " OR " not in result

    def test_pipe_separated_cjk_terms_are_grouped_as_or_phrases(self):
        result = expand_query("浏览器|权限", synonyms=False)

        assert result == '("浏" "览" "器") OR ("权" "限")'
        assert '"器" OR "权"' not in result
