from __future__ import annotations


def test_devanagari_spelled_latin_acronyms_become_bounded_technical_anchors() -> None:
    from agent_brain.memory.recall.technical_anchors import technical_acronym_anchors

    assert technical_acronym_anchors("कैश का टीटीएल कितना है") == ("ttl",)
    assert technical_acronym_anchors("एपीआई और एसडीके") == ("api", "sdk")
    assert technical_acronym_anchors("सीपीयू URL यूआरएल") == ("cpu", "url")


def test_devanagari_technical_loanwords_and_acronyms_preserve_query_order() -> None:
    from agent_brain.memory.recall.technical_anchors import (
        TECHNICAL_ALIAS_SET_ID,
        technical_query_anchors,
    )

    assert TECHNICAL_ALIAS_SET_ID == "devanagari-exact-v1"
    assert technical_query_anchors("कैश का टीटीएल कितना है") == ("cache", "ttl")
    assert technical_query_anchors("सर्वर एपीआई और क्लाइंट एसडीके") == (
        "server",
        "api",
        "client",
        "sdk",
    )
    assert technical_query_anchors("कैश कैश") == ("cache",)


def test_devanagari_words_and_partial_letter_names_do_not_become_anchors() -> None:
    from agent_brain.memory.recall.technical_anchors import technical_acronym_anchors

    assert technical_acronym_anchors("कैश कितना है") == ()
    assert technical_acronym_anchors("टी कितना है") == ()
    assert technical_acronym_anchors("टीटीएलx") == ()
    assert technical_acronym_anchors("यह एक सामान्य वाक्य है") == ()


def test_technical_acronym_anchor_is_deduplicated_and_length_bounded() -> None:
    from agent_brain.memory.recall.technical_anchors import technical_acronym_anchors

    assert technical_acronym_anchors("टीटीएल टीटीएल") == ("ttl",)
    assert technical_acronym_anchors("एबीसीडीईएफजीएचआई") == ()


def test_hindi_ttl_query_uses_shared_technical_anchor_for_recall() -> None:
    from agent_brain.memory.context.prompt_frame import analyze_prompt_frame
    from agent_brain.memory.context.query_signal import analyze_injection_query
    from agent_brain.memory.recall.admission import build_recall_request

    query = "कैश का टीटीएल कितना है"
    signal = analyze_injection_query(query)
    request = build_recall_request(query, adapter="codex")

    assert not signal.injectable
    assert signal.terms == ()
    assert request.query_signal.injectable
    assert request.query_signal.terms == ("cache", "ttl")
    assert request.query_signal.strong_terms == ("cache", "ttl")
    assert request.query_signal.anchors == ("technical_alias",)
    assert "technical_alias_set=devanagari-exact-v1" in request.query_signal.trace
    assert "technical_alias_terms=cache|ttl" in request.query_signal.trace
    assert request.lexical_terms == ("cache", "ttl")

    frame = analyze_prompt_frame(query)
    assert frame.retrieval_mode == "candidate_search"
    assert frame.injection_policy == "needs_answerability"
    assert frame.query_terms == ("cache", "ttl")

    rollback = build_recall_request(
        query,
        adapter="codex",
        enable_technical_anchors=False,
    )
    assert not rollback.query_signal.injectable
    assert rollback.lexical_terms == ()
    assert analyze_prompt_frame(
        query,
        enable_technical_anchors=False,
    ).retrieval_mode == "block"


def test_technical_alias_implementation_has_no_fixture_label_backchannel() -> None:
    from pathlib import Path

    import agent_brain.memory.recall.technical_anchors as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "multi-hi-08" not in source
    assert "mem-20260715-120032-cache-ttl" not in source
