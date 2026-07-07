from __future__ import annotations

from typing import Any


def build_enrichment_updates(result: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {"abstraction": "L2", "source.extractor": "llm"}
    if "title" in result and isinstance(result["title"], str):
        updates["title"] = result["title"]
    if "summary" in result and isinstance(result["summary"], str):
        updates["summary"] = result["summary"]
    if "tags" in result and isinstance(result["tags"], list):
        updates["tags"] = [tag for tag in result["tags"] if isinstance(tag, str)][:8]
    if "confidence" in result and isinstance(result["confidence"], (int, float)):
        updates["confidence"] = max(0.0, min(1.0, float(result["confidence"])))
    return updates
