"""Assemble a token-budgeted resume briefing from the md store — summaries only,
never bodies — so a resuming agent gets the whole picture in one bounded call
instead of bulk-reading full items into context.

What it does: scan ItemsStore, group items into priority tiers (open signals →
handoffs → decisions → episodes), pack `[type] **title** (id) — summary` lines
until a char budget (budget_tokens*4) is hit, and announce any withheld items
(no silent truncation). Offline-safe: pure md scan, no embedder/index dependency.

Depends on: ItemsStore (md source of truth), MemoryItem. Used by: CLI `memory
brief`, MCP `brief_memory`.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from agent_brain.memory.context.context_firewall_types import ContextCandidate
from agent_brain.memory.context.injection_gateway import evaluate_injection_candidates
from agent_brain.memory.store.items_store import ItemsStore

# Auto-captured session bookkeeping is noise for a resume briefing (the
# session-end hook writes one per session); mirror inject-discipline.sh.
_NOISE_TAGS = {"session-end", "auto-captured"}
_CHARS_PER_TOKEN = 4  # repo's existing rough token proxy

# (tier name, memory type, recency window in days or None for all-time)
_TIERS: list[tuple[str, str, int | None]] = [
    ("open_signals", "signal", 14),
    ("recent_handoffs", "handoff", None),
    ("key_decisions", "decision", None),
    ("recent_episodes", "episode", None),
]


@dataclass
class BriefItem:
    type: str
    title: str
    id: str
    summary: str

    def render(self) -> str:
        s = f" — {self.summary}" if self.summary else ""
        return f"[{self.type}] **{self.title}** (id:{self.id[:8]}){s}"


@dataclass
class BriefTier:
    name: str
    shown: list[BriefItem] = field(default_factory=list)
    withheld: int = 0


@dataclass
class Brief:
    tiers: list[BriefTier]
    budget_tokens: int
    footer: str

    @property
    def total_shown(self) -> int:
        return sum(len(t.shown) for t in self.tiers)

    @property
    def total_withheld(self) -> int:
        return sum(t.withheld for t in self.tiers)


def _brief_rows(store: ItemsStore, *, project: str | None):
    now = datetime.now(timezone.utc).astimezone()
    windows = {type_: days for _name, type_, days in _TIERS}
    rows = []
    for item, body in store.iter_all():
        item_type = str(item.type)
        if item_type not in windows:
            continue
        if project is not None and item.project != project:
            continue
        if _NOISE_TAGS & set(item.tags):
            continue
        since_days = windows[item_type]
        if since_days is not None and (now - item.created_at).days > since_days:
            continue
        rows.append((item, body))
    return rows


def _sort_items(items, *, type_: str, query: str | None):
    def score(item):
        haystack = f"{item.title} {item.summary} {' '.join(item.tags)}".lower()
        hits = sum(1 for word in (query or "").lower().split() if word in haystack)
        confidence = item.confidence if type_ == "decision" else 0.0
        return (hits, confidence, item.created_at.timestamp())

    return sorted(items, key=score, reverse=True)


def build_brief(
    store: ItemsStore,
    *,
    project: str | None = None,
    budget_tokens: int = 1500,
    query: str | None = None,
) -> Brief:
    rows = _brief_rows(store, project=project)
    firewall = evaluate_injection_candidates(
        [
            ContextCandidate(
                item=item,
                body=body,
                score=item.confidence,
                source="brief",
            )
            for item, body in rows
        ],
        query=query,
    )
    included_by_type: dict[str, list] = {}
    for decision in firewall.included:
        item = decision.candidate.item
        included_by_type.setdefault(str(item.type), []).append(item)
    rejected_by_type = Counter(
        str(decision.candidate.item.type) for decision in firewall.excluded
    )

    budget_chars = max(200, budget_tokens) * _CHARS_PER_TOKEN
    used = 0
    tiers: list[BriefTier] = []
    for name, type_, _since_days in _TIERS:
        tier = BriefTier(name=name, withheld=rejected_by_type[type_])
        for item in _sort_items(
            included_by_type.get(type_, []),
            type_=type_,
            query=query,
        ):
            brief_item = BriefItem(
                type=str(item.type),
                title=item.title,
                id=item.id,
                summary=item.summary or "",
            )
            line_cost = len(brief_item.render()) + 1
            if used + line_cost <= budget_chars:
                tier.shown.append(brief_item)
                used += line_cost
            else:
                tier.withheld += 1
        tiers.append(tier)
    footer = (
        "Read full bodies sparingly: `memory read --full <id>` only for the "
        "1–3 items you actually need."
    )
    return Brief(tiers=tiers, budget_tokens=budget_tokens, footer=footer)
