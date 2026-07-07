# tests/unit/test_harvest_watermark.py
from pathlib import Path

from agent_brain.memory.evidence.harvest.watermark import WatermarkStore


def test_watermark_roundtrip_and_advance(tmp_brain):
    ws = WatermarkStore()
    p = Path("/x/y/abc.jsonl")
    assert ws.get_offset(p) == 0                 # unseen → 0
    ws.set_offset(p, offset=120, msg_hash="sha256:aa")
    ws.save()
    assert WatermarkStore().get_offset(p) == 120  # persisted across instances


def test_watermark_reset_on_hash_mismatch(tmp_brain):
    ws = WatermarkStore()
    p = Path("/x/y/abc.jsonl")
    ws.set_offset(p, offset=120, msg_hash="sha256:aa", head_hash="sha256:aa")
    ws.save()
    # File was rewritten/truncated → caller passes a different observed hash:
    assert ws.resume_offset(p, observed_head_hash="sha256:bb") == 0
    assert ws.resume_offset(p, observed_head_hash="sha256:aa") == 120
