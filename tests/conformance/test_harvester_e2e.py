from pathlib import Path

from agent_brain.memory.evidence.harvest.harvester import Harvester


def test_harvest_offline_writes_raw_and_is_idempotent(tmp_brain, tmp_path, monkeypatch):
    # Arrange: a transcript under a fake CC projects root
    proj = tmp_path / "projects" / "p1"
    proj.mkdir(parents=True)
    src = Path("tests/fixtures/sample_transcript.jsonl").read_text()
    (proj / "t.jsonl").write_text(src)
    h = Harvester(transcripts_root=tmp_path / "projects")

    first = h.run(enrich=False)        # mechanical only, no network/model
    assert first.written >= 1
    second = h.run(enrich=False)       # watermark → nothing new
    assert second.written == 0
