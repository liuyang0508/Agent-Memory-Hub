"""Reversible context packing for before-inject memory views."""
from __future__ import annotations

from dataclasses import dataclass, replace

from agent_brain.contracts.memory_item import MemoryItem
from agent_brain.memory.context.context_firewall_rules import exclude_with
from agent_brain.memory.context.context_firewall_types import FirewallDecision
from agent_brain.memory.context.context_loading import (
    ContextVerbosity,
    ContextView,
    render_context_view,
    select_context_view,
)
from agent_brain.memory.context.adaptive_compression import compress_text
from agent_brain.memory.recall.retrieval_budget import estimate_tokens

DEFAULT_RETRIEVE_HEAD_CHARS = 2000


@dataclass(frozen=True)
class ContextPack:
    """Compressed prompt text plus a locator for retrieving the canonical body."""

    item_id: str
    selected_view: ContextView
    text: str
    detail_uri: str
    retrieve_head_chars: int
    load_reason: tuple[str, ...]
    packed_chars: int
    packed_tokens: int
    full_chars: int
    full_tokens: int
    compressed: bool
    reversible: bool = True
    compression_strategy: str = "none"
    compression_content_type: str = "plain_text"
    compression_ratio: float = 1.0
    tokens_saved: int = 0
    ccr_key: str | None = None
    ccr_marker: str | None = None

    @property
    def retrieve_hint(self) -> str:
        return (
            f"read_memory(id='{self.item_id}', "
            f"head={self.retrieve_head_chars}, view='detail')"
        )

    @property
    def cli_retrieve_hint(self) -> str:
        return (
            f"memory read {self.item_id} "
            f"--head {self.retrieve_head_chars} --view detail"
        )

    def to_dict(self) -> dict:
        """Return the MCP/SDK-friendly structured representation."""
        return {
            "item_id": self.item_id,
            "selected_view": self.selected_view,
            "text": self.text,
            "detail_uri": self.detail_uri,
            "retrieve_hint": self.retrieve_hint,
            "cli_retrieve_hint": self.cli_retrieve_hint,
            "retrieve_head_chars": self.retrieve_head_chars,
            "load_reason": list(self.load_reason),
            "packed_chars": self.packed_chars,
            "packed_tokens": self.packed_tokens,
            "full_chars": self.full_chars,
            "full_tokens": self.full_tokens,
            "compressed": self.compressed,
            "reversible": self.reversible,
            "compression_strategy": self.compression_strategy,
            "compression_content_type": self.compression_content_type,
            "compression_ratio": self.compression_ratio,
            "tokens_saved": self.tokens_saved,
            "ccr_key": self.ccr_key,
            "ccr_marker": self.ccr_marker,
        }


@dataclass(frozen=True)
class PackedDecision:
    """A firewall decision paired with the prompt pack that will be injected."""

    decision: FirewallDecision
    pack: ContextPack


@dataclass(frozen=True)
class ContextPackResult:
    """Cohort-level pack decisions after optional token-budget enforcement."""

    included: list[PackedDecision]
    excluded: list[FirewallDecision]
    used_tokens: int
    full_tokens: int

    def metrics(self) -> dict[str, object]:
        items = [
            {
                "id": entry.decision.candidate.item.id,
                "selected_view": entry.pack.selected_view,
                "packed_tokens": entry.pack.packed_tokens,
                "full_tokens": entry.pack.full_tokens,
                "compressed": entry.pack.compressed,
            }
            for entry in self.included
        ]
        return {
            "items": items,
            "packed_tokens": self.used_tokens,
            "full_tokens": self.full_tokens,
        }


def build_context_pack(
    item: MemoryItem,
    body: str = "",
    *,
    requested: ContextVerbosity = "auto",
    firewall_decision=None,
    budget_tokens: int | None = None,
    retrieve_head_chars: int = DEFAULT_RETRIEVE_HEAD_CHARS,
) -> ContextPack:
    """Build a Headroom-style reversible pack for one memory candidate.

    The prompt receives the selected compact context view. The canonical detail
    body remains addressable through ``detail_uri`` and the read hints.
    """
    selection = select_context_view(
        item,
        body,
        requested=requested,
        firewall_decision=firewall_decision,
        budget_tokens=budget_tokens,
    )
    text = render_context_view(item, body, selection.view)
    detail_uri = item.context_views.detail_uri or f"memory://items/{item.id}/body"
    full_text = body or text
    compression_strategy = "none"
    compression_content_type = "plain_text"
    compression_ratio = 1.0
    tokens_saved = 0
    ccr_key = None
    ccr_marker = None

    if selection.view == "detail" and text and budget_tokens is not None:
        compressed = compress_text(
            text,
            budget_chars=max(1, budget_tokens * 4),
            detail_uri=detail_uri,
        )
        if compressed.text != text:
            text = compressed.text
            compression_strategy = compressed.strategy
            compression_content_type = compressed.content_type
            compression_ratio = compressed.compression_ratio
            tokens_saved = compressed.tokens_saved
            ccr_key = compressed.ccr_key
            ccr_marker = compressed.ccr_marker

    return ContextPack(
        item_id=item.id,
        selected_view=selection.view,
        text=text,
        detail_uri=detail_uri,
        retrieve_head_chars=retrieve_head_chars,
        load_reason=selection.reasons,
        packed_chars=len(text),
        packed_tokens=_tokens(text),
        full_chars=len(full_text),
        full_tokens=_tokens(full_text),
        compressed=_is_compressed(selection.view, text=text, body=body)
        or compression_strategy != "none",
        compression_strategy=compression_strategy,
        compression_content_type=compression_content_type,
        compression_ratio=compression_ratio,
        tokens_saved=tokens_saved,
        ccr_key=ccr_key,
        ccr_marker=ccr_marker,
    )


def pack_decisions(
    decisions: list[FirewallDecision],
    *,
    requested: ContextVerbosity = "auto",
    budget_tokens: int | None = None,
    retrieve_head_chars: int = DEFAULT_RETRIEVE_HEAD_CHARS,
) -> ContextPackResult:
    """Build prompt packs for included firewall decisions and apply pack budget."""

    included: list[PackedDecision] = []
    excluded: list[FirewallDecision] = [
        decision for decision in decisions if decision.action == "exclude"
    ]
    used_tokens = 0
    full_tokens = 0

    for decision in decisions:
        if decision.action == "exclude":
            continue

        original_pack = build_context_pack(
            decision.candidate.item,
            decision.candidate.body,
            requested=requested,
            firewall_decision=decision,
            retrieve_head_chars=retrieve_head_chars,
        )
        pack = _fit_pack_to_remaining_budget(
            decision,
            original_pack,
            requested=requested,
            remaining_tokens=None if budget_tokens is None else budget_tokens - used_tokens,
            retrieve_head_chars=retrieve_head_chars,
        )

        if budget_tokens is not None and used_tokens + pack.packed_tokens > budget_tokens:
            excluded.append(exclude_with(decision, "pack_budget_exceeded"))
            continue

        packed_decision = decision
        if pack.selected_view != original_pack.selected_view:
            packed_decision = replace(
                decision,
                reasons=(
                    *decision.reasons,
                    f"budget_downgraded_to_{pack.selected_view}",
                ),
            )

        used_tokens += pack.packed_tokens
        full_tokens += pack.full_tokens
        included.append(PackedDecision(decision=packed_decision, pack=pack))

    return ContextPackResult(
        included=included,
        excluded=excluded,
        used_tokens=used_tokens,
        full_tokens=full_tokens,
    )


def _fit_pack_to_remaining_budget(
    decision: FirewallDecision,
    original_pack: ContextPack,
    *,
    requested: ContextVerbosity,
    remaining_tokens: int | None,
    retrieve_head_chars: int,
) -> ContextPack:
    if remaining_tokens is None or original_pack.packed_tokens <= remaining_tokens:
        return original_pack

    pack = original_pack
    if pack.selected_view == "detail" and requested == "detail":
        compressed_detail_pack = build_context_pack(
            decision.candidate.item,
            decision.candidate.body,
            requested="detail",
            firewall_decision=decision,
            budget_tokens=max(1, remaining_tokens),
            retrieve_head_chars=retrieve_head_chars,
        )
        if compressed_detail_pack.packed_tokens <= remaining_tokens:
            return compressed_detail_pack
        pack = compressed_detail_pack

    for fallback in ("overview", "locator"):
        if pack.selected_view == fallback:
            continue
        fallback_pack = build_context_pack(
            decision.candidate.item,
            decision.candidate.body,
            requested=fallback,
            firewall_decision=decision,
            retrieve_head_chars=retrieve_head_chars,
        )
        if fallback_pack.packed_tokens <= remaining_tokens:
            return fallback_pack
        pack = fallback_pack
    return pack


def _tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, estimate_tokens(text))


def _is_compressed(view: ContextView, *, text: str, body: str) -> bool:
    if view != "detail":
        return True
    return bool(body and text != body)


__all__ = [
    "ContextPack",
    "ContextPackResult",
    "DEFAULT_RETRIEVE_HEAD_CHARS",
    "PackedDecision",
    "build_context_pack",
    "pack_decisions",
]
