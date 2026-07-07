"""P1-8: token-budget tiered read path (borrowed from OpenViking).

When injecting recalled memories into an agent's context, returning top-k full
bodies blindly blows the token budget. Instead, pack greedily by rank: full
body while it fits, fall back to summary-only for items that don't, stop when
the budget is exhausted. Highest read-side token leverage.
"""
from agent_brain.memory.recall.retrieval import estimate_tokens, pack_within_budget


def test_token_budget_helpers_are_split_and_reexported():
    from agent_brain.memory.recall import retrieval
    from agent_brain.memory.recall.retrieval_budget import (
        estimate_tokens as split_estimate_tokens,
        pack_within_budget as split_pack_within_budget,
    )

    assert retrieval.estimate_tokens is split_estimate_tokens
    assert retrieval.pack_within_budget is split_pack_within_budget


def _entry(item_id, full, summary):
    return {"id": item_id, "full": full, "summary": summary}


def test_estimate_tokens_monotonic():
    assert estimate_tokens("") >= 0
    assert estimate_tokens("a" * 4) >= 1
    assert estimate_tokens("a" * 400) > estimate_tokens("a" * 40)


def test_all_fit_returns_full_tier():
    entries = [_entry("a", "x" * 40, "s"), _entry("b", "y" * 40, "s")]
    packed = pack_within_budget(entries, max_tokens=1000)
    assert [p["id"] for p in packed] == ["a", "b"]
    assert all(p["tier"] == "full" for p in packed)


def test_demotes_to_summary_then_stops():
    # ~25 tokens each full; budget admits one full then forces summary/stop.
    entries = [
        _entry("a", "w" * 100, "short a"),
        _entry("b", "w" * 100, "short b"),
        _entry("c", "w" * 100, "short c"),
    ]
    packed = pack_within_budget(entries, max_tokens=30)
    assert packed[0]["id"] == "a"
    assert packed[0]["tier"] == "full"
    # b should appear as summary (it fits where full would not), c excluded.
    tiers = {p["id"]: p["tier"] for p in packed}
    assert tiers.get("b") == "summary"
    assert "c" not in tiers


def test_budget_never_exceeded():
    entries = [_entry(str(i), "w" * 200, "sum") for i in range(10)]
    packed = pack_within_budget(entries, max_tokens=50)
    used = sum(
        estimate_tokens(p["text"]) for p in packed
    )
    assert used <= 50


def test_text_field_matches_tier():
    entries = [_entry("a", "FULLBODY", "SUMM")]
    packed = pack_within_budget(entries, max_tokens=1000)
    assert packed[0]["text"] == "FULLBODY"
    packed2 = pack_within_budget(entries, max_tokens=estimate_tokens("SUMM"))
    assert packed2[0]["tier"] == "summary"
    assert packed2[0]["text"] == "SUMM"
