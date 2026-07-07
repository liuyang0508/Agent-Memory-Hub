"""Unit tests for LLM enricher — mocks litellm to verify enrichment logic."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from agent_brain.memory.store.items_store import ItemsStore, make_item_id
from agent_brain.memory.evidence.harvest.enricher import (
    _build_prompt,
    _call_llm,
    enrich_item,
    enrich_pool,
)
from agent_brain.contracts.memory_item import (
    AbstractionLayer,
    MemoryItem,
    MemoryType,
    Source,
)


@pytest.fixture
def store_with_l0(tmp_path):
    """Create a store with 3 L0/mechanical items ready for enrichment."""
    items_dir = tmp_path / "items"
    items_dir.mkdir()
    store = ItemsStore(items_dir=items_dir)
    for i in range(3):
        now = datetime(2026, 1, 1 + i, tzinfo=timezone.utc)
        item = MemoryItem(
            id=make_item_id(f"raw-item-{i}", when=now),
            type=MemoryType.episode,
            created_at=now,
            title=f"Raw episode {i}",
            summary=f"Something happened #{i}",
            tags=["raw"],
            abstraction=AbstractionLayer.L0,
            source=Source(kind="harvested", extractor="mechanical"),
        )
        store.write(item, f"Body content for item {i} with details.")
    return store, tmp_path


class TestBuildPrompt:
    def test_includes_all_fields(self):
        prompt = _build_prompt("My Title", "My Summary", "Body text here", ["tag1", "tag2"])
        assert "My Title" in prompt
        assert "My Summary" in prompt
        assert "Body text here" in prompt
        assert "tag1" in prompt

    def test_truncates_long_body(self):
        long_body = "x" * 5000
        prompt = _build_prompt("t", "s", long_body, [])
        assert len(prompt) < 3000


class TestCallLlm:
    def test_successful_call(self, monkeypatch):
        monkeypatch.delenv("MEMORY_HUB_NO_MODEL", raising=False)
        result_json = json.dumps({
            "title": "Better Title",
            "summary": "Improved summary",
            "tags": ["improved", "test"],
            "confidence": 0.85,
        })
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = result_json
        mock_completion = MagicMock(return_value=mock_response)

        import sys
        mock_litellm = MagicMock()
        mock_litellm.completion = mock_completion
        monkeypatch.setitem(sys.modules, "litellm", mock_litellm)

        result = _call_llm("test prompt")
        assert result is not None
        assert result["title"] == "Better Title"
        assert result["confidence"] == 0.85
        mock_completion.assert_called_once()

    def test_handles_markdown_fenced_response(self, monkeypatch):
        monkeypatch.delenv("MEMORY_HUB_NO_MODEL", raising=False)
        fenced = '```json\n{"title": "X", "summary": "Y", "tags": [], "confidence": 0.7}\n```'
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = fenced

        import sys
        mock_litellm = MagicMock()
        mock_litellm.completion.return_value = mock_response
        monkeypatch.setitem(sys.modules, "litellm", mock_litellm)

        result = _call_llm("test")
        assert result is not None
        assert result["title"] == "X"

    def test_returns_none_on_invalid_json(self, monkeypatch):
        monkeypatch.delenv("MEMORY_HUB_NO_MODEL", raising=False)
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "not json at all"

        import sys
        mock_litellm = MagicMock()
        mock_litellm.completion.return_value = mock_response
        monkeypatch.setitem(sys.modules, "litellm", mock_litellm)

        result = _call_llm("test")
        assert result is None

    def test_returns_none_on_exception(self, monkeypatch):
        monkeypatch.delenv("MEMORY_HUB_NO_MODEL", raising=False)

        import sys
        mock_litellm = MagicMock()
        mock_litellm.completion.side_effect = RuntimeError("API timeout")
        monkeypatch.setitem(sys.modules, "litellm", mock_litellm)

        result = _call_llm("test")
        assert result is None


class TestEnrichItem:
    @patch("agent_brain.memory.evidence.harvest.enricher._call_llm")
    @patch("agent_brain.memory.evidence.harvest.enricher._model_available", return_value=True)
    def test_enriches_single_item(self, mock_avail, mock_call):
        mock_call.return_value = {
            "title": "Enhanced",
            "summary": "Better",
            "tags": ["a", "b"],
            "confidence": 0.9,
        }
        result = enrich_item("id1", "Raw", "Original", "Body", ["old"])
        assert result["title"] == "Enhanced"

    @patch("agent_brain.memory.evidence.harvest.enricher._model_available", return_value=False)
    def test_returns_none_when_no_model(self, mock_avail):
        result = enrich_item("id1", "Raw", "Original", "Body", ["old"])
        assert result is None


class TestEnrichPool:
    @patch("agent_brain.memory.evidence.harvest.enricher._call_llm")
    @patch("agent_brain.memory.evidence.harvest.enricher._model_available", return_value=True)
    def test_enriches_l0_items(self, mock_avail, mock_call, store_with_l0, monkeypatch):
        store, brain_dir = store_with_l0
        monkeypatch.setenv("BRAIN_DIR", str(brain_dir))

        mock_call.return_value = {
            "title": "Enriched Title",
            "summary": "Enriched summary",
            "tags": ["enriched", "better"],
            "confidence": 0.85,
        }

        count = enrich_pool(limit=10)
        assert count == 3

        for item, body in store.iter_all():
            assert item.abstraction == AbstractionLayer.L2
            assert item.source.extractor == "llm"
            assert item.title == "Enriched Title"
            assert "enriched" in item.tags

    @patch("agent_brain.memory.evidence.harvest.enricher._call_llm")
    @patch("agent_brain.memory.evidence.harvest.enricher._model_available", return_value=True)
    def test_skips_already_enriched(self, mock_avail, mock_call, store_with_l0, monkeypatch):
        store, brain_dir = store_with_l0
        monkeypatch.setenv("BRAIN_DIR", str(brain_dir))

        mock_call.return_value = {
            "title": "V1",
            "summary": "V1",
            "tags": ["v1"],
            "confidence": 0.8,
        }
        enrich_pool(limit=10)

        mock_call.reset_mock()
        count = enrich_pool(limit=10)
        assert count == 0
        mock_call.assert_not_called()

    @patch("agent_brain.memory.evidence.harvest.enricher._call_llm")
    @patch("agent_brain.memory.evidence.harvest.enricher._model_available", return_value=True)
    def test_handles_partial_failure(self, mock_avail, mock_call, store_with_l0, monkeypatch):
        store, brain_dir = store_with_l0
        monkeypatch.setenv("BRAIN_DIR", str(brain_dir))

        call_count = [0]

        def side_effect(prompt):
            call_count[0] += 1
            if call_count[0] == 2:
                return None
            return {"title": "OK", "summary": "OK", "tags": ["ok"], "confidence": 0.8}

        mock_call.side_effect = side_effect
        count = enrich_pool(limit=10)
        assert count == 2

    def test_noop_when_disabled(self, monkeypatch):
        monkeypatch.setenv("MEMORY_HUB_NO_MODEL", "1")
        assert enrich_pool() == 0

    @patch("agent_brain.memory.evidence.harvest.enricher._call_llm")
    @patch("agent_brain.memory.evidence.harvest.enricher._model_available", return_value=True)
    def test_respects_limit(self, mock_avail, mock_call, store_with_l0, monkeypatch):
        store, brain_dir = store_with_l0
        monkeypatch.setenv("BRAIN_DIR", str(brain_dir))

        mock_call.return_value = {
            "title": "OK",
            "summary": "OK",
            "tags": ["ok"],
            "confidence": 0.8,
        }
        count = enrich_pool(limit=1)
        assert count == 1
        assert mock_call.call_count == 1
