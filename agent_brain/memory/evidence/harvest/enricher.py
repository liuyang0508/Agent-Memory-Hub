"""Optional LLM upgrade of raw (L0) harvested items to distilled (L2).

Strictly optional: if no model is reachable (MEMORY_HUB_NO_MODEL set, or the
provider import/call fails), returns 0 and writes nothing. The mechanical layer
has already persisted a raw record, so enrichment is pure gravy — never on the
critical path. Idempotent: only items still at abstraction L0 with
source.extractor == 'mechanical' are considered.

Configuration (env vars):
  MEMORY_HUB_NO_MODEL=1         — disable enrichment entirely
  MEMORY_HUB_LLM_MODEL          — litellm model string (default: gpt-4o-mini)
  MEMORY_HUB_LLM_BASE_URL       — custom API base URL (optional)
  MEMORY_HUB_LLM_API_KEY        — API key override (optional, litellm respects OPENAI_API_KEY etc)
  MEMORY_HUB_ENRICH_BATCH_SIZE  — max items per run (default: 50)
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from agent_brain.memory.evidence.harvest.enrichment_updates import build_enrichment_updates

_log = logging.getLogger(__name__)

_DEFAULT_MODEL = "gpt-4o-mini"

_ENRICH_SYSTEM_PROMPT = """\
You are a memory enrichment engine. Given a raw memory item (title, summary, body, tags),
produce an improved version that is:
1. More concise and precisely titled
2. Better summarized (1-2 sentences capturing the key insight)
3. Tagged with the most relevant keywords (3-8 tags, lowercase)
4. Classified with a confidence score (0.0-1.0) indicating how universally useful this memory is

Respond with a JSON object:
{
  "title": "improved title",
  "summary": "improved 1-2 sentence summary",
  "tags": ["tag1", "tag2", ...],
  "confidence": 0.8
}

Only output the JSON, no markdown fences or extra text."""


def _get_model() -> str:
    return os.environ.get("MEMORY_HUB_LLM_MODEL", _DEFAULT_MODEL)


def _model_available() -> bool:
    if os.environ.get("MEMORY_HUB_NO_MODEL") == "1":
        return False
    try:
        import litellm  # noqa: F401
        return True
    except ImportError:
        _log.debug("litellm not installed — enrichment disabled")
        return False


def _call_llm(prompt: str) -> dict[str, Any] | None:
    """Call configured LLM via litellm. Returns parsed JSON or None on failure."""
    import litellm

    model = _get_model()
    kwargs: dict[str, Any] = {"model": model, "messages": [
        {"role": "system", "content": _ENRICH_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ], "temperature": 0.3, "max_tokens": 512}

    base_url = os.environ.get("MEMORY_HUB_LLM_BASE_URL")
    if base_url:
        kwargs["api_base"] = base_url

    api_key = os.environ.get("MEMORY_HUB_LLM_API_KEY")
    if api_key:
        kwargs["api_key"] = api_key

    try:
        response = litellm.completion(**kwargs)
        text = response.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(text)
    except json.JSONDecodeError as e:
        _log.warning("LLM returned non-JSON: %s", e)
        return None
    except Exception as e:
        _log.warning("LLM call failed: %s", e)
        return None


def _build_prompt(title: str, summary: str, body: str, tags: list[str]) -> str:
    parts = [
        f"Title: {title}",
        f"Summary: {summary}",
        f"Tags: {', '.join(tags) if tags else '(none)'}",
        f"Body:\n{body[:2000]}",
    ]
    return "\n\n".join(parts)


def enrich_item(
    item_id: str,
    title: str,
    summary: str,
    body: str,
    tags: list[str],
) -> dict[str, Any] | None:
    """Enrich a single item via LLM. Returns the enrichment dict or None."""
    if not _model_available():
        return None
    prompt = _build_prompt(title, summary, body, tags)
    return _call_llm(prompt)


def enrich_pool(limit: int = 50) -> int:
    """Enrich up to `limit` L0/mechanical items in the brain pool.

    Returns count of successfully enriched items.
    """
    if not _model_available():
        return 0

    from agent_brain.memory.store.items_store import ItemsStore
    from agent_brain.memory.store.write_service import _brain_dir
    from agent_brain.contracts.memory_item import AbstractionLayer

    brain = _brain_dir()
    store = ItemsStore(items_dir=brain / "items")

    candidates = []
    for item, body in store.iter_all():
        if (item.abstraction == AbstractionLayer.L0
                and getattr(item.source, "extractor", None) == "mechanical"):
            candidates.append((item, body))
        if len(candidates) >= limit:
            break

    if not candidates:
        _log.debug("no L0/mechanical items to enrich")
        return 0

    enriched_count = 0
    for item, body in candidates:
        prompt = _build_prompt(item.title, item.summary, body, item.tags)
        result = _call_llm(prompt)
        if result is None:
            continue

        updates = build_enrichment_updates(result)

        try:
            store.update_frontmatter(item.id, **updates)
            enriched_count += 1
            _log.info("enriched %s → L2", item.id)
        except Exception as e:
            _log.warning("failed to update %s: %s", item.id, e)

    return enriched_count
