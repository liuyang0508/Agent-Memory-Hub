from agent_brain.memory.evidence.harvest.extractor import extract_candidates, Candidate
from agent_brain.memory.evidence.harvest.transcript_reader import TranscriptSpan


def test_extracts_decision_marker():
    span = TranscriptSpan(text="Decision: chose mechanical-first over pure LLM so it works offline.",
                          start_offset=0, end_offset=50, role="assistant")
    cands = extract_candidates([span])
    assert any(c.type == "decision" for c in cands)
    assert all(c.abstraction == "L0" for c in cands)        # raw


def test_extracts_chinese_history_markers():
    spans = [
        TranscriptSpan(text="事实：AMH 只扫描本机历史", start_offset=0, end_offset=30, role="user"),
        TranscriptSpan(text="决策：保留人工审核", start_offset=31, end_offset=60, role="user"),
    ]
    cands = extract_candidates(spans)

    assert [c.type for c in cands] == ["fact", "decision"]


def test_extracts_error_then_fix_episode():
    span = TranscriptSpan(text="test_cli_version failed; reinstalling with pip install -e . fixed it.",
                          start_offset=0, end_offset=60, role="assistant")
    cands = extract_candidates([span])
    assert any(c.type in ("episode", "fact") for c in cands)


def test_redacts_secrets():
    secret = "AKIA" + "1234567890ABCDEF"
    span = TranscriptSpan(text=f"export AWS_SECRET_ACCESS_KEY={secret}",
                          start_offset=0, end_offset=50, role="assistant")
    cands = extract_candidates([span])
    assert all(secret not in c.body for c in cands)
