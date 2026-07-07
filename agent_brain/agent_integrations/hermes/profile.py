from __future__ import annotations

import logging
from inspect import Parameter, signature
from typing import Any, Callable

logger = logging.getLogger(__name__)


def enrich_profile_with_preferences(
    result: dict[str, Any],
    store: Any,
    preference_inferer: Callable[[Any], Any] | None = None,
    scope_context: Any | None = None,
) -> dict[str, Any]:
    if preference_inferer is None:
        from agent_brain.memory.governance.evolve.preference import infer_preferences

        preference_inferer = infer_preferences

    try:
        profile = _infer_profile(preference_inferer, store, scope_context)
        if profile.signals:
            result["preferences"] = [
                {
                    "dimension": signal.dimension,
                    "preference": signal.preference,
                    "confidence": signal.confidence,
                    "evidence_count": signal.evidence_count,
                    "tags": list(getattr(signal, "tags", [])),
                    "scope_match": getattr(signal, "scope_match", "exact"),
                    "source_item_ids": list(getattr(signal, "source_item_ids", [])),
                }
                for signal in profile.signals[:10]
            ]
        if profile.decision_patterns:
            result["decision_patterns"] = profile.decision_patterns[:5]
        if getattr(profile, "scope", None):
            result["preference_scope"] = profile.scope
    except Exception:
        logger.warning(
            "Failed to enrich Hermes profile with preferences",
            exc_info=True,
        )
    return result


def _infer_profile(preference_inferer: Callable[[Any], Any], store: Any, scope_context: Any | None) -> Any:
    if scope_context is None:
        return preference_inferer(store)
    try:
        params = signature(preference_inferer).parameters
    except (TypeError, ValueError):
        return preference_inferer(store, scope=scope_context)
    supports_keywords = any(param.kind == Parameter.VAR_KEYWORD for param in params.values())
    if "scope" in params or supports_keywords:
        return preference_inferer(store, scope=scope_context)
    return preference_inferer(store)


__all__ = ["enrich_profile_with_preferences"]
