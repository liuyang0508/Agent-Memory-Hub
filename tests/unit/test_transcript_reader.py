# tests/unit/test_transcript_reader.py
from pathlib import Path

from agent_brain.memory.evidence.harvest.transcript_reader import read_spans, TranscriptSpan


def test_read_spans_yields_text_with_offsets():
    p = Path("tests/fixtures/sample_transcript.jsonl")
    spans = list(read_spans(p, start_offset=0))
    assert len(spans) >= 1
    assert all(isinstance(s, TranscriptSpan) for s in spans)
    assert spans[-1].end_offset == p.stat().st_size       # full read reaches EOF
    assert "mechanical-first" in spans[-1].text


def test_read_spans_resumes_from_offset():
    p = Path("tests/fixtures/sample_transcript.jsonl")
    first = list(read_spans(p, start_offset=0))
    mid = first[0].end_offset
    rest = list(read_spans(p, start_offset=mid))
    assert rest and rest[0].start_offset == mid


def test_read_spans_accepts_common_agent_jsonl_shapes(tmp_path: Path) -> None:
    transcript = tmp_path / "agent.jsonl"
    transcript.write_text(
        "\n".join([
            '{"role":"user","content":"plain prompt"}',
            '{"type":"response_item","item":{"role":"assistant","content":[{"type":"output_text","text":"codex answer"}]}}',
            '{"payload":{"message":{"role":"assistant","content":[{"type":"text","text":"nested answer"}]}}}',
            '{"payload":{"type":"message","role":"developer","content":[{"type":"input_text","text":"payload content"}]}}',
        ])
        + "\n",
        encoding="utf-8",
    )

    spans = list(read_spans(transcript))

    assert [span.role for span in spans] == ["user", "assistant", "assistant", "developer"]
    assert [span.text for span in spans] == [
        "plain prompt",
        "codex answer",
        "nested answer",
        "payload content",
    ]
